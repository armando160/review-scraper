"""
supabase_client.py — All Supabase REST API operations.

Uses the REST API directly (no extra SDK needed beyond requests).
Key design: every write is idempotent — safe to re-run on errors.
"""
import hashlib
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from config import SUPABASE_URL, SUPABASE_KEY, TIER_HOURS

log = logging.getLogger(__name__)

BASE = SUPABASE_URL.rstrip("/") + "/rest/v1"
HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_review_key(author: str, title: str, text: str) -> str:
    """SHA-256 of author|title|first-80-chars. Stable dedup key."""
    raw = f"{author}|{title}|{text[:80]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_review_date(origin_description: str) -> Optional[str]:
    """Extract YYYY-MM-DD from 'Reviewed in the United States on April 5, 2026'."""
    if not origin_description:
        return None
    m = re.search(r"on (\w+ \d{1,2},?\s+\d{4})", origin_description)
    if m:
        raw = re.sub(r"\s+", " ", m.group(1).replace(",", "").strip())
        try:
            return datetime.strptime(raw, "%B %d %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _post(endpoint: str, payload, prefer: str = None) -> requests.Response:
    hdrs = {**HEADERS}
    if prefer:
        hdrs["Prefer"] = prefer
    resp = requests.post(f"{BASE}/{endpoint}", json=payload, headers=hdrs, timeout=20)
    resp.raise_for_status()
    return resp


def _patch(endpoint: str, payload: dict, params: dict) -> requests.Response:
    resp = requests.patch(
        f"{BASE}/{endpoint}", json=payload,
        headers=HEADERS, params=params, timeout=20
    )
    resp.raise_for_status()
    return resp


def _get(endpoint: str, params: dict = None) -> list:
    hdrs = {**HEADERS, "Prefer": "return=representation"}
    resp = requests.get(f"{BASE}/{endpoint}", headers=hdrs, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


# ─── Products ─────────────────────────────────────────────────────────────────

def upsert_products(products: list[dict]) -> int:
    """
    Upsert product records from Monday.com.
    On conflict (same ASIN), updates rating/review_count/status/tier.
    Does NOT reset next_scrape_at — preserves scrape schedule.
    Returns count inserted/updated.
    """
    if not products:
        return 0

    rows = []
    for p in products:
        rows.append({
            "asin":           p["asin"],
            "product_name":   p.get("product_name"),
            "monday_item_id": p.get("monday_item_id"),
            "status":         p.get("status"),
            "rating":         p.get("rating"),
            "review_count":   p.get("review_count", 0),
            "scrape_tier":    p.get("scrape_tier", 3),
            "updated_at":     _now_iso(),
        })

    _post(
        "products",
        rows,
        prefer="resolution=merge-duplicates,return=minimal",
    )
    log.info(f"Upserted {len(rows)} products")
    return len(rows)


def get_products_to_scrape(limit: int = 20) -> list[dict]:
    """
    Return up to `limit` products whose next_scrape_at is due (≤ now).
    Ordered: lowest tier first (most urgent), then oldest next_scrape_at.
    """
    now = _now_iso()
    rows = _get("products", params={
        "select":         "asin,product_name,rating,review_count,scrape_tier,last_scraped_at",
        "next_scrape_at": f"lte.{now}",
        "order":          "scrape_tier.asc,next_scrape_at.asc",
        "limit":          str(limit),
    })
    log.info(f"Queue: {len(rows)} ASINs ready to scrape")
    return rows


def update_product_after_scrape(asin: str, reviews_found: int, new_reviews: int, tier: int):
    """Mark ASIN as scraped and schedule next scrape."""
    interval_hours = TIER_HOURS.get(tier, 96)
    next_scrape = (
        datetime.now(timezone.utc) + timedelta(hours=interval_hours)
    ).isoformat()

    _patch(
        "products",
        {
            "last_scraped_at": _now_iso(),
            "next_scrape_at":  next_scrape,
            "updated_at":      _now_iso(),
        },
        params={"asin": f"eq.{asin}"},
    )
    log.debug(f"Updated {asin}: {reviews_found} found, {new_reviews} new, next in {interval_hours}h")


# ─── Reviews ──────────────────────────────────────────────────────────────────

def insert_reviews(asin: str, raw_reviews: list[dict]) -> int:
    """
    Insert reviews into the reviews table.
    Deduplicates via review_key unique constraint (ON CONFLICT DO NOTHING).
    Returns number of genuinely new reviews inserted.
    """
    if not raw_reviews:
        return 0

    rows = []
    for r in raw_reviews:
        author = r.get("Author") or ""
        title  = r.get("Title") or ""
        text   = r.get("Text") or ""
        key    = _make_review_key(author, title, text)

        rows.append({
            "asin":                asin,
            "review_key":          key,
            "author":              author,
            "title":               title,
            "review_text":         text,
            "rating":              r.get("OverallRating"),
            "review_date":         _parse_review_date(r.get("OriginDescription", "")),
            "origin_description":  r.get("OriginDescription"),
            "is_verified_purchase": bool(r.get("IsVerifiedPurchase")),
            "is_vine_review":      bool(r.get("IsVineReview")),
            "helpful_votes":       r.get("HelpfulVotes") or 0,
            "image_count":         len(r.get("ImageUrls") or []),
            "video_count":         len(r.get("MediaUrls") or []),
        })

    # Batch in chunks of 500 to stay within Supabase body limits
    new_total = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        resp = requests.post(
            f"{BASE}/reviews",
            json=chunk,
            headers={
                **HEADERS,
                "Prefer": "resolution=ignore-duplicates,return=representation",
            },
            timeout=30,
        )
        resp.raise_for_status()
        inserted = resp.json()
        new_total += len(inserted)

    log.info(f"{asin}: {len(raw_reviews)} scraped, {new_total} new")
    return new_total


def get_unchecked_reviews(limit: int = 200) -> list[dict]:
    """
    Fetch reviews that haven't been through LLM compliance check yet.
    Only 1–3 star reviews (4–5 stars benefit us, don't flag them).
    """
    rows = _get("reviews", params={
        "select":              "id,asin,author,title,review_text,rating,review_date,is_verified_purchase",
        "compliance_checked":  "eq.false",
        "rating":              "lte.3",
        "order":               "created_at.asc",
        "limit":               str(limit),
    })
    return rows


def mark_reviews_checked(review_ids: list[int]):
    """Mark reviews as having been through compliance checking."""
    if not review_ids:
        return
    # Supabase REST: filter by list of IDs
    id_list = "(" + ",".join(str(i) for i in review_ids) + ")"
    _patch(
        "reviews",
        {"compliance_checked": True},
        params={"id": f"in.{id_list}"},
    )


# ─── Compliance Flags ─────────────────────────────────────────────────────────

def insert_compliance_flags(flags: list[dict]) -> int:
    """
    Insert LLM-identified violations into compliance_flags.
    Idempotent: ignores if the same review_id already has a flag.
    """
    if not flags:
        return 0

    rows = [
        {
            "review_id":       f["review_id"],
            "asin":            f["asin"],
            "flag_reason":     f["violation_type"],
            "flag_details":    f["reason"],
            "confidence_score": f["confidence"],
            "tos_violation_type": f["violation_type"],
            "status":          "pending",
        }
        for f in flags
    ]

    _post("compliance_flags", rows, prefer="resolution=ignore-duplicates,return=minimal")
    log.info(f"Inserted {len(rows)} compliance flags")
    return len(rows)


def get_unsent_flags(limit: int = 100) -> list[dict]:
    """Return compliance flags not yet pushed to Google Sheets."""
    rows = _get("compliance_flags", params={
        "select": (
            "id,asin,flag_reason,flag_details,confidence_score,"
            "reviews(author,title,review_text,rating,review_date,is_verified_purchase),"
            "products(product_name)"
        ),
        "sent_to_sheets": "eq.false",
        "status":         "eq.pending",
        "order":          "created_at.asc",
        "limit":          str(limit),
    })
    return rows


def mark_flags_sent(flag_ids: list[int]):
    """Mark flags as pushed to Google Sheets."""
    if not flag_ids:
        return
    id_list = "(" + ",".join(str(i) for i in flag_ids) + ")"
    _patch(
        "compliance_flags",
        {"sent_to_sheets": True},
        params={"id": f"in.{id_list}"},
    )


# ─── Scrape Log ───────────────────────────────────────────────────────────────

def log_scrape_start(asin: str, mode: str) -> int:
    """Insert a scrape log entry, return its id."""
    hdrs = {**HEADERS, "Prefer": "return=representation"}
    resp = requests.post(
        f"{BASE}/scrape_log",
        json={"asin": asin, "mode": mode, "status": "running"},
        headers=hdrs,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()[0]["id"]


def log_scrape_finish(log_id: int, reviews_found: int, new_reviews: int, error: str = None):
    """Update a scrape log entry on completion."""
    payload = {
        "completed_at":  _now_iso(),
        "reviews_found": reviews_found,
        "new_reviews":   new_reviews,
        "status":        "error" if error else "completed",
    }
    if error:
        payload["error_message"] = error[:500]

    _patch("scrape_log", payload, params={"id": f"eq.{log_id}"})
