"""
Unit tests for multi-layer duplicate prevention and fail-closed safety semantics.

Tests cover:
- URL canonicalization: scheme/host lowercase, trailing slashes, fragments, tracking params (utm_*, fbclid, ref, etc.), resource params preserved.
- Title normalization: case, punctuation, whitespace stripping.
- Content fingerprinting: deterministic SHA-256 hash.
- Deterministic duplicate checking across URL, title, and content fingerprint.
- Fail-Closed semantic duplicate handling: UNKNOWN duplicate status results in candidate rejection.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services.editorial_engine import EditorialEngine
from utils.duplicate import (
    DuplicateStatus,
    canonical_title,
    check_deterministic_duplicate,
    content_fingerprint,
    normalize_url,
)


class TestDuplicatePrevention(unittest.IsolatedAsyncioTestCase):

    def test_url_canonicalization_tracking_params(self):
        url1 = "HTTPS://Example.com/article/123/?utm_source=twitter&utm_medium=social#section"
        url2 = "https://example.com/article/123"
        self.assertEqual(normalize_url(url1), normalize_url(url2))

    def test_url_canonicalization_preserves_resource_params(self):
        url1 = "https://example.com/watch?v=dQw4w9WgXcQ&utm_source=share"
        url2 = "https://example.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(normalize_url(url1), normalize_url(url2))
        self.assertIn("v=dQw4w9WgXcQ", normalize_url(url1))

    def test_url_canonicalization_trailing_slash(self):
        url1 = "https://news.org/tech/ai-breakthrough/"
        url2 = "https://news.org/tech/ai-breakthrough"
        self.assertEqual(normalize_url(url1), normalize_url(url2))

    def test_title_normalization(self):
        title1 = "  BREAKING: New AI Model Released!  "
        title2 = "breaking new ai model released"
        self.assertEqual(canonical_title(title1), canonical_title(title2))

    def test_content_fingerprint_deterministic(self):
        text1 = "Deep Learning Transformers benchmarked at 10x speedup."
        text2 = "deep  learning   transformers benchmarked at 10x speedup. "
        self.assertEqual(content_fingerprint(text1), content_fingerprint(text2))

    def test_check_deterministic_duplicate_url_match(self):
        candidate_url = "https://example.com/story?utm_source=news"
        candidate_title = "Unique Title"
        candidate_content = "Unique content text"
        existing = [
            {"url": "https://example.com/story", "title": "Other", "content": "Other content"}
        ]
        is_dup, reason = check_deterministic_duplicate(candidate_url, candidate_title, candidate_content, existing)
        self.assertTrue(is_dup)
        self.assertIn("URL match", reason)

    def test_check_deterministic_duplicate_title_match(self):
        candidate_url = "https://example.com/story1"
        candidate_title = "Major CVE Discovered in AI Library!"
        candidate_content = "Unique content"
        existing = [
            {"url": "https://example.com/story2", "title": "Major CVE Discovered in AI Library", "content": "Other content"}
        ]
        is_dup, reason = check_deterministic_duplicate(candidate_url, candidate_title, candidate_content, existing)
        self.assertTrue(is_dup)
        self.assertIn("Title match", reason)

    def test_check_deterministic_duplicate_fingerprint_match(self):
        candidate_url = "https://example.com/story1"
        candidate_title = "Headline A"
        candidate_content = "Identical text content across multiple sources."
        existing = [
            {"url": "https://example.com/story2", "title": "Headline B", "content": "Identical text content across multiple sources."}
        ]
        is_dup, reason = check_deterministic_duplicate(candidate_url, candidate_title, candidate_content, existing)
        self.assertTrue(is_dup)
        self.assertIn("Content fingerprint match", reason)

    async def test_check_semantic_duplicate_fail_closed_unknown(self):
        """Verify Requirement #3: Exception during semantic duplicate check returns UNKNOWN status."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(side_effect=RuntimeError("Provider network timeout"))
        mock_memory = MagicMock()
        mock_memory.get_feed.return_value = [{"text": "Existing post title"}]

        engine = EditorialEngine(llm_client=mock_llm, memory_store=mock_memory)
        status, matched = await engine.check_semantic_duplicate("agent-1", "New Title", "New Summary")

        self.assertEqual(status, DuplicateStatus.UNKNOWN)
        self.assertIsNone(matched)

    async def test_evaluate_candidate_unknown_rejected(self):
        """Verify Requirement #3: Candidate is rejected when semantic duplicate check returns UNKNOWN."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(side_effect=RuntimeError("LLM failed"))
        mock_memory = MagicMock()
        mock_memory.compute_topic_hash.return_value = "hash123"
        mock_memory.is_candidate_hash_covered.return_value = False
        mock_memory.get_feed.return_value = [{"text": "Previous post text"}]

        engine = EditorialEngine(llm_client=mock_llm, memory_store=mock_memory)
        cand = {"title": "Test Title", "summary": "Test summary", "source_urls": ["https://example.com"]}
        profile = {"domain": "AI Security", "name": "Tester"}

        result = await engine.evaluate_candidate("agent-1", profile, cand, set())
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("UNKNOWN", result["rejection_reason"])


if __name__ == "__main__":
    unittest.main()
