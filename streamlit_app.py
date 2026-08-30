"""
streamlit_app.py - VAM Portfolio Allocator (Web Edition)
Công cụ phân bổ danh mục đầu tư động theo mô hình VAM.
Chạy ứng dụng: streamlit run streamlit_app.py
"""

import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from vam_core import VAMInputs, compute
from sheets_log import sheets_configured, append_log_row, load_log_df, make_log_row

# ---------------------------------------------------------------------------
# Cấu hình trang Streamlit
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="VAM Portfolio Allocator",
    page_icon="📊",
    layout="wide"
)

SHEETS_ON = sheets_configured()

# Giá trị mặc định ban đầu
DEFAULTS = {
    "age": 40,
    "pe_current": 14.2, "pe_min": 10.0, "pe_max": 20.0,
    "pb_current": 1.72, "pb_min": 1.35, "pb_max": 2.60,
    "rf": 2.75, "erp_min": 1.5, "erp_max": 7.0,
    "dy_current": 1.70, "dy_min": 1.20, "dy_max": 3.20,
    "w_pe": 30.0, "w_pb": 20.0, "w_erp": 35.0, "w_dy": 15.0,
    "roe_current": 13.5, "roe_benchmark": 12.0,
    "eps_growth_exp": 10.0, "eps_growth_benchmark": 8.0,
    "price_current": 1250.5, "ma200": 1265.2,
    "volatility_current": 16.2, "volatility_avg": 17.5,
    "drawdown_pct": 4.2,
    "us10y": 4.25, "us_cpi": 2.90,
    "method": "step",
    "gemini_api_key": "",
}

# Khởi tạo Session State
for key, val in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val

if "log" not in st.session_state:
    st.session_state.log = []

if "show_constitution" not in st.session_state:
    st.session_state.show_constitution = False


def draw_plotly_pie_chart(weights, labels, colors):
    """Tạo biểu đồ đĩa tròn Donut tương tác bằng Plotly."""
    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=weights,
                hole=0.48,
                marker=dict(colors=colors, line=dict(color="#FFFFFF", width=2.5)),
                textinfo="label+percent",
                hoverinfo="label+value+percent",
                textfont=dict(size=13, family="Arial, sans-serif"),
                insidetextorientation="horizontal",
                pull=[0.05, 0, 0.05],
            )
        ]
    )

    fig.update_layout(
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5, font=dict(size=12)),
        margin=dict(l=20, r=20, t=20, b=20),
        height=380,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        annotations=[dict(text="<b>DANH MỤC</b><br>TỔNG THỂ", x=0.5, y=0.5, font_size=12, showarrow=False, align="center")]
    )
    return fig


def fetch_vndirect_market_data() -> dict:
    url = "https://finfo-api.vndirect.com.vn/v2/disclosures?q=code:VNINDEX~type:RATIO&sort=reportDate:desc&size=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://dstock.vndirect.com.vn",
        "Referer": "https://dstock.vndirect.com.vn/",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            res = json.loads(response.read().decode("utf-8"))
            data_list = res.get("data", [])
            if data_list:
                item = data_list[0]
                result = {}
                if item.get("pe") is not None: result["pe_current"] = float(item["pe"])
                if item.get("pb") is not None: result["pb_current"] = float(item["pb"])
                if item.get("dividendYield") is not None:
                    dy = float(item["dividendYield"])
                    result["dy_current"] = dy * 100.0 if dy <= 1.0 else dy
                return result
    except Exception as e:
        st.error(f"Lỗi truy xuất VNDirect: {e}")
    return {}


