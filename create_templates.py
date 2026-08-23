import pandas as pd

templates = {
    "template_1_very_cheap.csv": {
        "age": 30, "pe_current": 10.5, "pe_min": 10.0, "pe_max": 20.0,
        "pb_current": 1.40, "pb_min": 1.35, "pb_max": 2.60,
        "rf": 2.5, "erp_min": 1.5, "erp_max": 7.0,
        "dy_current": 3.10, "dy_min": 1.20, "dy_max": 3.20,
        "w_pe": 30.0, "w_pb": 20.0, "w_erp": 35.0, "w_dy": 15.0,
        "price_current": 1100.0, "ma200": 1250.0,
        "volatility_current": 14.0, "volatility_avg": 17.5,
        "drawdown_pct": 18.5, "method": "step"
    },
    "template_2_cheap.csv": {
        "age": 35, "pe_current": 12.5, "pe_min": 10.0, "pe_max": 20.0,
        "pb_current": 1.55, "pb_min": 1.35, "pb_max": 2.60,
        "rf": 2.75, "erp_min": 1.5, "erp_max": 7.0,
        "dy_current": 2.20, "dy_min": 1.20, "dy_max": 3.20,
        "w_pe": 30.0, "w_pb": 20.0, "w_erp": 35.0, "w_dy": 15.0,
        "price_current": 1200.0, "ma200": 1250.0,
        "volatility_current": 15.5, "volatility_avg": 17.5,
        "drawdown_pct": 8.0, "method": "step"
    },
    "template_3_neutral.csv": {
        "age": 40, "pe_current": 15.0, "pe_min": 10.0, "pe_max": 20.0,
        "pb_current": 1.975, "pb_min": 1.35, "pb_max": 2.60,
        "rf": 2.75, "erp_min": 1.5, "erp_max": 7.0,
        "dy_current": 2.20, "dy_min": 1.20, "dy_max": 3.20,
        "w_pe": 30.0, "w_pb": 20.0, "w_erp": 35.0, "w_dy": 15.0,
        "price_current": 1250.0, "ma200": 1250.0,
        "volatility_current": 17.5, "volatility_avg": 17.5,
        "drawdown_pct": 0.0, "method": "step"
    },
    "template_4_expensive.csv": {
        "age": 45, "pe_current": 17.5, "pe_min": 10.0, "pe_max": 20.0,
        "pb_current": 2.30, "pb_min": 1.35, "pb_max": 2.60,
        "rf": 3.2, "erp_min": 1.5, "erp_max": 7.0,
        "dy_current": 1.40, "dy_min": 1.20, "dy_max": 3.20,
        "w_pe": 30.0, "w_pb": 20.0, "w_erp": 35.0, "w_dy": 15.0,
        "price_current": 1380.0, "ma200": 1250.0,
        "volatility_current": 19.0, "volatility_avg": 17.5,
        "drawdown_pct": 2.0, "method": "step"
    },
    "template_5_very_expensive.csv": {
        "age": 50, "pe_current": 19.5, "pe_min": 10.0, "pe_max": 20.0,
        "pb_current": 2.55, "pb_min": 1.35, "pb_max": 2.60,
        "rf": 3.5, "erp_min": 1.5, "erp_max": 7.0,
        "dy_current": 1.25, "dy_min": 1.20, "dy_max": 3.20,
        "w_pe": 30.0, "w_pb": 20.0, "w_erp": 35.0, "w_dy": 15.0,
        "price_current": 1500.0, "ma200": 1250.0,
        "volatility_current": 22.0, "volatility_avg": 17.5,
        "drawdown_pct": 0.0, "method": "step"
    }
}

for filename, data in templates.items():
    df = pd.DataFrame([data])
    df.to_csv(filename, index=False)
    print(f"Đã tạo file: {filename}")