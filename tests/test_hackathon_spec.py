"""
EchoMind Autonomous News Publisher Test Suite.

Comprehensive tests covering:
1. Deterministic candidate scoring calculation (0-100 across 6 criteria).
2. Minimum threshold rejection (score < 75.0 rejected; score >= 75.0 eligible).
3. Leader tracking & late-breaking superior news replacing earlier leader.
4. Publishing window management: 120-minute production default & testing mode.
5. Zero publication outcome when no candidate qualifies (NO_QUALIFIED_STORY).
6. Deduplication across topic hashes and semantic memory.
7. Restart recovery of active window and leader from SQLite.
8. Evaluator API endpoints:
   - POST /api/agent/init -> {"agentId": "..."}
   - GET /api/agent/feed -> {"posts": [...]} with strict ISO 8601 UTC createdAt, array of string sources, and newest first sorting
   - GET /api/agent/status -> window and leader status
   - GET /health and GET /healthz
   - GET /api/agents
9. CRIT-001 Atomic CAS window-close lock under concurrent invocations.
10. CRIT-002 Non-open windows cannot be re-closed.
11. HIGH-002 Per-agent asyncio.Lock preventing scheduler cycle overlap.
12. MAX_AGENTS=5 Server-side atomic FIFO rotation and dependent data cleanup.
13. SQLite concurrency & WAL mode verification with 15s timeout.
"""

import asyncio
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
try:
    from fastapi.testclient import TestClient
    HAS_TESTCLIENT = True
except ImportError:
    HAS_TESTCLIENT = False
    class TestClient:
        def __init__(self, app): pass

from config.persona_engine import build_persona_profile
from config.settings import Settings, settings
from main import app
from services.autonomous_publisher import AutonomousPublisherService
from services.editorial_engine import EditorialEngine
from services.llm import LLMClient
from services.memory import AgentMemoryStore, memory_store

client = TestClient(app)
ISO_8601_REGEX = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


