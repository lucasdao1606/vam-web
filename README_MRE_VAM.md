# MRE-VAM

Macro Regime Engine + Valuation/Trend/Risk Asset Allocation Model.

## Install
pip install -r requirements_mre_vam.txt

## Run with Stooq
Windows PowerShell:
$env:FRED_API_KEY="YOUR_FREE_FRED_KEY"
python mre_vam.py --start 1970-01-01

Or:
python mre_vam.py --fred-key YOUR_FREE_FRED_KEY --start 1970-01-01

## Output
mre_vam_output/
- macro_regimes.csv
- vam_weights.csv
- portfolio_returns.csv
- performance_metrics.csv
- summary.json
- equity_curve.png
- regime_probabilities.png
- macro_factors.png

## Notes
The current implementation is deliberately conservative:
- macro variables are lagged one month;
- rolling Z-scores use only prior observations;
- HMM is refit walk-forward;
- portfolio uses previous-month weights;
- transaction costs and turnover limits are included.

For institutional-grade research, use ALFRED vintage data and add
point-in-time market/ETF availability, delisting handling, taxes,
slippage, and parameter stability tests.
