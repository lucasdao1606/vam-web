"""
sheets_log.py - Ghi và đọc nhật ký (log) lịch sử tính toán VAM lên Google Sheets.
"""
from datetime import datetime
import pandas as pd
import streamlit as st

LOG_COLUMNS = [
    "timestamp", "note", "age", "pe_current", "pb_current",
    "rf", "us10y", "us_cpi", "eps_growth_exp",
    "valuation_score", "equity_weight", "bond_weight", "gold_weight", "withdrawal_rate"
]

def sheets_configured() -> bool:
    try:
        return "gcp_service_account" in st.secrets and "sheets" in st.secrets
    except Exception:
        return False

@st.cache_resource(show_spinner=False)
def _get_worksheet():
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]), scopes=scopes
    )
    client = gspread.authorize(creds)
    sheet_id = st.secrets["sheets"]["sheet_id"]
    worksheet_name = "VAM_Data_Final"

    spreadsheet = client.open_by_key(sheet_id)
    try:
        ws = spreadsheet.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=len(LOG_COLUMNS))
        ws.append_row(LOG_COLUMNS)
        return ws

    try:
        headers = ws.row_values(1)
        if not headers or headers != LOG_COLUMNS:
            cell_list = ws.range(1, 1, 1, len(LOG_COLUMNS))
            for i, cell in enumerate(cell_list):
                cell.value = LOG_COLUMNS[i]
            ws.update_cells(cell_list)
    except Exception:
        pass

    return ws

def append_log_row(row: dict) -> None:
    ws = _get_worksheet()
    # Lớp bảo vệ 2: Chuyển toàn bộ về chuỗi (string) để chống Google Sheets tự động đổi dấu
    formatted_row = []
    for col in LOG_COLUMNS:
        val = row.get(col, "")
        if isinstance(val, (float, int)):
            formatted_row.append(str(val))
        else:
            formatted_row.append(str(val).strip())
            
    ws.append_row(formatted_row, value_input_option="USER_ENTERED")

def load_log_df() -> pd.DataFrame:
    ws = _get_worksheet()
    try:
        data = ws.get_all_values()
        
        if not data or len(data) <= 1:
            return pd.DataFrame(columns=LOG_COLUMNS)
            
        headers = data[0]
        rows = data[1:]
        
        # Đọc vào DataFrame dưới dạng chuỗi thô
        df = pd.DataFrame(rows, columns=headers)
        df = df.loc[:, df.columns != '']
        
        # Lớp bảo vệ 3: Khai báo toàn bộ 12 cột chứa số liệu để xử lý đồng loạt
        numeric_cols = [
            "age", "pe_current", "pb_current", "rf", "us10y", "us_cpi", 
            "eps_growth_exp", "valuation_score", "equity_weight", 
            "bond_weight", "gold_weight", "withdrawal_rate"
        ]
        
        # Làm sạch khoảng trắng, thay phẩy thành chấm và ép kiểu số thực (float)
        for col in numeric_cols:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip().str.replace(',', '.')
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        return df
        
    except Exception as e:
        print(f"Lỗi đọc log: {e}")
        return pd.DataFrame(columns=LOG_COLUMNS)

def make_log_row(inputs, result, note: str = "") -> dict:
    # Lớp bảo vệ 1: Làm tròn và ép định dạng cứng ngay từ ban đầu cho toàn bộ tham số
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": note,
        "age": int(inputs.age),
        "pe_current": round(float(inputs.pe_current), 2),
        "pb_current": round(float(inputs.pb_current), 2),
        "rf": round(float(inputs.rf), 2),
        "us10y": round(float(inputs.us10y), 2),
        "us_cpi": round(float(inputs.us_cpi), 2),
        "eps_growth_exp": round(float(inputs.eps_growth_exp), 2),
        "valuation_score": round(float(result.valuation_score), 4),
        "equity_weight": round(float(result.equity_weight), 2),
        "bond_weight": round(float(result.bond_weight), 2),
        "gold_weight": round(float(result.gold_weight), 2),
        "withdrawal_rate": round(float(result.withdrawal_rate), 2),
    }