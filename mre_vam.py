
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MRE-VAM 1.0
Macro Regime Engine + Valuation/Trend/Risk Asset Allocation Model

Purpose
-------
1) Download mostly-free macro/market data.
2) Build Growth / Inflation / Financial Conditions factors.
3) Detect 4 economic regimes with a Gaussian HMM.
4) Combine regime probability with valuation, trend, liquidity and risk.
5) Backtest a regime-aware portfolio against Buy & Hold.
6) Export CSV, JSON and PNG charts.

Data sources
------------
- FRED: macro series (requires free FRED API key)
- Stooq: market prices (free; availability may vary)
- Optional yfinance: useful for ETFs/indices if installed.

IMPORTANT
---------
This is a research/backtesting engine, not investment advice.
For production research, replace revised FRED observations with ALFRED
vintage observations and add transaction costs/slippage appropriate to
the target market.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

try:
    from fredapi import Fred
except ImportError:
    Fred = None

try:
    from hmmlearn.hmm import GaussianHMM
except ImportError:
    GaussianHMM = None


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

@dataclass
class Config:
    start_date: str = "1970-01-01"
    end_date: str | None = None
    output_dir: str = "mre_vam_output"
    hmm_states: int = 4
    z_window: int = 120
    min_train_months: int = 120
    rebalance_months: int = 1
    transaction_cost: float = 0.0015
    max_turnover: float = 0.50

    # Baseline long-term portfolio.
    base_equity: float = 0.60
    base_bond: float = 0.25
    base_gold: float = 0.10
    base_cash: float = 0.05


FRED_SERIES = {
    "industrial": "INDPRO",
    "unemployment": "UNRATE",
    "cpi": "CPIAUCSL",
    "activity": "CFNAI",
    "yield_curve": "T10Y2Y",
    "credit_spread": "BAA10Y",
    "financial_conditions": "NFCI",
    "m2": "M2SL",
    "fedfunds": "FEDFUNDS",
}

# Public Stooq tickers; change these if a local market is being tested.
MARKET_TICKERS = {
    "equity": "spy.us",
    "bond": "ief.us",
    "gold": "gld.us",
}


# ---------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------

