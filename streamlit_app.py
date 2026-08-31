"""
streamlit_app.py - VAM Portfolio Allocator (Web Edition)
Công cụ phân bổ danh mục đầu tư động tích hợp vnstock & Gemini AI.
Chạy ứng dụng: streamlit run streamlit_app.py
"""

import json
import re
import time
import os
from datetime import datetime, date, timedelta

import pandas as pd
import numpy as np
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

st.markdown(
    """
    <style>
    [data-testid="stMetricValue"] {
        font-size: clamp(1.2rem, 2vw, 2rem) !important;
        white-space: normal !important; 
    }
    </style>
    """,
    unsafe_allow_html=True,
)

SHEETS_ON = sheets_configured()

DEFAULTS = {
    "age": 40,
    "pe_current": 14.2, "pe_min": 10.0, "pe_max": 20.0,
    "pb_current": 1.72, "pb_min": 1.35, "pb_max": 2.60,
    "rf": 2.75, "erp_min": 1.5, "erp_max": 7.0,
    "dy_current": 1.70, "dy_min": 1.20, "dy_max": 3.20,
    "w_pe": 30.0, "w_pb": 20.0, "w_erp": 35.0, "w_dy": 15.0,
    "roe_current": 13.5, "roe_benchmark": 12.0,
    "eps_growth_exp": 10.0, "eps_growth_benchmark": 8.0,
    "price_current": 1250.5, "ma20": 1255.0, "ma200": 1265.2,
    "volatility_current": 16.2, "volatility_avg": 17.5,
    "drawdown_pct": 4.2,
    "us10y": 4.25, "us_cpi": 2.90,
    "method": "step",
    "gemini_api_key": "",
    "investment_notes": "",
}

for key, val in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val

if "log" not in st.session_state:
    st.session_state.log = []

if "show_constitution" not in st.session_state:
    st.session_state.show_constitution = False


# ---------------------------------------------------------------------------
# Các hàm phụ trợ cục bộ
# ---------------------------------------------------------------------------
def handle_json_import():
    uploaded = st.session_state.get("json_file_uploader")
    if uploaded is not None:
        try:
            imported_data = json.load(uploaded)
            for k, v in imported_data.items():
                if k in DEFAULTS:
                    if k == "age":
                        st.session_state[k] = int(v)
                    elif isinstance(v, (int, float)):
                        st.session_state[k] = float(v)
                    else:
                        st.session_state[k] = v
            st.toast("✅ Đã cập nhật tham số từ file JSON!", icon="📥")
        except Exception as e:
            st.error(f"❌ Lỗi đọc file JSON: {e}")

def handle_gemini_json_import():
    uploaded = st.session_state.get("gemini_json_uploader")
    if uploaded is not None:
        try:
            data = json.load(uploaded)
            api_key = data.get("api_key") or data.get("GEMINI_API_KEY") or data.get("key") or next(iter(data.values()), "")
            if api_key and isinstance(api_key, str):
                st.session_state["gemini_api_key"] = api_key.strip()
                st.toast("✅ Đã tải Gemini API Key từ file JSON thành công!", icon="🔑")
            else:
                st.error("❌ Không tìm thấy định dạng API Key hợp lệ trong file JSON!")
        except Exception as e:
            st.error(f"❌ Lỗi đọc file JSON API Key: {e}")

def is_local_env():
    """Kiểm tra xem ứng dụng có đang chạy trên máy tính cá nhân không."""
    return os.name == 'nt' or os.path.exists('/home/user') # Windows hoặc môi trường có desktop

def save_local_file(content_or_df, default_ext, file_types, initial_file):
    """Hàm gọi GUI lưu file của OS (chỉ cố gắng chạy nếu là Local PC)."""
    if not is_local_env():
        return False, "Server Cloud không hỗ trợ chọn thư mục trực tiếp."
    try:
        import tkinter as tk
        from tkinter import filedialog
        
        root = tk.Tk()
        root.attributes("-topmost", True)
        root.withdraw()
        
        file_path = filedialog.asksaveasfilename(
            defaultextension=default_ext,
            filetypes=file_types,
            initialfile=initial_file
        )
        root.destroy()
        
        if file_path:
            if isinstance(content_or_df, pd.DataFrame):
                content_or_df.to_csv(file_path, index=False, encoding="utf-8-sig")
            else:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content_or_df)
            return True, file_path
        return False, "Đã hủy lưu."
    except Exception as e:
        return False, f"Lỗi giao diện hệ thống: {e}"


