"""Hakem Analizi: deterministic season ordering and historical comparison."""
from __future__ import annotations
import re
import unicodedata
import pandas as pd

EXCLUDE = ("women", "woman", "female", "feminin", "feminine", "kadin", "ladies", "friendly", "friendlies", "hazirlik", "youth", "juvenil", "juvenile", "reserve", "reserves", "development", "academy", "amateur", "u-17", "u-18", "u-19", "u-20", "u-21", "u-23", "under 17", "under 18", "under 19", "under 20", "under 21", "under 23")
EUROPE_IDS = {2: "Şampiyonlar Ligi", 3: "Avrupa Ligi", 848: "Konferans Ligi"}
FINISHED = {"FT", "AET", "PEN"}


def clean(text):
    value = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in value if not unicodedata.combining(c)).casefold().strip()


# API-Football bazen hakemin ülkesini sonuna ekler:
# "Halil Umut Meler, Türkiye" ve "Halil Umut Meler" aynı kişidir.
# "Meler, Halil Umut" gibi soyad-önce yazımları da desteklenir.
COUNTRY_SUFFIXES = {
    "turkiye", "turkey", "türkiye", "turquie", "turchia",
    "england", "scotland", "wales", "northern ireland", "ireland",
    "france", "germany", "italy", "spain", "portugal", "netherlands",
    "belgium", "austria", "switzerland", "denmark", "sweden", "norway",
    "finland", "poland", "greece", "croatia", "serbia", "romania",
    "czech republic", "czechia", "slovakia", "slovenia", "hungary",
    "ukraine", "russia", "georgia", "azerbaijan", "israel", "cyprus",
    "albania", "bosnia and herzegovina", "bulgaria", "montenegro",
    "brazil", "argentina", "uruguay", "australia", "united states",
    "united states of america", "usa", "mexico", "japan", "korea republic",
    "saudi arabia", "morocco", "egypt", "tunisia", "qatar",
}


def norm_ref(text):
    s = clean(text)
    parts = [p.strip() for p in s.split(",") if p.strip()]
    # İlk olarak ülke etiketlerini çıkar. Soyad-ad biçimini yanlışlıkla ters çevirme.
    while len(parts) > 1 and parts[-1] in COUNTRY_SUFFIXES:
        parts.pop()
    if len(parts) == 2:
        # "Soyad, Ad" biçiminde ilk öğe genellikle tek kelimedir.
        if len(parts[0].split()) == 1 and len(parts[1].split()) >= 1:
            s = parts[1] + " " + parts[0]
        else:
            s = " ".join(parts)
    else:
        s = " ".join(parts)
    # Noktasız ı ve Türkçe normalizasyonu tutarlı olsun.
    s = s.replace("ı", "i").replace("æ", "ae")
    return " ".join(re.findall(r"[a-z0-9]+", s))


def referee_match(raw, query, exact=False):
    a, b = norm_ref(raw), norm_ref(query)
    if not a or not b:
        return False
    return a == b if exact else (b in a or a in b)


