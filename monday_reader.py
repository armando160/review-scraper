"""
monday_reader.py — Fetches all products from Monday.com board with full pagination.

Reads: ASIN, product name, rating, review count, status.
Returns structured product dicts that get synced to Supabase.
"""
import requests
import logging
from config import (
    MONDAY_TOKEN, MONDAY_BOARD_ID,
    MONDAY_COL_ASIN, MONDAY_COL_RATING, MONDAY_COL_REVIEWS,
    MONDAY_COL_STATUS, MONDAY_COL_PRODUCT,
    TIER_HOURS, ACTIVE_STATUSES,
)

log = logging.getLogger(__name__)

MONDAY_API = "https://api.monday.com/v2"
HEADERS = {
    "Authorization": MONDAY_TOKEN,
    "Content-Type": "application/json",
    "API-Version": "2024-01",
}

# Column IDs we care about — alert if these change
REQUIRED_COLS = {
    MONDAY_COL_ASIN:    "ASIN",
    MONDAY_COL_RATING:  "Rating",
    MONDAY_COL_REVIEWS: "Reviews",
    MONDAY_COL_STATUS:  "Status",
    MONDAY_COL_PRODUCT: "Product",
}


def _run_query(query: str, variables: dict = None) -> dict:
    """Execute a Monday GraphQL query, raise on errors."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = requests.post(MONDAY_API, json=payload, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"Monday API error: {data['errors']}")
    return data


def _assign_tier(rating: float | None, status: str) -> int:
    """Assign scrape priority tier based on rating."""
    if status not in ACTIVE_STATUSES:
        return 4
    if rating is None:
        return 4
    if rating < 4.0:
        return 1
    if rating < 4.5:
        return 2
    if rating < 4.8:
        return 3
    return 4


def get_all_products() -> list[dict]:
    """
    Fetch all items from the Monday board with pagination.
    Returns a list of product dicts ready for Supabase upsert.
    Skips items with no ASIN.
    """
    query = """
    query GetBoardItems($boardId: ID!, $cursor: String) {
        boards(ids: [$boardId]) {
            items_page(limit: 100, cursor: $cursor) {
                cursor
                items {
                    id
                    name
                    column_values(ids: ["%s", "%s", "%s", "%s", "%s"]) {
                        id
                        text
                    }
                }
            }
        }
    }
    """ % (
        MONDAY_COL_ASIN, MONDAY_COL_RATING, MONDAY_COL_REVIEWS,
        MONDAY_COL_STATUS, MONDAY_COL_PRODUCT,
    )

    products = []
    cursor = None
    page = 0

    while True:
        page += 1
        variables = {"boardId": MONDAY_BOARD_ID}
        if cursor:
            variables["cursor"] = cursor

        data = _run_query(query, variables)
        page_data = data["data"]["boards"][0]["items_page"]
        items = page_data["items"]
        cursor = page_data.get("cursor")

        for item in items:
            # Parse column values into a flat dict
            cols = {cv["id"]: cv["text"] for cv in item["column_values"]}

            asin = (cols.get(MONDAY_COL_ASIN) or "").strip()
            if not asin or len(asin) < 10:
                continue  # skip rows with no valid ASIN

            # Safe numeric parsing
            try:
                rating = float(cols.get(MONDAY_COL_RATING) or 0) or None
            except (ValueError, TypeError):
                rating = None

            try:
                review_count = int(float(cols.get(MONDAY_COL_REVIEWS) or 0))
            except (ValueError, TypeError):
                review_count = 0

            status = cols.get(MONDAY_COL_STATUS) or "Unknown"
            product_name = cols.get(MONDAY_COL_PRODUCT) or item["name"]
            tier = _assign_tier(rating, status)

            products.append({
                "asin":           asin,
                "product_name":   product_name,
                "monday_item_id": str(item["id"]),
                "status":         status,
                "rating":         rating,
                "review_count":   review_count,
                "scrape_tier":    tier,
            })

        log.info(f"Page {page}: fetched {len(items)} items, total so far: {len(products)}")

        if not cursor:
            break  # no more pages

    # Deduplicate by ASIN — keep the one with the lowest tier (highest priority)
    seen: dict[str, dict] = {}
    for p in products:
        asin = p["asin"]
        if asin not in seen or p["scrape_tier"] < seen[asin]["scrape_tier"]:
            seen[asin] = p

    unique = list(seen.values())
    log.info(f"Monday sync complete: {len(products)} rows → {len(unique)} unique ASINs")
    return unique


def validate_board_columns() -> bool:
    """
    Check that expected column IDs still exist on the board.
    Logs a warning if any are missing — alerts you if someone renamed a column.
    """
    query = """
    query { boards(ids: [%s]) { columns { id title } } }
    """ % MONDAY_BOARD_ID

    data = _run_query(query)
    board_cols = {c["id"]: c["title"] for c in data["data"]["boards"][0]["columns"]}

    all_ok = True
    for col_id, col_name in REQUIRED_COLS.items():
        if col_id not in board_cols:
            log.warning(
                f"⚠️  Monday column '{col_name}' (id={col_id}) NOT FOUND. "
                f"Someone may have renamed or deleted it. Check your Monday board."
            )
            all_ok = False
        else:
            log.debug(f"✓ Column '{col_name}' → '{board_cols[col_id]}'")

    return all_ok
