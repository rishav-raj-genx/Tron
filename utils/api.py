"""
LLM Provider API configuration.

Centralized constants and helper functions for Gemini and Groq API calls.
Includes credential sanitization for security log compliance.
"""

import re
from typing import Any

from config.settings import settings

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def sanitize_url_credentials(text: Any) -> str:
    """Sanitize API keys, bearer tokens, and credentials from URLs and log messages."""
    if not text:
        return ""
    s = str(text)
    # Redact ?key=... or &key=... query parameter values
    s = re.sub(r"([?&]key=)[^&\s'\"]+", r"\1[REDACTED]", s)
    # Redact x-goog-api-key header values
    s = re.sub(r"(x-goog-api-key['\"]?:\s*['\"]?)[^'\"]+", r"\1[REDACTED]", s, flags=re.IGNORECASE)
    # Redact Bearer tokens
    s = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.%]+", r"\1[REDACTED]", s, flags=re.IGNORECASE)
    return s


def get_gemini_headers() -> dict:
    """Get default headers for Gemini API requests."""
    headers = {
        "Content-Type": "application/json"
    }
    api_key = settings.gemini_api_key.strip()
    if api_key:
        headers["x-goog-api-key"] = api_key
    return headers


def get_groq_headers() -> dict:
    """Get authorization and content headers for Groq API requests."""
    return {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json"
    }
