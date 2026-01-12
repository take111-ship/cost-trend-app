import io
import re
import requests
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from datetime import date, datetime

# ----------------------------
# Page / UI
# ----------------------------
st.set_page_config(
    page_title="銅・アルミ 原価ダッシュボード",
    page_icon="📈",
    layout="wide",
)

st.title("📈 銅・アルミ 原価指標（円/kg・月次）")
st.caption("FREDの月次データ（USD/ton）× USDJPY から円/kgに換算して表示します。")

with st.sidebar:
    st.header("📚 データ元 / 計算条件")
    st.markdown("""
**■ データ元**
- FRED  
  - 銅（PCOPPUSDM） https://fred.stlouisfed.org/series/PCOPPUSDM  
  - アルミ（PALUMUSDM） https://fred.stlouisfed.org/series/PALUMUSDM  
  - 為替 USD/JPY（EXJPUS） https://fred.stlouisfed.org/series/EXJPUS  
- e-Stat（毎月勤労統計調査 など） https://www.e-stat.go.jp/
- 全日本トラック協会 WebKIT（成約運賃指数） https://jta.or.jp/

---

**■ 計算式（銅・アルミ）**  
USD/ton × USD/JPY ÷ 1000 = 円/kg  

---

**■ 更新頻度**  
月次（各データ提供元の更新タイミングに依存）
""")

with st.expander("このダッシュボードについて", expanded=True):
    st.markdown("""
- **目的**：原材料（銅・アルミ）＋（今後：輸送費・賃金）の原価感を“月次で”つかむ  
- **計算**：USD/ton × USDJPY ÷ 1000 = **円/kg**  
- **見方**：グラフは **最新月を強調表示**、KPIは **前月比** つき  
""")

# ----------------------------
# Secrets / API key
# ----------------------------
FRED_API_KEY = st.secrets.get("FRED_API_KEY", "")
if not FRED_API_KEY:
    st.error("FRED_API_KEY が設定されていません（Streamlit Secretsを確認してください）")
    st.stop()

# e-Stat（あなたのSecretsは ESTAT_APP_ID に統一）
ESTAT_APP_ID = st.secrets.get("ESTAT_APP_ID", "")
if not ESTAT_APP_ID:
    st.warning("ESTAT_APP_ID が未設定です（賃金の取得をスキップします）")

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
    return df.dropna().set_index("date")["value"].sort_index()

# ----------------------------
# e-Stat fetch (raw JSON) + picker
# ----------------------------
@st.cache_data(ttl=60 * 60)
def fetch_estat_statsdata(stats_data_id: str, limit: int = 100000) -> dict:
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

def estat_pick_series(json_data: dict, industry_label_contains="製造業", item_label_contains=None) -> pd.Series:
    """
    返ってきたJSONから、ラベル条件（例：製造業）を含むコードを推定して
    ざっくり時系列Seriesを作る（まずは“動く”優先の抽出）。
    """
    gsd = json_data.get("GET_STATS_DATA", {})
    if "STATISTICAL_DATA" not in gsd:
        # 失敗時は空Series
        return pd.Series(dtype="float64")

    root = gsd["STATISTICAL_DATA"]
    class_inf = root["CLASS_INF"]["CLASS_OBJ"]
    values = root["DATA_INF"]["VALUE"]

    # CLASS_OBJ を name→{code:label} に整形
    def to_map(obj):
        cls = obj["CLASS"]
        if isinstance(cls, dict):
            cls = [cls]
        return {c["@code"]: c["@name"] for c in cls}

    class_maps = {obj["@id"]: to_map(obj) for obj in class_inf}

    # ラベルを含むコード候補
    industry_codes = set()
    item_codes = set()

    for _, cmap in class_maps.items():
        for code, name in cmap.items():
            if industry_label_contains and industry_label_contains in name:
                industry_codes.add(code)
            if item_label_contains and item_label_contains in name:
                item_codes.add(code)

    rows = []
    for v in values:
        t = v.get("@time") or v.get("@TIME")
        val = v.get("$")
        if t is None or val is None:
            continue

        dim_codes = [v[k] for k in v.keys() if k.startswith("@cat")]

        ok_industry = True if not industry_codes else any(c in industry_codes for c in dim_codes)
        ok_item = True if not item_label_contains else (True if not item_codes else any(c in item_codes for c in dim_codes))

        if ok_industry and ok_item:
            try:
                rows.append((t, float(val)))
            except ValueError:
                pass

    if not rows:
        return pd.Series(dtype="float64")

    s = pd.Series(
        data=[x[1] for x in rows],
        index=pd.to_datetime([x[0] for x in rows], errors="coerce")
    ).dropna().sort_index()

    # 同月重複は最後を採用
    s = s[~s.index.duplicated(keep="last")]
    return s

# ----------------------------
# WebKIT (PDF) fetch
# ----------------------------
@st.cache_data(ttl=60 * 60)
def fetch_webkit_index_from_pdf(pdf_url: str) -> pd.Series:
    import pdfplumber  # requirements.txt に pdfplumber を追加してね

    r = requests.get(pdf_url, timeout=60)
    r.raise_for_status()

    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    # 年度行っぽい行を拾う
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rows = [ln for ln in lines if ("令和" in ln and "年度" in ln) or ("平成" in ln and "年度" in ln)]

    data = []
    for ln in rows:
        # 例： "令和７年度 137 135 131 ..." みたいな並びを想定
        nums = re.findall(r"\b\d+\b", ln)
        if len(nums) < 3:
            continue

        m = re.search(r"(令和|平成)\s*([0-9]+)\s*年度", ln)
        if not m:
            # スペース無しの "令和７年度" も拾う
            m = re.search(r"(令和|平成)([0-9]+)年度", ln)
        if not m:
            continue

        era = m.group(1)
        n = int(m.group(2))
        values = list(map(int, nums))[:12]  # 4月〜3月の最大12個を想定
        data.append((era, n, values))

    series = []
    for era, n, vals in data:
        if era == "令和":
            start_year = 2018 + n  # 令和1=2019 → 2018+1
        else:
            start_year = 1988 + n  # 平成1=1989 → 1988+1

        months = list(range(4, 13)) + [1, 2, 3]
        years = [start_year] * 9 + [start_year + 1] * 3

        for y, mo, v in zip(years, months, vals):
            series.append((pd.Timestamp(y, mo, 1), v))

    if not series:
        return pd.Series(dtype="float64")

    s = pd.Series({d: v for d, v in series}).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s

