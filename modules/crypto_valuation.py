"""
modules/crypto_valuation.py - Crypto VAM Multi-Asset Valuation & Automated Telegram Reporter
Tích hợp:
- Dùng chung 100% Security Vault (AES-128) và Credentials với stock_valuation.py
- Báo cáo Telegram chi tiết, đa tầng tương tự bản TTCK
- Lập lịch tự động gửi báo cáo Telegram & Forward Channel
- Tự động đồng bộ và lưu trữ toàn bộ tham số vào crypto_database.csv
- Tab tra cứu, lọc lịch sử và nút Download file CSV tiện lợi
"""

import os
import json
import time
import threading
import schedule
import logging
import requests
from datetime import datetime

import pandas as pd
import numpy as np
import plotly.graph_objects as go
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx

from vam_onchain import (
    run_vam_analysis,
    load_crypto_database,
    REBALANCE_THRESHOLD_PCT,
    CRYPTO_DB_FILE
)
from modules.stock_valuation import (
    get_secret,
    load_secure_vault,
    save_secure_vault,
    send_telegram_msg,
    forward_telegram_msg,
    SENSITIVE_KEYS,
    CRYPTO_ENABLED,
    GLOBAL_CONFIG as STOCK_GLOBAL_CONFIG
)

CRYPTO_GLOBAL_CONFIG = {}

