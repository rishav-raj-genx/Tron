"""
Model configuration for TRON Autonomous News Publisher.

Centralized model definitions used across all services and tools.
Change models here to update them everywhere.
"""

import os

# LLM Models (Primary: Gemini, Fallback: Groq)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Legacy/Default Alias
LLM_MODEL = GEMINI_MODEL

# Image Models (for image generation)
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "imagen-3.0-generate-002")

# Uncomment to override defaults:
# LLM_MAX_TOKENS = 1024
# LLM_TEMPERATURE = 0.8
