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

import pandas as pd
import matplotlib.pyplot as plt
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

# Các giá trị mặc định ban đầu
DEFAULTS = {
    "age": 40,
    "pe_current": 14.2, "pe_min": 10.0, "pe_max": 20.0,
    "pb_current": 1.72, "pb_min": 1.35, "pb_max": 2.60,
    "rf": 2.75, "erp_min": 1.5, "erp_max": 7.0,
    "dy_current": 1.70, "dy_min": 1.20, "dy_max": 3.20,
    "w_pe": 30.0, "w_pb": 20.0, "w_erp": 35.0, "w_dy": 15.0,
    "price_current": 1250.5, "ma200": 1265.2,
    "volatility_current": 16.2, "volatility_avg": 17.5,
    "drawdown_pct": 4.2,
    "method": "step",
    "gemini_api_key": "",
}

for key, val in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val

if "log" not in st.session_state:
    st.session_state.log = []


# ---------------------------------------------------------------------------
# Vẽ biểu đồ phân bổ Donut tương tác bằng Plotly (Hiện đại & Sắc nét)
# ---------------------------------------------------------------------------
def draw_plotly_pie_chart(weights, labels, colors):
    """Tạo biểu đồ đĩa tròn Donut tương tác chuẩn UI FinTech bằng Plotly."""
    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=weights,
                hole=0.48,  # Thiết kế Donut hiện đại
                marker=dict(
                    colors=colors,
                    line=dict(color="#FFFFFF", width=2.5)
                ),
                textinfo="label+percent",
                hoverinfo="label+value+percent",
                textfont=dict(size=13, family="Arial, sans-serif"),
                insidetextorientation="horizontal",
                pull=[0.06, 0, 0],  # Nổi bật phần Cổ phiếu
            )
        ]
    )

    fig.update_layout(
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=-0.15,
            xanchor="center",
            x=0.5,
            font=dict(size=12)
        ),
        margin=dict(l=20, r=20, t=20, b=20),
        height=380,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        annotations=[
            dict(
                text="<b>DANH MỤC</b><br>TỔNG THỂ",
                x=0.5, y=0.5,
                font_size=12,
                showarrow=False,
                align="center"
            )
        ]
    )
    return fig


# ---------------------------------------------------------------------------
# Tích hợp API Lấy dữ liệu VN-Index (VNDirect, DNSE & Gemini AI)
# ---------------------------------------------------------------------------
def fetch_vndirect_market_data() -> dict:
    """Lấy trực tiếp P/E, P/B và Dividend Yield (DY) của VN-Index từ API VNDirect Finfo."""
    url = "https://finfo-api.vndirect.com.vn/v2/disclosures?q=code:VNINDEX~type:RATIO&sort=reportDate:desc&size=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
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
                pe_val = item.get("pe")
                pb_val = item.get("pb")
                dy_val = item.get("dividendYield")
                
                result = {}
                if pe_val is not None:
                    result["pe_current"] = float(pe_val)
                if pb_val is not None:
                    result["pb_current"] = float(pb_val)
                if dy_val is not None:
                    result["dy_current"] = float(dy_val) * 100.0 if float(dy_val) <= 1.0 else float(dy_val)
                return result
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError) or "timed out" in str(e.reason):
            st.warning("⚠️ Kết nối tới VNDirect bị quá thời gian (Timeout). Vui lòng thử lại sau vài giây!")
        else:
            st.error(f"Lỗi truy xuất dữ liệu VNDirect: {e}")
    except Exception as e:
        st.error(f"Lỗi truy xuất dữ liệu VNDirect: {e}")
    
    return {}


