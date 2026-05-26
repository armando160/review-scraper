-- ============================================================
-- Migration 001: Add pipeline-specific columns
-- Run this in Supabase SQL Editor BEFORE deploying the pipeline
-- ============================================================

-- products: add scrape scheduling columns
ALTER TABLE products ADD COLUMN IF NOT EXISTS scrape_tier     INTEGER      DEFAULT 3;
ALTER TABLE products ADD COLUMN IF NOT EXISTS next_scrape_at  TIMESTAMPTZ  DEFAULT NOW();

-- products: index for priority queue (most important query in pipeline)
CREATE INDEX IF NOT EXISTS idx_products_scrape_queue
    ON products (scrape_tier ASC, next_scrape_at ASC);

-- reviews: track which reviews have been through compliance check
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS compliance_checked BOOLEAN DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_reviews_unchecked
    ON reviews (compliance_checked) WHERE compliance_checked = FALSE;

-- compliance_flags: track which flags have been pushed to Google Sheets
ALTER TABLE compliance_flags ADD COLUMN IF NOT EXISTS sent_to_sheets BOOLEAN DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_flags_unsent
    ON compliance_flags (sent_to_sheets) WHERE sent_to_sheets = FALSE;
