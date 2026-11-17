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

import logging
import os

logger = logging.getLogger(__name__)

try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv() or ".env", override=False)
except ImportError:
    pass

try:
    from pydantic import AliasChoices, Field, field_validator
    from pydantic_settings import BaseSettings, SettingsConfigDict
    HAS_PYDANTIC_SETTINGS = True
except ImportError:
    HAS_PYDANTIC_SETTINGS = False


if HAS_PYDANTIC_SETTINGS:
    class Settings(BaseSettings):
        """Application settings loaded from environment variables and .env file."""

        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            case_sensitive=False,
            extra="ignore"
        )

        port: int = Field(
            default=8080,
            validation_alias=AliasChoices("PORT", "port")
        )
        api_base_url: str = Field(
            default="",
            validation_alias=AliasChoices("ECHOMIND_API_BASE_URL", "API_BASE_URL", "api_base_url")
        )
        max_agents: int = Field(
            default=5,
            validation_alias=AliasChoices("MAX_AGENTS", "max_agents")
        )
        gemini_api_key: str = Field(
            default="",
            validation_alias=AliasChoices("GEMINI_API_KEY", "gemini_api_key")
        )
        gemini_model: str = Field(
            default="gemini-2.0-flash",
            validation_alias=AliasChoices("GEMINI_MODEL", "gemini_model")
        )
        groq_api_key: str = Field(
            default="",
            validation_alias=AliasChoices("GROQ_API_KEY", "groq_api_key")
        )
        groq_model: str = Field(
            default="llama-3.3-70b-versatile",
            validation_alias=AliasChoices("GROQ_MODEL", "groq_model")
        )
        llm_primary_provider: str = Field(
            default="gemini",
            validation_alias=AliasChoices("LLM_PRIMARY_PROVIDER", "llm_primary_provider")
        )
        llm_fallback_provider: str = Field(
            default="groq",
            validation_alias=AliasChoices("LLM_FALLBACK_PROVIDER", "llm_fallback_provider")
        )
        database_url: str = Field(
            default="sqlite:///./data/tron.db",
            validation_alias=AliasChoices("DATABASE_URL", "database_url")
        )
        agent_db_path: str = Field(
            default="./data/tron.db",
            validation_alias=AliasChoices("DATABASE_PATH", "AGENT_DB_PATH", "agent_db_path")
        )
        discovery_interval_minutes: int = Field(
            default=45,
            validation_alias=AliasChoices("DISCOVERY_INTERVAL_MINUTES", "AGENT_INTERVAL_MINUTES", "discovery_interval_minutes")
        )
        discovery_jitter_seconds: int = Field(
            default=300,
            validation_alias=AliasChoices("DISCOVERY_JITTER_SECONDS", "discovery_jitter_seconds")
        )
        publish_window_minutes: int = Field(
            default=45,
            validation_alias=AliasChoices("PUBLISH_WINDOW_MINUTES", "publish_window_minutes")
        )
        min_news_score: float = Field(
            default=75.0,
            validation_alias=AliasChoices("MIN_NEWS_SCORE", "min_news_score")
        )
        admin_api_key: str = Field(
            default="",
            validation_alias=AliasChoices("ADMIN_API_KEY", "admin_api_key")
        )
        cors_origins: list[str] = Field(
            default_factory=lambda: [
                "http://localhost:8080",
                "http://127.0.0.1:8080",
                "http://localhost:3000",
                "http://127.0.0.1:3000",
            ],
            validation_alias=AliasChoices("CORS_ORIGINS", "cors_origins")
        )

        @field_validator("cors_origins", mode="before")
        @classmethod
        def parse_cors_origins(cls, v: object) -> list[str]:
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return ["http://localhost:8080", "http://127.0.0.1:8080"]
                if s.startswith("[") and s.endswith("]"):
                    import json
                    try:
                        parsed = json.loads(s)
                        if isinstance(parsed, list):
                            return [str(item).strip() for item in parsed if item]
                    except Exception:
                        pass
                return [item.strip() for item in s.split(",") if item.strip()]
            elif isinstance(v, (list, tuple, set)):
                return [str(item).strip() for item in v if item]
            return ["http://localhost:8080", "http://127.0.0.1:8080"]

        @field_validator("api_base_url", mode="after")
        @classmethod
        def strip_trailing_slash(cls, v: str) -> str:
            return v.rstrip("/") if v else v

        def validate_api_keys(self) -> tuple[bool, list[str]]:
            """Validate presence of required LLM API credentials.

            Returns:
                tuple[bool, list[str]]: (has_at_least_one_key, list_of_diagnostics)
            """
            diagnostics: list[str] = []
            has_gemini = bool(self.gemini_api_key and self.gemini_api_key.strip())
            has_groq = bool(self.groq_api_key and self.groq_api_key.strip())

            if not has_gemini and not has_groq:
                diagnostics.append(
                    "CRITICAL: Neither GEMINI_API_KEY nor GROQ_API_KEY is configured. "
                    "Autonomous LLM article generation and live web search grounding will fail. "
                    "Configure GEMINI_API_KEY (primary) or GROQ_API_KEY (fallback) in .env or environment."
                )
            elif not has_gemini:
                diagnostics.append(
                    "WARNING: GEMINI_API_KEY is not configured. Primary LLM inference and Gemini search grounding "
                    "will fail over to Groq (or arXiv fallback)."
                )
            elif not has_groq:
                diagnostics.append(
                    "INFO: GROQ_API_KEY is not configured. System will operate on Gemini primary without secondary LLM fallback."
                )
            return (has_gemini or has_groq), diagnostics