def fetch_vnindex_yfinance() -> tuple[float, float]:
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (3 * 365 * 24 * 3600 * 1000)
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    close_prices = []
    try:
        dnse_url = f"https://services.entrade.com.vn/chart-api/v2/ohlc/stock?from={start_ms}&to={now_ms}&symbol=VNINDEX&resolution=1D"
        req = urllib.request.Request(dnse_url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as response:
            res = json.loads(response.read().decode("utf-8"))
            if "c" in res and isinstance(res["c"], list):
                close_prices = [float(x) for x in res["c"] if x is not None]
    except Exception:
        pass

    if close_prices:
        close_series = pd.Series(close_prices).dropna().reset_index(drop=True)
        if len(close_series) > 0:
            price_current = float(close_series.iloc[-1])
            ma_val = float(close_series.iloc[-200:].mean()) if len(close_series) >= 200 else float(close_series.mean())
            return round(price_current, 2), round(ma_val, 2)

    return float(st.session_state.price_current), float(st.session_state.ma200)


GEMINI_FIELDS = [
    "pe_current", "pb_current", "rf", "dy_current",
    "roe_current", "eps_growth_exp", "us10y", "us_cpi",
    "price_current", "ma200", "volatility_current", "drawdown_pct",
]


def fetch_market_data_via_gemini(api_key: str) -> dict:
    dnse_price, dnse_ma200 = fetch_vnindex_yfinance()
    clean_key = api_key.strip()
    encoded_key = urllib.parse.quote(clean_key)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={encoded_key}"

    prompt = f"""
    Cung cấp ước tính thông số thực tế mới nhất:
    - VN-Index Price: {dnse_price}, MA200: {dnse_ma200}

    Trả về JSON thuần túy:
    {{
        "pe_current": float, "pb_current": float, "rf": float, "dy_current": float,
        "roe_current": float, "eps_growth_exp": float,
        "us10y": float (Lợi suất TPCP Mỹ 10 năm - %),
        "us_cpi": float (Lạm phát CPI Mỹ - %),
        "price_current": {dnse_price}, "ma200": {dnse_ma200},
        "volatility_current": float, "drawdown_pct": float
    }}
    """
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.1}}
    data_bytes = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data_bytes, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=25) as response:
        res_data = json.loads(response.read().decode("utf-8"))
        text_response = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if "{" in text_response and "}" in text_response:
            text_response = text_response[text_response.find("{"):text_response.rfind("}") + 1]
        return json.loads(text_response)


# ---------------------------------------------------------------------------
# Sidebar - Nhập liệu & Quản lý File JSON Config
# ---------------------------------------------------------------------------
st.sidebar.title("⚙️ Thông số đầu vào")

with st.sidebar.expander("📁 Quản lý File Config (JSON)", expanded=True):
    config_data = {key: st.session_state[key] for key in DEFAULTS.keys() if key != "gemini_api_key"}
    json_string = json.dumps(config_data, indent=2, ensure_ascii=False)
    
    st.download_button(
        label="💾 Tải về File Config (JSON)",
        file_name="vam_input_config.json",
        mime="application/json",
        data=json_string,
        use_container_width=True
    )
    
    uploaded_file = st.file_uploader("📂 Import Config từ JSON", type=["json"])
    if uploaded_file is not None:
        try:
            imported_data = json.load(uploaded_file)
            for k, v in imported_data.items():
                if k in DEFAULTS:
                    st.session_state[k] = v
            st.success("✅ Tải dữ liệu từ File JSON thành công!")
            st.rerun()
        except Exception as e:
            st.error(f"❌ File JSON không hợp lệ: {e}")

st.sidebar.markdown("---")

with st.sidebar.expander("📊 VNDirect Dstock Auto-Fill", expanded=False):
    if st.button("🔄 Lấy P/E, P/B, DY từ VNDirect", use_container_width=True):
        vnd_data = fetch_vndirect_market_data()
        if vnd_data:
            for k, v in vnd_data.items():
                st.session_state[k] = v
            st.success("Đã cập nhật từ VNDirect!")
            st.rerun()

with st.sidebar.expander("🤖 Gemini AI Auto-Fill", expanded=False):
    st.session_state.gemini_api_key = st.text_input("Gemini API Key", value=st.session_state.gemini_api_key, type="password")
    if st.button("Tự động lấy dữ liệu AI"):
        if st.session_state.gemini_api_key:
            try:
                data = fetch_market_data_via_gemini(st.session_state.gemini_api_key)
                for k in GEMINI_FIELDS:
                    if k in data and data[k] is not None:
                        st.session_state[k] = float(data[k])
                st.success("Đã cập nhật dữ liệu AI!")
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi AI: {e}")

with st.sidebar.expander("👤 Thông tin nhà đầu tư", expanded=True):
    st.session_state.age = st.number_input("Tuổi của bạn", 18, 100, int(st.session_state.age))
    saa_equity = max(0.0, float(100 - st.session_state.age))
    saa_gold = 10.0
    saa_bond = max(0.0, 100.0 - saa_equity - saa_gold)

