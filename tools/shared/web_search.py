"""
Web search tool using Gemini Google Search grounding.

Provides real-time web search capability for the agent.
Uses Gemini's Google Search grounding with resilient URL citation extraction.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from config.settings import settings
from utils.api import sanitize_url_credentials
from utils.url_validator import sanitize_source_urls

logger = logging.getLogger(__name__)

# Tool configuration for auto-discovery
TOOL_CONFIG = {
    "name": "web_search",
    "description": "Search the web for current information, news, facts, or any data that might not be in training data",
    "params": {
        "query": {
            "type": "string",
            "description": "The search query",
            "required": True
        }
    }
}


async def web_search(query: str, **kwargs) -> str:
    """
    Search the web using Gemini Google Search grounding.

    Args:
        query: Search query string.
        **kwargs: Additional context - not used here.

    Returns:
        Formatted string with search results and verified sources.
    """
    logger.info(f"[WEB_SEARCH] Starting search: {query}")

    api_key = settings.gemini_api_key.strip()
    if api_key:
        try:
            gemini_model = settings.gemini_model or "gemini-2.5-flash"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={api_key}"
            payload = {
                "contents": [{
                    "role": "user",
                    "parts": [{"text": f"Search the web for real-time information: {query}"}]
                }],
                "tools": [{"google_search": {}}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 1000
                }
            }

            if HAS_HTTPX:
                timeout = httpx.Timeout(20.0, connect=5.0, read=15.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, json=payload)
                    if response.status_code == 200:
                        data = response.json()
                    else:
                        data = {}
            else:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=20.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

            candidates = data.get("candidates", [])
            if candidates:
                content_parts = candidates[0].get("content", {}).get("parts", [])
                content = " ".join([p.get("text", "") for p in content_parts if "text" in p])
                raw_sources = []
                grounding = candidates[0].get("groundingMetadata", {})
                for chunk in grounding.get("groundingChunks", []):
                    web = chunk.get("web", {})
                    uri = web.get("uri")
                    if uri and uri.startswith("http"):
                        raw_sources.append(uri)
                sources = sanitize_source_urls(raw_sources)
                if not sources:
                    logger.warning("[WEB_SEARCH] Gemini returned no valid grounding source URLs")
                    return "Error: Gemini live search returned no verified sources"
                logger.info(f"[WEB_SEARCH] Completed via Gemini search grounding: {len(sources)} sources found")
                return f"Search results:\n{content}\n\nSources: {len(sources)}\n" + "\n".join(sources)

        except Exception as exc:
            logger.warning(f"[WEB_SEARCH] Gemini search grounding request failed: {sanitize_url_credentials(exc)}")

    return "Error: Gemini live search unavailable"
