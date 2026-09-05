import os
import json
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone

# ============================================================
# CẤU HÌNH API & FAILOVER POOL
# ============================================================
BINANCE_SPOT_API = "https://api.binance.com/api/v3"
BINANCE_FUTURES_API = "https://fapi.binance.com/fapi/v1"
COINGECKO_API = "https://api.coingecko.com/api/v3"
CRYPTO_DB_FILE = "crypto_database.csv"

PUBLIC_ETH_RPCS = [
    "https://cloudflare-eth.com",
    "https://ethereum-rpc.publicnode.com",
    "https://1rpc.io/eth",
    "https://eth.llamarpc.com"
]

EXCLUDED_CATEGORIES = {
    "btc", "paxg", "usdt", "usdc", "fdusd", "dai", "tusd", "busd", "usde", "pyusd",
    "wbtc", "weth", "weeth", "wsteth", "steth", "ezeth", "cbeth", "reth"
}

REBALANCE_THRESHOLD_PCT = 0.05


# ============================================================
# MODULE 1: BỘ THU THẬP DỮ LIỆU TỰ ĐỘNG
# ============================================================
class ComprehensiveDataLoader:

    @staticmethod
    def get_top_5_altcoins_by_marketcap() -> list[dict]:
        try:
            url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=35&page=1&sparkline=false"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            res = requests.get(url, headers=headers, timeout=12).json()

            selected = []
            for coin in res:
                sym = coin.get("symbol", "").lower()
                name = coin.get("id", "").lower()

                if sym in EXCLUDED_CATEGORIES or name in EXCLUDED_CATEGORIES:
                    continue
                if "usd" in sym or "wrapped" in name or "staked" in name:
                    continue

                binance_pair = f"{sym.upper()}USDT"
                selected.append({
                    "symbol": binance_pair,
                    "name": sym.upper(),
                    "market_cap": float(coin.get("market_cap", 0.0))
                })

                if len(selected) == 5:
                    break

            if len(selected) == 5:
                return selected

        except Exception as e:
            print(f"  [Cảnh báo] Lỗi truy vấn CoinGecko ({e}), dùng danh sách chuẩn...")

        return [
            {"symbol": "ETHUSDT", "name": "ETH", "market_cap": 300e9},
            {"symbol": "BNBUSDT", "name": "BNB", "market_cap": 90e9},
            {"symbol": "SOLUSDT", "name": "SOL", "market_cap": 60e9},
            {"symbol": "XRPUSDT", "name": "XRP", "market_cap": 50e9},
            {"symbol": "ADAUSDT", "name": "ADA", "market_cap": 25e9}
        ]

    @staticmethod
    def get_binance_klines(symbol: str, limit: int = 365) -> pd.DataFrame:
        url = f"{BINANCE_SPOT_API}/klines?symbol={symbol}&interval=1d&limit={limit}"
        res = requests.get(url, timeout=12).json()
        df = pd.DataFrame(res, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "taker_buy_base", "taker_buy_quote", "ignore"
        ])
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        df["taker_buy_base"] = df["taker_buy_base"].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        return df[["open_time", "close", "volume", "taker_buy_base"]].sort_values("open_time").reset_index(drop=True)

    @staticmethod
    def get_derivatives_funding_rate(symbol: str = "BTCUSDT") -> float:
        try:
            url = f"{BINANCE_FUTURES_API}/fundingRate?symbol={symbol}&limit=1"
            res = requests.get(url, timeout=8).json()
            if res and isinstance(res, list):
                return float(res[0].get("fundingRate", 0.0001))
        except Exception:
            pass
        return 0.0001

    @staticmethod
    def get_realtime_onchain_metrics(num_blocks: int = 5) -> dict:
        headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}

        for rpc_url in PUBLIC_ETH_RPCS:
            try:
                payload_head = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
                r_head = requests.post(rpc_url, json=payload_head, headers=headers, timeout=8)
                if r_head.status_code != 200:
                    continue

                latest_block = int(r_head.json()["result"], 16)
                total_txs = 0
                unique_addrs = set()

                for b in range(latest_block - num_blocks + 1, latest_block + 1):
                    payload_block = {
                        "jsonrpc": "2.0",
                        "method": "eth_getBlockByNumber",
                        "params": [hex(b), True],
                        "id": 1
                    }
                    r_blk = requests.post(rpc_url, json=payload_block, headers=headers, timeout=8)
                    if r_blk.status_code == 200:
                        txs = r_blk.json().get("result", {}).get("transactions", [])
                        total_txs += len(txs)
                        for tx in txs:
                            if tx.get("from"):
                                unique_addrs.add(tx["from"].lower())
                            if tx.get("to"):
                                unique_addrs.add(tx["to"].lower())

                if total_txs > 0:
                    node_name = rpc_url.split("//")[1].split("/")[0]
                    return {
                        "source": f"Live_RPC ({node_name})",
                        "blocks_scanned": num_blocks,
                        "latest_block": latest_block,
                        "eth_tx_count": total_txs,
                        "eth_active_addresses": len(unique_addrs)
                    }

            except Exception:
                continue

        return {
            "source": "Baseline_Fallback",
            "blocks_scanned": num_blocks,
            "latest_block": 21_000_000,
            "eth_tx_count": 1250,
            "eth_active_addresses": 980
        }


