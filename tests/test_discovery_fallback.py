"""Regression coverage for reachable, live-only discovery fallback paths."""

import unittest
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx

from services.topic_discovery import TopicDiscoveryService
from services.autonomous_publisher import AutonomousPublisherService
from services.memory import AgentMemoryStore


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

    def test_arxiv_candidates_preserve_required_live_provenance(self):
        xml = """<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>http://arxiv.org/abs/2401.01234</id><title> AI Security Study </title><summary> Live reproducible evidence. </summary></entry></feed>"""
        candidates = TopicDiscoveryService._parse_arxiv_feed(xml, "AI Security")
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertTrue(all(candidate[field] for field in (
            "title", "summary", "source_urls", "domain_relevance", "source_quality", "topic_hash", "discovered_at",
        )))
        self.assertEqual(candidate["source_urls"], ["https://arxiv.org/abs/2401.01234"])


class TestAutonomousArxivFallback(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_outage_still_publishes_from_live_fallback_candidate(self):
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

            with patch("services.topic_discovery.web_search", new=AsyncMock(side_effect=httpx.ConnectError("Gemini outage"))):
                discovered = await service.run_discovery_and_evaluation_cycle(agent_id)
            self.assertEqual(discovered["candidates_found"], 1)

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
