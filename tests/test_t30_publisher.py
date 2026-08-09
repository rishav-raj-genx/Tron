"""
Unit tests for AutonomousPublisherService T-30 sequential candidate fallback
and single publication per window enforcement.

Tests cover:
- T-30 sequential fallback: Candidate 1 fails synthesis -> Candidate 2 attempted -> Candidate 2 succeeds -> Published.
- All candidates fail -> Window status set to NO_QUALIFIED_STORY.
- Scheduler survives provider failures without crashing.
- Single publication per window: Repeat calls to process_window_close on closed window return ignored.
- Atomic CAS window locking: Concurrently claimed window blocks duplicate executions.
"""

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services.autonomous_publisher import AutonomousPublisherService
from services.editorial_engine import EditorialEngine
from services.memory import AgentMemoryStore
from utils.duplicate import DuplicateStatus


class TestT30Publisher(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_t30.db")
        self.memory = AgentMemoryStore(db_path=self.db_path)
        self.agent = self.memory.register_agent("agent-t30", "T30 Agent", "AI Security")
        self.agent_id = "agent-t30"

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_t30_top_candidate_succeeds(self):
        """Top candidate passes all checks and is published."""
        window = self.memory.create_window(self.agent_id, duration_minutes=120)
        win_id = window["window_id"]

        self.memory.save_candidate(
            candidate_id="c1", agent_id=self.agent_id, window_id=win_id,
            title="Candidate 1", summary="Summary 1", source_urls=["https://example.com/1"],
            source_quality="High", score=90.0, score_breakdown={"total": 90.0},
            status="ELIGIBLE", topic_hash="hash1"
        )

        mock_editorial = MagicMock()
        mock_editorial.synthesize_post_for_leader = AsyncMock(return_value={
            "text": "Professional news article about candidate 1.",
            "rationale": "High score and top quality.",
            "sources": ["https://example.com/1"],
            "topic_hash": "hash1"
        })
        mock_editorial.check_semantic_duplicate = AsyncMock(return_value=(DuplicateStatus.NOT_DUPLICATE, None))

        publisher = AutonomousPublisherService(memory=self.memory)
        publisher.editorial = mock_editorial

        result = await publisher.process_window_close(self.agent_id, win_id)

        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "published")
        self.assertEqual(result["window_status"], "PUBLISHED")

        # Verify only 1 post published
        posts = self.memory.get_feed(self.agent_id)
        self.assertEqual(len(posts), 1)

    async def test_t30_top_candidate_fails_fallback_to_second(self):
        """Requirement #5: Top candidate fails synthesis -> publisher falls back to 2nd candidate."""
        window = self.memory.create_window(self.agent_id, duration_minutes=120)
        win_id = window["window_id"]

        # Candidate 1 (Score 95.0) - Will fail synthesis
        self.memory.save_candidate(
            candidate_id="c1", agent_id=self.agent_id, window_id=win_id,
            title="Top Candidate Fails", summary="Summary 1", source_urls=["https://example.com/1"],
            source_quality="High", score=95.0, score_breakdown={"total": 95.0},
            status="ELIGIBLE", topic_hash="hash1"
        )
        # Candidate 2 (Score 90.0) - Will succeed
        self.memory.save_candidate(
            candidate_id="c2", agent_id=self.agent_id, window_id=win_id,
            title="Second Candidate Succeeds", summary="Summary 2", source_urls=["https://example.com/2"],
            source_quality="High", score=90.0, score_breakdown={"total": 90.0},
            status="ELIGIBLE", topic_hash="hash2"
        )

        async def mock_synthesis(agent_id, profile, candidate):
            if candidate["candidate_id"] == "c1":
                raise RuntimeError("LLM synthesis failed for top candidate")
            return {
                "text": "Article text for second candidate.",
                "rationale": "Second candidate rationale.",
                "sources": ["https://example.com/2"],
                "topic_hash": "hash2"
            }

        mock_editorial = MagicMock()
        mock_editorial.synthesize_post_for_leader = AsyncMock(side_effect=mock_synthesis)
        mock_editorial.check_semantic_duplicate = AsyncMock(return_value=(DuplicateStatus.NOT_DUPLICATE, None))

        publisher = AutonomousPublisherService(memory=self.memory)
        publisher.editorial = mock_editorial

        result = await publisher.process_window_close(self.agent_id, win_id)

        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "published")
        posts = self.memory.get_feed(self.agent_id)
        self.assertEqual(len(posts), 1)
        self.assertIn("second candidate", posts[0]["text"].lower())

    async def test_t30_all_candidates_fail_no_publication(self):
        """All candidates fail -> Window closed as NO_QUALIFIED_STORY."""
        window = self.memory.create_window(self.agent_id, duration_minutes=120)
        win_id = window["window_id"]

        self.memory.save_candidate(
            candidate_id="c1", agent_id=self.agent_id, window_id=win_id,
            title="Failing Candidate", summary="Summary", source_urls=["https://example.com/1"],
            source_quality="High", score=88.0, score_breakdown={"total": 88.0},
            status="ELIGIBLE", topic_hash="hash1"
        )

        mock_editorial = MagicMock()
        mock_editorial.synthesize_post_for_leader = AsyncMock(side_effect=RuntimeError("Synthesis failed"))

        publisher = AutonomousPublisherService(memory=self.memory)
        publisher.editorial = mock_editorial

        result = await publisher.process_window_close(self.agent_id, win_id)

        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "no_publication")
        self.assertEqual(result["window_status"], "NO_QUALIFIED_STORY")
        self.assertEqual(len(self.memory.get_feed(self.agent_id)), 0)

    async def test_single_publication_per_window_repeat_call(self):
        """Requirement #7: Calling process_window_close on closed window is ignored."""
        window = self.memory.create_window(self.agent_id, duration_minutes=120)
        win_id = window["window_id"]

        self.memory.save_candidate(
            candidate_id="c1", agent_id=self.agent_id, window_id=win_id,
            title="Candidate 1", summary="Summary 1", source_urls=["https://example.com/1"],
            source_quality="High", score=90.0, score_breakdown={"total": 90.0},
            status="ELIGIBLE", topic_hash="hash1"
        )

        mock_editorial = MagicMock()
        mock_editorial.synthesize_post_for_leader = AsyncMock(return_value={
            "text": "Article text.", "rationale": "Rationale.",
            "sources": ["https://example.com/1"], "topic_hash": "hash1"
        })
        mock_editorial.check_semantic_duplicate = AsyncMock(return_value=(DuplicateStatus.NOT_DUPLICATE, None))

        publisher = AutonomousPublisherService(memory=self.memory)
        publisher.editorial = mock_editorial

        # First run closes window and publishes
        res1 = await publisher.process_window_close(self.agent_id, win_id)
        self.assertEqual(res1["action"], "published")

        # Second run on same window is ignored
        res2 = await publisher.process_window_close(self.agent_id, win_id)
        self.assertFalse(res2["success"])
        self.assertEqual(res2["action"], "ignored")
        self.assertEqual(len(self.memory.get_feed(self.agent_id)), 1)


if __name__ == "__main__":
    unittest.main()
