"""Hakem Analizi: günlük maç bülteni -> maça tıkla -> hakem sezon analizleri.

API-Football v3 kullanır. API'de hakem atanmadıysa tahmin etmez.
"""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import date
from typing import Optional

import pandas as pd
import requests
import streamlit as st

from engine import (
    EUROPE_IDS,
    allowed,
    rows_from_fixtures,
    make_table,
    build_signals,
    apply_cards,
    cards_from_events,
)

st.set_page_config(page_title="Hakem Analizi | Maç Bülteni", page_icon="⚽", layout="wide")

BASE = "https://v3.football.api-sports.io"
COUNTRIES = ["Turkey", "England", "Spain", "Germany", "Italy", "France", "Portugal",
             "Netherlands", "Norway", "Brazil", "Argentina", "Australia"]
COUNTRY_ALIASES = {
    "turkiye", "turkey", "england", "france", "germany", "italy", "spain",
    "portugal", "netherlands", "norway", "brazil", "argentina", "australia",
    "poland", "sweden", "denmark", "greece", "belgium", "austria", "croatia",
    "serbia", "switzerland", "ukraine", "israel", "scotland", "wales", "romania",
    "qatar", "saudi arabia", "united states", "usa", "morocco", "slovakia",
}


def simplify(text):
    s = str(text or "").replace("ı", "i").replace("İ", "I")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold().strip()


def normalized_referee(name):
    """'Ad Soyad, Türkiye' ile 'Ad Soyad' aynı; 'Soyad, Ad' ters çevrilir."""
    parts = [p.strip() for p in simplify(name).split(",") if p.strip()]
    while len(parts) > 1 and parts[-1] in COUNTRY_ALIASES:
        parts.pop()
    if len(parts) == 2 and len(parts[0].split()) == 1:
        parts = [parts[1], parts[0]]
    return " ".join(re.findall(r"[a-z0-9]+", " ".join(parts)))


def is_same_referee(name, target):
    a, b = normalized_referee(name), normalized_referee(target)
    if not a or not b:
        return False
    if a == b:
        return True
    # Tam ada karşı API'de baş harf bulunuyorsa tahmin yapma: hatalı kişiyi eklemeyelim.
    return False


def request_api(endpoint, params, key):
    try:
        r = requests.get(f"{BASE}/{endpoint}", params=params,
                         headers={"x-apisports-key": key}, timeout=40)
        r.raise_for_status()
        doc = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f"API bağlantısı / yanıt hatası: {exc}") from exc
    if doc.get("errors"):
        raise RuntimeError(str(doc["errors"]))
    return doc.get("response") or []


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def list_leagues(country, key):
    return request_api("leagues", {"country": country}, key)


@st.cache_data(ttl=600, show_spinner=False)
def get_day_fixtures(day, key):
    # Tek çağrı bütün ülkelerin o günkü müsabakalarını getirir, sonra filtrelenir.
    return request_api("fixtures", {"date": day, "timezone": "Europe/Istanbul"}, key)


@st.cache_data(ttl=900, show_spinner=False)
def get_match_details(fixture_id, key):
    result = request_api("fixtures", {"id": fixture_id, "timezone": "Europe/Istanbul"}, key)
    return result[0] if result else None


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_season_fixtures(league_id, season, key):
    return request_api("fixtures", {"league": league_id, "season": season,
                                    "timezone": "Europe/Istanbul"}, key)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fixture_events(fixture_id, key):
    return request_api("fixtures/events", {"fixture": fixture_id}, key)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fixture_stats(fixture_id, key):
    return request_api("fixtures/statistics", {"fixture": fixture_id}, key)


def eligible_competition(info):
    if not allowed(info):
        return False
    name = simplify(info.get("name"))
    if re.search(r"\bu\s*[-]?\s*(?:1[0-9]|2[0-3])\b", name):
        return False
    bans = ("women", "woman", "female", "kadin", "bayan", "girls", "friendly",
            "friendlies", "hazirlik", "youth", "reserve", "academy", "amateur", "amator")
    return not any(x in name for x in bans)


def default_turkish_names(catalog):
    """Başlangıçta profesyonel ligler + Türkiye Kupası; kullanıcı isterse değiştirebilir."""
    patterns = (r"\bsuper lig\b", r"\bsuper lig", r"\b1\.?\s*lig\b",
                r"\b2\.?\s*lig\b", r"\b3\.?\s*lig\b", r"turkiye kupas",
                r"turkish cup", r"tff 1", r"tff 2", r"tff 3")
    return [label for label in catalog if any(re.search(p, simplify(label)) for p in patterns)]


