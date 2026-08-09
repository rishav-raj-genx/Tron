"""Deterministic autonomy tests for the hackathon feed contract."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from services.autonomous_publisher import AutonomousPublisherService
from services.memory import AgentMemoryStore
from services.topic_discovery import TopicDiscoveryService


class DeterministicLLM:
    async def generate(self, system="", user=""):
        return '{"is_duplicate": false, "matched_post": null}'

    async def generate_structured(self, **kwargs):
        title = kwargs.get("user", "").split("Title: ", 1)[-1].split("\n", 1)[0]
        return {
            "post_text": f"Ada: {title[:120]} changes the AI-security threat model; validate the affected deployment path.",
            "rationale": "Selected for strong primary evidence, immediate AI-security relevance, and higher technical significance than competing candidates.",
            "sources": ["https://arxiv.org/abs/2401.00001"],
        }


class SequencedDiscovery:
    def __init__(self):
        self.index = 0

    async def discover_candidate_topics(self, domain, recent_hashes):
        self.index += 1
        # Each simulated scheduler cycle receives live-source-shaped data; the
        # test controls time, not the public API or feed response.
        return [{
            "title": f"Prompt injection defense evaluation {self.index}",
            "summary": "A reproducible evaluation identifies an agent authorization weakness and documents mitigations.",
            "source_urls": [f"https://arxiv.org/abs/2401.{self.index:05d}"],
            "source_quality": "Primary research source (arXiv)",
        }]


class FailingOptionalPublisher:
    async def publish_post(self, text, metadata=None):
        raise RuntimeError("optional X adapter unavailable")


class TestAutonomousFeedIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "autonomy.db")
        self.memory = AgentMemoryStore(self.db_path)
        self.agent_id = "agent-ada-48h"
        self.memory.register_agent(self.agent_id, "Ada", "AI Security")
        self.memory.create_window(self.agent_id, duration_minutes=10)
        self.service = AutonomousPublisherService(
            memory=self.memory, llm=DeterministicLLM(), publisher=FailingOptionalPublisher()
        )
        self.service.discovery = SequencedDiscovery()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def _expire_current_window(self):
        window = self.memory.get_active_window(self.agent_id)
        with self.memory._get_connection() as conn:
            conn.execute(
                "UPDATE publishing_windows SET ends_at = ? WHERE window_id = ?",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), window["window_id"]),
            )
            conn.commit()

    async def test_accelerated_48_hour_autonomous_simulation_and_restart(self):
        """24 simulated two-hour windows prove repeated autonomous operation."""
        for cycle in range(24):
            discovered = await self.service.run_discovery_and_evaluation_cycle(self.agent_id)
            self.assertEqual(discovered["action"], "discovery_cycle_completed")
            self._expire_current_window()
            closed = await self.service.run_discovery_and_evaluation_cycle(self.agent_id)
            self.assertEqual(closed["action"], "published")
            if cycle == 11:
                # Simulate process restart using only persisted SQLite state.
                self.memory = AgentMemoryStore(self.db_path)
                self.service = AutonomousPublisherService(
                    memory=self.memory, llm=DeterministicLLM(), publisher=FailingOptionalPublisher()
                )
                restarted_discovery = SequencedDiscovery()
                restarted_discovery.index = cycle + 1
                self.service.discovery = restarted_discovery

        feed = self.memory.get_feed(self.agent_id)
        self.assertEqual(len(feed), 24)
        self.assertEqual(len({post["id"] for post in feed}), 24)
        self.assertTrue(all(post["id"].startswith("p-") for post in feed))
        self.assertTrue(all(post["createdAt"].endswith("Z") for post in feed))
        self.assertTrue(all(post["rationale"] and post["sources"] for post in feed))
        self.assertEqual(len({post["text"] for post in feed}), 24)
        self.assertEqual(self.memory.count_posts(self.agent_id), 24)

    async def test_source_failure_isolated(self):
        class BrokenSource:
            async def discover_candidate_topics(self, domain, recent_hashes):
                raise RuntimeError("source timeout")

        self.service.discovery = BrokenSource()
        result = await self.service.run_discovery_and_evaluation_cycle(self.agent_id)
        self.assertEqual(result["action"], "discovery_failed")


class TestLiveSourceParsing(unittest.TestCase):
    def test_arxiv_atom_entries_preserve_live_primary_source_urls(self):
        xml = """<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>http://arxiv.org/abs/2401.01234</id><title>Agent security evaluation</title><summary>Reproducible analysis.</summary></entry></feed>"""
        candidates = TopicDiscoveryService._parse_arxiv_feed(xml, "AI Security")
        self.assertEqual(candidates[0]["source_urls"], ["https://arxiv.org/abs/2401.01234"])
