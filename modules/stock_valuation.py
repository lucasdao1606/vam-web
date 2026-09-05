"""
modules/stock_valuation.py - VAM Portfolio Allocator & HMM Market Clock
Tích hợp:
- Dữ liệu cơ bản, kỹ thuật và Volatility chuẩn hóa từ vnstock
- Tham số vĩ mô độc lập từ Gemini AI
- NÂNG CẤP 1 & 2: Đồng hồ chu kỳ HMM & Thanh khoản VN-FCI
- NÂNG CẤP 3: Thuật toán Kiểm soát Ma sát Thực thi
- NÂNG CẤP 4: Tự động hóa cập nhật API & Báo cáo Telegram
- NÂNG CẤP 5: Bảo mật đa tầng (Secrets, Env, AES-128)
- NÂNG CẤP 6: Hệ thống Ghi Log File
- NÂNG CẤP 7: Đồng bộ UI & Decoupling Logic
- NÂNG CẤP 8: Bảo mật Write-Only cho các trường API
- NÂNG CẤP 9: Báo cáo Telegram đầy đủ và chi tiết như giao diện UI
- NÂNG CẤP 10: Tự động Forward tin nhắn từ Admin lên Public Channel
- NÂNG CẤP 11: Tự động lưu trữ và đồng bộ vào stock_database.csv
- NÂNG CẤP 12: Bộ Scheduler độc lập (stock_scheduler) chống xung đột
- NÂNG CẤP 13: Tự động Git Commit & Push file stock_database.csv lên GitHub
- NÂNG CẤP 14: Nút bấm xác nhận kích hoạt lịch định kỳ (Tránh tự động chạy khi chỉ mới nhập giờ)
"""

import json
import re
import time
import os
import math
import subprocess
from datetime import datetime, date, timedelta, timezone
import threading
import schedule
import requests
import logging

import pandas as pd
import numpy as np
import plotly.graph_objects as go
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx

from vam_core import VAMInputs, compute
from sheets_log import sheets_configured, append_log_row, load_log_df, make_log_row

STOCK_DB_FILE = "stock_database.csv"

# Khởi tạo Scheduler độc lập cho riêng Stock
stock_scheduler = schedule.Scheduler()

