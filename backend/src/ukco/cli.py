"""Command-line entry point: `uv run ukco --help`."""

import psycopg
import typer

from ukco import __version__
from ukco.config import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)


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


if __name__ == "__main__":
    app()