with st.sidebar.expander("📈 Định giá & P/E, P/B, ERP, DY", expanded=False):
    st.session_state.pe_current = st.number_input("PE hiện tại", 0.01, 200.0, float(st.session_state.pe_current), 0.1)
    st.session_state.pe_min = st.number_input("PE min", 0.01, 200.0, float(st.session_state.pe_min), 0.1)
    st.session_state.pe_max = st.number_input("PE max", 0.01, 200.0, float(st.session_state.pe_max), 0.1)
    st.session_state.pb_current = st.number_input("PB hiện tại", 0.01, 50.0, float(st.session_state.pb_current), 0.05)
    st.session_state.pb_min = st.number_input("PB min", 0.01, 50.0, float(st.session_state.pb_min), 0.05)
    st.session_state.pb_max = st.number_input("PB max", 0.01, 50.0, float(st.session_state.pb_max), 0.05)
    st.session_state.rf = st.number_input("Lợi suất TPCP VN - Rf (%)", 0.0, 30.0, float(st.session_state.rf), 0.1)
    st.session_state.erp_min = st.number_input("ERP min (%)", -30.0, 30.0, float(st.session_state.erp_min), 0.1)
    st.session_state.erp_max = st.number_input("ERP max (%)", -30.0, 30.0, float(st.session_state.erp_max), 0.1)
    st.session_state.dy_current = st.number_input("DY hiện tại (%)", 0.0, 30.0, float(st.session_state.dy_current), 0.1)
    st.session_state.dy_min = st.number_input("DY min (%)", 0.0, 30.0, float(st.session_state.dy_min), 0.1)
    st.session_state.dy_max = st.number_input("DY max (%)", 0.0, 30.0, float(st.session_state.dy_max), 0.1)

with st.sidebar.expander("🌐 Vĩ mô Mỹ & Vàng Động", expanded=True):
    st.session_state.us10y = st.number_input("Lợi suất TPCP Mỹ 10 năm - US10Y (%)", 0.0, 20.0, float(st.session_state.us10y), 0.05)
    st.session_state.us_cpi = st.number_input("Lạm phát CPI Mỹ (%)", -5.0, 30.0, float(st.session_state.us_cpi), 0.1)
    real_yield = st.session_state.us10y - st.session_state.us_cpi
    st.caption(f"💡 Lợi suất thực Mỹ (Real Yield): **{real_yield:.2f}%**")

with st.sidebar.expander("🛡️ Chất lượng & Tăng trưởng", expanded=False):
    st.session_state.roe_current = st.number_input("ROE hiện tại (%)", -50.0, 100.0, float(st.session_state.roe_current), 0.5)
    st.session_state.roe_benchmark = st.number_input("ROE chuẩn (%)", 0.0, 100.0, float(st.session_state.roe_benchmark), 0.5)
    st.session_state.eps_growth_exp = st.number_input("Tăng trưởng EPS dự phóng (%)", -100.0, 200.0, float(st.session_state.eps_growth_exp), 0.5)
    st.session_state.eps_growth_benchmark = st.number_input("Tăng trưởng EPS chuẩn (%)", -50.0, 100.0, float(st.session_state.eps_growth_benchmark), 0.5)

with st.sidebar.expander("📉 Xu hướng Kỹ thuật", expanded=False):
    st.session_state.price_current = st.number_input("VN-Index Giá", 0.0, 1e7, float(st.session_state.price_current), 1.0)
    st.session_state.ma200 = st.number_input("VN-Index MA200", 0.0, 1e7, float(st.session_state.ma200), 1.0)
    st.session_state.volatility_current = st.number_input("Volatility thực tế (%)", 0.0, 200.0, float(st.session_state.volatility_current), 0.5)
    st.session_state.volatility_avg = st.number_input("Volatility TB (%)", 0.0, 200.0, float(st.session_state.volatility_avg), 0.5)
    st.session_state.drawdown_pct = st.number_input("Drawdown (%)", 0.0, 100.0, float(st.session_state.drawdown_pct), 0.5)

st.sidebar.markdown("---")
calc_clicked = st.sidebar.button("🚀 Tính toán phân bổ", use_container_width=True, type="primary")

