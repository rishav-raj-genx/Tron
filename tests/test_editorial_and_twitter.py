"""
Unit tests for Editorial Engine synthesis rules, fake fallback prevention,
article length validation, and X/Twitter credential independence.

Tests cover:
- Articles > 280 characters are accepted and NOT truncated.
- Exception during synthesis raises error (NO fake summary/fallback generated).
- Application startup and configuration without X/Twitter environment variables.
- Verification that no tweepy module dependency exists.
"""

import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

from config.settings import Settings, settings
from services.editorial_engine import EditorialEngine


class TestEditorialAndTwitter(unittest.IsolatedAsyncioTestCase):

    def test_no_tweepy_dependency(self):
        """Requirement #8: Verify tweepy module is not imported or required."""
        self.assertNotIn("tweepy", sys.modules)

    def test_settings_without_twitter_credentials(self):
        """Requirement #8: App settings instantiate cleanly without X/Twitter env vars."""
        s = Settings()
        self.assertFalse(hasattr(s, "x_api_key"))
        self.assertFalse(hasattr(s, "x_bearer_token"))

    async def test_article_greater_than_280_chars_accepted(self):
        """Requirement #4: Articles > 280 chars are accepted and not truncated."""
        long_article_text = (
            "Researchers have discovered a critical side-channel vulnerability in foundational "
            "transformer attention mechanisms that allows unprivileged user processes to reconstruct "
            "private prompt cache state in multi-tenant LLM inference servers. The attack, dubbed "
            "CacheBleed, exploits microscopic timing variances in speculative decoding implementations "
            "across modern GPU architectures. Mitigation requires zeroing KV-cache memory pages."
        )
        self.assertGreater(len(long_article_text), 280)

        mock_llm = MagicMock()
        mock_llm.generate_structured = AsyncMock(return_value={
            "post_text": long_article_text,
            "rationale": "High impact security vulnerability disclosure.",
            "sources": ["https://arxiv.org/abs/2601.00001"]
        })

        engine = EditorialEngine(llm_client=mock_llm)
        candidate = {
            "title": "CacheBleed Vulnerability",
            "summary": "Side-channel attack on prompt cache",
            "source_urls": ["https://arxiv.org/abs/2601.00001"]
        }
        persona = {"name": "Ada", "domain": "AI Security"}

        result = await engine.synthesize_post_for_leader("agent-1", persona, candidate)

        self.assertEqual(result["text"], long_article_text)
        self.assertEqual(len(result["text"]), len(long_article_text))
        self.assertGreater(len(result["text"]), 280)

    async def test_synthesis_failure_raises_exception_no_fake_fallback(self):
        """Requirement #6: LLM synthesis failure raises exception, no fake fallback generated."""
        mock_llm = MagicMock()
        mock_llm.generate_structured = AsyncMock(side_effect=RuntimeError("Provider API down"))

        engine = EditorialEngine(llm_client=mock_llm)
        candidate = {
            "title": "Failing Story",
            "summary": "Some summary text",
            "source_urls": ["https://example.com/source"]
        }
        persona = {"name": "Ada", "domain": "AI Security"}

        with self.assertRaises(RuntimeError) as ctx:
            await engine.synthesize_post_for_leader("agent-1", persona, candidate)

        self.assertIn("AI article synthesis failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
