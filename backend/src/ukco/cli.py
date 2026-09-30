"""Command-line entry point: `uv run ukco --help`."""

import asyncio
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Annotated

import psycopg
import typer

from ukco import __version__
from ukco.companies_house import (
    AuthError,
    CompaniesHouseClient,
    CompaniesHouseError,
    client_from_settings,
)
from ukco.config import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)
ch = typer.Typer(no_args_is_help=True, help="Query the Companies House API directly.")
db = typer.Typer(no_args_is_help=True, help="Database schema management.")
ingest = typer.Typer(no_args_is_help=True, help="Run ingestion pipeline steps.")
app.add_typer(ch, name="ch")
app.add_typer(db, name="db")
app.add_typer(ingest, name="ingest")
evals = typer.Typer(no_args_is_help=True, help="Point-in-time outcome evaluation.")
app.add_typer(evals, name="eval")
EVAL_DIR = Path(__file__).resolve().parents[3] / "evals"

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["configure_logger"] = False
    return cfg


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def doctor() -> None:
    """Check that local setup is ready: database reachable, pgvector installed, API key set."""
    settings = get_settings()
    ok = True

    try:
        with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
            row = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
        if row:
            typer.secho(f"✓ Postgres reachable, pgvector {row[0]}", fg="green")
        else:
            typer.secho("✗ Postgres reachable but pgvector extension missing", fg="red")
            ok = False
    except psycopg.OperationalError as exc:
        typer.secho(f"✗ Postgres unreachable: {exc}".strip(), fg="red")
        ok = False

    ok = _check_schema() and ok
    ok = _check_api_key(settings.companies_house_api_key) and ok
    raise typer.Exit(code=0 if ok else 1)


def _check_schema() -> bool:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy.exc import OperationalError

    from ukco.db import get_engine

    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    try:
        with get_engine().connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
    except OperationalError:
        return False  # already reported as unreachable
    if current == head:
        typer.secho(f"✓ Database schema up to date (revision {head})", fg="green")
        return True
    typer.secho(
        f"✗ Database schema at {current}, latest is {head}: run `ukco db upgrade`", fg="red"
    )
    return False


def _check_api_key(key) -> bool:
    if not key or not key.get_secret_value().strip():
        typer.secho("✗ COMPANIES_HOUSE_API_KEY not set (see .env.example)", fg="red")
        return False

    async def probe() -> None:
        # Uncached on purpose: a cached response would hide a bad key.
        async with CompaniesHouseClient(key.get_secret_value().strip()) as client:
            await client.get_company("00502851")

    try:
        asyncio.run(probe())
    except AuthError as exc:
        typer.secho(f"✗ {exc} (test/sandbox keys don't work on the live API)", fg="red")
        return False
    except CompaniesHouseError as exc:
        typer.secho(f"✗ Companies House API unreachable: {exc}", fg="red")
        return False
    typer.secho("✓ Companies House API key accepted by the live API", fg="green")
    return True


def _run(coro) -> None:
    try:
        asyncio.run(coro)
    except (CompaniesHouseError, ValueError) as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(code=1) from exc


@ch.command("search")
def ch_search(query: str, limit: int = 10) -> None:
    """Search companies by name."""

    async def go() -> None:
        async with client_from_settings() as client:
            result = await client.search_companies(query, limit=limit)
        for c in result.items:
            typer.echo(f"{c.company_number}  {c.title}  [{c.company_status}]")

    _run(go())


@ch.command("company")
def ch_company(company_number: str) -> None:
    """Show a company profile."""

    async def go() -> None:
        async with client_from_settings() as client:
            p = await client.get_company(company_number)
        address = p.registered_office_address.one_line() if p.registered_office_address else "-"
        typer.echo(f"{p.company_number}  {p.company_name}")
        typer.echo(f"  status: {p.company_status}  type: {p.type}  created: {p.date_of_creation}")
        typer.echo(f"  SIC: {', '.join(p.sic_codes) or '-'}")
        typer.echo(f"  registered office: {address}")

    _run(go())


