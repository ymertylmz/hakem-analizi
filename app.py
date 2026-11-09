import os
import re
import unicodedata
import requests
import pandas as pd
import streamlit as st
from engine import EUROPE_IDS, allowed, rows_from_fixtures, make_table, build_signals, cards_from_events, apply_cards

st.set_page_config(page_title="Hakem Analizi", page_icon="⚽", layout="wide")
st.title("⚽ Hakem Analizi")
st.caption("Geçen sezon + bu sezon | Bütün maçlar daima listede | Sıra ve hafta sinyalleri yalnızca ek işaret")

BASE = "https://v3.football.api-sports.io"

# API aynı hakemi "Halil Umut Meler", "Halil Umut Meler, Türkiye"
# veya "H. U. Meler" şeklinde yazabilir. Ülke ekini kaldırıp
# adın yalnızca ilk harfle yazılmasına da kontrollü izin veriyoruz.
COUNTRY_SUFFIXES = {
    "turkiye", "turkey", "turkiye republic", "turkish", "türkiye",
    "england", "spain", "germany", "france", "italy", "portugal",
    "netherlands", "brazil", "argentina", "australia", "norway",
}


def fold(text):
    value = str(text or "").replace("ı", "i").replace("İ", "I")
    value = unicodedata.normalize("NFKD", value)
    return "".join(c for c in value if not unicodedata.combining(c)).casefold().strip()


def referee_tokens(raw):
    parts = [x.strip() for x in str(raw or "").split(",")]
    if len(parts) >= 2:
        trailing = fold(parts[-1])
        if trailing in COUNTRY_SUFFIXES:
            parts.pop()
        elif len(parts) == 2:
            # "Meler, Halil Umut" biçimi
            parts = [parts[1], parts[0]]
    return re.findall(r"[a-z0-9]+", fold(" ".join(parts)))


def same_referee(raw, query, exact=False):
    a, b = referee_tokens(raw), referee_tokens(query)
    if not a or not b:
        return False
    if a == b:
        return True
    if exact:
        return False
    if len(a) >= len(b) and a[:len(b)] == b:
        return True
    if len(b) >= len(a) and b[:len(a)] == a:
        return True
    # Tam soyadı aynıysa kısaltılmış adlar eşleştirilebilir.
    if len(a) >= 2 and len(b) >= 2 and a[-1] == b[-1]:
        aa, bb = a[:-1], b[:-1]
        short, full = (aa, bb) if len(aa) <= len(bb) else (bb, aa)
        if all(x and y and x[0] == y[0] for x, y in zip(short, full)):
            return True
    return False


def eligible_competition(meta):
    if not allowed(meta):
        return False
    name = fold(meta.get("name", ""))
    blocked = (
        r"\bkadin", r"\bbayan", r"\bwomen", r"\bgirls",
        r"\bfriendly", r"\bhazirlik", r"\breserve",
        r"\byouth", r"\bakademi", r"\bacademy",
        r"\bu\s*[-]?\s*1[0-9]\b", r"\bu\s*[-]?\s*2[0-3]\b",
        r"\bamator", r"\bamateur",
    )
    return not any(re.search(pattern, name) for pattern in blocked)

@st.cache_data(ttl=3600, show_spinner=False)
def api(endpoint, params, key):
    try:
        response = requests.get(f"{BASE}/{endpoint}", params=dict(params), headers={"x-apisports-key": key}, timeout=35)
        response.raise_for_status()
        data = response.json()
        if data.get("errors"):
            raise RuntimeError(str(data["errors"]))
        return data.get("response") or []
    except requests.RequestException as exc:
        raise RuntimeError(f"API bağlantısı başarısız: {exc}") from exc

@st.cache_data(ttl=10800, show_spinner=False)
def get_leagues(country, key):
    return api("leagues", (("country", country),), key)

@st.cache_data(ttl=1800, show_spinner=False)
def get_fixtures(lid, season, key):
    return api("fixtures", (("league", lid), ("season", season), ("timezone", "Europe/Istanbul")), key)

@st.cache_data(ttl=86400, show_spinner=False)
def get_events(fid, key):
    return api("fixtures/events", (("fixture", int(fid)),), key)

try:
    secret = st.secrets.get("API_FOOTBALL_KEY", "")
except Exception:
    secret = ""
key = secret or os.getenv("API_FOOTBALL_KEY", "")
if not key:
    key = st.text_input("API-Football anahtarın", type="password", help="GitHub'a anahtar yükleme. Streamlit secrets tercih et.")
