import streamlit as st
import numpy as np
import pandas as pd
import plotly.express as px

def render():
    st.title("📊 Phân tích Hiệu quả & Rủi ro Đầu tư")
    st.markdown("Đo lường hiệu suất quá khứ, mức sụt giảm tối đa (Max Drawdown) và tỷ suất điều chỉnh rủi ro.")

    col1, col2 = st.columns(2)
    with col1:
        start_capital = st.number_input("Vốn khởi điểm ban đầu (VND)", value=1000000000.0, step=50000000.0)
    with col2:
        rf_rate = st.number_input("Lãi suất phi rủi ro tham chiếu (%/năm)", value=3.0, step=0.1) / 100

    # Giả lập chuỗi dữ liệu hiệu suất (Có thể đấu nối API hoặc file import CSV)
    np.random.seed(42)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=252, freq='B')
    daily_returns = np.random.normal(0.0006, 0.012, size=len(dates))
    portfolio_values = start_capital * (1 + pd.Series(daily_returns).cumprod())

    df_perf = pd.DataFrame({"Date": dates, "NAV": portfolio_values, "Return": daily_returns})
    df_perf["Peak"] = df_perf["NAV"].cummax()
    df_perf["Drawdown"] = (df_perf["NAV"] - df_perf["Peak"]) / df_perf["Peak"]

    # Chỉ số định lượng
    total_ret = (df_perf["NAV"].iloc[-1] - start_capital) / start_capital
    annual_ret = (1 + total_ret) ** (252 / len(df_perf)) - 1
    annual_vol = df_perf["Return"].std() * np.sqrt(252)
    sharpe = (annual_ret - rf_rate) / annual_vol if annual_vol > 0 else 0
    max_dd = df_perf["Drawdown"].min()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Tổng Tỷ suất sinh lời", f"{total_ret * 100:.2f}%")
    m2.metric("Lợi nhuận chuẩn hóa/năm", f"{annual_ret * 100:.2f}%")
    m3.metric("Sharpe Ratio", f"{sharpe:.2f}")
    m4.metric("Sụt giảm tối đa (Max DD)", f"{max_dd * 100:.2f}%")

    tab1, tab2 = st.tabs(["📈 Tăng trưởng NAV", "📉 Sụt giảm Drawdown"])
    with tab1:
        fig_nav = px.line(df_perf, x="Date", y="NAV", title="Đường cong Tăng trưởng Vốn")
        st.plotly_chart(fig_nav, use_container_width=True)
    with tab2:
        fig_dd = px.area(df_perf, x="Date", y="Drawdown", title="Biểu đồ Underwater (Drawdown Profile)")
        st.plotly_chart(fig_dd, use_container_width=True)