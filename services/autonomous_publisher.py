"""
Autonomous Persona Publishing Service.

Orchestrates the discovery loop and quality-driven publishing window:
1. Runs discovery & evaluation:
   - Discovers news candidates from live web sources.
   - Calculates deterministic 0-100 scores across 6 criteria.
   - Persists all candidates, scores, and rejection decisions to SQLite.
   - Dynamically tracks and updates the window's top-scoring leader.
2. At the end of every publishing window (ends_at <= now):
   - Atomically claims the window via SQLite CAS lock (OPEN -> SELECTING).
   - Retrieves ALL eligible candidates sorted by score descending.
   - Sequentially attempts each candidate through the full pipeline:
     source URL validation -> AI article rewrite -> content validation -> final duplicate check (deterministic & fail-closed semantic).
   - Publishes ONLY the first candidate that passes every check.
   - If candidate fails at ANY stage, logs failure and tries next candidate.
   - If all fail: publishes NOTHING (status: NO_QUALIFIED_STORY).
   - Closes window and opens next window.
3. Concurrency & Double-Publish Protection:
   - Per-agent asyncio.Lock prevents overlapping discovery cycles.
   - Atomic CAS transition (OPEN -> SELECTING) guarantees AT MOST ONE published post per window.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from config.persona_engine import build_persona_profile
from config.settings import settings
from services.editorial_engine import EditorialEngine
from services.llm import LLMClient
from services.memory import AgentMemoryStore, memory_store
from services.topic_discovery import TopicDiscoveryService
from utils.duplicate import DuplicateStatus, check_deterministic_duplicate

logger = logging.getLogger(__name__)


class AutonomousPublisherService:
    """
    Quality-driven autonomous publishing orchestrator.
    Publishes exclusively to the SQLite feed_posts table.
    Guarantees: ONE WINDOW = AT MOST ONE PUBLISHED POST.
    """

    def __init__(
        self,
        memory: AgentMemoryStore | None = None,
        llm: LLMClient | None = None,
        publisher: Any | None = None,
    ):
        self.memory = memory or memory_store
        self.llm = llm or LLMClient()
        self.discovery = TopicDiscoveryService(self.llm)
        self.editorial = EditorialEngine(self.llm, self.memory)
        self.publisher = publisher
        self._agent_locks: dict[str, asyncio.Lock] = {}

    def _get_agent_lock(self, agent_id: str) -> asyncio.Lock:
        """Get or create an asyncio.Lock for a specific agent to prevent cycle overlap."""
        if agent_id not in self._agent_locks:
            self._agent_locks[agent_id] = asyncio.Lock()
        return self._agent_locks[agent_id]

    async def run_discovery_and_evaluation_cycle(self, agent_id: str) -> dict[str, Any]:
        """
        Execute one discovery and evaluation cycle for an agent.
        Guarantees that cycles for the same agent never overlap concurrently.
        """
        lock = self._get_agent_lock(agent_id)
        if lock.locked():
            logger.warning(f"[PUBLISHER] Discovery cycle already in progress for agent {agent_id}; skipping overlapping execution.")
            return {"success": False, "action": "skipped_overlap", "agent_id": agent_id}

        async with lock:
            return await self._run_discovery_internal(agent_id)

    async def _run_discovery_internal(self, agent_id: str) -> dict[str, Any]:
        """Internal worker for discovery and window evaluation."""
        agent = self.memory.get_agent(agent_id)
        if not agent:
            logger.error(f"[PUBLISHER] Agent {agent_id} not found in memory store.")
            return {"success": False, "error": f"Agent {agent_id} not found"}

        # Step 1: Ensure active window exists or recover it
        window = self.memory.get_or_create_active_window(agent_id, duration_minutes=settings.publish_window_minutes)
        window_id = window["window_id"]
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        logger.info(f"[WINDOW] Active window {window_id} ({window['started_at']} -> {window['ends_at']}) for agent '{agent['name']}'")

        # Step 2: Check if current window duration has elapsed
        if window["ends_at"] <= now_utc:
            logger.info(f"[WINDOW] Window {window_id} has reached ends_at time ({window['ends_at']}). Executing atomic window close evaluation...")
            return await self.process_window_close(agent_id, window_id)

        # Step 3: Run candidate discovery from live sources
        recent_hashes = self.memory.get_recent_topic_hashes(agent_id, limit=50)
        profile = build_persona_profile(agent["name"], agent["domain"])
        try:
            raw_candidates = await self.discovery.discover_candidate_topics(agent["domain"], recent_hashes)
        except Exception as exc:
            logger.warning("[DISCOVERY] Live source failure for %s: %s", agent_id, exc)
            return {
                "success": False,
                "action": "discovery_failed",
                "window_id": window_id,
            }
        logger.info(f"[DISCOVERY] Found {len(raw_candidates)} candidate topics for window {window_id}")

        # Step 4: Evaluate and persist each candidate
        evaluated_candidates = []
        current_leader = self.memory.get_current_leader(window_id, min_score=settings.min_news_score)
        leader_score = current_leader["score"] if current_leader else 0.0

        for raw_c in raw_candidates:
            candidate_id = f"c-{uuid.uuid4().hex[:8]}"
            eval_result = await self.editorial.evaluate_candidate(
                agent_id=agent_id,
                persona_profile=profile,
                candidate=raw_c,
                recent_hashes=recent_hashes
            )

            score = eval_result["score"]
            status = eval_result["status"]
            rejection_reason = eval_result["rejection_reason"]
            topic_hash = eval_result["topic_hash"]

            # Save candidate record to SQLite
            saved_c = self.memory.save_candidate(
                candidate_id=candidate_id,
                agent_id=agent_id,
                window_id=window_id,
                title=raw_c["title"],
                summary=raw_c["summary"],
                source_urls=raw_c.get("source_urls", []),
                source_quality=raw_c.get("source_quality", "Unknown"),
                score=score,
                score_breakdown=eval_result["breakdown"],
                status=status,
                rejection_reason=rejection_reason,
                topic_hash=topic_hash
            )
            evaluated_candidates.append(saved_c)

            # Step 5: Update Leader if this eligible candidate beats the previous leader
            if status == "ELIGIBLE" and score > leader_score:
                if current_leader:
                    self.memory.update_candidate_status(current_leader["candidate_id"], "ELIGIBLE")
                    logger.info(f"[LEADER] New candidate {candidate_id} (Score: {score:.1f}) replaced previous leader {current_leader['candidate_id']} (Score: {leader_score:.1f})")
                else:
                    logger.info(f"[LEADER] Candidate {candidate_id} (Score: {score:.1f}) is initial window leader")

                self.memory.update_candidate_status(candidate_id, "LEADER")
                current_leader = saved_c
                leader_score = score

        return {
            "success": True,
            "action": "discovery_cycle_completed",
            "window_id": window_id,
            "candidates_found": len(raw_candidates),
            "current_leader": current_leader
        }

    async def process_window_close(self, agent_id: str, window_id: str) -> dict[str, Any]:
        """
        Execute sequential candidate processing & window close evaluation:
        1. Verify target window exists and is in 'OPEN' status.
        2. Atomically claim window (OPEN -> SELECTING) in SQLite CAS lock.
        3. Retrieve ALL eligible candidates for window sorted by score DESC.
        4. Sequentially attempt each candidate from highest score to lowest:
           a. Validate source URLs.
           b. AI article rewrite & synthesis.
           c. Validate content structure & sources.
           d. Perform final pre-publication duplicate check (URL, title, fingerprint, semantic fail-closed).
        5. Publish ONLY the first candidate that passes every check.
        6. If candidate fails at ANY stage, log failure and try next candidate.
        7. If ALL candidates fail: set window status to NO_QUALIFIED_STORY, publish NOTHING.
        8. Open next publishing window.
        """
        agent = self.memory.get_agent(agent_id)
        if not agent:
            return {"success": False, "error": f"Agent {agent_id} not found"}

        # Check existing window state
        window = self.memory.get_window(window_id)
        if not window:
            logger.warning(f"[WINDOW] Window {window_id} not found.")
            return {"success": False, "action": "ignored", "reason": f"Window {window_id} not found"}

        if window["status"] != "OPEN":
            logger.info(f"[WINDOW] Window {window_id} is already in status '{window['status']}'. Ignoring re-close attempt.")
            return {
                "success": False,
                "action": "ignored",
                "reason": f"Window is in state '{window['status']}' (must be OPEN)",
                "window_id": window_id,
                "current_status": window["status"]
            }

        # Atomic SQLite CAS transition (OPEN -> SELECTING)
        claimed = self.memory.claim_window_for_closing(window_id)
        if not claimed:
            logger.warning(f"[WINDOW] Concurrent attempt to close window {window_id} blocked by atomic CAS lock.")
            return {
                "success": False,
                "action": "ignored",
                "reason": "Window already claimed by another execution thread",
                "window_id": window_id
            }

        logger.info(f"[WINDOW] === Processing Window Close for {window_id} (Agent: '{agent['name']}') ===")
        profile = build_persona_profile(agent["name"], agent["domain"])

        # Retrieve ALL eligible candidates sorted by score DESC
        eligible_candidates = self.memory.get_eligible_candidates(window_id, min_score=settings.min_news_score)

        if not eligible_candidates:
            logger.info(f"[SELECTION] No candidate met minimum score {settings.min_news_score:.1f}. Publishing NOTHING.")
            self.memory.close_window(window_id=window_id, status="NO_QUALIFIED_STORY")
            new_window = self.memory.create_window(agent_id, duration_minutes=settings.publish_window_minutes)
            return {
                "success": True,
                "action": "no_publication",
                "window_status": "NO_QUALIFIED_STORY",
                "closed_window_id": window_id,
                "next_window_id": new_window["window_id"]
            }

        # Sequential T-30 candidate attempt loop (highest score to lowest)
        for candidate in eligible_candidates:
            cand_id = candidate["candidate_id"]
            title = candidate["title"]
            score = candidate["score"]
            logger.info(f"[T-30] Attempting candidate '{title}' (id={cand_id}, score={score:.1f})")

            # Step 1: Validate source URLs
            trusted_sources = candidate.get("source_urls", [])
            if isinstance(trusted_sources, str):
                trusted_sources = [trusted_sources]
            clean_sources = [
                str(u).strip() for u in trusted_sources
                if isinstance(u, str) and str(u).strip().startswith("http")
            ]
            if not clean_sources:
                reason = "No valid http/https source URLs"
                logger.warning(f"[T-30] Rejecting candidate '{title}': {reason}")
                self.memory.update_candidate_status(cand_id, "REJECTED", rejection_reason=reason)
                continue

            # Step 2: AI Rewrite & Synthesis
            try:
                post_data = await self.editorial.synthesize_post_for_leader(agent_id, profile, candidate)
            except Exception as exc:
                reason = f"AI rewrite / synthesis failed: {exc}"
                logger.warning(f"[T-30] Rejecting candidate '{title}': {reason}")
                self.memory.update_candidate_status(cand_id, "REJECTED", rejection_reason=reason)
                continue

            # Step 3: Validate synthesized article content & sources
            post_text = post_data.get("text", "").strip()
            rationale = post_data.get("rationale", "").strip()
            sources = post_data.get("sources", [])

            if not post_text or not rationale or not sources:
                reason = "Synthesized post text, rationale, or sources empty"
                logger.warning(f"[T-30] Rejecting candidate '{title}': {reason}")
                self.memory.update_candidate_status(cand_id, "REJECTED", rejection_reason=reason)
                continue

            if not set(sources).issubset(set(clean_sources)):
                reason = "Synthesized post returned unverified source URLs"
                logger.warning(f"[T-30] Rejecting candidate '{title}': {reason}")
                self.memory.update_candidate_status(cand_id, "REJECTED", rejection_reason=reason)
                continue

            # Step 4: Final Pre-publication Duplicate Check (Deterministic & Semantic Fail-Closed)
            recent_published = self.memory.get_feed(agent_id, limit=50)
            is_det_dup, det_reason = check_deterministic_duplicate(
                candidate.get("url") or candidate.get("source_url") or clean_sources[0],
                title,
                post_text,
                recent_published
            )
            if is_det_dup:
                reason = f"Final duplicate check failed: {det_reason}"
                logger.warning(f"[T-30] Rejecting candidate '{title}': {reason}")
                self.memory.update_candidate_status(cand_id, "REJECTED", rejection_reason=reason)
                continue

            dup_status, matched_post = await self.editorial.check_semantic_duplicate(
                agent_id,
                title,
                candidate.get("summary", "")
            )
            if dup_status in (DuplicateStatus.DUPLICATE, DuplicateStatus.UNKNOWN):
                reason = f"Final semantic duplicate check returned {dup_status.value} (matched: {matched_post})"
                logger.warning(f"[T-30] Rejecting candidate '{title}': {reason}")
                self.memory.update_candidate_status(cand_id, "REJECTED", rejection_reason=reason)
                continue

            # Step 5: Candidate passed ALL checks! Save post to SQLite feed & publish ONE article for window.
            post_id = f"p-{uuid.uuid4().hex[:8]}"
            logger.info(f"[PUBLISH] Candidate '{title}' passed all checks. Saving post {post_id} to feed ({len(post_text)} chars)")

            saved_post = self.memory.save_post(
                agent_id=agent_id,
                text=post_text,
                rationale=rationale,
                sources=sources,
                topic_hash=post_data["topic_hash"],
                post_id=post_id
            )

            self.memory.update_candidate_status(cand_id, "PUBLISHED")
            self.memory.close_window(
                window_id=window_id,
                status="PUBLISHED",
                selected_candidate_id=cand_id,
                post_id=post_id
            )
            logger.info(f"[PUBLISH] Post {post_id} saved to feed successfully. Window {window_id} closed as PUBLISHED.")

            # Open next window
            new_window = self.memory.create_window(agent_id, duration_minutes=settings.publish_window_minutes)
            return {
                "success": True,
                "action": "published",
                "window_status": "PUBLISHED",
                "post": saved_post,
                "closed_window_id": window_id,
                "next_window_id": new_window["window_id"]
            }

        # If all candidates failed
        logger.info(f"[SELECTION] All {len(eligible_candidates)} eligible candidates failed validation/synthesis/duplicate checks. Publishing NOTHING.")
        self.memory.close_window(window_id=window_id, status="NO_QUALIFIED_STORY")
        new_window = self.memory.create_window(agent_id, duration_minutes=settings.publish_window_minutes)
        return {
            "success": True,
            "action": "no_publication",
            "window_status": "NO_QUALIFIED_STORY",
            "closed_window_id": window_id,
            "next_window_id": new_window["window_id"]
        }

    async def run_publishing_cycle(self, agent_id: str) -> dict[str, Any]:
        """Entrypoint for scheduled execution."""
        return await self.run_discovery_and_evaluation_cycle(agent_id)

    async def run_all_agents_cycle(self) -> None:
        """
        Cycle through all registered agents and run continuous discovery & window checks.
        Safe against slow jobs and sequential per agent.
        """
        agents = self.memory.list_agents()
        logger.info(f"[PUBLISHER] Running background discovery cycle for {len(agents)} active agent(s)")
        for agent in agents:
            try:
                await self.run_discovery_and_evaluation_cycle(agent["agentId"])
            except Exception as e:
                logger.error(f"[PUBLISHER] Error in cycle for agent {agent['agentId']}: {e}")


# Global publisher singleton instance
publisher_service = AutonomousPublisherService()