else:
    class Settings:
        """Fallback Application settings loaded directly from os.getenv."""

        def __init__(self) -> None:
            self.port = int(os.getenv("PORT", "8080"))
            self.api_base_url = os.getenv(
                "ECHOMIND_API_BASE_URL",
                os.getenv("API_BASE_URL", "")
            ).rstrip("/")
            self.max_agents = int(os.getenv("MAX_AGENTS", "5"))
            self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
            self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
            self.groq_api_key = os.getenv("GROQ_API_KEY", "")
            self.groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
            self.llm_primary_provider = os.getenv("LLM_PRIMARY_PROVIDER", "gemini")
            self.llm_fallback_provider = os.getenv("LLM_FALLBACK_PROVIDER", "groq")
            self.database_url = os.getenv("DATABASE_URL", "sqlite:///./data/tron.db")
            self.agent_db_path = os.getenv("DATABASE_PATH", os.getenv("AGENT_DB_PATH", "./data/tron.db"))
            self.discovery_interval_minutes = int(os.getenv("DISCOVERY_INTERVAL_MINUTES", os.getenv("AGENT_INTERVAL_MINUTES", "45")))
            self.discovery_jitter_seconds = int(os.getenv("DISCOVERY_JITTER_SECONDS", "300"))
            self.publish_window_minutes = int(os.getenv("PUBLISH_WINDOW_MINUTES", "45"))
            self.min_news_score = float(os.getenv("MIN_NEWS_SCORE", "75.0"))
            self.admin_api_key = os.getenv("ADMIN_API_KEY", "")
            cors_env = os.getenv(
                "CORS_ORIGINS",
                "http://localhost:8080,http://127.0.0.1:8080,http://localhost:3000,http://127.0.0.1:3000",
            ).strip()
            if cors_env.startswith("[") and cors_env.endswith("]"):
                import json
                try:
                    self.cors_origins = json.loads(cors_env)
                except Exception:
                    self.cors_origins = [c.strip() for c in cors_env.split(",") if c.strip()]
            else:
                self.cors_origins = [c.strip() for c in cors_env.split(",") if c.strip()]

        def validate_api_keys(self) -> tuple[bool, list[str]]:
            diagnostics: list[str] = []
            has_gemini = bool(self.gemini_api_key and self.gemini_api_key.strip())
            has_groq = bool(self.groq_api_key and self.groq_api_key.strip())

            if not has_gemini and not has_groq:
                diagnostics.append(
                    "CRITICAL: Neither GEMINI_API_KEY nor GROQ_API_KEY is configured. "
                    "Autonomous LLM article generation and live web search grounding will fail. "
                    "Configure GEMINI_API_KEY (primary) or GROQ_API_KEY (fallback) in .env or environment."
                )
            elif not has_gemini:
                diagnostics.append(
                    "WARNING: GEMINI_API_KEY is not configured. Primary LLM inference and Gemini search grounding "
                    "will fail over to Groq (or arXiv fallback)."
                )
            elif not has_groq:
                diagnostics.append(
                    "INFO: GROQ_API_KEY is not configured. System will operate on Gemini primary without secondary LLM fallback."
                )
            return (has_gemini or has_groq), diagnostics


# Global settings instance
settings = Settings()
