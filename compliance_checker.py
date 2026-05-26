"""
compliance_checker.py — LLM-powered review TOS compliance analysis.

Primary: Claude API (claude-haiku-4-5) — fast, high limits on enterprise plans.
Fallback: Groq (llama-3.3-70b-versatile) — free tier backup.

Sends batches of reviews to the LLM.
Returns structured violation results with type, reason, and confidence.
Only flags genuine violations — not legitimate product complaints.
"""
import json
import logging
import time

import requests
from config import (
    GROQ_API_KEY, GROQ_MODEL,
    CLAUDE_API_KEY, CLAUDE_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL, GEMINI_URL,
    COMPLIANCE_BATCH_SIZE, MIN_CONFIDENCE,
)

log = logging.getLogger(__name__)

GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
CLAUDE_URL = "https://api.anthropic.com/v1/messages"

# ── System prompt ─────────────────────────────────────────────────────────────
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

USER_PROMPT_TEMPLATE = """Analyze each review below for Amazon TOS violations.

Respond with ONLY a valid JSON array. Each object must have:
{{"id": <int>, "violates": <bool>, "type": <string or null>, "reason": <one sentence>, "confidence": <float 0.0-1.0>}}

If a review does NOT violate, set violates=false, type=null, confidence=1.0.

REVIEWS:
{reviews_json}"""