def board_default_names(catalog, country):
    if country == "Turkey":
        return [label for label in catalog if re.search(r"\bsuper lig\b|\b1\.?\s*lig\b", simplify(label))]
    return list(catalog)[:3]


def competition_catalog(leagues, years):
    result = {}
    for entry in leagues:
        meta = entry.get("league") or {}
        if not eligible_competition(meta):
            continue
        lid = meta.get("id")
        if lid is None:
            continue
        seasons = {int(x.get("year")) for x in entry.get("seasons", []) if x.get("year") is not None}
        if not seasons.intersection(years):
            continue
        label = f"{meta.get('name', 'Lig')} · {meta.get('type', '')} · ID {lid}"
        result[label] = int(lid)
    return dict(sorted(result.items(), key=lambda x: simplify(x[0])))


def row_score(f):
    s = (f.get("fixture") or {}).get("status") or {}
    status = s.get("short") or "NS"
    if status in {"NS", "TBD", "PST", "CANC"}:
        return "-"
    goals = f.get("goals") or {}
    h, a = goals.get("home"), goals.get("away")
    return f"{h}-{a}" if h is not None and a is not None else "-"


def make_board(fixtures, selected_leagues):
    result = []
    for f in fixtures:
        league = f.get("league") or {}
        lid = league.get("id")
        if lid is None or int(lid) not in selected_leagues or not eligible_competition(league):
            continue
        fi = f.get("fixture") or {}
        t = f.get("teams") or {}
        local = pd.to_datetime(fi.get("date"), utc=True, errors="coerce")
        time_string = local.tz_convert("Europe/Istanbul").strftime("%H:%M") if pd.notna(local) else "-"
        result.append({
            "id": fi.get("id"), "date": str(fi.get("date") or ""), "kickoff": time_string,
            "competition": league.get("name") or "-", "league_id": int(lid),
            "round": league.get("round") or "", "home": (t.get("home") or {}).get("name") or "-",
            "away": (t.get("away") or {}).get("name") or "-",
            "score": row_score(f), "referee": fi.get("referee") or "",
            "status": (fi.get("status") or {}).get("short") or "NS",
        })
    return sorted(result, key=lambda r: (r["date"], r["competition"], str(r["id"])))


def history_scan(ids, season, key, progress=None):
    all_data, failures, coverage = [], [], []
    scans = [(yr, lid) for yr in [season - 1, season] for lid in sorted(ids)]
    for ix, (year, lid) in enumerate(scans):
        try:
            fixtures = get_season_fixtures(lid, year, key)
            # Her zaman gelen organizasyonun id'sini doğrula.
            converted = rows_from_fixtures(fixtures, year, set(ids))
            all_data.extend(converted)
            coverage.append({"Sezon": f"{year}-{year+1}", "Lig ID": lid,
                             "API maç": len(fixtures), "Okunan maç": len(converted), "Hata": ""})
        except Exception as exc:
            failures.append(f"{year}, ID {lid}: {exc}")
            coverage.append({"Sezon": f"{year}-{year+1}", "Lig ID": lid,
                             "API maç": "?", "Okunan maç": "?", "Hata": str(exc)[:100]})
        if progress:
            progress.progress((ix + 1) / max(1, len(scans)))
    return all_data, failures, coverage


def card_stats_from_api(data, home_id, away_id):
    """Takım istatistikleri her iki tarafın sarı/kırmızı değerlerini içeriyorsa kullan."""
    result = {}
    for team in data:
        tid = (team.get("team") or {}).get("id")
        prefix = "home" if tid == home_id else "away" if tid == away_id else None
        if prefix is None:
            continue
        seen = {}
        for entry in team.get("statistics") or []:
            key = simplify(entry.get("type"))
            if key in ("yellow cards", "red cards"):
                v = entry.get("value")
                if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
                    seen["yellow" if key.startswith("yellow") else "red"] = int(v)
        if "yellow" in seen and "red" in seen:
            result[prefix + "_yellow"] = seen["yellow"]
            result[prefix + "_red"] = seen["red"]
    return result if len(result) == 4 else None


def fetch_one_cards(row, key):
    fixture_id = row.get("id")
    if fixture_id is None or not row.get("finished", False):
        return None
    try:
        stats = fixture_stats(fixture_id, key)
        data = card_stats_from_api(stats, row.get("home_id"), row.get("away_id"))
        if data is not None:
            return data
    except Exception:
        pass
    events = fixture_events(fixture_id, key)
    return cards_from_events(events, row.get("home_id"), row.get("away_id"))