def fetch_vnstock_market_data(progress_bar, status_box) -> dict:
    try:
        from vnstock import Reference, Fundamental, Quote
    except ImportError:
        st.error("⚠️ Thư viện `vnstock` chưa được cài đặt! Hãy chạy: `pip install vnstock`")
        return {}

    results = {}
    
    status_box.update(label="📈 Đang tải & chuẩn hóa chuỗi giá VN-Index...", state="running")
    progress_bar.progress(5)
    try:
        end_d = date.today()
        start_d = end_d - timedelta(days=365)
        
        quote_idx = Quote(symbol="VNINDEX", source="VCI")
        df_idx = quote_idx.history(start=start_d.isoformat(), end=end_d.isoformat(), interval="1D")
        
        if df_idx is not None and not df_idx.empty:
            cot_close = "close" if "close" in df_idx.columns else df_idx.columns[-2]
            close_prices = df_idx[cot_close].dropna()
            
            if close_prices.iloc[-1] < 200:
                close_prices = close_prices * 1000
                
            p_curr = float(close_prices.iloc[-1])
            ma20_val = float(close_prices.iloc[-20:].mean()) if len(close_prices) >= 20 else float(close_prices.mean())
            ma200_val = float(close_prices.iloc[-200:].mean()) if len(close_prices) >= 200 else float(close_prices.mean())
            
            returns = close_prices.pct_change().dropna()
            vol_curr = float(returns.std() * np.sqrt(252) * 100)
            
            peak = close_prices.cummax()
            dd = (close_prices - peak) / peak
            drawdown_val = float(abs(dd.min()) * 100)

            results["price_current"] = round(p_curr, 1)
            results["ma20"] = round(ma20_val, 1)
            results["ma200"] = round(ma200_val, 1)
            results["volatility_current"] = round(vol_curr, 1)
            results["drawdown_pct"] = round(drawdown_val, 1)
    except Exception as e:
        st.warning(f"Không thể tính chỉ số kỹ thuật VNINDEX từ vnstock: {e}")

    status_box.update(label="📋 Đang lấy danh sách mã rổ VN30...", state="running")
    progress_bar.progress(10)
    try:
        ref = Reference()
        ket_qua_ref = ref.index.members(symbol="VN30")
        if isinstance(ket_qua_ref, pd.DataFrame):
            cot_symbol = next((c for c in ["symbol", "ticker", "stock_code", "code"] if c in ket_qua_ref.columns), ket_qua_ref.columns[0])
            danh_sach_ma = ket_qua_ref[cot_symbol].tolist()
        else:
            danh_sach_ma = list(ket_qua_ref)
    except Exception as e:
        st.error(f"Lỗi lấy danh sách VN30: {e}")
        return results

    tong_ma = len(danh_sach_ma)
    fa = Fundamental()
    list_pe, list_pb, list_roe = [], [], []

    ITEM_EPS = "trailing_eps"
    ITEM_ROE = "roe_trailling"
    ITEM_BVPS = "book_value_per_share_bvps"

    status_box.update(label=f"🔄 Đang xử lý dữ liệu cho {tong_ma} mã VN30 (Chờ 10s/mã)...", state="running")

    for idx, ma in enumerate(danh_sach_ma, start=1):
        percent_done = int(10 + (idx / tong_ma) * 85)
        progress_bar.progress(percent_done)
        status_box.write(f"⏳ [{idx}/{tong_ma}] Đang phân tích mã **{ma}**...")

        try:
            df_ratio = fa.equity(ma).ratio(period="quarter")
            if df_ratio is not None and not df_ratio.empty:
                cols_ky = [c for c in df_ratio.columns if re.match(r"\d{4}-Q\d", str(c))]
                if cols_ky:
                    cols_ky.sort(key=lambda x: (int(x.split("-")[0]), int(x.split("-")[1].replace("Q", ""))), reverse=True)
                    cot_gan = cols_ky[0]

                    r_eps = df_ratio[df_ratio.get("item_id", pd.Series()) == ITEM_EPS]
                    r_roe = df_ratio[df_ratio.get("item_id", pd.Series()) == ITEM_ROE]
                    r_bvps = df_ratio[df_ratio.get("item_id", pd.Series()) == ITEM_BVPS]

                    eps_val = float(r_eps[cot_gan].values[0]) if not r_eps.empty else None
                    roe_val = float(r_roe[cot_gan].values[0]) if not r_roe.empty else None
                    bvps_val = float(r_bvps[cot_gan].values[0]) if not r_bvps.empty else None

                    q = Quote(symbol=ma, source="VCI")
                    df_q = q.history(start=(date.today()-timedelta(weeks=4)).isoformat(), end=date.today().isoformat(), interval="1W")
                    if df_q is not None and not df_q.empty:
                        cot_g = "close" if "close" in df_q.columns else df_q.columns[-2]
                        gia_raw = float(df_q.iloc[-1][cot_g])
                        gia_dong = gia_raw * 1000 if gia_raw < 2000 else gia_raw

                        if eps_val and eps_val > 0:
                            pe_calc = gia_dong / eps_val
                            if 1.0 <= pe_calc <= 70.0: list_pe.append(pe_calc)

                        if bvps_val and bvps_val > 0:
                            pb_calc = gia_dong / bvps_val
                            if 0.2 <= pb_calc <= 15.0: list_pb.append(pb_calc)

                        if roe_val is not None:
                            roe_norm = roe_val * 100.0 if abs(roe_val) <= 1.0 else roe_val
                            if -50.0 <= roe_norm <= 100.0: list_roe.append(roe_norm)
        except Exception:
            pass

        time.sleep(10.0)

    if list_pe: results["pe_current"] = round(float(np.median(list_pe)), 2)
    if list_pb: results["pb_current"] = round(float(np.median(list_pb)), 2)
    if list_roe: results["roe_current"] = round(float(np.median(list_roe)), 2)

    progress_bar.progress(100)
    status_box.update(label="✅ Hoàn tất lấy dữ liệu từ vnstock!", state="complete", expanded=False)
    return results