if not key:
    st.info("Başlamak için API-Football anahtarını gir. Kurulum README dosyasında.")
    st.stop()

with st.sidebar:
    st.header("Tarama ayarları")
    season = st.number_input("Bu sezonun başlangıç yılı", min_value=2015, max_value=2035, value=2026, step=1)
    st.caption(f"Karşılaştırma: {season-1}/{season} ile {season}/{season+1}. Takvim yılı esaslı liglerde API'nin sezon yılı farklı olabilir.")
    country = st.selectbox("Ülke", ["Turkey", "England", "Spain", "Germany", "Italy", "France", "Portugal", "Netherlands", "Norway", "Brazil", "Argentina", "Australia"], index=0)
    europe = st.multiselect("Avrupa organizasyonları", list(EUROPE_IDS.values()), default=list(EUROPE_IDS.values()))
    st.caption("Lig/kupa/ŞL/AL/KL ayrı sayılır. Seçilmeyen yarışmalardan hakem maçı bulunamaz.")

try:
    competitions = get_leagues(country, key)
except Exception as exc:
    st.error(str(exc))
    st.stop()

options = {}
for item in competitions:
    meta = item.get("league") or {}
    if not eligible_competition(meta):
        continue
    lid = meta.get("id")
    if not lid:
        continue
    # Avoid stale or unsupported seasons
    years = {s.get("year") for s in item.get("seasons", [])}
    if not ({int(season), int(season) - 1} & years):
        continue
    label = f"{meta.get('name')} ({meta.get('type')}, ID {lid})"
    options[label] = int(lid)

# Türkiye'de Süper Lig + 1/2/3. Lig grupları + ulusal kupalar.
# API'de gerçekten sunulan organizasyonları /leagues sonucundan seçiyoruz.
DEFAULT_TURKEY = (
    "super lig", "1. lig", "2. lig", "3. lig",
    "turkiye kupasi", "turkish cup", "super cup", "super kupa",
)
league_default = [
    label for label in options
    if country == "Turkey"
    and any(fold(label).startswith(term) for term in DEFAULT_TURKEY)
]

if country == "Turkey":
    st.caption(
        "Türkiye için Süper Lig, 1./2./3. ligler ve ulusal kupalar "
        "varsayılan seçilir. API'de bulunmayan organizasyon seçilemez."
    )

selected_names = st.multiselect("Taranacak ligler ve ulusal kupalar", list(options), default=league_default,
                                help="Eksiksiz hakem arşivi için hakemin görev aldığı bütün profesyonel organizasyonları seç.")
selected = {options[x] for x in selected_names}
for lid, name in EUROPE_IDS.items():
    if name in europe:
        selected.add(lid)
extra = st.text_input("İlave organizasyon ID'leri (isteğe bağlı)", placeholder="Örnek: 203, 204")
try:
    selected.update(int(x.strip()) for x in extra.split(",") if x.strip())
except ValueError:
    st.error("İlave organizasyon ID alanına yalnızca sayılar yaz.")
    st.stop()

st.divider()
col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    search_name = st.text_input("Hakem adı", placeholder="Örn. Halil Umut Meler")
with col2:
    exact = st.checkbox("Tam isim eşleşmesi", value=False)
with col3:
    run = st.button("🔎 Hakemi analiz et", type="primary", use_container_width=True)

if run:
    if not selected or len(norm_ref(search_name)) < 3:
        st.warning("Hakem adını gir ve en az bir organizasyon seç.")
    else:
        all_data = []
        fails = []
        scan_report = []
        competition_labels = {v: k for k, v in options.items()}
        competition_labels.update({k: v for k, v in EUROPE_IDS.items()})
        total = 2 * len(selected)
        bar = st.progress(0, text=f"Toplam {total} sezon/organizasyon taraması")
        for i, (yr, lid) in enumerate((yr, lid) for yr in (int(season)-1, int(season)) for lid in sorted(selected)):
            try:
                records = get_fixtures(lid, yr, key)
                valid = rows_from_fixtures(records, yr, selected)
                matched = [r for r in valid if same_referee(r["referee"], search_name, exact=exact)]
                all_data.extend(matched)
                scan_report.append({
                    "Sezon": f"{yr}-{yr+1}",
                    "Organizasyon": competition_labels.get(lid, str(lid)),
                    "API maç": len(records),
                    "Hakem alanı dolu": sum(bool(r["referee"]) for r in valid),
                    "Eşleşen maç": len(matched),
                    "Durum": "Tamam",
                })
            except Exception as e:
                message = f"{lid}, {yr}: {str(e)[:170]}"
                fails.append(message)
                scan_report.append({
                    "Sezon": f"{yr}-{yr+1}",
                    "Organizasyon": competition_labels.get(lid, str(lid)),
                    "API maç": None, "Hakem alanı dolu": None,
                    "Eşleşen maç": None, "Durum": f"HATA: {str(e)[:110]}",
                })
            bar.progress((i+1)/total)
        bar.empty()
        st.session_state["results"] = dict(
            rows=all_data, failures=fails, report=scan_report,
            prev=int(season)-1, cur=int(season), searched=search_name,
            coverage=len(selected), selected=tuple(sorted(selected)),
        )
        st.session_state["cards"] = {}

