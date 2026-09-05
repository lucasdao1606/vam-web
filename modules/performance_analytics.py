"""
modules/performance_analytics.py - VAM Multi-Asset Performance & Quantitative Analytics
Xử lý và trực quan hóa chuỗi dữ liệu lịch sử từ:
- stock_database.csv (TTCK Việt Nam)
- crypto_database.csv (Thị trường Crypto)
Tối ưu:
- Tương thích Responsive đa màn hình (Mobile, Tablet, Desktop, Ultrawide).
- Toàn bộ biểu đồ VAM Score (Stock, Crypto, Cross-Asset) đều vẽ trên nền 3 dải phân vùng định giá (Chiết khấu rẻ, Cân bằng, Quá nhiệt).
- Đồng nhất hệ quy chiếu VAM Score về thang 0 - 100 điểm.
- Bổ sung biểu đồ đối chiếu giá song hành: VN-Index vs Bitcoin (BTC).
- Bổ sung biểu đồ phân tích biến động (Volatility) & thanh khoản (Volume) đa thị trường.
"""

import os
import re
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

STOCK_DB_FILE = "stock_database.csv"
CRYPTO_DB_FILE = "crypto_database.csv"

# Cấu hình chuẩn giúp Plotly tự co giãn trên mọi tỷ lệ màn hình
PLOTLY_RESPONSIVE_CONFIG = {
    "responsive": True,
    "displayModeBar": False,
    "scrollZoom": False
}

# ---------------------------------------------------------------------------
# HÀM HỖ TRỢ VẼ DẢI PHÂN VÙNG ĐỊNH GIÁ CHUẨN (VAM BACKGROUND BANDS)
# ---------------------------------------------------------------------------
def apply_vam_background_bands(fig, yref="y"):
    """
    Vẽ 3 dải nền định giá VAM chuẩn hóa thang 0 - 100:
    - 0 đến 35: Vùng Chiết khấu Rẻ (Undervalued)
    - 35 đến 65: Vùng Định giá Cân bằng (Fair Value)
    - 65 đến 100: Vùng Quá nhiệt (Overvalued)
    """
    fig.add_hrect(
        y0=0, y1=35,
        fillcolor="rgba(34, 197, 94, 0.12)",
        line_width=0,
        yref=yref,
        annotation_text="Vùng Chiết khấu Rẻ (Undervalued)",
        annotation_position="top left",
        annotation_font_size=10,
        annotation_font_color="#10B981"
    )
    fig.add_hrect(
        y0=35, y1=65,
        fillcolor="rgba(56, 189, 248, 0.08)",
        line_width=0,
        yref=yref,
        annotation_text="Vùng Định giá Cân bằng (Fair Value)",
        annotation_position="top left",
        annotation_font_size=10,
        annotation_font_color="#38BDF8"
    )
    fig.add_hrect(
        y0=65, y1=100,
        fillcolor="rgba(239, 68, 68, 0.12)",
        line_width=0,
        yref=yref,
        annotation_text="Vùng Quá nhiệt (Overvalued)",
        annotation_position="top left",
        annotation_font_size=10,
        annotation_font_color="#EF4444"
    )

# ---------------------------------------------------------------------------
# HÀM HỖ TRỢ XỬ LÝ CỘT & SỐ LIỆU ĐỘNG (ROBUST HELPERS)
# ---------------------------------------------------------------------------
def resolve_column(df: pd.DataFrame, keywords: list) -> str:
    """Tìm tên cột đầu tiên khớp chính xác hoặc chứa từ khóa (không phân biệt hoa/thường)."""
    if df.empty:
        return None
    cols = list(df.columns)
    for kw in keywords:
        for c in cols:
            if c.strip().lower() == kw.strip().lower():
                return c
    for kw in keywords:
        for c in cols:
            if kw.strip().lower() in c.strip().lower():
                return c
    return None

