"""MCP server exposing the KYB tools to assistants (Claude Desktop, Claude Code, …).

    uv run ukco mcp            # stdio transport, for a local MCP client

stdio only in v1: a network transport would need the same authentication as the API.
Nothing may print to stdout here (it carries the protocol); logs go to stderr.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ukco import __version__
from ukco.companies_house import client_from_settings
from ukco.db import get_engine
from ukco.mcp_server.tools import KybToolError, KybTools
from ukco.observability import setup_tracing, shutdown_tracing
from ukco.report.pipeline import writer_from_settings

INSTRUCTIONS = """KYB (know-your-business) due diligence on UK companies, from Companies House.

Typical flow: search_companies to find the company number → get_risk_report for a cited risk \
report (flags, score, every check's outcome) → get_flag_evidence to see exactly what a flag \
rests on → officer_network to look at directors' other companies.

Reports are automated screening, not advice: present flags as things for a person to verify, \
cite the evidence URLs, and don't state conclusions the report doesn't contain. Every report is \
stored for an analyst to review."""


def build_server(tools: KybTools | None = None) -> MCPServer:
    holder: dict[str, KybTools] = {}
    if tools is not None:
        holder["tools"] = tools

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        if "tools" in holder:
            yield
            return
        setup_tracing("ukco-mcp")
        client = client_from_settings()
        holder["tools"] = KybTools(get_engine(), client, writer_from_settings(), caller="mcp")
        try:
            yield
        finally:
            await client.aclose()
            shutdown_tracing()

    server = MCPServer(
        "ukco-kyb", instructions=INSTRUCTIONS, version=__version__, lifespan=lifespan
    )

    async def call(method: str, **kwargs: Any) -> Any:
        try:
            return await getattr(holder["tools"], method)(**kwargs)
        except KybToolError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool()
    async def search_companies(query: str, limit: int = 5) -> dict[str, Any]:
        """Find UK companies by name. Returns company numbers, status and incorporation date."""
        return {"results": await call("search_companies", query=query, limit=limit)}

    @server.tool()
    async def get_risk_report(
        company_number: str, as_of: str | None = None, check_network: bool = True
    ) -> dict[str, Any]:
        """Run a KYB risk report for a UK company number (e.g. "00502851").

        Returns the 0-100 risk score, red flags with severity, explanation and evidence links,
        and the outcome of every check. `as_of` (YYYY-MM-DD) back-tests using only records
        filed by that date. Takes a few seconds; the report is stored for analyst review."""
        return await call(
            "get_risk_report",
            company_number=company_number,
            as_of=as_of,
            check_network=check_network,
        )

    @server.tool()
    async def get_flag_evidence(report_id: int, flag_id: str) -> dict[str, Any]:
        """Show exactly what a flag in a stored report rests on: each cited Companies House
        record with its value, plus any analyst decision on that flag."""
        return await call("get_flag_evidence", report_id=report_id, flag_id=flag_id)

    @server.tool()
    async def officer_network(company_number: str) -> dict[str, Any]:
        """List a company's current and recent directors with their other companies and those
        companies' statuses (useful to spot directors linked to many failed companies)."""
        return await call("officer_network", company_number=company_number)

    return server


def main() -> None:
    build_server().run("stdio")
