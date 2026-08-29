# app/services/sheets_service.py
import base64
import json
from datetime import datetime, timezone
import gspread
from google.oauth2.service_account import Credentials
from app.config import settings

# Google Sheets Scopes
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Standard Sheet Schema (Column F is our anchor)
HEADER_ROW = [
    "Call Timestamp",
    "Caller Phone",
    "Customer Reply",
    "Lead Status",
    "Last Updated",
    "Call SID"
]
CALL_SID_COL_INDEX = 6  # Column F (1-indexed)


def get_sheets_client():
    """Decodes Base64 credentials and authenticates with Google."""
    if not settings.GOOGLE_SERVICE_ACCOUNT_JSON:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is missing in .env")

    key_json = base64.b64decode(settings.GOOGLE_SERVICE_ACCOUNT_JSON).decode("utf-8")
    creds_dict = json.loads(key_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def _ensure_headers(sheet):
    """Ensures the top row has the correct headers."""
    existing_headers = sheet.row_values(1)
    if not existing_headers or existing_headers[:6] != HEADER_ROW:
        sheet.update(range_name="A1:F1", values=[HEADER_ROW], value_input_option="USER_ENTERED")


def log_new_lead_reply(client_config: dict, lead_data: dict):
    """
    Appends or updates the lead row in Google Sheets when a reply comes in.
    Keyed by Call SID in Column F to prevent duplicate rows.
    """
    sheet_id = client_config.get("google_sheet_id")
    if not sheet_id or sheet_id == "placeholder_sheet_id":
        print(f"⚠️ No valid Google Sheet ID configured for client {client_config.get('_id')}")
        return

    try:
        gc = get_sheets_client()
        sheet = gc.open_by_key(sheet_id).sheet1
        _ensure_headers(sheet)

        call_sid = lead_data.get("call_sid")
        call_time = lead_data.get("call_time")
        if isinstance(call_time, datetime):
            call_time_str = call_time.strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            call_time_str = str(call_time or "")

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # SMART FIX: Safely append the audio link so it is clickable in the spreadsheet
        reply_text = lead_data.get("reply_text", "")
        recording_url = lead_data.get("recording_url")
        if recording_url and recording_url != "N/A":
            reply_text += f"\n\n🔗 Audio Link: {recording_url}"

        row_payload = [
            call_time_str,
            lead_data.get("caller_phone", ""),
            reply_text,
            lead_data.get("owner_status", "NEW"),
            now_str,
            call_sid
        ]

        # Read column F to find existing row with matching Call SID
        call_sid_col = sheet.col_values(CALL_SID_COL_INDEX)

        if call_sid in call_sid_col:
            # Row index in sheet is 1-based (index + 1)
            row_idx = call_sid_col.index(call_sid) + 1
            sheet.update(range_name=f"A{row_idx}:F{row_idx}", values=[row_payload], value_input_option="USER_ENTERED")
            print(f"📊 Updated existing lead row {row_idx} for Call SID: {call_sid}")
        else:
            sheet.append_row(row_payload, value_input_option="USER_ENTERED")
            print(f"📊 Appended new lead row for Call SID: {call_sid}")

    except Exception as e:
        print(f"❌ Error syncing with Google Sheet: {e}")


def update_sheet_status(client_config: dict, call_sid: str, new_status: str):
    """
    Finds the row matching call_sid and updates Column D (Status) and Column E (Last Updated).
    Used by the Telegram bot callback handler.
    """
    sheet_id = client_config.get("google_sheet_id")
    if not sheet_id:
        return

    try:
        gc = get_sheets_client()
        sheet = gc.open_by_key(sheet_id).sheet1

        call_sid_col = sheet.col_values(CALL_SID_COL_INDEX)
        if call_sid in call_sid_col:
            row_idx = call_sid_col.index(call_sid) + 1
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            
            # Update Column D (Status) and Column E (Last Updated)
            sheet.update(range_name=f"D{row_idx}:E{row_idx}", values=[[new_status, now_str]], value_input_option="USER_ENTERED")
            print(f"📊 Updated status to '{new_status}' in Google Sheet row {row_idx}")
        else:
            print(f"⚠️ Call SID {call_sid} not found in Google Sheet.")

    except Exception as e:
        print(f"❌ Error updating status in Google Sheet: {e}")