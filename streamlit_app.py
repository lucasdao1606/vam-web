import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta

# ==========================================
# 1. TRANG TRÍ & CẤU HÌNH TRANG (PAGE CONFIG)
# ==========================================
st.set_page_config(
    page_title="Báo Cáo Quản Lý Kinh Doanh",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS cho giao diện chuyên nghiệp
st.markdown("""
    <style>
    .main {
        background-color: #f8f9fa;
    }
    .stMetric {
        background-color: #ffffff;
        padding: 15px;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        border: 1px solid #e9ecef;
    }
    .metric-card {
        background-color: #ffffff;
        border-radius: 8px;
        padding: 16px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. GIẢ LẬP DỮ LIỆU (MOCK DATA GENERATION)
# ==========================================
@st.cache_data
def load_data():
    np.random.seed(42)
    n_rows = 500
    
    dates = [datetime(2025, 1, 1) + timedelta(days=int(i)) for i in np.random.randint(0, 365, n_rows)]
    categories = ['Điện thoại', 'Laptop', 'Phụ kiện', 'Thiết bị đeo', 'Đồ gia dụng']
    regions = ['Miền Bắc', 'Miền Trung', 'Miền Nam']
    sales_reps = ['Nguyễn Văn A', 'Trần Thị B', 'Lê Văn C', 'Phạm Thị D', 'Hoàng Văn E']
    
    data = pd.DataFrame({
        'Mã Đơn Hàng': [f"DH-{1000 + i}" for i in range(n_rows)],
        'Ngày': dates,
        'Danh Mục': np.random.choice(categories, n_rows, p=[0.25, 0.2, 0.3, 0.15, 0.1]),
        'Khu Vực': np.random.choice(regions, n_rows, p=[0.4, 0.25, 0.35]),
        'Nhân Viên': np.random.choice(sales_reps, n_rows),
        'Số Lượng': np.random.randint(1, 10, n_rows),
        'Đơn Giá': np.random.choice([200000, 500000, 1500000, 5000000, 15000000, 25000000], n_rows),
        'Đánh Giá (Sao)': np.random.randint(3, 6, n_rows)
    })
    
    data['Doanh Thu'] = data['Số Lượng'] * data['Đơn Giá']
    data['Lợi Nhuận'] = data['Doanh Thu'] * np.random.uniform(0.1, 0.3, n_rows)
    data['Tháng'] = data['Ngày'].dt.strftime('%Y-%m')
    data['Thứ'] = data['Ngày'].dt.day_name()
    
    return data.sort_values('Ngày')

df_raw = load_data()

# ==========================================
# 3. THANH ĐIỀU HƯỚNG BÊN (SIDEBAR FILTERS)
# ==========================================
st.sidebar.image("https://cdn-icons-png.flaticon.com/512/3135/3135715.png", width=80)
st.sidebar.title("📌 Bộ Lọc Dữ Liệu")

# Chọn khoảng thời gian
min_date = df_raw['Ngày'].min().date()
max_date = df_raw['Ngày'].max().date()

start_date, end_date = st.sidebar.date_input(
    "Thời gian:",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date
)

# Lọc theo Khu Vực
selected_regions = st.sidebar.multiselect(
    "Chọn Khu Vực:",
    options=df_raw['Khu Vực'].unique(),
    default=df_raw['Khu Vực'].unique()
)

# Lọc theo Danh Mục
selected_categories = st.sidebar.multiselect(
    "Chọn Danh Mục Sản Phẩm:",
    options=df_raw['Danh Mục'].unique(),
    default=df_raw['Danh Mục'].unique()
)

# Áp dụng bộ lọc vào DataFrame
df_filtered = df_raw[
    (df_raw['Ngày'].dt.date >= start_date) &
    (df_raw['Ngày'].dt.date <= end_date) &
    (df_raw['Khu Vực'].isin(selected_regions)) &
    (df_raw['Danh Mục'].isin(selected_categories))
]

st.sidebar.markdown("---")
st.sidebar.info("💡 **Mẹo:** Thay đổi bộ lọc để cập nhật dữ liệu tự động trên toàn bộ ứng dụng.")

# ==========================================
# 4. GIAO DIỆN CHÍNH (MAIN DASHBOARD)
# ==========================================

# Header chính
st.title("📈 Báo Cáo Phân Tích Doanh Số & Kinh Doanh")
st.markdown(f"Dữ liệu từ **{start_date.strftime('%d/%m/%Y')}** đến **{end_date.strftime('%d/%m/%Y')}**")

st.markdown("---")

# --- KHU VỰC METRICS (KPIs) ---
kpi1, kpi2, kpi3, kpi4 = st.columns(4)

total_revenue = df_filtered['Doanh Thu'].sum()
total_orders = df_filtered['Mã Đơn Hàng'].nunique()
total_profit = df_filtered['Lợi Nhuận'].sum()
avg_rating = df_filtered['Đánh Giá (Sao)'].mean() if not df_filtered.empty else 0

with kpi1:
    st.metric(
        label="💰 Tổng Doanh Thu",
        value=f"{total_revenue:,.0f} VNĐ",
        delta="12.5% vs tháng trước"
    )

with kpi2:
    st.metric(
        label="📦 Tổng Đơn Hàng",
        value=f"{total_orders:,}",
        delta="5.2%"
    )

with kpi3:
    st.metric(
        label="💵 Tổng Lợi Nhuận",
        value=f"{total_profit:,.0f} VNĐ",
        delta="8.1%"
    )

with kpi4:
    st.metric(
        label="⭐ Đánh Giá Trung Bình",
        value=f"{avg_rating:.2f} / 5.0",
        delta="0.3"
    )

st.markdown("<br>", unsafe_allow_html=True)

# --- TAB CHỨC NĂNG ---
tab_overview, tab_products, tab_team, tab_data = st.tabs([
    "📊 Tổng Quan Doanh Thu", 
    "🛍️ Phân Tích Sản Phẩm", 
    "👥 Hiệu Suất Nhân Viên", 
    "📋 Dữ Liệu Chi Tiết & Export"
])

# ------------------------------------------
# TAB 1: TỔNG QUAN DOANH THU
# ------------------------------------------
with tab_overview:
    col_chart1, col_chart2 = st.columns([2, 1])
    
    with col_chart1:
        st.subheader("📈 Xu Hướng Doanh Thu Theo Thời Gian")
        df_trend = df_filtered.groupby(df_filtered['Ngày'].dt.to_period('M'))['Doanh Thu'].sum().reset_index()
        df_trend['Ngày'] = df_trend['Ngày'].astype(str)
        
        fig_trend = px.line(
            df_trend, 
            x='Ngày', 
            y='Doanh Thu',
            markers=True,
            line_shape='spline',
            color_discrete_sequence=['#1f77b4']
        )
        fig_trend.update_layout(xaxis_title="Tháng", yaxis_title="Doanh Thu (VNĐ)", hovermode="x unified")
        st.plotly_chart(fig_trend, use_container_width=True)
        
    with col_chart2:
        st.subheader("🌐 Tỷ Tọng Doanh Thu Theo Khu Vực")
        df_region = df_filtered.groupby('Khu Vực')['Doanh Thu'].sum().reset_index()
        fig_pie = px.pie(
            df_region, 
            names='Khu Vực', 
            values='Doanh Thu', 
            hole=0.4,
            color_discrete_sequence=px.colors.qualitative.Set3
        )
        st.plotly_chart(fig_pie, use_container_width=True)

# ------------------------------------------
# TAB 2: PHÂN TÍCH SẢN PHẨM
# ------------------------------------------
with tab_products:
    col_p1, col_p2 = st.columns(2)
    
    with col_p1:
        st.subheader("🏆 Doanh Thu Theo Danh Mục Sản Phẩm")
        df_cat = df_filtered.groupby('Danh Mục')['Doanh Thu'].sum().reset_index().sort_values('Doanh Thu', ascending=True)
        fig_bar = px.bar(
            df_cat, 
            x='Doanh Thu', 
            y='Danh Mục', 
            orientation='h',
            text_auto='.2s',
            color='Doanh Thu',
            color_continuous_scale='Blues'
        )
        st.plotly_chart(fig_bar, use_container_width=True)
        
    with col_p2:
        st.subheader("⚖️ Mối Tương Quan: Số Lượng vs Doanh Thu")
        fig_scatter = px.scatter(
            df_filtered, 
            x='Số Lượng', 
            y='Doanh Thu', 
            color='Danh Mục',
            size='Lợi Nhuận',
            hover_data=['Mã Đơn Hàng', 'Đơn Giá'],
            opacity=0.7
        )
        st.plotly_chart(fig_scatter, use_container_width=True)

# ------------------------------------------
# TAB 3: HIỆU SUẤT NHÂN VIÊN
# ------------------------------------------
with tab_team:
    st.subheader("🥇 Top Nhân Viên Có Doanh Số Cao Nhất")
    
    df_sales_rep = df_filtered.groupby('Nhân Viên').agg(
        Tong_Doanh_Thu=('Doanh Thu', 'sum'),
        Tong_Don_Hang=('Mã Đơn Hàng', 'nunique'),
        Tong_Loi_Nhuan=('Lợi Nhuận', 'sum')
    ).reset_index().sort_values('Tong_Doanh_Thu', ascending=False)
    
    col_t1, col_t2 = st.columns([1, 1])
    
    with col_t1:
        st.dataframe(
            df_sales_rep.style.format({
                'Tong_Doanh_Thu': '{:,.0f} VNĐ',
                'Tong_Loi_Nhuan': '{:,.0f} VNĐ',
                'Tong_Don_Hang': '{:,}'
            }),
            use_container_width=True
        )
        
    with col_t2:
        fig_rep = px.bar(
            df_sales_rep, 
            x='Nhân Viên', 
            y='Tong_Doanh_Thu',
            color='Tong_Doanh_Thu',
            color_continuous_scale='Greens',
            labels={'Tong_Doanh_Thu': 'Tổng Doanh Thu (VNĐ)'}
        )
        st.plotly_chart(fig_rep, use_container_width=True)

# ------------------------------------------
# TAB 4: DỮ LIỆU CHI TIẾT & EXPORT
# ------------------------------------------
with tab_data:
    st.subheader("📄 Bảng Dữ Liệu Đã Lọc")
    
    # Tìm kiếm dữ liệu
    search_term = st.text_input("🔍 Tìm kiếm theo Mã Đơn Hàng hoặc Nhân Viên:", "")
    if search_term:
        df_display = df_filtered[
            df_filtered['Mã Đơn Hàng'].str.contains(search_term, case=False) |
            df_filtered['Nhân Viên'].str.contains(search_term, case=False)
        ]
    else:
        df_display = df_filtered
        
    st.dataframe(df_display, use_container_width=True)
    
    # Tải xuống dữ liệu dưới dạng CSV
    csv_data = df_display.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Tải xuống dữ liệu (CSV)",
        data=csv_data,
        file_name=f"bao_cao_kinh_doanh_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv"
    )

# ==========================================
# 5. FOOTER
# ==========================================
st.markdown("---")
st.markdown(
    "<div style='text-align: center; color: gray;'>"
    "Dashboard được thiết kế bằng <b>Streamlit</b> | Đã cập nhật dữ liệu tự động."
    "</div>", 
    unsafe_allow_html=True
)
