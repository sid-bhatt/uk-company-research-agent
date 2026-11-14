"""Pseudonymisation for data that leaves the database (test fixtures, eval snapshots).

Small companies' officers and PSCs are private individuals, and this repository is
public. People's names and addresses (including registered-office addresses, often a
director's home) become stable pseudonyms; birth dates and similar fields are dropped.
Company names and opaque Companies House ids are kept.
"""

import json
from typing import Any

# Dropped outright. `particulars` is a charge's free-text property description (often
# a flat's full address); no rule uses it.
DROP = {
    "date_of_birth", "address", "name_elements", "principal_office_address", "identification",
    "particulars",
}  # fmt: skip
ADDRESS_TEXT = {"old_address", "new_address"}  # AD01 description_values
# Free text inside a filing's description_values (e.g. "PSC's service address changed to
# <full address>"); no rule reads it. The filing's own `description` slug is kept.
DESCRIPTION_VALUES_FREE_TEXT = {"description"}


def is_address_text(key: str) -> bool:
    """Known address fields, plus any `*_address` a new filing type might add."""
    return key in ADDRESS_TEXT or key.endswith("_address")


def is_person_name(key: str) -> bool:
    """`name`, plus any `*_name` that isn't a company's: filing descriptions carry people's
    names as `officer_name`, `psc_name`, … and new filing types may add more."""
    return key == "name" or (key.endswith("_name") and "company" not in key)


class Pseudonymiser:
    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self.addresses: dict[str, str] = {}

    def _address(self, key: str) -> str:
        # Stable, so "moved back to a previous address" is still visible in tests.
        return self.addresses.setdefault(key, f"ADDRESS {len(self.addresses) + 1}")

    def __call__(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self(v) for v in value]
        if not isinstance(value, dict):
            return value
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in DROP:
                continue
            if is_person_name(k) and isinstance(v, str):
                out[k] = self.names.setdefault(v, f"PERSON {len(self.names) + 1}")
            elif is_address_text(k) and isinstance(v, str):
                out[k] = self._address(v)
            elif k == "description_values" and isinstance(v, dict):
                kept = {dk: dv for dk, dv in v.items() if dk not in DESCRIPTION_VALUES_FREE_TEXT}
                out[k] = self(kept)
            elif k == "registered_office_address" and isinstance(v, dict):
                # Small companies often register at a director's home address.
                full = json.dumps(v, sort_keys=True)
                out[k] = {"address_line_1": self._address(full), "country": v.get("country")}
            else:
                out[k] = self(v)
        return out