# ---------------------------------------------------------------------------
# LOGIC TẠO BÁO CÁO TELEGRAM CHI TIẾT
# ---------------------------------------------------------------------------
def build_crypto_telegram_message(data: dict, current_portfolio: dict) -> str:
    alloc = data["allocations"]
    vam_market = data["vam_market_final"]
    w_btc = data["weight_btc"] * 100.0
    w_alt = data["weight_alt"] * 100.0

    if vam_market < 35.0:
        regime_title = "CHIẾT KHẤU CAO (UNDERVALUED)"
        regime_desc = "Định giá toàn thị trường ở vùng an toàn, dưới giá vốn 1 năm. Rủi ro điều chỉnh thấp."
        regime_action = "TÍCH LŨY MẠNH (Tối đa tỷ trọng BTC & Altcoins)"
    elif vam_market > 68.0:
        regime_title = "QUÁ NHIỆT / PHÂN PHỐI (OVERVALUED)"
        regime_desc = "Biên lợi nhuận và đòn bẩy thị trường ở mức cao cực đoan, áp lực xả chốt lời lớn."
        regime_action = "NÂNG PHÒNG THỦ (Chốt lời từng phần chuyển sang USDT/PAXG)"
    else:
        regime_title = "CÂN BẰNG CHU KỲ (FAIR VALUE)"
        regime_desc = "Thị trường tích lũy bền vững, động lượng mua/bán cân bằng quanh mỏ neo dài hạn."
        regime_action = "DUY TRÌ TỶ TRỌNG CHUẨN (Chỉ tái cân bằng khi có tài sản lệch pha)"

    msg = "⚡ <b>BÁO CÁO CRYPTO VAM ĐỊNH LƯỢNG TỰ ĐỘNG</b>\n\n"

    # BLOCK 1: NHIỆT KẾ VĨ MÔ
    msg += "🧭 <b>NHIỆT KẾ VĨ MÔ & CHU KỲ THỊ TRƯỜNG</b>\n"
    msg += f"- <b>VAM Market Composite:</b> <code>{vam_market:.2f} / 100</code>\n"
    msg += f"- <b>Trạng thái:</b> {regime_title}\n"
    msg += f"- <b>Nhận định:</b> {regime_desc}\n"
    msg += f"- <b>Chiến lược:</b> <b>{regime_action}</b>\n"
    msg += f"- <b>Cơ cấu vốn hóa:</b> BTC ({w_btc:.1f}%) | Altcoins ({w_alt:.1f}%)\n\n"

    # BLOCK 2: ĐỊNH GIÁ ĐA TẦNG CHI TIẾT
    msg += "📊 <b>CHI TIẾT ĐỊNH GIÁ ĐA TẦNG TỪNG TÀI SẢN</b>\n"
    for item in data["asset_details"]:
        name = item["name"]
        p = item["price"]
        final_score = item["vam_final"]
        p_dev = item["p_dev"]
        mvrv_r = item["mvrv_ratio"]
        flow = item["flow_score"]
        vol = item["vol_score"]
        realized = item["realized_price"]

        msg += f"• <b>{name}</b> (${p:,.2f} | VAM: <code>{final_score:.2f}</code> - {item['status']})\n"
        msg += f"  ├ <i>Kỹ thuật P_Dev (35%):</i> {p_dev:.1f} | <i>Vol (15%):</i> {vol:.1f}\n"
        msg += f"  ├ <i>On-chain MVRV (30%):</i> {mvrv_r:.2f}x (Giá vốn VWAP: ${realized:,.2f})\n"
        msg += f"  └ <i>Dòng tiền Flow (20%):</i> {flow:.1f} ➔ <b>{item['action']}</b>\n"
    msg += "\n"

    # BLOCK 3: ĐO LƯỜNG RỦI RO & PHÁI SINH
    msg += "🛡️ <b>RỦI RO VĨ MÔ, PHÁI SINH & ON-CHAIN RPC</b>\n"
    msg += f"- <b>Funding Rate Futures:</b> {data['funding_rate']*100:.4f}%/8h ({data['leverage_status']})\n"
    msg += f"- <b>Phạt đòn bẩy phái sinh:</b> {data['leverage_penalty']*100:+.2f}%\n"
    msg += f"- <b>Tỷ số PAXG/BTC:</b> {data['gold_ratio']:.6f} (Phạt vàng: {data['risk_paxg']*100:.2f}%)\n"
    msg += f"- <b>Tổng hệ số phạt rủi ro:</b> <b>{data['total_risk']*100:.2f}%</b> (Chiết khấu trực tiếp vào V_Asset)\n"
    msg += f"- <b>Ethereum RPC Node:</b> {data['onchain']['source']}\n"
    msg += f"- <b>Lưu lượng On-chain:</b> {data['onchain']['eth_tx_count']:,} txs | {data['onchain']['eth_active_addresses']:,} addrs (5 blocks)\n\n"

    # BLOCK 4: THỰC THI TÁI CÂN BẰNG DANH MỤC
    msg += "⚖️ <b>THỰC THI TÁI CÂN BẰNG DANH MỤC (BANDWIDTH ±5%)</b>\n"
    has_action = False
    for asset in ["BTC", "Altcoins (Top 5 Market Cap)", "PAXG", "USDT"]:
        tgt = alloc[asset]
        cur = current_portfolio.get(asset, 0.25)
        drift = tgt - cur
        name_short = asset.replace(" (Top 5 Market Cap)", "")

        if abs(drift) >= REBALANCE_THRESHOLD_PCT:
            has_action = True
            action_tag = f"🚨 <b>{'MUA THÊM' if drift > 0 else 'BÁN BỚT'} {drift*100:+.1f}%</b>"
        else:
            action_tag = f"🟢 HOLD (Lệch {drift*100:+.1f}% trong biên an toàn)"

        msg += f"- <b>{name_short}:</b> HT {cur*100:.1f}% ➔ MT <b>{tgt*100:.1f}%</b> ({action_tag})\n"

    msg += f"- <b>Trạng thái khớp lệnh:</b> {'🔴 CẦN TÁI CÂN BẰNG' if has_action else '🟢 NẮM GIỮ NGUYÊN VỊ THẾ'}\n\n"

    # FOOTER
    msg += "🪙 <b>RỔ TOP 5 ALTCOINS THEO VỐN HÓA:</b>\n"
    msg += f"{', '.join(data['top_5_names'])}\n\n"
    msg += f"💾 <i>Dữ liệu đã tự động lưu vào crypto_database.csv lúc {data['timestamp_local']}.</i>"

    return msg