info = st.session_state.get("results")
if not info:
    st.info("Organizasyonlarını seçip hakemin adını yazarak taramayı başlat.")
    st.stop()

if info["failures"]:
    with st.expander(f"⚠️ {len(info['failures'])} tarama başarısız. Sonuçlar eksik olabilir."):
        st.code("\n".join(info["failures"]))

# Aynı hakemin API'deki farklı isim varyantlarını AYIRMADAN birleştir.
variants = sorted({r["referee"] for r in info["rows"] if r["referee"]})
if variants:
    st.caption("Birleştirilen hakem isimleri: " + " | ".join(variants))

# İki sezonun GENEL SIRA sayacı da ayrı ayrı 1'den başlamalı.
previous = make_table([r for r in info["rows"] if r["season"] == info["prev"]])
current = make_table([r for r in info["rows"] if r["season"] == info["cur"]])

with st.expander("🔍 Veri kontrolü: Hangi ligde kaç maç bulundu?", expanded=False):
    report_df = pd.DataFrame(info.get("report", []))
    if not report_df.empty:
        st.dataframe(report_df, hide_index=True, use_container_width=True)
    st.caption(
        "API maç = organizasyondan dönen toplam kayıt. "
        "Hakem alanı dolu = API'nin hakem bilgisi verdiği kayıt. "
        "Eşleşen maç = aranan hakemle eşleşen kayıt. "
        "0 maç bulunduysa bu, hakemin gerçekte maç yönetmediğini kanıtlamaz."
    )

if previous.empty and current.empty:
    st.warning(
        "Seçilen organizasyonlarda bu hakeme ait maç eşleşmedi. "
        "Yukarıdaki Veri kontrolü panelini açıp tarama hatalarını, "
        "hakem alanlarının doluluğunu ve lig seçimini incele."
    )
    st.stop()

if previous.empty:
    st.warning(
        "Geçen sezondan eşleşen kayıt yok. Sıra ve hafta sinyalleri "
        "hesaplanamaz; bu, hakemin geçen sezon 0 maç yönettiği anlamına gelmez."
    )
if current.empty:
    st.warning("Bu sezon henüz eşleşen kayıt bulunamadı.")

current = build_signals(previous, current)

cards = st.session_state.get("cards", {})
with st.expander("🟨🟥 Kart verilerini yükle", expanded=False):
    st.warning("Kart detayları maç başına ayrı API isteği gerektirir. Bilinmeyen kart verisini 0 olarak göstermiyoruz. API kapsamına göre bilgiler eksik kalabilir.")
    all_df = pd.concat([previous, current], ignore_index=True)
    finished = all_df[all_df.finished & all_df.id.notna()]
    to_fetch = finished[~finished.id.isin(cards.keys())]
    remaining = len(to_fetch)
    if remaining == 0:
        st.info("Kart bilgisi yüklenecek yeni tamamlanmış maç kalmadı.")
        max_batch = 0
    elif remaining == 1:
        st.caption("Kart bilgisi yüklenecek 1 maç kaldı.")
        max_batch = 1
    else:
        max_batch = st.slider(
            "Bu sefer kaç maçın kartını yükleyelim?",
            min_value=1,
            max_value=min(100, remaining),
            value=min(10, remaining),
        )
    if st.button("Seçilen sayıdaki maçın kartlarını getir", disabled=to_fetch.empty):
        failures = []
        for row in to_fetch.head(max_batch).itertuples():
            try:
                events = get_events(row.id, key)
                result = cards_from_events(events, row.home_id, row.away_id)
                cards[row.id] = result if result is not None else {"home_yellow": "?", "home_red": "?", "away_yellow": "?", "away_red": "?"}
            except Exception as exc:
                failures.append(f"{row.id}: {exc}")
        st.session_state["cards"] = cards
        if failures:
            st.error("Kart isteklerinden bazıları başarısız: " + "; ".join(failures[:5]))
        else:
            st.success("Kart sorguları tamamlandı.")