def table_card(yellow, red):
    if yellow is None or red is None or pd.isna(yellow) or pd.isna(red):
        return "Veri yok"
    return f"🟨 {yellow}  🟥 {red}"


def history_view(df, show_signals, download_key):
    if df.empty:
        st.info("Bu dönem için taranan organizasyonlarda hakemin maçı bulunamadı.")
        return
    df = df.copy()
    df["Tarih"] = df["date_sort"].dt.tz_convert("Europe/Istanbul").dt.strftime("%d.%m.%Y %H:%M")
    df["Ev kartları"] = [table_card(a, b) for a, b in zip(df.home_yellow, df.home_red)]
    df["Dep kartları"] = [table_card(a, b) for a, b in zip(df.away_yellow, df.away_red)]
    cols = {
        "Tarih": "Tarih", "all_order": "Genel sıra", "category": "Kategori",
        "cat_order": "Kategori sıra", "league": "Organizasyon", "round": "Hafta / Tur",
        "home": "Ev sahibi", "away": "Deplasman", "halftime": "İY skor",
        "fulltime": "MS skor", "iy_ms": "İY/MS", "Ev kartları": "Ev kart",
        "Dep kartları": "Dep kart", "status": "Durum",
    }
    if show_signals:
        cols.update({"order_signal": "Sıra sinyali", "week_signal": "Hafta sinyali",
                     "prev_order_match": "Geçen sezon aynı sıra",
                     "prev_week_match": "Geçen sezon aynı hafta"})
    frame = df[list(cols)].rename(columns=cols)
    st.dataframe(frame, hide_index=True, use_container_width=True,
                 height=min(650, 65 + 36 * len(frame)))
    st.download_button("📥 Tabloyu CSV indir", data=frame.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"hakem_maclari_{download_key}.csv", mime="text/csv", key=download_key)


# ------------ ARAYÜZ -------------
st.title("⚽ Hakem Analizi | Günlük Maç Bülteni")
st.caption("Önce günü ve ligleri seç → maçlardan birine tıkla → hakemin iki sezonluk analizini gör.")
try:
    api_key = st.secrets.get("API_FOOTBALL_KEY", "") or os.getenv("API_FOOTBALL_KEY", "")
except Exception:
    api_key = os.getenv("API_FOOTBALL_KEY", "")
if not api_key:
    api_key = st.text_input("API-Football anahtarı", type="password")
if not api_key:
    st.info("Streamlit App settings → Secrets içine API_FOOTBALL_KEY ekle.")
    st.stop()

with st.sidebar:
    st.header("📅 Maç bülteni")
    selected_date = st.date_input("Maç tarihi", value=date.today(), format="DD.MM.YYYY")
    country = st.selectbox("Ülke", COUNTRIES)
    season = st.number_input("Sezon başlangıç yılı", min_value=2015, max_value=2035,
                             value=selected_date.year if selected_date.month >= 7 else selected_date.year - 1,
                             step=1, help="Türkiye/Avrupa: sezon başlangıç yılı. Brezilya gibi takvim liglerinde yılı değiştirebilirsin.")
    st.caption(f"Geçen sezon: {season-1}/{season}; bu sezon: {season}/{season+1}")

try:
    catalog = competition_catalog(list_leagues(country, api_key), {int(season), int(season)-1})
except Exception as exc:
    st.error(f"Lig listesini çekemedim: {exc}")
    st.stop()

with st.sidebar:
    board_names = st.multiselect("Bültende görünecek ligler", list(catalog),
                                 default=board_default_names(catalog, country))
    european_board = st.multiselect("Bültendeki Avrupa kupaları", list(EUROPE_IDS.values()), default=[])
    st.divider()
    st.subheader("📚 Hakem arşivi kapsamı")
    default_history = default_turkish_names(catalog) if country == "Turkey" else list(catalog)[:4]
    history_names = st.multiselect("Hakemin geçmişini taranacak lig/kupalar", list(catalog),
                                   default=default_history,
                                   help="Seçilmemiş profesyonel yarışmalardan maçlar arşive katılamaz.")
    european_history = st.multiselect("Hakem geçmişi: Avrupa kupaları", list(EUROPE_IDS.values()),
                                      default=list(EUROPE_IDS.values()))
    st.caption("Hazırlık, kadın ve alt yaş maçları dahil edilmez. Diğer ülkelerin lig maçları bu tarama kapsamı dışındadır.")

board_ids = {catalog[n] for n in board_names}
board_ids.update(lid for lid, name in EUROPE_IDS.items() if name in european_board)
history_ids = {catalog[n] for n in history_names}
history_ids.update(lid for lid, name in EUROPE_IDS.items() if name in european_history)
# Maçın organizasyonunu hakem geçmişine mutlaka kat.
history_ids.update(board_ids)

