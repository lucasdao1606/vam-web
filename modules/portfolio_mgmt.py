import streamlit as st
import pandas as pd
import plotly.express as px

def render():
    st.title("💼 Quản lý Danh mục Đầu tư & Tái Cân Bằng")
    st.markdown("Theo dõi danh mục tài sản thực tế và đối chiếu với khuyến nghị phân bổ của VAM.")

    # Tỷ trọng mục tiêu lấy từ VAM nếu đã chạy, nếu chưa thì dùng mặc định
    last_res = st.session_state.get("last_result")
    target_eq = getattr(last_res, "equity_weight", 60.0)
    target_bd = getattr(last_res, "bond_weight", 30.0)
    target_gd = getattr(last_res, "gold_weight", 10.0)

    if "user_portfolio" not in st.session_state:
        st.session_state.user_portfolio = [
            {"Tài sản": "Cổ phiếu (VN30/Cơ sở)", "Giá trị hiện tại (VND)": 650000000.0, "Loại": "Cổ phiếu"},
            {"Tài sản": "Trái phiếu doanh nghiệp/Tiết kiệm", "Giá trị hiện tại (VND)": 250000000.0, "Loại": "Trái phiếu"},
            {"Tài sản": "Vàng vật chất / SJC", "Giá trị hiện tại (VND)": 100000000.0, "Loại": "Vàng"},
        ]

    st.subheader("1. Cập nhật số dư thực tế")
    df_current = pd.DataFrame(st.session_state.user_portfolio)
    edited_df = st.data_editor(df_current, num_rows="dynamic", use_container_width=True)
    st.session_state.user_portfolio = edited_df.to_dict("records")

    total_nav = edited_df["Giá trị hiện tại (VND)"].sum()
    st.metric("Tổng giá trị tài sản ròng (NAV)", f"{total_nav:,.0f} VND")

    if total_nav > 0:
        agg = edited_df.groupby("Loại")["Giá trị hiện tại (VND)"].sum().reset_index()
        agg["Tỷ trọng hiện tại (%)"] = (agg["Giá trị hiện tại (VND)"] / total_nav) * 100
        
        target_map = {"Cổ phiếu": target_eq, "Trái phiếu": target_bd, "Vàng": target_gd}
        agg["Tỷ trọng mục tiêu (%)"] = agg["Loại"].map(target_map).fillna(0.0)
        agg["Chênh lệch (%)"] = agg["Tỷ trọng hiện tại (%)"] - agg["Tỷ trọng mục tiêu (%)"]
        agg["Lệnh tái cân bằng (VND)"] = ((agg["Tỷ trọng mục tiêu (%)"] - agg["Tỷ trọng hiện tại (%)"]) / 100) * total_nav

        st.subheader("2. Đối chuẩn Tỷ trọng & Tái cân bằng")
        col1, col2 = st.columns([1.2, 1])
        with col1:
            st.dataframe(agg.style.format({
                "Giá trị hiện tại (VND)": "{:,.0f}",
                "Tỷ trọng hiện tại (%)": "{:.2f}%",
                "Tỷ trọng mục tiêu (%)": "{:.2f}%",
                "Chênh lệch (%)": "{:+.2f}%",
                "Lệnh tái cân bằng (VND)": "{:+,.0f}"
            }), use_container_width=True)
        with col2:
            fig = px.bar(
                agg, x="Loại", y=["Tỷ trọng hiện tại (%)", "Tỷ trọng mục tiêu (%)"],
                barmode="group", title="So sánh Hiện tại vs Target VAM"
            )
            st.plotly_chart(fig, use_container_width=True)
			