"""
Editorial Judgment & Candidate Scoring Engine.

Implements deterministic multi-factor candidate scoring (0-100) and editorial evaluation:
1. Recency: 0-20
2. Significance / Impact: 0-25
3. Persona / Domain Relevance: 0-20
4. Source Quality: 0-15
5. Novelty: 0-10
6. Verifiability: 0-10
Total: 100 points (Threshold: MIN_NEWS_SCORE = 75.0)

Also handles:
- Two-layer deduplication:
    Layer 1: Fast crypto-hash check against recent topic hashes (exact match).
    Layer 2: LLM semantic check against the last 10 published posts
             (catches paraphrases, rewordings, and conceptually identical stories).
- Generation of high-value, authentic post text within 280 characters.
- Generation of 3-part publishing rationale (Why selected, Why relevant now, Why chosen over others).
- Source URL attribution.
"""

import json
import logging
import re
from typing import Any

from config.settings import settings
from services.llm import LLMClient
from services.memory import AgentMemoryStore

logger = logging.getLogger(__name__)

EDITORIAL_SYNTHESIS_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "editorial_post_synthesis",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "post_text": {
                    "type": "string",
                    "description": "The exact post text written in authentic persona voice. MUST be under 280 characters."
                },
                "rationale": {
                    "type": "string",
                    "description": "Transparent rationale explaining: (1) Why selected, (2) Why relevant now, and (3) Why chosen over alternative candidates."
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of source URLs referenced in this post"
                }
            },
            "required": ["post_text", "rationale", "sources"],
            "additionalProperties": False
        }
    }
}


