"""Guard: small-company fixtures must not contain private individuals' personal data.

Their officers and PSCs are private people, and this repo goes public. Every person-name
and address field must be a pseudonym; birth dates must be absent.
"""

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest

from scripts.record_kyb_fixtures import ADDRESS_TEXT, is_person_name
from tests.conftest import FIXTURES

KYB_FIXTURES = sorted((FIXTURES / "companies_house" / "kyb").glob("*.json"))
PSEUDONYM = re.compile(r"^(PERSON|ADDRESS) \d+$")


def walk(node: Any, path: str = "") -> Iterator[tuple[str, str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, key, value
            yield from walk(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from walk(value, f"{path}/{i}")


@pytest.mark.parametrize("fixture", KYB_FIXTURES, ids=lambda p: p.name)
def test_no_personal_data(fixture) -> None:
    for path, key, value in walk(json.loads(fixture.read_text())):
        where = f"{fixture.name}:{path}/{key}"
        assert key != "date_of_birth", f"birth date at {where}"
        if isinstance(value, str) and (is_person_name(key) or key in ADDRESS_TEXT):
            assert PSEUDONYM.match(value), f"unpseudonymised {key}={value!r} at {where}"
        if key == "registered_office_address" and isinstance(value, dict):
            assert set(value) <= {"address_line_1", "country"}, f"address detail at {where}"
