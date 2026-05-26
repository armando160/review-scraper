"""
config.py — All configuration pulled from environment variables.
Set these as GitHub Actions secrets (never hardcode values here).
"""
import os

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://bjrtlozqpfbrsllthxnm.supabase.co")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# ── Groq ──────────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_MODEL   = "llama-3.3-70b-versatile"

# ── Gemini API ───────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL   = "gemini-2.5-flash"
GEMINI_URL     = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

# ── Claude API ───────────────────────────────────────────────────────────────
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")   # set in GitHub secrets
CLAUDE_MODEL   = "claude-haiku-4-5-20251001"            # fast + cheap, ideal for classification

# ── Monday.com ────────────────────────────────────────────────────────────────
MONDAY_TOKEN       = os.environ["MONDAY_TOKEN"]
MONDAY_BOARD_ID    = "8574487078"
MONDAY_COL_ASIN    = "text_mknhd0s7"
MONDAY_COL_RATING  = "numeric_mknj71zj"
MONDAY_COL_REVIEWS = "numeric_mknjr9cg"
MONDAY_COL_STATUS  = "status"
MONDAY_COL_PRODUCT = "text_mknhzj47"

# ── Google Sheets ─────────────────────────────────────────────────────────────
SHEETS_ID  = "1eUGdYlb0-xOEJOgIb1nytv8Ag7RWB3YIJRbdL-W4b5c"
SHEETS_GID = 1517234567          # the specific tab gid from the URL
# Full service-account JSON stored as a GitHub Actions secret (single-line JSON string)
GOOGLE_SA_JSON = os.environ["GOOGLE_SA_JSON"]

# ── Scraper ───────────────────────────────────────────────────────────────────
ASINS_PER_RUN       = 20     # how many ASINs to scrape each pipeline run
DELAY_BETWEEN_ASINS = 2.5    # seconds between ASINs — polite to Woot
SCRAPE_MODE         = "max"  # basic | full | max

# ── Compliance ────────────────────────────────────────────────────────────────
COMPLIANCE_BATCH_SIZE = 10    # reviews sent per LLM API call (10 = ~20 calls per 200 reviews)
MIN_CONFIDENCE        = 0.70  # only push to Google Sheet if confidence ≥ this

# ── Scrape cadence by tier (hours between scrapes) ────────────────────────────
# Tier is assigned in monday_reader.py based on product rating
TIER_HOURS = {
    1: 24,   # rating < 4.0   → daily
    2: 48,   # rating 4.0–4.4 → every 2 days
    3: 72,   # rating 4.5–4.7 → every 3 days
    4: 96,   # rating > 4.7 or inactive → every 4 days
}

# ── Statuses considered "active" (worth scraping) ─────────────────────────────
ACTIVE_STATUSES = {
    "Active",
    "Active/Only DI",
    "Top 10",
    "NewLaunch",
    "DTC only",
    "Relaunched",
    "Discontinue-PD Iterate",
    "Discontinued",       # keep for dashboard completeness
}

# Statuses that are high-priority for compliance (rating cutoff handled in tier assignment)
COMPLIANCE_SKIP_STATUSES = {"To be launched"}

# ── Sheet header (matches existing sheet columns exactly) ─────────────────────
SHEET_HEADER = [
    "ASIN",
    "Product Name",
    "Amazon Product Link",
    "Reviews Page",
    "Reviewer",
    "Star Rating",
    "Review Date",
    "Verified Purchase",
    "Review Title",
    "Review Text",
    "Primary Violation",
    "Confidence",
    "LLM Reasoning",
    "Report Status",
    "Date Submitted",
    "Outcome",
    "Notes",
]
