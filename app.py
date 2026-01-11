import pandas as pd
import matplotlib.pyplot as plt
import streamlit as st
from datetime import datetime, timezone, timedelta

st.title("原価推移グラフ")
st.caption("CSVを読み込み、月次の原価推移を可視化します。")

df = pd.read_csv("cost.csv")

# Jan-24 形式を明示的に日付へ変換
df["month"] = pd.to_datetime(df["month"], format="%b-%y")
df = df.sort_values("month")

latest = df.iloc[-1]
latest_month = latest["month"]
latest_cost = latest["cost"]

# 運用で重要：データ最終月と更新日時（JST）
data_last_month = latest_month.strftime("%Y-%m")
JST = timezone(timedelta(hours=9))
updated_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M")

col1, col2 = st.columns(2)
col1.metric("データ最終月", data_last_month)
col2.metric("更新日時", updated_at)

fig, ax = plt.subplots()
ax.plot(df["month"], df["cost"], marker="o")
ax.set_xlabel("Month")
ax.set_ylabel("Cost")
ax.set_title("Cost Trend")

# 最新月を強調
ax.scatter([latest_month], [latest_cost], s=120)
ax.text(latest_month, latest_cost, f"  {int(latest_cost)}", va="center")

st.pyplot(fig)

st.write("最新月:", data_last_month, " / 原価:", int(latest_cost))

