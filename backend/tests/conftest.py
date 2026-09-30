import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import Engine
from sqlalchemy.engine import make_url

from ukco.config import get_settings
from ukco.db import make_engine, sqlalchemy_url

FIXTURES = Path(__file__).parent / "fixtures"
TEST_DB = "ukco_test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / "companies_house" / f"{name}.json").read_text())


def _test_database_url() -> str:
    url = make_url(sqlalchemy_url(get_settings().database_url)).set(database=TEST_DB)
    return url.render_as_string(hide_password=False).replace("postgresql+psycopg", "postgresql")


def alembic_config(database_url: str):
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.attributes["database_url"] = database_url
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A fresh `ukco_test` database, migrated to head. Dropped and recreated each session."""
    admin_url = get_settings().database_url
    try:
        with psycopg.connect(admin_url, autocommit=True, connect_timeout=3) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
            conn.execute(f"CREATE DATABASE {TEST_DB}")
    except psycopg.OperationalError:
        pytest.skip("Postgres not running (make up)")

    from alembic import command

    url = _test_database_url()
    command.upgrade(alembic_config(url), "head")
    yield url


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    """Engine on the test database, with all tables emptied before each test."""
    eng = make_engine(database_url)
    with eng.begin() as conn:
        conn.exec_driver_sql(
            "TRUNCATE companies, officers, appointments, pscs, psc_statements, filings, "
            "documents, document_blobs, financial_facts, chunks, source_snapshots, "
            "ingestion_runs RESTART IDENTITY CASCADE"
        )
    yield eng
    eng.dispose()
