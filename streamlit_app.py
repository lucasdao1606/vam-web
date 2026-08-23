"""
streamlit_app.py - VAM Portfolio Allocator (Web Edition)
Phiên bản web của công cụ phân bổ danh mục đầu tư VAM (Valuation-based Asset Allocation).
Tái sử dụng nguyên vẹn logic tính toán từ vam_core.py (không thay đổi công thức).
Chạy: streamlit run streamlit_app.py
"""

import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

import pandas as pd
import matplotlib.pyplot as plt
import streamlit as st

from vam_core import VAMInputs, compute
from sheets_log import sheets_configured, append_log_row, load_log_df, make_log_row

# ---------------------------------------------------------------------------
# Cấu hình trang
# ---------------------------------------------------------------------------
st.set_page_config(page_title="VAM Portfolio Allocator", page_icon="📊", layout="wide")

SHEETS_ON = sheets_configured()

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
    st.session_state.log = []  # fallback log tạm khi chưa cấu hình Google Sheets

# ---------------------------------------------------------------------------
# Tích hợp Google Gemini AI - tự động lấy dữ liệu thị trường VN-Index
# (Giữ nguyên logic từ vam_app.py gốc, chuyển sang đồng bộ cho Streamlit)
# ---------------------------------------------------------------------------
GEMINI_FIELDS = [
    "pe_current", "pb_current", "rf", "dy_current",
    "price_current", "ma200", "volatility_current", "drawdown_pct",
]