def ensure_output(path: str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def annualized_return(r: pd.Series) -> float:
    r = r.dropna()
    if len(r) < 2:
        return np.nan
    years = len(r) / 12.0
    return (np.prod(1 + r) ** (1 / years)) - 1


def max_drawdown(r: pd.Series) -> float:
    wealth = (1 + r.fillna(0)).cumprod()
    dd = wealth / wealth.cummax() - 1
    return dd.min()


def sharpe_ratio(r: pd.Series, rf_monthly: float = 0.0) -> float:
    x = r.dropna() - rf_monthly
    if x.std(ddof=1) == 0:
        return np.nan
    return np.sqrt(12) * x.mean() / x.std(ddof=1)


def sortino_ratio(r: pd.Series) -> float:
    x = r.dropna()
    downside = x[x < 0].std(ddof=1)
    if downside == 0 or np.isnan(downside):
        return np.nan
    return np.sqrt(12) * x.mean() / downside


def cagr_from_returns(r: pd.Series) -> float:
    return annualized_return(r)


def portfolio_metrics(r: pd.Series) -> dict:
    r = r.dropna()
    return {
        "CAGR": cagr_from_returns(r),
        "Volatility": np.sqrt(12) * r.std(ddof=1),
        "Sharpe": sharpe_ratio(r),
        "Sortino": sortino_ratio(r),
        "MaxDrawdown": max_drawdown(r),
        "TotalReturn": (1 + r).prod() - 1,
        "Months": len(r),
    }


# ---------------------------------------------------------------------
# Data acquisition
# ---------------------------------------------------------------------

def fred_download(api_key: str, start_date: str) -> pd.DataFrame:
    if Fred is None:
        raise RuntimeError("Install fredapi: pip install fredapi")

    fred = Fred(api_key=api_key)
    data = {}

    for name, code in FRED_SERIES.items():
        try:
            s = fred.get_series(code, observation_start=start_date)
            s.index = pd.to_datetime(s.index)
            data[name] = s
            print(f"[FRED] {name:24s} {code}")
        except Exception as exc:
            print(f"[WARN] FRED {code} failed: {exc}")

    if not data:
        raise RuntimeError("No FRED series could be downloaded.")

    df = pd.concat(data, axis=1)
    df = df.resample("ME").last().ffill()
    return df


def market_download_stooq(start_date: str) -> pd.DataFrame:
    """
    Uses Stooq's public CSV endpoint.
    If Stooq blocks/changes the endpoint, use --market-source yfinance.
    """
    import urllib.parse
    import urllib.request
    from io import StringIO

    out = {}

    start = pd.Timestamp(start_date).strftime("%Y%m%d")
    end = pd.Timestamp.today().strftime("%Y%m%d")

    for asset, ticker in MARKET_TICKERS.items():
        url = (
            "https://stooq.com/q/d/l/?"
            + urllib.parse.urlencode({
                "s": ticker,
                "d1": start,
                "d2": end,
                "i": "d",
            })
        )

        try:
            raw = urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
            px = pd.read_csv(StringIO(raw))
            if "Date" not in px.columns or "Close" not in px.columns:
                raise ValueError("Unexpected Stooq response")

            px["Date"] = pd.to_datetime(px["Date"])
            px = px.set_index("Date")["Close"].replace(0, np.nan)
            out[asset] = px
            print(f"[STOOQ] {asset:8s} {ticker}")
        except Exception as exc:
            print(f"[WARN] Stooq {ticker} failed: {exc}")

    if not out:
        raise RuntimeError("No market series could be downloaded.")

    return pd.concat(out, axis=1).resample("ME").last().ffill()


def market_download_yfinance(start_date: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("Install yfinance: pip install yfinance")

    tickers = {
        "equity": "SPY",
        "bond": "IEF",
        "gold": "GLD",
    }

    data = {}
    for asset, ticker in tickers.items():
        x = yf.download(
            ticker,
            start=start_date,
            auto_adjust=True,
            progress=False,
        )
        if isinstance(x.columns, pd.MultiIndex):
            close = x["Close"].iloc[:, 0]
        else:
            close = x["Close"]
        data[asset] = close

    return pd.concat(data, axis=1).resample("ME").last().ffill()


# ---------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------

def annualized_change(s: pd.Series, months: int = 3) -> pd.Series:
    return ((s / s.shift(months)) ** (12 / months) - 1) * 100


def apply_release_lag(df: pd.DataFrame) -> pd.DataFrame:
    """
    Conservative 1-month lag for macro observations.
    Market/financial-condition variables are left contemporaneous.
    """
    x = df.copy()

    for col in [
        "industrial",
        "unemployment",
        "cpi",
        "activity",
        "m2",
    ]:
        if col in x:
            x[col] = x[col].shift(1)

    return x


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=df.index)

    f["ip_yoy"] = df["industrial"].pct_change(12) * 100
    f["ip_3m"] = annualized_change(df["industrial"], 3)

    # Unemployment rising is negative for growth.
    f["unemployment_change"] = -(
        df["unemployment"] - df["unemployment"].shift(3)
    )

    f["activity"] = df["activity"]

    f["inflation_yoy"] = df["cpi"].pct_change(12) * 100
    f["inflation_3m"] = annualized_change(df["cpi"], 3)

    f["yield_curve"] = df["yield_curve"]
    f["credit"] = -df["credit_spread"]
    f["financial"] = -df["financial_conditions"]
    f["m2_yoy"] = df["m2"].pct_change(12) * 100

    return f


def rolling_zscore(s: pd.Series, window: int) -> pd.Series:
    mu = s.rolling(window, min_periods=max(36, window // 2)).mean().shift(1)
    sd = s.rolling(window, min_periods=max(36, window // 2)).std().shift(1)
    z = (s - mu) / sd.replace(0, np.nan)
    return z.clip(-3, 3)


def normalize_features(f: pd.DataFrame, window: int) -> pd.DataFrame:
    z = pd.DataFrame(index=f.index)
    for col in f.columns:
        z[col] = rolling_zscore(f[col], window)
    return z


def build_factors(z: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=z.index)

    out["growth"] = (
        0.30 * z["ip_yoy"]
        + 0.25 * z["ip_3m"]
        + 0.20 * z["unemployment_change"]
        + 0.25 * z["activity"]
    )

    out["inflation"] = (
        0.60 * z["inflation_yoy"]
        + 0.40 * z["inflation_3m"]
    )

    out["financial"] = (
        0.30 * z["yield_curve"]
        + 0.30 * z["credit"]
        + 0.25 * z["financial"]
        + 0.15 * z["m2_yoy"]
    )

    out["growth_momentum"] = (
        out["growth"] - out["growth"].shift(3)
    )

    return out.dropna()


# ---------------------------------------------------------------------
# HMM
# ---------------------------------------------------------------------

def fit_hmm(factors: pd.DataFrame, n_states: int = 4):
    if GaussianHMM is None:
        raise RuntimeError("Install hmmlearn: pip install hmmlearn")

    cols = ["growth", "growth_momentum", "inflation", "financial"]
    X = factors[cols].values

    # Already z-scored factors, but scaling improves numerical stability.
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1
    Xs = (X - mean) / std

    model = GaussianHMM(
        n_components=n_states,
        covariance_type="diag",
        n_iter=1000,
        tol=1e-4,
        random_state=42,
        init_params="stmc",
    )
    model.fit(Xs)

    states = model.predict(Xs)
    probs = model.predict_proba(Xs)

    return model, states, probs


def map_states(factors: pd.DataFrame, states: np.ndarray) -> dict:
    x = factors.copy()
    x["state"] = states

    stats = x.groupby("state")[
        ["growth", "growth_momentum", "inflation", "financial"]
    ].mean()

    stats["cycle_score"] = (
        0.55 * stats["growth"]
        + 0.25 * stats["growth_momentum"]
        + 0.20 * stats["financial"]
    )

    expansion = stats["cycle_score"].idxmax()
    recession = stats["cycle_score"].idxmin()

    remaining = [s for s in stats.index if s not in [expansion, recession]]

    if len(remaining) == 2:
        recovery = stats.loc[remaining, "growth_momentum"].idxmax()
        slowdown = [s for s in remaining if s != recovery][0]
    else:
        recovery, slowdown = remaining[0], remaining[-1]

    return {
        expansion: "EXPANSION",
        recovery: "RECOVERY",
        slowdown: "SLOWDOWN",
        recession: "RECESSION",
    }


# ---------------------------------------------------------------------
# VAM allocation
# ---------------------------------------------------------------------

REGIME_PORTFOLIOS = {
    "RECOVERY": {"equity": .75, "bond": .10, "gold": .10, "cash": .05},
    "EXPANSION": {"equity": .80, "bond": .05, "gold": .10, "cash": .05},
    "SLOWDOWN": {"equity": .50, "bond": .20, "gold": .20, "cash": .10},
    "RECESSION": {"equity": .25, "bond": .40, "gold": .25, "cash": .10},
}


def regime_probability_allocation(row: pd.Series) -> dict:
    w = {k: 0.0 for k in ["equity", "bond", "gold", "cash"]}

    for regime in REGIME_PORTFOLIOS:
        p = float(row.get(f"P_{regime}", 0.0))
        for asset in w:
            w[asset] += p * REGIME_PORTFOLIOS[regime][asset]

    return w


def trend_score(market_returns: pd.DataFrame) -> pd.DataFrame:
    score = pd.DataFrame(index=market_returns.index)

    for asset in ["equity", "bond", "gold"]:
        if asset not in market_returns:
            continue

        r = market_returns[asset]
        p3 = (1 + r).rolling(3).prod() - 1
        p12 = (1 + r).rolling(12).prod() - 1

        score[asset] = (
            0.40 * np.sign(p3)
            + 0.60 * np.sign(p12)
        )

    return score.clip(-1, 1)


def build_vam_weights(
    results: pd.DataFrame,
    market_returns: pd.DataFrame,
    config: Config,
) -> pd.DataFrame:

    trend = trend_score(market_returns)
    rows = []

    for dt in results.index:
        base = regime_probability_allocation(results.loc[dt])

        # Trend overlay: tilt only, never fully exit an asset.
        for asset in ["equity", "bond", "gold"]:
            if dt in trend.index and asset in trend.columns:
                tilt = 1.0 + 0.15 * float(trend.loc[dt, asset])
                base[asset] *= tilt

        # Cash is residual after normalizing risk assets.
        total_risk = base["equity"] + base["bond"] + base["gold"]

        if total_risk > 0:
            # Keep cash as defined by regime probability; normalize total.
            total = total_risk + base["cash"]
            for a in base:
                base[a] /= total

        rows.append(pd.Series(base, name=dt))

    weights = pd.DataFrame(rows)

    # Long-only hard limits.
    weights["equity"] = weights["equity"].clip(.10, .90)
    weights["bond"] = weights["bond"].clip(.00, .60)
    weights["gold"] = weights["gold"].clip(.00, .35)

    # Re-normalize.
    weights = weights.div(weights.sum(axis=1), axis=0)

    return weights


def apply_turnover_limit(
    target: pd.DataFrame,
    max_turnover: float,
) -> pd.DataFrame:

    actual = target.copy()

    for i in range(1, len(actual)):
        prev = actual.iloc[i - 1].copy()
        desired = target.iloc[i].copy()

        turnover = np.abs(desired - prev).sum()

        if turnover > max_turnover:
            scale = max_turnover / turnover
            actual.iloc[i] = prev + scale * (desired - prev)

    return actual


# ---------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------

def backtest(
    asset_returns: pd.DataFrame,
    weights: pd.DataFrame,
    transaction_cost: float,
) -> pd.Series:

    w = weights.reindex(asset_returns.index).ffill().fillna(0)

    # Portfolio return uses previous month's weights to avoid same-period
    # look-ahead from the current month's return.
    w_lag = w.shift(1).fillna(w.iloc[0])

    gross = (w_lag * asset_returns).sum(axis=1)

    turnover = w.diff().abs().sum(axis=1).fillna(0)
    costs = turnover * transaction_cost

    return gross - costs


# ---------------------------------------------------------------------
# Walk-forward validation
# ---------------------------------------------------------------------

def walk_forward_regimes(
    factors: pd.DataFrame,
    config: Config,
):
    """
    Refit HMM sequentially.

    For each month t, fit only on observations available before t,
    then predict t. This is substantially safer than fitting one HMM
    on the entire sample for a backtest.
    """

    cols = ["growth", "growth_momentum", "inflation", "financial"]

    dates = factors.index
    all_probs = []
    all_states = []
    mappings = []

    for i in range(config.min_train_months, len(dates)):

        train = factors.iloc[:i]
        current = factors.iloc[i:i + 1]

        try:
            # Fit on train.
            X = train[cols].values
            mu = X.mean(axis=0)
            sd = X.std(axis=0)
            sd[sd == 0] = 1

            Xs = (X - mu) / sd
            Xc = (current[cols].values - mu) / sd

            model = GaussianHMM(
                n_components=config.hmm_states,
                covariance_type="diag",
                n_iter=1000,
                tol=1e-4,
                random_state=42,
            )
            model.fit(Xs)

            train_states = model.predict(Xs)
            mapping = map_states(train, train_states)

            p = model.predict_proba(Xc)[0]
            state = int(np.argmax(p))

            row = {f"P_{regime}": 0.0 for regime in REGIME_PORTFOLIOS}

            for latent, regime in mapping.items():
                row[f"P_{regime}"] = float(p[latent])

            all_probs.append(row)
            all_states.append(state)
            mappings.append(mapping)

        except Exception as exc:
            print(f"[WARN] walk-forward failed at {dates[i]}: {exc}")
            all_probs.append(
                {f"P_{r}": np.nan for r in REGIME_PORTFOLIOS}
            )
            all_states.append(np.nan)
            mappings.append({})

    out = pd.DataFrame(
        all_probs,
        index=dates[config.min_train_months:],
    )

    return out


# ---------------------------------------------------------------------
# Charts / reporting
# ---------------------------------------------------------------------

def save_chart(results: pd.DataFrame, portfolio: pd.Series, outdir: Path):
    wealth = (1 + portfolio.fillna(0)).cumprod()

    fig, ax = plt.subplots(figsize=(12, 6))
    wealth.plot(ax=ax, label="MRE-VAM")
    ax.set_title("MRE-VAM Equity Curve")
    ax.set_ylabel("Growth of $1")
    ax.grid(True, alpha=.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(outdir / "equity_curve.png", dpi=160)
    plt.close(fig)

    prob_cols = [c for c in results.columns if c.startswith("P_")]
    if prob_cols:
        fig, ax = plt.subplots(figsize=(12, 5))
        results[prob_cols].plot.area(ax=ax, stacked=True)
        ax.set_title("Economic Regime Probabilities")
        ax.set_ylim(0, 1)
        fig.tight_layout()
        fig.savefig(outdir / "regime_probabilities.png", dpi=160)
        plt.close(fig)


def save_factor_chart(results: pd.DataFrame, outdir: Path):
    cols = [c for c in ["growth", "inflation", "financial"] if c in results]

    fig, ax = plt.subplots(figsize=(12, 6))
    results[cols].plot(ax=ax)
    ax.axhline(0, linewidth=1)
    ax.set_title("Macro Factor Scores")
    ax.grid(True, alpha=.25)
    fig.tight_layout()
    fig.savefig(outdir / "macro_factors.png", dpi=160)
    plt.close(fig)


# ---------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------

def run(config: Config, fred_key: str, market_source: str):
    outdir = ensure_output(config.output_dir)

    print("\n=== MRE-VAM ===")
    print("Downloading macro data...")

    macro = fred_download(fred_key, config.start_date)
    macro = apply_release_lag(macro)

    print("\nBuilding macro factors...")
    features = build_features(macro)
    z = normalize_features(features, config.z_window)
    factors = build_factors(z)

    print("\nDownloading market data...")
    if market_source == "yfinance":
        market = market_download_yfinance(config.start_date)
    else:
        market = market_download_stooq(config.start_date)

    market_returns = market.pct_change().replace([np.inf, -np.inf], np.nan)

    # Align.
    idx = factors.index.intersection(market_returns.index)
    factors = factors.loc[idx]
    market_returns = market_returns.loc[idx]

    print("\nRunning walk-forward HMM...")
    regime_probs = walk_forward_regimes(factors, config)

    # Attach factors.
    results = factors.join(regime_probs, how="inner")

    # Most probable regime.
    pcols = [f"P_{r}" for r in REGIME_PORTFOLIOS]
    results["regime"] = results[pcols].idxmax(axis=1).str.replace("P_", "")

    # VAM weights.
    weights = build_vam_weights(
        results,
        market_returns,
        config,
    )

    weights = apply_turnover_limit(
        weights,
        config.max_turnover,
    )

    vam = backtest(
        market_returns[["equity", "bond", "gold"]],
        weights[["equity", "bond", "gold"]],
        config.transaction_cost,
    )

    # Buy & Hold benchmark: 60/25/10 + 5% cash.
    bh_weights = pd.DataFrame(
        {
            "equity": config.base_equity,
            "bond": config.base_bond,
            "gold": config.base_gold,
        },
        index=market_returns.index,
    )
    bh = backtest(
        market_returns[["equity", "bond", "gold"]],
        bh_weights,
        config.transaction_cost,
    )

    # Equity-only benchmark.
    spy = market_returns["equity"].copy()

    metrics = pd.DataFrame(
        {
            "MRE-VAM": portfolio_metrics(vam),
            "60/25/10": portfolio_metrics(bh),
            "Equity-only": portfolio_metrics(spy),
        }
    ).T

    # Save.
    results.to_csv(outdir / "macro_regimes.csv")
    weights.to_csv(outdir / "vam_weights.csv")

    equity = pd.DataFrame(
        {
            "MRE-VAM": vam,
            "60/25/10": bh,
            "Equity-only": spy,
        }
    )
    equity.to_csv(outdir / "portfolio_returns.csv")

    metrics.to_csv(outdir / "performance_metrics.csv")

    summary = {
        "latest_date": str(results.index[-1].date()),
        "latest_regime": str(results.iloc[-1]["regime"]),
        "latest_probabilities": {
            c.replace("P_", ""): float(results.iloc[-1][c])
            for c in pcols
        },
        "latest_weights": {
            c: float(weights.iloc[-1][c])
            for c in weights.columns
        },
        "performance": metrics.to_dict(orient="index"),
    }

    with open(outdir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    save_chart(results, vam, outdir)
    save_factor_chart(results, outdir)

    print("\n=== CURRENT REGIME ===")
    print(results.iloc[-1]["regime"])
    for c in pcols:
        print(f"{c.replace('P_', ''):12s}: {results.iloc[-1][c]*100:6.2f}%")

    print("\n=== CURRENT VAM WEIGHTS ===")
    print((weights.iloc[-1] * 100).round(2).to_string())

    print("\n=== PERFORMANCE ===")
    print(metrics.to_string(float_format=lambda x: f"{x:.4f}"))

    print(f"\nOutput: {outdir.resolve()}")

    return results, weights, metrics


def parse_args():
    p = argparse.ArgumentParser(description="MRE-VAM macro regime engine")
    p.add_argument(
        "--fred-key",
        default=os.getenv("FRED_API_KEY"),
        help="FRED API key; alternatively set FRED_API_KEY",
    )
    p.add_argument(
        "--start",
        default="1970-01-01",
    )
    p.add_argument(
        "--output",
        default="mre_vam_output",
    )
    p.add_argument(
        "--market-source",
        choices=["stooq", "yfinance"],
        default="stooq",
    )
    p.add_argument(
        "--transaction-cost",
        type=float,
        default=0.0015,
        help="Per-unit turnover cost, e.g. 0.0015 = 15 bps",
    )
    p.add_argument(
        "--max-turnover",
        type=float,
        default=0.50,
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if not args.fred_key:
        print(
            "ERROR: Provide --fred-key or set FRED_API_KEY.",
            file=sys.stderr,
        )
        sys.exit(2)

    cfg = Config(
        start_date=args.start,
        output_dir=args.output,
        transaction_cost=args.transaction_cost,
        max_turnover=args.max_turnover,
    )

    run(
        cfg,
        fred_key=args.fred_key,
        market_source=args.market_source,
    )
