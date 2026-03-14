import logging, os
from config import GOOGLE_SHEET_ID, GOOGLE_CREDENTIALS_FILE, SHEET_COLUMNS

log = logging.getLogger(__name__)

async def save_row(row: dict):
    if not GOOGLE_SHEET_ID or not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        log.info(f"Sheets skipped. Row: {row}")
        return
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"],
        )
        client = gspread.authorize(creds)
        sheet  = client.open_by_key(GOOGLE_SHEET_ID).sheet1
        sheet.append_row([row.get(c,"") for c in SHEET_COLUMNS])
        log.info(f"Saved to Sheets: {row.get('Phone Number')} | {row.get('Status')}")
    except ImportError:
        log.warning("gspread not installed")
    except Exception as e:
        log.error(f"Sheets error: {e}")
