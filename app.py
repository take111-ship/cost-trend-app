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
