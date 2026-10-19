"""Report validation: every citation must resolve to stored evidence, every id must exist.

A report that fails validation is never shown to a reviewer. This is the guard that
makes "cited" mean something: the citation points at bytes we still hold, and at the
exact place inside them.
"""

from typing import Any, Protocol

from sqlalchemy import Engine, select

from ukco.db.models import Document, DocumentBlob, SourceSnapshot
from ukco.evidence import Citation, resolve_pointer
from ukco.financials.ixbrl import IxbrlError, parse_ixbrl
from ukco.report.schema import RiskReport


class ReportInvalid(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class EvidenceStore(Protocol):
    def snapshot_body(self, snapshot_id: int) -> Any | None: ...
    def document_locators(self, document_id: str) -> set[str] | None: ...


class DbEvidenceStore:
    """Reads evidence back from Postgres: snapshots and document blobs."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._bodies: dict[int, Any] = {}
        self._locators: dict[str, set[str] | None] = {}

    def snapshot_body(self, snapshot_id: int) -> Any | None:
        if snapshot_id not in self._bodies:
            with self.engine.connect() as conn:
                self._bodies[snapshot_id] = conn.execute(
                    select(SourceSnapshot.body).where(SourceSnapshot.id == snapshot_id)
                ).scalar_one_or_none()
        return self._bodies[snapshot_id]

    def document_locators(self, document_id: str) -> set[str] | None:
        if document_id not in self._locators:
            with self.engine.connect() as conn:
                content = conn.execute(
                    select(DocumentBlob.content)
                    .join(Document, Document.content_sha256 == DocumentBlob.sha256)
                    .where(Document.document_id == document_id)
                ).scalar_one_or_none()
            try:
                self._locators[document_id] = (
                    {f.locator for f in parse_ixbrl(bytes(content)).facts} if content else None
                )
            except IxbrlError:
                self._locators[document_id] = None
        return self._locators[document_id]


def _citation_problem(c: Citation, store: EvidenceStore) -> str | None:
    if c.kind == "api":
        body = store.snapshot_body(c.snapshot_id or 0)
        if body is None:
            return f"snapshot {c.snapshot_id} not stored"
        try:
            resolve_pointer(body, c.pointer)
        except (KeyError, IndexError, TypeError, ValueError):
            return f"pointer {c.pointer!r} does not resolve in snapshot {c.snapshot_id}"
        return None
    locators = store.document_locators(c.document_id or "")
    if locators is None:
        return f"document {c.document_id} not stored"
    if c.pointer not in locators:
        return f"tag {c.pointer!r} not found in document {c.document_id}"
    return None


def validate_report(report: RiskReport, store: EvidenceStore) -> list[str]:
    problems: list[str] = []
    known = report.evidence_by_id()

    for item in report.evidence:
        if problem := _citation_problem(item.citation, store):
            problems.append(f"{item.id}: {problem}")

    def check_ids(where: str, ids: list[str]) -> None:
        problems.extend(f"{where}: unknown evidence id {i}" for i in ids if i not in known)

    check_ids("snapshot", report.company.evidence_ids)
    for fig in report.company.financials:
        check_ids(f"financials {fig.metric}", [fig.evidence_id])
    for flag in report.flags:
        check_ids(f"flag {flag.id}", flag.evidence_ids)

    knockout = any(f.details.get("knockout") for f in report.flags)
    expected = 100 if knockout else min(100, sum(f.points for f in report.flags))
    if report.risk_score != expected:
        basis = "a knockout flag" if knockout else "sum of flag points"
        problems.append(f"risk score {report.risk_score} != {basis} {expected}")

    flag_ids = {f.id for f in report.flags}
    for check in report.checks:
        problems.extend(
            f"check {check.rule}: unknown flag {i}" for i in check.flag_ids if i not in flag_ids
        )

    if report.narrative:
        problems.extend(narrative_problems(report))
    return problems


def narrative_problems(report: RiskReport) -> list[str]:
    """The LLM layer may only restate, and cite, what the deterministic report contains."""
    n = report.narrative
    if n is None:
        return []
    problems: list[str] = []
    known = report.evidence_by_id()
    flags = {f.id: f for f in report.flags}

    problems.extend(
        f"summary: unknown evidence id {i}" for i in n.summary_evidence_ids if i not in known
    )
    explained = [e.flag_id for e in n.flag_explanations]
    if sorted(explained) != sorted(flags):
        problems.append(f"flag explanations {sorted(explained)} don't match flags {sorted(flags)}")
    for e in n.flag_explanations:
        flag = flags.get(e.flag_id)
        if flag is None:
            continue
        if not e.evidence_ids:
            problems.append(f"explanation for {e.flag_id} cites nothing")
        stray = [i for i in e.evidence_ids if i not in flag.evidence_ids]
        if stray:
            problems.append(
                f"explanation for {e.flag_id} cites evidence outside that flag: {stray}"
            )
    return problems
