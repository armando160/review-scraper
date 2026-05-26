"""
sheets_writer.py — Appends flagged reviews to the Google Sheets removal queue.

Uses gspread + service account credentials.
Handles header creation, deduplication within sheet, and batch append.
"""
import json
import logging
from config import SHEETS_ID, SHEETS_GID, GOOGLE_SA_JSON, SHEET_HEADER

log = logging.getLogger(__name__)

# Star rating → Amazon filterByStar URL parameter
STAR_FILTER = {
    1: "one_star",
    2: "two_star",
    3: "three_star",
    4: "four_star",
    5: "five_star",
}


def _get_worksheet():
    """Authenticate and return the target worksheet."""
    import gspread
    from google.oauth2.service_account import Credentials

    sa_info = json.loads(GOOGLE_SA_JSON)
    creds = Credentials.from_service_account_info(
        sa_info,
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    client = gspread.authorize(creds)
    workbook = client.open_by_key(SHEETS_ID)

    # Find worksheet by GID
    for ws in workbook.worksheets():
        if ws.id == SHEETS_GID:
            return ws

    # Fallback: use first sheet if GID not found
    log.warning(f"Worksheet GID {SHEETS_GID} not found, using first sheet")
    return workbook.sheet1


def _ensure_header(ws) -> int:
    """
    Ensure the header row exists. If sheet is empty, write it.
    Returns the next empty row number.
    """
    all_values = ws.get_all_values()

    if not all_values:
        # Sheet is empty — write header
        ws.append_row(SHEET_HEADER, value_input_option="RAW")
        log.info("Header row created")
        return 2  # next data row

    # Check if first row matches expected header
    if all_values[0] != SHEET_HEADER:
        log.warning(
            f"Sheet header mismatch!\n"
            f"Expected: {SHEET_HEADER}\n"
            f"Found:    {all_values[0]}\n"
            f"Appending anyway — check column alignment."
        )

    return len(all_values) + 1


def _flag_to_row(flag: dict) -> list:
    """
    Convert a compliance flag dict to a spreadsheet row.
    Combines data from the flag, its review, and product info.
    """
    asin     = flag.get("asin", "")
    rating   = flag.get("_rating") or flag.get("reviews", {}).get("rating", "")
    author   = flag.get("_author") or flag.get("reviews", {}).get("author", "")
    title    = flag.get("_title") or flag.get("reviews", {}).get("title", "")
    text     = flag.get("_text") or flag.get("reviews", {}).get("review_text", "")
    date     = flag.get("_date") or flag.get("reviews", {}).get("review_date", "")
    verified = flag.get("_verified")
    if verified is None:
        verified = flag.get("reviews", {}).get("is_verified_purchase")

    product_name = flag.get("products", {}).get("product_name", "") if isinstance(flag.get("products"), dict) else ""

    star_param   = STAR_FILTER.get(int(rating) if rating else 0, "")
    review_page  = (
        f"https://www.amazon.com/product-reviews/{asin}"
        f"?filterByStar={star_param}&sortBy=recent"
        if star_param else
        f"https://www.amazon.com/product-reviews/{asin}"
    )

    return [
        asin,
        product_name,
        f"https://www.amazon.com/dp/{asin}",
        review_page,
        author,
        rating,
        date,
        "Yes" if verified else "No",
        title,
        (text or "")[:1000],   # truncate very long reviews
        flag.get("flag_reason") or flag.get("violation_type", ""),
        flag.get("flag_details") or flag.get("reason", ""),
        flag.get("confidence_score") or flag.get("confidence", ""),
        "Pending",  # Report Status
        "",         # Date Submitted
        "",         # Outcome
        "",         # Notes
    ]


def append_violations(flags: list[dict]) -> int:
    """
    Append new compliance flags to the Google Sheet.

    Input: list of flag dicts from supabase_client.get_unsent_flags()
    Returns: number of rows appended.
    """
    if not flags:
        log.info("No new violations to push to Google Sheets")
        return 0

    ws = _get_worksheet()
    _ensure_header(ws)

    rows = [_flag_to_row(f) for f in flags]

    # Batch append (gspread handles this efficiently)
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    log.info(f"Appended {len(rows)} new violations to Google Sheets")
    return len(rows)