# ---------------------------------------------------------------------------
# Màn hình chính
# ---------------------------------------------------------------------------
st.title("📊 VAM Portfolio Allocator (Dynamic Gold & Constitutional Edition)")

if calc_clicked:
    inputs = VAMInputs(
        age=int(st.session_state.age),
        saa_equity=saa_equity, saa_gold=saa_gold, saa_bond=saa_bond,
        pe_current=float(st.session_state.pe_current), pe_min=float(st.session_state.pe_min), pe_max=float(st.session_state.pe_max),
        pb_current=float(st.session_state.pb_current), pb_min=float(st.session_state.pb_min), pb_max=float(st.session_state.pb_max),
        rf=float(st.session_state.rf), erp_min=float(st.session_state.erp_min), erp_max=float(st.session_state.erp_max),
        dy_current=float(st.session_state.dy_current), dy_min=float(st.session_state.dy_min), dy_max=float(st.session_state.dy_max),
        w_pe=float(st.session_state.w_pe), w_pb=float(st.session_state.w_pb),
        w_erp=float(st.session_state.w_erp), w_dy=float(st.session_state.w_dy),
        roe_current=float(st.session_state.roe_current), roe_benchmark=float(st.session_state.roe_benchmark),
        eps_growth_exp=float(st.session_state.eps_growth_exp), eps_growth_benchmark=float(st.session_state.eps_growth_benchmark),
        price_current=float(st.session_state.price_current), ma200=float(st.session_state.ma200),
        volatility_current=float(st.session_state.volatility_current), volatility_avg=float(st.session_state.volatility_avg),
        drawdown_pct=float(st.session_state.drawdown_pct),
        us10y=float(st.session_state.us10y), us_cpi=float(st.session_state.us_cpi),
        method=st.session_state.method,
    )
    result = compute(inputs)
    st.session_state.last_result = result

    # GHI LOG TƯƠNG THÍCH VỚI FILE GỐC (sử dụng make_log_row và append_log_row)
    if SHEETS_ON:
        try:
            row = make_log_row(inputs.age, result)
            append_log_row(row)
            st.toast("✅ Đã tự động lưu kết quả lên Google Sheets!", icon="💾")
        except Exception as exc:
            st.warning(f"⚠️ Chưa ghi được log Google Sheets: {exc}")
    else:
        row_fallback = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "age": inputs.age,
            "valuation_score": result.valuation_score,
            "equity_weight": result.equity_weight,
            "bond_weight": result.bond_weight,
            "gold_weight": result.gold_weight,
        }
        st.session_state.log.append(row_fallback)
        st.toast("ℹ️ Đã lưu tạm thời vào phiên (Chưa bật Google Sheets).", icon="📝")

result = st.session_state.get("last_result")

col1, col2 = st.columns([1.2, 1])

