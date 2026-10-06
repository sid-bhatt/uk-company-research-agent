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

    # LLM narrative (summary + flag explanations). Optional: reports are complete without it.
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5-5"
    # Short grounded writing; raise only if evals show it helps.
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    chat_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    chat_daily_questions: int = 50  # per user: an LLM cost cap

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