@ch.command("officers")
def ch_officers(company_number: str, all_: Annotated[bool, typer.Option("--all")] = False) -> None:
    """List officers (current only unless --all)."""

    async def go() -> None:
        async with client_from_settings() as client:
            async for o in client.iter_officers(company_number):
                if all_ or o.is_active:
                    end = f" → {o.resigned_on}" if o.resigned_on else ""
                    typer.echo(f"{o.officer_role:<22} {o.name}  ({o.appointed_on}{end})")

    _run(go())


@ch.command("filings")
def ch_filings(company_number: str, category: str | None = None, limit: int = 20) -> None:
    """List filing history, newest first (e.g. --category accounts)."""

    async def go() -> None:
        async with client_from_settings() as client:
            page = await client.list_filing_history(
                company_number, category=category, items_per_page=limit
            )
        for f in page.items:
            doc = f.document_id or "-"
            typer.echo(f"{f.date}  {f.type:<8} {f.category:<22} doc={doc}  {f.description}")

    _run(go())


@db.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    """Apply migrations (default: to latest)."""
    from alembic import command

    command.upgrade(_alembic_config(), revision)
    typer.secho(f"✓ Database upgraded to {revision}", fg="green")


@db.command("current")
def db_current() -> None:
    """Show the database's migration revision."""
    from alembic import command

    command.current(_alembic_config())


