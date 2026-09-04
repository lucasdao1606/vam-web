import streamlit as st
import pandas as pd
import plotly.graph_objects as go

def render():
    st.title("⚡ Định giá Thị trường Crypto & On-chain Indicators")
    st.markdown("Hệ thống chỉ báo chu kỳ, định giá Bitcoin & thị trường tài sản số.")

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("1. Tham số On-chain chủ chốt")
        btc_price = st.number_input("Giá Bitcoin hiện tại (USD)", value=92500.0, step=500.0)
        mvrv_ratio = st.number_input("MVRV Z-Score (Market-to-Realized Value)", value=2.35, step=0.05,
                                     help="Dưới 0.1: Vùng đáy cực rẻ. Trên 6.0: Đỉnh bong bóng.")
        mayer_mult = st.number_input("Mayer Multiple (Price / 200-Day SMA)", value=1.28, step=0.02,
                                     help="Dưới 0.8: Vùng tích lũy mạnh. Trên 2.4: Vùng quá nhiệt.")
        fear_greed = st.slider("Chỉ số Fear & Greed (0: Cực kỳ sợ hãi, 100: Cực kỳ tham lam)", 0, 100, 72)

    # Đánh giá chu kỳ Crypto
    valuation_signals = []
    if mvrv_ratio < 1.0:
        valuation_signals.append(("MVRV Ratio", "Rất Rẻ (Undervalued)", "Vốn hóa thị trường thấp hơn giá trị thực tế on-chain."))
    elif mvrv_ratio > 3.5:
        valuation_signals.append(("MVRV Ratio", "Quá Nhiệt (Overheated)", "Nhà đầu tư đang có tỷ lệ lãi chưa chốt rất cao, rủi ro xả hàng lớn."))
    else:
        valuation_signals.append(("MVRV Ratio", "Trung Tính / Pha Tăng Trưởng", "Biên độ định giá nằm trong giới hạn bền vững."))

    if mayer_mult < 0.8:
        valuation_signals.append(("Mayer Multiple", "Mua Tích Lũy", "Giá lệch âm đáng kể so với đường cơ sở 200 ngày."))
    elif mayer_mult > 2.4:
        valuation_signals.append(("Mayer Multiple", "Vùng Bong Bóng", "Mức độ co dãn giá vượt ngưỡng an toàn lịch sử."))
    else:
        valuation_signals.append(("Mayer Multiple", "Bình Thường", "Xu hướng tăng duy trì độ dốc chuẩn hóa."))

    with col2:
        st.subheader("2. Trạng thái Định giá Chu kỳ")
        df_crypto_eval = pd.DataFrame(valuation_signals, columns=["Chỉ báo", "Trạng thái", "Diễn giải"])
        st.dataframe(df_crypto_eval, use_container_width=True, hide_index=True)

        # Radar Gauge tâm lý thị trường
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=fear_greed,
            title={'text': "Tâm lý Thị trường (Fear & Greed)"},
            gauge={
                'axis': {'range': [0, 100]},
                'bar': {'color': "#2563EB"},
                'steps': [
                    {'range': [0, 25], 'color': "#EF4444"},
                    {'range': [25, 45], 'color': "#F97316"},
                    {'range': [45, 55], 'color': "#EAB308"},
                    {'range': [55, 75], 'color': "#84CC16"},
                    {'range': [75, 100], 'color': "#22C55E"}
                ]
            }
        ))
        fig.update_layout(height=280, margin=dict(l=20, r=20, t=30, b=20))
        st.plotly_chart(fig, use_container_width=True)