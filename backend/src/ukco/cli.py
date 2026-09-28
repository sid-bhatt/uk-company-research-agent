"""Command-line entry point: `uv run ukco --help`."""

import asyncio

import psycopg
import typer

from ukco import __version__
from ukco.companies_house import CompaniesHouseError, client_from_settings
from ukco.config import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)
ch = typer.Typer(no_args_is_help=True, help="Query the Companies House API directly.")
app.add_typer(ch, name="ch")


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

    if settings.companies_house_api_key:
        typer.secho("✓ COMPANIES_HOUSE_API_KEY set", fg="green")
    else:
        typer.secho("✗ COMPANIES_HOUSE_API_KEY not set (see .env.example)", fg="yellow")
        ok = False

    raise typer.Exit(code=0 if ok else 1)


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
def ch_officers(company_number: str, all_: bool = typer.Option(False, "--all")) -> None:
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


if __name__ == "__main__":
    app()
