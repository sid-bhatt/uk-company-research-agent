"""In-process report jobs with progress events.

v1 runs jobs inside the API process (one ECS task): simple, and enough for an analyst
tool. Trade-off: a restart loses in-flight jobs, which are then marked failed on startup
(`store.fail_interrupted`). A queue (SQS) replaces this when there's more than one task.
"""

import asyncio
import datetime as dt
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine

from ukco.companies_house import CompaniesHouseClient, CompaniesHouseError
from ukco.report import store
from ukco.report.narrative import NarrativeWriter
from ukco.report.pipeline import generate_report
from ukco.report.validate import ReportInvalid

log = logging.getLogger(__name__)

TERMINAL_EVENTS = ("succeeded", "failed")


class ProgressHub:
    """Fan-out of progress events per report. Late subscribers get the history first."""

    def __init__(self) -> None:
        self._history: dict[int, list[dict[str, Any]]] = {}
        self._subscribers: dict[int, set[asyncio.Queue[dict[str, Any]]]] = {}

    def publish(self, report_id: int, event: str, **data: Any) -> None:
        message = {
            "event": event,
            "report_id": report_id,
            "at": dt.datetime.now(dt.UTC).isoformat(),
            **data,
        }
        self._history.setdefault(report_id, []).append(message)
        for queue in self._subscribers.get(report_id, ()):
            queue.put_nowait(message)

    def has_history(self, report_id: int) -> bool:
        return report_id in self._history

    async def subscribe(self, report_id: int) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        backlog = list(self._history.get(report_id, []))
        self._subscribers.setdefault(report_id, set()).add(queue)
        try:
            for message in backlog:
                yield message
                if message["event"] in TERMINAL_EVENTS:
                    return
            while True:
                message = await queue.get()
                yield message
                if message["event"] in TERMINAL_EVENTS:
                    return
        finally:
            self._subscribers[report_id].discard(queue)


@dataclass(frozen=True)
class JobRequest:
    report_id: int
    company_number: str
    as_of: dt.date | None
    network: bool
    narrative: bool
    requested_by: str | None = None


class JobRunner:
    def __init__(
        self,
        engine: Engine,
        client: CompaniesHouseClient,
        writer: NarrativeWriter | None,
        hub: ProgressHub,
        *,
        concurrency: int = 2,  # one shared rate limiter; a few jobs at once is plenty
    ) -> None:
        self.engine, self.client, self.writer, self.hub = engine, client, writer, hub
        self._slots = asyncio.Semaphore(concurrency)
        self._tasks: set[asyncio.Task[None]] = set()

    def submit(self, job: JobRequest) -> None:
        self.hub.publish(job.report_id, "queued", company_number=job.company_number)
        task = asyncio.create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for in-flight jobs (tests, graceful shutdown)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run(self, job: JobRequest) -> None:
        rid = job.report_id
        async with self._slots:
            store.mark_running(self.engine, rid)
            try:
                report = await generate_report(
                    self.client,
                    self.engine,
                    job.company_number,
                    as_of=job.as_of,
                    network=job.network,
                    writer=self.writer if job.narrative else None,
                    on_stage=lambda stage: self.hub.publish(rid, "stage", stage=stage),
                    requested_by=job.requested_by,
                )
                store.save_success(self.engine, rid, report)
                self.hub.publish(
                    rid,
                    "succeeded",
                    risk_score=report.risk_score,
                    flags=len(report.flags),
                    narrative=report.provenance.narrative_status,
                )
            except ReportInvalid as exc:
                self._fail(rid, f"report failed validation: {exc}")
            except (CompaniesHouseError, ValueError) as exc:
                self._fail(rid, str(exc))
            except Exception as exc:  # job boundary: the status must always end terminal
                log.exception("report %s crashed", rid)
                self._fail(rid, f"internal error: {type(exc).__name__}")

    def _fail(self, report_id: int, error: str) -> None:
        store.mark_failed(self.engine, report_id, error)
        self.hub.publish(report_id, "failed", error=error)
