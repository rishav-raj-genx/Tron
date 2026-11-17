"""
Tests for Phase 1 Step 1.2:
- SQLite WAL configuration and connection pragmas (journal_mode, busy_timeout, synchronous, foreign_keys)
- Composite indexes verification in sqlite_master
- Bounded keyset cursor pagination for feed posts (limit validation, non-overlapping pages, schema preservation)
- Deep /healthz probe returning 200 when healthy and 503 on database operational failures
"""

import sqlite3
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from services.memory import decode_cursor, encode_cursor, memory_store


class TestDatabaseOptimizationAndIndexes(unittest.TestCase):
    """Test connection pragmas, composite indexes, and database schema hardening."""

    def test_connection_pragmas(self) -> None:
        """Verify required SQLite pragmas on connection checkout."""
        with memory_store._get_connection() as conn:
            # 1. journal_mode == wal
            jm = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            self.assertEqual(str(jm).lower(), "wal")

            # 2. busy_timeout == 15000
            bt = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
            self.assertEqual(int(bt), 15000)

            # 3. synchronous == 1 (NORMAL)
            sync = conn.execute("PRAGMA synchronous;").fetchone()[0]
            self.assertEqual(int(sync), 1)

            # 4. foreign_keys == 1 (ON)
            fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            self.assertEqual(int(fk), 1)

    def test_composite_indexes_exist_in_sqlite_master(self) -> None:
        """Verify presence of hot read and deduplication composite indexes."""
        with memory_store._get_connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index';"
            ).fetchall()
            index_names = {r["name"] for r in rows}

        expected_indexes = {
            "idx_posts_agent_created_id",
            "idx_posts_created_id",
            "idx_posts_agent_time",
            "uq_posts_agent_topic_hash",
            "idx_decisions_agent",
            "idx_decisions_agent_title",
            "idx_windows_agent",
            "idx_candidates_window",
            "idx_candidates_agent_hash",
            "idx_candidates_agent_hash_status",
            "idx_x_pub_window",
        }

        for idx in expected_indexes:
            self.assertIn(idx, index_names, f"Expected composite index '{idx}' was not found in sqlite_master")

    def test_cursor_encoding_and_decoding(self) -> None:
        """Test opaque keyset cursor roundtrip serialization."""
        created_at = "2026-11-17T10:30:00Z"
        post_id = "post-abc-123"

        encoded = encode_cursor(created_at, post_id)
        self.assertIsInstance(encoded, str)
        self.assertGreater(len(encoded), 0)

        decoded = decode_cursor(encoded)
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded[0], created_at)
        self.assertEqual(decoded[1], post_id)

        # Invalid cursors return None
        self.assertIsNone(decode_cursor("invalid-base64-string!@#$"))
        self.assertIsNone(decode_cursor(""))


class TestFeedCursorPagination(unittest.TestCase):
    """Test backward-compatible bounded keyset cursor pagination on GET /api/agent/feed."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        self.agent_id = "agent-page-test-01"
        memory_store.register_agent(self.agent_id, "Paginator", "AI Systems")

        # Seed 7 posts with strictly descending timestamps
        self.seeded_post_ids: list[str] = []
        for i in range(7):
            hour = 12 - i
            t_str = f"2026-11-17T{hour:02d}:00:00Z"
            pid = f"post-p{i+1:02d}"
            memory_store.save_post(
                agent_id=self.agent_id,
                created_at=t_str,
                text=f"Article text #{i+1} covering frontier ML developments.",
                rationale=f"Editorial rationale for post #{i+1}.",
                sources=[f"https://arxiv.org/abs/2608.{1000+i}"],
                topic_hash=f"hash-{i+1:04d}",
                post_id=pid,
            )
            self.seeded_post_ids.append(pid)

    def test_limit_parameter_bounds_validation(self) -> None:
        """Query parameter limit must be bounded: 0 or 100 must be rejected with 422."""
        # limit=0 rejected
        r0 = self.client.get(f"/api/agent/feed?agentId={self.agent_id}&limit=0")
        self.assertEqual(r0.status_code, 422)

        # limit=100 rejected (max allowed is 50)
        r100 = self.client.get(f"/api/agent/feed?agentId={self.agent_id}&limit=100")
        self.assertEqual(r100.status_code, 422)

        # limit=-5 rejected
        r_neg = self.client.get(f"/api/agent/feed?agentId={self.agent_id}&limit=-5")
        self.assertEqual(r_neg.status_code, 422)

        # limit=20 (valid default/custom bound) succeeds
        r_valid = self.client.get(f"/api/agent/feed?agentId={self.agent_id}&limit=20")
        self.assertEqual(r_valid.status_code, 200)

    def test_keyset_cursor_pagination_non_overlapping_pages(self) -> None:
        """Verify cursor pagination returns non-overlapping pages in newest-first order with schema preservation."""
        # Page 1: limit=3
        res1 = self.client.get(f"/api/agent/feed?agentId={self.agent_id}&limit=3")
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertIn("posts", data1)
        self.assertIn("nextCursor", data1)
        self.assertEqual(len(data1["posts"]), 3)
        self.assertIsNotNone(data1["nextCursor"])

        page1_ids = [p["id"] for p in data1["posts"]]
        self.assertEqual(page1_ids, ["post-p01", "post-p02", "post-p03"])

        # Verify strict feed item contract on every returned post
        for p in data1["posts"]:
            self.assertIn("id", p)
            self.assertIn("createdAt", p)
            self.assertIn("text", p)
            self.assertIn("rationale", p)
            self.assertIn("sources", p)
            self.assertIsInstance(p["sources"], list)

        # Page 2: limit=3 with cursor from page 1
        cursor1 = data1["nextCursor"]
        res2 = self.client.get(f"/api/agent/feed?agentId={self.agent_id}&limit=3&cursor={cursor1}")
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(len(data2["posts"]), 3)
        self.assertIsNotNone(data2["nextCursor"])

        page2_ids = [p["id"] for p in data2["posts"]]
        self.assertEqual(page2_ids, ["post-p04", "post-p05", "post-p06"])

        # Assert zero overlap between Page 1 and Page 2
        self.assertEqual(set(page1_ids).intersection(set(page2_ids)), set())

        # Page 3: limit=3 with cursor from page 2 (remaining 1 item)
        cursor2 = data2["nextCursor"]
        res3 = self.client.get(f"/api/agent/feed?agentId={self.agent_id}&limit=3&cursor={cursor2}")
        self.assertEqual(res3.status_code, 200)
        data3 = res3.json()
        self.assertEqual(len(data3["posts"]), 1)
        self.assertEqual(data3["posts"][0]["id"], "post-p07")
        # Final page has no next cursor
        self.assertIsNone(data3["nextCursor"])


class TestDeepHealthzEndpoint(unittest.TestCase):
    """Test deep SQLite liveness verification and 503 error handling on /healthz."""

    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_healthz_healthy_returns_200_with_wal_and_scheduler(self) -> None:
        """Healthy database returns 200 with status=ok, db=wal, scheduler, and active_agents."""
        res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["db"], "wal")
        self.assertIn("scheduler", data)
        self.assertIn("active_agents", data)
        self.assertIsInstance(data["active_agents"], int)

    def test_healthz_database_failure_returns_503(self) -> None:
        """When database raises an OperationalError, /healthz returns 503 Service Unavailable."""
        with patch.object(memory_store, "check_liveness", side_effect=sqlite3.OperationalError("disk I/O error")):
            res = self.client.get("/healthz")
            self.assertEqual(res.status_code, 503)
            self.assertIn("Database health check failed", res.json()["detail"])


if __name__ == "__main__":
    unittest.main()
