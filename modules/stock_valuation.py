"""
modules/stock_valuation.py - VAM Portfolio Allocator & HMM Market Clock
Tích hợp:
- Dữ liệu cơ bản, kỹ thuật và Volatility chuẩn hóa từ vnstock
- Tham số vĩ mô độc lập từ Gemini AI (cơ chế cô lập lỗi, chống reset state)
- NÂNG CẤP 1: Đồng hồ chu kỳ lượng hóa bằng xác suất chuyển pha mềm HMM (Soft Probabilities)
- NÂNG CẤP 2: Bộ lọc Điều kiện Tài chính & Thanh khoản Nội địa (VN-FCI) trích xuất từ vnstock
- NÂNG CẤP 3: Thuật toán Kiểm soát Ma sát Thực thi (Turnover Cap, Deadband & Execution Cost)
"""

import json
import re
import time
import os
import math
from datetime import datetime, date, timedelta

import pandas as pd
import numpy as np
import plotly.graph_objects as go
import streamlit as st

from vam_core import VAMInputs, compute
from sheets_log import sheets_configured, append_log_row, load_log_df, make_log_row


def render():
    st.markdown(
        """
        <style>
        [data-testid="stMetricValue"] {
            font-size: clamp(1.1rem, 1.8vw, 1.8rem) !important;
            white-space: normal !important; 
        }
        .prob-card {
            background-color: #1E293B;
            border-radius: 6px;
            padding: 8px 12px;
            flex: 1 1 calc(25% - 10px);
            min-width: 110px;
            margin-bottom: 6px;
            border: 1px solid #334155;
            text-align: center;
        }
        .prob-title {
            font-size: 0.78rem;
            color: #94A3B8;
            font-weight: 600;
            white-space: nowrap;
        }
        .prob-value {
            font-size: 1.15rem;
            font-weight: 700;
            margin-top: 2px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    SHEETS_ON = sheets_configured()

    DEFAULTS = {
        "age": 40,
        "portfolio_nav": 1000.0,
        "curr_w_equity": 60.0,
        "curr_w_bond": 25.0,
        "curr_w_gold": 15.0,
        "max_turnover": 25.0,
        "fee_rate": 0.15,
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
        "vol_ratio": 1.05,
        "bank_breadth": 65.0,
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
    # NÂNG CẤP 1 & 2: HMM Soft Probabilities kết hợp VN-FCI Liquidity Engine
    # ---------------------------------------------------------------------------
    def calculate_hmm_market_clock(inputs: VAMInputs, ma20_val: float) -> dict:
        pe_norm = (inputs.pe_current - inputs.pe_min) / max(0.01, (inputs.pe_max - inputs.pe_min))
        pb_norm = (inputs.pb_current - inputs.pb_min) / max(0.01, (inputs.pb_max - inputs.pb_min))
        val_score = float(np.clip((pe_norm + pb_norm) / 2.0, 0.0, 1.0))

        p = inputs.price_current
        ma200 = inputs.ma200
        trend_long = (p - ma200) / max(1.0, ma200)
        trend_short = (p - ma20_val) / max(1.0, ma20_val)
        trend_score = float(np.clip((trend_long * 0.7 + trend_short * 0.3) * 5.0, -1.5, 1.5))

        vol_ratio = inputs.volatility_current / max(0.1, inputs.volatility_avg)
        dd_norm = float(np.clip(inputs.drawdown_pct / 30.0, 0.0, 1.5))
        stress_score = float(np.clip((vol_ratio - 1.0) + dd_norm, -1.0, 2.0))

        vr = float(st.session_state.get("vol_ratio", 1.0))
        bb = float(st.session_state.get("bank_breadth", 50.0)) / 100.0
        fci_score = float(np.clip((vr - 1.0) * 1.5 + (bb - 0.5) * 2.0, -1.5, 1.5))

        logit_rec = (1.0 - val_score) * 2.5 + stress_score * 0.8 - max(0.0, trend_score) * 1.0 + fci_score * 0.5
        logit_exp = trend_score * 2.2 + (0.5 - abs(val_score - 0.5)) * 1.2 - stress_score * 1.0 + fci_score * 1.5
        logit_slo = val_score * 2.5 - trend_short * 1.2 + (vol_ratio - 1.0) * 1.0 - fci_score * 0.8
        logit_con = -trend_score * 2.0 + stress_score * 1.5 + (val_score * 0.5) - fci_score * 1.2

        logits = np.array([logit_rec, logit_exp, logit_slo, logit_con], dtype=float)
        exp_logits = np.exp(logits - np.max(logits))
        probs = exp_logits / np.sum(exp_logits)

        p_recovery, p_expansion, p_slowdown, p_recession = probs[0], probs[1], probs[2], probs[3]

        centers_deg = np.array([180.0, 285.0, 352.5, 90.0])
        centers_rad = np.radians(centers_deg)

        mean_x = float(np.sum(probs * np.sin(centers_rad)))
        mean_y = float(np.sum(probs * np.cos(centers_rad)))

        exp_angle_rad = math.atan2(mean_x, mean_y)
        exp_angle_deg = math.degrees(exp_angle_rad) % 360.0

        clock_hour = round(float(exp_angle_deg / 30.0), 1)
        if clock_hour == 0.0:
            clock_hour = 12.0

        h_int = int(math.floor(clock_hour))
        m_int = int((clock_hour - h_int) * 60)
        time_str = f"{h_int:02d}:{m_int:02d}"

        regimes_map = [
            ("RECOVERY", "TÍCH LŨY & PHỤC HỒI (RECOVERY / 6H)", p_recovery,
             "Định giá chiết khấu sâu, xác suất phục hồi mở rộng. Dòng tiền giá trị chủ động gom tài sản rẻ.",
             "Chiến lược: Tối đa hóa tỷ trọng cổ phiếu giá trị, chủ động giải ngân theo từng nhịp điều chỉnh."),
            ("EXPANSION", "TĂNG TRƯỞNG & BÙNG NỔ (EXPANSION / 9H)", p_expansion,
             "Đồng thuận kỹ thuật và vĩ mô mạnh mẽ. Tăng trưởng lợi nhuận nâng đỡ định giá tăng bền vững.",
             "Chiến lược: Tối ưu hóa nắm giữ vị thế (Buy & Hold), duy trì tỷ trọng cổ phiếu cao theo VAM."),
            ("SLOWDOWN", "VÙNG ĐỈNH & PHÂN PHỐI (SLOWDOWN / 12H)", p_slowdown,
             "Định giá thị trường căng cứng, xác suất quá nhiệt tăng cao, xuất hiện áp lực phân phối dòng tiền.",
             "Chiến lược: Hiện thực hóa lợi nhuận từng phần, nâng tỷ trọng phòng thủ với Vàng và Trái phiếu."),
            ("RECESSION", "SUY THOÁI & SUY YẾU (CONTRACTION / 3H)", p_recession,
             "Gãy xu hướng dài hạn MA200, áp lực giải chấp diện rộng, định giá chưa đủ rẻ để tạo đáy.",
             "Chiến lược: Phòng thủ kỷ luật tối đa, hạn chế bắt dao rơi, ưu tiên bảo toàn vốn bằng Trái phiếu.")
        ]
        dominant = max(regimes_map, key=lambda x: x[2])

        if fci_score >= 0.4:
            fci_state = "Dồi Dào (Nới lỏng tiền tệ)"
            fci_color = "#4ADE80"
        elif fci_score <= -0.4:
            fci_state = "Thắt Chặt (Cạn kiệt thanh khoản)"
            fci_color = "#F87171"
        else:
            fci_state = "Trung Tính (Thanh khoản ổn định)"
            fci_color = "#FACC15"

        return {
            "hour": clock_hour,
            "time_str": time_str,
            "phase": dominant[1],
            "desc": dominant[3],
            "bias": dominant[4],
            "fci_score": fci_score,
            "fci_state": fci_state,
            "fci_color": fci_color,
            "probabilities": {
                "Recovery": p_recovery,
                "Expansion": p_expansion,
                "Slowdown": p_slowdown,
                "Recession": p_recession,
            }
        }

    # ---------------------------------------------------------------------------
    # NÂNG CẤP 3: Thuật toán Kiểm soát Ma sát Thực thi (Turnover Cap & Cost Engine)
    # ---------------------------------------------------------------------------
    def calculate_execution_friction(
        raw_weights: dict,
        curr_weights: dict,
        max_turnover_pct: float,
        fee_rate_pct: float,
        portfolio_nav_mil: float,
        deadband_pct: float = 3.0
    ) -> dict:
        """
        Lấy cảm hứng từ apply_turnover_limit trong mre_vam.py:
        - Áp dụng Deadband dung sai loại bỏ nhiễu ngắn hạn.
        - Giới hạn tốc độ đảo danh mục (Turnover Cap).
        - Tính chi phí thuế phí và ma sát thực tế.
        """
        keys = ["equity", "bond", "gold"]
        w_prev = np.array([curr_weights[k] for k in keys], dtype=float)
        w_desired = np.array([raw_weights[k] for k in keys], dtype=float)

        raw_diff = w_desired - w_prev
        turnover_raw = float(np.sum(np.abs(raw_diff)) / 2.0)

        # 1. Deadband filter: Nếu tổng lệch < deadband thì không cần tái cân bằng
        if turnover_raw < deadband_pct:
            w_exec = w_prev.copy()
            status = "Giữ Nguyên (Độ lệch trong vùng dung sai < 3%)"
            action_needed = False
            turnover_actual = 0.0
        else:
            action_needed = True
            # 2. Turnover cap: Co giãn tỷ lệ nếu vượt trần
            if turnover_raw > max_turnover_pct:
                scale = max_turnover_pct / max(0.001, turnover_raw)
                w_exec = w_prev + scale * raw_diff
                status = f"Điều chỉnh có kiểm soát (Cắt trần đảo danh mục ở {max_turnover_pct:.1f}%)"
                turnover_actual = max_turnover_pct
            else:
                w_exec = w_desired.copy()
                status = "Thực thi toàn phần theo tỷ trọng mục tiêu VAM"
                turnover_actual = turnover_raw

        # Chuẩn hóa lại tổng = 100%
        w_exec = (w_exec / np.sum(w_exec)) * 100.0

        # 3. Tính chi phí ma sát thực tế (VND)
        money_turnover = (turnover_actual / 100.0) * portfolio_nav_mil
        est_fee_cost = money_turnover * (fee_rate_pct / 100.0)

        exec_weights = {keys[i]: round(float(w_exec[i]), 1) for i in range(len(keys))}

        return {
            "exec_weights": exec_weights,
            "raw_turnover": turnover_raw,
            "actual_turnover": turnover_actual,
            "money_turnover": money_turnover,
            "est_fee_cost": est_fee_cost,
            "status": status,
            "action_needed": action_needed,
        }

    # ---------------------------------------------------------------------------
    # Vẽ Biểu đồ Đồng hồ Chu kỳ (Plotly Polar Gauge) - Responsive & Auto-scaled
    # ---------------------------------------------------------------------------
    def draw_market_clock_chart(clock_val: float):
        needle_angle = (clock_val % 12.0) * 30.0

        fig = go.Figure()

        quadrants = [
            {"theta_mid": 45, "name": "12h - 3h: Phân phối/Rơi", "color": "rgba(239, 68, 68, 0.22)"},
            {"theta_mid": 135, "name": "3h - 6h: Downtrend/Đáy", "color": "rgba(249, 115, 22, 0.22)"},
            {"theta_mid": 225, "name": "6h - 9h: Tích lũy/Bứt phá", "color": "rgba(34, 197, 94, 0.22)"},
            {"theta_mid": 315, "name": "9h - 12h: Tăng trưởng", "color": "rgba(59, 130, 246, 0.22)"},
        ]
        for q in quadrants:
            fig.add_trace(go.Barpolar(
                r=[0.80],
                theta=[q["theta_mid"]],
                width=[90],
                name=q["name"],
                marker_color=q["color"],
                showlegend=False,
                hoverinfo="none"
            ))

        fig.add_trace(go.Scatterpolar(
            r=[0, 0.72],
            theta=[needle_angle, needle_angle],
            mode="lines+markers",
            line=dict(color="#EF4444", width=3.5),
            marker=dict(size=[6, 12], color=["#1E293B", "#EF4444"]),
            name=f"Vị thế HMM: {clock_val:.1f} Giờ",
            hoverinfo="name"
        ))

        fig.update_layout(
            autosize=True,
            polar=dict(
                radialaxis=dict(visible=False, range=[0, 1.0]),
                angularaxis=dict(
                    direction="clockwise",
                    period=360,
                    rotation=90,
                    tickmode="array",
                    tickvals=[0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330],
                    ticktext=[
                        "<b>12h<br>(ĐỈNH)</b>", "1h", "2h",
                        "<b>3h<br>(Rơi)</b>", "4h", "5h",
                        "<b>6h<br>(ĐÁY)</b>", "7h", "8h",
                        "<b>9h<br>(Tăng)</b>", "10h", "11h"
                    ],
                    tickfont=dict(size=11, color="#94A3B8")
                )
            ),
            margin=dict(l=55, r=55, t=45, b=45),
            height=370,
            paper_bgcolor="rgba(0,0,0,0)",
            showlegend=False
        )
        return fig

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
                        if k == "age": st.session_state[k] = int(v)
                        elif isinstance(v, (int, float)): st.session_state[k] = float(v)
                        else: st.session_state[k] = v
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
                    st.toast("✅ Đã nạp Gemini API Key thành công!", icon="🔑")
                else:
                    st.error("❌ Định dạng API Key không hợp lệ trong file JSON!")
            except Exception as e:
                st.error(f"❌ Lỗi đọc file JSON API Key: {e}")

    def is_local_env():
        return os.name == 'nt' or os.path.exists('/home/user')

    def save_local_file(content_or_df, default_ext, file_types, initial_file):
        if not is_local_env():
            return False, "Server Cloud không hỗ trợ chọn thư mục trực tiếp."
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.attributes("-topmost", True)
            root.withdraw()
            file_path = filedialog.asksaveasfilename(defaultextension=default_ext, filetypes=file_types, initialfile=initial_file)
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
            st.error("⚠️ Thư viện `vnstock` chưa được cài đặt!")
            return {}

        results = {}
        status_box.update(label="📈 Đang tải chuỗi giá & thanh khoản VN-Index 2 năm...", state="running")
        progress_bar.progress(5)
        try:
            end_d = date.today()
            start_d = end_d - timedelta(days=730)
            quote_idx = Quote(symbol="VNINDEX", source="VCI")
            df_idx = quote_idx.history(start=start_d.isoformat(), end=end_d.isoformat(), interval="1D")
            
            if df_idx is not None and not df_idx.empty:
                cot_close = "close" if "close" in df_idx.columns else df_idx.columns[-2]
                close_prices = df_idx[cot_close].dropna()
                if close_prices.iloc[-1] < 200: close_prices = close_prices * 1000
                
                p_curr = float(close_prices.iloc[-1])
                ma20_val = float(close_prices.iloc[-20:].mean()) if len(close_prices) >= 20 else float(close_prices.mean())
                ma200_val = float(close_prices.iloc[-200:].mean()) if len(close_prices) >= 200 else float(close_prices.mean())
                
                returns = close_prices.pct_change().dropna()
                vol_curr = float(returns.iloc[-252:].std() * np.sqrt(252) * 100) if len(returns) >= 252 else float(returns.std() * np.sqrt(252) * 100)
                vol_avg = float(returns.std() * np.sqrt(252) * 100)
                
                peak = close_prices.iloc[-252:].cummax() if len(close_prices) >= 252 else close_prices.cummax()
                dd = (close_prices.iloc[-252:] - peak) / peak
                drawdown_val = float(abs(dd.min()) * 100)

                cot_vol = next((c for c in ["volume", "total_volume", "vol"] if c in df_idx.columns), None)
                if cot_vol:
                    v_series = df_idx[cot_vol].dropna()
                    v_ma20 = float(v_series.iloc[-20:].mean()) if len(v_series) >= 20 else 1.0
                    v_ma200 = float(v_series.iloc[-200:].mean()) if len(v_series) >= 200 else 1.0
                    results["vol_ratio"] = round(float(v_ma20 / max(1.0, v_ma200)), 2)
                else:
                    results["vol_ratio"] = 1.0

                results["price_current"] = round(p_curr, 1)
                results["ma20"] = round(ma20_val, 1)
                results["ma200"] = round(ma200_val, 1)
                results["volatility_current"] = round(vol_curr, 1)
                results["volatility_avg"] = round(vol_avg, 1)
                results["drawdown_pct"] = round(drawdown_val, 1)
        except Exception as e:
            st.warning(f"Không thể tính kỹ thuật VNINDEX từ vnstock: {e}")

        status_box.update(label="📋 Lấy danh sách rổ VN30 & Nhóm Ngân hàng...", state="running")
        progress_bar.progress(10)
        try:
            ref = Reference()
            ket_qua_ref = ref.index.members(symbol="VN30")
            cot_symbol = next((c for c in ["symbol", "ticker", "stock_code", "code"] if c in ket_qua_ref.columns), ket_qua_ref.columns[0]) if isinstance(ket_qua_ref, pd.DataFrame) else None
            danh_sach_ma = ket_qua_ref[cot_symbol].tolist() if cot_symbol else list(ket_qua_ref)
        except Exception as e:
            st.error(f"Lỗi lấy VN30: {e}")
            return results

        tong_ma = len(danh_sach_ma)
        fa = Fundamental()
        list_pe, list_pb, list_roe = [], [], []

        bank_tickers = {"VCB", "BID", "CTG", "TCB", "MBB", "ACB", "VPB", "HDB", "STB"}
        bank_above_ma50_count = 0
        bank_total_detected = 0

        status_box.update(label=f"🔄 Phân tích {tong_ma} mã VN30 (Chờ 10s/mã)...", state="running")

        for idx, ma in enumerate(danh_sach_ma, start=1):
            progress_bar.progress(int(10 + (idx / tong_ma) * 85))
            status_box.write(f"⏳ [{idx}/{tong_ma}] Phân tích mã **{ma}**...")
            try:
                df_ratio = fa.equity(ma).ratio(period="quarter")
                if df_ratio is not None and not df_ratio.empty:
                    cols_ky = [c for c in df_ratio.columns if re.match(r"\d{4}-Q\d", str(c))]
                    if cols_ky:
                        cols_ky.sort(key=lambda x: (int(x.split("-")[0]), int(x.split("-")[1].replace("Q", ""))), reverse=True)
                        cot_gan = cols_ky[0]
                        r_eps = df_ratio[df_ratio.get("item_id", pd.Series()) == "trailing_eps"]
                        r_roe = df_ratio[df_ratio.get("item_id", pd.Series()) == "roe_trailling"]
                        r_bvps = df_ratio[df_ratio.get("item_id", pd.Series()) == "book_value_per_share_bvps"]

                        eps_val = float(r_eps[cot_gan].values[0]) if not r_eps.empty else None
                        roe_val = float(r_roe[cot_gan].values[0]) if not r_roe.empty else None
                        bvps_val = float(r_bvps[cot_gan].values[0]) if not r_bvps.empty else None

                        q = Quote(symbol=ma, source="VCI")
                        df_q = q.history(start=(date.today()-timedelta(weeks=12)).isoformat(), end=date.today().isoformat(), interval="1D")
                        if df_q is not None and not df_q.empty:
                            cot_g = "close" if "close" in df_q.columns else df_q.columns[-2]
                            gia_dong = float(df_q.iloc[-1][cot_g])
                            if gia_dong < 2000: gia_dong *= 1000

                            if eps_val and eps_val > 0:
                                pe_calc = gia_dong / eps_val
                                if 1.0 <= pe_calc <= 70.0: list_pe.append(pe_calc)
                            if bvps_val and bvps_val > 0:
                                pb_calc = gia_dong / bvps_val
                                if 0.2 <= pb_calc <= 15.0: list_pb.append(pb_calc)
                            if roe_val is not None:
                                roe_norm = roe_val * 100.0 if abs(roe_val) <= 1.0 else roe_val
                                if -50.0 <= roe_norm <= 100.0: list_roe.append(roe_norm)

                            if ma in bank_tickers:
                                bank_total_detected += 1
                                c_series = df_q[cot_g].dropna()
                                ma50 = float(c_series.iloc[-50:].mean()) if len(c_series) >= 50 else float(c_series.mean())
                                if gia_dong >= ma50:
                                    bank_above_ma50_count += 1
            except Exception:
                pass
            time.sleep(10.0)

        if list_pe: results["pe_current"] = round(float(np.median(list_pe)), 2)
        if list_pb: results["pb_current"] = round(float(np.median(list_pb)), 2)
        if list_roe: results["roe_current"] = round(float(np.median(list_roe)), 2)

        if bank_total_detected > 0:
            results["bank_breadth"] = round(float((bank_above_ma50_count / bank_total_detected) * 100.0), 1)
        else:
            results["bank_breadth"] = 50.0

        progress_bar.progress(100)
        status_box.update(label="✅ Hoàn tất lấy dữ liệu từ vnstock!", state="complete", expanded=False)
        return results

    MISSING_PARAMS_KEYS = ["rf", "us10y", "us_cpi", "eps_growth_exp"]

    def fetch_missing_params_via_gemini(api_key: str) -> dict:
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            st.error("⚠️ Chưa cài đặt thư viện `google-genai`!")
            return {}

        client = genai.Client(api_key=api_key.strip())
        prompt = """
        Bạn là chuyên gia kinh tế vĩ mô. DÙNG GOOGLE SEARCH để tìm thông số mới nhất tính đến hôm nay.
        Chỉ trả về DUY NHẤT một chuỗi JSON thuần túy (số thập phân %, KHÔNG thêm bất kỳ ghi chú hay văn bản giải thích nào):
        {
            "rf": float (Lợi suất TPCP Việt Nam 10Y - VN10Y hiện tại),
            "us10y": float (Lợi suất TPCP Mỹ 10Y - US10Y hiện tại),
            "us_cpi": float (Lạm phát CPI Mỹ YoY mới nhất),
            "eps_growth_exp": float (Dự phóng tăng trưởng EPS bình quân rổ VN30/VN-Index năm nay theo CTCK)
        }
        """
        try:
            response = client.models.generate_content(
                model="gemini-1.5-pro",
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1, tools=[{"google_search": {}}]),
            )
            text_response = response.text.strip()
            if "{" in text_response and "}" in text_response:
                text_response = text_response[text_response.find("{"):text_response.rfind("}") + 1]
            return json.loads(text_response)
        except Exception as e:
            return {"error": str(e)}

    def draw_plotly_pie_chart(weights, labels, colors, center_text="TARGET"):
        fig = go.Figure(data=[go.Pie(
            labels=labels, values=weights, hole=0.48,
            marker=dict(colors=colors, line=dict(color="#FFFFFF", width=2.5)),
            textinfo="label+percent", hoverinfo="label+value+percent",
            textfont=dict(size=12), insidetextorientation="horizontal",
        )])
        fig.update_layout(
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=-0.18, xanchor="center", x=0.5, font=dict(size=11)),
            margin=dict(l=20, r=20, t=20, b=20), height=370,
            paper_bgcolor="rgba(0,0,0,0)",
            annotations=[dict(text=f"<b>{center_text}</b>", x=0.5, y=0.5, font_size=11, showarrow=False)]
        )
        return fig

    # ---------------------------------------------------------------------------
    # Sidebar - Nhập liệu & Cấu hình
    # ---------------------------------------------------------------------------
    st.sidebar.title("⚙️ Thông số đầu vào")

    with st.sidebar.expander("📁 Quản lý File Config (JSON)", expanded=False):
        config_data = {key: st.session_state[key] for key in DEFAULTS.keys() if key != "gemini_api_key"}
        json_string = json.dumps(config_data, indent=2, ensure_ascii=False)
        if is_local_env():
            if st.button("💾 Xuất File Config", use_container_width=True):
                success, msg = save_local_file(json_string, ".json", [("JSON files", "*.json")], "vam_input_config.json")
                if success: st.toast(f"✅ Đã lưu tại: {msg}", icon="💾")
                elif "Đã hủy" not in msg: st.error(msg)
        else:
            st.download_button("💾 Tải về File Config (JSON)", file_name="vam_input_config.json", mime="application/json", data=json_string, use_container_width=True)
        st.file_uploader("📂 Import Config từ JSON", type=["json"], key="json_file_uploader", on_change=handle_json_import)

    st.sidebar.markdown("---")
    with st.sidebar.expander("🚀 vnstock (Chứng khoán, Kỹ thuật & Thanh khoản)", expanded=True):
        if st.button("🚀 Lấy dữ liệu từ vnstock", use_container_width=True):
            p_bar = st.sidebar.progress(0)
            s_box = st.sidebar.status("Đang kết nối vnstock...", expanded=True)
            vn_data = fetch_vnstock_market_data(p_bar, s_box)
            if vn_data:
                for k, v in vn_data.items():
                    st.session_state[k] = v
                st.sidebar.success("✅ Cập nhật vnstock & VN-FCI thành công!")
                st.rerun()

    with st.sidebar.expander("🤖 Gemini AI (Vĩ mô & Lãi suất)", expanded=True):
        st.file_uploader("📂 File JSON chứa API Key", type=["json"], key="gemini_json_uploader", on_change=handle_gemini_json_import)
        
        if st.button("🌐 Gemini Auto-Fill Tham số", use_container_width=True):
            api_key = st.session_state.get("gemini_api_key", "").strip()
            if not api_key:
                st.sidebar.warning("⚠️ Vui lòng nạp Gemini API Key trước!")
            else:
                try:
                    with st.spinner("🤖 Gemini đang truy xuất vĩ mô..."):
                        missing_data = fetch_missing_params_via_gemini(api_key)
                        
                        if missing_data and "error" not in missing_data and isinstance(missing_data, dict):
                            updated = 0
                            for key in MISSING_PARAMS_KEYS:
                                if key in missing_data and missing_data[key] is not None:
                                    try:
                                        val = float(missing_data[key])
                                        st.session_state[key] = val
                                        updated += 1
                                    except (ValueError, TypeError):
                                        pass
                            
                            if updated > 0:
                                st.sidebar.success(f"✅ Đã nạp thành công {updated} tham số vĩ mô!")
                                st.rerun()
                            else:
                                st.sidebar.error("❌ Gemini không tìm thấy dữ liệu phù hợp.")
                        else:
                            err_msg = missing_data.get("error", "Lỗi phản hồi API") if isinstance(missing_data, dict) else "Lỗi kết nối"
                            st.sidebar.error(f"❌ Gemini thất bại: {err_msg}. Dữ liệu vnstock vẫn được bảo toàn nguyên vẹn!")
                except Exception as exc:
                    st.sidebar.error(f"❌ Xử lý Gemini bị gián đoạn: {exc}. Dữ liệu vnstock được giữ nguyên!")

    st.sidebar.markdown("---")
    with st.sidebar.expander("👤 Thông tin nhà đầu tư & Danh mục hiện tại", expanded=True):
        st.number_input("Tuổi của bạn", 18, 100, key="age")
        saa_equity = max(0.0, float(100 - st.session_state.age))
        saa_gold = 10.0
        saa_bond = max(0.0, 100.0 - saa_equity - saa_gold)

        st.caption("💼 **Tỷ trọng Thực tế Hiện tại (Để tính Đảo danh mục):**")
        st.number_input("Tỷ trọng Cổ phiếu Hiện tại (%)", 0.0, 100.0, step=1.0, key="curr_w_equity")
        st.number_input("Tỷ trọng Trái phiếu Hiện tại (%)", 0.0, 100.0, step=1.0, key="curr_w_bond")
        st.number_input("Tỷ trọng Vàng Hiện tại (%)", 0.0, 100.0, step=1.0, key="curr_w_gold")
        st.number_input("Tổng Quy mô Danh mục NAV (Triệu VND)", 1.0, 1e7, step=10.0, key="portfolio_nav")

    with st.sidebar.expander("⚖️ Kiểm soát Ma sát Thực thi [NÂNG CẤP 3]", expanded=True):
        st.number_input("Trần Đảo Danh Mục Max Turnover (%) [mre_vam]", 5.0, 100.0, step=5.0, key="max_turnover",
                        help="Giới hạn tối đa % danh mục được phép thay đổi trong một lần tái cân bằng")
        st.number_input("Thuế & Phí Giao dịch Ước tính (%)", 0.0, 2.0, step=0.05, key="fee_rate",
                        help="Bao gồm phí môi giới, thuế TNCN và trượt giá (mặc định 0.15%)")

    with st.sidebar.expander("📈 Định giá & P/E, P/B, ERP, DY", expanded=False):
        st.number_input("PE hiện tại", 0.01, 200.0, step=0.1, key="pe_current")
        st.number_input("PE min", 0.01, 200.0, step=0.1, key="pe_min")
        st.number_input("PE max", 0.01, 200.0, step=0.1, key="pe_max")
        st.number_input("PB hiện tại", 0.01, 50.0, step=0.05, key="pb_current")
        st.number_input("PB min", 0.01, 50.0, step=0.05, key="pb_min")
        st.number_input("PB max", 0.01, 50.0, step=0.05, key="pb_max")
        st.number_input("Lãi suất phi rủi ro Rf (%) [Gemini]", 0.0, 30.0, step=0.1, key="rf")
        st.number_input("ERP min (%)", -30.0, 30.0, step=0.1, key="erp_min")
        st.number_input("ERP max (%)", -30.0, 30.0, step=0.1, key="erp_max")
        st.number_input("DY hiện tại (%)", 0.0, 30.0, step=0.1, key="dy_current")
        st.number_input("DY min (%)", 0.0, 30.0, step=0.1, key="dy_min")
        st.number_input("DY max (%)", 0.0, 30.0, step=0.1, key="dy_max")

    with st.sidebar.expander("🌐 Vĩ mô Mỹ & Vàng Động [Gemini]", expanded=False):
        st.number_input("US10Y (%)", 0.0, 20.0, step=0.05, key="us10y")
        st.number_input("Lạm phát CPI Mỹ (%)", -5.0, 30.0, step=0.1, key="us_cpi")

    with st.sidebar.expander("🛡️ Chất lượng & Tăng trưởng", expanded=False):
        st.number_input("ROE hiện tại (%)", -50.0, 100.0, step=0.5, key="roe_current")
        st.number_input("ROE chuẩn (%)", 0.0, 100.0, step=0.5, key="roe_benchmark")
        st.number_input("Tăng trưởng EPS dự phóng (%) [Gemini]", -100.0, 200.0, step=0.5, key="eps_growth_exp")
        st.number_input("Tăng trưởng EPS chuẩn (%)", -50.0, 100.0, step=0.5, key="eps_growth_benchmark")

    with st.sidebar.expander("📉 Xu hướng Kỹ thuật & Biến động [vnstock]", expanded=False):
        st.number_input("VN-Index Giá", 0.0, 1e7, step=1.0, key="price_current")
        st.number_input("VN-Index MA20", 0.0, 1e7, step=1.0, key="ma20")
        st.number_input("VN-Index MA200", 0.0, 1e7, step=1.0, key="ma200")
        st.number_input("Volatility thực tế 1Y (%)", 0.0, 200.0, step=0.5, key="volatility_current")
        st.number_input("Volatility TB 2Y (%) [vnstock]", 0.0, 200.0, step=0.5, key="volatility_avg")
        st.number_input("Drawdown (%)", 0.0, 100.0, step=0.5, key="drawdown_pct")

    with st.sidebar.expander("💧 Thanh khoản Nội địa VN-FCI [vnstock]", expanded=False):
        st.number_input("Động lượng Volume MA20/MA200", 0.0, 10.0, step=0.05, key="vol_ratio",
                        help="> 1.0: Dòng tiền nở rộ; < 0.8: Dòng tiền rút lui")
        st.number_input("Độ rộng Nhóm Ngân hàng > MA50 (%)", 0.0, 100.0, step=1.0, key="bank_breadth",
                        help="% cổ phiếu ngân hàng trụ cột giữ vững xu hướng trung hạn")

    st.sidebar.markdown("---")
    st.session_state.investment_notes = st.sidebar.text_area("📝 Ghi chú đầu tư", value=st.session_state.get("investment_notes", ""))
    calc_clicked = st.sidebar.button("🚀 Tính toán phân bổ", use_container_width=True, type="primary")

    # ---------------------------------------------------------------------------
    # Màn hình chính
    # ---------------------------------------------------------------------------
    st.title("📊 VAM Multi-Asset Allocator & HMM Market Clock")

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

        if SHEETS_ON:
            try:
                row = make_log_row(inputs, result, st.session_state.investment_notes)
                append_log_row(row)
                st.toast("✅ Đã lưu Google Sheets!", icon="💾")
            except Exception as exc:
                st.warning(f"⚠️ Chưa ghi log Sheets: {exc}")
        else:
            st.session_state.log.append({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "note": st.session_state.investment_notes,
                "valuation_score": result.valuation_score,
                "equity_weight": result.equity_weight,
                "bond_weight": result.bond_weight,
                "gold_weight": result.gold_weight,
            })
            st.toast("ℹ️ Đã lưu tạm phiên làm việc.", icon="📝")

    result = st.session_state.get("last_result")
    inputs = st.session_state.get("last_inputs")

    if result is None:
        st.info('👈 Nhập thông số hoặc bấm "🚀 Tính toán phân bổ" ở thanh bên trái.')
        return

    # ---------------------------------------------------------------------------
    # TÍNH TOÁN & HIỂN THỊ ĐỒNG HỒ CHU KỲ (HMM + VN-FCI ENGINE)
    # ---------------------------------------------------------------------------
    ma20_curr = float(st.session_state.get("ma20", inputs.ma200))
    clock_data = calculate_hmm_market_clock(inputs, ma20_curr)
    probs = clock_data["probabilities"]

    st.markdown(
        f"""
        <div style="background-color: #0F172A; border-left: 6px solid #38BDF8; padding: 16px 20px; border-radius: 8px; margin-bottom: 20px;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                <div>
                    <div style="font-size: 0.85rem; text-transform: uppercase; color: #94A3B8; font-weight: 600;">
                        🕰️ ĐỒNG HỒ CHU KỲ HMM & THANH KHOẢN VN-FCI
                    </div>
                    <div style="font-size: 1.45rem; font-weight: 700; color: #F8FAFC; margin-top: 4px;">
                        {clock_data['phase']}
                    </div>
                </div>
                <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                    <div style="background: #1E293B; padding: 8px 14px; border-radius: 6px; border: 1px solid #334155; text-align: center;">
                        <span style="font-size: 0.75rem; color: #94A3B8;">Thanh khoản VN-FCI:</span><br>
                        <span style="font-size: 1.05rem; font-weight: 700; color: {clock_data['fci_color']};">{clock_data['fci_state']}</span>
                    </div>
                    <div style="background: #1E293B; padding: 8px 14px; border-radius: 6px; border: 1px solid #334155; text-align: center;">
                        <span style="font-size: 0.75rem; color: #94A3B8;">Vị thế Giờ HMM:</span><br>
                        <span style="font-size: 1.35rem; font-weight: 800; color: #38BDF8;">🕒 {clock_data['time_str']}</span>
                    </div>
                </div>
            </div>
            <div style="font-size: 0.95rem; color: #CBD5E1; line-height: 1.5; margin-top: 10px;">
                📌 <b>Trạng thái:</b> {clock_data['desc']}<br>
                💡 <b>Hành động:</b> {clock_data['bias']}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ---------------------------------------------------------------------------
    # NÂNG CẤP 3: Tính toán Thực thi (Execution Friction & Turnover Cap)
    # ---------------------------------------------------------------------------
    raw_w = {"equity": result.equity_weight, "bond": result.bond_weight, "gold": result.gold_weight}
    curr_w = {
        "equity": float(st.session_state.get("curr_w_equity", 60.0)),
        "bond": float(st.session_state.get("curr_w_bond", 25.0)),
        "gold": float(st.session_state.get("curr_w_gold", 15.0)),
    }
    # Chuẩn hóa tổng current weight = 100%
    curr_sum = sum(curr_w.values())
    if curr_sum > 0:
        curr_w = {k: (v / curr_sum) * 100.0 for k, v in curr_w.items()}

    frict_res = calculate_execution_friction(
        raw_weights=raw_w,
        curr_weights=curr_w,
        max_turnover_pct=float(st.session_state.get("max_turnover", 25.0)),
        fee_rate_pct=float(st.session_state.get("fee_rate", 0.15)),
        portfolio_nav_mil=float(st.session_state.get("portfolio_nav", 1000.0)),
    )
    exec_w = frict_res["exec_weights"]

    # Hiển thị song song: Đồng hồ Chu kỳ và Biểu đồ Phân bổ Thực thi
    c_graph1, c_graph2 = st.columns([1, 1])

    with c_graph1:
        st.subheader("🕒 Đồng hồ Chu kỳ HMM")
        fig_clock = draw_market_clock_chart(clock_data["hour"])
        st.plotly_chart(fig_clock, use_container_width=True, config={"responsive": True})

        st.markdown(
            f"""
            <div style="margin-top: 10px;">
                <div style="font-size: 0.85rem; color: #94A3B8; font-weight: 600; margin-bottom: 6px;">
                    📊 Phân phối xác suất 4 Pha Chu kỳ Vĩ mô (HMM Soft Probabilities):
                </div>
                <div style="display: flex; flex-wrap: wrap; gap: 8px; width: 100%;">
                    <div class="prob-card">
                        <div class="prob-title">🟢 Tích lũy (6h)</div>
                        <div class="prob-value" style="color: #4ADE80;">{probs['Recovery']*100:.1f}%</div>
                    </div>
                    <div class="prob-card">
                        <div class="prob-title">🔵 Tăng trưởng (9h)</div>
                        <div class="prob-value" style="color: #60A5FA;">{probs['Expansion']*100:.1f}%</div>
                    </div>
                    <div class="prob-card">
                        <div class="prob-title">🟡 Phân phối (12h)</div>
                        <div class="prob-value" style="color: #FACC15;">{probs['Slowdown']*100:.1f}%</div>
                    </div>
                    <div class="prob-card">
                        <div class="prob-title">🔴 Suy thoái (3h)</div>
                        <div class="prob-value" style="color: #F87171;">{probs['Recession']*100:.1f}%</div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with c_graph2:
        st.subheader("📈 Tỷ trọng Thực thi (Turnover Capped)")
        weights_exec = [exec_w["equity"], exec_w["bond"], exec_w["gold"]]
        labels = ["Cổ phiếu", "Trái phiếu", "Vàng"]
        colors = ["#2563EB", "#F59E0B", "#10B981"]
        fig_pie = draw_plotly_pie_chart(weights_exec, labels, colors, center_text="EXECUTABLE")
        st.plotly_chart(fig_pie, use_container_width=True, config={"responsive": True})

    # ---------------------------------------------------------------------------
    # NÂNG CẤP 3: Thẻ Đo lường Ma sát & Chi phí Thực thi (Turnover & Cost Dashboard)
    # ---------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("⚖️ Bảng Thực thi & Kiểm soát Ma sát Đảo danh mục (Turnover Safeguard)")

    col_t1, col_t2, col_t3, col_t4 = st.columns(4)
    col_t1.metric("Đảo danh mục Dự kiến", f"{frict_res['actual_turnover']:.1f}%",
                  delta=f"Lý thuyết: {frict_res['raw_turnover']:.1f}%", delta_color="inverse")
    col_t2.metric("Giá trị Luân chuyển", f"{frict_res['money_turnover']:.1f} Tr VND")
    col_t3.metric("Chi phí Ma sát Ước tính", f"{frict_res['est_fee_cost']*1e6:,.0f} VND",
                  help=f"Tính theo mức thuế phí {st.session_state.fee_rate}%")
    col_t4.metric("Lệnh Khuyến nghị", "Thực thi" if frict_res['action_needed'] else "Nắm giữ (Hold)")

    if frict_res['action_needed']:
        st.info(f"💡 **Trạng thái thực thi:** {frict_res['status']}. "
                f"Tỷ trọng khuyến nghị điều chỉnh: **Cổ phiếu {exec_w['equity']:.1f}%** | "
                f"**Trái phiếu {exec_w['bond']:.1f}%** | **Vàng {exec_w['gold']:.1f}%**.")
    else:
        st.success(f"✅ **Trạng thái thực thi:** {frict_res['status']}. "
                   f"Lợi ích điều chỉnh không bù đắp được chi phí ma sát và trượt giá. Danh mục được khuyến nghị giữ nguyên trạng thái.")

    # Chỉ số & Khuyến nghị VAM
    st.markdown("---")
    m1, m2, m3, m4, m5 = st.columns([1.2, 1, 1, 1, 1])
    m1.metric("VAM Valuation Score", f"{result.valuation_score:.2f}")
    m2.metric("CP Lý thuyết", f"{result.equity_weight:.1f}%")
    m3.metric("TP Lý thuyết", f"{result.bond_weight:.1f}%")
    m4.metric("Vàng Lý thuyết", f"{result.gold_weight:.1f}%")
    m5.metric("Rút vốn/năm", f"{result.withdrawal_rate:.1f}%")

    rec = getattr(result, "recommendation", {})
    action = rec.get("action", "")
    headline = rec.get("headline", "")
    detail = rec.get("detail", "")
    rule_text = getattr(result, "rule_text", "")
    st.info(f"**⚖️ Quy tắc:** {rule_text}\n\n**📢 Hành động:** `{action}` - **{headline}**\n\n**📝 Chi tiết:** {detail}")

    # Tabs Đánh giá chi tiết & Logs
    tab1, tab2 = st.tabs(["🔍 Bảng So sánh Tỷ trọng & Chi tiết Chỉ số", "📜 Lịch sử lưu Google Sheets"])

    with tab1:
        st.markdown("**1. Bảng So sánh Tỷ trọng: Hiện tại vs Lý thuyết VAM vs Thực thi Capped**")
        df_weights_compare = pd.DataFrame([
            {
                "Tài sản": "Cổ phiếu",
                "Hiện tại (%)": f"{curr_w['equity']:.1f}%",
                "Mục tiêu Lý thuyết (%)": f"{result.equity_weight:.1f}%",
                "Thực thi Capped (%)": f"{exec_w['equity']:.1f}%",
                "Thay đổi Thực thi (%)": f"{exec_w['equity'] - curr_w['equity']:+.1f}%"
            },
            {
                "Tài sản": "Trái phiếu",
                "Hiện tại (%)": f"{curr_w['bond']:.1f}%",
                "Mục tiêu Lý thuyết (%)": f"{result.bond_weight:.1f}%",
                "Thực thi Capped (%)": f"{exec_w['bond']:.1f}%",
                "Thay đổi Thực thi (%)": f"{exec_w['bond'] - curr_w['bond']:+.1f}%"
            },
            {
                "Tài sản": "Vàng",
                "Hiện tại (%)": f"{curr_w['gold']:.1f}%",
                "Mục tiêu Lý thuyết (%)": f"{result.gold_weight:.1f}%",
                "Thực thi Capped (%)": f"{exec_w['gold']:.1f}%",
                "Thay đổi Thực thi (%)": f"{exec_w['gold'] - curr_w['gold']:+.1f}%"
            },
        ])
        st.dataframe(df_weights_compare, use_container_width=True, hide_index=True)

        st.markdown("**2. Bảng Đánh giá Các Chỉ số Đầu vào & Thanh khoản**")
        detail_data = [
            {"Chỉ số": "Vị thế Giờ HMM", "Giá trị": f"{clock_data['time_str']}", "Pha": clock_data['phase'], "Nhận xét": clock_data['desc']},
            {"Chỉ số": "Thanh khoản VN-FCI", "Giá trị": f"Score: {clock_data['fci_score']:+.2f}", "Pha": clock_data['fci_state'], "Nhận xét": "Đo lường mức độ nới lỏng dòng tiền và sức khỏe nhóm cổ phiếu Ngân hàng."},
            {"Chỉ số": "Động lượng Volume", "Giá trị": f"{st.session_state.get('vol_ratio', 1.0):.2f}x", "Pha": "Vol MA20 / MA200", "Nhận xét": "Tốc độ giãn nở thanh khoản giao dịch so với trung bình 1 năm."},
            {"Chỉ số": "Độ rộng Ngân hàng", "Giá trị": f"{st.session_state.get('bank_breadth', 50.0):.1f}%", "Pha": "% Cổ phiếu > MA50", "Nhận xét": "Tỷ lệ cổ phiếu ngân hàng duy trì xu hướng tăng trung hạn."},
            {"Chỉ số": "P/E", "Giá trị": f"{inputs.pe_current:.2f}", "Pha": f"Min: {inputs.pe_min} - Max: {inputs.pe_max}", "Nhận xét": "Vùng định giá theo lợi nhuận rổ VN30."},
            {"Chỉ số": "P/B", "Giá trị": f"{inputs.pb_current:.2f}", "Pha": f"Min: {inputs.pb_min} - Max: {inputs.pb_max}", "Nhận xét": "Vùng định giá theo giá trị sổ sách."},
            {"Chỉ số": "Kỹ thuật Đa tầng", "Giá trị": f"Giá: {inputs.price_current:.1f} | MA20: {ma20_curr:.1f}", "Pha": f"MA200: {inputs.ma200:.1f}", "Nhận xét": "Cấu trúc dòng tiền ngắn hạn và xu hướng dài hạn."}
        ]
        st.dataframe(pd.DataFrame(detail_data), use_container_width=True, hide_index=True)

    with tab2:
        try:
            df_logs = load_log_df() if SHEETS_ON else pd.DataFrame(st.session_state.log)
            if not df_logs.empty:
                st.dataframe(df_logs, use_container_width=True, hide_index=True)
            else:
                st.info("Chưa có bản ghi lịch sử nào.")
        except Exception as exc:
            st.error(f"Lỗi tải logs: {exc}")