# Khởi tạo File Logging cho quá trình chạy ngầm
logging.basicConfig(
    filename='bot_schedule.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

# ---------------------------------------------------------------------------
# HÀM TỰ ĐỘNG GIT COMMIT & PUSH (NON-BLOCKING BACKGROUND THREAD)
# ---------------------------------------------------------------------------
def git_auto_commit_push(file_path: str, commit_message: str):
    def _task():
        try:
            subprocess.run(["git", "add", "-f", file_path], check=True, capture_output=True, text=True)
            res = subprocess.run(["git", "commit", "-m", commit_message], capture_output=True, text=True)
            if "nothing to commit" in res.stdout or "nothing to commit" in res.stderr:
                logging.info(f"[GIT AUTO-SYNC] {file_path}: Không có thay đổi mới để commit.")
                return
            subprocess.run(["git", "push", "origin", "main"], check=True, capture_output=True, text=True)
            logging.info(f"[GIT AUTO-SYNC] {file_path}: Đã đẩy dữ liệu mới lên GitHub thành công.")
        except subprocess.CalledProcessError as e:
            logging.error(f"[GIT AUTO-SYNC] Lỗi Git lệnh: {e.stderr or e.stdout}")
        except Exception as ex:
            logging.error(f"[GIT AUTO-SYNC] Lỗi không xác định: {ex}")

    th = threading.Thread(target=_task, daemon=True)
    th.start()

# ---------------------------------------------------------------------------
# CƠ CHẾ BẢO MẬT & MÃ HÓA (SECURITY VAULT)
# ---------------------------------------------------------------------------
try:
    from cryptography.fernet import Fernet
    CRYPTO_ENABLED = True
except ImportError:
    CRYPTO_ENABLED = False

def get_secret(key_name, default=""):
    val = os.getenv(key_name)
    if not val:
        try:
            val = st.secrets.get(key_name, default)
        except Exception:
            val = default
    return val

def get_or_create_fernet_key():
    if not CRYPTO_ENABLED: return None
    key = get_secret("APP_ENCRYPTION_KEY")
    key_file = ".fernet_key"
    if not key:
        if os.path.exists(key_file):
            with open(key_file, "rb") as f: key = f.read().decode()
        else:
            key = Fernet.generate_key().decode()
            try:
                with open(key_file, "wb") as f: f.write(key.encode())
            except Exception: pass
    return key.encode()

FERNET_KEY = get_or_create_fernet_key()
CIPHER_SUITE = Fernet(FERNET_KEY) if FERNET_KEY else None

def save_secure_vault(data: dict) -> bool:
    if not CIPHER_SUITE: return False
    try:
        json_str = json.dumps(data)
        encrypted_data = CIPHER_SUITE.encrypt(json_str.encode())
        with open("secure_vault.enc", "wb") as f: f.write(encrypted_data)
        return True
    except Exception as e:
        logging.error(f"Lỗi mã hóa: {e}")
        return False

def load_secure_vault() -> dict:
    if not CIPHER_SUITE or not os.path.exists("secure_vault.enc"): return {}
    try:
        with open("secure_vault.enc", "rb") as f: encrypted_data = f.read()
        decrypted_data = CIPHER_SUITE.decrypt(encrypted_data).decode()
        return json.loads(decrypted_data)
    except Exception as e:
        logging.error(f"Lỗi giải mã: {e}")
        return {}

SENSITIVE_KEYS = {
    "gemini_api_key", "telegram_token", "telegram_chat_id", "telegram_channel_id",
    "schedule_mode", "schedule_time", "crypto_schedule_mode", "crypto_schedule_time"
}
GLOBAL_CONFIG = {}

# ---------------------------------------------------------------------------
# CƠ CHẾ LƯU TRỮ VÀ QUẢN TRỊ FILE stock_database.csv
# ---------------------------------------------------------------------------
def load_stock_database(db_path: str = STOCK_DB_FILE) -> pd.DataFrame:
    if os.path.exists(db_path):
        try:
            return pd.read_csv(db_path, encoding="utf-8-sig")
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

def save_stock_database(inputs: VAMInputs, result, frict_res: dict, clock_data: dict, curr_w: dict, note: str = "", db_path: str = STOCK_DB_FILE) -> bool:
    try:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        ts_local = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ts_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        exec_w = frict_res.get("exec_weights", {})
        rec = getattr(result, "recommendation", {})

        asset_map = [
            ("Cổ phiếu", "equity", curr_w.get("equity", 0.0), result.equity_weight, exec_w.get("equity", 0.0)),
            ("Trái phiếu", "bond", curr_w.get("bond", 0.0), result.bond_weight, exec_w.get("bond", 0.0)),
            ("Vàng", "gold", curr_w.get("gold", 0.0), result.gold_weight, exec_w.get("gold", 0.0))
        ]

        records = []
        for name_vn, key, cur, tgt, exc in asset_map:
            records.append({
                "run_id": run_id,
                "timestamp": ts_local,
                "timestamp_utc": ts_utc,
                "asset": name_vn,
                "asset_key": key,
                "current_weight_pct": round(float(cur), 2),
                "target_weight_pct": round(float(tgt), 2),
                "exec_weight_pct": round(float(exc), 2),
                "drift_pct": round(float(exc - cur), 2),
                "valuation_score": round(float(result.valuation_score), 2),
                "withdrawal_rate_pct": round(float(result.withdrawal_rate), 2),
                "rebalance_action_needed": frict_res.get("action_needed", False),
                "rebalance_status": frict_res.get("status", ""),
                "actual_turnover_pct": round(float(frict_res.get("actual_turnover", 0.0)), 2),
                "money_turnover_mil": round(float(frict_res.get("money_turnover", 0.0)), 2),
                "est_fee_cost_vnd": round(float(frict_res.get("est_fee_cost", 0.0) * 1e6), 0),
                "hmm_hour": round(float(clock_data.get("hour", 0.0)), 2),
                "hmm_time_str": clock_data.get("time_str", ""),
                "hmm_phase": clock_data.get("phase", ""),
                "fci_score": round(float(clock_data.get("fci_score", 0.0)), 2),
                "fci_state": clock_data.get("fci_state", ""),
                "prob_recovery": round(float(clock_data.get("probabilities", {}).get("Recovery", 0.0)), 4),
                "prob_expansion": round(float(clock_data.get("probabilities", {}).get("Expansion", 0.0)), 4),
                "prob_slowdown": round(float(clock_data.get("probabilities", {}).get("Slowdown", 0.0)), 4),
                "prob_recession": round(float(clock_data.get("probabilities", {}).get("Recession", 0.0)), 4),
                "vnindex_price": round(float(inputs.price_current), 2),
                "vnindex_ma20": round(float(getattr(inputs, "ma20", inputs.ma200)), 2),
                "vnindex_ma200": round(float(inputs.ma200), 2),
                "volatility_current": round(float(inputs.volatility_current), 2),
                "volatility_avg": round(float(inputs.volatility_avg), 2),
                "drawdown_pct": round(float(inputs.drawdown_pct), 2),
                "pe_current": round(float(inputs.pe_current), 2),
                "pb_current": round(float(inputs.pb_current), 2),
                "roe_current": round(float(inputs.roe_current), 2),
                "rf_pct": round(float(inputs.rf), 2),
                "us10y_pct": round(float(inputs.us10y), 2),
                "us_cpi_pct": round(float(inputs.us_cpi), 2),
                "recommendation_action": rec.get("action", ""),
                "recommendation_headline": rec.get("headline", ""),
                "investment_note": note
            })

        df_new = pd.DataFrame(records)
        if not os.path.exists(db_path):
            df_new.to_csv(db_path, mode="w", index=False, encoding="utf-8-sig")
        else:
            df_new.to_csv(db_path, mode="a", header=False, index=False, encoding="utf-8-sig")

        git_auto_commit_push(db_path, f"data(stock): Auto-record VAM analysis {ts_local}")
        return True
    except PermissionError:
        logging.warning(f"File {db_path} đang mở trong ứng dụng khác.")
        return False
    except Exception as e:
        logging.error(f"Lỗi lưu trữ {db_path}: {e}")
        return False

# ---------------------------------------------------------------------------
# API LIÊN KẾT BÊN NGOÀI
# ---------------------------------------------------------------------------
def send_telegram_msg(token, chat_id, message):
    if not token or not chat_id: return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            return data.get("result", {}).get("message_id", True)
        return True
    except Exception as e:
        logging.error(f"Lỗi gửi Telegram: {e}")
        return False

def forward_telegram_msg(token, target_chat_id, from_chat_id, message_id):
    if not token or not target_chat_id: return False
    url = f"https://api.telegram.org/bot{token}/forwardMessage"
    payload = {"chat_id": target_chat_id, "from_chat_id": from_chat_id, "message_id": message_id}
    try:
        res = requests.post(url, json=payload, timeout=10)
        return res.json().get("ok", False)
    except Exception as e:
        logging.error(f"Lỗi forward Telegram: {e}")
        print(f"[TELEGRAM BOT] Lỗi API forward: {e}")
        return False

# ---------------------------------------------------------------------------
# LOGIC TÍNH TOÁN CORE
# ---------------------------------------------------------------------------
def calculate_hmm_market_clock(inputs: VAMInputs, ma20_val: float, vol_ratio: float, bank_breadth: float) -> dict:
    pe_norm = (inputs.pe_current - inputs.pe_min) / max(0.01, (inputs.pe_max - inputs.pe_min))
    pb_norm = (inputs.pb_current - inputs.pb_min) / max(0.01, (inputs.pb_max - inputs.pb_min))
    val_score = float(np.clip((pe_norm + pb_norm) / 2.0, 0.0, 1.0))
    p, ma200 = inputs.price_current, inputs.ma200
    trend_long = (p - ma200) / max(1.0, ma200)
    trend_short = (p - ma20_val) / max(1.0, ma20_val)
    trend_score = float(np.clip((trend_long * 0.7 + trend_short * 0.3) * 5.0, -1.5, 1.5))
    vol_r = inputs.volatility_current / max(0.1, inputs.volatility_avg)
    dd_norm = float(np.clip(inputs.drawdown_pct / 30.0, 0.0, 1.5))
    stress_score = float(np.clip((vol_r - 1.0) + dd_norm, -1.0, 2.0))
    vr = float(vol_ratio)
    bb = float(bank_breadth) / 100.0
    fci_score = float(np.clip((vr - 1.0) * 1.5 + (bb - 0.5) * 2.0, -1.5, 1.5))

    logit_rec = (1.0 - val_score) * 2.5 + stress_score * 0.8 - max(0.0, trend_score) * 1.0 + fci_score * 0.5
    logit_exp = trend_score * 2.2 + (0.5 - abs(val_score - 0.5)) * 1.2 - stress_score * 1.0 + fci_score * 1.5
    logit_slo = val_score * 2.5 - trend_short * 1.2 + (vol_r - 1.0) * 1.0 - fci_score * 0.8
    logit_con = -trend_score * 2.0 + stress_score * 1.5 + (val_score * 0.5) - fci_score * 1.2

    logits = np.array([logit_rec, logit_exp, logit_slo, logit_con], dtype=float)
    probs = np.exp(logits - np.max(logits)) / np.sum(np.exp(logits - np.max(logits)))
    p_recovery, p_expansion, p_slowdown, p_recession = probs[0], probs[1], probs[2], probs[3]

    centers_rad = np.radians(np.array([180.0, 285.0, 352.5, 90.0]))
    exp_angle_deg = math.degrees(math.atan2(float(np.sum(probs * np.sin(centers_rad))), float(np.sum(probs * np.cos(centers_rad))))) % 360.0
    clock_hour = round(float(exp_angle_deg / 30.0), 1)
    if clock_hour == 0.0: clock_hour = 12.0
    h_int, m_int = int(math.floor(clock_hour)), int((clock_hour - int(math.floor(clock_hour))) * 60)
    time_str = f"{h_int:02d}:{m_int:02d}"

    regimes_map = [
        ("RECOVERY", "TÍCH LŨY (6H)", p_recovery, "Định giá rẻ, gom tài sản.", "Ưu tiên Cổ phiếu"),
        ("EXPANSION", "TĂNG TRƯỞNG (9H)", p_expansion, "Đồng thuận vĩ mô, tăng trưởng.", "Buy & Hold"),
        ("SLOWDOWN", "PHÂN PHỐI (12H)", p_slowdown, "Quá nhiệt, định giá cao.", "Nâng phòng thủ"),
        ("RECESSION", "SUY THOÁI (3H)", p_recession, "Gãy xu hướng, áp lực bán.", "Bảo toàn vốn")
    ]
    dominant = max(regimes_map, key=lambda x: x[2])
    if fci_score >= 0.4: fci_state, fci_color = "Dồi Dào", "#4ADE80"
    elif fci_score <= -0.4: fci_state, fci_color = "Thắt Chặt", "#F87171"
    else: fci_state, fci_color = "Trung Tính", "#FACC15"

    return {"hour": clock_hour, "time_str": time_str, "phase": dominant[1], "desc": dominant[3], "bias": dominant[4], "fci_score": fci_score, "fci_state": fci_state, "fci_color": fci_color, "probabilities": {"Recovery": p_recovery, "Expansion": p_expansion, "Slowdown": p_slowdown, "Recession": p_recession}}

def calculate_execution_friction(raw_weights, curr_weights, max_turnover_pct, fee_rate_pct, portfolio_nav_mil, deadband_pct=3.0):
    keys = ["equity", "bond", "gold"]
    w_prev = np.array([curr_weights[k] for k in keys], dtype=float)
    w_desired = np.array([raw_weights[k] for k in keys], dtype=float)
    raw_diff = w_desired - w_prev
    turnover_raw = float(np.sum(np.abs(raw_diff)) / 2.0)

    if turnover_raw < deadband_pct:
        w_exec, status, action_needed, turnover_actual = w_prev.copy(), "Độ lệch trong vùng dung sai < 3%", False, 0.0
    else:
        action_needed = True
        if turnover_raw > max_turnover_pct:
            w_exec = w_prev + (max_turnover_pct / turnover_raw) * raw_diff
            status, turnover_actual = f"Cắt trần ở {max_turnover_pct:.1f}%", max_turnover_pct
        else:
            w_exec, status, turnover_actual = w_desired.copy(), "Thực thi toàn phần", turnover_raw

    w_exec = (w_exec / np.sum(w_exec)) * 100.0
    exec_weights = {keys[i]: round(float(w_exec[i]), 1) for i in range(len(keys))}
    return {"exec_weights": exec_weights, "raw_turnover": turnover_raw, "actual_turnover": turnover_actual, "money_turnover": (turnover_actual / 100.0) * portfolio_nav_mil, "est_fee_cost": ((turnover_actual / 100.0) * portfolio_nav_mil) * (fee_rate_pct / 100.0), "status": status, "action_needed": action_needed}

def fetch_vnstock_market_data(progress_bar=None, status_box=None, log_func=print) -> dict:
    try: 
        from vnstock import Reference, Fundamental, Quote
    except ImportError:
        log_func("⚠️ Cần cài thư viện vnstock!")
        return {}
    results = {}
    if status_box: status_box.update(label="📈 Tải dữ liệu VNINDEX...", state="running")
    if progress_bar: progress_bar.progress(5)
    try:
        end_d = date.today()
        start_d = end_d - timedelta(days=730)
        df_idx = Quote(symbol="VNINDEX", source="VCI").history(start=start_d.isoformat(), end=end_d.isoformat(), interval="1D")
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
            drawdown_val = float(abs(((close_prices.iloc[-252:] - peak) / peak).min()) * 100)

            cot_vol = next((c for c in ["volume", "total_volume", "vol"] if c in df_idx.columns), None)
            if cot_vol:
                v_series = df_idx[cot_vol].dropna()
                v_ma20 = float(v_series.iloc[-20:].mean()) if len(v_series) >= 20 else 1.0
                v_ma200 = float(v_series.iloc[-200:].mean()) if len(v_series) >= 200 else 1.0
                results["vol_ratio"] = round(float(v_ma20 / max(1.0, v_ma200)), 2)
            else: results["vol_ratio"] = 1.0

            results.update({"price_current": round(p_curr, 1), "ma20": round(ma20_val, 1), "ma200": round(ma200_val, 1), "volatility_current": round(vol_curr, 1), "volatility_avg": round(vol_avg, 1), "drawdown_pct": round(drawdown_val, 1)})
    except Exception as e: 
        log_func(f"Lỗi tải VNINDEX: {e}")

    if status_box: status_box.update(label="📋 Lấy danh sách VN30...", state="running")
    if progress_bar: progress_bar.progress(10)
    try:
        ref = Reference()
        ket_qua_ref = ref.index.members(symbol="VN30")
        cot_symbol = next((c for c in ["symbol", "ticker", "stock_code", "code"] if c in ket_qua_ref.columns), ket_qua_ref.columns[0]) if isinstance(ket_qua_ref, pd.DataFrame) else None
        danh_sach_ma = ket_qua_ref[cot_symbol].tolist() if cot_symbol else list(ket_qua_ref)
    except Exception: return results

    tong_ma, fa, list_pe, list_pb, list_roe = len(danh_sach_ma), Fundamental(), [], [], []
    bank_tickers = {"VCB", "BID", "CTG", "TCB", "MBB", "ACB", "VPB", "HDB", "STB"}
    bank_above_ma50_count, bank_total_detected = 0, 0

    if status_box: status_box.update(label=f"🔄 Phân tích {tong_ma} mã VN30 (Chờ 10s/mã)...", state="running")
    for idx, ma in enumerate(danh_sach_ma, start=1):
        if progress_bar: progress_bar.progress(int(10 + (idx / tong_ma) * 85))
        if status_box: status_box.write(f"⏳ [{idx}/{tong_ma}] Phân tích mã **{ma}**...")
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

                    df_q = Quote(symbol=ma, source="VCI").history(start=(date.today()-timedelta(weeks=12)).isoformat(), end=date.today().isoformat(), interval="1D")
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
                            if gia_dong >= ma50: bank_above_ma50_count += 1
        except Exception: pass
        time.sleep(10.0)

    if list_pe: results["pe_current"] = round(float(np.median(list_pe)), 2)
    if list_pb: results["pb_current"] = round(float(np.median(list_pb)), 2)
    if list_roe: results["roe_current"] = round(float(np.median(list_roe)), 2)
    results["bank_breadth"] = round(float((bank_above_ma50_count / bank_total_detected) * 100.0), 1) if bank_total_detected > 0 else 50.0

    if progress_bar: progress_bar.progress(100)
    if status_box: status_box.update(label="✅ Hoàn tất lấy dữ liệu từ vnstock!", state="complete", expanded=False)
    return results

def fetch_missing_params_via_gemini(api_key: str, log_func=print) -> dict:
    MISSING_PARAMS_KEYS = ["rf", "us10y", "us_cpi", "eps_growth_exp"]
    try:
        from google import genai
        from google.genai import types
    except ImportError: 
        log_func("⚠️ Chưa cài đặt google-genai")
        return {"error": "Chưa cài đặt google-genai"}
    client = genai.Client(api_key=api_key.strip())
    prompt = "Trích xuất JSON 4 chỉ số (float): rf, us10y, us_cpi, eps_growth_exp mới nhất."
    for model_name in ["gemini-3.1-pro-preview", "gemini-2.5-flash"]:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt, config=types.GenerateContentConfig(temperature=0.1, tools=[{"google_search": {}}]))
            match = re.search(r"\{[\s\S]*?\}", response.text or "")
            if match:
                data = json.loads(match.group(0))
                return {k: float(data[k]) for k in MISSING_PARAMS_KEYS if k in data}
        except Exception: continue
    return {"error": "Lỗi API"}

