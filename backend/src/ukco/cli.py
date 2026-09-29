"""Command-line entry point: `uv run ukco --help`."""

import asyncio
import json
import logging
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
                client, get_engine(), [company_number], network=network, trigger="fetch"
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


if __name__ == "__main__":
    app()
