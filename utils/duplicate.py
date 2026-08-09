"""
Duplicate detection utilities for TRON autonomous news publisher.

Provides multi-layer duplicate detection:
1. Canonical URL comparison
2. Canonical title comparison
3. Content fingerprint (SHA-256) comparison
4. Fail-closed duplicate status enum (NOT_DUPLICATE, DUPLICATE, UNKNOWN)
"""

import hashlib
import re
from enum import Enum
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Common tracking parameters to strip during URL canonicalization
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_cid", "utm_reader", "utm_name", "utm_social", "utm_viz_id",
    "fbclid", "gclid", "msclkid", "dclid", "twclid", "zanpid",
    "_hsenc", "_hsmi", "mc_cid", "mc_eid", "ref", "ref_src", "source", "rc"
}


class DuplicateStatus(str, Enum):
    NOT_DUPLICATE = "NOT_DUPLICATE"
    DUPLICATE = "DUPLICATE"
    UNKNOWN = "UNKNOWN"


def normalize_url(url: str) -> str:
    """Normalize URL by converting scheme/host to lowercase, stripping fragments,
    removing trailing slashes from path, and filtering out common tracking parameters.
    Preserves structural parameters like 'id', 'v', 'p', 'article_id', etc.
    """
    if not url or not url.strip():
        return ""

    url = url.strip()
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        # Remove trailing slash from path unless it is root '/'
        path = parsed.path
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")

        # Parse query params and strip tracking params
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        filtered_pairs = [
            (k, v) for k, v in query_pairs
            if k.lower() not in TRACKING_PARAMS
        ]
        # Sort parameter pairs for deterministic ordering
        filtered_pairs.sort()
        new_query = urlencode(filtered_pairs)

        # Omit fragment
        canonical_tuple = (scheme, netloc, path, parsed.params, new_query, "")
        return urlunparse(canonical_tuple)
    except Exception:
        return url.strip().lower()


def canonical_title(title: str) -> str:
    """Normalize title for fuzzy exact matching:
    - Lowercase
    - Strip punctuation
    - Normalize whitespace
    """
    if not title or not title.strip():
        return ""

    text = title.strip().lower()
    # Replace punctuation with single space
    text = re.sub(r'[^\w\s]', ' ', text)
    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def content_fingerprint(text: str) -> str:
    """Generate a deterministic SHA-256 hash of normalized content text."""
    if not text or not text.strip():
        return ""

    # Normalize text by lowercasing and collapsing whitespace
    normalized = re.sub(r'\s+', ' ', text.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def check_deterministic_duplicate(
    candidate_url: str,
    candidate_title: str,
    candidate_content: str,
    existing_records: list[dict]
) -> tuple[bool, str]:
    """Check candidate against a list of existing published articles/candidates
    using URL, Title, and Content Fingerprint.

    Returns:
        (is_duplicate: bool, match_reason: str)
    """
    cand_url_norm = normalize_url(candidate_url)
    cand_title_norm = canonical_title(candidate_title)
    cand_fp = content_fingerprint(candidate_content)

    for rec in existing_records:
        rec_raw_url = rec.get("url") or rec.get("source_url") or (rec.get("sources", [""])[0] if isinstance(rec.get("sources"), list) and rec.get("sources") else "")
        rec_url = normalize_url(rec_raw_url)
        if cand_url_norm and rec_url and cand_url_norm == rec_url:
            return True, f"URL match: {rec_url}"

        rec_title = canonical_title(rec.get("title") or rec.get("headline") or "")
        if cand_title_norm and rec_title and cand_title_norm == rec_title:
            return True, f"Title match: {rec_title}"

        rec_content = rec.get("content") or rec.get("raw_content") or rec.get("text") or ""
        rec_fp = rec.get("content_fingerprint") or (content_fingerprint(rec_content) if rec_content else "")
        if cand_fp and rec_fp and cand_fp == rec_fp:
            return True, f"Content fingerprint match: {rec_fp}"

    return False, ""
