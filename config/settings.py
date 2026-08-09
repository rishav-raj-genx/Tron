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

try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv() or ".env", override=False)
except ImportError:
    pass

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    HAS_PYDANTIC_SETTINGS = True
except ImportError:
    HAS_PYDANTIC_SETTINGS = False


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

        max_agents: int = int(os.getenv("MAX_AGENTS", "5"))
        gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
        gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        groq_api_key: str = os.getenv("GROQ_API_KEY", "")
        groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        llm_primary_provider: str = os.getenv("LLM_PRIMARY_PROVIDER", "gemini")
        llm_fallback_provider: str = os.getenv("LLM_FALLBACK_PROVIDER", "groq")
        database_url: str = os.getenv("DATABASE_URL", "sqlite:///agent_memory.db")
        agent_db_path: str = os.getenv("AGENT_DB_PATH", "agent_memory.db")
        discovery_interval_minutes: int = int(os.getenv("DISCOVERY_INTERVAL_MINUTES", os.getenv("AGENT_INTERVAL_MINUTES", "45")))
        discovery_jitter_seconds: int = int(os.getenv("DISCOVERY_JITTER_SECONDS", "300"))
        publish_window_minutes: int = int(os.getenv("PUBLISH_WINDOW_MINUTES", "45"))
        min_news_score: float = float(os.getenv("MIN_NEWS_SCORE", "75.0"))
        admin_api_key: str = os.getenv("ADMIN_API_KEY", "")
else:
    class Settings:
        """Fallback Application settings loaded directly from os.getenv."""

        def __init__(self):
            self.api_base_url = os.getenv(
                "ECHOMIND_API_BASE_URL",
                os.getenv("API_BASE_URL", "https://echomind-ltwo.onrender.com")
            ).rstrip("/")
            self.max_agents = int(os.getenv("MAX_AGENTS", "5"))
            self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
            self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
            self.groq_api_key = os.getenv("GROQ_API_KEY", "")
            self.groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
            self.llm_primary_provider = os.getenv("LLM_PRIMARY_PROVIDER", "gemini")
            self.llm_fallback_provider = os.getenv("LLM_FALLBACK_PROVIDER", "groq")
            self.database_url = os.getenv("DATABASE_URL", "sqlite:///agent_memory.db")
            self.agent_db_path = os.getenv("AGENT_DB_PATH", "agent_memory.db")
            self.discovery_interval_minutes = int(os.getenv("DISCOVERY_INTERVAL_MINUTES", os.getenv("AGENT_INTERVAL_MINUTES", "45")))
            self.discovery_jitter_seconds = int(os.getenv("DISCOVERY_JITTER_SECONDS", "300"))
            self.publish_window_minutes = int(os.getenv("PUBLISH_WINDOW_MINUTES", "45"))
            self.min_news_score = float(os.getenv("MIN_NEWS_SCORE", "75.0"))
            self.admin_api_key = os.getenv("ADMIN_API_KEY", "")


# Global settings instance
settings = Settings()
