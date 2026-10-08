"""A deployed API refuses to start without the settings that keep it safe."""

import pytest
from pydantic import SecretStr

from ukco.config import Settings, check_production

KEY = SecretStr("ch-key")
SECRET = SecretStr("x" * 32)


def test_local_needs_nothing() -> None:
    check_production(Settings(env="local", api_jwt_secret=None, companies_house_api_key=None))


@pytest.mark.parametrize(
    ("secret", "key", "message"),
    [
        (None, KEY, "API_JWT_SECRET"),  # would otherwise answer every request with 401
        (SECRET, None, "COMPANIES_HOUSE_API_KEY"),
        (SecretStr("short"), KEY, "at least 32"),
    ],
)
def test_production_needs_secrets(secret, key, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        check_production(
            Settings(env="production", api_jwt_secret=secret, companies_house_api_key=key)
        )


def test_production_with_secrets_starts() -> None:
    check_production(Settings(env="production", api_jwt_secret=SECRET, companies_house_api_key=KEY))