if not board_ids:
    st.warning("En az bir bülten ligi seç.")
    st.stop()

st.subheader(f"📋 {selected_date.strftime('%d.%m.%Y')} maçları")
try:
    day = get_day_fixtures(selected_date.isoformat(), api_key)
except Exception as exc:
    st.error(f"Günlük maç bülteni yüklenemedi: {exc}")
    st.stop()

matches = make_board(day, board_ids)
st.caption(f"Seçili organizasyonlarda {len(matches)} maç bulundu. Hakem bilgisi açıklanmayan maçlar da listede kalır.")
if not matches:
    st.info("Bu tarihte seçilen organizasyonlardan maç bulunamadı. Tarihi veya ligleri değiştir.")
    st.stop()

rows = pd.DataFrame([{
    "Saat": r["kickoff"], "Lig": r["competition"], "Ev sahibi": r["home"],
    "Deplasman": r["away"], "Skor": r["score"], "Hakem": r["referee"] or "Henüz açıklanmadı",
    "Durum": r["status"]
} for r in matches])
st.markdown("**Bir maçın satırına tıkla:**")
selection = st.dataframe(rows, hide_index=True, use_container_width=True, on_select="rerun",
                         selection_mode="single-row", key=f"daily_fixtures_{selected_date.isoformat()}_{'_'.join(map(str,sorted(board_ids)))}",
                         height=min(700, 80 + len(rows) * 36))
indices = selection.selection.rows
if not indices:
    st.info("☝️ Maçın hakem analizini açmak için yukarıdaki tablodan bir karşılaşma seç.")
    st.stop()

fixture = matches[indices[0]]
st.divider()
st.subheader(f"🔎 {fixture['home']} - {fixture['away']}")
st.caption(f"{fixture['competition']} · {fixture['kickoff']} · {fixture['round']}")

# Seçilmiş maçın güncel hakem bilgisini ayrıca doğrula.
try:
    refreshed = get_match_details(fixture["id"], api_key) if fixture["id"] is not None else None
    if refreshed and (refreshed.get("fixture") or {}).get("referee"):
        fixture["referee"] = refreshed["fixture"]["referee"]
except Exception as exc:
    st.caption(f"Maç detay güncellemesi alınamadı: {exc}")

referee = fixture["referee"]
if not referee:
    st.warning("Bu maçın hakemi API'de henüz açıklanmadı. Maç bültende kalır, ancak hakem analizi şimdilik açılamaz.")
    st.stop()

st.markdown(f"**Hakem:** {referee}")
if not history_ids:
    st.warning("Hakem arşivi için organizasyon seçmelisin.")
    st.stop()

# Arşiv taramaları kullanıcı bir maça tıkladığında çalışır.
with st.spinner("Hakemin geçen sezon ve bu sezon yönettiği maçlar aranıyor..."):
    bar = st.progress(0, text="Sezon ve organizasyonlar taranıyor")
    entries, failures, coverage = history_scan(history_ids, int(season), api_key, progress=bar)
    bar.empty()

filtered = [r for r in entries if is_same_referee(r.get("referee"), referee)]
# Günlük bültendeki atanmış hakem bilgisini, sezon taramasındaki boş alana güvenli biçimde ekle.
# Ancak sadece tıklanan aynı maçın ID'si ile eşleşirse ekleriz, başka maç üretmeyiz.
for row in entries:
    if row.get("id") == fixture["id"] and not row.get("referee"):
        row["referee"] = referee
        if row not in filtered:
            filtered.append(row)

# Tıklanan maç sezon arşivi uç noktasında yoksa tek maçın API detayını kullan.
if not any(r.get("id") == fixture["id"] for r in filtered) and refreshed:
    new_rows = rows_from_fixtures([refreshed], int(season), history_ids)
    filtered.extend(r for r in new_rows if is_same_referee(r.get("referee"), referee))

all_df = make_table(filtered)
if all_df.empty:
    st.error("Hakemin sezon arşivi API'de bulunamadı. Organizasyon kapsamını ve hakem ismini kontrol et.")
    st.stop()

prev = all_df[all_df.season == int(season)-1].copy()
cur = all_df[all_df.season == int(season)].copy()
cur = build_signals(prev, cur)

