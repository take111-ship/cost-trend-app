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
    **■ データ元（FRED）**  
    - 銅価格（USD/ton）  
      https://fred.stlouisfed.org/series/PCOPPUSDM  
    - アルミ価格（USD/ton）  
      https://fred.stlouisfed.org/series/PALUMUSDM  
    - 為替（USD/JPY）  
      https://fred.stlouisfed.org/series/EXJPUS  

    ---

    **■ 計算式**  
    USD/ton × USD/JPY ÷ 1000 = 円/kg  

    ---

    **■ 更新頻度**  
    月次（FREDの更新タイミングに依存）
    """)


with st.expander("このダッシュボードについて", expanded=True):
    st.markdown(
        """
- **目的**：原材料（銅・アルミ）の原価感を“月次で”つかむ  
- **計算**：USD/ton × USDJPY ÷ 1000 = **円/kg**  
- **データ**：FRED（PCOPPUSDM / PALUMUSDM / EXJPUS）  
- **見方**：グラフは **最新月を強調表示**、KPIは **前月比** つき  
"""
    )


# ----------------------------
# Secrets / API key
# ----------------------------
FRED_API_KEY = st.secrets.get("FRED_API_KEY", "")
if not FRED_API_KEY:
    st.error("FRED_API_KEY が設定されていません（Streamlit Secretsを確認してください）")
    st.stop()

# ----------------------------
# Data fetch
# ----------------------------
@st.cache_data(ttl=60 * 60)  # 1時間キャッシュ
def fetch(series_id: str, start: str = "2018-01-01") -> pd.Series:
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

copper = fetch("PCOPPUSDM")
aluminum = fetch("PALUMUSDM")
usdjpy = fetch("EXJPUS")

df = pd.concat([copper, aluminum, usdjpy], axis=1, join="inner")
df.columns = ["copper_usd_ton", "aluminum_usd_ton", "usdjpy"]

df["copper_jpy_kg"] = df["copper_usd_ton"] * df["usdjpy"] / 1000
df["aluminum_jpy_kg"] = df["aluminum_usd_ton"] * df["usdjpy"] / 1000

@st.cache_data(ttl=60*60)
def fetch_estat(stats_data_id: str, params_extra: dict) -> pd.Series:
    app_id = st.secrets.get("ESTAT_APP_ID", "")
    if not app_id:
        st.error("ESTAT_APP_ID が設定されていません（Streamlit Secretsを確認）")
        st.stop()

    url = "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData"
    params = {
        "appId": app_id,
        "statsDataId": stats_data_id,
        # "cdCat01": "...",  # 指標（現金給与総額など）
        # "cdCat02": "...",  # 産業（製造業）
        # "metaGetFlg": "N",
        "lang": "J",
    }
    params.update(params_extra)

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()

    # e-Statは構造が少し複雑なので、まずは値の入っている配列を取り出す
    values = data["GET_STATS_DATA"]["STATISTICAL_DATA"]["DATA_INF"]["VALUE"]

    df = pd.DataFrame(values)
    # df["$"] が数値、時間コードが "@time" などで入る（表により変わる）
    # ここは「あなたが選んだ表」に合わせて列名を調整する
    df["value"] = pd.to_numeric(df["$"], errors="coerce")

    # 時間キーの候補（表により違うので順に試す）
    time_key = None
    for k in ["@time", "@TIME", "@cat03", "@cat01"]:
        if k in df.columns:
            time_key = k
            break
    if time_key is None:
        raise ValueError("e-Statの時間キーが見つかりません。df.columns を確認してください。")

    # 月次にする（YYYYMM 形式が多い）
    df["date"] = pd.to_datetime(df[time_key].astype(str), format="%Y%m", errors="coerce")
    s = df.dropna(subset=["date", "value"]).set_index("date")["value"].sort_index()
    return s


# ----------------------------
# Latest / KPI
# ----------------------------
latest_date = df.index[-1]
latest_month_str = latest_date.strftime("%Y-%m")

latest_copper = float(df.loc[latest_date, "copper_jpy_kg"])
latest_aluminum = float(df.loc[latest_date, "aluminum_jpy_kg"])

# 前月比（差分）
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
# Charts (tabs) + highlight latest point
# ----------------------------
def plot_with_latest_highlight(series: pd.Series, title: str, y_label: str):
    fig, ax = plt.subplots()
    ax.plot(series.index, series.values)

    # 最新点を強調
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

tab1, tab2, tab3 = st.tabs(["🟠 銅", "⚪ アルミ", "📉 まとめ（同一グラフ）"])

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

st.divider()

# ----------------------------
# Data table + download
# ----------------------------
st.subheader("📄 データ（ダウンロード）")

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

import re

E_STAT_APP_ID = st.secrets.get("E_STAT_APP_ID", "")
if not E_STAT_APP_ID:
    st.error("E_STAT_APP_ID が設定されていません（Streamlit Secretsに追加してください）")
    st.stop()

def fetch_estat_statsdata(stats_data_id: str, limit: int = 100000):
    # e-Stat: getStatsData (JSON)
    url = "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData"
    params = {
        "appId": E_STAT_APP_ID,
        "statsDataId": stats_data_id,
        "metaGetFlg": "Y",   # メタ情報も一緒に取る（コード→日本語名の辞書に使う）
        "cntGetFlg": "N",
        "limit": limit,
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

def estat_pick_series(json_data, industry_label_contains="製造業", item_label_contains="賃金指数"):
    """
    返ってきたJSONから、
    - 産業分類で「製造業」
    - 表章項目で「賃金指数（現金給与総額）」系
    をざっくり選んで、時系列(series)にする。
    """
    root = json_data["GET_STATS_DATA"]["STATISTICAL_DATA"]
    class_inf = root["CLASS_INF"]["CLASS_OBJ"]
    values = root["DATA_INF"]["VALUE"]

    # CLASS_OBJ を name→{code:label} に整形
    def to_map(obj):
        # obj["CLASS"] は list だったり dict だったりする
        cls = obj["CLASS"]
        if isinstance(cls, dict):
            cls = [cls]
        return {c["@code"]: c["@name"] for c in cls}

    class_maps = {obj["@id"]: to_map(obj) for obj in class_inf}

    # どの次元が「産業」「表章項目」かは統計表によって違うので
    # VALUEの中のキー（例：@cat01, @cat02...）を見て総当たり気味に探す
    # まず「製造業」というラベルを含むコード候補を全部集める
    industry_codes = set()
    item_codes = set()
    for dim_id, cmap in class_maps.items():
        for code, name in cmap.items():
            if industry_label_contains in name:
                industry_codes.add(code)
            if item_label_contains in name:
                item_codes.add(code)

    # 実データをなめて、製造業×賃金指数っぽいものを拾う
    rows = []
    for v in values:
        time = v.get("@time")
        val = v.get("$")
        # 各次元のコードを拾う（@cat01, @cat02... のようなもの）
        dim_codes = [v[k] for k in v.keys() if k.startswith("@cat")]
        if any(c in industry_codes for c in dim_codes) and (len(item_codes) == 0 or any(c in item_codes for c in dim_codes)):
            rows.append((time, float(val)))

    if not rows:
        return pd.Series(dtype="float64")

    s = pd.Series(
        data=[x[1] for x in rows],
        index=pd.to_datetime([x[0] for x in rows])
    ).sort_index()

    # 同じ月が重複することがあるので、最後を採用
    s = s[~s.index.duplicated(keep="last")]
    return s

# ---- ここで実際に取得（例のstatsDataId）----
estat_json = fetch_estat_statsdata("000008232508")  # 毎月勤労統計調査（産業別賃金指数の例）:contentReference[oaicite:6]{index=6}
wage_mfg = estat_pick_series(estat_json, industry_label_contains="製造業", item_label_contains="賃金指数")

st.subheader("製造業の賃金指標（e-Stat）")
if wage_mfg.empty:
    st.warning("製造業の系列が見つかりませんでした（表の選び方を調整します）")
else:
    st.line_chart(wage_mfg.rename("wage_index_mfg"))

import io
import pdfplumber

def fetch_webkit_index_from_pdf(pdf_url: str):
    r = requests.get(pdf_url, timeout=60)
    r.raise_for_status()

    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    # 「成約運賃指数（月別）の推移」テーブル周辺を使う
    # 例: "令和７年度 137 135 131 135 143 138 137 141 146"
    # 年度行を拾って、年度→4月〜3月の値に変換
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rows = [ln for ln in lines if ln.startswith("令和") or ln.startswith("平成")]

    data = []
    for ln in rows:
        # 年度名 + 数字を拾う
        nums = re.findall(r"\b\d+\b", ln)
        if len(nums) < 3:
            continue
        year_label = ln.split()[0]  # "令和７年度" など
        values = list(map(int, nums))  # 100 98 ... の部分
        # 4月〜3月（最大12個）として扱う
        values = values[:12]
        data.append((year_label, values))

    # 年度→月へ展開（ざっくり：年度は4月スタート）
    # "令和７年度" → 2025年度（令和7=2025）なので 2025-04〜
    series = []
    for year_label, vals in data:
        m = re.search(r"(令和|平成)(\d+)年度", year_label)
        if not m:
            continue
        era, n = m.group(1), int(m.group(2))
        if era == "令和":
            start_year = 2018 + n  # 令和1=2019 → 2018+1
        else:
            start_year = 1988 + n  # 平成1=1989 → 1988+1
        # 4月〜12月（9個）+ 1月〜3月（3個）
        months = list(range(4, 13)) + [1, 2, 3]
        years = [start_year]*9 + [start_year+1]*3
        for y, mo, v in zip(years, months, vals):
            series.append((pd.Timestamp(y, mo, 1), v))

    s = pd.Series({d: v for d, v in series}).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s

st.subheader("国内トラック運賃指数（WebKIT 成約運賃指数）")
# 最新のPDF（例：2025年12月分のPDF）:contentReference[oaicite:9]{index=9}
webkit = fetch_webkit_index_from_pdf("https://jta.or.jp/pdf/kit_release/202512.pdf")
st.line_chart(webkit.rename("webkit_freight_index"))