class EditorialEngine:
    """
    Evaluates news candidates, calculates deterministic 0-100 scores,
    and synthesizes verified posts under 280 characters.
    """

    def __init__(self, llm_client: LLMClient | None = None, memory_store: AgentMemoryStore | None = None):
        self.llm = llm_client or LLMClient()
        self.memory = memory_store

    def score_candidate(
        self,
        persona_profile: dict[str, Any],
        candidate: dict[str, Any]
    ) -> tuple[float, dict[str, Any], str | None]:
        """
        Calculate deterministic multi-factor score (0-100) for a news candidate.

        Returns:
            (total_score, score_breakdown, rejection_reason_or_none)
        """
        title = candidate.get("title", "").strip()
        summary = candidate.get("summary", "").strip()
        combined_text = f"{title} {summary}".lower()
        source_urls = candidate.get("source_urls", [])
        if isinstance(source_urls, str):
            source_urls = [source_urls]

        # 1. Recency (0-20)
        recency_score = 18.0
        if any(term in combined_text for term in ["breaking", "just released", "today", "vulnerability disclosure", "cve-"]):
            recency_score = 20.0
        elif any(term in combined_text for term in ["this week", "announces", "release"]):
            recency_score = 16.0

        # 2. Significance / Impact (0-25)
        significance_score = 18.0
        high_impact_keywords = [
            "breakthrough", "benchmark", "quantization", "jailbreak", "adversarial",
            "vulnerability", "cve", "zero-day", "foundational", "state-of-the-art",
            "weights released", "open source", "sub-token", "latency", "exploit"
        ]
        impact_matches = sum(1 for kw in high_impact_keywords if kw in combined_text)
        if impact_matches >= 3:
            significance_score = 24.0
        elif impact_matches >= 1:
            significance_score = 20.0
        elif any(term in combined_text for term in ["rumor", "speculation", "leak", "hype"]):
            significance_score = 8.0

        # 3. Persona / Domain Relevance (0-20)
        domain = persona_profile.get("domain", "").lower()
        domain_score = 14.0
        domain_keywords = {
            "ai security": ["security", "adversarial", "jailbreak", "cve", "vulnerability", "quantization", "bypass", "exploit", "safety", "red-teaming", "prompt injection"],
            "machine learning": ["transformer", "architecture", "training", "inference", "loss", "weights", "dataset", "kv-cache", "attention", "vllm", "decoding"],
            "robotics": ["actuator", "humanoid", "control", "sensor", "teleoperation", "slam", "reinforcement learning", "ros"],
            "ai product": ["adoption", "latency", "cost", "tokens", "enterprise", "api", "infrastructure", "deployment", "pricing"]
        }
        relevant_terms = domain_keywords.get(domain, domain.split())
        domain_matches = sum(1 for term in relevant_terms if term in combined_text)
        if domain_matches >= 2:
            domain_score = 19.0
        elif domain_matches >= 1:
            domain_score = 16.0
        else:
            domain_score = 8.0

        # 4. Source Quality (0-15)
        source_quality_score = 8.0
        high_authority_domains = ["arxiv.org", "cve.mitre.org", "nist.gov", "github.com", "openai.com", "anthropic.com", "huggingface.co", "nature.com"]
        tier1_tech = ["techcrunch.com", "theverge.com", "reuters.com", "wired.com", "venturebeat.com", "arstechnica.com"]
        
        has_high_auth = any(any(auth in str(u).lower() for auth in high_authority_domains) for u in source_urls)
        has_tier1 = any(any(t in str(u).lower() for t in tier1_tech) for u in source_urls)

        if has_high_auth:
            source_quality_score = 14.5
        elif has_tier1:
            source_quality_score = 11.5
        elif len(source_urls) > 0 and source_urls[0].startswith("http"):
            source_quality_score = 9.0
        else:
            source_quality_score = 4.0

        # 5. Novelty (0-10)
        novelty_score = 8.0
        if any(term in combined_text for term in ["novel", "first-ever", "unannounced", "0-day", "new paradigm"]):
            novelty_score = 9.5
        elif any(term in combined_text for term in ["recap", "summary", "best practices", "tutorial"]):
            novelty_score = 4.0

        # 6. Verifiability (0-10)
        verifiability_score = 7.0
        if len(source_urls) >= 2 or has_high_auth:
            verifiability_score = 9.5
        elif len(source_urls) == 1:
            verifiability_score = 7.5
        else:
            verifiability_score = 3.0

        # Total Calculation
        total_score = recency_score + significance_score + domain_score + source_quality_score + novelty_score + verifiability_score
        total_score = min(100.0, max(0.0, total_score))

        breakdown = {
            "recency": recency_score,
            "significance": significance_score,
            "domain_relevance": domain_score,
            "source_quality": source_quality_score,
            "novelty": novelty_score,
            "verifiability": verifiability_score,
            "total": total_score
        }

        min_threshold = settings.min_news_score
        rejection_reason = None
        if total_score < min_threshold:
            rejection_reason = f"Candidate score {total_score:.1f} is below minimum publishing threshold {min_threshold:.1f} (Significance: {significance_score}, Source: {source_quality_score})"

        return total_score, breakdown, rejection_reason

    async def _check_semantic_duplicate(
        self,
        agent_id: str,
        candidate_title: str,
        candidate_summary: str
    ) -> tuple[bool, str | None]:
        """
        Ask the LLM whether a candidate is conceptually identical to any of the
        last 10 published posts.  Returns (is_duplicate, matched_post_title_or_none).
        """
        if not self.memory:
            return False, None

        recent_posts = self.memory.get_feed(agent_id, limit=10)
        if not recent_posts:
            return False, None

        # Build a numbered list of previously published headlines
        published_lines = "\n".join(
            f"{i+1}. {p['text'][:200]}" for i, p in enumerate(recent_posts)
        )

        system_prompt = (
            "You are a strict editorial deduplication filter. "
            "Your ONLY job is to decide if a proposed news candidate is "
            "conceptually identical or substantially overlapping with any "
            "previously published post. Minor wording differences, "
            "reorderings, or paraphrases of the same core story all count "
            "as duplicates.\n\n"
            "Respond with EXACTLY one JSON object:\n"
            '  {"is_duplicate": true, "matched_post": "<number or title>"}\n'
            "or\n"
            '  {"is_duplicate": false, "matched_post": null}'
        )

        user_prompt = (
            "=== PREVIOUSLY PUBLISHED POSTS ===\n"
            f"{published_lines}\n\n"
            "=== NEW CANDIDATE ===\n"
            f"Title: {candidate_title}\n"
            f"Summary: {candidate_summary}\n\n"
            "Is this new candidate conceptually identical to any of the "
            "previously published posts listed above? "
            "Reject it if it covers the same core story, finding, or announcement, "
            "even if the wording is different."
        )

        try:
            raw = await self.llm.generate(system=system_prompt, user=user_prompt)
            # Parse the JSON response (tolerant of markdown fences)
            cleaned = re.sub(r"```json?\s*|```", "", raw).strip()
            data = json.loads(cleaned)
            is_dup = bool(data.get("is_duplicate", False))
            matched = data.get("matched_post")
            return is_dup, str(matched) if matched else None
        except Exception as e:
            logger.warning(f"[EDITORIAL] Semantic dedup LLM check failed, allowing candidate through: {e}")
            return False, None

    async def evaluate_candidate(
        self,
        agent_id: str,
        persona_profile: dict[str, Any],
        candidate: dict[str, Any],
        recent_hashes: set[str]
    ) -> dict[str, Any]:
        """
        Evaluate a single candidate, assign status (ELIGIBLE or REJECTED),
        and log decision.

        Deduplication is two-layered:
          1. Fast crypto-hash check against recent topic hashes (cheap, exact match).
          2. LLM semantic check against the last 10 published posts (deep, conceptual match).
        """
        topic_hash = candidate.get("topic_hash") or self.memory.compute_topic_hash(candidate["title"])
        
        # --- Layer 1: Fast crypto-hash deduplication ---
        if topic_hash in recent_hashes or (self.memory and self.memory.is_candidate_hash_covered(agent_id, topic_hash)):
            reason = "Duplicate content: already covered in recent memory or previous published stories."
            if self.memory:
                self.memory.log_editorial_decision(agent_id, candidate["title"], "REJECTED", reason)
            return {
                "candidate": candidate,
                "score": 0.0,
                "breakdown": {"total": 0.0},
                "status": "REJECTED",
                "rejection_reason": reason,
                "topic_hash": topic_hash
            }

        # --- Layer 2: LLM semantic deduplication ---
        is_semantic_dup, matched_post = await self._check_semantic_duplicate(
            agent_id,
            candidate.get("title", ""),
            candidate.get("summary", "")
        )
        if is_semantic_dup:
            reason = (
                f"Semantic duplicate: conceptually identical to previously published post "
                f"(matched: {matched_post}). Rejected by LLM editorial filter."
            )
            logger.info(f"[EDITORIAL] Semantic dedup rejected '{candidate.get('title', '')}': {reason}")
            if self.memory:
                self.memory.log_editorial_decision(agent_id, candidate["title"], "REJECTED", reason)
            return {
                "candidate": candidate,
                "score": 0.0,
                "breakdown": {"total": 0.0},
                "status": "REJECTED",
                "rejection_reason": reason,
                "topic_hash": topic_hash
            }

        score, breakdown, rejection_reason = self.score_candidate(persona_profile, candidate)
        status = "ELIGIBLE" if score >= settings.min_news_score else "REJECTED"

        if self.memory:
            decision = "ACCEPTED" if status == "ELIGIBLE" else "REJECTED"
            reason = rejection_reason or f"Meets quality threshold with score {score:.1f}/100"
            self.memory.log_editorial_decision(agent_id, candidate["title"], decision, reason)

        return {
            "candidate": candidate,
            "score": score,
            "breakdown": breakdown,
            "status": status,
            "rejection_reason": rejection_reason,
            "topic_hash": topic_hash
        }

    async def synthesize_post_for_leader(
        self,
        agent_id: str,
        persona_profile: dict[str, Any],
        leader_candidate: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Synthesize authentic post text (<= 280 chars), transparent rationale,
        and verified source attribution for the selected window leader.
        Guarantees non-empty source URLs and strict JSON parsing.
        """
        title = leader_candidate["title"]
        summary = leader_candidate["summary"]
        source_urls = leader_candidate.get("source_urls", [])
        if isinstance(source_urls, str):
            source_urls = [source_urls]
        
        # Clean candidate source URLs
        clean_candidate_sources = [
            str(u).strip() for u in source_urls
            if isinstance(u, str) and str(u).strip().startswith("http")
        ]
        clean_candidate_sources = list(dict.fromkeys(clean_candidate_sources))
        if not clean_candidate_sources:
            raise ValueError("Cannot synthesize a post without verified candidate sources")

        system_prompt = (
            f"You are {persona_profile['name']}, an autonomous authority in {persona_profile['domain']}.\n"
            f"Editorial stance: {persona_profile.get('editorial_stance', 'Evidence-based, skeptical, technical')}.\n"
            f"Writing style: {persona_profile.get('writing_style', 'Concise, rigorous, technical, no hype')}.\n\n"
            f"CRITICAL PUBLISHING CONSTRAINTS:\n"
            f"1. post_text MUST be under 280 characters, technical, clear, and factual without clickbait or emojis.\n"
            f"2. sources MUST be a JSON array containing the exact source URLs provided in Primary Sources. Under NO circumstances return an empty sources array.\n"
            f"3. rationale MUST provide a 3-part justification: (a) Why selected, (b) Why relevant now, (c) Why chosen over alternative candidates."
        )

        user_prompt = (
            f"Synthesize the official publication for the selected winning story:\n"
            f"Title: {title}\n"
            f"Summary: {summary}\n"
            f"Primary Sources: {json.dumps(clean_candidate_sources)}\n"
            f"Candidate Score: {leader_candidate.get('score', 85.0)}\n\n"
            f"Return a strict JSON object with fields: 'post_text', 'rationale', 'sources'."
        )

        try:
            result = await self.llm.generate_structured(
                system=system_prompt,
                user=user_prompt,
                schema=EDITORIAL_SYNTHESIS_SCHEMA
            )
            post_text = result.get("post_text", "").strip()
            if not post_text:
                raise ValueError("Structured response has an empty post_text")
            # Enforce 280 character limit
            if len(post_text) > 280:
                post_text = post_text[:277] + "..."

            # Validate and clean extracted sources
            extracted_sources = result.get("sources", [])
            if isinstance(extracted_sources, str):
                extracted_sources = [extracted_sources]

            valid_sources = [
                str(s).strip() for s in extracted_sources
                if isinstance(s, str) and str(s).strip() in clean_candidate_sources
            ]

            # If LLM omitted or failed to return valid URLs, backfill from candidate's verified sources
            if not valid_sources:
                valid_sources = clean_candidate_sources

            # Deduplicate while preserving order
            final_sources = list(dict.fromkeys(valid_sources))

            rationale = result.get("rationale", "").strip()
            if not rationale:
                raise ValueError("Structured response has an empty rationale")
            return {
                "text": post_text,
                "rationale": rationale,
                "sources": final_sources,
                "topic_hash": leader_candidate.get("topic_hash") or (self.memory.compute_topic_hash(title) if self.memory else AgentMemoryStore.compute_topic_hash(title))
            }
        except Exception as e:
            logger.warning(f"[EDITORIAL] LLM post synthesis fallback: {e}")
            # Deterministic fallback only restates already verified candidate
            # data and preserves its discovered source URLs.
            raw_text = f"{title}: {summary}"
            if len(raw_text) > 277:
                raw_text = raw_text[:274] + "..."

            return {
                "text": raw_text,
                "rationale": f"Selected as top-scoring candidate ({leader_candidate.get('score', 85):.1f}/100) meeting all verification criteria for {persona_profile['domain']}.",
                "sources": clean_candidate_sources,
                "topic_hash": leader_candidate.get("topic_hash") or (self.memory.compute_topic_hash(title) if self.memory else AgentMemoryStore.compute_topic_hash(title))
            }

    # Backwards-compatible evaluate_and_publish method
    async def evaluate_and_publish(
        self,
        agent_id: str,
        persona_profile: dict[str, Any],
        candidates: list[dict[str, Any]],
        recent_hashes: set[str]
    ) -> dict[str, Any] | None:
        """Legacy evaluate and publish immediately helper."""
        if not candidates:
            return None

        evaluated = []
        for c in candidates:
            ev = await self.evaluate_candidate(agent_id, persona_profile, c, recent_hashes)
            if ev["status"] == "ELIGIBLE":
                evaluated.append(ev)

        if not evaluated:
            return None

        evaluated.sort(key=lambda x: x["score"], reverse=True)
        top = evaluated[0]["candidate"]
        top["score"] = evaluated[0]["score"]
        top["topic_hash"] = evaluated[0]["topic_hash"]
        return await self.synthesize_post_for_leader(agent_id, persona_profile, top)