def fetch_vnindex_yfinance() -> tuple[float, float]:
    """Tải lịch sử VN-Index từ DNSE Chart API v2."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (3 * 365 * 24 * 3600 * 1000)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://mkt.dnse.com.vn",
        "Referer": "https://mkt.dnse.com.vn/",
    }

    close_prices = []
    try:
        dnse_url = f"https://services.entrade.com.vn/chart-api/v2/ohlc/stock?from={start_ms}&to={now_ms}&symbol=VNINDEX&resolution=1D"
        req = urllib.request.Request(dnse_url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as response:
            res = json.loads(response.read().decode("utf-8"))
            if "c" in res and isinstance(res["c"], list) and len(res["c"]) > 0:
                close_prices = [float(x) for x in res["c"] if x is not None]
    except Exception:
        pass

    if close_prices:
        close_series = pd.Series(close_prices).dropna().reset_index(drop=True)
        total_samples = len(close_series)
        if total_samples > 0:
            price_current = float(close_series.iloc[-1])
            ma_val = float(close_series.iloc[-200:].mean()) if total_samples >= 200 else float(close_series.mean())
            return round(price_current, 2), round(ma_val, 2)

    return float(st.session_state.price_current), float(st.session_state.ma200)


GEMINI_FIELDS = [
    "pe_current", "pb_current", "rf", "dy_current",
    "price_current", "ma200", "volatility_current", "drawdown_pct",
]


def get_available_gemini_model(encoded_key: str) -> str:
    """Tự động kiểm tra danh sách Model Gemini khả dụng."""
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={encoded_key}"
    req = urllib.request.Request(list_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            res = json.loads(response.read().decode("utf-8"))
            models = res.get("models", [])
            priority_targets = [
                "gemini-1.5-flash",
                "gemini-1.5-pro",
                "gemini-2.0-flash",
                "gemini-1.0-pro"
            ]
            for target in priority_targets:
                for m in models:
                    name = m.get("name", "")
                    supported = m.get("supportedGenerationMethods", [])
                    if "generateContent" in supported and target in name:
                        return name.replace("models/", "")
            for m in models:
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    return m.get("name", "").replace("models/", "")
    except Exception:
        pass
    return "gemini-1.5-flash"


def fetch_market_data_via_gemini(api_key: str) -> dict:
    """Tự động lấy thông số định giá VN-Index thông qua Gemini AI."""
    dnse_price, dnse_ma200 = fetch_vnindex_yfinance()

    clean_key = api_key.strip()
    encoded_key = urllib.parse.quote(clean_key)
    model_name = get_available_gemini_model(encoded_key)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={encoded_key}"

    prompt = f"""
    Hãy cung cấp ước tính thông số thực tế mới nhất của thị trường chứng khoán Việt Nam (VN-Index):
    - Dữ liệu giá hiện tại: {dnse_price}
    - Dữ liệu MA200: {dnse_ma200}

    Bắt buộc trả về ĐÚNG MỘT CHUỖI JSON thuần túy (không chứa ký tự markdown ```json) dạng:
    {{
        "pe_current": float (P/E hiện tại của VN-Index),
        "pb_current": float (P/B hiện tại của VN-Index),
        "rf": float (Lợi suất TPCP 10 năm - %),
        "dy_current": float (Dividend Yield - %),
        "price_current": {dnse_price},
        "ma200": {dnse_ma200},
        "volatility_current": float (Biến động Volatility - %),
        "drawdown_pct": float (Drawdown từ đỉnh - %)
    }}
    Chỉ trả về JSON thuần túy.
    """

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1}
    }

    data_bytes = json.dumps(payload).encode("utf-8")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                url, data=data_bytes, headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(req, timeout=25) as response:
                res_data = json.loads(response.read().decode("utf-8"))

            candidates = res_data.get("candidates", [])
            if not candidates:
                raise Exception("Gemini AI không phản hồi dữ liệu.")

            parts = candidates[0].get("content", {}).get("parts", [])
            text_response = "".join([p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p]).strip()

            if "{" in text_response and "}" in text_response:
                text_response = text_response[text_response.find("{"):text_response.rfind("}") + 1]

            return json.loads(text_response)

        except urllib.error.HTTPError as err:
            if err.code == 429:
                if attempt < max_retries - 1:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise Exception("Hạn mức Gemini API tạm thời vượt quá giới hạn. Vui lòng đợi 1 phút!")
            err_body = err.read().decode("utf-8", errors="ignore")
            raise Exception(f"Lỗi gọi Gemini API ({err.code}): {err.reason}
{err_body}")

    raise Exception("Không thể kết nối Gemini API.")


# ---------------------------------------------------------------------------
# Thanh bên trái (Sidebar) - Nhập liệu thông số
# ---------------------------------------------------------------------------
st.sidebar.title("⚙️ Thông số đầu vào")

with st.sidebar.expander("📊 VNDirect Dstock Auto-Fill", expanded=True):
    if st.button("🔄 Lấy P/E, P/B, DY từ VNDirect", use_container_width=True):
        with st.spinner("Đang kết nối VNDirect API..."):
            vnd_data = fetch_vndirect_market_data()
            if vnd_data:
                if "pe_current" in vnd_data:
                    st.session_state.pe_current = vnd_data["pe_current"]
                if "pb_current" in vnd_data:
                    st.session_state.pb_current = vnd_data["pb_current"]
                if "dy_current" in vnd_data:
                    st.session_state.dy_current = vnd_data["dy_current"]
                st.success("Đã cập nhật dữ liệu P/E, P/B, DY từ VNDirect!")
                st.rerun()

with st.sidebar.expander("🤖 Google Gemini AI Auto-Fill", expanded=False):
    st.session_state.gemini_api_key = st.text_input(
        "Gemini API Key", value=st.session_state.gemini_api_key, type="password",
        help="Key chỉ lưu trong phiên làm việc hiện tại."
    )
    if st.button("Tự động lấy dữ liệu AI"):
        if not st.session_state.gemini_api_key:
            st.warning("Vui lòng nhập API Key trước.")
        else:
            with st.spinner("Đang kết nối Gemini AI..."):
                try:
                    data = fetch_market_data_via_gemini(st.session_state.gemini_api_key)
                    updated = 0
                    for k in GEMINI_FIELDS:
                        if k in data and data[k] is not None:
                            st.session_state[k] = float(data[k])
                            updated += 1
                    st.success(f"Đã cập nhật {updated} thông số từ AI!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Lỗi AI: {exc}")

with st.sidebar.expander("👤 Thông tin nhà đầu tư", expanded=True):
    st.session_state.age = st.number_input("Tuổi của bạn", 18, 100, st.session_state.age)
    saa_equity = max(0.0, float(100 - st.session_state.age))
    saa_gold = 10.0
    saa_bond = max(0.0, 100.0 - saa_equity - saa_gold)
    st.caption(
        f"SAA cơ sở (theo tuổi): Cổ phiếu **{saa_equity:.1f}%** · "
        f"Vàng **{saa_gold:.1f}%** · Trái phiếu **{saa_bond:.1f}%**"
    )

with st.sidebar.expander("📈 Định giá P/E", expanded=True):
    st.session_state.pe_current = st.number_input("PE hiện tại", 0.01, 200.0, float(st.session_state.pe_current), 0.1)
    st.session_state.pe_min = st.number_input("PE min", 0.01, 200.0, float(st.session_state.pe_min), 0.1)
    st.session_state.pe_max = st.number_input("PE max", 0.01, 200.0, float(st.session_state.pe_max), 0.1)

with st.sidebar.expander("📊 Định giá P/B", expanded=True):
    st.session_state.pb_current = st.number_input("PB hiện tại", 0.01, 50.0, float(st.session_state.pb_current), 0.05)
    st.session_state.pb_min = st.number_input("PB min", 0.01, 50.0, float(st.session_state.pb_min), 0.05)
    st.session_state.pb_max = st.number_input("PB max", 0.01, 50.0, float(st.session_state.pb_max), 0.05)

with st.sidebar.expander("💰 Phần bù rủi ro (ERP)", expanded=True):
    st.session_state.rf = st.number_input("Lợi suất TPCP - Rf (%)", 0.0, 30.0, float(st.session_state.rf), 0.1)
    st.session_state.erp_min = st.number_input("ERP min (%)", -30.0, 30.0, float(st.session_state.erp_min), 0.1)
    st.session_state.erp_max = st.number_input("ERP max (%)", -30.0, 30.0, float(st.session_state.erp_max), 0.1)

with st.sidebar.expander("💵 Tỷ suất cổ tức (DY)", expanded=True):
    st.session_state.dy_current = st.number_input("DY hiện tại (%)", 0.0, 30.0, float(st.session_state.dy_current), 0.1)
    st.session_state.dy_min = st.number_input("DY min (%)", 0.0, 30.0, float(st.session_state.dy_min), 0.1)
    st.session_state.dy_max = st.number_input("DY max (%)", 0.0, 30.0, float(st.session_state.dy_max), 0.1)

with st.sidebar.expander("⚖️ Trọng số 4 thành phần (%)", expanded=True):
    st.session_state.w_pe = st.number_input("Trọng số P/E (w1)", 0.0, 100.0, float(st.session_state.w_pe), 1.0)
    st.session_state.w_pb = st.number_input("Trọng số P/B (w2)", 0.0, 100.0, float(st.session_state.w_pb), 1.0)
    st.session_state.w_erp = st.number_input("Trọng số ERP (w3)", 0.0, 100.0, float(st.session_state.w_erp), 1.0)
    st.session_state.w_dy = st.number_input("Trọng số DY (w4)", 0.0, 100.0, float(st.session_state.w_dy), 1.0)

with st.sidebar.expander("📉 Xu hướng & Rủi ro", expanded=False):
    if st.button("📈 Lấy giá & MA200 tự động", use_container_width=True):
        with st.spinner("Đang kết nối DNSE API..."):
            p_curr, ma_val = fetch_vnindex_yfinance()
            st.session_state.price_current = p_curr
            st.session_state.ma200 = ma_val
            st.success(f"Đã cập nhật từ DNSE! Giá: {p_curr} | MA200: {ma_val}")
            st.rerun()

    st.session_state.price_current = st.number_input("Giá hiện tại", 0.0, 1e7, float(st.session_state.price_current), 1.0)
    st.session_state.ma200 = st.number_input("MA200", 0.0, 1e7, float(st.session_state.ma200), 1.0)
    st.session_state.volatility_current = st.number_input(
        "Volatility thực tế (%)", 0.0, 200.0, float(st.session_state.volatility_current), 0.5
    )
    st.session_state.volatility_avg = st.number_input(
        "Volatility TB lịch sử (%)", 0.0, 200.0, float(st.session_state.volatility_avg), 0.5
    )

    current_drawdown = float(st.session_state.get("drawdown_pct", 0.0))
    current_drawdown = max(0.0, min(100.0, abs(current_drawdown)))
    st.session_state.drawdown_pct = st.number_input(
        "Drawdown hiện tại (%)", 0.0, 100.0, current_drawdown, 0.5
    )

with st.sidebar.expander("🧮 Phương pháp map VS → Tỷ trọng", expanded=False):
    method_label = st.radio(
        "Phương pháp", ["Bậc thang (khuyến nghị)", "Tuyến tính"],
        index=0 if st.session_state.method == "step" else 1,
    )
    st.session_state.method = "step" if method_label.startswith("Bậc") else "linear"

st.sidebar.markdown("---")

# Quản lý Import / Export CSV
with st.sidebar.expander("📂 Quản lý thông số CSV", expanded=True):
    uploaded_csv = st.file_uploader("Import thông số từ CSV", type=["csv"])
    if uploaded_csv is not None:
        csv_signature = f"{uploaded_csv.name}-{uploaded_csv.size}"
        if st.session_state.get("_last_csv_signature") != csv_signature:
            row = pd.read_csv(uploaded_csv).iloc[0].to_dict()
            for k in DEFAULTS:
                if k in row and pd.notna(row[k]) and k != "gemini_api_key":
                    st.session_state[k] = row[k] if k in ("age", "method") else float(row[k])
            st.session_state["_last_csv_signature"] = csv_signature
            st.success("Đã nạp thông số từ CSV!")
            st.rerun()

    current_params = {k: [st.session_state[k]] for k in DEFAULTS if k != "gemini_api_key"}
    export_df = pd.DataFrame(current_params)

    st.download_button(
        label="💾 Xuất thông số hiện tại (CSV)",
        data=export_df.to_csv(index=False).encode("utf-8"),
        file_name="vam_inputs_config.csv",
        mime="text/csv",
        use_container_width=True
    )

calc_clicked = st.sidebar.button("🚀 Tính toán phân bổ", use_container_width=True, type="primary")

# ---------------------------------------------------------------------------
# Màn hình chính - Khuyến nghị & Đánh giá chi tiết
# ---------------------------------------------------------------------------
st.title("📊 VAM Portfolio Allocator")
st.caption("Mô hình phân bổ tài sản động dựa trên định giá thị trường (Valuation-based Asset Allocation)")

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
        price_current=float(st.session_state.price_current), ma200=float(st.session_state.ma200),
        volatility_current=float(st.session_state.volatility_current), volatility_avg=float(st.session_state.volatility_avg),
        drawdown_pct=float(st.session_state.drawdown_pct), method=st.session_state.method,
    )
    result = compute(inputs)
    st.session_state.last_result = result

    try:
        row = make_log_row(inputs.age, result)
        if SHEETS_ON:
            append_log_row(row)
        else:
            st.session_state.log.append(row)
    except Exception as exc:
        st.caption(f"Lưu log phiên: {exc}")

result = st.session_state.get("last_result")

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("🎯 Kết quả khuyến nghị")
    if result is None:
        st.info('Nhập thông số ở thanh bên trái và bấm "🚀 Tính toán phân bổ".')
    else:
        st.metric("Điểm định giá (Valuation Score)", f"{result.valuation_score:.2f}")

        # Hiển thị nhận xét tổng quan VAM Score
        if hasattr(result, "overall_assessment") and result.overall_assessment:
            st.info(f"**Đánh giá tổng thể:** {result.overall_assessment}

💡 *{result.overall_comment}*")

        m1, m2, m3 = st.columns(3)
        m1.metric("Cổ phiếu", f"{result.equity_weight:.1f}%")
        m2.metric("Trái phiếu", f"{result.bond_weight:.1f}%")
        m3.metric("Vàng", f"{result.gold_weight:.1f}%")
        st.caption(
            f"SAA gốc theo tuổi: Cổ phiếu {saa_equity:.0f}% · "
            f"Trái phiếu {saa_bond:.0f}% · Vàng {saa_gold:.0f}%"
        )

with col2:
    st.subheader("📈 Biểu đồ tỷ trọng phân bổ")
    if result is None:
        fig = go.Figure()
        fig.add_annotation(
            text="Chưa có dữ liệu tính toán",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=14, color="gray")
        )
        fig.update_layout(
            height=350,
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)"
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        weights = [result.equity_weight, result.bond_weight, result.gold_weight]
        labels = ["Cổ phiếu", "Trái phiếu", "Vàng"]
        colors = ["#2563EB", "#F59E0B", "#10B981"]  # Bảng màu FinTech tiêu chuẩn
        fig = draw_plotly_pie_chart(weights, labels, colors)
        st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# Đánh giá chi tiết từng tham số đầu vào
# ---------------------------------------------------------------------------
if result is not None:
    st.markdown("---")
    st.subheader("🔍 Đánh giá trạng thái chi tiết từng tham số đầu vào")

    if hasattr(result, "details") and result.details:
        details_data = result.details
    else:
        details_data = [
            {"parameter": "P/E", "current_value": st.session_state.pe_current, "z_score": f"{result.pe_score:+.2f}", "status": "🟢 Rẻ" if result.pe_score >= 0.5 else ("🔴 Đắt" if result.pe_score <= -0.5 else "🟡 Trung vị")},
            {"parameter": "P/B", "current_value": st.session_state.pb_current, "z_score": f"{result.pb_score:+.2f}", "status": "🟢 Rẻ" if result.pb_score >= 0.5 else ("🔴 Đắt" if result.pb_score <= -0.5 else "🟡 Trung vị")},
            {"parameter": "ERP", "current_value": "-", "z_score": f"{result.erp_score:+.2f}", "status": "🟢 Tích cực" if result.erp_score >= 0.5 else ("🔴 Tiêu cực" if result.erp_score <= -0.5 else "🟡 Cân bằng")},
            {"parameter": "DY", "current_value": f"{st.session_state.dy_current}%", "z_score": f"{result.dy_score:+.2f}", "status": "🟢 Tích cực" if result.dy_score >= 0.5 else ("🔴 Tiêu cực" if result.dy_score <= -0.5 else "🟡 Cân bằng")},
        ]

    details_df = pd.DataFrame(details_data)

    st.dataframe(
        details_df,
        column_config={
            "parameter": "Chỉ số / Tham số",
            "current_value": "Giá trị hiện tại",
            "benchmark_range": "Dải tham chiếu (Min - Max / TB)",
            "z_score": "Điểm Z-Score / % Lệch",
            "status": "Đánh giá trạng thái",
            "comment": "Nhận xét chi tiết",
        },
        use_container_width=True,
        hide_index=True
    )

# ---------------------------------------------------------------------------
# Nhật ký lịch sử tính toán
# ---------------------------------------------------------------------------
st.markdown("---")
log_header_col1, log_header_col2 = st.columns([4, 1])
with log_header_col1:
    st.subheader("🗒️ Nhật ký lịch sử tính toán")
with log_header_col2:
    if SHEETS_ON:
        st.button("🔄 Làm mới")

if SHEETS_ON:
    try:
        log_df = load_log_df()
    except Exception as exc:
        st.error(f"Không đọc được Google Sheets: {exc}")
        log_df = pd.DataFrame(st.session_state.log)
    st.caption("✅ Đang lưu vĩnh viễn lên Google Sheets.")
else:
    log_df = pd.DataFrame(st.session_state.log)
    st.caption("⚠️ Chưa cấu hình Google Sheets — log chỉ tồn tại trong phiên hiện tại.")

if not log_df.empty:
    st.dataframe(log_df, use_container_width=True, hide_index=True)
    st.download_button(
        "⬇️ Xuất log CSV", log_df.to_csv(index=False).encode("utf-8"),
        file_name="vam_log_export.csv", mime="text/csv",
    )
else:
    st.caption("Chưa có lịch sử tính toán nào.")
