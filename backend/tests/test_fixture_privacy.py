"""Guard: small-company fixtures must not contain private individuals' personal data.

Their officers and PSCs are private people, and this repo goes public. Every person-name
and address field must be a pseudonym; birth dates must be absent.
"""

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest

from tests.conftest import FIXTURES
from ukco.privacy import is_address_text, is_person_name

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
        if isinstance(value, str) and (is_person_name(key) or is_address_text(key)):
            assert PSEUDONYM.match(value), f"unpseudonymised {key}={value!r} at {where}"
        if key == "registered_office_address" and isinstance(value, dict):
            assert set(value) <= {"address_line_1", "country"}, f"address detail at {where}"


POSTCODE = re.compile(r"\b[A-Z]{1,2}[0-9][0-9A-Z]? ?[0-9][A-Z]{2}\b")
SNAPSHOTS = sorted((FIXTURES.parents[2] / "evals" / "data").glob("*/snapshot.jsonl.gz"))


@pytest.mark.parametrize("snapshot", SNAPSHOTS, ids=lambda p: p.parent.name)
def test_eval_snapshots_have_no_personal_data(snapshot) -> None:
    from ukco.evals.frozen import read_snapshot

    for company in read_snapshot(snapshot):
        for response in company.responses:
            for path, key, value in walk(response["body"]):
                where = f"{company.company_number}{response['path']}{path}/{key}"
                assert key not in ("date_of_birth", "particulars"), where
                if not isinstance(value, str):
                    continue
                if is_person_name(key) or is_address_text(key):
                    assert PSEUDONYM.match(value), f"unpseudonymised {where}"
                elif key != "document_metadata":  # document ids can look like postcodes
                    assert not POSTCODE.search(value), f"postcode-like text at {where}"


def test_free_text_in_filing_descriptions_is_dropped() -> None:
    from ukco.privacy import Pseudonymiser

    filing = {
        "type": "PSC04",
        "description": "change-to-a-person-with-significant-control",  # the slug: kept
        "description_values": {
            "description": "PSC's service address changed to 1 High Street, Leeds, LS1 1AA",
            "change_date": "2024-09-30",
            "psc_name": "Jane Example",
        },
    }
    out = Pseudonymiser()(filing)
    assert out["description"] == filing["description"]
    assert out["description_values"] == {"change_date": "2024-09-30", "psc_name": "PERSON 1"}
