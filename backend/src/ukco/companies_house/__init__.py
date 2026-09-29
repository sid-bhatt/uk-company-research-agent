"""Companies House API client."""

from pathlib import Path

from ukco.companies_house.cache import DiskCache
from ukco.companies_house.client import (
    AuthError,
    CompaniesHouseClient,
    CompaniesHouseError,
    JsonObserver,
    NotFoundError,
    RateLimitedError,
    RetryPolicy,
    normalise_company_number,
)
from ukco.companies_house.ratelimit import RateLimiter
from ukco.config import get_settings

__all__ = [
    "AuthError",
    "CompaniesHouseClient",
    "CompaniesHouseError",
    "DiskCache",
    "JsonObserver",
    "NotFoundError",
    "RateLimitedError",
    "RateLimiter",
    "RetryPolicy",
    "client_from_settings",
    "normalise_company_number",
]


def client_from_settings() -> CompaniesHouseClient:
    settings = get_settings()
    key = settings.companies_house_api_key
    return CompaniesHouseClient(
        key.get_secret_value().strip() if key else "",
        cache=DiskCache(Path(settings.cache_dir) / "companies_house"),
        limiter=RateLimiter(limit=settings.companies_house_rate_limit),
    )
