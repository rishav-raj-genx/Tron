"""Regression coverage for reachable, live-only discovery fallback paths and secret log sanitization."""

import unittest
import os
import tempfile
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx

from services.topic_discovery import TopicDiscoveryService
from services.autonomous_publisher import AutonomousPublisherService
from services.memory import AgentMemoryStore
from utils.api import sanitize_url_credentials


SEARCH_TEXT = "Search results:\nAI security research https://example.org/live-paper"
ARXIV_CANDIDATE = {
    "title": "Live arXiv AI security evaluation",
    "summary": "A reproducible live research result documents an AI security weakness.",
    "source_urls": ["https://arxiv.org/abs/2401.01234"],
    "domain_relevance": "Live arXiv research relevant to AI Security",
    "source_quality": "Primary research source (arXiv Atom)",
    "topic_hash": "test-hash",
    "discovered_at": "2026-08-09T00:00:00Z",
}

REAL_ARXIV_ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <link href="http://arxiv.org/api/query?search_query=cat:cs.AI" rel="self" type="application/atom+xml"/>
  <title type="html">ArXiv Query: search_query=cat:cs.AI</title>
  <id>http://arxiv.org/api/query?search_query=cat:cs.AI</id>
  <updated>2026-08-09T00:00:00Z</updated>
  <opensearch:totalResults xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">2</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2401.01234v1</id>
    <updated>2026-08-08T12:00:00Z</updated>
    <published>2026-08-08T12:00:00Z</published>
    <title> Adversarial Prompt Injection Mitigations in Quantized LLMs </title>
    <summary> We evaluate jailbreak vulnerabilities across edge AI deployments and document reproducible defenses. </summary>
    <author><name>Alice Smith</name></author>
    <link href="http://arxiv.org/abs/2401.01234v1" rel="alternate" type="text/html"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.05678v2</id>
    <updated>2026-08-08T14:00:00Z</updated>
    <published>2026-08-08T14:00:00Z</published>
    <title> Side-Channel Attacks on Shared GPU Inference Clusters </title>
    <summary> Analysis of hardware side-channel leakage during KV-cache speculative decoding. </summary>
    <author><name>Bob Jones</name></author>
    <link href="http://arxiv.org/abs/2401.05678v2" rel="alternate" type="text/html"/>
  </entry>