# ---------------------------------------------------------------------------
# TIẾN TRÌNH BACKGROUND ĐỘC LẬP
# ---------------------------------------------------------------------------
def automated_job():
    print("[TELEGRAM BOT] Bắt đầu kích hoạt tiến trình báo cáo VAM tự động...")
    logging.info("Bắt đầu kích hoạt tiến trình báo cáo VAM tự động...")
    token = GLOBAL_CONFIG.get("telegram_token")
    chat_id = GLOBAL_CONFIG.get("telegram_chat_id")
    api_key = GLOBAL_CONFIG.get("gemini_api_key")
    notify_mode = GLOBAL_CONFIG.get("telegram_notify_mode", "Luôn gửi")
    
    if not token or not chat_id:
        print("[TELEGRAM BOT] ⚠️ Thiếu Token hoặc Chat ID, hủy tác vụ.")
        return

    class DummyBox:
        def update(self, *args, **kwargs): pass
        def write(self, *args, **kwargs): pass
    class DummyBar:
        def progress(self, *args, **kwargs): pass
        
    print("[TELEGRAM BOT] Đang lấy dữ liệu từ vnstock (mất khoảng 5 phút)...")
    vn_data = fetch_vnstock_market_data(DummyBar(), DummyBox(), log_func=logging.warning)
    job_config = GLOBAL_CONFIG.copy()
    job_config.update(vn_data)
    
    if api_key:
        print("[TELEGRAM BOT] Đang lấy dữ liệu vĩ mô từ Gemini...")
        missing_data = fetch_missing_params_via_gemini(api_key, log_func=logging.warning)
        if missing_data and isinstance(missing_data, dict) and "error" not in missing_data:
            for k, v in missing_data.items():
                if v is not None: job_config[k] = v

    saa_equity = max(0.0, float(100 - job_config.get("age", 40)))
    saa_bond = max(0.0, 100.0 - saa_equity - 10.0)
    inputs = VAMInputs(
        age=int(job_config.get("age", 40)), saa_equity=saa_equity, saa_gold=10.0, saa_bond=saa_bond,
        pe_current=float(job_config.get("pe_current", 14.2)), pe_min=float(job_config.get("pe_min", 10.0)), pe_max=float(job_config.get("pe_max", 20.0)),
        pb_current=float(job_config.get("pb_current", 1.72)), pb_min=float(job_config.get("pb_min", 1.35)), pb_max=float(job_config.get("pb_max", 2.6)),
        rf=float(job_config.get("rf", 2.75)), erp_min=float(job_config.get("erp_min", 1.5)), erp_max=float(job_config.get("erp_max", 7.0)),
        dy_current=float(job_config.get("dy_current", 1.7)), dy_min=float(job_config.get("dy_min", 1.2)), dy_max=float(job_config.get("dy_max", 3.2)),
        w_pe=float(job_config.get("w_pe", 30.0)), w_pb=float(job_config.get("w_pb", 20.0)), w_erp=float(job_config.get("w_erp", 35.0)), w_dy=float(job_config.get("w_dy", 15.0)),
        roe_current=float(job_config.get("roe_current", 13.5)), roe_benchmark=float(job_config.get("roe_benchmark", 12.0)),
        eps_growth_exp=float(job_config.get("eps_growth_exp", 10.0)), eps_growth_benchmark=float(job_config.get("eps_growth_benchmark", 8.0)),
        price_current=float(job_config.get("price_current", 1250.5)), ma200=float(job_config.get("ma200", 1265.2)),
        volatility_current=float(job_config.get("volatility_current", 16.2)), volatility_avg=float(job_config.get("volatility_avg", 17.5)),
        drawdown_pct=float(job_config.get("drawdown_pct", 4.2)), us10y=float(job_config.get("us10y", 4.25)), us_cpi=float(job_config.get("us_cpi", 2.9)),
        method=job_config.get("method", "step"),
    )
    result = compute(inputs)
    
    raw_w = {"equity": result.equity_weight, "bond": result.bond_weight, "gold": result.gold_weight}
    curr_w = {"equity": float(job_config.get("curr_w_equity", 60.0)), "bond": float(job_config.get("curr_w_bond", 25.0)), "gold": float(job_config.get("curr_w_gold", 15.0))}
    curr_sum = sum(curr_w.values())
    if curr_sum > 0: curr_w = {k: (v / curr_sum) * 100.0 for k, v in curr_w.items()}

    frict_res = calculate_execution_friction(raw_weights=raw_w, curr_weights=curr_w, max_turnover_pct=float(job_config.get("max_turnover", 25.0)), fee_rate_pct=float(job_config.get("fee_rate", 0.15)), portfolio_nav_mil=float(job_config.get("portfolio_nav", 1000.0)))

    ma20_curr = float(job_config.get("ma20", inputs.ma200))
    vol_r = float(job_config.get("vol_ratio", 1.0))
    bank_b = float(job_config.get("bank_breadth", 50.0))
    clock_data = calculate_hmm_market_clock(inputs, ma20_curr, vol_r, bank_b)
    
    save_stock_database(inputs, result, frict_res, clock_data, curr_w, note="Automated Job Background")

    if notify_mode == "Chỉ gửi khi vượt ngưỡng thực thi" and not frict_res["action_needed"]:
        print("[TELEGRAM BOT] Bỏ qua gửi báo cáo do tỷ trọng chưa vượt ngưỡng thực thi.")
        return

    exec_w = frict_res["exec_weights"]
    rec = getattr(result, "recommendation", {})

    msg = f"📊 <b>BÁO CÁO VAM TỰ ĐỘNG</b>\n\n"
    msg += f"🕰️ <b>ĐỒNG HỒ CHU KỲ & THANH KHOẢN</b>\n"
    msg += f"- <b>Pha thị trường:</b> {clock_data['phase']} ({clock_data['time_str']})\n"
    msg += f"- <b>Thanh khoản VN-FCI:</b> {clock_data['fci_state']} (Score: {clock_data['fci_score']:+.2f})\n"
    msg += f"- <b>Trạng thái:</b> {clock_data['desc']}\n"
    msg += f"- <b>Chiến lược:</b> {clock_data['bias']}\n\n"
    
    msg += f"🔹 <b>ĐỊNH GIÁ & TỶ TRỌNG LÝ THUYẾT (VAM)</b>\n"
    msg += f"- <b>VAM Score:</b> {result.valuation_score:.2f}\n"
    msg += f"- <b>Tỷ trọng lý thuyết:</b> CP {result.equity_weight:.1f}% | TP {result.bond_weight:.1f}% | Vàng {result.gold_weight:.1f}%\n"
    msg += f"- <b>Rút vốn/năm:</b> {result.withdrawal_rate:.1f}%\n\n"
    
    msg += f"⚖️ <b>THỰC THI ĐẢO DANH MỤC (CAPPED)</b>\n"
    msg += f"- <b>Tỷ trọng hiện tại:</b> CP {curr_w['equity']:.1f}% | TP {curr_w['bond']:.1f}% | Vàng {curr_w['gold']:.1f}%\n"
    msg += f"- <b>Tỷ trọng khuyến nghị:</b> CP {exec_w['equity']:.1f}% | TP {exec_w['bond']:.1f}% | Vàng {exec_w['gold']:.1f}%\n"
    msg += f"- <b>Đảo danh mục:</b> {frict_res['actual_turnover']:.1f}% (Lý thuyết: {frict_res['raw_turnover']:.1f}%)\n"
    msg += f"- <b>Giá trị luân chuyển:</b> {frict_res['money_turnover']:.1f} Tr VND\n"
    msg += f"- <b>Chi phí ma sát:</b> {frict_res['est_fee_cost']*1e6:,.0f} VND\n"
    msg += f"- <b>Lệnh:</b> {'🔴 THỰC THI' if frict_res['action_needed'] else '🟢 NẮM GIỮ (HOLD)'} - {frict_res['status']}\n\n"
    
    msg += f"💡 <b>KHUYẾN NGHỊ CHI TIẾT</b>\n"
    msg += f"- <b>Quy tắc:</b> {getattr(result, 'rule_text', '')}\n"
    msg += f"- <b>Hành động:</b> {rec.get('action', '')} - {rec.get('headline', '')}\n"
    msg += f"- <b>Chi tiết:</b> {rec.get('detail', '')}\n\n"
    
    msg += f"📋 <b>DỮ LIỆU ĐẦU VÀO</b>\n"
    msg += f"- <b>Kỹ thuật:</b> VN-Index {inputs.price_current:.1f} | MA20: {ma20_curr:.1f} | MA200: {inputs.ma200:.1f}\n"
    msg += f"- <b>Định giá:</b> P/E {inputs.pe_current:.2f} | P/B {inputs.pb_current:.2f}\n"
    msg += f"- <b>Động lượng:</b> Vol Ratio {vol_r:.2f}x | Ngân hàng > MA50: {bank_b:.1f}%\n"
    msg += f"- <b>Vĩ mô:</b> Rf {inputs.rf:.2f}% | US10Y {inputs.us10y:.2f}% | US CPI {inputs.us_cpi:.2f}%\n\n"
    msg += f"💾 <i>Dữ liệu cập nhật & lưu trữ tự động vào stock_database.csv.</i>"
    
    print("[TELEGRAM BOT] Đang gửi báo cáo qua Telegram...")
    msg_id = send_telegram_msg(token, chat_id, msg)
    if msg_id: 
        print("[TELEGRAM BOT] ✅ Gửi báo cáo cho Admin thành công!")
        channel_id = str(GLOBAL_CONFIG.get("telegram_channel_id", "")).strip()
        if channel_id:
            chan_id = channel_id if (channel_id.startswith("-") or channel_id.startswith("@")) else "@" + channel_id
            print(f"[TELEGRAM BOT] Đang forward báo cáo lên Channel {chan_id}...")
            if forward_telegram_msg(token, chan_id, chat_id, msg_id):
                print(f"[TELEGRAM BOT] ✅ Forward báo cáo lên Channel thành công!")
            else:
                print(f"[TELEGRAM BOT] ❌ Forward báo cáo lên Channel thất bại!")
    else: 
        print("[TELEGRAM BOT] ❌ Gửi báo cáo cho Admin thất bại!")

