"""
scraper.py — Thin wrapper around amazon_review_scraper.py.

Runs the scraper as a subprocess, captures JSON output,
returns the raw review list ready for the database.
"""
import json
import logging
import os
import subprocess
import sys
import tempfile

from config import SCRAPE_MODE

log = logging.getLogger(__name__)

# Path to the scraper script — same directory as this file
SCRAPER_PATH = os.path.join(os.path.dirname(__file__), "amazon_review_scraper.py")


def scrape_asin(asin: str, mode: str = SCRAPE_MODE) -> tuple[list[dict], dict]:
    """
    Scrape reviews for a single ASIN.

    Returns:
        (reviews: list[dict], summary: dict)
        reviews is the raw list from the scraper — pass directly to supabase_client.insert_reviews()
        summary contains star distribution, date range, etc.

    Raises:
        RuntimeError if the scraper exits non-zero or returns invalid JSON.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [sys.executable, SCRAPER_PATH, asin, "--mode", mode, "--output", tmp_path],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute max per ASIN
        )

        if result.returncode != 0:
            err = result.stderr[-500:] if result.stderr else "unknown error"
            raise RuntimeError(f"Scraper failed for {asin}: {err}")

        with open(tmp_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        reviews = data.get("reviews", [])
        summary = data.get("summary", {})

        log.info(
            f"Scraped {asin}: {len(reviews)} reviews "
            f"(⭐ {summary.get('star_distribution', {})})"
        )
        return reviews, summary

    except json.JSONDecodeError as e:
        raise RuntimeError(f"Scraper output invalid JSON for {asin}: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
