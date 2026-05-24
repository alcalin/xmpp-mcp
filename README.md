# xmpp-mcp

[![CI](https://github.com/alcalin/xmpp-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/alcalin/xmpp-mcp/actions/workflows/ci.yml)

An [MCP](https://modelcontextprotocol.io) server that lets an LLM operate over
**XMPP** — send and receive direct and group-chat messages, manage presence and
rosters, run service discovery, drive **XEP-0060 pubsub with XEP-0004 dynamic
data forms** (read nodes, publish/fill forms, react to live events), and apply
**XEP-0258 security labels**.

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
| `search_messages` | Non-destructive search over the inbox (by query/room/participant/since) — powers "who said X about Y" |
| `mam_query` | Query server-side MUC history (XEP-0313); works on ejabberd, see notes for Openfire |
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

### Pubsub (XEP-0060) + dynamic data forms (XEP-0004)

Full pubsub coverage — read and write nodes, publish and fill dynamic forms,
and react to live events:

| Area | Tools |
|---|---|
| Nodes | `pubsub_list_nodes`, `pubsub_create_node`, `pubsub_delete_node` |
| Config | `pubsub_get_node_config`, `pubsub_configure_node` |
| Forms (XEP-0004) | `pubsub_publish_form_template`, `pubsub_submit_form`, `pubsub_read_forms`, `pubsub_get_item` |
| Raw payloads | `pubsub_publish_raw`, `pubsub_get_items_raw` |
| Item ops | `pubsub_retract_item`, `pubsub_purge_node` |
| Subscriptions | `pubsub_subscribe`, `pubsub_unsubscribe`, `pubsub_list_subscriptions`, `pubsub_list_node_subscriptions`, `pubsub_get_subscription_options`, `pubsub_set_subscription_options` |
| Affiliations | `pubsub_list_my_affiliations`, `pubsub_list_node_affiliations`, `pubsub_set_affiliations` |
| Live events | `pubsub_get_recent_events` (drain buffered publish/retract notifications) |

Form payloads are exchanged as `DataForm` dicts
(`{type, title?, instructions?, fields[]}`) — never raw XML; `pubsub_publish_raw`
/ `pubsub_get_items_raw` are the escape hatch for non-form items (ATOM, JSON,
PEP). `pubsub_get_item` auto-detects form vs raw. See `scripts/demo_pubsub_forms.py`
for a publish → fill → read-back walkthrough.

> On Openfire, create form nodes with
> `config_values={"pubsub#persist_items": true, "pubsub#max_items": "100"}` —
> the default `max_items=1` evicts a template as soon as someone submits.

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
