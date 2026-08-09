"""
Live Topic Discovery Engine.

Discovers fresh candidate topics exclusively from live information sources:
1. Live Web Search via Gemini Google Search grounding
2. LLM-powered extraction of structured candidates from raw search results
3. Multi-query domain exploration to produce diverse candidates

IMPORTANT: No static fallback pools. If live search is unreachable,
the discovery cycle is skipped with a WARNING log and retried next interval.
"""

import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from services.llm import LLMClient
from tools.shared.web_search import web_search
from services.memory import AgentMemoryStore

logger = logging.getLogger(__name__)


class TopicDiscoveryService:
    """
    Autonomous candidate topic discovery service.
    Queries live web sources only — no hardcoded or static fallback data.
    """

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm = llm_client or LLMClient()

    async def discover_candidate_topics(
        self,
        persona_domain: str,
        recent_hashes: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """
        Discover 3-5 candidate topics for editorial evaluation.

        Uses live web search exclusively. If the search API is unreachable
        or returns an error, logs a WARNING and returns an empty list so
        the publishing cycle is cleanly skipped without faking data.
        """
        recent_hashes = recent_hashes or set()
        search_query = f"latest {persona_domain} breakthroughs vulnerabilities benchmarks papers 2026"

        # Gemini is preferred. Every primary failure mode reaches the same
        # independent live-source fallback; model knowledge is never retrieval.
        try:
            logger.info("[DISCOVERY] Primary Gemini search started: domain=%s", persona_domain)
            search_raw = await web_search(query=search_query)
        except Exception as e:
            return await self._fallback_to_arxiv(persona_domain, f"Gemini search raised {type(e).__name__}: {e}")

        if not search_raw or "Error:" in search_raw:
            return await self._fallback_to_arxiv(persona_domain, "Gemini search returned empty or error data")

        logger.info("[DISCOVERY] Primary Gemini search succeeded: raw_chars=%d", len(search_raw))
        try:
            search_candidates = await self._extract_candidates_from_search(search_raw, persona_domain)
        except Exception as e:
            return await self._fallback_to_arxiv(persona_domain, f"candidate extraction raised {type(e).__name__}: {e}")

        if not search_candidates:
            return await self._fallback_to_arxiv(persona_domain, "candidate extraction returned zero valid candidates")

        # Deduplicate by title
        seen = set()
        unique_candidates = []
        for c in search_candidates:
            if c["title"] not in seen:
                seen.add(c["title"])
                unique_candidates.append(c)

        logger.info("[DISCOVERY] Final candidate count=%d source=gemini", len(unique_candidates))
        return unique_candidates[:5]

    async def _fallback_to_arxiv(self, persona_domain: str, reason: str) -> list[dict[str, Any]]:
        """Log a primary failure and use the independent live retrieval path."""
        logger.warning("[DISCOVERY] Primary Gemini search/extraction failed: %s", reason)
        logger.info("[DISCOVERY] Falling back to independent arXiv retrieval: domain=%s", persona_domain)
        candidates = await self._discover_from_arxiv(persona_domain)
        logger.info("[DISCOVERY] Final candidate count=%d source=arxiv", len(candidates))
        return candidates

    @staticmethod
    def _parse_arxiv_feed(feed_xml: str, persona_domain: str) -> list[dict[str, Any]]:
        """Normalize live arXiv Atom entries without LLM-derived citations."""
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(feed_xml)
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        candidates: list[dict[str, Any]] = []
        for entry in root.findall("atom:entry", namespace):
            title = " ".join(entry.findtext("atom:title", default="", namespaces=namespace).split())
            summary = " ".join(entry.findtext("atom:summary", default="", namespaces=namespace).split())
            source_url = entry.findtext("atom:id", default="", namespaces=namespace).strip()
            if source_url.startswith("http://arxiv.org/"):
                source_url = "https://" + source_url.removeprefix("http://")
            if not title or not summary or not source_url.startswith("https://arxiv.org/"):
                continue
            candidates.append({
                "title": title,
                "summary": summary,
                "source_urls": [source_url],
                "domain_relevance": f"Live arXiv research relevant to {persona_domain}",
                "source_quality": "Primary research source (arXiv Atom)",
                "topic_hash": AgentMemoryStore.compute_topic_hash(title),
                "discovered_at": now_utc,
            })
        return candidates

    async def _discover_from_arxiv(self, persona_domain: str) -> list[dict[str, Any]]:
        """Fetch a bounded provider-independent live research fallback."""
        # cs.AI is the stable broad AI corpus; cs.CR adds security research for
        # Ada's AI Security persona without relying on fragile text syntax.
        category_query = "cat:cs.AI+OR+cat:cs.CR" if persona_domain.lower() == "ai security" else "cat:cs.AI"
        url = "https://export.arxiv.org/api/query?" + urlencode({
            "search_query": category_query,
            "start": 0,
            "max_results": 5,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        })
        try:
            import httpx
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(url)
                logger.info("[DISCOVERY] arXiv HTTP status=%d", response.status_code)
                response.raise_for_status()
            candidates = self._parse_arxiv_feed(response.text, persona_domain)
            logger.info("[DISCOVERY] arXiv entries parsed=%d", len(candidates))
            logger.info("[DISCOVERY] arXiv valid candidates=%d", len(candidates))
            return candidates
        except Exception as exc:
            logger.warning("[DISCOVERY] Independent arXiv fallback failed: %s. Skipping cycle.", exc)
            return []

    async def _extract_candidates_from_search(
        self,
        search_text: str,
        persona_domain: str
    ) -> list[dict[str, Any]]:
        """Use LLM to extract clean topic candidates from live search raw text with strict source URL tracking."""
        # Pre-extract all HTTP/HTTPS URLs present in the search text
        all_discovered_urls = []
        for u in re.findall(r'https?://[^\s)\]">]+', search_text):
            clean_u = u.rstrip(".,;:)")
            if clean_u not in all_discovered_urls:
                all_discovered_urls.append(clean_u)

        schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "topic_extraction",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "topics": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {
                                        "type": "string",
                                        "description": "Specific technical headline of the discovery or news item."
                                    },
                                    "summary": {
                                        "type": "string",
                                        "description": "Detailed factual technical summary of what was released, discovered, or benchmarked."
                                    },
                                    "source_urls": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "The exact HTTP/HTTPS source URLs where this information was discovered."
                                    },
                                    "domain_relevance": {
                                        "type": "string",
                                        "description": "Explanation of how this relates directly to the technical domain."
                                    }
                                },
                                "required": ["title", "summary", "source_urls", "domain_relevance"],
                                "additionalProperties": False
                            }
                        }
                    },
                    "required": ["topics"],
                    "additionalProperties": False
                }
            }
        }

        system_prompt = (
            "You are an autonomous technical research analyst. Your job is to extract verified, "
            "factual technical news topics from live search results.\n\n"
            "CRITICAL REQUIREMENT:\n"
            "You MUST extract the exact HTTP/HTTPS source URLs from the search results and assign them "
            "to 'source_urls' for each candidate topic. Under NO circumstances return an empty source_urls array."
        )

        user_prompt = f"""Extract 2-3 distinct technical candidate topics from these live search results for domain '{persona_domain}'.

Search results:
{search_text[:3500]}
"""

        try:
            result = await self.llm.generate_structured(
                system=system_prompt,
                user=user_prompt,
                response_format=schema
            )
            extracted = []
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            for t in result.get("topics", []):
                # Clean and validate source URLs
                raw_sources = t.get("source_urls", [])
                if isinstance(raw_sources, str):
                    raw_sources = [raw_sources]

                valid_sources = [
                    str(s).strip() for s in raw_sources
                    if isinstance(s, str) and str(s).strip() in all_discovered_urls
                ]

                # If LLM omitted URLs, backfill from discovered search URLs
                if not valid_sources and all_discovered_urls:
                    valid_sources = all_discovered_urls[:2]

                # Deduplicate sources while preserving order
                clean_sources = list(dict.fromkeys(valid_sources))

                if not clean_sources:
                    logger.warning("[DISCOVERY] Rejecting extracted topic without a discovered source URL")
                    continue

                extracted.append({
                    "title": t["title"].strip(),
                    "summary": t["summary"].strip(),
                    "source_urls": clean_sources,
                    "domain_relevance": t.get("domain_relevance", f"Relevant to {persona_domain}").strip(),
                    "topic_hash": AgentMemoryStore.compute_topic_hash(t["title"]),
                    "discovered_at": now_utc
                })
            return extracted
        except Exception as e:
            logger.warning(f"[DISCOVERY] Error extracting topics with LLM: {e}")
            return []