# Seçilen maç satırını ayrı vurgulayalım. Sinyal olmasa bile görünür.
selected_record = cur[cur.id == fixture["id"]]
if not selected_record.empty:
    r = selected_record.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bu sezon genel maç sırası", int(r.all_order))
    c2.metric("Bu sezon kategori sırası", f"{r.category} {int(r.cat_order)}")
    c3.metric("Geçen sezon aynı sıra", r.prev_order_match or "Bulunamadı")
    c4.metric("Geçen sezon aynı hafta", r.prev_week_match or "Bulunamadı")
    if r.order_signal or r.week_signal:
        st.success("📌 Geçmiş sonuç sinyali: " + " | ".join(x for x in [r.order_signal, r.week_signal] if x))
    else:
        st.info("Bu maç için 1/2 veya 2/1 geçmiş sinyali yok. Tüm maçlar aşağıda yine gösteriliyor.")
else:
    st.warning("Seçilen maçın sezon sıra numarası kesinleştirilemedi. İlgili sezon veya API bilgisi eksik olabilir.")

s1, s2, s3, s4 = st.columns(4)
s1.metric("Geçen sezon bulunan", len(prev))
s2.metric("Bu sezon bulunan", len(cur))
s3.metric("Sıra sinyali", sum(bool(x) for x in cur.order_signal) if not cur.empty else 0)
s4.metric("Hafta sinyali", sum(bool(x) for x in cur.week_signal) if not cur.empty else 0)

with st.expander("🔍 Veri kapsamı ve eksik taramalar"):
    st.caption("Bu tabloda API'den çekilen toplam maçlar gösterilir; hakemin maç sayısı değildir.")
    st.dataframe(pd.DataFrame(coverage), hide_index=True, use_container_width=True)
    if failures:
        st.error("Bazı organizasyonlar taranamadı. Sıralamalar eksik olabilir:\n" + "\n".join(failures))
    if any((r.get("referee") or "") == "" for r in entries):
        st.warning("API'de hakem adı boş olan maçlar var. Bu hakemin geçmiş maçları eksik olabilir; tüm maç sayısı kesin değildir.")
    st.caption("Taranan lig/kupa sayısı: " + str(len(history_ids)) + ". Hariç tutulan başka ülke ligleri ve API'deki boş atamalar sıralamayı etkileyebilir.")

if "card_results" not in st.session_state:
    st.session_state.card_results = {}
with st.expander("🟨🟥 Takım bazında kartları getir"):
    finished = [r for r in filtered if r.get("finished") and r.get("id") is not None]
    pending = [r for r in finished if r["id"] not in st.session_state.card_results]
    st.caption(f"{len(finished)} tamamlanmış maç; {len(pending)} maçın kartları henüz sorgulanmadı. Maç başına ayrı API istekleri kullanılır.")
    if len(pending) <= 1:
        batch = len(pending)
        st.caption("Yüklenecek maç sayısı: " + str(batch))
    else:
        batch = st.number_input("Bu sefer kaç maç?", min_value=1,
                                max_value=min(50, len(pending)), value=min(10, len(pending)))
    if st.button("Kartları yükle", disabled=not pending):
        errors = []
        for record in pending[:int(batch)]:
            try:
                cards = fetch_one_cards(record, api_key)
                if cards is None:
                    cards = {"home_yellow": None, "home_red": None, "away_yellow": None, "away_red": None}
                st.session_state.card_results[record["id"]] = cards
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            st.error("Bazı kart verileri çekilemedi: " + "; ".join(errors[:3]))
        else:
            st.success("Kart verileri güncellendi.")

prev = apply_cards(prev, st.session_state.card_results)
cur = apply_cards(cur, st.session_state.card_results)

st.caption("Lig, ulusal kupa ve üç Avrupa kupası ayrı sayılır. Geçen sezon aynı sıra / aynı hafta 1/2 veya 2/1 ise sinyal işaretlenir; sinyal yok diye maç gizlenmez.")
t1, t2, t3 = st.tabs(["📅 Bu sezon: bütün maçlar", "📚 Geçen sezon: bütün maçlar", "🚨 Sinyaller"])
with t1:
    history_view(cur, True, "current")
with t2:
    history_view(prev, False, "previous")
with t3:
    if cur.empty:
        st.info("Bu sezon maç bulunamadı.")
    else:
        filtered_signals = cur[(cur.order_signal != "") | (cur.week_signal != "")]
        if filtered_signals.empty:
            st.info("Geçmiş sonuç eşleşmesinde 1/2 veya 2/1 sinyali bulunamadı.")
        else:
            history_view(filtered_signals, True, "signals")

st.caption("⚠️ API-Football'un hakem alanı boş veya eksik olduğunda karşılaşmalar eksik eşleşebilir. Veri doğruluğunu garanti edemeyiz; bu ekran tahmin değil geçmiş veri analizidir.")