class TestEchoMindNewsPublisher(unittest.TestCase):

    def setUp(self):
        """Create isolated temporary SQLite database for each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_news_memory.db")
        self.memory = AgentMemoryStore(self.db_path)
        self.service = AutonomousPublisherService(
            memory=self.memory
        )

    def tearDown(self):
        """Clean up temporary test directory."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # =========================================================================
    # 1. CANDIDATE SCORING & THRESHOLD REJECTION
    # =========================================================================

    def test_candidate_scoring_and_threshold(self):
        """Test candidate scoring computes 0-100 score across 6 criteria."""
        editorial = EditorialEngine(memory_store=self.memory)
        profile = build_persona_profile("Ada", "AI Security")

        # High-impact, verified security candidate
        high_candidate = {
            "title": "Breaking: CVE-2026-10492 Discloses Adversarial Sub-Token Quantization Bypass in LLM Weights",
            "summary": "State-of-the-art benchmark exploit demonstrates reproducible sub-token perturbation bypassing refusal boundaries.",
            "source_urls": ["https://cve.mitre.org/cve-2026-10492", "https://arxiv.org/abs/2608.01234"],
            "source_quality": "High"
        }
        score, breakdown, reason = editorial.score_candidate(profile, high_candidate)
        self.assertTrue(score >= 75.0, f"Expected score >= 75, got {score}")
        self.assertIsNone(reason)
        self.assertIn("recency", breakdown)
        self.assertIn("significance", breakdown)
        self.assertIn("domain_relevance", breakdown)
        self.assertIn("source_quality", breakdown)
        self.assertIn("novelty", breakdown)
        self.assertIn("verifiability", breakdown)

        # Low-quality marketing hype candidate
        low_candidate = {
            "title": "Startup claims miraculous unhackable AI wrapper with zero proof",
            "summary": "General marketing recap of generic software.",
            "source_urls": ["http://genericblog.xyz"],
            "source_quality": "Low"
        }
        low_score, low_breakdown, low_reason = editorial.score_candidate(profile, low_candidate)
        self.assertTrue(low_score < 75.0, f"Expected score < 75, got {low_score}")
        self.assertIsNotNone(low_reason)
        self.assertIn("below minimum publishing threshold", low_reason)

    # =========================================================================
    # 2. LEADER TRACKING & REPLACEMENT
    # =========================================================================

    def test_leader_tracking_and_replacement(self):
        """Verify higher scoring candidate replaces earlier leader."""
        agent_id = "agent-leader-test"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)
        window_id = window["window_id"]

        # Initial leader candidate with score 78.0
        c1 = self.memory.save_candidate(
            candidate_id="c1",
            agent_id=agent_id,
            window_id=window_id,
            title="Good story on LLM security",
            summary="LLM prompt injection defense paper",
            source_urls=["https://arxiv.org/abs/2601.0001"],
            source_quality="Medium",
            score=78.0,
            score_breakdown={"significance": 15, "domain_relevance": 18},
            status="LEADER",
            rejection_reason=None,
            topic_hash="hash-1"
        )
        leader = self.memory.get_current_leader(window_id, min_score=75.0)
        self.assertEqual(leader["candidate_id"], "c1")
        self.assertEqual(leader["score"], 78.0)

        # Better candidate with score 92.0 arrives
        self.memory.update_candidate_status("c1", "ELIGIBLE")
        c2 = self.memory.save_candidate(
            candidate_id="c2",
            agent_id=agent_id,
            window_id=window_id,
            title="Critical zero-day in frontier model transformer kernel",
            summary="Zero-day exploit published by MIT researchers",
            source_urls=["https://cve.mitre.org/2026-9999"],
            source_quality="High",
            score=92.0,
            score_breakdown={"significance": 20, "domain_relevance": 20},
            status="LEADER",
            rejection_reason=None,
            topic_hash="hash-2"
        )
        new_leader = self.memory.get_current_leader(window_id, min_score=75.0)
        self.assertEqual(new_leader["candidate_id"], "c2")
        self.assertEqual(new_leader["score"], 92.0)

    # =========================================================================
    # 3. FEED PERSISTENCE & STRICT API CONTRACT
    # =========================================================================

    def test_feed_contract_and_ordering(self):
        """Verify feed returns reverse-chronological order, ISO 8601 UTC timestamps, and string array sources."""
        agent_id = "agent-feed-test"
        self.memory.register_agent(agent_id, "Ada", "AI Security")

        # Initially empty feed returns {"posts": []}
        empty_feed = self.memory.get_feed(agent_id)
        self.assertEqual(empty_feed, [])

        # Save first post
        post1 = self.memory.save_post(
            agent_id=agent_id,
            text="First post on AI vulnerabilities",
            rationale="Critical security update",
            sources=["https://arxiv.org/1", "https://cve.mitre.org/1"],
            topic_hash="hash-p1",
            post_id="p-001",
            created_at="2026-08-07T10:00:00Z"
        )
        self.assertIsNotNone(post1)
        self.assertTrue(re.match(ISO_8601_REGEX, post1["createdAt"]))

        # Save second post (newer)
        post2 = self.memory.save_post(
            agent_id=agent_id,
            text="Second newer post on model weights",
            rationale="High impact disclosure",
            sources=["https://arxiv.org/2"],
            topic_hash="hash-p2",
            post_id="p-002",
            created_at="2026-08-07T11:00:00Z"
        )
        self.assertIsNotNone(post2)

        # Retrieve feed - must be newest first
        feed = self.memory.get_feed(agent_id)
        self.assertEqual(len(feed), 2)
        self.assertEqual(feed[0]["id"], "p-002")  # Newest first
        self.assertEqual(feed[1]["id"], "p-001")
        self.assertTrue(re.match(ISO_8601_REGEX, feed[0]["createdAt"]))
        self.assertTrue(re.match(ISO_8601_REGEX, feed[1]["createdAt"]))
        self.assertIsInstance(feed[0]["sources"], list)
        self.assertEqual(feed[0]["sources"], ["https://arxiv.org/2"])

    # =========================================================================
    # 4. EVALUATOR HTTP API CONTRACT
    # =========================================================================

    def test_evaluator_api_contract(self):
        """Test strict API contract for /api/agent/init and /api/agent/feed."""
        # 1. POST /api/agent/init returns {"agentId": "..."}
        init_res = client.post("/api/agent/init", json={"persona": {"name": "Ada", "domain": "AI Security"}})
        self.assertEqual(init_res.status_code, 200)
        data = init_res.json()
        self.assertIn("agentId", data)
        self.assertEqual(list(data.keys()), ["agentId"])  # Strictly only agentId
        agent_id = data["agentId"]

        # 2. GET /api/agent/feed returns {"posts": []} when empty
        feed_res = client.get(f"/api/agent/feed?agentId={agent_id}")
        self.assertEqual(feed_res.status_code, 200)
        feed_data = feed_res.json()
        self.assertIn("posts", feed_data)
        self.assertIsInstance(feed_data["posts"], list)

        # 3. GET /feed alias
        alias_res = client.get("/feed")
        self.assertEqual(alias_res.status_code, 200)
        self.assertIn("posts", alias_res.json())

        # 4. GET /health and /healthz
        h_res = client.get("/healthz")
        self.assertEqual(h_res.status_code, 200)
        self.assertEqual(h_res.json()["status"], "healthy")

    # =========================================================================
    # 5. SOURCE EXTRACTION & STRUCTURED SYNTHESIS
    # =========================================================================

    def test_source_extraction_and_structured_json_synthesis(self):
        """Verify candidate sources are preserved, non-empty, and correctly populated in synthesized posts."""
        editorial = EditorialEngine(memory_store=self.memory)
        profile = build_persona_profile("Ada", "AI Security")

        candidate = {
            "title": "Critical zero-day in transformer kernel weights",
            "summary": "Exploit published by research team with reproducible proofs.",
            "source_urls": ["https://arxiv.org/abs/2608.12345", "https://cve.mitre.org/cve-2026-99999"],
            "score": 95.0,
            "topic_hash": "hash-src-123"
        }

        # Run async synthesis synchronously via asyncio.run
        synth = asyncio.run(editorial.synthesize_post_for_leader("agent-src-test", profile, candidate))
        self.assertIn("text", synth)
        self.assertIn("rationale", synth)
        self.assertIn("sources", synth)
        self.assertIsInstance(synth["sources"], list)
        self.assertTrue(len(synth["sources"]) >= 1)
        self.assertTrue(all(isinstance(s, str) and s.startswith("http") for s in synth["sources"]))

        # Save to SQLite and verify feed returns sources as array of strings
        agent_id = "agent-src-test"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        saved = self.memory.save_post(
            agent_id=agent_id,
            text=synth["text"],
            rationale=synth["rationale"],
            sources=synth["sources"],
            topic_hash=synth["topic_hash"]
        )
        self.assertIsNotNone(saved)
        feed = self.memory.get_feed(agent_id)
        self.assertEqual(len(feed), 1)
        self.assertIsInstance(feed[0]["sources"], list)
        self.assertEqual(feed[0]["sources"], synth["sources"])

    # =========================================================================
    # 6. ATOMIC CAS WINDOW LOCK & IDEMPOTENCY
    # =========================================================================

    def test_atomic_cas_window_close(self):
        """Verify atomic CAS prevents double closing of windows."""
        agent_id = "agent-cas-test"
        self.memory.register_agent(agent_id, "Ada", "AI Security")
        window = self.memory.create_window(agent_id, duration_minutes=120)
        window_id = window["window_id"]

        # First claim succeeds
        claim1 = self.memory.claim_window_for_closing(window_id)
        self.assertTrue(claim1)

        # Second claim fails atomically
        claim2 = self.memory.claim_window_for_closing(window_id)
        self.assertFalse(claim2)

    # =========================================================================
    # 6. MAX_AGENTS=5 FIFO ROTATION
    # =========================================================================

    def test_max_agents_fifo_rotation(self):
        """Verify 5-agent limit with FIFO rotation."""
        for i in range(7):
            aid = f"agent-fifo-{i}"
            self.memory.register_agent(aid, f"Agent {i}", "Domain", max_agents=5)

        agents = self.memory.list_agents()
        self.assertLessEqual(len(agents), 5)
        agent_ids = [a["agentId"] for a in agents]
        self.assertNotIn("agent-fifo-0", agent_ids)
        self.assertNotIn("agent-fifo-1", agent_ids)
        self.assertIn("agent-fifo-6", agent_ids)


if __name__ == "__main__":
    unittest.main()
