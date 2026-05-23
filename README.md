# xmpp-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an LLM operate over
**XMPP** — send and receive direct and group-chat messages, manage presence and
rosters, run service discovery, and apply **XEP-0258 security labels**.

It targets two XMPP server products in particular:

- **Openfire** — plus an admin layer over the Openfire *REST API* plugin
  (`of_*` tools: create/delete users, manage groups and rooms).
- **Isode M-Link** — via XEP-0258 security labels, M-Link's clearance-based
  message access control.

The core messaging surface speaks standard RFC 6120/6121, so it also works
against ejabberd, Prosody, and other compliant servers.

## Requirements

- Python 3.11+
- An XMPP service account the server logs in as
- *(optional)* An Openfire server with the **REST API** plugin enabled, for the
  `of_*` admin tools

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Configure

Copy `.env.example` to `.env` and fill it in, or set the variables in the
environment. Required:

| Variable | Purpose |
|---|---|
| `XMPP_JID` | Bare JID of the service account, e.g. `bot@example.com` |
| `XMPP_PASSWORD` | Password for that account |

Common optional variables:

| Variable | Default | Purpose |
|---|---|---|
| `XMPP_HOST` | JID domain | Connect host if it differs from the JID domain |
| `XMPP_PORT` | `5222` | C2S port |
| `XMPP_TLS_INSECURE` | `false` | Skip TLS cert checks (lab/self-signed only) |
| `XMPP_NICK` | `xmpp-mcp` | Default MUC nickname |
| `OPENFIRE_BASE_URL` | — | Enables `of_*` tools, e.g. `http://openfire:9090` |
| `OPENFIRE_SECRET_KEY` | — | Openfire REST API shared secret |
| `OPENFIRE_ADMIN_USER` / `OPENFIRE_ADMIN_PASSWORD` | — | Alternative to the secret key |

See `.env.example` for the full list.

## Run

```powershell
.\.venv\Scripts\xmpp-mcp.exe
```

The server speaks the MCP **stdio** transport. To explore it interactively:

```powershell
.\.venv\Scripts\python.exe -m fastmcp dev src/xmpp_mcp/server.py
```

## Use from Claude Desktop / Claude Code

Add to `claude_desktop_config.json` (Claude Desktop) or your MCP client config:

```json
{
  "mcpServers": {
    "xmpp": {
      "command": "C:\\work\\xmpp\\.venv\\Scripts\\xmpp-mcp.exe",
      "env": {
        "XMPP_JID": "bot@example.com",
        "XMPP_PASSWORD": "change-me",
        "XMPP_HOST": "xmpp.example.com"
      }
    }
  }
}
```

If you built the standalone executable (see below), point `command` at
`dist\xmpp-mcp.exe` instead — no Python install needed on that machine.

## Tools

| Tool | Description |
|---|---|
| `send_message` | Send a 1:1 chat message (optional XEP-0258 `security_label`) |
| `get_recent_messages` | Drain buffered inbound messages |
| `join_room` / `leave_room` | Join / leave a MUC room |
| `send_room_message` | Send to a joined MUC room (optional `security_label`) |
| `list_room_occupants` | Occupants of a joined room with role/affiliation |
| `set_presence` | Set availability and status text |
| `get_roster` / `add_contact` / `remove_contact` | Manage the contact list |
| `discover_features` | XEP-0030 disco; reports XEP-0258 support |
| `list_security_labels` | Fetch a destination's XEP-0258 label catalog (M-Link) |
| `of_list_users` / `of_get_user` / `of_create_user` / `of_delete_user` | Openfire user admin |
| `of_add_group_member` | Add a user to an Openfire group |
| `of_list_rooms` / `of_create_room` | Openfire MUC room admin |

Resources: `xmpp://roster`, `xmpp://server-info`.

### Sending under an M-Link security label

1. `list_security_labels(to="user@mlink.example.com")` → returns selectable labels.
2. `send_message(to=..., body=..., security_label="<selector>")` using a
   `selector` from step 1. The exact catalog label element is reused, which is
   what M-Link expects.

## Tests

Three layers:

```powershell
# Unit tests only (no network, no Docker) — fast.
.\.venv\Scripts\python.exe -m pytest -m "not docker and not integration"

# Docker-based end-to-end suite: boots Openfire 4.8.1, drives the MCP server
# in-process, asserts on real messaging, MUC, presence, discovery and `of_*`
# admin behaviour. Requires Docker Desktop. ~20s total.
.\.venv\Scripts\python.exe -m pytest -m docker

# Opt-in single-host integration test (just XMPP connect/disco) against a
# server you provide via env vars.
$env:XMPP_JID = "bot@your-server"; $env:XMPP_PASSWORD = "..."
.\.venv\Scripts\python.exe -m pytest -m integration
```

The Docker harness lives in `tests/integration/` and is self-contained — the
session-scoped fixture brings Openfire up, primes the REST API plugin (it's
disabled by default), pre-creates the test users via `<autosetup/>` in
`openfire.xml` and the test rooms via REST. See
[`docs/PLAN-integration-tests.md`](docs/PLAN-integration-tests.md) for the
design.

## Build a standalone Windows .exe

Produces `dist\xmpp-mcp.exe` with no Python runtime dependency:

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\pyinstaller.exe xmpp-mcp.spec
```

## License

MIT
