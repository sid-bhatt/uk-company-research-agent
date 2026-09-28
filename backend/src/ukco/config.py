"""Typed settings loaded from environment variables (and a local .env file).

Every secret and environment-specific value goes through here, so there is one
place to see what the app needs in order to run.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    env: str = "local"
    database_url: str = "postgresql://ukco:ukco@localhost:5432/ukco"
    companies_house_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
