import re
import io
import requests
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from datetime import date, datetime
import pdfplumber

# ----------------------------
# Page / UI
# ----------------------------
st.set_page_config(
    page_title="原価ダッシュボード（銅・アルミ・運賃・賃金）",
    page_icon="📈",
    layout="wide",
)

st.title("📈 原価ダッシュボード（銅・アルミ・運賃・賃金）")
st.caption("銅/アルミはFRED（USD/ton）×USDJPYで円/kg換算。運賃はWebKIT PDF。賃金はe-Stat API。")

with st.sidebar:
    st.header("📚 データ元リンク（固定表示）")
    st.markdown("""
**■ FRED**
- 銅（USD/ton）: https://fred.stlouisfed.org/series/PCOPPUSDM  
- アルミ（USD/ton）: https://fred.stlouisfed.org/series/PALUMUSDM  
- 為替（USD/JPY）: https://fred.stlouisfed.org/series/EXJPUS  

**■ WebKIT（全ト協）**
- 公表ページ: https://jta.or.jp/member/keiei/kit_release.html  

**■ e-Stat API**
- 仕様/案内: https://www.e-stat.go.jp/api/api-info/e-stat-manual  
""")

with st.expander("このダッシュボードについて", expanded=True):
    st.markdown("""
- **銅・アルミ（円/kg）**：FREDの月次（USD/ton）× USDJPY ÷ 1000  
- **運賃指数**：WebKIT成約運賃指数（PDF内の月別表）  
- **賃金（製造業）**：e-Stat APIから取得（統計表ID=statsDataId を検索して利用）  
""")

# ----------------------------
# Secrets
# ----------------------------
FRED_API_KEY = st.secrets.get("FRED_API_KEY", "")
if not FRED_API_KEY:
    st.error("FRED_API_KEY が設定されていません（Streamlit Secrets を確認）")
    st.stop()

ESTAT_APP_ID = st.secrets.get("ESTAT_APP_ID", "")
if not ESTAT_APP_ID:
    st.warning("ESTAT_APP_ID が未設定です（賃金パートは動きません）。Streamlit Secrets に追加してください。")

# ----------------------------
# FRED fetch
# ----------------------------
@st.cache_data(ttl=60 * 60)
def fetch_fred(series_id: str, start: str = "2018-01-01") -> pd.Series:
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": start,
        "observation_end": date.today().isoformat(),
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    obs = r.json()["observations"]

    df = pd.DataFrame(obs)[["date", "value"]]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    s = df.dropna().set_index("date")["value"].sort_index()
    return s

# ----------------------------
# WebKIT (latest pdf URL from JTA page)
# ----------------------------
@st.cache_data(ttl=60 * 60)
def webkit_latest_pdf_url() -> str:
    # JTAのページに毎月のPDFリンクが載る（例: /pdf/kit_release/202512.pdf）
    url = "https://jta.or.jp/member/keiei/kit_release.html"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    html = r.text

    links = re.findall(r"/pdf/kit_release/(\d{6})\.pdf", html)
    if not links:
        raise ValueError("WebKITのPDFリンクが見つかりませんでした。ページ構造が変わった可能性があります。")

    latest_yyyymm = max(links)  # 文字列比較でOK（YYYYMM）
    return f"https://jta.or.jp/pdf/kit_release/{latest_yyyymm}.pdf"

@st.cache_data(ttl=60 * 60)
def fetch_webkit_index_from_pdf(pdf_url: str) -> pd.Series:
    r = requests.get(pdf_url, timeout=60)
    r.raise_for_status()

    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)

    # 「成約運賃指数（月別）の推移」の表は
    # 平成２２年度 100 98 ... のような行で出てくる（PDFのページ2付近） :contentReference[oaicite:2]{index=2}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # 「平成」「令和」どちらも拾う（全角数字でもOKにするため \d ではなく数字抽出方式）
    target_rows = []
    for ln in lines:
        if re.match(r"^(平成|令和).+年度", ln):
            target_rows.append(ln)

    if not target_rows:
        return pd.Series(dtype="float64")

    data_points = []
    for ln in target_rows:
        # 行から数値を全部拾う（年度名の中の数字も拾うので、12個だけ使う）
        nums = re.findall(r"\d+", ln)
        if len(nums) < 12:
            # 12個ない行はスキップ（令和７年度みたいに途中までの可能性はあるので後で許容）
            pass

        # 年度ラベル抽出
        m = re.search(r"(平成|令和)\s*([0-9]+)\s*年度", ln)
        if not m:
            m = re.search(r"(平成|令和)([0-9]+)年度", ln)
        if not m:
            continue

        era = m.group(1)
        n = int(m.group(2))

        # 年度開始の西暦（4月開始）
        if era == "令和":
            start_year = 2018 + n   # 令和1=2019
        else:
            start_year = 1988 + n   # 平成1=1989

        # この行の「月別指数」部分は12個（4月〜3月）
        # ただし nums には年度番号などが混ざるので、最後の方にある数値群を優先
        # → 行末側から最大12個取る
        month_vals = list(map(int, nums[-12:]))

        months = list(range(4, 13)) + [1, 2, 3]
        years = [start_year]*9 + [start_year+1]*3

        for y, mo, v in zip(years, months, month_vals):
            data_points.append((pd.Timestamp(y, mo, 1), v))

    if not data_points:
        return pd.Series(dtype="float64")

    s = pd.Series({d: v for d, v in data_points}).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s