MISSING_PARAMS_KEYS = ["rf", "us10y", "us_cpi", "eps_growth_exp", "volatility_avg"]

def fetch_missing_params_via_gemini(api_key: str) -> dict:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        st.error("⚠️ Chưa cài đặt thư viện `google-genai`. Hãy chạy: `pip install google-genai`")
        return {}

    client = genai.Client(api_key=api_key.strip())

    prompt = """
    Bạn là chuyên gia dữ liệu tài chính. Hãy SỬ DỤNG GOOGLE SEARCH để tìm kiếm và cập nhật các thông số vĩ mô mới nhất tính đến hôm nay.
    Chỉ trả về DUY NHẤT một chuỗi JSON thuần túy chứa các tham số dưới đây (tính bằng phần trăm %, định dạng số thập phân, KHÔNG để trống):

    {
        "rf": float (Lợi suất Trái phiếu Chính phủ Việt Nam kỳ hạn 10 năm - VN10Y hiện tại),
        "us10y": float (Lợi suất Trái phiếu Chính phủ Mỹ kỳ hạn 10 năm - US10Y hiện tại),
        "us_cpi": float (Mức lạm phát CPI Mỹ YoY mới nhất được công bố),
        "eps_growth_exp": float (Dự phóng mức tăng trưởng EPS bình quân rổ VN30/VN-Index trong 12 tháng tới),
        "volatility_avg": float (Mức độ biến động Volatility trung bình dài hạn của VN-Index)
    }
    """

    try:
        response = client.models.generate_content(
            model="gemini-1.5-pro", 
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
                tools=[{"google_search": {}}]
            ),
        )

        text_response = response.text.strip()
        if "{" in text_response and "}" in text_response:
            text_response = text_response[text_response.find("{"):text_response.rfind("}") + 1]

        return json.loads(text_response)
    except Exception as e:
        st.error(f"Lỗi truy xuất Gemini: {e}")
        return {}

def draw_plotly_pie_chart(weights, labels, colors):
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


# ---------------------------------------------------------------------------
# Sidebar - Nhập liệu & Auto-Fill
# ---------------------------------------------------------------------------
st.sidebar.title("⚙️ Thông số đầu vào")