with col1:
    st.subheader("🎯 Khuyến nghị Tỷ trọng & Trích dẫn Hiến Pháp")
    if result is None:
        st.info('Bấm "🚀 Tính toán phân bổ" ở thanh bên trái.')
    else:
        st.metric("VAM Valuation Score", f"{result.valuation_score:.2f}")

        legal = getattr(result, "legal_basis", {})
        chapter = legal.get("chapter", "Hiến Pháp Đầu Tư VAM")
        article = legal.get("article", "Quy tắc phân bổ tài sản")
        clause = legal.get("clause", "Căn cứ Định giá & Vĩ mô")

        st.success(f"**📌 {chapter}**\n\n"
                   f"**📌 {article}**\n\n"
                   f"**📌 {clause}**")

        # NÚT XEM TOÀN VĂN HIẾN PHÁP
        if st.button("📜 Xem toàn văn Hiến pháp Đầu tư VAM", use_container_width=True):
            st.session_state.show_constitution = not st.session_state.show_constitution

        if st.session_state.show_constitution:
            st.info("""
            ### 📜 HIẾN PHÁP ĐẦU TƯ VAM (TOÀN VĂN)
            
            **Chương I: Tôn chỉ & Nguyên tắc cốt lõi**
            - **Định giá là kim chỉ nam:** Phân bổ tài sản phải dựa trên mức độ rẻ/đắt của thị trường thông qua hệ thống định giá định lượng (P/E, P/B, ERP, DY).
            - **Kỷ luật chiến lược:** Tuyệt đối tuân thủ các quy tắc tự động theo điểm số định giá (Valuation Score), không để cảm xúc chi phối quyết định đầu tư.
            
            **Chương II: Quy tắc phân bổ tỷ trọng tài sản**
            - **Cổ phiếu (Equity):** Tăng tỷ trọng khi thị trường ở vùng định giá thấp (vùng rẻ, Z-score cao) và hạ tỷ trọng khi thị trường hưng phấn, đắt đỏ.
            - **Trái phiếu (Bond):** Đóng vai trò neo giữ sự ổn định, giảm thiểu biến động và đảm bảo dòng tiền cấu trúc cho danh mục.
            - **Vàng động (Dynamic Gold):** Tài sản phòng thủ động, điều chỉnh theo rủi ro vĩ mô, lạm phát thực (Real Yield) và biến động thị trường.
            
            **Chương III: Quản trị rủi ro & Vĩ mô**
            - Giám sát chặt chẽ các thông số vĩ mô toàn cầu (US10Y, CPI Mỹ) và kỹ thuật nội tại (MA200, Drawdown, Volatility) để bảo vệ vốn toàn diện.
            """)

        rec = getattr(result, "recommendation", {})
        action = rec.get("action", "")
        headline = rec.get("headline", "")
        detail = rec.get("detail", "")
        rule_text = getattr(result, "rule_text", "")

        st.info(f"**⚖️ Quy tắc:** {rule_text}\n\n"
                f"**📢 Hành động:** `{action}` - **{headline}**\n\n"
                f"**📝 Chi tiết:** {detail}")

        m1, m2, m3 = st.columns(3)
        m1.metric("Cổ phiếu", f"{result.equity_weight:.1f}%")
        m2.metric("Trái phiếu", f"{result.bond_weight:.1f}%")
        m3.metric("Vàng (Dynamic)", f"{result.gold_weight:.1f}%")

        st.markdown("---")
        if SHEETS_ON:
            st.caption("🟢 Chế độ ghi log tự động vào Google Sheets đang BẬT.")
        else:
            st.caption("ℹ️ Cấu hình Google Sheets (`gcp_service_account` và `sheets`) trong Secrets để lưu trữ vĩnh viễn trực tuyến.")

with col2:
    st.subheader("📈 Biểu đồ phân bổ")
    if result is not None:
        weights = [result.equity_weight, result.bond_weight, result.gold_weight]
        labels = ["Cổ phiếu", "Trái phiếu", "Vàng"]
        colors = ["#2563EB", "#F59E0B", "#10B981"]
        fig = draw_plotly_pie_chart(weights, labels, colors)
        st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# TAB HIỂN THỊ CHI TIẾT & LỊCH SỬ LOGS GOOGLE SHEETS
# ---------------------------------------------------------------------------
if result is not None:
    st.markdown("---")
    tab1, tab2 = st.tabs(["🔍 Đánh giá Chi tiết Chỉ số", "📜 Lịch sử lưu Google Sheets"])
    
    with tab1:
        if hasattr(result, "details"):
            st.dataframe(pd.DataFrame(result.details), use_container_width=True, hide_index=True)
            
    with tab2:
        col_hdr1, col_hdr2 = st.columns([4, 1])
        with col_hdr1:
            st.caption("✅ Nhật ký lưu trữ lịch sử các lần tính toán VAM trên Google Sheets." if SHEETS_ON else "⚠️ Chưa cấu hình Google Sheets - Hiển thị nhật ký phiên làm việc hiện tại.")
        with col_hdr2:
            if st.button("🔄 Làm mới"):
                st.rerun()

        try:
            if SHEETS_ON:
                df_logs = load_log_df()
            else:
                df_logs = pd.DataFrame(st.session_state.log)

            if not df_logs.empty:
                st.dataframe(df_logs, use_container_width=True, hide_index=True)
                st.download_button(
                    "⬇️ Xuất log CSV",
                    df_logs.to_csv(index=False).encode("utf-8"),
                    file_name="vam_log_export.csv",
                    mime="text/csv"
                )
            else:
                st.info("Chưa có bản ghi lịch sử nào.")
        except Exception as exc:
            st.error(f"Lỗi tải dữ liệu lịch sử: {exc}")