"""
Pytest configuration and deterministic test fixtures for TRON (Autonoma).

Provides:
1. Isolated temporary SQLite database fixture with WAL mode enabled.
2. Deterministic mock/stub LLM client fixture.
3. Universal offline LLM guard ensuring tests run 100% offline without network egress or API quota spend.
4. Support for optional @pytest.mark.live marker for live external connectivity tests.
"""

import json
import re
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from config.settings import settings
from services.memory import AgentMemoryStore, memory_store
from utils.rate_limiter import rate_limiter_registry


class DeterministicStubLLM:
    """Deterministic stub LLM client for testing without external API calls."""

    async def generate(self, system: str = "", user: str = "") -> str:
        sys_lower = system.lower()
        if "semantic deduplication" in sys_lower or "is_duplicate" in sys_lower:
            return '{"is_duplicate": false, "matched_post": null}'
        return "Deterministic offline text completion."

    async def generate_structured(
        self,
        system: str = "",
        user: str = "",
        response_format: dict[str, Any] | None = None,
        schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        target = response_format or schema or {}
        inner_schema = target.get("json_schema", {}).get("schema", target)
        properties = inner_schema.get("properties", {})

        # Handle editorial article synthesis
        if "post_text" in properties:
            extracted_sources = []
            if "Primary Sources:" in user:
                try:
                    src_match = re.search(r'Primary Sources:\s*(\[[^\]]+\])', user)
                    if src_match:
                        extracted_sources = json.loads(src_match.group(1))
                except Exception:
                    pass
            if not extracted_sources:
                extracted_sources = ["https://arxiv.org/abs/2608.12345"]

            return {
                "post_text": "Ada: Verified technical vulnerability disclosure in frontier kernel weights with reproducible mitigations.",
                "rationale": "Selected for primary evidence, immediate security relevance, and superior impact over alternative candidates.",
                "sources": extracted_sources
            }

        # Handle candidate topic discovery extraction
        if "topics" in properties:
            return {
                "topics": [
                    {
                        "title": "Deterministic offline discovery",
                        "summary": "Verified research finding with reproducible benchmarks.",
                        "source_urls": ["https://arxiv.org/abs/2608.12345"],
                        "domain_relevance": "Direct technical domain relevance."
                    }
                ]
            }

        return {}


@pytest.fixture
def stub_llm() -> DeterministicStubLLM:
    """Fixture providing a deterministic stub LLM client."""
    return DeterministicStubLLM()


@pytest.fixture(autouse=True)
def reset_test_state() -> Generator[None, None, None]:
    """Autouse fixture ensuring clean global memory store and fresh rate limiters for every test."""
    rate_limiter_registry.reset_all()
    with memory_store._get_connection() as conn:
        with conn:
            conn.execute("DELETE FROM x_publication_records")
            conn.execute("DELETE FROM news_candidates")
            conn.execute("DELETE FROM publishing_windows")
            conn.execute("DELETE FROM feed_posts")
            conn.execute("DELETE FROM editorial_decisions")
            conn.execute("DELETE FROM agents")
    yield
    rate_limiter_registry.reset_all()


@pytest.fixture
def isolated_db(tmp_path: Path) -> Generator[AgentMemoryStore, None, None]:
    """Provide an isolated SQLite database store initialized in WAL mode."""
    db_file = tmp_path / "isolated_test_memory.db"
    store = AgentMemoryStore(str(db_file))
    orig_path = settings.agent_db_path
    settings.agent_db_path = str(db_file)
    try:
        yield store
    finally:
        settings.agent_db_path = orig_path


@pytest.fixture(autouse=True)
def offline_llm_guard(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    """
    Autouse fixture ensuring all tests run offline without network egress.
    Bypassed only when test is explicitly decorated with @pytest.mark.live.
    """
    if request.node.get_closest_marker("live"):
        yield
        return

    async def fake_gemini_raw(
        self: Any,
        system: str = "",
        contents: list[dict[str, Any]] | None = None,
        messages: list[dict[str, Any]] | None = None,
        target_schema: dict[str, Any] | None = None,
        is_json: bool = False
    ) -> str:
        contents = contents or []
        user_text = ""
        for item in contents:
            user_text += " ".join([p.get("text", "") for p in item.get("parts", [])])

        if is_json and target_schema:
            inner_schema = target_schema.get("json_schema", {}).get("schema", target_schema)
            properties = inner_schema.get("properties", {})

            if "post_text" in properties:
                extracted_sources = []
                if "Primary Sources:" in user_text:
                    try:
                        src_match = re.search(r'Primary Sources:\s*(\[[^\]]+\])', user_text)
                        if src_match:
                            extracted_sources = json.loads(src_match.group(1))
                    except Exception:
                        pass
                if not extracted_sources:
                    extracted_sources = ["https://arxiv.org/abs/2608.12345"]

                return json.dumps({
                    "post_text": "Ada: Verified technical vulnerability disclosure in frontier kernel weights with reproducible mitigations.",
                    "rationale": "Selected for primary evidence, immediate security relevance, and superior impact over alternative candidates.",
                    "sources": extracted_sources
                })

            if "topics" in properties:
                return json.dumps({
                    "topics": [
                        {
                            "title": "Deterministic offline discovery",
                            "summary": "Verified research finding with reproducible benchmarks.",
                            "source_urls": ["https://arxiv.org/abs/2608.12345"],
                            "domain_relevance": "Direct technical domain relevance."
                        }
                    ]
                })

        sys_lower = system.lower()
        if "semantic deduplication" in sys_lower or "is_duplicate" in sys_lower:
            return '{"is_duplicate": false, "matched_post": null}'

        return "Deterministic offline text generation."

    async def fake_groq_text(
        self: Any,
        messages: list[dict[str, Any]],
        target_schema: dict[str, Any] | None = None,
        is_json: bool = False
    ) -> str:
        user_text = ""
        for m in messages:
            if m.get("role") == "user":
                user_text += str(m.get("content", ""))

        if is_json and target_schema:
            inner_schema = target_schema.get("json_schema", {}).get("schema", target_schema)
            properties = inner_schema.get("properties", {})

            if "post_text" in properties:
                extracted_sources = []
                if "Primary Sources:" in user_text:
                    try:
                        src_match = re.search(r'Primary Sources:\s*(\[[^\]]+\])', user_text)
                        if src_match:
                            extracted_sources = json.loads(src_match.group(1))
                    except Exception:
                        pass
                if not extracted_sources:
                    extracted_sources = ["https://arxiv.org/abs/2608.12345"]

                return json.dumps({
                    "post_text": "Ada: Verified technical vulnerability disclosure in frontier kernel weights with reproducible mitigations.",
                    "rationale": "Selected for primary evidence, immediate security relevance, and superior impact over alternative candidates.",
                    "sources": extracted_sources
                })

        return "Deterministic offline groq fallback text."

    with patch("services.llm.LLMClient._call_gemini_raw", new=fake_gemini_raw), \
         patch("services.llm.LLMClient._call_groq_text", new=fake_groq_text):
        yield
