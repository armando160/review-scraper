"""
compliance_checker.py — LLM-powered review TOS compliance analysis via Groq.

Sends batches of 1-3 star reviews to Llama 3.3 70B.
Returns structured violation results with type, reason, and confidence.
Only flags genuine violations — not legitimate product complaints.
"""
import json
import logging
import time
from typing import Optional

import requests
from config import GROQ_API_KEY, GROQ_MODEL, COMPLIANCE_BATCH_SIZE, MIN_CONFIDENCE

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
HEADERS  = {
    "Authorization": f"Bearer {GROQ_API_KEY}",
    "Content-Type":  "application/json",
}

# ── System prompt (sent once per batch call) ──────────────────────────────────
SYSTEM_PROMPT = """You are an Amazon review compliance analyst. Your job is to identify reviews that violate Amazon's Community Guidelines and should be reported for removal.

VIOLATION TYPES (only flag if clearly applicable):

HIGH CONFIDENCE violations (Amazon almost certainly removes these):
- EMPTY_MEANINGLESS: Review body has no substantive product feedback (under ~20 words with nothing specific)
- SELLER_FEEDBACK_ONLY: Review is ENTIRELY about shipping, delivery, or Amazon logistics — zero product content
- WRONG_PRODUCT: Review is clearly about a different product than listed
- INCENTIVIZED: Reviewer states they received product free/discounted in exchange for review
- PROMOTIONAL_SPAM: Contains links, promo codes, or self-promotion
- BILLING_ONLY: Review is entirely about refund disputes or billing — no product experience

MEDIUM CONFIDENCE violations:
- COMPETITOR_ATTACK: Unverified + no real product experience described + recommends a specific competing product/brand
- INAPPROPRIATE_LANGUAGE: Contains threats, personal attacks, or harassment

NEVER FLAG (these are legitimate reviews Amazon keeps):
- Genuine product complaints, even if harsh or angry
- Reviews that mention customer service ALONGSIDE a product complaint
- Reviews saying the product stopped working, broke, leaked, malfunctioned
- Short reviews that still describe a real product experience (e.g. "terrible, broke in 2 weeks")
- Price complaints combined with product quality feedback
- Reviews expressing frustration with the company while also describing the product

CRITICAL RULE: If the review describes ANY real experience with the physical product itself, it is LEGITIMATE — do not flag it, even if it's also angry about customer service, returns, or refunds."""


def _call_groq(reviews: list[dict]) -> list[dict]:
    """
    Send a batch of reviews to Groq. Returns raw parsed JSON result list.
    Retries once on rate limit (429).
    """
    # Build the user prompt
    review_data = []
    for r in reviews:
        review_data.append({
            "id":       r["id"],
            "rating":   r["rating"],
            "verified": r["is_verified_purchase"],
            "title":    (r.get("title") or "")[:200],
            "text":     (r.get("review_text") or "")[:1000],
        })

    user_prompt = (
        "Analyze each review below for Amazon TOS violations.\n\n"
        "Respond with ONLY a valid JSON array. Each object must have:\n"
        '{"id": <int>, "violates": <bool>, "type": <string or null>, '
        '"reason": <one sentence>, "confidence": <float 0.0-1.0>}\n\n'
        "If a review does NOT violate, set violates=false, type=null, confidence=1.0.\n\n"
        "REVIEWS:\n" + json.dumps(review_data, ensure_ascii=False)
    )

    body = {
        "model":       GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens":  800,
        "temperature": 0.1,   # low temperature = consistent, deterministic output
    }

    for attempt in range(2):
        try:
            resp = requests.post(GROQ_URL, json=body, headers=HEADERS, timeout=45)

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 12))
                log.warning(f"Groq rate limit hit, waiting {wait}s…")
                time.sleep(wait + 1)
                continue

            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()

            # Strip markdown fences if present
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(
                    l for l in lines
                    if not l.strip().startswith("```")
                )

            return json.loads(content.strip())

        except json.JSONDecodeError as e:
            log.error(f"Groq returned invalid JSON (attempt {attempt+1}): {e}")
            log.debug(f"Raw content: {content[:500]}")
            if attempt == 1:
                return []
        except Exception as e:
            log.error(f"Groq API error (attempt {attempt+1}): {e}")
            if attempt == 1:
                return []
            time.sleep(3)

    return []


def check_reviews(reviews: list[dict]) -> list[dict]:
    """
    Check a list of review dicts for TOS violations.

    Input: list of review rows from supabase_client.get_unchecked_reviews()
    Output: list of violation dicts ready for supabase_client.insert_compliance_flags()

    Violations below MIN_CONFIDENCE are silently dropped.
    """
    violations = []
    total      = len(reviews)
    checked    = 0

    # Process in batches
    for i in range(0, total, COMPLIANCE_BATCH_SIZE):
        batch = reviews[i:i + COMPLIANCE_BATCH_SIZE]
        results = _call_groq(batch)

        # Map results back to review records
        result_by_id = {r["id"]: r for r in results}

        for review in batch:
            rid = review["id"]
            result = result_by_id.get(rid)

            if not result:
                log.warning(f"No LLM result for review id={rid}, skipping")
                continue

            if result.get("violates") and result.get("confidence", 0) >= MIN_CONFIDENCE:
                violations.append({
                    "review_id":       rid,
                    "asin":            review["asin"],
                    "violation_type":  result.get("type", "UNKNOWN"),
                    "reason":          result.get("reason", ""),
                    "confidence":      result.get("confidence", 0.0),
                    # Extra context for Google Sheets (not stored in DB, used in sheets_writer)
                    "_author":         review.get("author", ""),
                    "_title":          review.get("title", ""),
                    "_text":           review.get("review_text", ""),
                    "_rating":         review.get("rating"),
                    "_date":           review.get("review_date", ""),
                    "_verified":       review.get("is_verified_purchase"),
                })

        checked += len(batch)
        log.info(f"Compliance: {checked}/{total} checked, {len(violations)} violations so far")

        # Brief pause between batches to be polite to Groq
        if i + COMPLIANCE_BATCH_SIZE < total:
            time.sleep(0.5)

    return violations
