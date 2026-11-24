"""
Application settings using Pydantic Settings with stdlib fallback.

Loads configuration from environment variables and .env file with resilient defaults:
- Deployed EchoMind Backend API base URL.
- Gemini Primary & Groq Fallback API credentials for LLM inference & live web search.
- SQLite database storage path.
- Discovery interval & publishing window duration.
- Minimum news score threshold (default: 75.0).
- Multi-agent cap (MAX_AGENTS = 5).
"""

import os
from typing import Any

try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv() or ".env", override=False)
except ImportError:
    from pathlib import Path
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.is_file():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    HAS_PYDANTIC_SETTINGS = True
except ImportError:
    HAS_PYDANTIC_SETTINGS = False


def _safe_int(val: Any, default: int) -> int:
    if val is None or str(val).strip() == "":
        return default
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return default


def _safe_float(val: Any, default: float) -> float:
    if val is None or str(val).strip() == "":
        return default
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return default


if HAS_PYDANTIC_SETTINGS:
    class Settings(BaseSettings):
        """Application settings loaded from environment variables."""

        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            case_sensitive=False,
            extra="ignore"
        )

        api_base_url: str = os.getenv(
            "ECHOMIND_API_BASE_URL",
            os.getenv("API_BASE_URL", "https://echomind-ltwo.onrender.com")
        ).rstrip("/")

        max_agents: int = _safe_int(os.getenv("MAX_AGENTS"), 5)
        gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
        gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        groq_api_key: str = os.getenv("GROQ_API_KEY", "")
        groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        llm_primary_provider: str = os.getenv("LLM_PRIMARY_PROVIDER", "gemini")
        llm_fallback_provider: str = os.getenv("LLM_FALLBACK_PROVIDER", "groq")
        database_url: str = os.getenv("DATABASE_URL", "sqlite:///agent_memory.db")
        agent_db_path: str = os.getenv("AGENT_DB_PATH", "agent_memory.db")
        discovery_interval_minutes: int = _safe_int(os.getenv("DISCOVERY_INTERVAL_MINUTES", os.getenv("AGENT_INTERVAL_MINUTES")), 45)
        discovery_jitter_seconds: int = _safe_int(os.getenv("DISCOVERY_JITTER_SECONDS"), 300)
        publish_window_minutes: int = _safe_int(os.getenv("PUBLISH_WINDOW_MINUTES"), 45)
        min_news_score: float = _safe_float(os.getenv("MIN_NEWS_SCORE"), 75.0)
        admin_api_key: str = os.getenv("ADMIN_API_KEY", "")
        turso_database_url: str = os.getenv("TURSO_DATABASE_URL", os.getenv("TURSO_DB_URL", ""))
        turso_auth_token: str = os.getenv("TURSO_AUTH_TOKEN", "")
        enable_docs: bool = os.getenv("ENABLE_DOCS", "true").lower() in ("true", "1", "yes")
        cors_origins: list[str] = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
        mutation_rate_limit: int = _safe_int(os.getenv("MUTATION_RATE_LIMIT_PER_MINUTE"), 30)
else:
    class Settings:
        """Fallback Application settings loaded directly from os.getenv."""

        def __init__(self):
            self.api_base_url = os.getenv(
                "ECHOMIND_API_BASE_URL",
                os.getenv("API_BASE_URL", "https://echomind-ltwo.onrender.com")
            ).rstrip("/")
            self.max_agents = _safe_int(os.getenv("MAX_AGENTS"), 5)
            self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
            self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
            self.groq_api_key = os.getenv("GROQ_API_KEY", "")
            self.groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
            self.llm_primary_provider = os.getenv("LLM_PRIMARY_PROVIDER", "gemini")
            self.llm_fallback_provider = os.getenv("LLM_FALLBACK_PROVIDER", "groq")
            self.database_url = os.getenv("DATABASE_URL", "sqlite:///agent_memory.db")
            self.agent_db_path = os.getenv("AGENT_DB_PATH", "agent_memory.db")
            self.discovery_interval_minutes = _safe_int(os.getenv("DISCOVERY_INTERVAL_MINUTES", os.getenv("AGENT_INTERVAL_MINUTES")), 45)
            self.discovery_jitter_seconds = _safe_int(os.getenv("DISCOVERY_JITTER_SECONDS"), 300)
            self.publish_window_minutes = _safe_int(os.getenv("PUBLISH_WINDOW_MINUTES"), 45)
            self.min_news_score = _safe_float(os.getenv("MIN_NEWS_SCORE"), 75.0)
            self.admin_api_key = os.getenv("ADMIN_API_KEY", "")
            self.turso_database_url = os.getenv("TURSO_DATABASE_URL", os.getenv("TURSO_DB_URL", ""))
            self.turso_auth_token = os.getenv("TURSO_AUTH_TOKEN", "")
            self.enable_docs = os.getenv("ENABLE_DOCS", "true").lower() in ("true", "1", "yes")
            self.cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
            self.mutation_rate_limit = _safe_int(os.getenv("MUTATION_RATE_LIMIT_PER_MINUTE"), 30)


# Global settings instance
settings = Settings()