def init_stock_scheduler_from_vault():
    vault = load_secure_vault()
    mode = vault.get("schedule_mode", get_secret("SCHEDULE_MODE", "Không"))
    run_time = vault.get("schedule_time", get_secret("SCHEDULE_TIME", "08:00"))
    stock_scheduler.clear()
    if mode == "Hàng ngày":
        stock_scheduler.every().day.at(run_time).do(automated_job)
    elif mode == "Hàng tuần":
        stock_scheduler.every().monday.at(run_time).do(automated_job)
    elif mode == "Hàng tháng":
        stock_scheduler.every(30).days.at(run_time).do(automated_job)
    if mode != "Không":
        logging.info(f"[STOCK BOT] Đã nạp lịch trình xác nhận từ Vault: {mode} lúc {run_time}")

def run_schedule_and_polling():
    last_update_id = 0
    print("[TELEGRAM BOT] Đợi hệ thống khởi tạo cấu hình...")
    time.sleep(3)
    
    token = GLOBAL_CONFIG.get("telegram_token")
    if token:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/deleteWebhook", timeout=5)
        except Exception:
            pass
            
    print("[TELEGRAM BOT] 2. Bắt đầu tiến trình Lập lịch & Lắng nghe tin nhắn ngầm...\n")

    while True:
        try:
            stock_scheduler.run_pending()
        except Exception as e:
            logging.error(f"[STOCK BOT] Lỗi Schedule: {e}")
            
        token = GLOBAL_CONFIG.get("telegram_token")
        admin_id = str(GLOBAL_CONFIG.get("telegram_chat_id", "")).strip()
        channel_id = str(GLOBAL_CONFIG.get("telegram_channel_id", "")).strip()
        
        if channel_id and not channel_id.startswith("-") and not channel_id.startswith("@"):
            channel_id = "@" + channel_id
            
        if token and admin_id and channel_id:
            try:
                url = f"https://api.telegram.org/bot{token}/getUpdates"
                params = {"offset": last_update_id + 1, "timeout": 5}
                resp = requests.get(url, params=params, timeout=10)
                data = resp.json()
                
                if data.get("ok"):
                    for res in data.get("result", []):
                        last_update_id = res["update_id"]
                        msg = res.get("message")
                        
                        if msg:
                            chat_id = str(msg.get("chat", {}).get("id", ""))
                            message_id = msg.get("message_id")
                            text_preview = msg.get("text", "[Đính kèm/Hình ảnh]")[:30]
                            
                            print(f"[TELEGRAM BOT] [Nhận] Tin nhắn mới từ ID {chat_id}: {text_preview}...")
                            
                            if chat_id == admin_id and message_id:
                                success = forward_telegram_msg(token, channel_id, chat_id, message_id)
                                if success:
                                    print("   ✅ Forward thành công!")
                                else:
                                    print("   ❌ Lỗi forward: Kiểm tra quyền Channel.")
            except Exception:
                pass
        time.sleep(2)

