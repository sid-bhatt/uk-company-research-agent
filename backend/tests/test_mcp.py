"""MCP server: tools called through the MCP layer, against Postgres and fixture data."""

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from sqlalchemy import Engine

from tests.conftest import load_fixture
from tests.fakes import ACTIVE
from tests.test_report import troubled_company
from ukco.mcp_server.server import build_server
from ukco.mcp_server.tools import KybTools
from ukco.report import store

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
async def server(engine: Engine) -> AsyncIterator[MCPServer]:
    ch = troubled_company()
    ch.routes["/search/companies"] = load_fixture("search_greggs")
    async with ch.client() as client:
        yield build_server(KybTools(engine, client, writer=None, caller="mcp:test"))


async def call(server: MCPServer, tool: str, **args: Any) -> Any:
    result = await server.call_tool(tool, args)
    assert isinstance(result, CallToolResult) and not result.is_error
    block = result.content[0]
    assert isinstance(block, TextContent)
    return json.loads(block.text)


async def test_tools_are_listed_with_descriptions(server: MCPServer) -> None:
    tools = {t.name: t for t in await server.list_tools()}
    assert set(tools) == {
        "search_companies",
        "get_risk_report",
        "get_flag_evidence",
        "officer_network",
    }
    assert all(t.description for t in tools.values())
    assert "company_number" in tools["get_risk_report"].input_schema["properties"]


async def test_search_companies(server: MCPServer) -> None:
    results = (await call(server, "search_companies", query="greggs", limit=2))["results"]
    assert results[0]["company_number"] == "00502851" and results[0]["name"] == "GREGGS PLC"


async def test_risk_report_is_returned_and_stored_for_review(
    server: MCPServer, engine: Engine
) -> None:
    report = await call(server, "get_risk_report", company_number=ACTIVE)

    assert report["risk_score"] == 14
    assert [f["id"] for f in report["flags"]] == ["F1", "F2"]
    flag = report["flags"][0]
    assert flag["evidence"][0]["url"].startswith("https://")
    assert {c["rule"] for c in report["checks"]} >= {"overdue_accounts", "financial_deterioration"}
    row = store.get_report(engine, report["report_id"])
    assert row and row["status"] == "succeeded" and row["requested_by"] == "mcp:test"


async def test_flag_evidence_includes_the_cited_values(server: MCPServer) -> None:
    report = await call(server, "get_risk_report", company_number=ACTIVE)
    evidence = await call(server, "get_flag_evidence", report_id=report["report_id"], flag_id="F2")

    assert evidence["flag"]["title"] == "Financial deterioration"
    values = [e["value"] for e in evidence["evidence"]]
    assert {"metric": "net_assets", "period_end": "2025-04-05", "value": "8000.00"} in values
    assert {"metric": "net_assets", "period_end": "2024-04-05", "value": "40000.00"} in values


async def test_officer_network(server: MCPServer) -> None:
    network = await call(server, "officer_network", company_number=ACTIVE)
    assert network["directors_checked"] == 1
    director = network["directors"][0]
    assert director["total_appointments"] >= 1 and "other_companies_by_status" in director


@pytest.mark.parametrize(
    ("tool", "args", "message"),
    [
        ("get_risk_report", {"company_number": "../../etc"}, "Invalid UK company number"),
        ("get_risk_report", {"company_number": "99999999"}, "No company 99999999"),
        ("get_risk_report", {"company_number": ACTIVE, "as_of": "yesterday"}, "YYYY-MM-DD"),
        ("get_flag_evidence", {"report_id": 424242, "flag_id": "F1"}, "not found"),
        ("search_companies", {"query": "  "}, "query is empty"),
    ],
    ids=["bad-number", "unknown-company", "bad-date", "unknown-report", "empty-query"],
)
async def test_tool_errors_are_clear(
    server: MCPServer, tool: str, args: dict[str, Any], message: str
) -> None:
    with pytest.raises(ToolError, match=message):
        await server.call_tool(tool, args)
