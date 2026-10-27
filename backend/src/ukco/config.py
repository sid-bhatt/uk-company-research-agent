"""Typed settings loaded from environment variables (and a local .env file).

Every secret and environment-specific value goes through here, so there is one
place to see what the app needs in order to run.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    env: str = "local"
    database_url: str = "postgresql://ukco:ukco@localhost:5432/ukco"
    companies_house_api_key: SecretStr | None = None
    # Requests per 5-minute window; Companies House allows 600 per key.
    companies_house_rate_limit: int = 550
    cache_dir: str = ".cache"

    # LLM narrative (summary + flag explanations) and report chat. Optional: reports are
    # complete without it. LLM_API_KEY selects any OpenAI-compatible API (default: Google
    # Gemini's free tier; Groq or OpenRouter via LLM_BASE_URL); otherwise ANTHROPIC_API_KEY
    # selects Claude.
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    # Pinned: on the free tier the newest Flash models (3.7, 3.8) timed out or returned 503
    # (2026-10-27), and a "-latest" alias would move under us.
    llm_model: str = "gemini-3.5-flash"
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    # Claude only. Short grounded writing; raise only if evals show it helps.
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    chat_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    chat_daily_questions: int = 50  # per user: an LLM cost cap

    # Daily usage limits (UTC days), so a public demo can't run up Companies House or LLM use.
    reports_daily_per_user: int = 20
    reports_daily_total: int = 200
    chat_daily_total: int = 500

    # Tracing (OpenTelemetry → Langfuse). Off unless keys or an OTLP endpoint are set.
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    otel_exporter_otlp_endpoint: str | None = None
    trace_content: bool = False  # export prompts/completions (they name officers): opt in

    # Shared secret the web app signs short-lived user tokens with (HS256). When set, the API
    # requires them; when unset, only `local` accepts the X-User header.
    api_jwt_secret: SecretStr | None = None

    # Recorded on every report so a decision can be traced to the exact code that made it.
    git_sha: str = "dev"

    def llm_provider(self) -> Literal["openai_compatible", "anthropic"] | None:
        """Which LLM the narrative and chat use, or None when neither key is set."""
        if self.llm_api_key and self.llm_api_key.get_secret_value().strip():
            return "openai_compatible"
        if self.anthropic_api_key and self.anthropic_api_key.get_secret_value().strip():
            return "anthropic"
        return None


def check_production(settings: Settings) -> None:
    """Refuse to start a non-local deployment that would be unsafe or half-configured."""
    if settings.env == "local":
        return
    missing = [
        name
        for name, value in [
            ("API_JWT_SECRET", settings.api_jwt_secret),
            ("COMPANIES_HOUSE_API_KEY", settings.companies_house_api_key),
        ]
        if value is None or not value.get_secret_value().strip()
    ]
    if missing:
        raise RuntimeError(f"ENV={settings.env} needs {', '.join(missing)}")
    if len(settings.api_jwt_secret.get_secret_value()) < 32:  # type: ignore[union-attr]
        raise RuntimeError("API_JWT_SECRET must be at least 32 characters")


@lru_cache
def get_settings() -> Settings:
    return Settings()