def automated_crypto_job():
    logging.info("[CRYPTO BOT] Kích hoạt tiến trình báo cáo Crypto VAM tự động...")
    vault = load_secure_vault()
    token = CRYPTO_GLOBAL_CONFIG.get("telegram_token") or vault.get("telegram_token") or get_secret("TELEGRAM_BOT_TOKEN")
    chat_id = CRYPTO_GLOBAL_CONFIG.get("telegram_chat_id") or vault.get("telegram_chat_id") or get_secret("TELEGRAM_CHAT_ID")
    channel_id = str(CRYPTO_GLOBAL_CONFIG.get("telegram_channel_id") or vault.get("telegram_channel_id") or get_secret("TELEGRAM_CHANNEL_ID", "")).strip()

    if not token or not chat_id:
        logging.warning("[CRYPTO BOT] Thiếu Token hoặc Admin Chat ID trong Vault chung, hủy lệnh.")
        return

    try:
        data = run_vam_analysis(log_func=logging.info, auto_save=True)
        curr_port = {
            "BTC": float(CRYPTO_GLOBAL_CONFIG.get("crypto_curr_w_btc", 25.0)) / 100.0,
            "Altcoins (Top 5 Market Cap)": float(CRYPTO_GLOBAL_CONFIG.get("crypto_curr_w_alt", 25.0)) / 100.0,
            "PAXG": float(CRYPTO_GLOBAL_CONFIG.get("crypto_curr_w_paxg", 10.0)) / 100.0,
            "USDT": float(CRYPTO_GLOBAL_CONFIG.get("crypto_curr_w_usdt", 40.0)) / 100.0
        }

        msg = build_crypto_telegram_message(data, curr_port)
        msg_id = send_telegram_msg(token, chat_id, msg)

        if msg_id and channel_id:
            chan = channel_id if (channel_id.startswith("-") or channel_id.startswith("@")) else "@" + channel_id
            forward_telegram_msg(token, chan, chat_id, msg_id)
            logging.info("[CRYPTO BOT] Đã gửi Admin và Forward lên Channel chung thành công.")
    except Exception as e:
        logging.error(f"[CRYPTO BOT] Lỗi chạy tự động Crypto VAM: {e}")

def run_crypto_scheduler():
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            logging.error(f"[CRYPTO BOT] Lỗi schedule: {e}")
        time.sleep(2)

@st.cache_resource
def start_crypto_background_worker():
    thread = threading.Thread(target=run_crypto_scheduler, daemon=True)
    add_script_run_ctx(thread)
    thread.start()
    return thread

