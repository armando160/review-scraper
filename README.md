# Amazon Review Compliance Pipeline

Automated daily scraper that:
1. Reads product ASINs from Monday.com
2. Scrapes Amazon reviews via Woot's public API
3. Stores all reviews in Supabase for the analytics dashboard
4. Runs LLM compliance checks on negative reviews (Groq / Llama 3.3 70B)
5. Pushes TOS violations to a Google Sheet removal queue

---

## Setup (one-time)

### 1. Run the database migration

In your [Supabase SQL Editor](https://supabase.com/dashboard/project/bjrtlozqpfbrsllthxnm/sql):

1. First run `migrations/001_pipeline_columns.sql` (adds scheduling and tracking columns)

### 2. Set GitHub Actions Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

Add each of these:

| Secret Name | Value |
|---|---|
| `SUPABASE_URL` | `https://bjrtlozqpfbrsllthxnm.supabase.co` |
| `SUPABASE_KEY` | Your Supabase anon key (`eyJhbGci...`) |
| `GROQ_API_KEY` | Your Groq API key (`gsk_...`) |
| `MONDAY_TOKEN` | Your Monday.com API token |
| `GOOGLE_SA_JSON` | The **entire contents** of `review-scraper-497502-b62fa38a9510.json` as a single line |

**For `GOOGLE_SA_JSON`:** Open the JSON file, copy everything, paste it as the secret value. GitHub handles multi-line secrets fine.

### 3. Verify Google Sheets access

The service account `armando@review-scraper-497502.iam.gserviceaccount.com` must have **Editor** access to your Google Sheet. Share it with that email if not already done.

### 4. Enable GitHub Actions

Go to your repo → **Actions** → click "I understand my workflows, go ahead and enable them"

---

## Schedule

Runs **4 times per day** (6 AM, 12 PM, 6 PM, 12 AM EST).
Each run scrapes 20 ASINs, selected by priority:

| Tier | Rating | Scrape Every |
|---|---|---|
| 1 | < 4.0 | 24 hours |
| 2 | 4.0 – 4.4 | 48 hours |
| 3 | 4.5 – 4.7 | 72 hours |
| 4 | > 4.7 | 96 hours |

Full catalog (~300 products) completes in **3–4 days**.

**Dashboard freshness disclaimer:** *Review data refreshes on a rolling 4-day cycle. Priority products (rating below 4.0) update daily. Each chart shows the most recent scrape date per product.*

---

## Manual Run

Trigger a run anytime:
1. Go to **Actions → Review Pipeline → Run workflow**
2. Optionally paste specific ASINs to force-scrape

Or locally:
```bash
export SUPABASE_URL="..."
export SUPABASE_KEY="..."
export GROQ_API_KEY="..."
export MONDAY_TOKEN="..."
export GOOGLE_SA_JSON='{"type":"service_account",...}'
python pipeline.py
```

---

## File Structure

```
review-pipeline/
├── pipeline.py              # Main entry point
├── config.py                # All settings (reads from env vars)
├── monday_reader.py         # Fetches ASINs from Monday.com
├── supabase_client.py       # All database operations
├── scraper.py               # Wrapper for amazon_review_scraper.py
├── compliance_checker.py    # Groq LLM compliance analysis
├── sheets_writer.py         # Pushes violations to Google Sheets
├── amazon_review_scraper.py # The actual review scraper
├── requirements.txt
├── migrations/
│   └── 001_pipeline_columns.sql
└── .github/
    └── workflows/
        └── scrape.yml
```

---

## Google Sheets Output Columns

| Column | Description |
|---|---|
| ASIN | Amazon ASIN |
| Product Name | From Monday.com |
| Amazon Product Link | `amazon.com/dp/{ASIN}` |
| Filtered Review Page | Filtered to the review's star rating |
| Reviewer | Author name |
| Star Rating | 1–3 (we only check negative reviews) |
| Review Date | When the review was posted |
| Verified Purchase | Yes / No |
| Review Title | Title of the review |
| Review Text | Full text (truncated at 1000 chars) |
| Violation Type | e.g. EMPTY_MEANINGLESS, SELLER_FEEDBACK_ONLY |
| LLM Reasoning | One-sentence explanation from Groq |
| Confidence | 0.0–1.0 (only ≥ 0.70 shown) |
| Report Status | You update this: Pending / Submitted / Removed / Rejected |
| Date Submitted | You fill in when you report to Amazon |
| Outcome | You fill in: Removed / Rejected |
| Notes | Any additional notes |

---

## Troubleshooting

**Pipeline fails with "Queue empty"**
→ All ASINs were recently scraped. Normal — wait for next scheduled run.

**Monday sync warning about columns**
→ Someone renamed a column. Update `config.py` MONDAY_COL_* constants.

**Groq 429 errors**
→ Rate limit hit. Pipeline retries automatically. If persistent, reduce COMPLIANCE_BATCH_SIZE.

**Google Sheets push fails**
→ Check service account has Editor access on the sheet. Verify GOOGLE_SA_JSON secret is valid JSON.