def get_available_gemini_model(encoded_key: str) -> str:
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={encoded_key}"
    req = urllib.request.Request(list_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            res = json.loads(response.read().decode("utf-8"))
            models = res.get("models", [])
            priority_targets = [
                "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                "gemini-3.0-flash", "gemini-2.5-flash-001", "gemini-2.0-flash",
                "gemini-1.5-flash",
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
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8", errors="ignore")
        raise Exception(f"Lỗi kiểm tra API Key ({err.code}): {err.reason}\n{err_body}")
    except Exception as exc:
        raise Exception(f"Không thể lấy danh sách Model Gemini: {exc}")
    return "gemini-2.0-flash"


def fetch_market_data_via_gemini(api_key: str) -> dict:
    clean_key = api_key.strip()
    encoded_key = urllib.parse.quote(clean_key)
    model_name = get_available_gemini_model(encoded_key)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={encoded_key}"

    prompt = """
    Hãy tra cứu và cung cấp thông số thực tế mới nhất của thị trường chứng khoán Việt Nam (VN-Index) hiện tại và trả về ĐÚNG MỘT CHUỖI JSON thuần túy (không chứa ký tự markdown ```json) có cấu trúc đúng như sau:
    {
        "pe_current": float (P/E hiện tại của VN-Index),
        "pb_current": float (P/B hiện tại của VN-Index),
        "rf": float (Lợi suất Trái phiếu chính phủ Việt Nam 10 năm - %),
        "dy_current": float (Tỷ suất cổ tức Dividend Yield hiện tại của VN-Index - %),
        "price_current": float (Điểm số hiện tại của chỉ số VN-Index),
        "ma200": float (Đường trung bình động MA200 ngày của VN-Index),
        "volatility_current": float (Biến động Volatility niên hóa hiện tại - %),
        "drawdown_pct": float (Mức sụt giảm Drawdown từ đỉnh gần nhất - %)
    }
    Chỉ trả về chuỗi JSON, tuyệt đối không viết thêm bất kỳ từ ngữ hay ký tự nào khác.
    """
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data_bytes, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            res_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8", errors="ignore")
        raise Exception(f"Lỗi gọi Gemini API ({err.code}): {err.reason}\n{err_body}")

    candidates = res_data.get("candidates", [])
    if not candidates:
        raise Exception("Gemini AI không phản hồi dữ liệu.")
    parts = candidates[0].get("content", {}).get("parts", [])
    text_response = "".join(p.get("text", "") for p in parts if "text" in p).strip()

    if text_response.startswith("```"):
        lines = text_response.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text_response = "\n".join(lines).strip()

    return json.loads(text_response)


# ---------------------------------------------------------------------------
# Sidebar - Các thông số đầu vào
# ---------------------------------------------------------------------------
st.sidebar.title("⚙️ Thông số đầu vào")

with st.sidebar.expander("🤖 Google Gemini AI Auto-Fill", expanded=False):
    st.session_state.gemini_api_key = st.text_input(
        "Gemini API Key", value=st.session_state.gemini_api_key, type="password",
        help="Key chỉ lưu trong phiên làm việc hiện tại, không được ghi ra máy chủ.",
    )
    if st.button("Tự động lấy dữ liệu AI"):
        if not st.session_state.gemini_api_key:
            st.warning("Vui lòng nhập API Key trước.")
        else:
            with st.spinner("Đang kết nối Gemini AI lấy dữ liệu VN-Index..."):
                try:
                    data = fetch_market_data_via_gemini(st.session_state.gemini_api_key)
                    updated = 0
                    for k in GEMINI_FIELDS:
                        if k in data and data[k] is not None:
                            st.session_state[k] = float(data[k])
                            updated += 1
                    st.success(f"Đã cập nhật {updated} thông số từ Gemini AI!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Lỗi kết nối Gemini API:\n{exc}")

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
    st.session_state.pe_current = st.number_input("PE hiện tại", 0.01, 200.0, st.session_state.pe_current, 0.1)
    st.session_state.pe_min = st.number_input("PE min", 0.01, 200.0, st.session_state.pe_min, 0.1)
    st.session_state.pe_max = st.number_input("PE max", 0.01, 200.0, st.session_state.pe_max, 0.1)

with st.sidebar.expander("📊 Định giá P/B", expanded=True):
    st.session_state.pb_current = st.number_input("PB hiện tại", 0.01, 50.0, st.session_state.pb_current, 0.05)
    st.session_state.pb_min = st.number_input("PB min", 0.01, 50.0, st.session_state.pb_min, 0.05)
    st.session_state.pb_max = st.number_input("PB max", 0.01, 50.0, st.session_state.pb_max, 0.05)

with st.sidebar.expander("💰 Phần bù rủi ro (ERP)", expanded=True):
    st.session_state.rf = st.number_input("Lợi suất TPCP - Rf (%)", 0.0, 30.0, st.session_state.rf, 0.1)
    st.session_state.erp_min = st.number_input("ERP min (%)", -30.0, 30.0, st.session_state.erp_min, 0.1)
    st.session_state.erp_max = st.number_input("ERP max (%)", -30.0, 30.0, st.session_state.erp_max, 0.1)

with st.sidebar.expander("💵 Tỷ suất cổ tức (DY)", expanded=True):
    st.session_state.dy_current = st.number_input("DY hiện tại (%)", 0.0, 30.0, st.session_state.dy_current, 0.1)
    st.session_state.dy_min = st.number_input("DY min (%)", 0.0, 30.0, st.session_state.dy_min, 0.1)
    st.session_state.dy_max = st.number_input("DY max (%)", 0.0, 30.0, st.session_state.dy_max, 0.1)

with st.sidebar.expander("⚖️ Trọng số 4 thành phần (%)", expanded=True):
    st.session_state.w_pe = st.number_input("Trọng số P/E (w1)", 0.0, 100.0, st.session_state.w_pe, 1.0)
    st.session_state.w_pb = st.number_input("Trọng số P/B (w2)", 0.0, 100.0, st.session_state.w_pb, 1.0)
    st.session_state.w_erp = st.number_input("Trọng số ERP (w3)", 0.0, 100.0, st.session_state.w_erp, 1.0)
    st.session_state.w_dy = st.number_input("Trọng số DY (w4)", 0.0, 100.0, st.session_state.w_dy, 1.0)

with st.sidebar.expander("📉 Xu hướng & Rủi ro", expanded=False):
    st.session_state.price_current = st.number_input("Giá hiện tại", 0.0, 1e7, st.session_state.price_current, 1.0)
    st.session_state.ma200 = st.number_input("MA200", 0.0, 1e7, st.session_state.ma200, 1.0)
    st.session_state.volatility_current = st.number_input(
        "Volatility thực tế (%)", 0.0, 200.0, st.session_state.volatility_current, 0.5
    )
    st.session_state.volatility_avg = st.number_input(
        "Volatility TB lịch sử (%)", 0.0, 200.0, st.session_state.volatility_avg, 0.5
    )
    st.session_state.drawdown_pct = st.number_input(
        # Đảm bảo giá trị luôn nằm trong khoảng 0.0 - 100.0 (lấy giá trị tuyệt đối nếu âm)
        current_drawdown = float(st.session_state.get("drawdown_pct", 0.0))
        current_drawdown = max(0.0, min(100.0, abs(current_drawdown)))
)
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
uploaded_csv = st.sidebar.file_uploader("📂 Import thông số từ CSV", type=["csv"])
if uploaded_csv is not None:
    # Dùng file_id để chỉ xử lý MỖI file một lần — tránh lặp rerun vô hạn
    # (vì file_uploader vẫn giữ file đã chọn qua các lần rerun sau đó)
    csv_signature = f"{uploaded_csv.name}-{uploaded_csv.size}"
    if st.session_state.get("_last_csv_signature") != csv_signature:
        row = pd.read_csv(uploaded_csv).iloc[0].to_dict()
        for k in DEFAULTS:
            if k in row and pd.notna(row[k]) and k != "gemini_api_key":
                st.session_state[k] = row[k] if k in ("age", "method") else float(row[k])
        st.session_state["_last_csv_signature"] = csv_signature
        st.sidebar.success("Đã nạp thông số từ CSV.")
        st.rerun()
    else:
        st.sidebar.caption("✅ Đã nạp file này rồi. Chọn file khác hoặc bấm Tính toán phân bổ bên dưới.")

calc_clicked = st.sidebar.button("🚀 Tính toán phân bổ", use_container_width=True, type="primary")

# ---------------------------------------------------------------------------
# Khu vực chính - Kết quả
# ---------------------------------------------------------------------------
st.title("📊 VAM Portfolio Allocator")
st.caption("Mô hình phân bổ tài sản động dựa trên định giá thị trường (Valuation-based Asset Allocation)")

if calc_clicked:
    inputs = VAMInputs(
        age=st.session_state.age, saa_equity=saa_equity, saa_gold=saa_gold, saa_bond=saa_bond,
        pe_current=st.session_state.pe_current, pe_min=st.session_state.pe_min, pe_max=st.session_state.pe_max,
        pb_current=st.session_state.pb_current, pb_min=st.session_state.pb_min, pb_max=st.session_state.pb_max,
        rf=st.session_state.rf, erp_min=st.session_state.erp_min, erp_max=st.session_state.erp_max,
        dy_current=st.session_state.dy_current, dy_min=st.session_state.dy_min, dy_max=st.session_state.dy_max,
        w_pe=st.session_state.w_pe, w_pb=st.session_state.w_pb,
        w_erp=st.session_state.w_erp, w_dy=st.session_state.w_dy,
        price_current=st.session_state.price_current, ma200=st.session_state.ma200,
        volatility_current=st.session_state.volatility_current, volatility_avg=st.session_state.volatility_avg,
        drawdown_pct=st.session_state.drawdown_pct, method=st.session_state.method,
    )
    result = compute(inputs)
    st.session_state.last_result = result
    row = make_log_row(inputs.age, result)

    if SHEETS_ON:
        try:
            append_log_row(row)
        except Exception as exc:
            st.warning(f"Không ghi được lên Google Sheets (đã lưu tạm trong phiên): {exc}")
            st.session_state.log.append(row)
    else:
        st.session_state.log.append(row)

result = st.session_state.get("last_result")

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("Kết quả khuyến nghị")
    if result is None:
        st.info('Nhập thông số ở thanh bên trái và bấm "🚀 Tính toán phân bổ".')
    else:
        st.metric("Điểm định giá (Valuation Score)", f"{result.valuation_score:.2f}")
        m1, m2, m3 = st.columns(3)
        m1.metric("Cổ phiếu", f"{result.equity_weight:.1f}%")
        m2.metric("Trái phiếu", f"{result.bond_weight:.1f}%")
        m3.metric("Vàng", f"{result.gold_weight:.1f}%")
        st.caption(
            f"SAA gốc theo tuổi: Cổ phiếu {saa_equity:.0f}% · "
            f"Trái phiếu {saa_bond:.0f}% · Vàng {saa_gold:.0f}%"
        )

with col2:
    st.subheader("Biểu đồ phân bổ danh mục")
    fig, ax = plt.subplots(figsize=(4.5, 4.5))
    if result is None:
        ax.text(0.5, 0.5, "Chưa có dữ liệu", ha="center", va="center")
        ax.axis("off")
    else:
        weights = [result.equity_weight, result.bond_weight, result.gold_weight]
        labels = ["Cổ phiếu", "Trái phiếu", "Vàng"]
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c"]
        ax.pie(weights, labels=labels, autopct="%1.1f%%", startangle=140, colors=colors, explode=(0.05, 0, 0))
        ax.axis("equal")
    st.pyplot(fig)

st.markdown("---")
log_header_col1, log_header_col2 = st.columns([4, 1])
with log_header_col1:
    st.subheader("🗒️ Nhật ký lịch sử tính toán")
with log_header_col2:
    if SHEETS_ON:
        st.button("🔄 Làm mới")  # chỉ để trigger rerun, đọc lại sheet bên dưới

if SHEETS_ON:
    try:
        log_df = load_log_df()
    except Exception as exc:
        st.error(f"Không đọc được Google Sheets: {exc}")
        log_df = pd.DataFrame(st.session_state.log)
    st.caption("✅ Đang lưu vĩnh viễn lên Google Sheets — mọi thiết bị đều xem chung một lịch sử.")
else:
    log_df = pd.DataFrame(st.session_state.log)
    st.caption(
        "⚠️ Chưa cấu hình Google Sheets — log chỉ tồn tại trong phiên hiện tại (mất khi tải lại trang). "
        "Xem README.md phần \"Thiết lập Google Sheets\" để bật lưu vĩnh viễn."
    )

if not log_df.empty:
    st.dataframe(log_df, use_container_width=True, hide_index=True)
    st.download_button(
        "⬇️ Xuất log CSV", log_df.to_csv(index=False).encode("utf-8"),
        file_name="vam_log_export.csv", mime="text/csv",
    )
else:
    st.caption("Chưa có lịch sử tính toán nào.")