with st.sidebar.expander("📁 Quản lý File Config (JSON)", expanded=False):
    config_data = {key: st.session_state[key] for key in DEFAULTS.keys() if key != "gemini_api_key"}
    json_string = json.dumps(config_data, indent=2, ensure_ascii=False)
    
    if is_local_env():
        if st.button("💾 Xuất File Config (Tùy chọn thư mục)", use_container_width=True):
            success, msg = save_local_file(
                json_string, ".json", [("JSON files", "*.json")], "vam_input_config.json"
            )
            if success:
                st.toast(f"✅ Đã lưu tại: {msg}", icon="💾")
            elif "Đã hủy" not in msg:
                st.error(msg)
    else:
        st.download_button(
            label="💾 Tải về File Config (JSON)",
            file_name="vam_input_config.json",
            mime="application/json",
            data=json_string,
            use_container_width=True
        )
    
    st.file_uploader(
        "📂 Import Config từ JSON",
        type=["json"],
        key="json_file_uploader",
        on_change=handle_json_import
    )

st.sidebar.markdown("---")

with st.sidebar.expander("🚀 vnstock (Chứng khoán & VN30)", expanded=True):
    if st.button("🚀 Lấy dữ liệu từ vnstock", use_container_width=True):
        progress_bar = st.sidebar.progress(0)
        status_box = st.sidebar.status("Đang kết nối vnstock...", expanded=True)
        
        vn_data = fetch_vnstock_market_data(progress_bar, status_box)
        if vn_data:
            for k, v in vn_data.items():
                st.session_state[k] = v
            st.sidebar.success("✅ Đã cập nhật chỉ số VN30 & Kỹ thuật từ vnstock!")
            st.rerun()

with st.sidebar.expander("🤖 Gemini AI (Tham số Vĩ mô thiếu)", expanded=True):
    st.caption("🔍 Chức năng này lấy các tham số vĩ mô: `Rf`, `US10Y`, `US CPI`, `EPS Growth Exp`, `Volatility Avg`.")
    
    st.file_uploader(
        "📂 Import file JSON chứa Gemini API Key",
        type=["json"],
        key="gemini_json_uploader",
        on_change=handle_gemini_json_import
    )
    
    if st.session_state.get("gemini_api_key"):
        st.success("🔑 Đã nạp Gemini API Key thành công trong phiên làm việc.")
    else:
        st.info("ℹ️ Vui lòng import file JSON chứa API Key để kích hoạt.")
    
    if st.button("🌐 Gemini Auto-Fill Tham số Vĩ mô", use_container_width=True):
        if not st.session_state.get("gemini_api_key"):
            st.warning("⚠️ Vui lòng import file JSON chứa Gemini API Key trước!")
        else:
            try:
                with st.spinner("🤖 Gemini đang truy xuất thông số vĩ mô..."):
                    missing_data = fetch_missing_params_via_gemini(st.session_state.gemini_api_key)
                    
                    updated_count = 0
                    for key in MISSING_PARAMS_KEYS:
                        if key in missing_data and missing_data[key] is not None:
                            st.session_state[key] = float(missing_data[key])
                            updated_count += 1
                            
                st.sidebar.success(f"✅ Đã cập nhật thành công {updated_count} tham số vĩ mô từ Gemini AI!")
                st.rerun()
            except Exception as e:
                st.sidebar.error(f"❌ Lỗi Gemini AI: {e}")

st.sidebar.markdown("---")

with st.sidebar.expander("👤 Thông tin nhà đầu tư", expanded=True):
    st.number_input("Tuổi của bạn", 18, 100, key="age")
    saa_equity = max(0.0, float(100 - st.session_state.age))
    saa_gold = 10.0
    saa_bond = max(0.0, 100.0 - saa_equity - saa_gold)

with st.sidebar.expander("📈 Định giá & P/E, P/B, ERP, DY", expanded=False):
    st.number_input("PE hiện tại", 0.01, 200.0, step=0.1, key="pe_current")
    st.number_input("PE min", 0.01, 200.0, step=0.1, key="pe_min")
    st.number_input("PE max", 0.01, 200.0, step=0.1, key="pe_max")
    st.number_input("PB hiện tại", 0.01, 50.0, step=0.05, key="pb_current")
    st.number_input("PB min", 0.01, 50.0, step=0.05, key="pb_min")
    st.number_input("PB max", 0.01, 50.0, step=0.05, key="pb_max")
    
    st.number_input("Lãi suất phi rủi ro Rf (%) để tính ERP [Gemini]", 0.0, 30.0, step=0.1, key="rf")
    st.number_input("ERP min (%)", -30.0, 30.0, step=0.1, key="erp_min")
    st.number_input("ERP max (%)", -30.0, 30.0, step=0.1, key="erp_max")
    st.number_input("DY hiện tại (%)", 0.0, 30.0, step=0.1, key="dy_current")
    st.number_input("DY min (%)", 0.0, 30.0, step=0.1, key="dy_min")
    st.number_input("DY max (%)", 0.0, 30.0, step=0.1, key="dy_max")

