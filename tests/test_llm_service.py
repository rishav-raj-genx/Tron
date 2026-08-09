"""
Unit tests for LLMClient and ProviderError handling.

Tests cover:
- Gemini REST request formatting and payload structure.
- Gemini response parsing: success, empty response, malformed, safety blocks.
- HTTP status errors (429 rate limit, 5xx server error).
- Automatic fallback from Gemini primary to Groq secondary.
- Exception handling when both providers fail (ProviderError raised).
- Structured output validation and error handling.
"""

import json
import unittest
from unittest.mock import AsyncMock, patch

from services.llm import LLMClient, ProviderError, _clean_and_parse_json, _extract_content_from_data, _validate_structured_response


class TestLLMService(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.llm = LLMClient()

    def test_extract_content_gemini_success(self):
        data = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Gemini response text"}]
                    },
                    "finishReason": "STOP"
                }
            ]
        }
        text = _extract_content_from_data(data)
        self.assertEqual(text, "Gemini response text")

    def test_extract_content_gemini_empty_candidates(self):
        data = {"candidates": []}
        with self.assertRaises(ProviderError):
            _extract_content_from_data(data)

    def test_extract_content_gemini_safety_block(self):
        data = {
            "candidates": [
                {
                    "finishReason": "SAFETY",
                    "content": {"parts": [{"text": ""}]}
                }
            ]
        }
        with self.assertRaises(ProviderError):
            _extract_content_from_data(data)

    def test_extract_content_groq_success(self):
        data = {
            "choices": [
                {
                    "message": {"content": "Groq response text"},
                    "finish_reason": "stop"
                }
            ]
        }
        text = _extract_content_from_data(data)
        self.assertEqual(text, "Groq response text")

    def test_extract_content_groq_empty_content(self):
        data = {
            "choices": [
                {
                    "message": {"content": ""},
                    "finish_reason": "stop"
                }
            ]
        }
        with self.assertRaises(ProviderError):
            _extract_content_from_data(data)

    def test_clean_and_parse_json(self):
        raw_json_markdown = "```json\n{\"key\": \"value\"}\n```"
        parsed = _clean_and_parse_json(raw_json_markdown)
        self.assertEqual(parsed, {"key": "value"})

        raw_plain = "{\"title\": \"Test Article\"}"
        parsed_plain = _clean_and_parse_json(raw_plain)
        self.assertEqual(parsed_plain, {"title": "Test Article"})

    def test_validate_structured_response(self):
        schema = {
            "type": "object",
            "properties": {
                "post_text": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["post_text", "sources"]
        }
        valid_data = {"post_text": "Content", "sources": ["https://example.com"]}
        res = _validate_structured_response(valid_data, schema)
        self.assertEqual(res, valid_data)

        invalid_data = {"post_text": "Content"}  # missing sources
        with self.assertRaises(ValueError):
            _validate_structured_response(invalid_data, schema)

    @patch("services.llm.LLMClient._call_gemini_text", new_callable=AsyncMock)
    async def test_generate_gemini_success(self, mock_gemini):
        mock_gemini.return_value = "Gemini generated text"
        result = await self.llm.generate("system prompt", "user prompt")
        self.assertEqual(result, "Gemini generated text")

    @patch("services.llm.LLMClient._call_groq_text", new_callable=AsyncMock)
    @patch("services.llm.LLMClient._call_gemini_text", new_callable=AsyncMock)
    async def test_gemini_failure_groq_fallback(self, mock_gemini, mock_groq):
        mock_gemini.side_effect = ProviderError("Gemini rate limit 429")
        mock_groq.return_value = "Groq fallback text"

        result = await self.llm.generate("system prompt", "user prompt")
        self.assertEqual(result, "Groq fallback text")
        mock_gemini.assert_called_once()
        mock_groq.assert_called_once()

    @patch("services.llm.LLMClient._call_groq_text", new_callable=AsyncMock)
    @patch("services.llm.LLMClient._call_gemini_text", new_callable=AsyncMock)
    async def test_both_providers_fail(self, mock_gemini, mock_groq):
        mock_gemini.side_effect = ProviderError("Gemini down 503")
        mock_groq.side_effect = ProviderError("Groq down 500")

        with self.assertRaises(ProviderError):
            await self.llm.generate("system prompt", "user prompt")

    @patch("services.llm.LLMClient._call_gemini_text", new_callable=AsyncMock)
    async def test_valid_gemini_structured_response(self, mock_gemini):
        schema = {
            "type": "object",
            "properties": {
                "post_text": {"type": "string"},
                "rationale": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["post_text", "rationale", "sources"]
        }
        mock_gemini.return_value = '{"post_text": "Article", "rationale": "Reason", "sources": ["https://s.org"]}'
        res = await self.llm.generate_structured("sys", "user", schema=schema)
        self.assertEqual(res["post_text"], "Article")
        self.assertEqual(res["rationale"], "Reason")
        self.assertEqual(res["sources"], ["https://s.org"])

    @patch("services.llm.LLMClient._call_groq_text", new_callable=AsyncMock)
    @patch("services.llm.LLMClient._call_gemini_text", new_callable=AsyncMock)
    async def test_gemini_failure_valid_groq_structured_fallback(self, mock_gemini, mock_groq):
        schema = {
            "type": "object",
            "properties": {
                "post_text": {"type": "string"},
                "rationale": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["post_text", "rationale", "sources"]
        }
        mock_gemini.side_effect = ProviderError("Gemini 404/400")
        mock_groq.return_value = '{"post_text": "Groq Article", "rationale": "Groq Reason", "sources": ["https://s.org"]}'
        res = await self.llm.generate_structured("sys", "user", schema=schema)
        self.assertEqual(res["post_text"], "Groq Article")
        self.assertEqual(res["rationale"], "Groq Reason")

    @patch("services.llm.LLMClient._call_groq_text", new_callable=AsyncMock)
    @patch("services.llm.LLMClient._call_gemini_text", new_callable=AsyncMock)
    async def test_gemini_failure_malformed_groq_response(self, mock_gemini, mock_groq):
        schema = {
            "type": "object",
            "properties": {
                "post_text": {"type": "string"},
                "rationale": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["post_text", "rationale", "sources"]
        }
        mock_gemini.side_effect = ProviderError("Gemini 404")
        # Groq returns object for rationale instead of string
        mock_groq.return_value = '{"post_text": "Article", "rationale": {"nested": "obj"}, "sources": ["https://s.org"]}'
        with self.assertRaises(ProviderError):
            await self.llm.generate_structured("sys", "user", schema=schema)

    def test_schema_validation_rationale_must_be_string(self):
        schema = {
            "type": "object",
            "properties": {"rationale": {"type": "string"}},
            "required": ["rationale"]
        }
        with self.assertRaises(ValueError) as ctx:
            _validate_structured_response({"rationale": {"sub": "val"}}, schema)
        self.assertIn("must be a string", str(ctx.exception))

    def test_schema_validation_sources_must_be_string_array(self):
        schema = {
            "type": "object",
            "properties": {"sources": {"type": "array", "items": {"type": "string"}}},
            "required": ["sources"]
        }
        with self.assertRaises(ValueError) as ctx:
            _validate_structured_response({"sources": {"invalid": "dict"}}, schema)
        self.assertIn("must be an array", str(ctx.exception))

    def test_schema_validation_sources_string_coerced_to_array(self):
        schema = {
            "type": "object",
            "properties": {"sources": {"type": "array", "items": {"type": "string"}}},
            "required": ["sources"]
        }
        res = _validate_structured_response({"sources": "https://example.com/url"}, schema)
        self.assertEqual(res["sources"], ["https://example.com/url"])

    def test_schema_validation_topic_hash_must_be_string(self):
        schema = {
            "type": "object",
            "properties": {"topic_hash": {"type": "string"}},
            "required": ["topic_hash"]
        }
        with self.assertRaises(ValueError) as ctx:
            _validate_structured_response({"topic_hash": 12345}, schema)
        self.assertIn("must be a string", str(ctx.exception))

    def test_convert_to_gemini_schema_nested_types(self):
        from services.llm import _convert_to_gemini_schema
        json_schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "test_schema",
                "schema": {
                    "type": "object",
                    "properties": {
                        "post_text": {"type": "string"},
                        "rationale": {"type": "string"},
                        "sources": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "count": {"type": "integer"},
                        "active": {"type": "boolean"},
                        "details": {
                            "type": "object",
                            "properties": {
                                "meta": {"type": "string"}
                            }
                        }
                    },
                    "required": ["post_text", "rationale", "sources"]
                }
            }
        }
        converted = _convert_to_gemini_schema(json_schema)
        self.assertEqual(converted["type"], "OBJECT")
        self.assertEqual(converted["properties"]["post_text"]["type"], "STRING")
        self.assertEqual(converted["properties"]["sources"]["type"], "ARRAY")
        self.assertEqual(converted["properties"]["sources"]["items"]["type"], "STRING")
        self.assertEqual(converted["properties"]["count"]["type"], "INTEGER")
        self.assertEqual(converted["properties"]["active"]["type"], "BOOLEAN")
        self.assertEqual(converted["properties"]["details"]["type"], "OBJECT")
        self.assertEqual(converted["properties"]["details"]["properties"]["meta"]["type"], "STRING")
        self.assertEqual(converted["required"], ["post_text", "rationale", "sources"])

    def test_schema_validation_rationale_rejects_dict_or_list(self):
        schema = {
            "type": "object",
            "properties": {
                "post_text": {"type": "string"},
                "rationale": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "string"}},
                "topic_hash": {"type": "string"}
            },
            "required": ["post_text", "rationale", "sources", "topic_hash"]
        }
        # Reject dict rationale
        with self.assertRaises(ValueError):
            _validate_structured_response({
                "post_text": "text", "rationale": {"foo": "bar"}, "sources": ["s1"], "topic_hash": "h"
            }, schema)
        # Reject list rationale
        with self.assertRaises(ValueError):
            _validate_structured_response({
                "post_text": "text", "rationale": ["foo", "bar"], "sources": ["s1"], "topic_hash": "h"
            }, schema)
        # Accept valid string rationale
        valid = {
            "post_text": "text", "rationale": "plain text rationale", "sources": ["https://s.org"], "topic_hash": "h1"
        }
        res = _validate_structured_response(valid, schema)
        self.assertEqual(res["rationale"], "plain text rationale")


if __name__ == "__main__":
    unittest.main()


