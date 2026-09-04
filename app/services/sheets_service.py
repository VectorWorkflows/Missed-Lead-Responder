# app/services/sheets_service.py
"""
Google Sheets sync.

These functions are SYNCHRONOUS and blocking (gspread is), so they are always
called via asyncio.to_thread from the outbox worker - never from a webhook.

They RAISE on failure. That is intentional: the outbox catches the exception
and retries with backoff. The old code swallowed every error with a print(),
which meant a transient Google 429 silently lost the row forever.
"""

import base64
import binascii
import json
import os
import zoneinfo
from datetime import datetime, timezone
from typing import Any, Optional

import gspread
from google.oauth2.service_account import Credentials

from app.config import settings
from app.logging_config import get_logger

log = get_logger("sheets")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADER_ROW = [
    "Call Timestamp",
    "Caller Phone",
    "Customer Reply",
    "Lead Status",
    "Last Updated",
    "Call SID",
]
CALL_SID_COL_INDEX = 6  # Column F

_gc: Optional[gspread.Client] = None
_headers_verified: set[str] = set()


def _load_credentials_dict() -> dict[str, Any]:
    """Accept base64 JSON, raw JSON, or a path to a key file."""
    raw = (settings.GOOGLE_SERVICE_ACCOUNT_JSON or "").strip()
    if not raw:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not set")

    if raw.startswith("{"):
        return json.loads(raw)

    if os.path.isfile(raw):
        with open(raw, encoding="utf-8") as fh:
            return json.load(fh)

    try:
        return json.loads(base64.b64decode(raw).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid base64 JSON, raw JSON, or a file path"
        ) from exc


def get_sheets_client() -> gspread.Client:
    global _gc
    if _gc is None:
        creds = Credentials.from_service_account_info(_load_credentials_dict(), scopes=SCOPES)
        _gc = gspread.authorize(creds)
        log.info("Google Sheets client authorised.")
    return _gc


def sheets_configured() -> bool:
    return bool((settings.GOOGLE_SERVICE_ACCOUNT_JSON or "").strip())


def _tz(client_config: dict) -> Any:
    try:
        return zoneinfo.ZoneInfo(client_config.get("timezone") or "UTC")
    except Exception:
        return timezone.utc


def _fmt(dt: Any, tz: Any) -> str:
    if not isinstance(dt, datetime):
        try:
            dt = datetime.fromisoformat(str(dt))
        except Exception:
            return str(dt or "")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%Y-%m-%d %I:%M:%S %p %Z")


def _open_sheet(sheet_id: str):
    return get_sheets_client().open_by_key(sheet_id).sheet1


def _ensure_headers(sheet, sheet_id: str) -> None:
    """Checked once per sheet per process, not on every single write."""
    if sheet_id in _headers_verified:
        return
    existing = sheet.row_values(1)
    if not existing or existing[:6] != HEADER_ROW:
        sheet.update(range_name="A1:F1", values=[HEADER_ROW], value_input_option="USER_ENTERED")
        log.info("Header row written to sheet %s", sheet_id)
    _headers_verified.add(sheet_id)


def upsert_lead_row(client_config: dict, lead_data: dict) -> None:
    """Append or update the row for this Call SID. Idempotent. Raises on failure."""
    if not sheets_configured():
        log.debug("Sheets not configured; skipping.")
        return

    sheet_id = (client_config or {}).get("google_sheet_id")
    if not sheet_id or sheet_id == "placeholder_sheet_id":
        log.warning("Client %s has no google_sheet_id; skipping sheet sync.",
                    (client_config or {}).get("_id"))
        return

    tz = _tz(client_config)
    call_sid = lead_data.get("call_sid", "")

    reply_text = lead_data.get("reply_text", "") or ""
    recording_url = lead_data.get("recording_url")
    if recording_url and recording_url != "N/A":
        clean = recording_url if recording_url.endswith(".mp3") else f"{recording_url}.mp3"
        reply_text = f"{reply_text}\n\n🔗 Audio: {clean}".strip()

    row = [
        _fmt(lead_data.get("call_time"), tz),
        lead_data.get("caller_phone", ""),
        reply_text,
        lead_data.get("owner_status", "NEW"),
        _fmt(datetime.now(timezone.utc), tz),
        call_sid,
    ]

    sheet = _open_sheet(sheet_id)
    _ensure_headers(sheet, sheet_id)

    sid_column = sheet.col_values(CALL_SID_COL_INDEX)
    if call_sid and call_sid in sid_column:
        idx = sid_column.index(call_sid) + 1
        sheet.update(range_name=f"A{idx}:F{idx}", values=[row], value_input_option="USER_ENTERED")
        log.info("Sheet row %d updated for %s", idx, call_sid)
    else:
        sheet.append_row(row, value_input_option="USER_ENTERED")
        log.info("Sheet row appended for %s", call_sid)


def update_row_status(client_config: dict, call_sid: str, new_status: str) -> None:
    """Update only columns D and E for an existing row. Raises on failure."""
    if not sheets_configured():
        return

    sheet_id = (client_config or {}).get("google_sheet_id")
    if not sheet_id:
        return

    tz = _tz(client_config)
    sheet = _open_sheet(sheet_id)
    sid_column = sheet.col_values(CALL_SID_COL_INDEX)

    if call_sid not in sid_column:
        # The row-create job may still be queued behind us. Raise so we retry.
        raise RuntimeError(f"Call SID {call_sid} not in sheet yet - will retry")

    idx = sid_column.index(call_sid) + 1
    sheet.update(
        range_name=f"D{idx}:E{idx}",
        values=[[new_status, _fmt(datetime.now(timezone.utc), tz)]],
        value_input_option="USER_ENTERED",
    )
    log.info("Sheet row %d status -> %s", idx, new_status)


def healthcheck(sheet_id: str) -> tuple[bool, str]:
    try:
        sheet = _open_sheet(sheet_id)
        sheet.row_values(1)
        return True, f"OK ({sheet.title})"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
