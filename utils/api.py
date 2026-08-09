"""
LLM Provider API configuration.

Centralized constants and helper functions for Gemini and Groq API calls.
"""

from config.settings import settings

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def get_gemini_headers() -> dict:
    """Get default headers for Gemini API requests."""
    return {
        "Content-Type": "application/json"
    }


def get_groq_headers() -> dict:
    """Get authorization and content headers for Groq API requests."""
    return {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json"
    }