with st.sidebar.expander("🌐 Vĩ mô Mỹ & Vàng Động [Gemini Auto]", expanded=True):
    st.number_input("Lợi suất TPCP Mỹ 10 năm - US10Y (%)", 0.0, 20.0, step=0.05, key="us10y")
    st.number_input("Lạm phát CPI Mỹ (%)", -5.0, 30.0, step=0.1, key="us_cpi")
    real_yield = st.session_state.us10y - st.session_state.us_cpi
    st.caption(f"💡 Lợi suất thực Mỹ (Real Yield): **{real_yield:.2f}%**")

with st.sidebar.expander("🛡️ Chất lượng & Tăng trưởng", expanded=False):
    st.number_input("ROE hiện tại (%)", -50.0, 100.0, step=0.5, key="roe_current")
    st.number_input("ROE chuẩn (%)", 0.0, 100.0, step=0.5, key="roe_benchmark")
    st.number_input("Tăng trưởng EPS dự phóng (%) [Gemini]", -100.0, 200.0, step=0.5, key="eps_growth_exp")
    st.number_input("Tăng trưởng EPS chuẩn (%)", -50.0, 100.0, step=0.5, key="eps_growth_benchmark")

with st.sidebar.expander("📉 Xu hướng Kỹ thuật (Đa tầng MA20 & MA200)", expanded=False):
    st.number_input("VN-Index Giá", 0.0, 1e7, step=1.0, key="price_current")
    st.number_input("VN-Index MA20 (Dòng tiền ngắn/trung hạn)", 0.0, 1e7, step=1.0, key="ma20")
    st.number_input("VN-Index MA200 (Xu hướng lớn)", 0.0, 1e7, step=1.0, key="ma200")
    st.number_input("Volatility thực tế (%)", 0.0, 200.0, step=0.5, key="volatility_current")
    st.number_input("Volatility TB (%) [Gemini]", 0.0, 200.0, step=0.5, key="volatility_avg")
    st.number_input("Drawdown (%)", 0.0, 100.0, step=0.5, key="drawdown_pct")

st.sidebar.markdown("---")
st.session_state.investment_notes = st.sidebar.text_area("📝 Ghi chú đầu tư", value=st.session_state.get("investment_notes", ""))

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
    st.session_state.last_inputs = inputs

    row_fallback = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": st.session_state.investment_notes,
        "age": inputs.age,
        "pe_current": inputs.pe_current,
        "pb_current": inputs.pb_current,
        "rf": inputs.rf,
        "us10y": inputs.us10y,
        "us_cpi": inputs.us_cpi,
        "eps_growth_exp": inputs.eps_growth_exp,
        "valuation_score": result.valuation_score,
        "equity_weight": result.equity_weight,
        "bond_weight": result.bond_weight,
        "gold_weight": result.gold_weight,
        "withdrawal_rate": result.withdrawal_rate,
    }

    if SHEETS_ON:
        try:
            row = make_log_row(inputs, result, st.session_state.investment_notes) 
            append_log_row(row)
            st.toast("✅ Đã tự động lưu kết quả lên Google Sheets!", icon="💾")
        except Exception as exc:
            st.warning(f"⚠️ Chưa ghi được log Google Sheets: {exc}")
    else:
        st.session_state.log.append(row_fallback)
        st.toast("ℹ️ Đã lưu tạm thời vào phiên.", icon="📝")