# ----------------------------
# Build base df (Copper/Aluminum)
# ----------------------------
copper = fetch_fred("PCOPPUSDM")
aluminum = fetch_fred("PALUMUSDM")
usdjpy = fetch_fred("EXJPUS")

df = pd.concat([copper, aluminum, usdjpy], axis=1, join="inner")
df.columns = ["copper_usd_ton", "aluminum_usd_ton", "usdjpy"]

df["copper_jpy_kg"] = df["copper_usd_ton"] * df["usdjpy"] / 1000
df["aluminum_jpy_kg"] = df["aluminum_usd_ton"] * df["usdjpy"] / 1000

# ----------------------------
# Latest / KPI
# ----------------------------
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
    st.metric(
        label="更新日時",
        value=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

st.divider()

# ----------------------------
# Charts
# ----------------------------
def plot_with_latest_highlight(series: pd.Series, title: str, y_label: str):
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

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["🟠 銅", "⚪ アルミ", "📉 まとめ", "💴 賃金（製造業）", "🚚 トラック運賃指数"]
)

with tab1:
    st.subheader("銅 価格推移（円/kg）")
    plot_with_latest_highlight(df["copper_jpy_kg"], "Copper (JPY/kg)", "JPY/kg")
    st.caption("データ：FRED PCOPPUSDM（USD/ton）と EXJPUS（USDJPY）から換算")

with tab2:
    st.subheader("アルミ 価格推移（円/kg）")
    plot_with_latest_highlight(df["aluminum_jpy_kg"], "Aluminum (JPY/kg)", "JPY/kg")
    st.caption("データ：FRED PALUMUSDM（USD/ton）と EXJPUS（USDJPY）から換算")

with tab3:
    st.subheader("銅・アルミ（円/kg）同一グラフ")
    st.line_chart(df[["copper_jpy_kg", "aluminum_jpy_kg"]])
    st.caption("ざっくり比較したい人向け（詳細は各タブへ）")

with tab4:
    st.subheader("製造業の賃金（e-Stat）")
    if not ESTAT_APP_ID:
        st.info("ESTAT_APP_ID が未設定のため、賃金データは表示しません。")
    else:
        # あなたのe-Stat URLにあった stat_infid を statsDataId として試す
        stats_data_id = "000040277086"
        estat_json = fetch_estat_statsdata(stats_data_id)

        # 失敗時に理由を表示（赤塗り対策）
        gsd = estat_json.get("GET_STATS_DATA", {})
        if "STATISTICAL_DATA" not in gsd:
            st.error("e-Stat APIが STATISTICAL_DATA を返していません（取得失敗）")
            st.write("RESULT:", gsd.get("RESULT"))
            st.write("ERROR_MSG:", gsd.get("ERROR_MSG"))
            st.stop()

        # まずは “動く優先” の抽出（製造業を含む系列を拾う）
        wage_mfg = estat_pick_series(estat_json, industry_label_contains="製造業", item_label_contains=None)

        if wage_mfg.empty:
            st.warning("製造業の系列が見つかりませんでした（統計表ID/抽出条件の調整が必要です）")
        else:
            st.line_chart(wage_mfg.rename("wage_mfg"))
            st.caption("※まずは製造業を含む系列を推定抽出しています。次に『現金給与総額（円）』に絞る調整が可能です。")

with tab5:
    st.subheader("国内トラック運賃指数（WebKIT 成約運賃指数）")
    pdf_url = "https://jta.or.jp/pdf/kit_release/202512.pdf"  # まずは固定で動作確認
    try:
        webkit = fetch_webkit_index_from_pdf(pdf_url)
        if webkit.empty:
            st.warning("PDFから指数を抽出できませんでした（PDF形式が変わっている可能性）")
        else:
            st.line_chart(webkit.rename("webkit_freight_index"))
            st.caption(f"出典PDF：{pdf_url}")
    except Exception as e:
        st.error("PDFの取得/解析でエラーになりました。requirements.txt に pdfplumber が入っているか確認してください。")
        st.write(str(e))

st.divider()

# ----------------------------
# Data table + download (Copper/Aluminum)
# ----------------------------
st.subheader("📄 データ（ダウンロード：銅・アルミ）")

download_df = df[["copper_jpy_kg", "aluminum_jpy_kg"]].copy()
download_df = download_df.rename(
    columns={
        "copper_jpy_kg": "copper_jpy_per_kg",
        "aluminum_jpy_kg": "aluminum_jpy_per_kg",
    }
)
download_df.index.name = "date"

with st.expander("表を表示"):
    st.dataframe(download_df.tail(24), use_container_width=True)

csv_bytes = download_df.to_csv(encoding="utf-8-sig").encode("utf-8-sig")
st.download_button(
    label="📥 CSVをダウンロード",
    data=csv_bytes,
    file_name="copper_aluminum_jpy_per_kg_monthly.csv",
    mime="text/csv",
)

