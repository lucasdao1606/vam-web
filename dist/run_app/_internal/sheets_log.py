"""
sheets_log.py - Ghi và đọc nhật ký (log) lịch sử tính toán VAM lên Google Sheets.

Yêu cầu cấu hình trong st.secrets (xem README.md phần "Thiết lập Google Sheets"):

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "..."
client_email = "..."
client_id = "..."
... (toàn bộ nội dung file JSON service account)

[sheets]
sheet_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz..."   # ID lấy từ URL Google Sheet
worksheet_name = "log"                         # tên tab trong sheet (mặc định "log")
"""

from datetime import datetime

import pandas as pd
import streamlit as st

LOG_COLUMNS = [
    "timestamp", "age", "valuation_score",
    "equity_weight", "bond_weight", "gold_weight",
]


def sheets_configured() -> bool:
    """Kiểm tra xem st.secrets đã có đủ cấu hình Google Sheets chưa."""
    try:
        return "gcp_service_account" in st.secrets and "sheets" in st.secrets
    except Exception:
        return False


@st.cache_resource(show_spinner=False)
def _get_worksheet():
    """Kết nối tới Google Sheets, cache lại connection trong suốt phiên server."""
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
    worksheet_name = st.secrets["sheets"].get("worksheet_name", "log")

    spreadsheet = client.open_by_key(sheet_id)
    try:
        ws = spreadsheet.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=len(LOG_COLUMNS))
        ws.append_row(LOG_COLUMNS)

    # Nếu sheet đang trống, ghi header
    if not ws.get_all_values():
        ws.append_row(LOG_COLUMNS)

    return ws


def append_log_row(row: dict) -> None:
    """Ghi thêm một dòng log lên Google Sheets. Ném lỗi nếu thất bại (caller tự xử lý)."""
    ws = _get_worksheet()
    ws.append_row([row.get(col, "") for col in LOG_COLUMNS])


def load_log_df() -> pd.DataFrame:
    """Đọc toàn bộ log hiện có từ Google Sheets thành DataFrame."""
    ws = _get_worksheet()
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame(columns=LOG_COLUMNS)
    return pd.DataFrame(records)


def make_log_row(age, result) -> dict:
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "age": age,
        "valuation_score": result.valuation_score,
        "equity_weight": result.equity_weight,
        "bond_weight": result.bond_weight,
        "gold_weight": result.gold_weight,
    }
