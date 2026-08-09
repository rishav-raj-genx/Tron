"""
Image generation tool using Gemini API.

Generates images based on text prompts and reference images from assets folder.
Used when enable_image_generation is set to True.
"""

import base64
import logging
from pathlib import Path

import httpx

from config.models import IMAGE_MODEL
from config.settings import settings
from utils.api import sanitize_url_credentials

logger = logging.getLogger(__name__)

# Tool configuration for auto-discovery
TOOL_CONFIG = {
    "name": "generate_image",
    "description": "Generate an image based on a text description using reference images for consistent character appearance",
    "params": {
        "prompt": {
            "type": "string",
            "description": "Text description of the image to generate",
            "required": True
        }
    }
}

ASSETS_PATH = Path(__file__).parent.parent.parent / "assets"


async def generate_image(prompt: str, **kwargs) -> bytes | None:
    """
    Generate an image from a text prompt.

    Args:
        prompt: Text description of the image to generate.
        **kwargs: Additional context (not used).

    Returns:
        Raw image bytes (PNG format), or None on error.
    """
    if not settings.enable_image_generation:
        logger.info("[IMAGE_GEN] Image generation is disabled")
        return None

    api_key = settings.gemini_api_key.strip()
    if not api_key:
        logger.warning("[IMAGE_GEN] Gemini API key not configured")
        return None

    logger.info(f"[IMAGE_GEN] Starting image generation for prompt: {prompt[:100]}...")

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{IMAGE_MODEL}:predict"
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {"sampleCount": 1}
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, params={"key": api_key}, json=payload)
            if response.status_code == 200:
                data = response.json()
                predictions = data.get("predictions", [])
                if predictions:
                    b64 = predictions[0].get("bytesBase64Encoded")
                    if b64:
                        return base64.b64decode(b64)
        logger.warning("[IMAGE_GEN] Image generation API returned no image data")
        return None
    except Exception as exc:
        logger.error(f"[IMAGE_GEN] Image generation error: {sanitize_url_credentials(exc)}")
        return None