# ============================================================
# MODULE 2: THUẬT TOÁN ĐỊNH GIÁ ĐA TẦNG TOÀN DIỆN
# ============================================================
class CryptoVAMEngine:

    @staticmethod
    def calculate_price_deviation(close_series: pd.Series) -> tuple[float, float, float]:
        win_long = min(200, max(30, len(close_series) - 1))
        win_short = min(20, max(5, len(close_series) // 10))

        sma_short = close_series.rolling(window=win_short).mean()
        sma_long = close_series.rolling(window=win_long).mean()

        log_macro = np.log(sma_short / sma_long)
        log_local = np.log(close_series / sma_short)
        log_anchor = np.log(close_series / sma_long)

        raw_dev = (0.40 * log_macro) + (0.35 * log_local) + (0.25 * log_anchor)

        rolling_mean = raw_dev.rolling(window=len(raw_dev), min_periods=20).mean().iloc[-1]
        rolling_std = raw_dev.rolling(window=len(raw_dev), min_periods=20).std().iloc[-1]

        z = (raw_dev.iloc[-1] - rolling_mean) / (rolling_std + 1e-9)
        score = 1.0 / (1.0 + np.exp(-z)) * 100.0
        return float(np.clip(score, 0.0, 100.0)), float(sma_short.iloc[-1]), float(sma_long.iloc[-1])

    @staticmethod
    def calculate_mvrv_onchain(df_klines: pd.DataFrame) -> tuple[float, float, float]:
        closes = df_klines["close"]
        vols = df_klines["volume"]

        cum_vp = (closes * vols).sum()
        cum_vol = vols.sum()
        realized_price_est = float(cum_vp / (cum_vol + 1e-9))
        mvrv_ratio = float(closes.iloc[-1] / (realized_price_est + 1e-9))

        mvrv_score = (mvrv_ratio - 0.8) / (2.6 - 0.8) * 100.0
        mvrv_score = float(np.clip(mvrv_score, 0.0, 100.0))
        return mvrv_score, mvrv_ratio, realized_price_est

    @staticmethod
    def calculate_capital_flow(df_klines: pd.DataFrame) -> tuple[float, float]:
        closed_df = df_klines.iloc[:-1].copy()
        buy_vol = closed_df["taker_buy_base"]
        sell_vol = closed_df["volume"] - buy_vol
        flow_ratio = buy_vol / (sell_vol + 1e-9)

        rolling_mean = flow_ratio.rolling(window=60, min_periods=15).mean().iloc[-1]
        rolling_std = flow_ratio.rolling(window=60, min_periods=15).std().iloc[-1]
        current_flow = float(flow_ratio.iloc[-1])

        z = (current_flow - rolling_mean) / (rolling_std + 1e-9)
        flow_score = 1.0 / (1.0 + np.exp(-z)) * 100.0
        return float(np.clip(flow_score, 0.0, 100.0)), current_flow

    @staticmethod
    def calculate_volume_momentum(vol_series: pd.Series) -> tuple[float, float]:
        closed_day_vol = vol_series.iloc[-2]
        vol_ma20 = vol_series.iloc[-22:-2].mean()
        vol_ratio = float(closed_day_vol / (vol_ma20 + 1e-9))
        vol_score = float(np.clip(vol_ratio * 50.0, 10.0, 95.0))
        return vol_score, vol_ratio

    @staticmethod
    def calculate_paxg_risk_penalty(btc_series: pd.Series, paxg_series: pd.Series) -> tuple[float, float]:
        min_len = min(len(btc_series), len(paxg_series))
        ratio = paxg_series.iloc[-min_len:].values / btc_series.iloc[-min_len:].values
        ratio_series = pd.Series(ratio)

        rolling_mean = ratio_series.rolling(window=60, min_periods=15).mean().iloc[-1]
        rolling_std = ratio_series.rolling(window=60, min_periods=15).std().iloc[-1]
        current_ratio = float(ratio_series.iloc[-1])

        z = (current_ratio - rolling_mean) / (rolling_std + 1e-9)
        sigmoid_penalty = 1.0 / (1.0 + np.exp(-z))
        penalty = float(np.clip(sigmoid_penalty * 0.35, 0.0, 0.35))
        return penalty, current_ratio

    @staticmethod
    def calculate_derivatives_leverage_penalty(funding_rate: float) -> tuple[float, str]:
        if funding_rate > 0.0003:
            penalty = float(np.clip((funding_rate - 0.0003) / 0.0007 * 0.15, 0.03, 0.15))
            status = "Đòn bẩy Long quá nhiệt (Long Overheat Risk)"
        elif funding_rate < -0.0002:
            penalty = -0.05
            status = "Phe Short áp đảo (Short Squeeze Potential)"
        else:
            penalty = 0.0
            status = "Đòn bẩy cân bằng (Neutral Leverage)"
        return penalty, status

    @staticmethod
    def calculate_macro_allocation(btc_score: float, altcoin_score: float, combined_risk_penalty: float) -> dict:
        avg_score = (btc_score * 0.60) + (altcoin_score * 0.40)

        w_paxg = float(np.clip(0.03 + (combined_risk_penalty * 0.50), 0.03, 0.20))
        w_usdt = float(np.clip((avg_score / 100.0) * 0.55, 0.10, 0.55))

        defense_sum = w_paxg + w_usdt
        if defense_sum > 0.70:
            scale = 0.70 / defense_sum
            w_paxg *= scale
            w_usdt *= scale

        risk_capital = 1.0 - (w_paxg + w_usdt)

        inv_btc = (100.0 - btc_score) / 100.0
        inv_alt = (100.0 - altcoin_score) / 100.0
        sum_inv = inv_btc + inv_alt

        w_btc = (inv_btc / sum_inv) * risk_capital
        w_alt = (inv_alt / sum_inv) * risk_capital

        return {
            "BTC": w_btc,
            "Altcoins (Top 5 Market Cap)": w_alt,
            "PAXG": w_paxg,
            "USDT": w_usdt
        }


# ============================================================
# MODULE 3: HÀM QUẢN TRỊ FILE DATABASE CSV (LOAD & SAVE)
# ============================================================
def load_crypto_database(db_path: str = CRYPTO_DB_FILE) -> pd.DataFrame:
    """Đọc dữ liệu từ file CSV, nếu chưa có sẽ trả về DataFrame rỗng."""
    if os.path.exists(db_path):
        try:
            return pd.read_csv(db_path, encoding="utf-8-sig")
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

def save_crypto_database(data: dict, db_path: str = CRYPTO_DB_FILE) -> bool:
    """Ghi dữ liệu phiên tính toán vào crypto_database.csv."""
    try:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        ts_local = data.get("timestamp_local", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        ts_utc = data.get("timestamp", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        alloc = data.get("allocations", {})

        records = []

        # 1. Chi tiết từng coin (BTC + Top 5 Altcoins)
        for item in data.get("asset_details", []):
            name = item["name"]
            is_btc = (name == "BTC")
            tgt_w = alloc.get("BTC", 0.0) if is_btc else (alloc.get("Altcoins (Top 5 Market Cap)", 0.0) / 5.0)

            records.append({
                "run_id": run_id,
                "timestamp": ts_local,
                "timestamp_utc": ts_utc,
                "asset": name,
                "category": item.get("category", "Altcoin"),
                "price": round(float(item.get("price", 0.0)), 4),
                "sma20": round(float(item.get("sma20", 0.0)), 4),
                "sma200": round(float(item.get("sma200", 0.0)), 4),
                "realized_vwap": round(float(item.get("realized_price", 0.0)), 4),
                "p_dev": round(float(item.get("p_dev", 0.0)), 2),
                "mvrv_ratio": round(float(item.get("mvrv_ratio", 0.0)), 4),
                "mvrv_score": round(float(item.get("mvrv_score", 0.0)), 2),
                "flow_ratio": round(float(item.get("flow_ratio", 0.0)), 4),
                "flow_score": round(float(item.get("flow_score", 0.0)), 2),
                "vol_ratio": round(float(item.get("vol_ratio", 0.0)), 4),
                "vol_score": round(float(item.get("vol_score", 0.0)), 2),
                "v_internal": round(float(item.get("v_internal", 0.0)), 2),
                "vam_final": round(float(item.get("vam_final", 0.0)), 2),
                "status": item.get("status", ""),
                "action": item.get("action", ""),
                "target_weight_pct": round(float(tgt_w * 100.0), 2),
                "vam_market_final": round(float(data.get("vam_market_final", 0.0)), 2),
                "market_regime": data.get("market_regime", ""),
                "btc_score": round(float(data.get("btc_score", 0.0)), 2),
                "altcoin_index": round(float(data.get("altcoin_index", 0.0)), 2),
                "weight_btc_cap": round(float(data.get("weight_btc", 0.0) * 100.0), 2),
                "weight_alt_cap": round(float(data.get("weight_alt", 0.0) * 100.0), 2),
                "funding_rate_pct": round(float(data.get("funding_rate", 0.0) * 100.0), 4),
                "leverage_status": data.get("leverage_status", ""),
                "leverage_penalty_pct": round(float(data.get("leverage_penalty", 0.0) * 100.0), 2),
                "gold_ratio_paxg_btc": round(float(data.get("gold_ratio", 0.0)), 6),
                "risk_paxg_pct": round(float(data.get("risk_paxg", 0.0) * 100.0), 2),
                "total_risk_penalty_pct": round(float(data.get("total_risk", 0.0) * 100.0), 2),
                "target_alloc_btc_pct": round(float(alloc.get("BTC", 0.0) * 100.0), 2),
                "target_alloc_alt_pct": round(float(alloc.get("Altcoins (Top 5 Market Cap)", 0.0) * 100.0), 2),
                "target_alloc_paxg_pct": round(float(alloc.get("PAXG", 0.0) * 100.0), 2),
                "target_alloc_usdt_pct": round(float(alloc.get("USDT", 0.0) * 100.0), 2),
                "eth_rpc_source": data.get("onchain", {}).get("source", ""),
                "eth_latest_block": data.get("onchain", {}).get("latest_block", 0),
                "eth_tx_count_5b": data.get("onchain", {}).get("eth_tx_count", 0),
                "eth_active_addrs_5b": data.get("onchain", {}).get("eth_active_addresses", 0),
                "top_5_basket": ",".join(data.get("top_5_names", []))
            })

        # 2. PAXG
        records.append({
            "run_id": run_id, "timestamp": ts_local, "timestamp_utc": ts_utc,
            "asset": "PAXG", "category": "Hedge",
            "price": round(float(data.get("raw_prices", {}).get("PAXG", 0.0)), 2),
            "sma20": np.nan, "sma200": np.nan, "realized_vwap": np.nan,
            "p_dev": np.nan, "mvrv_ratio": np.nan, "mvrv_score": np.nan,
            "flow_ratio": np.nan, "flow_score": np.nan, "vol_ratio": np.nan, "vol_score": np.nan,
            "v_internal": np.nan, "vam_final": np.nan,
            "status": "Phòng thủ Vĩ mô", "action": "Bảo hiểm tài sản",
            "target_weight_pct": round(float(alloc.get("PAXG", 0.0) * 100.0), 2),
            "vam_market_final": round(float(data.get("vam_market_final", 0.0)), 2),
            "market_regime": data.get("market_regime", ""),
            "btc_score": round(float(data.get("btc_score", 0.0)), 2),
            "altcoin_index": round(float(data.get("altcoin_index", 0.0)), 2),
            "weight_btc_cap": round(float(data.get("weight_btc", 0.0) * 100.0), 2),
            "weight_alt_cap": round(float(data.get("weight_alt", 0.0) * 100.0), 2),
            "funding_rate_pct": round(float(data.get("funding_rate", 0.0) * 100.0), 4),
            "leverage_status": data.get("leverage_status", ""),
            "leverage_penalty_pct": round(float(data.get("leverage_penalty", 0.0) * 100.0), 2),
            "gold_ratio_paxg_btc": round(float(data.get("gold_ratio", 0.0)), 6),
            "risk_paxg_pct": round(float(data.get("risk_paxg", 0.0) * 100.0), 2),
            "total_risk_penalty_pct": round(float(data.get("total_risk", 0.0) * 100.0), 2),
            "target_alloc_btc_pct": round(float(alloc.get("BTC", 0.0) * 100.0), 2),
            "target_alloc_alt_pct": round(float(alloc.get("Altcoins (Top 5 Market Cap)", 0.0) * 100.0), 2),
            "target_alloc_paxg_pct": round(float(alloc.get("PAXG", 0.0) * 100.0), 2),
            "target_alloc_usdt_pct": round(float(alloc.get("USDT", 0.0) * 100.0), 2),
            "eth_rpc_source": data.get("onchain", {}).get("source", ""),
            "eth_latest_block": data.get("onchain", {}).get("latest_block", 0),
            "eth_tx_count_5b": data.get("onchain", {}).get("eth_tx_count", 0),
            "eth_active_addrs_5b": data.get("onchain", {}).get("eth_active_addresses", 0),
            "top_5_basket": ",".join(data.get("top_5_names", []))
        })

        # 3. USDT
        records.append({
            "run_id": run_id, "timestamp": ts_local, "timestamp_utc": ts_utc,
            "asset": "USDT", "category": "Cash", "price": 1.0,
            "sma20": np.nan, "sma200": np.nan, "realized_vwap": np.nan,
            "p_dev": np.nan, "mvrv_ratio": np.nan, "mvrv_score": np.nan,
            "flow_ratio": np.nan, "flow_score": np.nan, "vol_ratio": np.nan, "vol_score": np.nan,
            "v_internal": np.nan, "vam_final": np.nan,
            "status": "Thanh khoản Chờ Gom", "action": "Dry Powder",
            "target_weight_pct": round(float(alloc.get("USDT", 0.0) * 100.0), 2),
            "vam_market_final": round(float(data.get("vam_market_final", 0.0)), 2),
            "market_regime": data.get("market_regime", ""),
            "btc_score": round(float(data.get("btc_score", 0.0)), 2),
            "altcoin_index": round(float(data.get("altcoin_index", 0.0)), 2),
            "weight_btc_cap": round(float(data.get("weight_btc", 0.0) * 100.0), 2),
            "weight_alt_cap": round(float(data.get("weight_alt", 0.0) * 100.0), 2),
            "funding_rate_pct": round(float(data.get("funding_rate", 0.0) * 100.0), 4),
            "leverage_status": data.get("leverage_status", ""),
            "leverage_penalty_pct": round(float(data.get("leverage_penalty", 0.0) * 100.0), 2),
            "gold_ratio_paxg_btc": round(float(data.get("gold_ratio", 0.0)), 6),
            "risk_paxg_pct": round(float(data.get("risk_paxg", 0.0) * 100.0), 2),
            "total_risk_penalty_pct": round(float(data.get("total_risk", 0.0) * 100.0), 2),
            "target_alloc_btc_pct": round(float(alloc.get("BTC", 0.0) * 100.0), 2),
            "target_alloc_alt_pct": round(float(alloc.get("Altcoins (Top 5 Market Cap)", 0.0) * 100.0), 2),
            "target_alloc_paxg_pct": round(float(alloc.get("PAXG", 0.0) * 100.0), 2),
            "target_alloc_usdt_pct": round(float(alloc.get("USDT", 0.0) * 100.0), 2),
            "eth_rpc_source": data.get("onchain", {}).get("source", ""),
            "eth_latest_block": data.get("onchain", {}).get("latest_block", 0),
            "eth_tx_count_5b": data.get("onchain", {}).get("eth_tx_count", 0),
            "eth_active_addrs_5b": data.get("onchain", {}).get("eth_active_addresses", 0),
            "top_5_basket": ",".join(data.get("top_5_names", []))
        })

        df_new = pd.DataFrame(records)
        if not os.path.exists(db_path):
            df_new.to_csv(db_path, mode="w", index=False, encoding="utf-8-sig")
        else:
            df_new.to_csv(db_path, mode="a", header=False, index=False, encoding="utf-8-sig")
        return True
    except Exception as e:
        print(f"  [Lỗi Database] {e}")
        return False


# ============================================================
# MODULE 4: HÀM CHÍNH CHO STREAMLIT & BOT
# ============================================================
def run_vam_analysis(log_func=print, auto_save: bool = True) -> dict:
    loader = ComprehensiveDataLoader()
    engine = CryptoVAMEngine()

    top_5_data = loader.get_top_5_altcoins_by_marketcap()
    top_5_altcoins = [c["symbol"] for c in top_5_data]
    total_alt_cap = sum(c["market_cap"] for c in top_5_data)

    assets_to_fetch = ["BTCUSDT", "PAXGUSDT"] + top_5_altcoins
    data = {}
    for sym in assets_to_fetch:
        data[sym] = loader.get_binance_klines(sym, limit=365)

    onchain = loader.get_realtime_onchain_metrics(num_blocks=5)
    funding_rate = loader.get_derivatives_funding_rate("BTCUSDT")
    leverage_penalty, leverage_status = engine.calculate_derivatives_leverage_penalty(funding_rate)
    risk_paxg, gold_ratio = engine.calculate_paxg_risk_penalty(data["BTCUSDT"]["close"], data["PAXGUSDT"]["close"])
    total_risk_penalty = float(np.clip(risk_paxg + leverage_penalty, 0.0, 0.40))

    altcoin_scores = []
    asset_details = []
    intermediate_details = []

    all_target_coins = [("BTC", "BTCUSDT")] + [(c["name"], c["symbol"]) for c in top_5_data]

    for name, sym in all_target_coins:
        df_coin = data[sym]
        close = df_coin["close"]
        vol = df_coin["volume"]

        p_dev, sma20, sma200 = engine.calculate_price_deviation(close)
        mvrv_score, mvrv_ratio, realized_price = engine.calculate_mvrv_onchain(df_coin)
        flow_score, cur_flow_ratio = engine.calculate_capital_flow(df_coin)
        vol_score, cur_vol_ratio = engine.calculate_volume_momentum(vol)

        v_internal = (0.35 * p_dev) + (0.30 * mvrv_score) + (0.20 * flow_score) + (0.15 * vol_score)
        final_vam = v_internal * (1.0 - total_risk_penalty)

        if name == "BTC":
            btc_score = final_vam
        else:
            altcoin_scores.append(final_vam)

        if final_vam < 35.0:
            status = "ĐỊNH GIÁ THẤP"
            action = "TÍCH LŨY MẠNH (+Tỷ trọng)"
        elif final_vam > 68.0:
            status = "ĐỊNH GIÁ CAO"
            action = "HẠ TỶ TRỌNG (Take Profit)"
        else:
            status = "CÂN BẰNG"
            action = "GIỮ NGUYÊN TỶ TRỌNG CHUẨN"

        asset_details.append({
            "name": name,
            "category": "Core" if name == "BTC" else "Altcoin",
            "price": close.iloc[-1],
            "sma20": sma20,
            "sma200": sma200,
            "p_dev": p_dev,
            "mvrv_score": mvrv_score,
            "mvrv_ratio": mvrv_ratio,
            "flow_score": flow_score,
            "flow_ratio": cur_flow_ratio,
            "vol_score": vol_score,
            "vol_ratio": cur_vol_ratio,
            "v_internal": v_internal,
            "vam_final": final_vam,
            "status": status,
            "action": action,
            "realized_price": realized_price
        })

        intermediate_details.append({
            "Tài sản": name,
            "SMA20": f"${sma20:,.2f}",
            "SMA200": f"${sma200:,.2f}",
            "Realized VWAP": f"${realized_price:,.2f}",
            "MVRV Ratio": f"{mvrv_ratio:.2f}x",
            "Flow Ratio (B/S)": f"{cur_flow_ratio:.2f}",
            "Vol/MA20": f"{cur_vol_ratio:.2f}x"
        })

    altcoin_index_score = float(np.mean(altcoin_scores))
    allocations = engine.calculate_macro_allocation(btc_score, altcoin_index_score, total_risk_penalty)

    btc_est_cap = data["BTCUSDT"]["close"].iloc[-1] * 19.8e6
    weight_btc = float(np.clip(btc_est_cap / (btc_est_cap + total_alt_cap + 1e-9), 0.45, 0.70))
    weight_alt = 1.0 - weight_btc

    vam_market_final = (weight_btc * btc_score) + (weight_alt * altcoin_index_score)

    if vam_market_final < 35.0:
        market_regime = "CHIẾT KHẤU CAO (Undervalued) -> Tích lũy tối đa tài sản rủi ro"
    elif vam_market_final > 68.0:
        market_regime = "QUÁ NHIỆT (Overvalued / Risk-off) -> Ưu tiên chốt lời về USDT/PAXG"
    else:
        market_regime = "CÂN BẰNG CHU KỲ (Fair Value) -> Giữ cấu trúc danh mục ổn định"

    results = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "timestamp_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "btc_score": btc_score,
        "altcoin_index": altcoin_index_score,
        "vam_market_final": vam_market_final,
        "market_regime": market_regime,
        "weight_btc": weight_btc,
        "weight_alt": weight_alt,
        "allocations": allocations,
        "asset_details": asset_details,
        "intermediate_details": intermediate_details,
        "top_5_names": [c["name"] for c in top_5_data],
        "funding_rate": funding_rate,
        "leverage_status": leverage_status,
        "leverage_penalty": leverage_penalty,
        "risk_paxg": risk_paxg,
        "total_risk": total_risk_penalty,
        "gold_ratio": gold_ratio,
        "onchain": onchain,
        "raw_prices": {
            "BTC": data["BTCUSDT"]["close"].iloc[-1],
            "PAXG": data["PAXGUSDT"]["close"].iloc[-1]
        }
    }

    if auto_save:
        save_crypto_database(results)

    return results


if __name__ == "__main__":
    res = run_vam_analysis()
    print("VAM Market Composite:", res["vam_market_final"])