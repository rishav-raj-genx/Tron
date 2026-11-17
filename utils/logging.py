"""
Structured JSON Observability & Secret-Redacting Logging.

Provides:
1. Context-propagated request_id and cycle_id tracking via contextvars.
2. Production-grade structured JSON log formatting (ISO-8601 UTC timestamps, level, logger, correlation IDs).
3. Secret-redaction engine masking Gemini API keys (AIza...), Groq keys (gsk_...), and Bearer tokens.
"""

import json
import logging
import re
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

# Correlation ID Context Variables
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
cycle_id_ctx: ContextVar[str | None] = ContextVar("cycle_id", default=None)


def get_request_id() -> str | None:
    """Retrieve the active request_id for the current async task context."""
    return request_id_ctx.get()


def set_request_id(req_id: str | None) -> Token[str | None]:
    """Bind a request_id to the current async task context."""
    return request_id_ctx.set(req_id)


def get_cycle_id() -> str | None:
    """Retrieve the active autonomous cycle_id for the current async task context."""
    return cycle_id_ctx.get()


def set_cycle_id(cyc_id: str | None) -> Token[str | None]:
    """Bind a cycle_id to the current async task context."""
    return cycle_id_ctx.set(cyc_id)


# Secret Redaction Patterns
REDACTION_PATTERNS = [
    # Google Gemini API Keys: AIzaSy... (39 chars total)
    (re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "AIza[REDACTED_GEMINI_KEY]"),
    # Groq API Keys: gsk_...
    (re.compile(r"gsk_[0-9A-Za-z]{20,}"), "gsk_[REDACTED_GROQ_KEY]"),
    # Bearer authorization tokens
    (re.compile(r"(?i)\bBearer\s+[a-zA-Z0-9_\-\.]{15,}"), "Bearer [REDACTED_TOKEN]"),
    # URL query parameter secrets: ?key=..., &api_key=...
    (re.compile(r"(?i)(key|api_key|token|secret|password)=([a-zA-Z0-9_\-]+)"), r"\1=[REDACTED]"),
]


def redact_secrets(text: str) -> str:
    """Redact known API keys, tokens, and credentials from log text."""
    if not text or not isinstance(text, str):
        return str(text)

    redacted = text
    for pattern, replacement in REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


# Standard LogRecord attribute names to exclude from JSON extra payload
STANDARD_RECORD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "request_id", "cycle_id"
}


class StructuredJsonFormatter(logging.Formatter):
    """
    Format LogRecords as structured single-line JSON objects.
    Enriches each record with ISO-8601 UTC timestamp, active correlation IDs,
    and applies automated secret redaction.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Determine timestamp in ISO-8601 UTC format
        dt = datetime.fromtimestamp(record.created, tz=UTC)
        timestamp_str = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        # Determine active correlation IDs (record attribute takes precedence, then contextvar)
        req_id = getattr(record, "request_id", None) or get_request_id()
        cyc_id = getattr(record, "cycle_id", None) or get_cycle_id()

        # Format message and apply secret redaction
        raw_message = record.getMessage()
        safe_message = redact_secrets(raw_message)

        payload: dict[str, Any] = {
            "timestamp": timestamp_str,
            "level": record.levelname,
            "logger": record.name,
            "message": safe_message,
        }

        if req_id:
            payload["request_id"] = req_id
        if cyc_id:
            payload["cycle_id"] = cyc_id

        # Attach custom extra fields (e.g. latency, status codes, cycle metrics)
        for key, value in record.__dict__.items():
            if key not in STANDARD_RECORD_ATTRS and not key.startswith("_"):
                if isinstance(value, str):
                    payload[key] = redact_secrets(value)
                else:
                    payload[key] = value

        # Handle exception information safely
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            payload["exception"] = redact_secrets(exc_text)

        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger with StructuredJsonFormatter on standard stream handler."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter())

    # Replace existing handlers cleanly
    root_logger.handlers = [handler]