@ingest.command("metadata")
def ingest_metadata(
    company: Annotated[
        list[str] | None,
        typer.Option("--company", "-c", help="Company number(s). Default: whole seed list."),
    ] = None,
    limit: Annotated[int | None, typer.Option(help="Only the first N seed companies.")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Bulk: profiles, officers, PSCs, filings for many companies (no network check)."""
    from ukco.db import get_engine
    from ukco.ingest import load_seed
    from ukco.ingest.company import ingest_companies, summarise

    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(message)s"
    )
    numbers = company or [c.company_number for c in load_seed()][:limit]
    typer.echo(f"Ingesting metadata for {len(numbers)} companies…")

    async def go():
        async with client_from_settings() as client:
            return await ingest_companies(client, get_engine(), numbers)

    run_id, results = asyncio.run(go())
    typer.echo(f"Run {run_id}:")
    typer.echo(json.dumps(summarise(results), indent=2))
    failed = [r for r in results if r.error]
    for r in failed:
        typer.secho(f"✗ {r.company_number}: {r.error}", fg="red")
    raise typer.Exit(code=1 if failed else 0)


@app.command()
def fetch(
    company_number: str,
    network: Annotated[bool, typer.Option(help="Also fetch officers' other appointments.")] = True,
) -> None:
    """Fetch one company's full KYB bundle (+ director network) with stored evidence."""
    from ukco.db import get_engine
    from ukco.ingest.company import ingest_companies, summarise

    async def go():
        async with client_from_settings() as client:
            return await ingest_companies(
                client,
                get_engine(),
                [company_number],
                network=network,
                financials=True,
                trigger="fetch",
            )

    try:
        run_id, results = asyncio.run(go())
    except ValueError as exc:  # invalid company number
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(code=1) from exc
    result = results[0]
    if result.error:
        typer.secho(f"✗ {result.error}", fg="red", err=True)
        raise typer.Exit(code=1)
    summary = summarise(results)
    for key in ("companies_ok", "companies_failed"):
        summary.pop(key)
    typer.echo(f"Run {run_id}: {len(result.evidence)} citable facts")
    typer.echo(json.dumps(summary, indent=2))


SEVERITY_COLOURS = {"high": "red", "medium": "yellow", "low": "cyan"}


@app.command()
def assess(
    company_number: str,
    as_of: Annotated[
        str | None, typer.Option("--as-of", help="Back-test date YYYY-MM-DD (default: today).")
    ] = None,
    network: Annotated[bool, typer.Option(help="Check directors' other companies.")] = True,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the full assessment JSON.")
    ] = False,
) -> None:
    """Fetch a company and run the KYB rules: flags, citations and risk score."""
    import datetime as dt

    from ukco.db import get_engine
    from ukco.ingest.company import fetch_bundle, persist_bundle
    from ukco.risk import assess as run_assessment

    async def go():
        async with client_from_settings() as client:
            return await fetch_bundle(client, company_number, network=network)

    try:
        bundle = asyncio.run(go())
        with get_engine().begin() as conn:
            stored = persist_bundle(conn, bundle)
        result = run_assessment(
            bundle, stored.evidence, as_of=dt.date.fromisoformat(as_of) if as_of else None
        )
    except (CompaniesHouseError, ValueError) as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    mode = "live" if result.live else "back-test"
    typer.secho(
        f"{result.company_name} ({result.company_number}): as of {result.as_of} [{mode}], "
        f"policy v{result.policy_version} ({result.policy_sha256[:8]})",
        bold=True,
    )
    typer.secho(f"Risk score: {result.risk_score}/100\n", bold=True)
    for o in result.outcomes:
        if o.status == "flagged":
            for f in o.flags:
                typer.secho(f"  ✗ {f.severity.upper():<6} {f.title} (+{f.points})",
                            fg=SEVERITY_COLOURS[f.severity])  # fmt: skip
                typer.echo(f"           {f.explanation}")
                for c in f.evidence[:3]:
                    typer.echo(f"           ↳ {c.url}  [snapshot {c.snapshot_id} {c.pointer}]")
                if len(f.evidence) > 3:
                    typer.echo(f"           ↳ … {len(f.evidence) - 3} more citation(s)")
            if o.note:
                typer.echo(f"           note: {o.note}")
        elif o.status == "clear":
            typer.secho(f"  ✓ {o.rule}: {o.note}", fg="green")
        else:
            typer.secho(f"  · {o.rule}: not assessed. {o.note}", dim=True)


@app.command()
def report(
    company_number: str,
    as_of: Annotated[
        str | None, typer.Option("--as-of", help="Back-test date YYYY-MM-DD (default: today).")
    ] = None,
    network: Annotated[bool, typer.Option(help="Check directors' other companies.")] = True,
    llm: Annotated[
        bool, typer.Option(help="Add the LLM summary (needs ANTHROPIC_API_KEY).")
    ] = True,
    out: Annotated[Path, typer.Option(help="Directory for the JSON + Markdown report.")] = Path(
        "../reports"
    ),
) -> None:
    """Full KYB report: rules, citations validated against stored evidence, LLM narrative."""
    import datetime as dt

    from ukco.db import get_engine
    from ukco.report import ReportInvalid, generate_report, render_markdown, writer_from_settings

    writer = writer_from_settings() if llm else None

    async def go():
        async with client_from_settings() as client:
            return await generate_report(
                client,
                get_engine(),
                company_number,
                as_of=dt.date.fromisoformat(as_of) if as_of else None,
                network=network,
                writer=writer,
            )

    try:
        result = asyncio.run(go())
    except ReportInvalid as exc:
        typer.secho("✗ Report failed validation:", fg="red", err=True)
        for problem in exc.problems:
            typer.echo(f"  - {problem}", err=True)
        raise typer.Exit(code=2) from exc
    except (CompaniesHouseError, ValueError) as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(code=1) from exc

    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"{result.company.company_number}_{result.as_of}"
    stem.with_suffix(".json").write_text(result.model_dump_json(indent=2))
    stem.with_suffix(".md").write_text(render_markdown(result))
    p = result.provenance
    typer.secho(
        f"{result.company.name}: risk {result.risk_score}/100, {len(result.flags)} flag(s), "
        f"{len(result.evidence)} citations validated",
        bold=True,
    )
    narrative = p.narrative_status + (f" ({p.narrative_note})" if p.narrative_note else "")
    if p.llm:
        narrative += f", ${p.llm.cost_usd:.4f}, {p.llm.latency_ms} ms"
    typer.echo(f"narrative: {narrative}")
    typer.echo(f"→ {stem.with_suffix('.md')}\n→ {stem.with_suffix('.json')}")


@evals.command("build")
def eval_build(
    name: Annotated[str, typer.Option(help="Dataset name under evals/data/.")] = "v0",
    seed: int = 7,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Sample and label companies from the LIVE API into a frozen snapshot (run once)."""
    from ukco.evals.build import build_eval_set
    from ukco.evals.frozen import write_snapshot

    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(message)s")

    async def go():
        async with client_from_settings() as client:
            return await build_eval_set(client, seed=seed)

    companies, stats = asyncio.run(go())
    path = EVAL_DIR / "data" / name / "snapshot.jsonl.gz"
    write_snapshot(path, companies)
    seconds = sorted(stats.seconds_per_company)
    typer.echo(f"kept {len(companies)} of {stats.candidates} candidates → {path}")
    typer.echo(f"outcomes: {dict(Counter(c.outcome for c in companies))}")
    typer.echo(
        f"ineligible: {dict(stats.ineligible)}; over quota: {stats.over_quota}; "
        f"errors: {stats.errors}"
    )
    if seconds:
        typer.echo(f"live fetch per kept company: median {seconds[len(seconds) // 2]:.1f}s")


@evals.command("fidelity-sheet")
def eval_fidelity_sheet(
    name: Annotated[str, typer.Option(help="Dataset name under evals/data/.")] = "v0",
    companies: int = 12,
) -> None:
    """Write a rule-fidelity labelling sheet (CSV) for a human reviewer to fill in."""
    from ukco.evals.run import fidelity_sheet

    out = EVAL_DIR / "labels" / f"rule_fidelity_{name}.csv"
    rows = asyncio.run(
        fidelity_sheet(EVAL_DIR / "data" / name / "snapshot.jsonl.gz", out, companies=companies)
    )
    typer.echo(f"{rows} rows → {out}")
    typer.echo("Fill in your_verdict (agree/disagree) by checking each company's page.")


@evals.command("run")
def eval_run(
    name: Annotated[str, typer.Option(help="Dataset name under evals/data/.")] = "v0",
    summary_md: Annotated[
        Path | None,
        typer.Option(help="Also append the Markdown summary here (e.g. CI step summary)."),
    ] = None,
    check: Annotated[bool, typer.Option(help="Fail if AUC regresses vs the baseline.")] = False,
    update_baseline: Annotated[
        bool, typer.Option(help="Save this run as the new baseline.")
    ] = False,
    tolerance: float = 0.05,
) -> None:
    """Replay the frozen snapshot offline, score as of the eval date, compare to outcomes."""
    from ukco.evals.run import evaluate, fidelity_score, summarise, to_markdown, write_results

    snapshot = EVAL_DIR / "data" / name / "snapshot.jsonl.gz"
    baseline_path = EVAL_DIR / "baseline.json"
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else None

    results = asyncio.run(evaluate(snapshot))
    summary = summarise(results)
    sheet = EVAL_DIR / "labels" / f"rule_fidelity_{name}.csv"
    summary["rule_fidelity"] = fidelity_score(sheet) if sheet.exists() else None
    write_results(EVAL_DIR / "results" / name, results, summary)
    markdown = to_markdown(summary, baseline)
    typer.echo(markdown)
    if summary_md:
        with summary_md.open("a") as f:
            f.write(markdown)
    if update_baseline:
        baseline_path.write_text(json.dumps(summary, indent=2) + "\n")
        typer.secho(f"baseline updated → {baseline_path}", fg="green")
    if check and baseline:
        now, before = summary["score_auc"]["auc"], baseline["score_auc"]["auc"]
        if now is None or now < before - tolerance:
            typer.secho(
                f"✗ AUC {now} regressed below baseline {before} - {tolerance}", fg="red", err=True
            )
            raise typer.Exit(code=1)
        typer.secho(f"✓ AUC {now} within {tolerance} of baseline {before}", fg="green")


if __name__ == "__main__":
    app()
