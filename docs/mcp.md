# MCP server

The KYB tools are exposed over the [Model Context Protocol](https://modelcontextprotocol.io)
so an assistant (Claude Desktop, Claude Code, any MCP client) can run due diligence directly.
It uses the same pipeline as the API: every report an assistant requests is stored and appears
in the analyst review queue.

| Tool | What it does |
|---|---|
| `search_companies(query, limit)` | Find company numbers by name |
| `get_risk_report(company_number, as_of?, check_network?)` | Run a cited KYB report (score, flags, every check); stored for review |
| `get_flag_evidence(report_id, flag_id)` | The records a flag rests on, with their values, plus any analyst decision |
| `officer_network(company_number)` | Directors' other companies and those companies' statuses |

## Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`, then restart
Claude Desktop (Postgres must be running: `make up`):

```json
{
  "mcpServers": {
    "ukco-kyb": {
      "command": "uv",
      "args": ["--directory", "/ABSOLUTE/PATH/TO/uk-company-research-agent/backend", "run", "ukco", "mcp"]
    }
  }
}
```

## Claude Code

```bash
claude mcp add ukco-kyb -- uv --directory "$PWD/backend" run ukco mcp
```

## Design notes
- **stdio only in v1.** A network transport (streamable HTTP) would need the same
  authentication as the API; it's planned alongside auth.
- Tool errors (invalid company number, unknown company, bad date) come back as MCP tool
  errors with a clear message, not stack traces.
- The server's instructions tell the assistant to present flags as things to verify, cite
  evidence URLs, and not state conclusions the report doesn't contain.