previous = apply_cards(previous, cards)
current = apply_cards(current, cards)
metrics = st.columns(4)
metrics[0].metric("Geçen sezon bulunan maç", len(previous))
metrics[1].metric("Bu sezon bulunan maç", len(current))
metrics[2].metric("Sıra sinyali", sum(bool(x) for x in current.order_signal) if not current.empty else 0)
metrics[3].metric("Hafta sinyali", sum(bool(x) for x in current.week_signal) if not current.empty else 0)

st.info("Sinyal olmasa bile tüm bulunan maçlar listelenir. Sıra sinyali: geçen sezon aynı kategorideki aynı sıradaki maç 1/2 veya 2/1. Hafta sinyali: aynı kategoride, API'nin sayısal tur/hafta değeri aynı olan geçmiş maçta 1/2 veya 2/1. Bu sonuçlar tahmin garantisi değildir.")


def display(df, heading, show_signal=False):
    st.subheader(heading)
    if df.empty:
        st.info("Bu dönem için seçili organizasyonlarda hakemin maçı bulunamadı.")
        return
    view = df.copy()
    def show_cards(a,b):
        if pd.isna(a) or pd.isna(b) or a == "?" or b == "?":
            return "Veri yok"
        return f"🟨 {a}  🟥 {b}"
    view["Ev kart"] = [show_cards(x,y) for x,y in zip(view.home_yellow,view.home_red)]
    view["Dep kart"] = [show_cards(x,y) for x,y in zip(view.away_yellow,view.away_red)]
    view["Tarih"] = view.date_sort.dt.tz_convert("Europe/Istanbul").dt.strftime("%d.%m.%Y %H:%M")
    cols = {"all_order":"Genel sıra", "category":"Kategori", "cat_order":"Kategori sıra", "league":"Organizasyon", "round":"Hafta / Tur", "home":"Ev sahibi", "away":"Deplasman", "halftime":"İY skor", "fulltime":"MS skor", "iy_ms":"İY/MS", "status":"Durum"}
    keys = ["Tarih", "all_order", "category", "cat_order", "league", "round", "home", "away", "halftime", "fulltime", "iy_ms", "Ev kart", "Dep kart", "status"]
    if show_signal:
        cols.update({"order_signal":"Sıra sinyali", "week_signal":"Hafta sinyali", "prev_order_match":"Geçen sezon aynı sıra", "prev_week_match":"Geçen sezon aynı hafta"})
        keys += ["order_signal", "week_signal", "prev_order_match", "prev_week_match"]
    output = view[keys].rename(columns=cols)
    st.dataframe(output, hide_index=True, use_container_width=True, height=min(650, 70 + 36*len(output)))
    st.download_button("CSV indir", output.to_csv(index=False).encode("utf-8-sig"), file_name=f"hakem_{heading.lower().replace(' ','_')}.csv", mime="text/csv")


tab_cur, tab_prev, tab_signal = st.tabs(["📅 Bu sezon tüm maçlar", "🗃️ Geçen sezon tüm maçlar", "🚨 Sinyaller"])
with tab_cur:
    display(current, f"{info['cur']}-{info['cur']+1}: tüm bulunan maçlar", True)
with tab_prev:
    display(previous, f"{info['prev']}-{info['prev']+1}: tüm bulunan maçlar")
with tab_signal:
    if current.empty:
        st.info("Bu sezon maç yok.")
    else:
        hits = current[(current.order_signal != "") | (current.week_signal != "")]
        display(hits, "Geçmişte 1/2 veya 2/1 görülen karşılıklar", True)

st.caption("ÖNEMLİ: API'de hakem alanı boş olan maçlar bu isimle eşleştirilemez. Taranmamış ligler/kupalar ve atanmamış gelecek hakemleri eksik kalır; bu nedenle sıra numaralarının kesinliği seçilen organizasyonların eksiksizliğine bağlıdır. Avrupa organizasyonlarının her biri ayrı kategori olarak sayılır.")