def _parse_llm_response(content: str) -> list[dict]:
    """Strip markdown fences and parse JSON from LLM response."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(l for l in lines if not l.strip().startswith("```"))
    return json.loads(content.strip())


def _call_claude(reviews: list[dict]) -> list[dict]:
    """Call Claude API (primary)."""
    review_data = [
        {
            "id":       r["id"],
            "rating":   r["rating"],
            "verified": r["is_verified_purchase"],
            "title":    (r.get("title") or "")[:200],
            "text":     (r.get("review_text") or "")[:1000],
        }
        for r in reviews
    ]

    body = {
        "model":      CLAUDE_MODEL,
        "max_tokens": 1500,
        "system":     SYSTEM_PROMPT,
        "messages": [
            {
                "role":    "user",
                "content": USER_PROMPT_TEMPLATE.format(
                    reviews_json=json.dumps(review_data, ensure_ascii=False)
                ),
            }
        ],
    }

    for attempt in range(2):
        try:
            resp = requests.post(
                CLAUDE_URL,
                json=body,
                headers={
                    "x-api-key":         CLAUDE_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type":      "application/json",
                },
                timeout=60,
            )

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10))
                log.warning(f"Claude rate limit hit, waiting {wait}s…")
                time.sleep(wait + 1)
                continue

            resp.raise_for_status()
            content = resp.json()["content"][0]["text"]
            return _parse_llm_response(content)

        except json.JSONDecodeError as e:
            log.error(f"Claude returned invalid JSON (attempt {attempt+1}): {e}")
            if attempt == 1:
                return []
        except Exception as e:
            log.error(f"Claude API error (attempt {attempt+1}): {e}")
            if attempt == 1:
                return []
            time.sleep(3)

    return []


def _call_groq(reviews: list[dict]) -> list[dict]:
    """Call Groq API (fallback)."""
    review_data = [
        {
            "id":       r["id"],
            "rating":   r["rating"],
            "verified": r["is_verified_purchase"],
            "title":    (r.get("title") or "")[:200],
            "text":     (r.get("review_text") or "")[:1000],
        }
        for r in reviews
    ]

    body = {
        "model":       GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": USER_PROMPT_TEMPLATE.format(
                reviews_json=json.dumps(review_data, ensure_ascii=False)
            )},
        ],
        "max_tokens":  1500,
        "temperature": 0.1,
    }

    for attempt in range(2):
        try:
            resp = requests.post(
                GROQ_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type":  "application/json",
                },
                timeout=45,
            )

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 12))
                # Cap at 30s — if Groq wants us to wait longer, daily limit is hit
                # Better to skip remaining reviews than hang the pipeline for 30+ minutes
                if wait > 30:
                    log.warning(f"Groq daily limit hit (retry-after={wait}s) — skipping remaining reviews. Add CLAUDE_API_KEY to resolve.")
                    return []
                log.warning(f"Groq rate limit hit, waiting {wait}s…")
                time.sleep(wait + 1)
                continue

            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return _parse_llm_response(content)

        except json.JSONDecodeError as e:
            log.error(f"Groq returned invalid JSON (attempt {attempt+1}): {e}")
            if attempt == 1:
                return []
        except Exception as e:
            log.error(f"Groq API error (attempt {attempt+1}): {e}")
            if attempt == 1:
                return []
            time.sleep(3)

    return []


def _call_gemini(reviews: list[dict]) -> list[dict]:
    """Call Gemini API."""
    review_data = [
        {
            "id":       r["id"],
            "rating":   r["rating"],
            "verified": r["is_verified_purchase"],
            "title":    (r.get("title") or "")[:200],
            "text":     (r.get("review_text") or "")[:1000],
        }
        for r in reviews
    ]

    prompt = (
        SYSTEM_PROMPT + "\n\n" +
        USER_PROMPT_TEMPLATE.format(
            reviews_json=json.dumps(review_data, ensure_ascii=False)
        )
    )

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1500},
    }

    for attempt in range(2):
        try:
            resp = requests.post(
                f"{GEMINI_URL}?key={GEMINI_API_KEY}",
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=60,
            )

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10))
                if wait > 30:
                    log.warning(f"Gemini daily limit hit — skipping remaining reviews.")
                    return []
                log.warning(f"Gemini rate limit hit, waiting {wait}s…")
                time.sleep(wait + 1)
                continue

            resp.raise_for_status()
            content = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            return _parse_llm_response(content)

        except json.JSONDecodeError as e:
            raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"] if resp else ""
            log.error(f"Gemini invalid JSON (attempt {attempt+1}): {e} | raw: {raw[:300]}")
            if attempt == 1:
                return []
        except Exception as e:
            log.error(f"Gemini API error (attempt {attempt+1}): {e}")
            if attempt == 1:
                return []
            time.sleep(3)

    return []


def _call_llm(reviews: list[dict]) -> list[dict]:
    """
    Call LLM with automatic fallback chain:
    1. Claude  — best limits on enterprise, preferred
    2. Gemini  — 1,500 req/day free, fast
    3. Groq    — last resort, low daily limits

    Each is tried in order, skipped if key not set or call fails.
    """
    if CLAUDE_API_KEY:
        results = _call_claude(reviews)
        if results:
            return results
        log.warning("Claude failed, trying Gemini…")

    if GEMINI_API_KEY:
        results = _call_gemini(reviews)
        if results:
            return results
        log.warning("Gemini failed, trying Groq…")

    if GROQ_API_KEY:
        return _call_groq(reviews)

    log.error("No LLM API key available — set CLAUDE_API_KEY, GEMINI_API_KEY, or GROQ_API_KEY")
    return []


def check_reviews(reviews: list[dict]) -> list[dict]:
    """
    Check a list of review dicts for TOS violations.

    Input:  list of review rows from supabase_client.get_unchecked_reviews()
    Output: list of violation dicts ready for supabase_client.insert_compliance_flags()

    Violations below MIN_CONFIDENCE are silently dropped.
    """
    violations = []
    total      = len(reviews)
    checked    = 0

    for i in range(0, total, COMPLIANCE_BATCH_SIZE):
        batch   = reviews[i:i + COMPLIANCE_BATCH_SIZE]
        results = _call_llm(batch)

        result_by_id = {r["id"]: r for r in results}

        for review in batch:
            rid    = review["id"]
            result = result_by_id.get(rid)

            if not result:
                log.warning(f"No LLM result for review id={rid}, skipping")
                continue

            if result.get("violates") and result.get("confidence", 0) >= MIN_CONFIDENCE:
                violations.append({
                    "review_id":      rid,
                    "asin":           review["asin"],
                    "violation_type": result.get("type", "UNKNOWN"),
                    "reason":         result.get("reason", ""),
                    "confidence":     result.get("confidence", 0.0),
                    # Extra context passed through to sheets_writer
                    "_author":    review.get("author", ""),
                    "_title":     review.get("title", ""),
                    "_text":      review.get("review_text", ""),
                    "_rating":    review.get("rating"),
                    "_date":      review.get("review_date", ""),
                    "_verified":  review.get("is_verified_purchase"),
                })

        checked += len(batch)
        log.info(f"Compliance: {checked}/{total} checked, {len(violations)} violations so far")

        # Small pause between batches
        if i + COMPLIANCE_BATCH_SIZE < total:
            time.sleep(0.3)

    return violations