result = st.session_state.get("last_result")
inputs = st.session_state.get("last_inputs")

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

        st.success(f"**📌 {chapter}**\n\n**📌 {article}**\n\n**📌 {clause}**")

        if st.button("📜 Xem toàn văn Hiến pháp Đầu tư VAM", use_container_width=True):
            st.session_state.show_constitution = not st.session_state.show_constitution

        if st.session_state.show_constitution:
            st.info("""
            ### 📜 HIẾN PHÁP ĐẦU TƯ VAM (TOÀN VĂN)
            **Chương I: Tôn chỉ & Nguyên tắc cốt lõi**
            - **Định giá là kim chỉ nam:** Phân bổ tài sản phải dựa trên mức độ rẻ/đắt của thị trường.
            - **Kỷ luật chiến lược:** Tuyệt đối tuân thủ quy tắc tự động theo điểm số (Valuation Score).
            
            **Chương II: Quy tắc phân bổ tỷ trọng**
            - **Cổ phiếu:** Tăng tỷ trọng khi thị trường rẻ, hạ tỷ trọng khi đắt đỏ.
            - **Trái phiếu:** Đóng vai trò neo giữ sự ổn định, giảm thiểu biến động.
            - **Vàng động:** Điều chỉnh theo rủi ro vĩ mô, lạm phát thực (Real Yield).
            
            **Chương III: Quản trị rủi ro**
            - Giám sát các thông số vĩ mô (US10Y, CPI Mỹ) và hệ thống kỹ thuật đa tầng (MA20, MA200).
            """)

        rec = getattr(result, "recommendation", {})
        action = rec.get("action", "")
        headline = rec.get("headline", "")
        detail = rec.get("detail", "")
        rule_text = getattr(result, "rule_text", "")

        st.info(f"**⚖️ Quy tắc:** {rule_text}\n\n**📢 Hành động:** `{action}` - **{headline}**\n\n**📝 Chi tiết:** {detail}")

        m1, m2, m3, m4 = st.columns([1, 1, 1.2, 1.2])
        m1.metric("Cổ phiếu", f"{result.equity_weight:.1f}%")
        m2.metric("Trái phiếu", f"{result.bond_weight:.1f}%")
        m3.metric("Vàng (Dynamic)", f"{result.gold_weight:.1f}%")
        m4.metric("Rút vốn/năm", f"{result.withdrawal_rate:.1f}%")

with col2:
    st.subheader("📈 Biểu đồ phân bổ")
    if result is not None:
        weights = [result.equity_weight, result.bond_weight, result.gold_weight]
        labels = ["Cổ phiếu", "Trái phiếu", "Vàng"]
        colors = ["#2563EB", "#F59E0B", "#10B981"]
        fig = draw_plotly_pie_chart(weights, labels, colors)
        st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# TAB HIỂN THỊ CHI TIẾT & LỊCH SỬ LOGS