def allowed(meta):
    name = clean(meta.get("name"))
    if any(re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", name) for t in EXCLUDE):
        return False
    if re.search(r"(?<![a-z0-9])u\s*[-]?\s*(?:1[0-9]|2[0-3])(?![0-9])", name):
        return False
    return True


def category(meta):
    lid = int(meta.get("id") or 0)
    if lid in EUROPE_IDS:
        return EUROPE_IDS[lid]
    return "Kupa" if clean(meta.get("type")) == "cup" else "Lig"


def round_no(round_name):
    """Only unambiguous numeric rounds count as same-week matching.
    Cup knockouts are not interpreted as a league 'week'.
    """
    s = clean(round_name)
    if any(x in s for x in ("quarter", "semi", "final", "play-off", "playoff", "qualif", "round of", "last ")):
        return None
    if not any(x in s for x in ("regular season", "matchday", "week", "round", "group stage", "league stage")):
        return None
    m = re.search(r"(?:regular season|matchday|week|round|group stage|league stage)\s*[-:]?\s*(\d+)\b", s)
    return int(m.group(1)) if m else None


def outcome(h, a):
    if h is None or a is None:
        return "-"
    return "1" if h > a else "2" if h < a else "X"


def score(h, a):
    return f"{h}-{a}" if h is not None and a is not None else "-"


def rows_from_fixtures(fixtures, season, selected_ids):
    rows = []
    for f in fixtures:
        fi, le = f.get("fixture") or {}, f.get("league") or {}
        lid = le.get("id")
        if lid is None or int(lid) not in selected_ids or not allowed(le):
            continue
        t = f.get("teams") or {}
        home, away = t.get("home") or {}, t.get("away") or {}
        g = f.get("goals") or {}
        sc = f.get("score") or {}
        ht = sc.get("halftime") or {}
        status = (fi.get("status") or {}).get("short") or "NS"
        htf, hta = ht.get("home"), ht.get("away")
        gf, ga = g.get("home"), g.get("away")
        finished = status in FINISHED
        ht_result = outcome(htf, hta) if finished else "-"
        ms_result = outcome(gf, ga) if finished else "-"
        rows.append(dict(
            id=fi.get("id"), date=fi.get("date"), season=season,
            referee=fi.get("referee") or "", category=category(le),
            league=le.get("name", ""), league_id=int(lid),
            round=le.get("round") or "", round_number=round_no(le.get("round")),
            home=home.get("name", ""), away=away.get("name", ""),
            home_id=home.get("id"), away_id=away.get("id"),
            halftime=score(htf, hta) if finished else "-",
            fulltime=score(gf, ga) if finished else "-",
            iy_ms=f"{ht_result}/{ms_result}" if finished and ht_result != "-" and ms_result != "-" else "-",
            status=status, finished=finished,
            home_yellow=None, home_red=None, away_yellow=None, away_red=None,
        ))
    return rows


def make_table(rows):
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date_sort"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
    df = df.sort_values(["date_sort", "id"], kind="stable").drop_duplicates("id").reset_index(drop=True)
    df["all_order"] = df.groupby("season").cumcount() + 1
    df["cat_order"] = df.groupby(["season", "category"]).cumcount() + 1
    return df


def build_signals(previous, current):
    """Always return all current rows; signal columns are optional annotations."""
    if current.empty:
        return current.copy()
    current = current.copy()
    current["order_signal"] = ""
    current["week_signal"] = ""
    current["prev_order_match"] = ""
    current["prev_week_match"] = ""
    for idx, row in current.iterrows():
        if previous.empty:
            continue
        cat = previous[previous.category == row.category]
        order = cat[cat.cat_order == row.cat_order]
        if not order.empty:
            p = order.iloc[0]
            current.at[idx, "prev_order_match"] = f"{p['home']} - {p['away']} ({p['iy_ms']})"
            if p["iy_ms"] in ("1/2", "2/1"):
                current.at[idx, "order_signal"] = f"Sıra: {p['iy_ms']}"
        week = row["round_number"]
        if pd.notna(week):
            week_matches = cat[(cat.round_number == week)]
            if not week_matches.empty:
                current.at[idx, "prev_week_match"] = "; ".join(f"{p.home}-{p.away} ({p.iy_ms})" for p in week_matches.itertuples())
                flip = [p.iy_ms for p in week_matches.itertuples() if p.iy_ms in ("1/2", "2/1")]
                if flip:
                    current.at[idx, "week_signal"] = "Hafta: " + ", ".join(flip)
    return current


def apply_cards(df, cards):
    """cards: fixture_id -> home_yellow,home_red,away_yellow,away_red; missing remains unknown."""
    out = df.copy()
    if out.empty:
        return out
    for idx, r in out.iterrows():
        if r["id"] in cards:
            for k, v in cards[r["id"]].items():
                out.at[idx, k] = v
    return out


def cards_from_events(events, home_id, away_id):
    result = {"home_yellow": 0, "home_red": 0, "away_yellow": 0, "away_red": 0}
    found = False
    for ev in events:
        if clean(ev.get("type")) != "card":
            continue
        team = (ev.get("team") or {}).get("id")
        prefix = "home" if team == home_id else "away" if team == away_id else None
        if not prefix:
            continue
        detail = clean(ev.get("detail"))
        if "yellow" in detail:
            result[f"{prefix}_yellow"] += 1
            found = True
        if "red" in detail:
            result[f"{prefix}_red"] += 1
            found = True
    # An empty event response does not establish 0 cards.
    return result if found else None