# ---------------------------------------------------------------------------
# GIAO DIỆN UI STREAMLIT (HÀM RENDER CHÍNH)
# ---------------------------------------------------------------------------
def render():
    st.markdown(
        """
        <style>
        [data-testid="stMetricValue"] { font-size: clamp(1.1rem, 1.8vw, 1.8rem) !important; }
        </style>
        """,
        unsafe_allow_html=True
    )

    start_crypto_background_worker()

    vault_data = load_secure_vault()
    if "vault_loaded" not in st.session_state:
        st.session_state.update(vault_data)
        st.session_state.vault_loaded = True

    common_keys = {
        "telegram_token": vault_data.get("telegram_token", get_secret("TELEGRAM_BOT_TOKEN", "")),
        "telegram_chat_id": vault_data.get("telegram_chat_id", get_secret("TELEGRAM_CHAT_ID", "")),
        "telegram_channel_id": vault_data.get("telegram_channel_id", get_secret("TELEGRAM_CHANNEL_ID", "")),
        "gemini_api_key": vault_data.get("gemini_api_key", get_secret("GEMINI_API_KEY", "")),
        "crypto_curr_w_btc": 25.0,
        "crypto_curr_w_alt": 25.0,
        "crypto_curr_w_paxg": 10.0,
        "crypto_curr_w_usdt": 40.0,
        "crypto_schedule_mode": "Không",
        "crypto_schedule_time": "08:30"
    }
    for k, v in common_keys.items():
        if k not in st.session_state:
            st.session_state[k] = v

    global CRYPTO_GLOBAL_CONFIG
    CRYPTO_GLOBAL_CONFIG.clear()
    CRYPTO_GLOBAL_CONFIG.update(st.session_state)

    # ---------------------------------------------------------------------------
    # SIDEBAR: CẤU HÌNH DÙNG CHUNG
    # ---------------------------------------------------------------------------
    st.sidebar.title("⚡ Crypto VAM & Telegram Bot")

    server_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    st.sidebar.info(f"🕒 Giờ Server hiện tại: **{server_time}**")

    with st.sidebar.expander("📲 API Bot & Kênh chung (Shared Vault)", expanded=True):
        st.caption("🔑 *Các thông số dưới đây dùng chung 100% với mục Định giá TTCK.*")

        has_token = bool(st.session_state.get("telegram_token"))
        new_token = st.text_input(
            "Telegram Bot Token",
            value="",
            placeholder="•••••••• (Đã liên kết chung)" if has_token else "Nhập Bot Token mới",
            type="password",
            help="Cập nhật tại đây sẽ tự động lưu và dùng chung cho cả TTCK và Crypto."
        )
        if new_token:
            st.session_state.telegram_token = new_token
            STOCK_GLOBAL_CONFIG["telegram_token"] = new_token

        has_chat = bool(st.session_state.get("telegram_chat_id"))
        new_chat = st.text_input(
            "Admin Chat ID",
            value="",
            placeholder="•••••••• (Đã liên kết chung)" if has_chat else "Nhập Admin Chat ID",
            type="password"
        )
        if new_chat:
            st.session_state.telegram_chat_id = new_chat
            STOCK_GLOBAL_CONFIG["telegram_chat_id"] = new_chat

        has_chan = bool(st.session_state.get("telegram_channel_id"))
        new_chan = st.text_input(
            "Public Channel ID",
            value="",
            placeholder=st.session_state.get("telegram_channel_id") if has_chan else "@channel_name",
            type="password"
        )
        if new_chan:
            st.session_state.telegram_channel_id = new_chan
            STOCK_GLOBAL_CONFIG["telegram_channel_id"] = new_chan

        if CRYPTO_ENABLED and (new_token or new_chat or new_chan):
            sync_data = {k: st.session_state.get(k) for k in SENSITIVE_KEYS}
            save_secure_vault(sync_data)

        st.markdown("---")
        s_modes = ["Không", "Hàng ngày", "Hàng tuần", "Hàng tháng"]
        st.session_state.crypto_schedule_mode = st.selectbox(
            "Chu kỳ báo cáo Crypto",
            s_modes,
            index=s_modes.index(st.session_state.crypto_schedule_mode)
        )
        st.session_state.crypto_schedule_time = st.text_input(
            "Giờ gửi Crypto (HH:MM)",
            value=st.session_state.crypto_schedule_time
        )

        cfg_key = f"crypto_{st.session_state.crypto_schedule_mode}_{st.session_state.crypto_schedule_time}"
        if st.session_state.get("last_crypto_schedule_cfg") != cfg_key:
            schedule.clear("crypto_jobs")
            m = st.session_state.crypto_schedule_mode
            t = st.session_state.crypto_schedule_time
            if m == "Hàng ngày":
                schedule.every().day.at(t).do(automated_crypto_job).tag("crypto_jobs")
            elif m == "Hàng tuần":
                schedule.every().monday.at(t).do(automated_crypto_job).tag("crypto_jobs")
            elif m == "Hàng tháng":
                schedule.every(30).days.at(t).do(automated_crypto_job).tag("crypto_jobs")
            st.session_state.last_crypto_schedule_cfg = cfg_key

        c_btn1, c_btn2 = st.columns(2)
        with c_btn1:
            if st.button("Kiểm tra Bot", use_container_width=True):
                tok = st.session_state.get("telegram_token")
                cid = st.session_state.get("telegram_chat_id")
                if not tok or not cid:
                    st.error("Thiếu Token/Chat ID!")
                else:
                    mid = send_telegram_msg(tok, cid, "✅ [Crypto Module] Kiểm tra kết nối Bot thành công!")
                    if mid: st.success("Gửi Admin OK!")
                    else: st.error("Gửi thất bại!")

        with c_btn2:
            if st.button("Gửi báo cáo ngay", use_container_width=True):
                with st.spinner("Đang phân tích & đẩy báo cáo chi tiết..."):
                    automated_crypto_job()
                    st.toast("Đã gửi báo cáo chi tiết & lưu Database!", icon="🚀")

    with st.sidebar.expander("👤 Tỷ trọng danh mục hiện tại (%)", expanded=True):
        st.number_input("BTC Hiện tại (%)", 0.0, 100.0, step=1.0, key="crypto_curr_w_btc")
        st.number_input("Top 5 Altcoins (%)", 0.0, 100.0, step=1.0, key="crypto_curr_w_alt")
        st.number_input("PAXG Vàng (%)", 0.0, 100.0, step=1.0, key="crypto_curr_w_paxg")
        st.number_input("USDT Tiền mặt (%)", 0.0, 100.0, step=1.0, key="crypto_curr_w_usdt")

    # ---------------------------------------------------------------------------
    # MAIN DASHBOARD
    # ---------------------------------------------------------------------------
    st.title("⚡ Crypto VAM Multi-Asset Valuation & Dynamic Allocation")
    st.markdown("Hệ thống định lượng đa tầng: On-chain VWAP MVRV, Price Deviation, Taker Flow, Funding Rate & Rebalance Safeguard.")

    calc_btn = st.button("🚀 Chạy phân tích Crypto VAM Realtime", type="primary")

    if calc_btn or "last_crypto_data" not in st.session_state:
        with st.spinner("Đang quét On-chain RPC Pool, Binance Spot/Futures và CoinGecko Market Cap..."):
            try:
                st.session_state.last_crypto_data = run_vam_analysis(log_func=st.write, auto_save=True)
                st.toast("✅ Đã cập nhật & đồng bộ dữ liệu vào crypto_database.csv!", icon="💾")
            except Exception as e:
                st.error(f"Lỗi phân tích: {e}")
                return

    data = st.session_state.last_crypto_data
    vam_market = data["vam_market_final"]

    # Header Card
    if vam_market < 35.0:
        c_bg, c_border, status_txt = "#064E3B", "#10B981", "CHIẾT KHẤU CAO (Undervalued) - Tích lũy tối đa"
    elif vam_market > 68.0:
        c_bg, c_border, status_txt = "#7F1D1D", "#EF4444", "QUÁ NHIỆT (Overvalued) - Nâng phòng thủ"
    else:
        c_bg, c_border, status_txt = "#0F172A", "#38BDF8", "CÂN BẰNG CHU KỲ (Fair Value) - Duy trì ổn định"

    st.markdown(
        f"""
        <div style="background-color: {c_bg}; border-left: 6px solid {c_border}; padding: 16px 20px; border-radius: 8px; margin-bottom: 20px;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                <div>
                    <div style="font-size: 0.85rem; color: #94A3B8; font-weight: 600;">🌡️ NHIỆT KẾ VĨ MÔ CRYPTO VAM</div>
                    <div style="font-size: 1.5rem; font-weight: 700; color: #F8FAFC;">{status_txt}</div>
                </div>
                <div style="background: #1E293B; padding: 10px 16px; border-radius: 6px; border: 1px solid #334155; text-align: center;">
                    <span style="font-size: 0.75rem; color: #94A3B8;">VAM Market Composite:</span><br>
                    <span style="font-size: 1.5rem; font-weight: 800; color: #38BDF8;">{vam_market:.2f} / 100</span>
                </div>
            </div>
            <div style="margin-top: 8px; font-size: 0.9rem; color: #CBD5E1;">
                Top 5 Altcoins theo Vốn Hóa: <b>{', '.join(data['top_5_names'])}</b> | RPC Node: <b>{data['onchain']['source']}</b>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # 4 Metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Bitcoin (BTC) VAM", f"{data['btc_score']:.2f}")
    m2.metric("Altcoins Top 5 VAM", f"{data['altcoin_index']:.2f}")
    m3.metric("BTC Funding Rate", f"{data['funding_rate']*100:.4f}%", delta=data["leverage_status"])
    m4.metric("Tổng Phạt Rủi Ro", f"{data['total_risk']*100:.2f}%", delta=f"PAXG: {data['risk_paxg']*100:.1f}%")

    st.markdown("---")

    tab1, tab2, tab3 = st.tabs([
        "📊 Bảng Định Lượng & Biểu Đồ",
        "⚖️ Kiểm Soát Tái Cân Bằng (Bandwidth ±5%)",
        "📜 Lịch Sử Cơ Sở Dữ Liệu (crypto_database.csv)"
    ])

    with tab1:
        col_chart1, col_chart2 = st.columns([1, 1.2])
        with col_chart1:
            st.subheader("🎯 Tỷ trọng Phân bổ Mục tiêu")
            alloc = data["allocations"]
            fig_pie = go.Figure(data=[go.Pie(
                labels=["BTC", "Altcoins", "PAXG (Vàng)", "USDT"],
                values=[alloc["BTC"], alloc["Altcoins (Top 5 Market Cap)"], alloc["PAXG"], alloc["USDT"]],
                hole=0.45,
                marker=dict(colors=["#F59E0B", "#3B82F6", "#EAB308", "#10B981"])
            )])
            fig_pie.update_layout(margin=dict(l=20, r=20, t=20, b=20), height=300)
            st.plotly_chart(fig_pie, use_container_width=True)

        with col_chart2:
            st.subheader("📋 Bảng Định lượng Đa tầng Chi tiết")
            df_details = pd.DataFrame(data["asset_details"])
            df_display = pd.DataFrame({
                "Tài sản": df_details["name"],
                "Giá": df_details["price"].apply(lambda x: f"${x:,.2f}"),
                "P_Dev": df_details["p_dev"].round(1),
                "MVRV Ratio": df_details["mvrv_ratio"].apply(lambda x: f"{x:.2f}x"),
                "MVRV Score": df_details["mvrv_score"].round(1),
                "Flow": df_details["flow_score"].round(1),
                "Vol": df_details["vol_score"].round(1),
                "VAM Final": df_details["vam_final"].round(2),
                "Realized VWAP": df_details["realized_price"].apply(lambda x: f"${x:,.2f}"),
                "Trạng thái": df_details["status"]
            })
            st.dataframe(df_display, use_container_width=True, hide_index=True)

    with tab2:
        st.subheader("⚖️ Kiểm soát Tái cân bằng Thực tế")
        alloc = data["allocations"]
        curr_port = {
            "BTC": st.session_state.crypto_curr_w_btc / 100.0,
            "Altcoins (Top 5 Market Cap)": st.session_state.crypto_curr_w_alt / 100.0,
            "PAXG": st.session_state.crypto_curr_w_paxg / 100.0,
            "USDT": st.session_state.crypto_curr_w_usdt / 100.0
        }
        rebal_rows = []
        for asset in ["BTC", "Altcoins (Top 5 Market Cap)", "PAXG", "USDT"]:
            tgt = alloc[asset]
            cur = curr_port[asset]
            drift = tgt - cur
            if abs(drift) >= REBALANCE_THRESHOLD_PCT:
                act = f"🚨 {'MUA THÊM' if drift > 0 else 'BÁN BỚT'} ({drift*100:+.1f}%)"
            else:
                act = "🟢 HOLD (Dung sai an toàn)"
            rebal_rows.append({
                "Tài sản": asset.replace(" (Top 5 Market Cap)", ""),
                "Hiện tại": f"{cur*100:.1f}%",
                "Mục tiêu": f"{tgt*100:.1f}%",
                "Độ lệch": f"{drift*100:+.1f}%",
                "Hành động": act
            })
        st.dataframe(pd.DataFrame(rebal_rows), use_container_width=True, hide_index=True)

    with tab3:
        st.subheader(f"📜 Dữ liệu đã lưu trữ ({CRYPTO_DB_FILE})")
        df_history = load_crypto_database(CRYPTO_DB_FILE)

        if not df_history.empty:
            c_h1, c_h2, c_h3 = st.columns(3)
            c_h1.metric("Tổng số bản ghi", len(df_history))
            c_h2.metric("Số phiên phân tích", df_history["run_id"].nunique())
            c_h3.metric("Phiên mới nhất", df_history["timestamp"].iloc[-1])

            col_f1, col_f2 = st.columns(2)
            with col_f1:
                unique_assets = ["Tất cả"] + list(df_history["asset"].unique())
                filter_asset = st.selectbox("Lọc theo tài sản:", unique_assets)
            with col_f2:
                unique_categories = ["Tất cả"] + list(df_history["category"].dropna().unique())
                filter_cat = st.selectbox("Lọc theo phân loại:", unique_categories)

            filtered_df = df_history.copy()
            if filter_asset != "Tất cả":
                filtered_df = filtered_df[filtered_df["asset"] == filter_asset]
            if filter_cat != "Tất cả":
                filtered_df = filtered_df[filtered_df["category"] == filter_cat]

            st.dataframe(filtered_df, use_container_width=True, hide_index=True)

            csv_data = df_history.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button(
                label="💾 Tải File crypto_database.csv",
                data=csv_data,
                file_name="crypto_database.csv",
                mime="text/csv",
                use_container_width=True
            )
        else:
            st.info("Chưa có bản ghi nào trong crypto_database.csv. Hãy bấm '🚀 Chạy phân tích Crypto VAM Realtime' hoặc kích hoạt Bot để tạo bản ghi đầu tiên.")