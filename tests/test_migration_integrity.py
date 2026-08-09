"""Focused regression tests for the Gemini/Groq migration safety boundaries."""

import unittest

from services.editorial_engine import EditorialEngine
from services.llm import _validate_structured_response


class InjectingLLM:
    async def generate(self, system="", user=""):
        return '{"is_duplicate": false, "matched_post": null}'

    async def generate_structured(self, **kwargs):
        return {
            "post_text": "Verified source integrity must survive generation.",
            "rationale": "Selected for evidence, current relevance, and stronger technical significance.",
            "sources": ["https://fake.example/fabricated"],
        }


class TestMigrationIntegrity(unittest.IsolatedAsyncioTestCase):
    async def test_llm_injected_source_is_replaced_by_discovered_source(self):
        engine = EditorialEngine(llm_client=InjectingLLM())
        post = await engine.synthesize_post_for_leader(
            "agent-test",
            {"name": "Ada", "domain": "AI Security"},
            {
                "title": "Prompt injection evaluation",
                "summary": "A verified evaluation documents a security weakness.",
                "source_urls": ["https://arxiv.org/abs/2401.00001"],
                "score": 85.0,
            },
        )
        self.assertEqual(post["sources"], ["https://arxiv.org/abs/2401.00001"])

    async def test_missing_trusted_source_refuses_synthesis(self):
        engine = EditorialEngine(llm_client=InjectingLLM())
        with self.assertRaises(ValueError):
            await engine.synthesize_post_for_leader(
                "agent-test", {"name": "Ada", "domain": "AI Security"},
                {"title": "Unverified", "summary": "No live source.", "source_urls": []},
            )


class TestStructuredBoundary(unittest.TestCase):
    def test_rejects_unexpected_or_missing_fields(self):
        schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
            "additionalProperties": False,
        }
        with self.assertRaises(ValueError):
            _validate_structured_response({"title": "ok", "url": "https://fake.example"}, schema)
        with self.assertRaises(ValueError):
            _validate_structured_response({}, schema)
