import psycopg
import pytest
from typer.testing import CliRunner

from ukco import __version__
from ukco.cli import app
from ukco.config import Settings, get_settings


def test_version_command() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_settings_read_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/x")
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "secret")
    settings = Settings()
    assert settings.database_url == "postgresql://u:p@db:5432/x"
    assert settings.companies_house_api_key is not None
    assert "secret" not in repr(settings)  # secrets never leak into logs


@pytest.mark.integration
def test_pgvector_available() -> None:
    with psycopg.connect(get_settings().database_url, connect_timeout=5) as conn:
        distance = conn.execute("SELECT '[1,0]'::vector <-> '[0,0]'::vector").fetchone()
    assert distance == (1.0,)
