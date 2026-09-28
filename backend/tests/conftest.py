import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / "companies_house" / f"{name}.json").read_text())