@st.cache_resource
def start_background_bot():
    init_stock_scheduler_from_vault()
    thread = threading.Thread(target=run_schedule_and_polling, daemon=True)
    add_script_run_ctx(thread)
    thread.start()
    return thread

# ---------------------------------------------------------------------------
# GIAO DIỆN UI CHÍNH
# ---------------------------------------------------------------------------
def render():
    st.markdown(
        """
        <style>
        [data-testid="stMetricValue"] { font-size: clamp(1.1rem, 1.8vw, 1.8rem) !important; white-space: normal !important; }
        .prob-card { background-color: #1E293B; border-radius: 6px; padding: 8px 12px; flex: 1 1 calc(25% - 10px); min-width: 110px; margin-bottom: 6px; border: 1px solid #334155; text-align: center; }
        .prob-title { font-size: 0.78rem; color: #94A3B8; font-weight: 600; white-space: nowrap; }
        .prob-value { font-size: 1.15rem; font-weight: 700; margin-top: 2px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    SHEETS_ON = sheets_configured()

    vault_data = load_secure_vault()
    if "vault_loaded" not in st.session_state:
        st.session_state.update(vault_data)
        st.session_state.vault_loaded = True

    global DEFAULTS
    DEFAULTS = {
        "age": 40, "portfolio_nav": 1000.0,
        "curr_w_equity": 60.0, "curr_w_bond": 25.0, "curr_w_gold": 15.0,
        "max_turnover": 25.0, "fee_rate": 0.15,
        "pe_current": 14.2, "pe_min": 10.0, "pe_max": 20.0,
        "pb_current": 1.72, "pb_min": 1.35, "pb_max": 2.60,
        "rf": 2.75, "erp_min": 1.5, "erp_max": 7.0,
        "dy_current": 1.70, "dy_min": 1.20, "dy_max": 3.20,
        "w_pe": 30.0, "w_pb": 20.0, "w_erp": 35.0, "w_dy": 15.0,
        "roe_current": 13.5, "roe_benchmark": 12.0,
        "eps_growth_exp": 10.0, "eps_growth_benchmark": 8.0,
        "price_current": 1250.5, "ma20": 1255.0, "ma200": 1265.2,
        "volatility_current": 16.2, "volatility_avg": 17.5,
        "drawdown_pct": 4.2, "us10y": 4.25, "us_cpi": 2.90,
        "vol_ratio": 1.05, "bank_breadth": 65.0,
        "method": "step", "investment_notes": "",
        "gemini_api_key": get_secret("GEMINI_API_KEY", ""),
        "telegram_token": get_secret("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": get_secret("TELEGRAM_CHAT_ID", ""),
        "telegram_channel_id": get_secret("TELEGRAM_CHANNEL_ID", ""),
        "schedule_mode": vault_data.get("schedule_mode", "Không"),
        "schedule_time": vault_data.get("schedule_time", "08:00"),
        "telegram_notify_mode": "Luôn gửi",
    }

    for key, val in DEFAULTS.items():
        if key not in st.session_state: st.session_state[key] = val
        if key in SENSITIVE_KEYS and get_secret(key.upper()): st.session_state[key] = get_secret(key.upper())

    if "log" not in st.session_state: st.session_state.log = []

    global GLOBAL_CONFIG
    GLOBAL_CONFIG.clear()
    GLOBAL_CONFIG.update(st.session_state)

    start_background_bot()

    # ---------------------------------------------------------------------------
    # UTILS GIAO DIỆN
    # ---------------------------------------------------------------------------
    def handle_json_import():
        uploaded = st.session_state.get("json_file_uploader")
        if uploaded is not None:
            try:
                imported_data = json.load(uploaded)
                for k, v in imported_data.items():
                    if k in DEFAULTS and k not in SENSITIVE_KEYS:
                        if k == "age": st.session_state[k] = int(v)
                        elif isinstance(v, (int, float)): st.session_state[k] = float(v)
                        else: st.session_state[k] = v
                st.toast("✅ Đã cập nhật tham số (Bỏ qua key nhạy cảm)!", icon="📥")
            except Exception as e: st.error(f"❌ Lỗi JSON: {e}")

    def handle_gemini_json_import():
        uploaded = st.session_state.get("gemini_json_uploader")
        if uploaded is not None:
            try:
                data = json.load(uploaded)
                api_key = data.get("api_key") or data.get("GEMINI_API_KEY") or next(iter(data.values()), "")
                if api_key and isinstance(api_key, str):
                    st.session_state["gemini_api_key"] = api_key.strip()
                    st.toast("✅ Đã nạp Gemini API Key qua file JSON!", icon="🔑")
            except Exception as e: st.error(f"❌ Lỗi nạp Key: {e}")

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
            fig.add_trace(go.Barpolar(r=[0.80], theta=[q["theta_mid"]], width=[90], name=q["name"], marker_color=q["color"], showlegend=False, hoverinfo="none"))
        fig.add_trace(go.Scatterpolar(r=[0, 0.72], theta=[needle_angle, needle_angle], mode="lines+markers", line=dict(color="#EF4444", width=3.5), marker=dict(size=[6, 12], color=["#1E293B", "#EF4444"]), name=f"Vị thế HMM: {clock_val:.1f} Giờ", hoverinfo="name"))
        fig.update_layout(autosize=True, polar=dict(radialaxis=dict(visible=False, range=[0, 1.0]), angularaxis=dict(direction="clockwise", period=360, rotation=90, tickmode="array", tickvals=[0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330], ticktext=["<b>12h<br>(ĐỈNH)</b>", "1h", "2h", "<b>3h<br>(Rơi)</b>", "4h", "5h", "<b>6h<br>(ĐÁY)</b>", "7h", "8h", "<b>9h<br>(Tăng)</b>", "10h", "11h"], tickfont=dict(size=11, color="#94A3B8"))), margin=dict(l=55, r=55, t=45, b=45), height=370, paper_bgcolor="rgba(0,0,0,0)", showlegend=False)
        return fig
        
    def draw_plotly_pie_chart(weights, labels, colors, center_text="TARGET"):
        fig = go.Figure(data=[go.Pie(labels=labels, values=weights, hole=0.48, marker=dict(colors=colors, line=dict(color="#FFFFFF", width=2.5)), textinfo="label+percent", hoverinfo="label+value+percent", textfont=dict(size=12), insidetextorientation="horizontal")])
        fig.update_layout(showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=-0.18, xanchor="center", x=0.5, font=dict(size=11)), margin=dict(l=20, r=20, t=20, b=20), height=370, paper_bgcolor="rgba(0,0,0,0)", annotations=[dict(text=f"<b>{center_text}</b>", x=0.5, y=0.5, font_size=11, showarrow=False)])
        return fig

    # ---------------------------------------------------------------------------
    # GIAO DIỆN CẤU HÌNH SIDEBAR
    # ---------------------------------------------------------------------------
    st.sidebar.title("⚙️ Thông số đầu vào")
    
    server_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    st.sidebar.info(f"🕒 Giờ Server hiện tại: **{server_time}**")

    with st.sidebar.expander("📁 Quản lý File Config & Bảo mật", expanded=False):
        config_data = {k: st.session_state[k] for k in DEFAULTS.keys() if k not in SENSITIVE_KEYS}
        st.download_button("💾 Tải File Config (JSON An toàn)", file_name="vam_input_config.json", mime="application/json", data=json.dumps(config_data, indent=2, ensure_ascii=False), use_container_width=True)
        st.file_uploader("📂 Import Config (JSON)", type=["json"], key="json_file_uploader", on_change=handle_json_import)
        
        st.markdown("---")
        if CRYPTO_ENABLED:
            if st.button("🔒 Mã hóa & Lưu API/Token cục bộ", help="Lưu Telegram & Gemini API bằng AES-128"):
                secure_data = {k: st.session_state.get(k) for k in SENSITIVE_KEYS}
                if save_secure_vault(secure_data): 
                    st.toast("✅ Đã lưu Vault mã hóa thành công!", icon="🔒")
                    GLOBAL_CONFIG.update(secure_data)
                else: st.error("❌ Lỗi mã hóa lưu trữ!")

    with st.sidebar.expander("🤖 Gemini AI (Vĩ mô & Lãi suất)", expanded=True):
        has_gemini = bool(st.session_state.get("gemini_api_key"))
        new_gemini = st.text_input("Gemini API Key", value="", placeholder="•••••••••••••••• (Đã thiết lập)" if has_gemini else "Nhập API Key mới", type="password", help="Chỉ cho phép ghi đè, không hiển thị key cũ để bảo mật.")
        if new_gemini:
            st.session_state.gemini_api_key = new_gemini

        st.file_uploader("📂 Hoặc nạp từ file JSON API Key", type=["json"], key="gemini_json_uploader", on_change=handle_gemini_json_import)
        
        if st.button("🌐 Gemini Auto-Fill Tham số", use_container_width=True):
            api_key = st.session_state.get("gemini_api_key", "").strip()
            if not api_key: st.sidebar.warning("⚠️ Vui lòng thiết lập API Key!")
            else:
                try:
                    with st.spinner("🤖 Đang truy xuất..."):
                        missing_data = fetch_missing_params_via_gemini(api_key, log_func=st.error)
                        if missing_data and "error" not in missing_data:
                            for key in ["rf", "us10y", "us_cpi", "eps_growth_exp"]:
                                if key in missing_data and missing_data[key] is not None: st.session_state[key] = float(missing_data[key])
                            st.sidebar.success("✅ Nạp thành công tham số vĩ mô!")
                            st.rerun()
                        else: st.sidebar.error("❌ Gemini thất bại.")
                except Exception as exc: st.sidebar.error(f"❌ Lỗi: {exc}")

    with st.sidebar.expander("📲 Telegram & Tự động hóa", expanded=False):
        has_tele_token = bool(st.session_state.get("telegram_token"))
        new_tele_token = st.text_input("Telegram Bot Token", value="", placeholder="•••••••• (Đã thiết lập)" if has_tele_token else "Nhập Bot Token mới", type="password", help="Chỉ cho phép ghi đè, không hiển thị token cũ.")
        if new_tele_token:
            st.session_state.telegram_token = new_tele_token

        has_tele_chat = bool(st.session_state.get("telegram_chat_id"))
        new_tele_chat = st.text_input("Telegram Admin Chat ID", value="", placeholder="•••••••• (Đã thiết lập)" if has_tele_chat else "Nhập Chat ID của bạn", type="password", help="Chỉ cho phép ghi đè, không hiển thị ID cũ.")
        if new_tele_chat:
            st.session_state.telegram_chat_id = new_tele_chat

        has_tele_chan = bool(st.session_state.get("telegram_channel_id"))
        new_tele_chan = st.text_input("Public Channel ID (VD: @vam_channel)", value="", placeholder="•••••••• (Đã thiết lập)" if has_tele_chan else "Nhập Channel Username/ID", type="password", help="Chỉ cho phép ghi đè, không hiển thị ID cũ.")
        if new_tele_chan:
            st.session_state.telegram_channel_id = new_tele_chan
        
        schedule_modes = ["Không", "Hàng ngày", "Hàng tuần", "Hàng tháng"]
        st.session_state.schedule_mode = st.selectbox("Chu kỳ tự động", schedule_modes, index=schedule_modes.index(st.session_state.get("schedule_mode", "Không")))
        st.session_state.schedule_time = st.text_input("Thời gian chạy (HH:MM)", value=st.session_state.get("schedule_time", "08:00"))
        
        notify_modes = ["Luôn gửi", "Chỉ gửi khi vượt ngưỡng thực thi"]
        st.session_state.telegram_notify_mode = st.selectbox("Điều kiện gửi báo cáo", notify_modes, index=notify_modes.index(st.session_state.get("telegram_notify_mode", "Luôn gửi")))

        # NÚT BẤM XÁC NHẬN KÍCH HOẠT LỊCH GỬI ĐỊNH KỲ
        if st.button("🔔 Xác nhận kích hoạt lịch gửi", use_container_width=True):
            stock_scheduler.clear()
            mode = st.session_state.schedule_mode
            run_time = st.session_state.schedule_time
            
            if mode == "Hàng ngày": stock_scheduler.every().day.at(run_time).do(automated_job)
            elif mode == "Hàng tuần": stock_scheduler.every().monday.at(run_time).do(automated_job)
            elif mode == "Hàng tháng": stock_scheduler.every(30).days.at(run_time).do(automated_job)
                
            logging.info(f"[STOCK BOT] Đã xác nhận cài đặt lịch trình mới: {mode} lúc {run_time}")
            
            if CRYPTO_ENABLED:
                current_vault = load_secure_vault()
                current_vault["schedule_mode"] = mode
                current_vault["schedule_time"] = run_time
                save_secure_vault(current_vault)
            st.toast("✅ Đã xác nhận và kích hoạt lịch gửi thành công!", icon="⏰")
            st.rerun()

        next_run = getattr(stock_scheduler, "next_run", None)
        if callable(next_run):
            try: next_run = next_run()
            except Exception: next_run = None
            
        if next_run:
            st.caption(f"⏳ Lần chạy tiếp theo: **{next_run.strftime('%Y-%m-%d %H:%M:%S')}** (Theo giờ Server)")
        else:
            st.caption("⏸️ Lịch chạy tự động đang tắt hoặc chưa xác nhận.")

        c_tele1, c_tele2 = st.columns(2)
        with c_tele1:
            if st.button("Kiểm tra Bot Telegram", use_container_width=True):
                test_token = st.session_state.get("telegram_token")
                test_chat = st.session_state.get("telegram_chat_id")
                test_chan = st.session_state.get("telegram_channel_id")
                
                if not test_token or not test_chat:
                    st.error("⚠️ Vui lòng nhập Token và Admin Chat ID trước!")
                else:
                    msg_id = send_telegram_msg(test_token, test_chat, "✅ Hệ thống VAM Bot kết nối thành công! Đang test tính năng Forward...")
                    if msg_id:
                        st.toast("Đã gửi tin nhắn cho Admin!", icon="📨")
                        if test_chan:
                            chan_id = test_chan if (test_chan.startswith("-") or test_chan.startswith("@")) else "@" + test_chan
                            if forward_telegram_msg(test_token, chan_id, test_chat, msg_id):
                                st.success(f"✅ Gửi Admin và Forward lên Channel {chan_id} thành công!")
                                st.toast("Forward thành công!", icon="🚀")
                            else:
                                st.error(f"❌ Gửi Admin thành công nhưng Forward lên {chan_id} thất bại. Kiểm tra quyền Channel!")
                        else:
                            st.success("✅ Gửi Admin thành công! (Chưa cấu hình Channel)")
                    else:
                        st.error("❌ Gửi thất bại, kiểm tra lại Token hoặc Chat ID!")

        with c_tele2:
            if st.button("Gửi báo cáo ngay", use_container_width=True):
                with st.spinner("Đang chạy phân tích & gửi báo cáo..."):
                    automated_job()
                    st.toast("Đã gửi báo cáo VAM ngay!", icon="🚀")

    with st.sidebar.expander("🚀 vnstock (Chứng khoán & Thanh khoản)", expanded=True):
        if st.button("🚀 Lấy dữ liệu từ vnstock", use_container_width=True):
            p_bar = st.sidebar.progress(0)
            s_box = st.sidebar.status("Đang kết nối vnstock...", expanded=True)
            vn_data = fetch_vnstock_market_data(p_bar, s_box, log_func=st.warning)
            if vn_data:
                for k, v in vn_data.items(): st.session_state[k] = v
                st.sidebar.success("✅ Cập nhật vnstock thành công!")
                st.rerun()

    st.sidebar.markdown("---")
    with st.sidebar.expander("👤 Thông tin nhà đầu tư", expanded=True):
        st.number_input("Tuổi của bạn", 18, 100, key="age")
        st.number_input("Tỷ trọng CP Hiện tại (%)", 0.0, 100.0, step=1.0, key="curr_w_equity")
        st.number_input("Tỷ trọng TP Hiện tại (%)", 0.0, 100.0, step=1.0, key="curr_w_bond")
        st.number_input("Tỷ trọng Vàng Hiện tại (%)", 0.0, 100.0, step=1.0, key="curr_w_gold")
        st.number_input("Quy mô Danh mục (Triệu)", 1.0, 1e7, step=10.0, key="portfolio_nav")

    with st.sidebar.expander("⚖️ Kiểm soát Ma sát Thực thi", expanded=True):
        st.number_input("Trần Đảo Danh Mục Turnover (%)", 5.0, 100.0, step=5.0, key="max_turnover")
        st.number_input("Thuế & Phí Giao dịch Ước tính (%)", 0.0, 2.0, step=0.05, key="fee_rate")

    with st.sidebar.expander("📈 Định giá & P/E, P/B", expanded=False):
        st.number_input("PE hiện tại", 0.01, 200.0, step=0.1, key="pe_current")
        st.number_input("PE min", 0.01, 200.0, step=0.1, key="pe_min")
        st.number_input("PE max", 0.01, 200.0, step=0.1, key="pe_max")
        st.number_input("PB hiện tại", 0.01, 50.0, step=0.05, key="pb_current")
        st.number_input("PB min", 0.01, 50.0, step=0.05, key="pb_min")
        st.number_input("PB max", 0.01, 50.0, step=0.05, key="pb_max")
        st.number_input("Lãi suất phi rủi ro Rf (%)", 0.0, 30.0, step=0.1, key="rf")
        st.number_input("ERP min (%)", -30.0, 30.0, step=0.1, key="erp_min")
        st.number_input("ERP max (%)", -30.0, 30.0, step=0.1, key="erp_max")
        st.number_input("DY hiện tại (%)", 0.0, 30.0, step=0.1, key="dy_current")
        st.number_input("DY min (%)", 0.0, 30.0, step=0.1, key="dy_min")
        st.number_input("DY max (%)", 0.0, 30.0, step=0.1, key="dy_max")

    with st.sidebar.expander("🌐 Vĩ mô Mỹ", expanded=False):
        st.number_input("US10Y (%)", 0.0, 20.0, step=0.05, key="us10y")
        st.number_input("Lạm phát CPI Mỹ (%)", -5.0, 30.0, step=0.1, key="us_cpi")

    with st.sidebar.expander("🛡️ Chất lượng & Tăng trưởng", expanded=False):
        st.number_input("ROE hiện tại (%)", -50.0, 100.0, step=0.5, key="roe_current")
        st.number_input("ROE chuẩn (%)", 0.0, 100.0, step=0.5, key="roe_benchmark")
        st.number_input("Tăng trưởng EPS dự phóng (%)", -100.0, 200.0, step=0.5, key="eps_growth_exp")
        st.number_input("Tăng trưởng EPS chuẩn (%)", -50.0, 100.0, step=0.5, key="eps_growth_benchmark")

    with st.sidebar.expander("📉 Kỹ thuật & Biến động", expanded=False):
        st.number_input("VN-Index Giá", 0.0, 1e7, step=1.0, key="price_current")
        st.number_input("VN-Index MA20", 0.0, 1e7, step=1.0, key="ma20")
        st.number_input("VN-Index MA200", 0.0, 1e7, step=1.0, key="ma200")
        st.number_input("Volatility thực tế 1Y (%)", 0.0, 200.0, step=0.5, key="volatility_current")
        st.number_input("Volatility TB 2Y (%)", 0.0, 200.0, step=0.5, key="volatility_avg")
        st.number_input("Drawdown (%)", 0.0, 100.0, step=0.5, key="drawdown_pct")

    with st.sidebar.expander("💧 Thanh khoản Nội địa", expanded=False):
        st.number_input("Động lượng Volume MA20/MA200", 0.0, 10.0, step=0.05, key="vol_ratio")
        st.number_input("Độ rộng Nhóm Ngân hàng (%)", 0.0, 100.0, step=1.0, key="bank_breadth")

    st.sidebar.markdown("---")
    st.session_state.investment_notes = st.sidebar.text_area("📝 Ghi chú đầu tư", value=st.session_state.get("investment_notes", ""))
    calc_clicked = st.sidebar.button("🚀 Tính toán phân bổ", use_container_width=True, type="primary")

    # ---------------------------------------------------------------------------
    # TÍNH TOÁN (UI Chính)
    # ---------------------------------------------------------------------------
    st.title("📊 VAM Multi-Asset Allocator & HMM Market Clock")

    if calc_clicked:
        saa_equity = max(0.0, float(100 - st.session_state.age))
        saa_bond = max(0.0, 100.0 - saa_equity - 10.0)
        inputs = VAMInputs(
            age=int(st.session_state.age), saa_equity=saa_equity, saa_gold=10.0, saa_bond=saa_bond,
            pe_current=st.session_state.pe_current, pe_min=st.session_state.pe_min, pe_max=st.session_state.pe_max,
            pb_current=st.session_state.pb_current, pb_min=st.session_state.pb_min, pb_max=st.session_state.pb_max,
            rf=st.session_state.rf, erp_min=st.session_state.erp_min, erp_max=st.session_state.erp_max,
            dy_current=st.session_state.dy_current, dy_min=st.session_state.dy_min, dy_max=st.session_state.dy_max,
            w_pe=st.session_state.w_pe, w_pb=st.session_state.w_pb, w_erp=st.session_state.w_erp, w_dy=st.session_state.w_dy,
            roe_current=st.session_state.roe_current, roe_benchmark=st.session_state.roe_benchmark,
            eps_growth_exp=st.session_state.eps_growth_exp, eps_growth_benchmark=st.session_state.eps_growth_benchmark,
            price_current=st.session_state.price_current, ma200=st.session_state.ma200,
            volatility_current=st.session_state.volatility_current, volatility_avg=st.session_state.volatility_avg,
            drawdown_pct=st.session_state.drawdown_pct, us10y=st.session_state.us10y, us_cpi=st.session_state.us_cpi,
            method=st.session_state.method,
        )
        result = compute(inputs)
        st.session_state.last_result = result
        st.session_state.last_inputs = inputs

        curr_w_temp = {"equity": st.session_state.curr_w_equity, "bond": st.session_state.curr_w_bond, "gold": st.session_state.curr_w_gold}
        curr_sum = sum(curr_w_temp.values())
        if curr_sum > 0: curr_w_temp = {k: (v / curr_sum) * 100.0 for k, v in curr_w_temp.items()}

        frict_res_temp = calculate_execution_friction({"equity": result.equity_weight, "bond": result.bond_weight, "gold": result.gold_weight}, curr_w_temp, st.session_state.max_turnover, st.session_state.fee_rate, st.session_state.portfolio_nav)
        clock_data_temp = calculate_hmm_market_clock(inputs, float(st.session_state.get("ma20", inputs.ma200)), float(st.session_state.get("vol_ratio", 1.0)), float(st.session_state.get("bank_breadth", 50.0)))

        if save_stock_database(inputs, result, frict_res_temp, clock_data_temp, curr_w_temp, note=st.session_state.investment_notes):
            st.toast("✅ Đã cập nhật & đồng bộ dữ liệu vào stock_database.csv!", icon="💾")

        if SHEETS_ON:
            try: append_log_row(make_log_row(inputs, result, st.session_state.investment_notes)); st.toast("✅ Đã lưu Google Sheets!")
            except Exception as exc: st.warning(f"⚠️ Chưa ghi log Sheets: {exc}")
        else:
            st.session_state.log.append({"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "note": st.session_state.investment_notes, "valuation_score": result.valuation_score, "equity_weight": result.equity_weight, "bond_weight": result.bond_weight, "gold_weight": result.gold_weight})

    result = st.session_state.get("last_result")
    inputs = st.session_state.get("last_inputs")

    if not result:
        st.info("👈 Bấm 'Tính toán phân bổ' ở thanh bên trái.")
        return

    ma20_curr = float(st.session_state.get("ma20", inputs.ma200))
    vol_r = float(st.session_state.get("vol_ratio", 1.0))
    bank_b = float(st.session_state.get("bank_breadth", 50.0))
    clock_data = calculate_hmm_market_clock(inputs, ma20_curr, vol_r, bank_b)
    
    st.markdown(
        f"""
        <div style="background-color: #0F172A; border-left: 6px solid #38BDF8; padding: 16px 20px; border-radius: 8px; margin-bottom: 20px;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                <div>
                    <div style="font-size: 0.85rem; text-transform: uppercase; color: #94A3B8; font-weight: 600;">🕰️ ĐỒNG HỒ CHU KỲ HMM & THANH KHOẢN VN-FCI</div>
                    <div style="font-size: 1.45rem; font-weight: 700; color: #F8FAFC; margin-top: 4px;">{clock_data['phase']}</div>
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
            <div style="font-size: 0.95rem; color: #CBD5E1; line-height: 1.5; margin-top: 10px;">📌 <b>Trạng thái:</b> {clock_data['desc']}<br>💡 <b>Hành động:</b> {clock_data['bias']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    curr_w = {"equity": st.session_state.curr_w_equity, "bond": st.session_state.curr_w_bond, "gold": st.session_state.curr_w_gold}
    curr_sum = sum(curr_w.values())
    if curr_sum > 0: curr_w = {k: (v / curr_sum) * 100.0 for k, v in curr_w.items()}

    frict_res = calculate_execution_friction({"equity": result.equity_weight, "bond": result.bond_weight, "gold": result.gold_weight}, curr_w, st.session_state.max_turnover, st.session_state.fee_rate, st.session_state.portfolio_nav)
    exec_w = frict_res["exec_weights"]

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("🕒 Đồng hồ Chu kỳ HMM")
        st.plotly_chart(draw_market_clock_chart(clock_data["hour"]), use_container_width=True)
    with c2:
        st.subheader("📈 Tỷ trọng Thực thi (Turnover Capped)")
        st.plotly_chart(draw_plotly_pie_chart([exec_w["equity"], exec_w["bond"], exec_w["gold"]], ["Cổ phiếu", "Trái phiếu", "Vàng"], ["#2563EB", "#F59E0B", "#10B981"], "EXECUTABLE"), use_container_width=True)

    st.markdown("---")
    st.subheader("⚖️ Bảng Thực thi & Kiểm soát Ma sát Đảo danh mục (Turnover Safeguard)")
    c_t1, c_t2, c_t3, c_t4 = st.columns(4)
    c_t1.metric("Đảo danh mục", f"{frict_res['actual_turnover']:.1f}%", delta=f"Lý thuyết: {frict_res['raw_turnover']:.1f}%", delta_color="inverse")
    c_t2.metric("Giá trị Luân chuyển", f"{frict_res['money_turnover']:.1f} Tr VND")
    c_t3.metric("Chi phí Ma sát Ước tính", f"{frict_res['est_fee_cost']*1e6:,.0f} VND")
    c_t4.metric("Lệnh Khuyến nghị", "Thực thi" if frict_res['action_needed'] else "Nắm giữ (Hold)")

    if frict_res['action_needed']: st.info(f"💡 **Thực thi:** {frict_res['status']}. CP {exec_w['equity']:.1f}% | TP {exec_w['bond']:.1f}% | Vàng {exec_w['gold']:.1f}%.")
    else: st.success(f"✅ **Thực thi:** {frict_res['status']}. Khuyến nghị giữ nguyên trạng thái.")

    st.markdown("---")
    m1, m2, m3, m4, m5 = st.columns([1.2, 1, 1, 1, 1])
    m1.metric("VAM Valuation Score", f"{result.valuation_score:.2f}")
    m2.metric("CP Lý thuyết", f"{result.equity_weight:.1f}%")
    m3.metric("TP Lý thuyết", f"{result.bond_weight:.1f}%")
    m4.metric("Vàng Lý thuyết", f"{result.gold_weight:.1f}%")
    m5.metric("Rút vốn/năm", f"{result.withdrawal_rate:.1f}%")

    rec = getattr(result, "recommendation", {})
    st.info(f"**⚖️ Quy tắc:** {getattr(result, 'rule_text', '')}\n\n**📢 Hành động:** `{rec.get('action', '')}` - **{rec.get('headline', '')}**\n\n**📝 Chi tiết:** {rec.get('detail', '')}")

    tab1, tab2, tab3 = st.tabs([
        "🔍 Bảng So sánh Tỷ trọng & Chi tiết Chỉ số",
        "📜 Lịch sử lưu Google Sheets",
        f"📜 Lịch sử Cơ sở Dữ liệu ({STOCK_DB_FILE})"
    ])

    with tab1:
        st.markdown("**1. Bảng So sánh Tỷ trọng**")
        df_comp = pd.DataFrame([
            {"Tài sản": "Cổ phiếu", "Hiện tại (%)": f"{curr_w['equity']:.1f}%", "Mục tiêu (%)": f"{result.equity_weight:.1f}%", "Thực thi (%)": f"{exec_w['equity']:.1f}%", "Độ lệch": f"{exec_w['equity'] - curr_w['equity']:+.1f}%"},
            {"Tài sản": "Trái phiếu", "Hiện tại (%)": f"{curr_w['bond']:.1f}%", "Mục tiêu (%)": f"{result.bond_weight:.1f}%", "Thực thi (%)": f"{exec_w['bond']:.1f}%", "Độ lệch": f"{exec_w['bond'] - curr_w['bond']:+.1f}%"},
            {"Tài sản": "Vàng", "Hiện tại (%)": f"{curr_w['gold']:.1f}%", "Mục tiêu (%)": f"{result.gold_weight:.1f}%", "Thực thi (%)": f"{exec_w['gold']:.1f}%", "Độ lệch": f"{exec_w['gold'] - curr_w['gold']:+.1f}%"},
        ])
        st.dataframe(df_comp, use_container_width=True, hide_index=True)
        st.markdown("**2. Bảng Đánh giá Các Chỉ số Đầu vào & Thanh khoản**")
        st.dataframe(pd.DataFrame([
            {"Chỉ số": "Vị thế Giờ HMM", "Giá trị": f"{clock_data['time_str']}", "Pha": clock_data['phase'], "Nhận xét": clock_data['desc']},
            {"Chỉ số": "Thanh khoản VN-FCI", "Giá trị": f"Score: {clock_data['fci_score']:+.2f}", "Pha": clock_data['fci_state'], "Nhận xét": "Đo lường mức độ nới lỏng dòng tiền."},
            {"Chỉ số": "Động lượng Volume", "Giá trị": f"{st.session_state.get('vol_ratio', 1.0):.2f}x", "Pha": "Vol MA20 / MA200", "Nhận xét": "Tốc độ giãn nở thanh khoản giao dịch."},
            {"Chỉ số": "Độ rộng Ngân hàng", "Giá trị": f"{st.session_state.get('bank_breadth', 50.0):.1f}%", "Pha": "% Cổ phiếu > MA50", "Nhận xét": "Tỷ lệ CP ngân hàng duy trì xu hướng."},
            {"Chỉ số": "P/E", "Giá trị": f"{inputs.pe_current:.2f}", "Pha": f"Min: {inputs.pe_min} - Max: {inputs.pe_max}", "Nhận xét": "Vùng định giá theo lợi nhuận rổ VN30."},
            {"Chỉ số": "Kỹ thuật Đa tầng", "Giá trị": f"Giá: {inputs.price_current:.1f} | MA20: {ma20_curr:.1f}", "Pha": f"MA200: {inputs.ma200:.1f}", "Nhận xét": "Cấu trúc dòng tiền."}
        ]), use_container_width=True, hide_index=True)

    with tab2:
        try:
            df_logs = load_log_df() if SHEETS_ON else pd.DataFrame(st.session_state.log)
            if not df_logs.empty: st.dataframe(df_logs, use_container_width=True, hide_index=True)
            else: st.info("Chưa có bản ghi lịch sử nào.")
        except Exception as exc: st.error(f"Lỗi tải logs: {exc}")

    with tab3:
        st.subheader(f"📜 Dữ liệu đã lưu trữ ({STOCK_DB_FILE})")
        df_stock_history = load_stock_database(STOCK_DB_FILE)

        if not df_stock_history.empty:
            c_h1, c_h2, c_h3 = st.columns(3)
            c_h1.metric("Tổng số bản ghi", len(df_stock_history))
            c_h2.metric("Số phiên phân tích", df_stock_history["run_id"].nunique())
            c_h3.metric("Phiên mới nhất", df_stock_history["timestamp"].iloc[-1])

            col_f1, col_f2 = st.columns(2)
            with col_f1:
                unique_assets = ["Tất cả"] + list(df_stock_history["asset"].unique())
                filter_asset = st.selectbox("Lọc theo lớp tài sản:", unique_assets, key="filter_stock_asset")
            with col_f2:
                unique_phases = ["Tất cả"] + list(df_stock_history["hmm_phase"].dropna().unique())
                filter_phase = st.selectbox("Lọc theo pha chu kỳ HMM:", unique_phases, key="filter_stock_phase")

            filtered_df = df_stock_history.copy()
            if filter_asset != "Tất cả":
                filtered_df = filtered_df[filtered_df["asset"] == filter_asset]
            if filter_phase != "Tất cả":
                filtered_df = filtered_df[filtered_df["hmm_phase"] == filter_phase]

            st.dataframe(filtered_df, use_container_width=True, hide_index=True)

            csv_stock_bytes = df_stock_history.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button(
                label=f"💾 Tải File {STOCK_DB_FILE}",
                data=csv_stock_bytes,
                file_name=STOCK_DB_FILE,
                mime="text/csv",
                use_container_width=True
            )
        else:
            st.info(f"Chưa có bản ghi nào trong {STOCK_DB_FILE}. Hãy bấm '🚀 Tính toán phân bổ' hoặc kích hoạt Bot để tạo bản ghi đầu tiên.")