def clean_to_numeric(series: pd.Series) -> pd.Series:
    """Làm sạch và chuyển đổi Series sang kiểu số thực (loại bỏ %, $, khoảng trắng)."""
    s = (
        series.astype(str)
        .str.replace("%", "", regex=False)
        .str.replace("$", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    return pd.to_numeric(s, errors="coerce")

def load_and_preprocess_stock(db_path=STOCK_DB_FILE):
    if not os.path.exists(db_path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(db_path, encoding="utf-8-sig")
        if df.empty:
            return pd.DataFrame()
        ts_col = resolve_column(df, ["timestamp", "timestamp_local", "datetime"])
        if ts_col:
            df["datetime"] = pd.to_datetime(df[ts_col], errors="coerce")
            df = df.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
        return df
    except Exception as e:
        st.error(f"Lỗi đọc dữ liệu Stock: {e}")
        return pd.DataFrame()

def load_and_preprocess_crypto(db_path=CRYPTO_DB_FILE):
    if not os.path.exists(db_path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(db_path, encoding="utf-8-sig")
        if df.empty:
            return pd.DataFrame()
        ts_col = resolve_column(df, ["timestamp", "timestamp_local", "datetime"])
        if ts_col:
            df["datetime"] = pd.to_datetime(df[ts_col], errors="coerce")
            df = df.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
        return df
    except Exception as e:
        st.error(f"Lỗi đọc dữ liệu Crypto: {e}")
        return pd.DataFrame()

# ---------------------------------------------------------------------------
# GIAO DIỆN PHÂN TÍCH CHÍNH (RENDER)
# ---------------------------------------------------------------------------
def render():
    st.markdown(
        """
        <style>
        .main .block-container {
            padding-left: clamp(1rem, 3vw, 3rem);
            padding-right: clamp(1rem, 3vw, 3rem);
            max-width: 1600px;
        }
        h1 {
            font-size: clamp(1.35rem, 2.2vw, 2.2rem) !important;
            font-weight: 700 !important;
        }
        h2, h3 {
            font-size: clamp(1.1rem, 1.6vw, 1.5rem) !important;
            font-weight: 600 !important;
            margin-top: 1rem !important;
        }
        [data-testid="stMetricValue"] {
            font-size: clamp(1.1rem, 1.7vw, 1.75rem) !important;
            font-weight: 700 !important;
            white-space: normal !important;
        }
        [data-testid="stMetricLabel"] {
            font-size: clamp(0.75rem, 0.95vw, 0.9rem) !important;
            color: #94A3B8 !important;
        }
        @media (max-width: 768px) {
            .main .block-container {
                padding-left: 0.8rem;
                padding-right: 0.8rem;
            }
            [data-testid="stMetricValue"] {
                font-size: 1.25rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    st.title("📈 Phân tích Hiệu quả Đầu tư & Chuỗi Dữ liệu VAM")
    st.markdown("Khai thác và phân tích định lượng chuỗi lịch sử phân bổ tài sản, chu kỳ kinh tế và rủi ro thị trường.")

    stock_df = load_and_preprocess_stock()
    crypto_df = load_and_preprocess_crypto()

    has_stock = not stock_df.empty and "datetime" in stock_df.columns
    has_crypto = not crypto_df.empty and "datetime" in crypto_df.columns

    if not has_stock and not has_crypto:
        st.info("Chưa tìm thấy dữ liệu hợp lệ trong cả hai file `stock_database.csv` và `crypto_database.csv`. Hãy chạy phân tích hoặc kích hoạt bot để tạo dữ liệu lịch sử đầu tiên.")
        return

    tab_stock, tab_crypto, tab_cross = st.tabs([
        "🏛️ Phân tích Lịch sử TTCK (VAM Core)",
        "⚡ Phân tích Lịch sử Crypto",
        "🌐 Tương quan Đa Tài sản (Cross-Asset)"
    ])

    # ---------------------------------------------------------------------------
    # TAB 1: PHÂN TÍCH STOCK DATABASE
    # ---------------------------------------------------------------------------
    with tab_stock:
        if not has_stock:
            st.warning("Chưa có dữ liệu lịch sử hợp lệ trong `stock_database.csv`.")
        else:
            run_col = resolve_column(stock_df, ["run_id", "timestamp"]) or "datetime"
            runs = stock_df[run_col].unique()
            c_s1, c_s2, c_s3, c_s4 = st.columns(4)
            c_s1.metric("Tổng số phiên ghi nhận", len(runs))
            c_s2.metric("Phiên đầu tiên", stock_df["datetime"].min().strftime("%Y-%m-%d %H:%M"))
            c_s3.metric("Phiên gần nhất", stock_df["datetime"].max().strftime("%Y-%m-%d %H:%M"))

            score_col = resolve_column(stock_df, ["valuation_score", "score", "vam_score"])
            if score_col and not stock_df[score_col].dropna().empty:
                latest_score = float(stock_df[score_col].dropna().iloc[-1])
                c_s4.metric("VAM Score hiện tại", f"{latest_score:.2f}")
            else:
                c_s4.metric("VAM Score", "N/A")

            # 1. BIỂU ĐỒ TỶ TRỌNG STOCK
            st.subheader("1. Lịch sử Cơ cấu Tỷ trọng Thực thi (Execution Weights)")
            w_col = resolve_column(stock_df, ["exec_weight_pct", "target_weight_pct", "current_weight_pct", "weight"])
            asset_col = resolve_column(stock_df, ["asset", "asset_key", "asset_name"])

            if w_col and asset_col:
                stock_df[w_col] = clean_to_numeric(stock_df[w_col])
                pivot_weights = stock_df.pivot_table(index="datetime", columns=asset_col, values=w_col, aggfunc="last").fillna(0)
                fig_alloc = go.Figure()
                color_map_stock = {"Cổ phiếu": "#2563EB", "Trái phiếu": "#F59E0B", "Vàng": "#10B981", "equity": "#2563EB", "bond": "#F59E0B", "gold": "#10B981"}
                
                if len(pivot_weights) == 1:
                    for col in pivot_weights.columns:
                        fig_alloc.add_trace(go.Bar(
                            x=[pivot_weights.index[0].strftime('%Y-%m-%d %H:%M')],
                            y=pivot_weights[col],
                            name=str(col),
                            marker_color=color_map_stock.get(col, "#64748B")
                        ))
                    fig_alloc.update_layout(barmode="stack")
                else:
                    for col in pivot_weights.columns:
                        fig_alloc.add_trace(go.Scatter(
                            x=pivot_weights.index,
                            y=pivot_weights[col],
                            mode="lines",
                            stackgroup="one",
                            name=str(col),
                            line=dict(width=0.5, color=color_map_stock.get(col, "#94A3B8")),
                            fillcolor=color_map_stock.get(col, "rgba(148, 163, 184, 0.5)")
                        ))
                fig_alloc.update_layout(
                    autosize=True,
                    yaxis=dict(title="Tỷ trọng (%)", range=[0, 100]),
                    hovermode="x unified",
                    height=360,
                    margin=dict(l=15, r=15, t=30, b=25),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5, font=dict(size=10.5))
                )
                st.plotly_chart(fig_alloc, use_container_width=True, config=PLOTLY_RESPONSIVE_CONFIG)

            # 2. ĐỐI CHIẾU VNINDEX VÀ VAM SCORE (ĐÃ BỔ SUNG 3 DẢI NỀN ĐỊNH GIÁ)
            st.subheader("2. Tương quan VN-Index & VAM Valuation Score")
            df_unique_runs = stock_df.drop_duplicates(subset=[run_col]).copy()
            price_col = resolve_column(df_unique_runs, ["vnindex_price", "price_current", "price", "close"])

            if price_col and score_col:
                df_unique_runs[price_col] = clean_to_numeric(df_unique_runs[price_col])
                df_unique_runs[score_col] = clean_to_numeric(df_unique_runs[score_col])
                # Chuẩn hóa Stock VAM Score về thang 0 - 100
                df_unique_runs["stock_vam_norm"] = np.clip(df_unique_runs[score_col] * 50.0, 0.0, 100.0)
                mode_plot = "lines+markers" if len(df_unique_runs) > 1 else "markers"

                fig_dual = make_subplots(specs=[[{"secondary_y": True}]])

                # ÁP DỤNG 3 DẢI PHÂN VÙNG ĐỊNH GIÁ VÀO TRỤC THỨ CẤP Y2
                apply_vam_background_bands(fig_dual, yref="y2")

                fig_dual.add_trace(
                    go.Scatter(
                        x=df_unique_runs["datetime"],
                        y=df_unique_runs[price_col],
                        mode=mode_plot,
                        name="VN-Index Giá",
                        line=dict(color="#38BDF8", width=2.5),
                        marker=dict(size=7)
                    ),
                    secondary_y=False
                )
                fig_dual.add_trace(
                    go.Scatter(
                        x=df_unique_runs["datetime"],
                        y=df_unique_runs["stock_vam_norm"],
                        mode=mode_plot,
                        name="VAM Score (Chuẩn hóa 0-100)",
                        line=dict(color="#F43F5E", width=2.5),
                        marker=dict(size=7),
                        hovertemplate="VAM Score (Norm): %{y:.1f} / 100<br>Điểm gốc: %{customdata:.2f}<extra></extra>",
                        customdata=df_unique_runs[score_col]
                    ),
                    secondary_y=True
                )
                fig_dual.update_layout(
                    autosize=True,
                    height=400,
                    hovermode="x unified",
                    margin=dict(l=15, r=15, t=35, b=25),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5, font=dict(size=10.5))
                )
                fig_dual.update_yaxes(title_text="Điểm số VN-Index", secondary_y=False)
                fig_dual.update_yaxes(title_text="VAM Valuation Score (0 - 100)", range=[0, 100], secondary_y=True)
                st.plotly_chart(fig_dual, use_container_width=True, config=PLOTLY_RESPONSIVE_CONFIG)

            # 3. MA SÁT VÒNG QUAY & CHI PHÍ
            st.subheader("3. Ma sát Tái cân bằng (Turnover & Estimated Fee Cost)")
            turnover_col = resolve_column(df_unique_runs, ["actual_turnover_pct", "actual_turnover", "turnover"])
            cost_col = resolve_column(df_unique_runs, ["est_fee_cost_vnd", "est_fee_cost", "fee_cost"])

            if turnover_col or cost_col:
                c_m1, c_m2 = st.columns(2)
                with c_m1:
                    if turnover_col:
                        df_unique_runs[turnover_col] = clean_to_numeric(df_unique_runs[turnover_col])
                        fig_turnover = go.Figure()
                        fig_turnover.add_trace(go.Bar(
                            x=df_unique_runs["datetime"],
                            y=df_unique_runs[turnover_col],
                            name="Turnover Thực tế (%)",
                            marker_color="#8B5CF6"
                        ))
                        fig_turnover.update_layout(
                            autosize=True,
                            title="Vòng quay Danh mục Thực tế (%)",
                            height=320,
                            margin=dict(l=15, r=15, t=40, b=25)
                        )
                        st.plotly_chart(fig_turnover, use_container_width=True, config=PLOTLY_RESPONSIVE_CONFIG)
                with c_m2:
                    if cost_col:
                        df_unique_runs[cost_col] = clean_to_numeric(df_unique_runs[cost_col])
                        fig_cost = go.Figure()
                        fig_cost.add_trace(go.Scatter(
                            x=df_unique_runs["datetime"],
                            y=df_unique_runs[cost_col],
                            name="Chi phí ma sát (VND)",
                            line=dict(color="#EC4899", width=2),
                            mode="lines+markers",
                            marker=dict(size=6)
                        ))
                        fig_cost.update_layout(
                            autosize=True,
                            title="Ước tính Chi phí Giao dịch (VND)",
                            height=320,
                            margin=dict(l=15, r=15, t=40, b=25)
                        )
                        st.plotly_chart(fig_cost, use_container_width=True, config=PLOTLY_RESPONSIVE_CONFIG)

    # ---------------------------------------------------------------------------
    # TAB 2: CRYPTO DATABASE
    # ---------------------------------------------------------------------------
    with tab_crypto:
        if not has_crypto:
            st.warning("Chưa có dữ liệu lịch sử hợp lệ trong `crypto_database.csv`.")
        else:
            c_run_col = resolve_column(crypto_df, ["run_id", "timestamp"]) or "datetime"
            c_asset_col = resolve_column(crypto_df, ["asset", "asset_name", "name", "symbol", "coin"])
            c_w_col = resolve_column(crypto_df, ["target_weight_pct", "weight_pct", "target_weight", "weight", "allocation_pct", "allocation", "exec_weight_pct"])
            p_col = resolve_column(crypto_df, ["price_usd", "price", "current_price", "close", "gia"])
            num_runs = crypto_df[c_run_col].nunique()

            c_score_col = resolve_column(
                crypto_df,
                ["vam_market_final", "vam_market_composite", "market_composite", "vam_composite", "vam_market", "score"]
            )
            if c_score_col:
                crypto_df[c_score_col] = clean_to_numeric(crypto_df[c_score_col])
                run_score_map = crypto_df.dropna(subset=[c_score_col]).groupby(c_run_col)[c_score_col].first().to_dict()
                crypto_df["crypto_vam_final_sync"] = crypto_df[c_run_col].map(run_score_map)
            else:
                crypto_df["crypto_vam_final_sync"] = np.nan

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Số phiên Crypto ghi nhận", num_runs)
            k2.metric("Phiên đầu tiên", crypto_df["datetime"].min().strftime("%Y-%m-%d %H:%M"))
            k3.metric("Phiên gần nhất", crypto_df["datetime"].max().strftime("%Y-%m-%d %H:%M"))

            valid_scores = crypto_df["crypto_vam_final_sync"].dropna()
            if not valid_scores.empty:
                k4.metric("Crypto VAM Score Final", f"{float(valid_scores.iloc[-1]):.2f} / 100")
            else:
                k4.metric("Crypto VAM Score Final", "N/A")

            # Phân tách 2 nhóm bản ghi: Allocation vs Asset Details
            if c_w_col:
                crypto_df[c_w_col] = clean_to_numeric(crypto_df[c_w_col])
                df_alloc = crypto_df[crypto_df[c_w_col].notna()].copy()
            else:
                df_alloc = pd.DataFrame()

            if p_col:
                crypto_df[p_col] = clean_to_numeric(crypto_df[p_col])
                df_assets = crypto_df[crypto_df[p_col].notna()].copy()
            else:
                df_assets = pd.DataFrame()

            # 1. BIỂU ĐỒ PHÂN BỔ TỶ TRỌNG MỤC TIÊU CRYPTO
            st.subheader("1. Lịch sử Cơ cấu Phân bổ Danh mục Crypto")
            if not df_alloc.empty and c_asset_col and c_w_col:
                pivot_c_alloc = df_alloc.pivot_table(index="datetime", columns=c_asset_col, values=c_w_col, aggfunc="last").fillna(0)
                
                if pivot_c_alloc.max().max() <= 1.05:
                    pivot_c_alloc = pivot_c_alloc * 100.0

                fig_c_alloc = go.Figure()
                color_map_crypto = {
                    "BTC": "#F59E0B", "Altcoins (Top 5)": "#3B82F6", "Altcoins (Top 5 Market Cap)": "#3B82F6",
                    "PAXG": "#EAB308", "USDT": "#10B981"
                }

                if len(pivot_c_alloc) == 1:
                    for a_name in pivot_c_alloc.columns:
                        fig_c_alloc.add_trace(go.Bar(
                            x=[pivot_c_alloc.index[0].strftime('%Y-%m-%d %H:%M')],
                            y=pivot_c_alloc[a_name],
                            name=str(a_name),
                            marker_color=color_map_crypto.get(str(a_name), "#64748B")
                        ))
                    fig_c_alloc.update_layout(barmode="stack")
                else:
                    for a_name in pivot_c_alloc.columns:
                        fig_c_alloc.add_trace(go.Scatter(
                            x=pivot_c_alloc.index,
                            y=pivot_c_alloc[a_name],
                            mode="lines",
                            stackgroup="one",
                            name=str(a_name),
                            line=dict(width=0.5, color=color_map_crypto.get(str(a_name), "#64748B")),
                            fillcolor=color_map_crypto.get(str(a_name), "rgba(100, 116, 139, 0.5)")
                        ))

                fig_c_alloc.update_layout(
                    autosize=True,
                    yaxis=dict(title="Tỷ trọng (%)", range=[0, 100]),
                    hovermode="x unified",
                    height=360,
                    margin=dict(l=15, r=15, t=30, b=25),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5, font=dict(size=10.5))
                )
                st.plotly_chart(fig_c_alloc, use_container_width=True, config=PLOTLY_RESPONSIVE_CONFIG)
            else:
                st.info("Chưa trích xuất được dữ liệu tỷ trọng phân bổ.")

            # 2. ĐỐI CHIẾU GIÁ BTC, ON-CHAIN MVRV & CRYPTO VAM SCORE FINAL (ĐÃ BỔ SUNG 3 DẢI NỀN ĐỊNH GIÁ)
            st.subheader("2. Tương quan Giá Bitcoin, On-chain MVRV & Crypto VAM Score Final")
            if not df_assets.empty and c_asset_col:
                btc_mask = (
                    df_assets[c_asset_col].astype(str).str.upper().str.contains(r"\bBTC\b|BITCOIN") &
                    ~df_assets[c_asset_col].astype(str).str.upper().str.contains(r"WBTC|WRAP")
                )
                btc_series = df_assets[btc_mask].copy()

                if not btc_series.empty:
                    mvrv_col = resolve_column(btc_series, ["mvrv_ratio", "mvrv", "mvrv_score"])
                    
                    fig_btc = make_subplots(specs=[[{"secondary_y": True}]])
                    mode_btc = "lines+markers" if len(btc_series) > 1 else "markers"

                    # ÁP DỤNG 3 DẢI PHÂN VÙNG ĐỊNH GIÁ VÀO TRỤC THỨ CẤP Y2
                    apply_vam_background_bands(fig_btc, yref="y2")

                    # Trace 1: Giá BTC (Trục trái)
                    if p_col and not btc_series[p_col].dropna().empty:
                        fig_btc.add_trace(
                            go.Scatter(
                                x=btc_series["datetime"],
                                y=btc_series[p_col],
                                mode=mode_btc,
                                name="Giá BTC ($)",
                                line=dict(color="#F59E0B", width=2.5),
                                marker=dict(size=7)
                            ),
                            secondary_y=False
                        )
                    # Trace 2: On-chain MVRV Ratio (Trục phải)
                    if mvrv_col:
                        btc_series[mvrv_col] = clean_to_numeric(btc_series[mvrv_col])
                        fig_btc.add_trace(
                            go.Scatter(
                                x=btc_series["datetime"],
                                y=btc_series[mvrv_col],
                                mode=mode_btc,
                                name="MVRV Ratio (x)",
                                line=dict(color="#10B981", width=2, dash="dot"),
                                marker=dict(size=7)
                            ),
                            secondary_y=True
                        )
                    # Trace 3: Crypto VAM Score Final (Trục phải)
                    if "crypto_vam_final_sync" in btc_series.columns and not btc_series["crypto_vam_final_sync"].dropna().empty:
                        fig_btc.add_trace(
                            go.Scatter(
                                x=btc_series["datetime"],
                                y=btc_series["crypto_vam_final_sync"],
                                mode=mode_btc,
                                name="Crypto VAM Score Final (0-100)",
                                line=dict(color="#EF4444", width=2.5),
                                marker=dict(size=7)
                            ),
                            secondary_y=True
                        )

                    fig_btc.update_layout(
                        autosize=True,
                        height=400,
                        hovermode="x unified",
                        margin=dict(l=15, r=15, t=35, b=25),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5, font=dict(size=10.5))
                    )
                    fig_btc.update_yaxes(title_text="Giá BTC ($)", secondary_y=False)
                    fig_btc.update_yaxes(title_text="MVRV Ratio / Crypto VAM Score Final (0 - 100)", range=[0, 100], secondary_y=True)
                    st.plotly_chart(fig_btc, use_container_width=True, config=PLOTLY_RESPONSIVE_CONFIG)
                else:
                    st.info("Chưa tìm thấy các bản ghi riêng của Bitcoin trong dữ liệu định lượng.")
            else:
                st.info("Chưa có bản ghi giá tài sản trong database.")

            # 3. ĐÒN BẨY PHÁI SINH & HỆ SỐ PHẠT RỦI RO
            st.subheader("3. Đòn bẩy Phái sinh & Hệ số Phạt Rủi ro Toàn thị trường")
            funding_col = resolve_column(crypto_df, ["funding_rate", "funding", "rate"])
            risk_col = resolve_column(crypto_df, ["total_risk", "risk_penalty", "risk", "penalty"])

            if funding_col or risk_col:
                unique_c_runs = crypto_df.drop_duplicates(subset=[c_run_col]).copy()
                fig_deriv = make_subplots(specs=[[{"secondary_y": True}]])
                mode_r = "lines+markers" if len(unique_c_runs) > 1 else "markers"

                if funding_col:
                    unique_c_runs[funding_col] = clean_to_numeric(unique_c_runs[funding_col])
                    if abs(unique_c_runs[funding_col].max()) < 0.05:
                        unique_c_runs[funding_col] = unique_c_runs[funding_col] * 100.0

                    fig_deriv.add_trace(
                        go.Bar(
                            x=unique_c_runs["datetime"],
                            y=unique_c_runs[funding_col],
                            name="Funding Rate (%/8h)",
                            marker_color="#38BDF8"
                        ),
                        secondary_y=False
                    )
                if risk_col:
                    unique_c_runs[risk_col] = clean_to_numeric(unique_c_runs[risk_col])
                    if unique_c_runs[risk_col].max() <= 1.05:
                        unique_c_runs[risk_col] = unique_c_runs[risk_col] * 100.0

                    fig_deriv.add_trace(
                        go.Scatter(
                            x=unique_c_runs["datetime"],
                            y=unique_c_runs[risk_col],
                            mode=mode_r,
                            name="Tổng Phạt Rủi Ro (%)",
                            line=dict(color="#F43F5E", width=2),
                            marker=dict(size=7)
                        ),
                        secondary_y=True
                    )

                fig_deriv.update_layout(
                    autosize=True,
                    height=340,
                    hovermode="x unified",
                    margin=dict(l=15, r=15, t=35, b=25),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5, font=dict(size=10.5))
                )
                fig_deriv.update_yaxes(title_text="Funding Rate (%)", secondary_y=False)
                fig_deriv.update_yaxes(title_text="Phạt Rủi Ro (%)", secondary_y=True)
                st.plotly_chart(fig_deriv, use_container_width=True, config=PLOTLY_RESPONSIVE_CONFIG)

    # ---------------------------------------------------------------------------
    # TAB 3: TƯƠNG QUAN ĐA TÀI SẢN (CROSS-ASSET)
    # ---------------------------------------------------------------------------
    with tab_cross:
        st.subheader("🌐 Phân tích Tương quan Đa Thị trường (Stock & Crypto)")
        st.markdown(
            """
            Khu vực tích hợp so sánh liên thị trường:
            - **Đồng nhất Hệ quy chiếu VAM**: Quy chuẩn hóa Stock VAM Score (0 - 2) và Crypto VAM Score (0 - 100) về cùng thang đo chuẩn **0 – 100 điểm**.
            - **Tương quan đường giá**: So sánh trực tiếp VN-Index và Bitcoin (BTC).
            - **Tương quan Biến động & Thanh khoản**: Đo lường rủi ro dao động Volatility giữa hai thị trường.
            """
        )

        if has_stock and has_crypto:
            s_run_col = resolve_column(stock_df, ["run_id", "timestamp"]) or "datetime"
            s_score_col = resolve_column(stock_df, ["valuation_score", "score", "vam_score"])
            s_price_col = resolve_column(stock_df, ["vnindex_price", "price_current", "price", "close"])
            s_vol_col = resolve_column(stock_df, ["volatility_current", "vol_ratio", "volatility"])

            c_run_col = resolve_column(crypto_df, ["run_id", "timestamp"]) or "datetime"
            c_score_col = resolve_column(crypto_df, ["vam_market_final", "vam_market_composite", "market_composite", "vam_composite", "score"])
            c_asset_col = resolve_column(crypto_df, ["asset", "asset_name", "name", "coin"])
            c_price_col = resolve_column(crypto_df, ["price_usd", "price", "current_price", "close"])
            c_vol_col = resolve_column(crypto_df, ["vol_score", "flow_score", "mvrv_ratio"])

            # 1. Trích xuất bảng Stock đồng nhất
            stock_clean = stock_df.drop_duplicates(subset=[s_run_col]).copy()
            if s_score_col: stock_clean["raw_stock_score"] = clean_to_numeric(stock_clean[s_score_col])
            else: stock_clean["raw_stock_score"] = np.nan
            if s_price_col: stock_clean["vnindex"] = clean_to_numeric(stock_clean[s_price_col])
            else: stock_clean["vnindex"] = np.nan
            if s_vol_col: stock_clean["stock_vol"] = clean_to_numeric(stock_clean[s_vol_col])
            else: stock_clean["stock_vol"] = np.nan

            # Chuẩn hóa Stock VAM Score sang thang 0 - 100
            stock_clean["stock_vam_norm"] = np.clip(stock_clean["raw_stock_score"] * 50.0, 0.0, 100.0)

            # 2. Trích xuất bảng Crypto đồng nhất
            crypto_clean_runs = crypto_df.drop_duplicates(subset=[c_run_col]).copy()
            if c_score_col: crypto_clean_runs["crypto_vam_norm"] = clean_to_numeric(crypto_clean_runs[c_score_col])
            else: crypto_clean_runs["crypto_vam_norm"] = np.nan

            btc_rows = crypto_df[
                crypto_df[c_asset_col].astype(str).str.upper().str.contains(r"\bBTC\b|BITCOIN") &
                ~crypto_df[c_asset_col].astype(str).str.upper().str.contains(r"WBTC|WRAP")
            ].copy()
            
            if not btc_rows.empty:
                if c_price_col: btc_rows["btc_price"] = clean_to_numeric(btc_rows[c_price_col])
                else: btc_rows["btc_price"] = np.nan
                if c_vol_col: btc_rows["crypto_vol"] = clean_to_numeric(btc_rows[c_vol_col])
                else: btc_rows["crypto_vol"] = np.nan

                btc_price_map = btc_rows.dropna(subset=["btc_price"]).groupby(c_run_col)["btc_price"].last().to_dict()
                btc_vol_map = btc_rows.dropna(subset=["crypto_vol"]).groupby(c_run_col)["crypto_vol"].last().to_dict()
                
                crypto_clean_runs["btc_price"] = crypto_clean_runs[c_run_col].map(btc_price_map)
                crypto_clean_runs["crypto_vol"] = crypto_clean_runs[c_run_col].map(btc_vol_map)
            else:
                crypto_clean_runs["btc_price"] = np.nan
                crypto_clean_runs["crypto_vol"] = np.nan

            s_merge = stock_clean[["datetime", "stock_vam_norm", "raw_stock_score", "vnindex", "stock_vol"]].dropna(subset=["datetime"])
            c_merge = crypto_clean_runs[["datetime", "crypto_vam_norm", "btc_price", "crypto_vol"]].dropna(subset=["datetime"])

            merged_cross = pd.merge_asof(
                s_merge.sort_values("datetime"),
                c_merge.sort_values("datetime"),
                on="datetime",
                direction="nearest",
                tolerance=pd.Timedelta("24h")
            ).dropna(subset=["stock_vam_norm", "crypto_vam_norm"])

            if not merged_cross.empty:
                # ---------------------------------------------------------------
                # BIỂU ĐỒ 1: ĐỐI CHIẾU VAM SCORE ĐỒNG NHẤT (CHUẨN HÓA 0 - 100)
                # ---------------------------------------------------------------
                st.markdown("#### 1. Đối chiếu VAM Score Đồng nhất (Chuẩn hóa thang 0 – 100)")
                st.caption("💡 *Stock VAM Score (gốc 0-2) được quy đổi sang thang 0-100 (x50). Cả hai đều có điểm cân bằng là 50 điểm.*")

                fig_vam_unified = go.Figure()
                mode_cross = "lines+markers" if len(merged_cross) > 1 else "markers"

                # ÁP DỤNG 3 DẢI NỀN ĐỊNH LƯỢNG VÀO TRỤC CHÍNH Y
                apply_vam_background_bands(fig_vam_unified, yref="y")

                # Đường TTCK
                fig_vam_unified.add_trace(
                    go.Scatter(
                        x=merged_cross["datetime"],
                        y=merged_cross["stock_vam_norm"],
                        mode=mode_cross,
                        name="TTCK VAM Score (Chuẩn hóa 0-100)",
                        line=dict(color="#2563EB", width=3),
                        marker=dict(size=7),
                        hovertemplate="TTCK VAM (Norm): %{y:.1f} / 100<br>Điểm gốc: %{customdata:.2f}<extra></extra>",
                        customdata=merged_cross["raw_stock_score"]
                    )
                )

                # Đường Crypto
                fig_vam_unified.add_trace(
                    go.Scatter(
                        x=merged_cross["datetime"],
                        y=merged_cross["crypto_vam_norm"],
                        mode=mode_cross,
                        name="Crypto VAM Score Final (0-100)",
                        line=dict(color="#F59E0B", width=3),
                        marker=dict(size=7),
                        hovertemplate="Crypto VAM: %{y:.1f} / 100<extra></extra>"
                    )
                )

                fig_vam_unified.update_layout(
                    autosize=True,
                    height=390,
                    yaxis=dict(title="Thang điểm VAM Chuẩn hóa (0 - 100)", range=[0, 100]),
                    xaxis=dict(title="Thời gian"),
                    hovermode="x unified",
                    margin=dict(l=15, r=15, t=35, b=25),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5, font=dict(size=10.5))
                )
                st.plotly_chart(fig_vam_unified, use_container_width=True, config=PLOTLY_RESPONSIVE_CONFIG)

                # ---------------------------------------------------------------
                # BIỂU ĐỒ 2: ĐỐI CHIẾU ĐƯỜNG GIÁ: VN-INDEX VS BITCOIN (BTC)
                # ---------------------------------------------------------------
                st.markdown("#### 2. Tương quan Đường giá Song hành: VN-Index vs. Bitcoin (BTC)")
                fig_price_dual = make_subplots(specs=[[{"secondary_y": True}]])

                has_vnindex_data = not merged_cross["vnindex"].dropna().empty
                has_btc_data = not merged_cross["btc_price"].dropna().empty

                if has_vnindex_data:
                    fig_price_dual.add_trace(
                        go.Scatter(
                            x=merged_cross["datetime"],
                            y=merged_cross["vnindex"],
                            mode=mode_cross,
                            name="VN-Index (Điểm)",
                            line=dict(color="#38BDF8", width=2.5),
                            marker=dict(size=6)
                        ),
                        secondary_y=False
                    )

                if has_btc_data:
                    fig_price_dual.add_trace(
                        go.Scatter(
                            x=merged_cross["datetime"],
                            y=merged_cross["btc_price"],
                            mode=mode_cross,
                            name="Bitcoin Price ($ USD)",
                            line=dict(color="#F59E0B", width=2.5, dash="dot"),
                            marker=dict(size=6)
                        ),
                        secondary_y=True
                    )

                fig_price_dual.update_layout(
                    autosize=True,
                    height=390,
                    hovermode="x unified",
                    margin=dict(l=15, r=15, t=35, b=25),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5, font=dict(size=10.5))
                )
                fig_price_dual.update_yaxes(title_text="Điểm số VN-Index", secondary_y=False)
                fig_price_dual.update_yaxes(title_text="Giá BTC ($ USD)", secondary_y=True)
                st.plotly_chart(fig_price_dual, use_container_width=True, config=PLOTLY_RESPONSIVE_CONFIG)

                # ---------------------------------------------------------------
                # BIỂU ĐỒ 3: BIẾN ĐỘNG (VOLATILITY) & THANH KHOẢN ĐA THỊ TRƯỜNG
                # ---------------------------------------------------------------
                st.markdown("#### 3. Tương quan Biến động (Volatility) & Thanh khoản Liên thị trường")
                fig_vol_dual = make_subplots(specs=[[{"secondary_y": True}]])

                has_stock_vol = not merged_cross["stock_vol"].dropna().empty
                has_crypto_vol = not merged_cross["crypto_vol"].dropna().empty

                if has_stock_vol:
                    fig_vol_dual.add_trace(
                        go.Scatter(
                            x=merged_cross["datetime"],
                            y=merged_cross["stock_vol"],
                            mode=mode_cross,
                            name="Volatility VN-Index (%)",
                            line=dict(color="#A855F7", width=2.2),
                            marker=dict(size=6)
                        ),
                        secondary_y=False
                    )

                if has_crypto_vol:
                    fig_vol_dual.add_trace(
                        go.Scatter(
                            x=merged_cross["datetime"],
                            y=merged_cross["crypto_vol"],
                            mode=mode_cross,
                            name="Crypto Vol/Momentum Score",
                            line=dict(color="#EC4899", width=2.2, dash="dash"),
                            marker=dict(size=6)
                        ),
                        secondary_y=True
                    )

                fig_vol_dual.update_layout(
                    autosize=True,
                    height=360,
                    hovermode="x unified",
                    margin=dict(l=15, r=15, t=35, b=25),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5, font=dict(size=10.5))
                )
                fig_vol_dual.update_yaxes(title_text="Stock Volatility (%)", secondary_y=False)
                fig_vol_dual.update_yaxes(title_text="Crypto Vol Score", secondary_y=True)
                st.plotly_chart(fig_vol_dual, use_container_width=True, config=PLOTLY_RESPONSIVE_CONFIG)

                # BẢNG DỮ LIỆU ĐỐI CHIẾU
                with st.expander("📋 Xem bảng dữ liệu đối chiếu đa tài sản chi tiết", expanded=False):
                    df_display_cross = pd.DataFrame({
                        "Thời gian": merged_cross["datetime"].dt.strftime("%Y-%m-%d %H:%M"),
                        "Stock VAM (0-100)": merged_cross["stock_vam_norm"].round(1),
                        "Crypto VAM Final (0-100)": merged_cross["crypto_vam_norm"].round(1),
                        "Độ lệch VAM": (merged_cross["crypto_vam_norm"] - merged_cross["stock_vam_norm"]).round(1),
                        "VN-Index": merged_cross["vnindex"].apply(lambda x: f"{x:,.1f}" if pd.notnull(x) else "N/A"),
                        "Giá BTC ($)": merged_cross["btc_price"].apply(lambda x: f"${x:,.1f}" if pd.notnull(x) else "N/A"),
                    })
                    st.dataframe(df_display_cross, use_container_width=True, hide_index=True)

            else:
                st.info("Chưa tìm thấy các phiên ghi nhận đồng thời giữa TTCK và Crypto trong cùng khoảng thời gian 24 giờ để lập biểu đồ đối chiếu chéo.")
        else:
            st.info("Cần có dữ liệu hợp lệ ở cả hai file `stock_database.csv` và `crypto_database.csv` để lập chuỗi so sánh đa thị trường.")