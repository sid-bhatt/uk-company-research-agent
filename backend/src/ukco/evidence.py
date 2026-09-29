"""Evidence: citations into stored API responses.

A citation is `snapshot_id + JSON pointer` into a `source_snapshots` row: the exact
bytes Companies House returned when the report was built. The `EvidenceIndex` maps
the facts rules care about ("this filing", "this appointment", "this PSC") to the
citation where that fact appears, so every flag can cite precisely.
"""

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from ukco.companies_house.models import (
    FilingHistoryItem,
    Officer,
    OfficerAppointment,
    Psc,
    PscStatement,
)

WEB_BASE_URL = "https://find-and-update.company-information.service.gov.uk"


class Citation(BaseModel):
    kind: Literal["api"] = "api"
    snapshot_id: int
    pointer: str  # RFC 6901, "" = whole response
    path: str  # API path, e.g. /company/13237895/officers
    url: str  # where a human can check it on the Companies House website


@dataclass(frozen=True)
class StoredResponse:
    snapshot_id: int
    path: str
    params: dict[str, Any]
    body: Any


def resolve_pointer(document: Any, pointer: str) -> Any:
    """Follow an RFC 6901 JSON pointer. Raises KeyError/IndexError if it doesn't resolve."""
    node = document
    if pointer == "":
        return node
    for raw in pointer.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


def web_url(path: str) -> str:
    return WEB_BASE_URL + path


def _iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


_COMPANY = re.compile(r"^/company/(?P<n>[A-Z0-9]{8})(?P<rest>/.*)?$")
_OFFICER_APPOINTMENTS = re.compile(r"^/officers/(?P<oid>[^/]+)/appointments$")

Key = tuple[str | None, ...]


class EvidenceIndex:
    """Fact key → citation. Keys:

    ("profile", n)                                   the company profile
    ("appointment", n, officer_id, role, appointed)  a row in the company's officer list
    ("network", officer_id, n, role, appointed)      a row in an officer's appointment list
    ("network_summary", officer_id)                  an officer's appointment list (totals)
    ("psc", n, psc_id)  ("psc_statement", n, statement, notified_on)
    ("filing", n, transaction_id)
    ("register", path)                               first page of any register (for absences)
    """

    def __init__(self) -> None:
        self._by_key: dict[Key, Citation] = {}

    def __len__(self) -> int:
        return len(self._by_key)

    def get(self, *key: str | None) -> Citation | None:
        return self._by_key.get(tuple(key))

    def require(self, *key: str | None) -> Citation:
        citation = self.get(*key)
        if citation is None:
            raise KeyError(f"no evidence for {key}")
        return citation

    def _add(self, key: Key, response: StoredResponse, pointer: str) -> None:
        # First occurrence wins: pages are indexed in request order.
        self._by_key.setdefault(
            key,
            Citation(
                snapshot_id=response.snapshot_id,
                pointer=pointer,
                path=response.path,
                url=web_url(response.path),
            ),
        )

    @classmethod
    def build(cls, responses: Iterable[StoredResponse]) -> "EvidenceIndex":
        index = cls()
        for r in responses:
            if int(r.params.get("start_index", 0) or 0) == 0:
                index._add(("register", r.path), r, "")
            if m := _OFFICER_APPOINTMENTS.match(r.path):
                index._index_network(m["oid"], r)
            elif m := _COMPANY.match(r.path):
                index._index_company(m["n"], m["rest"] or "", r)
        return index

    def _items(self, r: StoredResponse) -> list[tuple[str, dict[str, Any]]]:
        items = r.body.get("items", []) if isinstance(r.body, dict) else []
        return [(f"/items/{i}", item) for i, item in enumerate(items) if isinstance(item, dict)]

    def _index_company(self, n: str, rest: str, r: StoredResponse) -> None:
        if rest == "":
            self._add(("profile", n), r, "")
        elif rest == "/officers":
            for ptr, item in self._items(r):
                o = Officer.model_validate(item)
                if o.officer_id:
                    self._add(
                        ("appointment", n, o.officer_id, o.officer_role, _iso(o.appointed_on)),
                        r,
                        ptr,
                    )
        elif rest == "/persons-with-significant-control":
            for ptr, item in self._items(r):
                p = Psc.model_validate(item)
                if p.psc_id:
                    self._add(("psc", n, p.psc_id), r, ptr)
        elif rest == "/persons-with-significant-control-statements":
            for ptr, item in self._items(r):
                s = PscStatement.model_validate(item)
                self._add(("psc_statement", n, s.statement, _iso(s.notified_on)), r, ptr)
        elif rest == "/filing-history":
            for ptr, item in self._items(r):
                f = FilingHistoryItem.model_validate(item)
                self._add(("filing", n, f.transaction_id), r, ptr)

    def _index_network(self, officer_id: str, r: StoredResponse) -> None:
        if int(r.params.get("start_index", 0) or 0) == 0:
            self._add(("network_summary", officer_id), r, "")
        for ptr, item in self._items(r):
            a = OfficerAppointment.model_validate(item)
            self._add(
                (
                    "network",
                    officer_id,
                    a.appointed_to.company_number,
                    a.officer_role,
                    _iso(a.appointed_on),
                ),
                r,
                ptr,
            )
