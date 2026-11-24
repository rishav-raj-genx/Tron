"""
Tests for structured JSON observability, correlation IDs, secret redaction, and cycle telemetry.
"""

import json
import logging
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from main import app
from services.autonomous_publisher import AutonomousPublisherService
from services.memory import memory_store
from utils.logging import (
    StructuredJsonFormatter,
    get_cycle_id,
    get_request_id,
    redact_secrets,
    set_cycle_id,
    set_request_id,
)


class TestSecretRedaction:
    """Verifies that API keys, tokens, and query credentials are automatically redacted."""

    def test_redacts_gemini_api_key(self) -> None:
        raw = "Connecting with key AIzaSyD1234567890abcdefghijklmnopqrstuv in request"
        redacted = redact_secrets(raw)
        assert "AIzaSyD1234567890abcdefghijklmnopqrstuv" not in redacted
        assert "AIza[REDACTED_GEMINI_KEY]" in redacted

    def test_redacts_groq_api_key(self) -> None:
        raw = "Groq authorization failed for key gsk_1234567890abcdefghijklmnopqrstuvwxyz"
        redacted = redact_secrets(raw)
        assert "gsk_1234567890abcdefghijklmnopqrstuvwxyz" not in redacted
        assert "gsk_[REDACTED_GROQ_KEY]" in redacted

    def test_redacts_bearer_token(self) -> None:
        raw = "Headers: Authorization: Bearer secret_jwt_token_payload_1234567890"
        redacted = redact_secrets(raw)
        assert "secret_jwt_token_payload_1234567890" not in redacted
        assert "Bearer [REDACTED_TOKEN]" in redacted

    def test_redacts_url_query_key_parameter(self) -> None:
        raw = "URL: https://generativelanguage.googleapis.com/v1beta/models?key=AIzaSyD1234567890abcdefghijklmnopqrstuv"
        redacted = redact_secrets(raw)
        assert "key=[REDACTED]" in redacted
        assert "AIzaSyD1234567890abcdefghijklmnopqrstuv" not in redacted


class TestStructuredJsonFormatter:
    """Verifies StructuredJsonFormatter serializes records to valid single-line JSON with context fields."""

    def test_formats_valid_json_with_timestamp_and_level(self) -> None:
        formatter = StructuredJsonFormatter()
        record = logging.LogRecord(
            name="tron.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=42,
            msg="System test log entry",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["logger"] == "tron.test"
        assert data["message"] == "System test log entry"
        assert "timestamp" in data
        assert data["timestamp"].endswith("Z")

    def test_propagates_request_and_cycle_correlation_ids(self) -> None:
        formatter = StructuredJsonFormatter()

        req_token = set_request_id("req-test-uuid-1234")
        cyc_token = set_cycle_id("cyc-test-uuid-5678")
        try:
            record = logging.LogRecord(
                name="tron.test",
                level=logging.WARNING,
                pathname=__file__,
                lineno=55,
                msg="Warning inside active cycle",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            data = json.loads(output)

            assert data["request_id"] == "req-test-uuid-1234"
            assert data["cycle_id"] == "cyc-test-uuid-5678"
            assert get_request_id() == "req-test-uuid-1234"
            assert get_cycle_id() == "cyc-test-uuid-5678"
        finally:
            set_request_id(None)
            set_cycle_id(None)

    def test_attaches_custom_extra_fields_and_redacts(self) -> None:
        formatter = StructuredJsonFormatter()
        record = logging.LogRecord(
            name="tron.http",
            level=logging.INFO,
            pathname=__file__,
            lineno=80,
            msg="HTTP call completed",
            args=(),
            exc_info=None,
        )
        record.duration_ms = 45.2
        record.http_status = 200
        record.api_token = "Bearer secret_access_token_1234567890"

        output = formatter.format(record)
        data = json.loads(output)

        assert data["duration_ms"] == 45.2
        assert data["http_status"] == 200
        assert data["api_token"] == "Bearer [REDACTED_TOKEN]"


class TestRequestCorrelationMiddleware:
    """Verifies X-Request-ID handling across HTTP requests."""

    def test_generates_uuid_when_no_header_provided(self) -> None:
        client = TestClient(app)
        response = client.get("/healthz")
        assert response.status_code == 200
        req_id = response.headers.get("x-request-id")
        assert req_id is not None
        # Must be valid UUID4 format
        uuid_obj = uuid.UUID(req_id)
        assert uuid_obj.version == 4

    def test_preserves_and_echoes_valid_x_request_id(self) -> None:
        client = TestClient(app)
        custom_id = "req-custom-client-id-987654321"
        response = client.get("/healthz", headers={"X-Request-ID": custom_id})
        assert response.status_code == 200
        assert response.headers.get("x-request-id") == custom_id

    def test_replaces_invalid_x_request_id_with_safe_uuid(self) -> None:
        client = TestClient(app)
        unsafe_id = "<script>alert(1)</script>"
        response = client.get("/healthz", headers={"X-Request-ID": unsafe_id})
        assert response.status_code == 200
        returned_id = response.headers.get("x-request-id")
        assert returned_id != unsafe_id
        # Safely replaced with valid UUID
        assert uuid.UUID(returned_id)


class TestCycleCorrelationLogging:
    """Verifies autonomous publisher cycle instrumentation and structured telemetry."""

    @pytest.mark.asyncio
    async def test_cycle_telemetry_emitted_during_discovery(self, caplog: Any) -> None:
        # Initialize an agent in the memory store
        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        memory_store.register_agent(agent_id, "Ada", "AI Security")

        # Mock discovery returning 2 sample candidate topics
        mock_discovery = AsyncMock()
        mock_discovery.discover_candidate_topics.return_value = [
            {
                "title": "Quantum Weight Vulnerability Analysis",
                "summary": "Verified vulnerability in model weights.",
                "source_urls": ["https://arxiv.org/abs/2608.12345"],
                "domain_relevance": "AI Security vulnerability.",
                "source_quality": "Primary research",
            },
            {
                "title": "Zero-Day Adversarial Jailbreak Benchmark",
                "summary": "State-of-the-art robustness benchmark.",
                "source_urls": ["https://arxiv.org/abs/2608.67890"],
                "domain_relevance": "Robustness evaluation.",
                "source_quality": "Primary research",
            },
        ]

        publisher = AutonomousPublisherService(memory=memory_store)
        publisher.discovery = mock_discovery

        with caplog.at_level(logging.INFO):
            result = await publisher.run_discovery_and_evaluation_cycle(agent_id)

        assert result["success"] is True
        assert result["candidates_found"] == 2

        # Verify structured cycle log was emitted
        cycle_log = None
        for record in caplog.records:
            if hasattr(record, "cycle_id") and hasattr(record, "candidates_discovered"):
                cycle_log = record
                break

        assert cycle_log is not None, "Expected cycle completion log record with structured attributes"
        assert cycle_log.candidates_discovered == 2
        assert cycle_log.candidates_scored == 2
        assert cycle_log.top_score >= 0.0
        assert cycle_log.cycle_duration_ms > 0
        assert cycle_log.cycle_id.startswith("cyc-")
        assert cycle_log.llm_provider_used in ("Gemini", "Groq")
