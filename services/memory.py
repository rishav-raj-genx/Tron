"""
Resilient Memory and Feed Store for Autonomous Personas.

Provides thread-safe, resilient persistence using SQLite with WAL mode, storing:
- Agents (agent_id, name, domain, created_at)
- Feed Posts (id, agent_id, created_at, text, rationale, sources, topic_hash)
- Editorial Decisions (agent_id, topic_title, decision, reason, evaluated_at)
- Publishing Windows (window_id, agent_id, started_at, ends_at, status, selected_candidate_id, published_at, post_id)
- News Candidates (candidate_id, agent_id, window_id, title, summary, source_urls, source_quality, discovered_at, score, score_breakdown, status, rejection_reason, topic_hash)
- X Publication Records for cross-restart idempotent posting deduplication
- Enforces MAX_AGENTS=5 server-side atomic FIFO rotation.
"""

import hashlib
import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)

# Default SQLite database path in workspace
DEFAULT_DB_PATH = os.getenv("AGENT_DB_PATH", str(Path(__file__).parent.parent / "agent_memory.db"))


class AgentMemoryStore:
    """
    Asynchronous and synchronous capable persistent memory store for autonomous agents.
    Uses SQLite with WAL mode for concurrency, durability, and zero external dependency.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._ensure_parent_dir()
        self._init_db()

    def _ensure_parent_dir(self) -> None:
        """Ensure parent directory exists for configured database path."""
        try:
            parent_dir = Path(self.db_path).parent
            if str(parent_dir) not in ("", "."):
                parent_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"[MEMORY] Could not create parent directory for {self.db_path}: {e}")

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        self._ensure_parent_dir()
        conn = sqlite3.connect(self.db_path, timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for high concurrency (concurrent readers + single writer)
        conn.execute("PRAGMA journal_mode=WAL;")
        # SQLite-level busy timeout (ms) — retry internally before raising "database is locked"
        conn.execute("PRAGMA busy_timeout=15000;")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            # Agents table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            # Feed posts table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feed_posts (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    text TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    topic_hash TEXT NOT NULL,
                    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
                )
            """)

            # Editorial decisions & rejections table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS editorial_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    topic_title TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
                )
            """)

            # Publishing Windows table (duration driven by configuration)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS publishing_windows (
                    window_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    status TEXT NOT NULL, -- OPEN, SELECTING, PUBLISHED, NO_QUALIFIED_STORY, EXPIRED, FAILED
                    selected_candidate_id TEXT,
                    published_at TEXT,
                    post_id TEXT,
                    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
                )
            """)

            # News Candidates table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS news_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    window_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source_urls_json TEXT NOT NULL,
                    source_quality TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    score REAL NOT NULL,
                    score_breakdown_json TEXT NOT NULL,
                    status TEXT NOT NULL, -- DISCOVERED, EVALUATED, REJECTED, ELIGIBLE, LEADER, SELECTED, PUBLISHED, EXPIRED
                    rejection_reason TEXT,
                    topic_hash TEXT NOT NULL,
                    FOREIGN KEY (agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY (window_id) REFERENCES publishing_windows(window_id)
                )
            """)

            # Persistent X publication records table for restart-safe idempotency
            conn.execute("""
                CREATE TABLE IF NOT EXISTS x_publication_records (
                    idempotency_key TEXT PRIMARY KEY,
                    post_id TEXT,
                    text TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    window_id TEXT NOT NULL
                )
            """)

            # Create performance and uniqueness indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_agent_time ON feed_posts(agent_id, created_at DESC);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_posts_agent_topic_hash ON feed_posts(agent_id, topic_hash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_agent ON editorial_decisions(agent_id, evaluated_at DESC);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_windows_agent ON publishing_windows(agent_id, started_at DESC);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_window ON news_candidates(window_id, score DESC);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_agent_hash ON news_candidates(agent_id, topic_hash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_x_pub_window ON x_publication_records(window_id);")
            conn.commit()
            logger.info(f"[MEMORY] Initialized SQLite store at {self.db_path}")

    @staticmethod
    def compute_topic_hash(topic_text: str) -> str:
        """Compute normalized SHA-256 hash for topic deduplication."""
        normalized = "".join(c.lower() for c in topic_text if c.isalnum() or c.isspace()).strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    # =========================================================================
    # AGENT MANAGEMENT & ATOMIC FIFO 5-AGENT ROTATION
    # =========================================================================

    def register_agent(self, agent_id: str, name: str, domain: str, max_agents: int = 5) -> dict[str, Any]:
        """
        Register a new autonomous agent with server-side atomic FIFO rotation.
        If existing agents count >= max_agents (default 5), atomically deletes the oldest agent
        and all owned dependent records (windows, candidates, feed posts, decisions, x records).
        """
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._get_connection() as conn:
            # Query existing agents ordered strictly by persisted creation timestamp ASC
            rows = conn.execute("SELECT agent_id, name, created_at FROM agents ORDER BY created_at ASC").fetchall()
            
            # Enforce 5-agent limit: rotate out oldest agent if at or above capacity
            if len(rows) >= max_agents:
                excess_count = (len(rows) - max_agents) + 1
                oldest_to_evict = rows[:excess_count]
                for old in oldest_to_evict:
                    old_id = old["agent_id"]
                    logger.info(f"[MEMORY] Enforcing MAX_AGENTS={max_agents}: Evicting oldest agent '{old['name']}' ({old_id}, created {old['created_at']})")
                    # Delete all dependent data owned by the evicted agent
                    conn.execute("DELETE FROM publishing_windows WHERE agent_id = ?", (old_id,))
                    conn.execute("DELETE FROM news_candidates WHERE agent_id = ?", (old_id,))
                    conn.execute("DELETE FROM feed_posts WHERE agent_id = ?", (old_id,))
                    conn.execute("DELETE FROM editorial_decisions WHERE agent_id = ?", (old_id,))
                    conn.execute("DELETE FROM x_publication_records WHERE agent_id = ?", (old_id,))
                    conn.execute("DELETE FROM agents WHERE agent_id = ?", (old_id,))

            # Insert new agent
            conn.execute(
                "INSERT INTO agents (agent_id, name, domain, created_at) VALUES (?, ?, ?, ?)",
                (agent_id, name, domain, now_utc)
            )
            conn.commit()

        logger.info(f"[MEMORY] Registered agent '{name}' ({domain}) with machine id={agent_id}")
        return {"agentId": agent_id, "name": name, "domain": domain, "createdAt": now_utc}

    def delete_agent(self, agent_id: str) -> bool:
        """
        Explicitly delete an agent and all owned records across all tables atomically.
        """
        with self._get_connection() as conn:
            conn.execute("DELETE FROM publishing_windows WHERE agent_id = ?", (agent_id,))
            conn.execute("DELETE FROM news_candidates WHERE agent_id = ?", (agent_id,))
            conn.execute("DELETE FROM feed_posts WHERE agent_id = ?", (agent_id,))
            conn.execute("DELETE FROM editorial_decisions WHERE agent_id = ?", (agent_id,))
            conn.execute("DELETE FROM x_publication_records WHERE agent_id = ?", (agent_id,))
            cur = conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
            conn.commit()
            deleted = cur.rowcount > 0
            if deleted:
                logger.info(f"[MEMORY] Deleted agent {agent_id} and all associated records.")
            return deleted

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Get agent profile by agent_id."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
            if row:
                return {
                    "agentId": row["agent_id"],
                    "name": row["name"],
                    "domain": row["domain"],
                    "createdAt": row["created_at"]
                }
            return None

    def list_agents(self) -> list[dict[str, Any]]:
        """List all registered agents ordered by created_at ASC."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY created_at ASC").fetchall()
            return [
                {
                    "agentId": r["agent_id"],
                    "name": r["name"],
                    "domain": r["domain"],
                    "createdAt": r["created_at"]
                }
                for r in rows
            ]

    def get_agents_detailed_status(self, active_agent_id: str | None = None) -> list[dict[str, Any]]:
        """
        Retrieve all agents with real-time window, candidate count, leader, and active indicator.
        Used to render the multi-agent dashboard cards.
        """
        agents = self.list_agents()
        result = []
        for a in agents:
            aid = a["agentId"]
            window = self.get_active_window(aid) or self.get_latest_window(aid)
            candidate_count = 0
            current_leader = None
            last_published = None
            window_status = "OPEN"
            window_id = None
            started_at = None
            ends_at = None

            if window:
                window_id = window["window_id"]
                window_status = window["status"]
                started_at = window["started_at"]
                ends_at = window["ends_at"]
                candidates = self.get_candidates_for_window(window_id)
                candidate_count = len(candidates)
                leader = self.get_current_leader(window_id, min_score=75.0)
                if leader:
                    current_leader = {
                        "candidateId": leader["candidate_id"],
                        "title": leader["title"],
                        "score": leader["score"],
                        "summary": leader.get("summary")
                    }

            feed = self.get_feed(aid, limit=1)
            if feed:
                last_published = feed[0]["createdAt"]

            result.append({
                "agentId": aid,
                "name": a["name"],
                "domain": a["domain"],
                "createdAt": a["createdAt"],
                "isActive": (aid == active_agent_id) if active_agent_id else False,
                "status": {
                    "windowId": window_id,
                    "windowStatus": window_status,
                    "startedAt": started_at,
                    "endsAt": ends_at,
                    "candidateCount": candidate_count,
                    "currentLeader": current_leader,
                    "lastPublishedAt": last_published
                }
            })
        return result

    # =========================================================================
    # FEED POSTS & PERSISTENCE
    # =========================================================================

    def save_post(
        self,
        agent_id: str,
        text: str,
        rationale: str,
        sources: list[str],
        topic_hash: str | None = None,
        created_at: str | None = None,
        post_id: str | None = None
    ) -> dict[str, Any] | None:
        """
        Save a published post to the feed.
        Enforces UTC ISO 8601 timestamps and unique post IDs.
        """
        if not post_id:
            post_id = f"p-{uuid.uuid4().hex[:8]}"

        if not created_at:
            created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if not topic_hash:
            topic_hash = self.compute_topic_hash(text)

        sources_json = json.dumps(sources if isinstance(sources, list) else [])

        with self._get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO feed_posts (id, agent_id, created_at, text, rationale, sources_json, topic_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (post_id, agent_id, created_at, text, rationale, sources_json, topic_hash)
                )
                conn.commit()
                logger.info(f"[MEMORY] Saved post {post_id} for agent {agent_id}")
                return {
                    "id": post_id,
                    "createdAt": created_at,
                    "text": text,
                    "rationale": rationale,
                    "sources": sources,
                    "is_duplicate": False
                }
            except sqlite3.IntegrityError as e:
                logger.warning(f"[MEMORY] Duplicate post insertion blocked for agent {agent_id}, topic_hash {topic_hash}: {e}")
                row = conn.execute(
                    "SELECT id, created_at, text, rationale, sources_json FROM feed_posts WHERE agent_id = ? AND topic_hash = ?",
                    (agent_id, topic_hash)
                ).fetchone()
                if row:
                    try:
                        existing_sources = json.loads(row["sources_json"])
                    except Exception:
                        existing_sources = []
                    return {
                        "id": row["id"],
                        "createdAt": row["created_at"],
                        "text": row["text"],
                        "rationale": row["rationale"],
                        "sources": existing_sources,
                        "is_duplicate": True
                    }
                return None

    def get_feed(self, agent_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """
        Get posts in reverse chronological order (newest first).
        Returns list of posts with id, createdAt (ISO 8601 UTC string), text, rationale, sources (list of strings).
        If agent_id is None, returns posts across all agents.
        If no posts exist, returns an empty list [].
        """
        with self._get_connection() as conn:
            if agent_id:
                rows = conn.execute(
                    """
                    SELECT id, created_at, text, rationale, sources_json 
                    FROM feed_posts 
                    WHERE agent_id = ? 
                    ORDER BY created_at DESC 
                    LIMIT ?
                    """,
                    (agent_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, created_at, text, rationale, sources_json 
                    FROM feed_posts 
                    ORDER BY created_at DESC 
                    LIMIT ?
                    """,
                    (limit,)
                ).fetchall()

            posts = []
            for r in rows:
                try:
                    raw_sources = json.loads(r["sources_json"])
                    if isinstance(raw_sources, list):
                        sources = [str(s) for s in raw_sources]
                    elif isinstance(raw_sources, str):
                        sources = [raw_sources]
                    else:
                        sources = []
                except Exception:
                    sources = []

                created_at_val = r["created_at"]
                # Ensure ISO 8601 UTC format ends with Z
                if created_at_val and isinstance(created_at_val, str) and not created_at_val.endswith("Z") and "+" not in created_at_val:
                    created_at_val = f"{created_at_val}Z"

                posts.append({
                    "id": str(r["id"]),
                    "createdAt": str(created_at_val),
                    "text": str(r["text"]),
                    "rationale": str(r["rationale"]),
                    "sources": sources
                })
            return posts

    def count_posts(self, agent_id: str | None = None) -> int:
        """Count total posts published."""
        with self._get_connection() as conn:
            if agent_id:
                row = conn.execute("SELECT COUNT(*) as c FROM feed_posts WHERE agent_id = ?", (agent_id,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) as c FROM feed_posts").fetchone()
            return row["c"] if row else 0

    def is_topic_covered(self, agent_id: str, topic_hash: str) -> bool:
        """Check if a topic hash has already been published for this agent."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM feed_posts WHERE agent_id = ? AND topic_hash = ? LIMIT 1",
                (agent_id, topic_hash)
            ).fetchone()
            return row is not None

    def get_recent_topic_hashes(self, agent_id: str, limit: int = 100) -> set[str]:
        """Get set of recently published topic hashes."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT topic_hash FROM feed_posts WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit)
            ).fetchall()
            return {r["topic_hash"] for r in rows if r["topic_hash"]}

    # =========================================================================
    # EDITORIAL DECISIONS
    # =========================================================================

    def log_editorial_decision(
        self,
        agent_id: str,
        topic_title: str,
        decision: str,
        reason: str
    ) -> None:
        """Log an explicit editorial decision (ACCEPTED or REJECTED) with reason."""
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO editorial_decisions (agent_id, topic_title, decision, reason, evaluated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (agent_id, topic_title, decision, reason, now_utc)
            )
            conn.commit()
        logger.info(f"[EDITORIAL] Logged decision for '{topic_title}': {decision} ({reason[:60]}...)")

    def get_recent_editorial_decisions(self, agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent editorial decisions and rejection history."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, topic_title, decision, reason, evaluated_at
                FROM editorial_decisions
                WHERE agent_id = ?
                ORDER BY evaluated_at DESC
                LIMIT ?
                """,
                (agent_id, limit)
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "topic": r["topic_title"],
                    "decision": r["decision"],
                    "reason": r["reason"],
                    "evaluatedAt": r["evaluated_at"]
                }
                for r in rows
            ]

    # =========================================================================
    # PUBLISHING WINDOWS (CONFIGURATION-DRIVEN DURATION & CAS LOCKS)
    # =========================================================================

    def create_window(self, agent_id: str, duration_minutes: int | None = None) -> dict[str, Any]:
        """
        Create a new publishing window.
        Duration is configuration-driven via settings.publish_window_minutes (default 120, overrideable).
        """
        if duration_minutes is None:
            from config.settings import settings
            duration_minutes = settings.publish_window_minutes

        window_id = f"win-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        started_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        ends_at = (now + timedelta(minutes=duration_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO publishing_windows (window_id, agent_id, started_at, ends_at, status, selected_candidate_id, published_at, post_id)
                VALUES (?, ?, ?, ?, 'OPEN', NULL, NULL, NULL)
                """,
                (window_id, agent_id, started_at, ends_at)
            )
            conn.commit()
        logger.info(f"[WINDOW] Created window {window_id} ({duration_minutes}m) for agent {agent_id}: {started_at} -> {ends_at}")
        return {
            "window_id": window_id,
            "agent_id": agent_id,
            "started_at": started_at,
            "ends_at": ends_at,
            "status": "OPEN",
            "selected_candidate_id": None,
            "published_at": None,
            "post_id": None
        }

    def get_active_window(self, agent_id: str) -> dict[str, Any] | None:
        """Get current OPEN window if one exists and has not yet expired."""
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM publishing_windows 
                WHERE agent_id = ? AND status = 'OPEN' AND ends_at > ?
                ORDER BY started_at DESC LIMIT 1
                """,
                (agent_id, now_utc)
            ).fetchone()
            if row:
                return dict(row)
            return None

    def get_latest_window(self, agent_id: str) -> dict[str, Any] | None:
        """Get the most recent window for an agent regardless of status."""
        with self._get_connection() as conn:
            row = conn.execute(
                # Windows created and closed within the same second have equal
                # ISO timestamps; rowid makes restart recovery deterministic.
                "SELECT * FROM publishing_windows WHERE agent_id = ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
                (agent_id,)
            ).fetchone()
            if row:
                return dict(row)
            return None

    def get_or_create_active_window(self, agent_id: str, duration_minutes: int | None = None) -> dict[str, Any]:
        """
        Get the active open window or automatically create a new one.
        Handles restart recovery seamlessly.
        """
        if duration_minutes is None:
            from config.settings import settings
            duration_minutes = settings.publish_window_minutes

        active = self.get_active_window(agent_id)
        if active:
            return active

        # If previous window exists but expired without formal closure, return it so it can be evaluated
        latest = self.get_latest_window(agent_id)
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if latest and latest["status"] == "OPEN" and latest["ends_at"] <= now_utc:
            return latest

        return self.create_window(agent_id, duration_minutes)

    def claim_window_for_closing(self, window_id: str) -> bool:
        """
        CRIT-001 ATOMIC CAS LOCK:
        Atomically transition window from 'OPEN' to 'SELECTING'.
        Guarantees that exactly ONE execution thread can ever process and close a window.
        Returns True if claimed, False if already claimed or closed.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE publishing_windows 
                SET status = 'SELECTING' 
                WHERE window_id = ? AND status = 'OPEN'
                """,
                (window_id,)
            )
            conn.commit()
            claimed = (cursor.rowcount == 1)
            if claimed:
                logger.info(f"[WINDOW] Atomically claimed window {window_id} for closing (OPEN -> SELECTING)")
            else:
                logger.warning(f"[WINDOW] Claim rejected for window {window_id} (status is not OPEN or already claimed)")
            return claimed

    def close_window(
        self,
        window_id: str,
        status: str,
        selected_candidate_id: str | None = None,
        post_id: str | None = None
    ) -> None:
        """Close a publishing window with final status."""
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE publishing_windows 
                SET status = ?, selected_candidate_id = ?, post_id = ?, published_at = ?
                WHERE window_id = ?
                """,
                (status, selected_candidate_id, post_id, now_utc if status == "PUBLISHED" else None, window_id)
            )
            conn.commit()
        logger.info(f"[WINDOW] Closed window {window_id} with status '{status}' (candidate: {selected_candidate_id})")

    def get_window(self, window_id: str) -> dict[str, Any] | None:
        """Get window by window_id."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM publishing_windows WHERE window_id = ?", (window_id,)).fetchone()
            return dict(row) if row else None

    # =========================================================================
    # NEWS CANDIDATES & SCORING PERSISTENCE
    # =========================================================================

    def save_candidate(
        self,
        candidate_id: str,
        agent_id: str,
        window_id: str,
        title: str,
        summary: str,
        source_urls: list[str],
        source_quality: str,
        score: float,
        score_breakdown: dict[str, Any],
        status: str,
        rejection_reason: str | None = None,
        topic_hash: str | None = None,
        discovered_at: str | None = None
    ) -> dict[str, Any]:
        """Persist an evaluated news candidate with multi-factor score and breakdown."""
        if not discovered_at:
            discovered_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not topic_hash:
            topic_hash = self.compute_topic_hash(title)

        # Clamping score strictly within 0-100
        score = min(100.0, max(0.0, float(score)))

        source_urls_json = json.dumps(source_urls if isinstance(source_urls, list) else [])
        score_breakdown_json = json.dumps(score_breakdown if isinstance(score_breakdown, dict) else {})

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO news_candidates (
                    candidate_id, agent_id, window_id, title, summary, source_urls_json, 
                    source_quality, discovered_at, score, score_breakdown_json, status, 
                    rejection_reason, topic_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id, agent_id, window_id, title, summary, source_urls_json,
                    source_quality, discovered_at, score, score_breakdown_json, status,
                    rejection_reason, topic_hash
                )
            )
            conn.commit()

        logger.info(f"[CANDIDATE] Saved candidate {candidate_id} '{title[:40]}...' (Score: {score:.1f}, Status: {status})")
        return {
            "candidate_id": candidate_id,
            "agent_id": agent_id,
            "window_id": window_id,
            "title": title,
            "summary": summary,
            "source_urls": source_urls,
            "source_quality": source_quality,
            "discovered_at": discovered_at,
            "score": score,
            "score_breakdown": score_breakdown,
            "status": status,
            "rejection_reason": rejection_reason,
            "topic_hash": topic_hash
        }

    def get_candidates_for_window(self, window_id: str) -> list[dict[str, Any]]:
        """Get all candidates evaluated during a specific window, sorted by score DESC."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM news_candidates WHERE window_id = ? ORDER BY score DESC, discovered_at DESC",
                (window_id,)
            ).fetchall()

            candidates = []
            for r in rows:
                try:
                    urls = json.loads(r["source_urls_json"])
                except Exception:
                    urls = []
                try:
                    breakdown = json.loads(r["score_breakdown_json"])
                except Exception:
                    breakdown = {}

                candidates.append({
                    "candidate_id": r["candidate_id"],
                    "agent_id": r["agent_id"],
                    "window_id": r["window_id"],
                    "title": r["title"],
                    "summary": r["summary"],
                    "source_urls": urls,
                    "source_quality": r["source_quality"],
                    "discovered_at": r["discovered_at"],
                    "score": r["score"],
                    "score_breakdown": breakdown,
                    "status": r["status"],
                    "rejection_reason": r["rejection_reason"],
                    "topic_hash": r["topic_hash"]
                })
            return candidates

    def get_current_leader(self, window_id: str, min_score: float = 75.0) -> dict[str, Any] | None:
        """
        Get the current best eligible candidate in a window that meets min_score.
        Deterministic tie-breaking uses highest score, then most recent discovered_at.
        """
        candidates = self.get_candidates_for_window(window_id)
        eligible = [c for c in candidates if c["score"] >= min_score and c["status"] in ("ELIGIBLE", "LEADER", "SELECTED")]
        if eligible:
            # Sort by score DESC, then discovered_at DESC
            eligible.sort(key=lambda x: (x["score"], x["discovered_at"]), reverse=True)
            return eligible[0]
        return None

    def update_candidate_status(
        self,
        candidate_id: str,
        status: str,
        score: float | None = None,
        rejection_reason: str | None = None
    ) -> None:
        """Update status or score of a specific candidate."""
        with self._get_connection() as conn:
            if score is not None:
                conn.execute(
                    "UPDATE news_candidates SET status = ?, score = ?, rejection_reason = ? WHERE candidate_id = ?",
                    (status, score, rejection_reason, candidate_id)
                )
            else:
                conn.execute(
                    "UPDATE news_candidates SET status = ?, rejection_reason = ? WHERE candidate_id = ?",
                    (status, rejection_reason, candidate_id)
                )
            conn.commit()

    def is_candidate_hash_covered(self, agent_id: str, topic_hash: str) -> bool:
        """Check if candidate hash already exists in memory feed or previous published candidates."""
        if self.is_topic_covered(agent_id, topic_hash):
            return True
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM news_candidates WHERE agent_id = ? AND topic_hash = ? AND status = 'PUBLISHED' LIMIT 1",
                (agent_id, topic_hash)
            ).fetchone()
            return row is not None

    # =========================================================================
    # PERSISTENT X IDEMPOTENCY RECORDS
    # =========================================================================

    def save_x_publication_record(
        self,
        idempotency_key: str,
        post_id: str,
        text: str,
        agent_id: str,
        window_id: str
    ) -> None:
        """Persist X publication record for restart-durable duplicate prevention."""
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO x_publication_records 
                (idempotency_key, post_id, text, published_at, agent_id, window_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (idempotency_key, post_id, text, now_utc, agent_id, window_id)
            )
            conn.commit()

    def get_x_publication_record(self, idempotency_key: str) -> dict[str, Any] | None:
        """Query persistent X publication record by idempotency key."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM x_publication_records WHERE idempotency_key = ?",
                (idempotency_key,)
            ).fetchone()
            return dict(row) if row else None


# Global persistent memory singleton
memory_store = AgentMemoryStore()