# ----------------------------
# e-Stat: getStatsList -> pick statsDataId -> getStatsData
# ----------------------------
@st.cache_data(ttl=60 * 60)
def estat_get_stats_list(search_word: str, stats_code: str = "00450071", limit: int = 100) -> list[dict]:
    # 仕様: 統計表情報取得（getStatsList） :contentReference[oaicite:3]{index=3}
    url = "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsList"
    params = {
        "appId": ESTAT_APP_ID,
        "searchWord": search_word,
        "statsCode": stats_code,
        "limit": limit,
        "lang": "J",
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()

    result = data.get("GET_STATS_LIST", {}).get("DATALIST_INF", {})
    tables = result.get("TABLE_INF", [])
    if isinstance(tables, dict):
        tables = [tables]

    out = []
    for t in tables:
        out.append({
            "statsDataId": t.get("@id"),
            "title": t.get("TITLE", ""),
            "updated": t.get("UPDATED_DATE", ""),
        })
    return [x for x in out if x["statsDataId"]]

@st.cache_data(ttl=60 * 60)
def estat_get_stats_data(stats_data_id: str, limit: int = 100000) -> dict:
    url = "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData"
    params = {
        "appId": ESTAT_APP_ID,
        "statsDataId": stats_data_id,
        "metaGetFlg": "Y",
        "cntGetFlg": "N",
        "limit": limit,
        "lang": "J",
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

def estat_build_class_maps(root: dict) -> dict:
    class_obj = root["CLASS_INF"]["CLASS_OBJ"]

    def to_map(obj):
        cls = obj["CLASS"]
        if isinstance(cls, dict):
            cls = [cls]
        return {c["@code"]: c["@name"] for c in cls}

    return {obj["@id"]: to_map(obj) for obj in class_obj}

def estat_series_from_statsdata(json_data: dict, *, industry_contains: str, item_contains: str) -> pd.Series:
    gsd = json_data.get("GET_STATS_DATA", {})
    stat = gsd.get("STATISTICAL_DATA")
    if not stat:
        return pd.Series(dtype="float64")

    class_maps = estat_build_class_maps(stat)
    values = stat["DATA_INF"]["VALUE"]
    if isinstance(values, dict):
        values = [values]

    # 「どのcatが何か」は統計表ごとに変わるので、名前でゆるく探す
    # → class_maps の日本語ラベルに "製造業" や "現金給与" を含むコードを集める
    industry_codes = set()
    item_codes = set()

    for dim_id, cmap in class_maps.items():
        for code, name in cmap.items():
            if industry_contains in name:
                industry_codes.add(code)
            if item_contains in name:
                item_codes.add(code)

    rows = []
    for v in values:
        # time は "@time" が基本
        t = v.get("@time")
        val = v.get("$")
        if t is None or val is None:
            continue

        dim_codes = [v[k] for k in v.keys() if k.startswith("@cat")]
        if industry_codes and (not any(c in industry_codes for c in dim_codes)):
            continue
        if item_codes and (not any(c in item_codes for c in dim_codes)):
            continue

        try:
            rows.append((t, float(val)))
        except:
            continue

    if not rows:
        return pd.Series(dtype="float64")

    # time は "202501" や "2025-01" 等が混ざることがあるので両対応
    idx = []
    vals = []
    for t, v in rows:
        t_str = str(t)
        dt = None
        for fmt in ("%Y%m", "%Y-%m", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(t_str, fmt)
                break
            except:
                pass
        if dt is None:
            # 最後の手段
            try:
                dt = pd.to_datetime(t_str)
            except:
                continue
        idx.append(pd.Timestamp(dt.year, dt.month, 1))
        vals.append(v)

    s = pd.Series(vals, index=idx).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s

# ----------------------------
# Common plot helper
# ----------------------------
def plot_with_latest_highlight(series: pd.Series, title: str, y_label: str):
    if series.empty:
        st.info("データが空です。")
        return

    fig, ax = plt.subplots()
    ax.plot(series.index, series.values)
    ax.scatter(series.index[-1], series.values[-1], s=80, zorder=3)
    ax.annotate(
        f"{series.values[-1]:,.0f}",
        (series.index[-1], series.values[-1]),
        textcoords="offset points",
        xytext=(8, 8),
        ha="left",
    )
    ax.set_title(title)
    ax.set_xlabel("Month")
    ax.set_ylabel(y_label)
    ax.grid(True, alpha=0.3)
    st.pyplot(fig, clear_figure=True)

# ----------------------------
# 1) Copper / Aluminum (FRED)
# ----------------------------
copper = fetch_fred("PCOPPUSDM")
aluminum = fetch_fred("PALUMUSDM")
usdjpy = fetch_fred("EXJPUS")

df = pd.concat([copper, aluminum, usdjpy], axis=1, join="inner")
df.columns = ["copper_usd_ton", "aluminum_usd_ton", "usdjpy"]
df["copper_jpy_kg"] = df["copper_usd_ton"] * df["usdjpy"] / 1000
df["aluminum_jpy_kg"] = df["aluminum_usd_ton"] * df["usdjpy"] / 1000

latest_date = df.index[-1]
latest_month_str = latest_date.strftime("%Y-%m")

latest_copper = float(df.loc[latest_date, "copper_jpy_kg"])
latest_aluminum = float(df.loc[latest_date, "aluminum_jpy_kg"])

delta_copper = None
delta_aluminum = None
if len(df) >= 2:
    prev_date = df.index[-2]
    delta_copper = latest_copper - float(df.loc[prev_date, "copper_jpy_kg"])
    delta_aluminum = latest_aluminum - float(df.loc[prev_date, "aluminum_jpy_kg"])

st.subheader("📌 最新月の原価（円/kg）")
k1, k2, k3 = st.columns([1, 1, 1])
with k1:
    st.metric(
        label=f"銅（{latest_month_str}）",
        value=f"{latest_copper:,.0f} 円/kg",
        delta=f"{delta_copper:+,.0f} 円/kg" if delta_copper is not None else None,
    )
with k2:
    st.metric(
        label=f"アルミ（{latest_month_str}）",
        value=f"{latest_aluminum:,.0f} 円/kg",
        delta=f"{delta_aluminum:+,.0f} 円/kg" if delta_aluminum is not None else None,
    )
with k3:
    st.metric(label="更新日時", value=datetime.now().strftime("%Y-%m-%d %H:%M"))

tab1, tab2, tab3 = st.tabs(["🟠 銅", "⚪ アルミ", "📉 まとめ（同一グラフ）"])
with tab1:
    st.subheader("銅 価格推移（円/kg）")
    plot_with_latest_highlight(df["copper_jpy_kg"], "Copper (JPY/kg)", "JPY/kg")
with tab2:
    st.subheader("アルミ 価格推移（円/kg）")
    plot_with_latest_highlight(df["aluminum_jpy_kg"], "Aluminum (JPY/kg)", "JPY/kg")
with tab3:
    st.subheader("銅・アルミ（円/kg）同一グラフ")
    st.line_chart(df[["copper_jpy_kg", "aluminum_jpy_kg"]])

st.divider()

# ----------------------------
# 2) WebKIT freight index
# ----------------------------
st.subheader("🚚 国内トラック運賃指数（WebKIT 成約運賃指数）")

try:
    pdf_url = webkit_latest_pdf_url()
    st.caption(f"最新PDF: {pdf_url}")
    webkit = fetch_webkit_index_from_pdf(pdf_url)

    if webkit.empty:
        st.warning("PDFから月別指数を抽出できませんでした（PDF構造が変わった可能性）。")
    else:
        plot_with_latest_highlight(webkit, "WebKIT Freight Index", "Index (2010-04=100)")
        st.line_chart(webkit.rename("webkit_freight_index"))
except Exception as e:
    st.error(f"WebKIT取得でエラー: {e}")

st.divider()

# ----------------------------
# 3) e-Stat wage (manufacturing)
# ----------------------------
st.subheader("💴 製造業の賃金（e-Stat API）")

if not ESTAT_APP_ID:
    st.info("ESTAT_APP_ID が未設定のため、このセクションは停止しています。")
else:
    # まず統計表を検索（statsDataIdを自動で候補にする）
    search_word = st.text_input(
        "統計表の検索ワード（例）",
        value="毎月勤労統計調査 全国調査 現金給与総額",
        help="ここで候補の統計表（statsDataId）を検索します。",
    )

    try:
        tables = estat_get_stats_list(search_word=search_word, stats_code="00450071", limit=100)

        if not tables:
            st.warning("候補が見つかりませんでした。検索ワードを変えてください。")
        else:
            # タイトルを見ながら選べるようにする（これが一番確実）
            options = {f'{t["statsDataId"]} | {t["title"]}': t["statsDataId"] for t in tables}
            selected_key = st.selectbox("取得する統計表を選択（statsDataId）", list(options.keys()))
            stats_data_id = options[selected_key]

            estat_json = estat_get_stats_data(stats_data_id)

            # “製造業” × “現金給与総額” をゆるく抽出
            wage_mfg = estat_series_from_statsdata(
                estat_json,
                industry_contains="製造業",
                item_contains="現金給与総額",
            )

            if wage_mfg.empty:
                st.warning("製造業×現金給与総額の系列が見つかりませんでした。")
                st.info("ヒント：item_contains を 'きまって支給する給与' などに変えると見つかる表もあります。")
            else:
                plot_with_latest_highlight(wage_mfg, "Manufacturing Wage (e-Stat)", "Value")
                st.line_chart(wage_mfg.rename("wage_mfg"))

    except Exception as e:
        st.error(f"e-Stat取得でエラー: {e}")

