"""Database access: engine factory and schema."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from ukco.config import get_settings


def sqlalchemy_url(database_url: str) -> str:
    """postgresql://... → postgresql+psycopg://... (psycopg 3 driver)."""
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url.removeprefix(prefix)
    return database_url


def make_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    return create_engine(sqlalchemy_url(url), pool_pre_ping=True)


@lru_cache
def get_engine() -> Engine:
    return make_engine()
