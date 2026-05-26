"""
pipeline.py — Main orchestrator. Run this directly or via GitHub Actions.

Flow:
  1. Validate Monday board columns (warn if renamed)
  2. Sync Monday products → Supabase
  3. Pick next batch of ASINs to scrape
  4. Scrape each ASIN, store new reviews
  5. Run LLM compliance check on unchecked reviews
  6. Push new violations to Google Sheets
  7. Log everything
"""
import logging
import sys
import time

import monday_reader
import supabase_client
import scraper
import compliance_checker
import sheets_writer
from config import ASINS_PER_RUN, DELAY_BETWEEN_ASINS

# ─── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("pipeline")


def run():
    log.info("=" * 60)
    log.info("REVIEW PIPELINE STARTING")
    log.info("=" * 60)

    # ── Step 1: Validate Monday column structure ───────────────────────────────
    log.info("Step 1: Validating Monday.com board columns…")
    ok = monday_reader.validate_board_columns()
    if not ok:
        log.warning("⚠️  Column validation failed — check Monday board. Continuing anyway.")

    # ── Step 2: Sync Monday → Supabase products table ─────────────────────────
    log.info("Step 2: Syncing Monday products → Supabase…")
    try:
        products = monday_reader.get_all_products()
        synced = supabase_client.upsert_products(products)
        log.info(f"  Synced {synced} products from Monday")
    except Exception as e:
        log.error(f"  Monday sync failed: {e}")
        # Non-fatal: use existing products in DB for scraping

    # ── Step 3: Pick ASINs to scrape this run ─────────────────────────────────
    log.info(f"Step 3: Selecting up to {ASINS_PER_RUN} ASINs to scrape…")
    try:
        queue = supabase_client.get_products_to_scrape(ASINS_PER_RUN)
    except Exception as e:
        log.error(f"  Failed to get scrape queue: {e}")
        queue = []

    if not queue:
        log.info("  Nothing in the queue right now — all ASINs are freshly scraped.")
    else:
        log.info(f"  Queue: {[p['asin'] for p in queue]}")

    # ── Step 4: Scrape reviews ─────────────────────────────────────────────────
    log.info("Step 4: Scraping reviews…")
    total_new = 0

    for i, product in enumerate(queue):
        asin = product["asin"]
        tier = product.get("scrape_tier", 3)
        name = product.get("product_name", asin)

        log.info(f"  [{i+1}/{len(queue)}] {asin} — {name} (tier {tier})")
        log_id = None

        try:
            log_id = supabase_client.log_scrape_start(asin, "max")
            reviews, summary = scraper.scrape_asin(asin)
            new_count = supabase_client.insert_reviews(asin, reviews)
            supabase_client.update_product_after_scrape(asin, len(reviews), new_count, tier)
            if log_id:
                supabase_client.log_scrape_finish(log_id, len(reviews), new_count)
            total_new += new_count
            log.info(f"    ✓ {len(reviews)} scraped, {new_count} new reviews stored")

        except Exception as e:
            log.error(f"    ✗ Scrape failed for {asin}: {e}")
            if log_id:
                try:
                    supabase_client.log_scrape_finish(log_id, 0, 0, error=str(e))
                except Exception:
                    pass

        # Polite delay between ASINs — don't hammer Woot
        if i < len(queue) - 1:
            time.sleep(DELAY_BETWEEN_ASINS)

    log.info(f"  Scraping done: {total_new} new reviews across {len(queue)} ASINs")

    # ── Step 5: LLM compliance check ──────────────────────────────────────────
    log.info("Step 5: Running LLM compliance check on new reviews…")
    try:
        unchecked = supabase_client.get_unchecked_reviews(limit=200)
        log.info(f"  {len(unchecked)} reviews to check")

        if unchecked:
            violations = compliance_checker.check_reviews(unchecked)
            review_ids = [r["id"] for r in unchecked]
            supabase_client.mark_reviews_checked(review_ids)
            log.info(f"  {len(violations)} violations found out of {len(unchecked)} checked")

            if violations:
                inserted = supabase_client.insert_compliance_flags(violations)
                log.info(f"  {inserted} new flags added to compliance_flags table")
        else:
            violations = []
            log.info("  No unchecked reviews — nothing to analyze")

    except Exception as e:
        log.error(f"  Compliance check failed: {e}")
        violations = []

    # ── Step 6: Push violations to Google Sheets ──────────────────────────────
    log.info("Step 6: Pushing new violations to Google Sheets…")
    try:
        unsent_flags = supabase_client.get_unsent_flags(limit=100)
        log.info(f"  {len(unsent_flags)} unsent flags")

        if unsent_flags:
            pushed = sheets_writer.append_violations(unsent_flags)
            flag_ids = [f["id"] for f in unsent_flags]
            supabase_client.mark_flags_sent(flag_ids)
            log.info(f"  ✓ Pushed {pushed} rows to Google Sheets")
        else:
            log.info("  No new flags to push")

    except Exception as e:
        log.error(f"  Google Sheets push failed: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info(
        f"PIPELINE COMPLETE — "
        f"{len(queue)} ASINs scraped, "
        f"{total_new} new reviews stored, "
        f"{len(violations) if 'violations' in dir() else 0} violations flagged"
    )
    log.info("=" * 60)


if __name__ == "__main__":
    run()
