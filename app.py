import streamlit as st
from modules import (
    stock_valuation,
    portfolio_mgmt,
    performance_analytics,
    crypto_valuation
)

st.set_page_config(
    page_title="VAM Multi-Asset Intelligence Platform",
    page_icon="📊",
    layout="wide"
)

# KÍCH HOẠT 2 TIẾN TRÌNH LẬP LỊCH CHẠY NGẦM ĐỘC LẬP NGAY TỪ ĐẦU
stock_valuation.start_background_bot()
crypto_valuation.start_crypto_background_worker()

NAV_MODULES = {
    "🏛️ Định giá TTCK (VAM Core)": stock_valuation.render,
    "💼 Quản lý Danh mục": portfolio_mgmt.render,
    "📈 Phân tích Hiệu quả Đầu tư": performance_analytics.render,
    "⚡ Định giá Thị trường Crypto": crypto_valuation.render,
}

st.sidebar.markdown("# 🧭 VAM Terminal")
selected_module = st.sidebar.radio(
    "Lựa chọn không gian làm việc:",
    list(NAV_MODULES.keys()),
    index=0
)
st.sidebar.markdown("---")

NAV_MODULES[selected_module]()