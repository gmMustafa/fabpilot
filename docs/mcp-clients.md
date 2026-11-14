# Connecting the FabPilot MCP servers to a client

Two stdio servers, both read-only:

| Server | Tools | Start |
|---|---|---|
| `fabpilot-sensors` | `list_machines`, `get_machine`, `get_readings`, `get_alarm_log`, `detect_anomalies` | `uv run sensor-mcp` |
| `fabpilot-docs` | `search_docs`, `get_document`, `get_incident_report` | `uv run docs-mcp` |

Prerequisites: `docker compose up -d`, migrated + seeded DB with simulated data, and the knowledge base
ingested (`cd api; uv run python -m app.rag.ingest`). `docs-mcp` loads the embedding model at start
(~5 s; the first run downloads ~130 MB).

**What leaves your machine:** the model runs at Anthropic, so every tool RESULT (sensor values,
document text) is sent there. Fine here, because all data is synthetic. With real plant data, that
is the question to settle before connecting anything.

## Claude Code

The repo root contains [`.mcp.json`](../.mcp.json) (project scope, committed). Open a Claude Code session
**in the repo root**; it asks once whether to trust the project's servers. Check with `/mcp`: both servers
should be `connected`, with 5 and 3 tools.

Good for development. NOT for the blind sanity check (`evals/mcp_sanity/protocol.md`): Claude Code in
the repo also has file and shell tools, so it can read `data/docs/` (or the `sim` schema) directly instead
of going through the MCP tools. Use Claude Desktop for that, which only sees the MCP servers.

## Claude Desktop

Edit `%APPDATA%\Claude\claude_desktop_config.json` (Windows) and restart Claude Desktop completely
(quit from the tray icon). Desktop does not start in the repo, so give absolute paths:

```json
{
  "mcpServers": {
    "fabpilot-sensors": {
      "command": "C:\\Users\\<you>\\.local\\bin\\uv.exe",
      "args": ["run", "--directory", "D:\\Learning\\2026\\FabPilot", "sensor-mcp"]
    },
    "fabpilot-docs": {
      "command": "C:\\Users\\<you>\\.local\\bin\\uv.exe",
      "args": ["run", "--directory", "D:\\Learning\\2026\\FabPilot", "docs-mcp"]
    }
  }
}
```

`where.exe uv` prints the path of your uv.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Server "failed" immediately | Run the command yourself in a terminal (`uv run sensor-mcp`): an exception at start (DB down, `.env` missing) is printed on stderr |
| Connects, then disconnects at the first call | Something printed to **stdout** (the protocol channel). Logs must go to stderr |
| Every DB tool fails on Windows | The server must run on the SelectorEventLoop (`app.runtime.run`), not `server.run()` |
| `docs-mcp` times out at start | First run downloads the model; run `uv run docs-mcp` once by hand, then reconnect |
| Claude Desktop logs | `%APPDATA%\Claude\logs\mcp-server-fabpilot-*.log` |