# ---------------------------------------------------------------------------
if result is not None and inputs is not None:
    st.markdown("---")
    tab1, tab2 = st.tabs(["🔍 Đánh giá Chi tiết Chỉ số", "📜 Lịch sử lưu Google Sheets"])
    
    with tab1:
        st.markdown("**Bảng Phân Tích Chuyên Sâu Từng Yếu Tố Của Danh Mục**")
        detail_data = []
        
        pe = inputs.pe_current
        pe_min, pe_max = inputs.pe_min, inputs.pe_max
        if pe <= pe_min:
            pe_eval, pe_cmt = "Rất Rẻ", "Định giá theo lợi nhuận đang ở vùng rất hấp dẫn, ưu tiên tích lũy."
        elif pe >= pe_max:
            pe_eval, pe_cmt = "Rất Đắt", "Thị trường trả giá quá cao so với lợi nhuận tạo ra, rủi ro điều chỉnh lớn."
        else:
            pe_eval, pe_cmt = "Hợp Lý", "Định giá P/E nằm trong vùng trung tính, cân bằng giữa rủi ro và lợi nhuận."
        detail_data.append({"Chỉ số": "P/E", "Giá trị hiện tại": f"{pe:.2f}", "Ngưỡng (Min-Max)": f"{pe_min} - {pe_max}", "Đánh giá": pe_eval, "Nhận xét chi tiết": pe_cmt})

        pb = inputs.pb_current
        pb_min, pb_max = inputs.pb_min, inputs.pb_max
        if pb <= pb_min:
            pb_eval, pb_cmt = "Rất Rẻ", "Giá thị trường đang giao dịch gần với giá trị sổ sách ròng, cơ hội mua tốt."
        elif pb >= pb_max:
            pb_eval, pb_cmt = "Rất Đắt", "Định giá P/B ở mức cao, báo hiệu sự hưng phấn quá mức."
        else:
            pb_eval, pb_cmt = "Hợp Lý", "Định giá P/B ở vùng an toàn và phù hợp với lịch sử."
        detail_data.append({"Chỉ số": "P/B", "Giá trị hiện tại": f"{pb:.2f}", "Ngưỡng (Min-Max)": f"{pb_min} - {pb_max}", "Đánh giá": pb_eval, "Nhận xét chi tiết": pb_cmt})

        erp_est = (1 / pe) * 100 - inputs.rf if pe > 0 else 0
        erp_min, erp_max = inputs.erp_min, inputs.erp_max
        if erp_est >= erp_max:
            erp_eval, erp_cmt = "Rất Hấp Dẫn", "Phần bù rủi ro cao, lợi suất cổ phiếu vượt trội so với gửi tiết kiệm không rủi ro."
        elif erp_est <= erp_min:
            erp_eval, erp_cmt = "Kém Hấp Dẫn", "Lợi suất cổ phiếu quá mỏng, không đủ bù đắp rủi ro so với trái phiếu/tiết kiệm."
        else:
            erp_eval, erp_cmt = "Trung Tính", "Phần bù rủi ro ở mức chấp nhận được cho việc nắm giữ dài hạn."
        detail_data.append({"Chỉ số": "ERP (Phần bù Rủi ro)", "Giá trị hiện tại": f"{erp_est:.2f}%", "Ngưỡng (Min-Max)": f"{erp_min}% - {erp_max}%", "Đánh giá": erp_eval, "Nhận xét chi tiết": erp_cmt})

        dy = inputs.dy_current
        dy_min, dy_max = inputs.dy_min, inputs.dy_max
        if dy >= dy_max:
            dy_eval, dy_cmt = "Tích Cực", "Dòng tiền từ cổ tức rất dồi dào, đóng vai trò phòng thủ xuất sắc khi TT biến động."
        elif dy <= dy_min:
            dy_eval, dy_cmt = "Tiêu Cực", "Tỷ suất cổ tức quá thấp, danh mục thiếu bộ đệm an toàn từ tiền mặt."
        else:
            dy_eval, dy_cmt = "Trung Bình", "Mức chi trả cổ tức duy trì ổn định, cung cấp dòng tiền vừa đủ."
        detail_data.append({"Chỉ số": "Tỷ suất Cổ tức (DY)", "Giá trị hiện tại": f"{dy:.2f}%", "Ngưỡng (Min-Max)": f"{dy_min}% - {dy_max}%", "Đánh giá": dy_eval, "Nhận xét chi tiết": dy_cmt})

        roe, roe_bench = inputs.roe_current, inputs.roe_benchmark
        roe_eval = "Tốt" if roe >= roe_bench else "Kém"
        detail_data.append({"Chỉ số": "Chất lượng (ROE)", "Giá trị hiện tại": f"{roe:.2f}%", "Ngưỡng (Min-Max)": f">= {roe_bench}%", "Đánh giá": roe_eval, "Nhận xét chi tiết": f"Hiệu quả sử dụng vốn chủ sở hữu {'đạt yêu cầu tích cực' if roe_eval=='Tốt' else 'chưa đạt kỳ vọng tối thiểu'}."})

        eps, eps_bench = inputs.eps_growth_exp, inputs.eps_growth_benchmark
        eps_eval = "Tích Cực" if eps >= eps_bench else "Tiêu Cực"
        detail_data.append({"Chỉ số": "Tăng trưởng EPS", "Giá trị hiện tại": f"{eps:.2f}%", "Ngưỡng (Min-Max)": f">= {eps_bench}%", "Đánh giá": eps_eval, "Nhận xét chi tiết": f"Triển vọng tăng trưởng lợi nhuận {'đang mở rộng, hỗ trợ tăng giá' if eps_eval=='Tích Cực' else 'đang thu hẹp, tạo áp lực giảm giá'}."})

        us_real_yield = inputs.us10y - inputs.us_cpi
        ry_eval = "Rủi Ro Cao" if us_real_yield > 2.0 else "Nới Lỏng"
        detail_data.append({"Chỉ số": "Lợi suất thực (Mỹ)", "Giá trị hiện tại": f"{us_real_yield:.2f}%", "Ngưỡng (Min-Max)": "< 2.00%", "Đánh giá": ry_eval, "Nhận xét chi tiết": f"Môi trường vĩ mô quốc tế đang {'rút thanh khoản, gây áp lực mạnh lên định giá cổ phiếu' if ry_eval=='Rủi Ro Cao' else 'cung cấp thanh khoản dồi dào, thuận lợi cho dòng tiền'}."})

        price, ma200 = inputs.price_current, inputs.ma200
        ma20 = st.session_state.get("ma20", ma200)
        
        if price >= ma200 and ma20 >= ma200:
            tech_eval, tech_cmt = "Uptrend Mạnh", "Giá và dòng tiền ngắn hạn (MA20) đều nằm trên xu hướng dài hạn (MA200), tín hiệu rất an toàn."
        elif price >= ma200 and ma20 < ma200:
            tech_eval, tech_cmt = "Hồi Phục Yếu", "Giá ở trên MA200 nhưng MA20 nằm dưới MA200, cẩn trọng nhịp hồi kỹ thuật ngắn hạn."
        elif price < ma200 and ma20 >= ma200:
            tech_eval, tech_cmt = "Cảnh Báo Sớm", "Giá đã thủng xu hướng dài hạn nhưng MA20 còn đỡ, tiềm ẩn rủi ro chuyển pha giảm."
        else:
            tech_eval, tech_cmt = "Downtrend", "Cả giá, dòng tiền ngắn hạn và xu hướng dài hạn đều nằm dưới MA200, rủi ro cấu trúc rất cao."

        detail_data.append({"Chỉ số": "Xu hướng Kỹ thuật (Đa tầng)", "Giá trị hiện tại": f"Giá: {price:.1f} | MA20: {ma20:.1f}", "Ngưỡng (Min-Max)": f"MA200: {ma200:.1f}", "Đánh giá": tech_eval, "Nhận xét chi tiết": tech_cmt})

        vol, vol_avg = inputs.volatility_current, inputs.volatility_avg
        vol_eval = "Rủi Ro Cao" if vol > vol_avg else "Ổn Định"
        detail_data.append({"Chỉ số": "Biến động (Volatility)", "Giá trị hiện tại": f"{vol:.2f}%", "Ngưỡng (Min-Max)": f"Avg: {vol_avg}%", "Đánh giá": vol_eval, "Nhận xét chi tiết": f"Trạng thái tâm lý thị trường đang {'dao động rất mạnh, tiềm ẩn rủi ro bẫy giá' if vol_eval=='Rủi Ro Cao' else 'ổn định, thuận lợi cho việc giữ vị thế'}."})
        
        df_eval = pd.DataFrame(detail_data)
        st.dataframe(df_eval, use_container_width=True, hide_index=True)
            
    with tab2:
        col_hdr1, col_hdr2 = st.columns([4, 1])
        with col_hdr1:
            st.caption("✅ Nhật ký lưu trữ lịch sử các lần tính toán VAM trên Google Sheets." if SHEETS_ON else "⚠️ Chưa cấu hình Google Sheets.")
        with col_hdr2:
            if st.button("🔄 Làm mới"):
                st.rerun()

        try:
            df_logs = load_log_df() if SHEETS_ON else pd.DataFrame(st.session_state.log)
            if not df_logs.empty:
                st.dataframe(df_logs, use_container_width=True, hide_index=True)
                
                if is_local_env():
                    if st.button("⬇️ Xuất log CSV (Tùy chọn thư mục)"):
                        success, msg = save_local_file(
                            df_logs, ".csv", [("CSV files", "*.csv")], "vam_log_export.csv"
                        )
                        if success:
                            st.toast(f"✅ Đã lưu tại: {msg}", icon="💾")
                        elif "Đã hủy" not in msg:
                            st.error(msg)
                else:
                    st.download_button(
                        "⬇️ Tải xuống log CSV",
                        df_logs.to_csv(index=False).encode("utf-8-sig"),
                        file_name="vam_log_export.csv",
                        mime="text/csv"
                    )
            else:
                st.info("Chưa có bản ghi lịch sử nào.")
        except Exception as exc:
            st.error(f"Lỗi tải dữ liệu lịch sử: {exc}")