</feed>"""


class TestDiscoveryFallback(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.discovery = TopicDiscoveryService()

    async def test_valid_gemini_candidates_skip_arxiv(self):
        self.discovery._extract_candidates_from_search = AsyncMock(return_value=[ARXIV_CANDIDATE])
        self.discovery._discover_from_arxiv = AsyncMock(return_value=[ARXIV_CANDIDATE])
        with patch("services.topic_discovery.web_search", new=AsyncMock(return_value=SEARCH_TEXT)) as search:
            result = await self.discovery.discover_candidate_topics("AI Security")
        search.assert_awaited_once()
        self.discovery._extract_candidates_from_search.assert_awaited_once()
        self.discovery._discover_from_arxiv.assert_not_awaited()
        self.assertEqual(result, [ARXIV_CANDIDATE])

    async def test_search_exception_reaches_arxiv(self):
        self.discovery._discover_from_arxiv = AsyncMock(return_value=[ARXIV_CANDIDATE])
        with patch("services.topic_discovery.web_search", new=AsyncMock(side_effect=httpx.TimeoutException("timeout"))):
            result = await self.discovery.discover_candidate_topics("AI Security")
        self.discovery._discover_from_arxiv.assert_awaited_once_with("AI Security")
        self.assertEqual(result, [ARXIV_CANDIDATE])

    async def test_empty_or_error_search_reaches_arxiv(self):
        for primary_result in ("", "Error: Gemini live search unavailable"):
            with self.subTest(primary_result=primary_result):
                self.discovery._discover_from_arxiv = AsyncMock(return_value=[ARXIV_CANDIDATE])
                with patch("services.topic_discovery.web_search", new=AsyncMock(return_value=primary_result)):
                    result = await self.discovery.discover_candidate_topics("AI Security")
                self.discovery._discover_from_arxiv.assert_awaited_once()
                self.assertEqual(result, [ARXIV_CANDIDATE])

    async def test_zero_extracted_candidates_reaches_arxiv(self):
        self.discovery._extract_candidates_from_search = AsyncMock(return_value=[])
        self.discovery._discover_from_arxiv = AsyncMock(return_value=[ARXIV_CANDIDATE])
        with patch("services.topic_discovery.web_search", new=AsyncMock(return_value=SEARCH_TEXT)):
            result = await self.discovery.discover_candidate_topics("AI Security")
        self.discovery._discover_from_arxiv.assert_awaited_once()
        self.assertEqual(result, [ARXIV_CANDIDATE])

    async def test_arxiv_request_or_malformed_xml_fails_closed(self):
        class BrokenClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url):
                raise httpx.RequestError("arXiv unavailable")

        with patch("httpx.AsyncClient", return_value=BrokenClient()):
            self.assertEqual(await TopicDiscoveryService()._discover_from_arxiv("AI Security"), [])

        class MalformedResponse:
            status_code = 200
            text = "<feed><entry>"

            def raise_for_status(self):
                return None

        class MalformedClient(BrokenClient):
            async def get(self, url):
                return MalformedResponse()

        with patch("httpx.AsyncClient", return_value=MalformedClient()):
            self.assertEqual(await TopicDiscoveryService()._discover_from_arxiv("AI Security"), [])

    def test_real_arxiv_xml_atom_parsing(self):
        candidates = TopicDiscoveryService._parse_arxiv_feed(REAL_ARXIV_ATOM_XML, "AI Security")
        self.assertEqual(len(candidates), 2)

        c1 = candidates[0]
        self.assertEqual(c1["title"], "Adversarial Prompt Injection Mitigations in Quantized LLMs")
        self.assertTrue(c1["summary"].startswith("We evaluate jailbreak vulnerabilities"))
        self.assertEqual(c1["source_urls"], ["https://arxiv.org/abs/2401.01234v1"])
        self.assertEqual(c1["source_quality"], "Primary research source (arXiv Atom)")

        c2 = candidates[1]
        self.assertEqual(c2["title"], "Side-Channel Attacks on Shared GPU Inference Clusters")
        self.assertEqual(c2["source_urls"], ["https://arxiv.org/abs/2401.05678v2"])

    def test_sanitize_url_credentials(self):
        secret_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=SECRET_GEMINI_KEY_12345"
        sanitized = sanitize_url_credentials(secret_url)
        self.assertNotIn("SECRET_GEMINI_KEY_12345", sanitized)
        self.assertIn("key=[REDACTED]", sanitized)

        secret_header = "x-goog-api-key: MY_SECRET_HEADER_KEY"
        sanitized_header = sanitize_url_credentials(secret_header)
        self.assertNotIn("MY_SECRET_HEADER_KEY", sanitized_header)
        self.assertIn("[REDACTED]", sanitized_header)

        bearer_str = "Authorization: Bearer my-secret-token-abc123xyz"
        sanitized_bearer = sanitize_url_credentials(bearer_str)
        self.assertNotIn("my-secret-token-abc123xyz", sanitized_bearer)
        self.assertIn("Bearer [REDACTED]", sanitized_bearer)


class TestAutonomousArxivFallback(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_429_outage_still_publishes_from_live_fallback_candidate(self):
        class EditorialLLM:
            async def generate_structured(self, **kwargs):
                user = kwargs["user"]
                if "Primary Sources:" not in user:
                    return {"topics": []}
                return {
                    "post_text": "Live arXiv security evidence warrants validation of affected agent deployments.",
                    "rationale": "Selected for direct evidence, current AI-security relevance, and stronger technical significance.",
                    "sources": ["https://arxiv.org/abs/2401.01234"],
                }

            async def generate(self, **kwargs):
                return '{"is_duplicate": false, "matched_post": null}'

        with tempfile.TemporaryDirectory() as tmp_dir:
            memory = AgentMemoryStore(os.path.join(tmp_dir, "fallback.db"))
            agent_id = "agent-arxiv-fallback"
            memory.register_agent(agent_id, "Ada", "AI Security")
            memory.create_window(agent_id, duration_minutes=10)
            service = AutonomousPublisherService(memory=memory, llm=EditorialLLM())
            service.discovery._discover_from_arxiv = AsyncMock(return_value=[ARXIV_CANDIDATE])

            # Simulate Gemini HTTP 429 rate limit
            rate_limit_error = httpx.HTTPStatusError(
                "HTTP 429 Too Many Requests: https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=SECRET_GEMINI_API_KEY",
                request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=SECRET_GEMINI_API_KEY"),
                response=httpx.Response(429)
            )

            with patch("services.topic_discovery.web_search", new=AsyncMock(side_effect=rate_limit_error)), \
                 self.assertLogs("services.topic_discovery", level="WARNING") as cm:
                discovered = await service.run_discovery_and_evaluation_cycle(agent_id)

            self.assertEqual(discovered["candidates_found"], 1)

            # Prove API key secret was redacted from logs
            log_output = "\n".join(cm.output)
            self.assertNotIn("SECRET_GEMINI_API_KEY", log_output)
            self.assertIn("[REDACTED]", log_output)

            # Prove autonomous publishing finishes cleanly
            window = memory.get_active_window(agent_id)
            with memory._get_connection() as conn:
                conn.execute(
                    "UPDATE publishing_windows SET ends_at = ? WHERE window_id = ?",
                    ((datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), window["window_id"]),
                )
                conn.commit()
            closed = await service.run_discovery_and_evaluation_cycle(agent_id)
            self.assertEqual(closed["action"], "published")
            self.assertEqual(memory.get_feed(agent_id)[0]["sources"], ["https://arxiv.org/abs/2401.01234"])
