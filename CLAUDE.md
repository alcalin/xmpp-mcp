# xmpp-mcp

An MCP server that lets an LLM operate over XMPP — direct and group chat,
roster/presence, service discovery, **XEP-0258 security labels** (Isode
M-Link), full **XEP-0060 pubsub + XEP-0004 data forms**, plus admin over the
Openfire REST API plugin.

Targets two XMPP server products in particular: **Openfire** (open-source) and
**Isode M-Link** (commercial, military-grade). The core messaging surface is
RFC 6120/6121 so it also works against ejabberd, Prosody, etc.

## Stack & layout

Python 3.11+ • FastMCP v2 (`fastmcp` ≥ 2.0) • slixmpp (1.15+) • httpx •
pydantic-settings.

```
src/xmpp_mcp/
  __init__.py
  __main__.py            # `python -m xmpp_mcp` entry; PyInstaller entry too
  server.py              # FastMCP app, lifespan, tool registration, main()
  config.py              # pydantic-settings Settings (XMPP_* + OPENFIRE_*)
  xmpp_client.py         # slixmpp ClientXMPP wrapper, inbox deque,
                         #   pubsub event buffer, pubsub property
  data_forms.py          # XEP-0004 DataForm / FormField TypedDicts;
                         #   parse_form, build_form_element, build_submit_form
  security_labels.py     # XEP-0258 catalog fetch + label builder (M-Link)
  pubsub.py              # PubSubClient — async wrapper over slixmpp xep_0060
  mam.py                 # MAMClient — XEP-0313 archive queries (xfail on this lab,
                         #   see gotcha #16)
  openfire_admin.py      # Openfire REST API client (httpx)
  tools/
    __init__.py          # CTX_* keys, get_xmpp / get_settings / get_openfire
    messaging.py         # send_message, get_recent_messages, search_messages
    muc.py               # join_room, leave_room, send_room_message, ...
    presence.py          # set_presence, get_roster, add/remove_contact + resource
    disco.py             # discover_features, list_security_labels + resource
    pubsub.py            # 23 pubsub tools (see "Pubsub surface" below)
    mam.py               # mam_query — XEP-0313 historical room queries
    admin.py             # Openfire of_* tools

tests/                   # unit tests (no network)
  test_config.py
  test_data_forms.py
  test_search_inbox.py
  test_security_labels.py
  test_openfire_admin.py
  test_pubsub_events.py
  test_xmpp_client.py        # opt-in `integration` marker, needs live server

tests/integration/       # docker-based E2E (two labs available — see below)
  conftest.py            # session-scoped openfire fixture (compose up/down)
                         #   + function-scoped mcp / raw_* / seclabel_component
  docker/
    Dockerfile.openfire  # nasqueron/openfire:4.8.1 + REST API plugin + autosetup
    openfire.xml         # <autosetup/> pre-creates bot/alice/bob/carol
    docker-compose.yml   # project name xmpp-mcp-test; exposes 5222/5269/5275/9090
    ejabberd/            # alternative lab — MAM actually works here
      docker-compose.yml # project name xmpp-mcp-ej; same ports + 5280 admin
      ejabberd.yml       # mod_mam, mod_muc with mam=true on every room
  helpers/
    raw_client.py        # RawXMPPClient — slixmpp wrapper for the "other side"
    chat_script.py       # ChatScript — scripted multi-room conversations
    seclabel_component.py # SecurityLabelComponent — XEP-0114 stub of M-Link's
                         #   XEP-0258 catalog (on seclabel.xmpp.test)
  test_smoke.py
  test_messaging_e2e.py
  test_muc_e2e.py
  test_presence_e2e.py
  test_discovery_e2e.py
  test_openfire_admin_e2e.py
  test_chat_search_e2e.py        # scripted chat → search_messages assertions
  test_pubsub_e2e.py             # 15 tests: nodes, forms, raw, events, niche ops
  test_security_labels_e2e.py    # 4 tests vs the XEP-0258 stub component
  test_mcp_wire_e2e.py           # 4 tests over real stdio JSON-RPC to dist\\xmpp-mcp.exe
  test_resilience_e2e.py         # 3 tests — docker pause/unpause survival
  test_llm_driven_e2e.py         # 1 test — real Claude session via Anthropic SDK
                                 #   (opt-in: needs ANTHROPIC_API_KEY)

scripts/                 # one-off demo runners (use the test fixtures + an
  demo_chat_search.py    #   in-process FastMCP Client)
  demo_pubsub_forms.py
```

## MCP tool surface (`pytest`-verified)

**Messaging / MUC / presence / discovery / admin** — see `src/xmpp_mcp/tools/`.

Notably:

- `search_messages(query?, room?, participant?, since?, limit?)` — non-destructive
  search over the inbox. Powers "who said X about Y" workflows. Inbox records
  carry `room` and `nick` derived fields for clean filtering.

### Pubsub surface (23 tools — full XEP-0060 coverage)

| Area | Tools |
|---|---|
| Nodes | `pubsub_list_nodes`, `pubsub_create_node` (with optional `config_values`), `pubsub_delete_node` |
| Config | `pubsub_get_node_config`, `pubsub_configure_node` |
| Forms | `pubsub_publish_form_template`, `pubsub_submit_form`, `pubsub_read_forms`, `pubsub_get_item` |
| Raw payloads | `pubsub_publish_raw`, `pubsub_get_items_raw`, `pubsub_get_item` (auto-detects form vs raw) |
| Item ops | `pubsub_retract_item`, `pubsub_purge_node` |
| Subscriptions | `pubsub_subscribe`, `pubsub_unsubscribe`, `pubsub_list_subscriptions`, `pubsub_list_node_subscriptions`, `pubsub_get_subscription_options` (incl. `defaults=True`), `pubsub_set_subscription_options` |
| Affiliations | `pubsub_list_my_affiliations`, `pubsub_list_node_affiliations`, `pubsub_set_affiliations` |
| **Live events** | `pubsub_get_recent_events(node?, kind?, limit?)` — drains buffered pubsub-event notifications captured by slixmpp handlers; required for "react to new fills" workflows |

All form payloads exchange the `DataForm` shape from `xmpp_mcp.data_forms`
(`{type, title?, instructions?, fields[]}`) — never raw XML in the API. The
escape hatch is `pubsub_publish_raw` / `pubsub_get_items_raw` for non-form
items (PEP, ATOM, JSON-in-pubsub).

## Running

```powershell
# install
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# unit tests (fast, no network) — 44 tests, ~2.5s
.\.venv\Scripts\python.exe -m pytest -m "not docker and not integration"

# full docker-based E2E suite (boots Openfire) — 52 tests, ~70s
.\.venv\Scripts\python.exe -m pytest -m docker

# wire-protocol tests against the built .exe (subset of docker)
.\.venv\Scripts\pyinstaller.exe xmpp-mcp.spec   # rebuild if stale!
.\.venv\Scripts\python.exe -m pytest -m wire

# bring up the lab — choose the right server for the job
python start-lab.py              # Openfire (legacy, of_* admin works,
                                 #   MAM doesn't — see gotcha #16)
python start-lab-ejabberd.py     # ejabberd 25.04 (MAM works, no Openfire REST,
                                 #   what you want for cross-session history)

# real-LLM tests (opt-in, costs API tokens, needs ANTHROPIC_API_KEY)
$env:ANTHROPIC_API_KEY = "sk-…"
.\.venv\Scripts\python.exe -m pytest -m llm

# everything
.\.venv\Scripts\python.exe -m pytest

# build standalone Windows .exe (no runtime needed on target)
.\.venv\Scripts\pyinstaller.exe xmpp-mcp.spec   # → dist\xmpp-mcp.exe (~32 MB)

# run the server
.\.venv\Scripts\xmpp-mcp.exe                    # stdio MCP transport
python -m xmpp_mcp                              # same thing from source

# lab demos
python scripts\demo_chat_search.py              # multi-room chat + search_messages
python scripts\demo_pubsub_forms.py             # publish form, 3 fills, read back
```

Required env vars: `XMPP_JID`, `XMPP_PASSWORD`. Optional: `XMPP_HOST`,
`XMPP_PORT`, `XMPP_TLS_INSECURE`, `XMPP_NICK`, `OPENFIRE_BASE_URL` +
`OPENFIRE_ADMIN_USER` / `OPENFIRE_ADMIN_PASSWORD` (enables `of_*` tools). See
`.env.example`.

## Architectural conventions

- **All XMPP / Openfire ops raise `XMPPError` / `OpenfireError`** at the
  wrapper layer (`xmpp_client.py`, `pubsub.py`, `openfire_admin.py`). Tools
  catch those and re-raise as `fastmcp.exceptions.ToolError` — anything else
  leaks as an unhelpful stacktrace to the client.
- **Tool modules expose `register(mcp)`** and are wired in `server.py`.
- **Shared state lives in `ctx.lifespan_context`** (keys: `xmpp`, `settings`,
  `openfire`). Helpers in `tools/__init__.py`: `get_xmpp`, `get_settings`,
  `get_openfire` (the last raises a clean ToolError when not configured).
- **DataForm dicts, never raw XML, in MCP responses.** Returning a
  `TypedDict` directly from a FastMCP tool wraps it in a Pydantic root model
  on the client side (non-subscriptable); declare return type as
  `dict[str, Any]` while still building the typed shape internally.

## Gotchas already learned (don't re-discover)

1. **slixmpp `enable_direct_tls` footgun.** When you pin a custom host/port,
   slixmpp's default tries direct TLS on that port *before* STARTTLS. On the
   standard C2S port 5222 the ClientHello bytes hit the server's plain XML
   parser → connection reset, no useful error. Always set
   `xmpp.enable_direct_tls = False` when `XMPP_HOST` is given. (Already
   handled in `XMPPClient.__init__` and `RawXMPPClient.__init__`.)
2. **slixmpp `connect()` signature changed.** 1.15.0 is `connect(host, port)`,
   *not* `connect(address=(host, port))`.
3. **FastMCP banner corrupts stdio.** `create_server().run(show_banner=False)`
   — stdout is reserved for the MCP protocol on stdio transport.
4. **slixmpp double-handler on MUC.** Subscribing to both `message` AND
   `groupchat_message` double-counts every MUC line; subscribe to `message`
   only (it fires for groupchat too).
5. **MUC history on join.** `join_muc_wait(maxstanzas=0)` to opt out — without
   it, every new connection back-fills the inbox with the room's recent
   history and `search_messages` returns duplicates.
6. **slixmpp `pubsub_publish` fires per-item.** For multi-item messages it
   fires once per item with the *full* msg attached every time; dedupe by
   `msg["id"]` to process all items exactly once. (Done in
   `XMPPClient._on_pubsub_items` via `_seen_pubsub_msg_ids`.)
7. **Openfire pubsub default `max_items=1` + `persist_items=false`.** Without
   overriding these at node creation, publishing a second item silently evicts
   the first. The form template vanishes as soon as someone submits. Tests
   that need persistence pass `config_values={"pubsub#persist_items": True,
   "pubsub#max_items": "100"}` to `pubsub_create_node`.
8. **Openfire REST API is disabled by default.** Needs two DB-backed system
   properties: `adminConsole.access.allow-wildcards-in-excludes=true` (the
   actual gate — without it AuthCheckFilter strips the plugin's URL
   exclusions) **and** `plugin.restapi.enabled=true`. Openfire has no
   facility to seed DB-backed system properties at first boot, so the test
   fixture drives the admin-console session form (`tests/integration/conftest.py
   _enable_restapi`). Same handshake is needed for any deployment.
9. **Openfire pubsub purge keeps the last item.** Even after `purge`, one
   "last-published" item may remain (configurable per node). Tests assert
   `count < before and count <= 1`, not strict equality with zero.
10. **Openfire doesn't implement per-node default subscription options.**
    `pubsub_get_subscription_options(defaults=True)` returns a clean
    `ToolError` against Openfire; works against ejabberd / Prosody / M-Link.
11. **Openfire AUTO_SETUP for embedded DB.** The autosetup `<database>` block
    only handles the "standard" (external JDBC) path. For embedded HSQLDB,
    put `<connectionProvider><className>...EmbeddedConnectionProvider</className></connectionProvider>`
    at the **top level** of `<jive>`, alongside `<autosetup>`, not inside it.
    See `tests/integration/docker/openfire.xml`.
12. **Openfire external components need three DB-backed properties.** Same
    enable-handshake as the REST API plugin: `xmpp.component.socket.active=true`,
    `xmpp.component.socket.port=5275`, `xmpp.component.defaultSecret=…`. Set
    in `conftest.py _enable_restapi`. The port doesn't bind until *after*
    these are set, so `_wait_for_tcp(host, 5275)` is required before
    connecting a component.
13. **Async fixture scope vs test loop scope.** pytest-asyncio in auto mode
    runs each test in its own event loop. A `@pytest_asyncio.fixture(scope="session")`
    that connects an XMPP/component over the network ends up with sockets
    registered in the *session* loop — the test's loop won't poll them, so
    every IQ times out. **Function-scope any async fixture that holds a live
    socket.** The seclabel_component fixture is function-scoped for this
    reason (slow but correct).
14. **slixmpp reconnect backoff is exponential and uncapped at the test level.**
    `reschedule_connection_attempt` does `_connect_loop_wait = min(300, n*2+1)`
    so a 7-second Openfire restart can land in a 15–31s wait window. The bot
    *will* reconnect, just not quickly. The resilience tests use
    `docker pause`/`unpause` instead — TCP preserved, no backoff involved.
    For production fast-reconnect, override `reschedule_connection_attempt`
    to cap the wait at a few seconds.
15. **FastMCP wire transport keeps subprocess alive by default.**
    `StdioTransport(..., keep_alive=True)` (the default) preserves the
    subprocess between `async with Client(...)` blocks. Pass `keep_alive=False`
    in test fixtures so each test gets a clean subprocess.
16. **Openfire Monitoring plugin 2.6.1 silently drops MUC MAM queries in
    this lab.** With every documented property set (`conversation.metadataArchiving`,
    `messageArchiving`, `roomArchiving`, `roomArchivingStanzas` all true, room
    created via REST with `logEnabled=true`), MAM IQs addressed to a room JID
    time out with no server response. Wire/disco shows `urn:xmpp:mam:2`
    supported, the IQ format is correct, but no `<result>`/`<fin>` ever comes
    back. Sometimes returns 404 "archive not found" instead. The
    `mam_query` tool is correctly wired — **the workaround is the ejabberd lab**
    (`python start-lab-ejabberd.py`), where MAM returns results immediately.
17. **`failed_auth` fires per-mechanism; use `failed_all_auth` instead.**
    slixmpp tries SASL mechanisms in order (SCRAM-SHA-1 → X-OAUTH2 → PLAIN
    typically) and fires `failed_auth` after each one. Treating the first
    failure as final tears down the connection mid-fallback — ejabberd's
    SCRAM-SHA-1 channel-binding rejects slixmpp's `c=y,,` flag, then PLAIN
    succeeds, but the disconnect cancels everything. Hook `failed_all_auth`
    (post-exhaustion) instead.
18. **SASL over plain TCP requires explicit opt-in.** slixmpp refuses PLAIN
    and SCRAM-SHA-1 over unencrypted streams by default. The ejabberd lab
    serves SASL on plain 5222 (no TLS configured), so the bot has to set
    `self.xmpp["feature_mechanisms"].unencrypted_plain = True` and
    `unencrypted_scram = True`. Gated on `XMPP_TLS_INSECURE=true` so it's
    lab-only behaviour, never production.
19. **ejabberd `auth_password_format: scram` + `ejabberdctl register`** —
    works. The CLI accepts plaintext passwords and the server stores the
    SCRAM-derived secrets internally. No special bootstrap needed beyond
    `ejabberdctl register <user> <domain> <password>`.

## Test markers

- `not docker and not integration` → fast unit tests, no network/Docker
- `docker` → boots Openfire container; superset of `wire`
- `wire` → drives `dist\xmpp-mcp.exe` over real MCP stdio JSON-RPC (subset of `docker`)
- `llm` → real Claude session via Anthropic SDK (needs `ANTHROPIC_API_KEY`)
- `integration` → opt-in connect/disco test against a user-provided server

## Test fixture conventions

- Session-scoped `openfire` fixture handles compose up/down, the REST-API
  enable handshake (sets *5* DB-backed system properties — REST API, wildcard
  exclusions, external components on 5275), and pre-creates three
  persistent rooms (`r1`/`r2`/`r3`).
- Function-scoped `mcp` fixture builds a fresh `create_server()` per test
  and yields an in-process `fastmcp.Client` connected as `bot`.
- Function-scoped `raw_alice` / `raw_bob` / `raw_carol` fixtures connect
  raw slixmpp clients for the "other side" of conversations.
- Function-scoped `seclabel_component` fixture spins up a XEP-0258 stub on
  `seclabel.xmpp.test` (function-scoped on purpose — see gotcha #13).
- `temp_node` fixture in `test_pubsub_e2e.py` creates a uniquely-named node
  and cleans it up around each test.

## Known limitations / non-goals

- **PEP-typed wrappers** (XEP-0163 `user_avatar` / `user_nick` / `user_tune`):
  not exposed. Plain pubsub at a user's bare JID works today (pass
  `service=<user-jid>`).
- **Pending-subscription approve/deny** (XEP-0060 §9.5): not exposed; only
  relevant for nodes with `access_model=authorize`.
- **XEP-0248 collection-node child/parent management**: collection nodes can
  be created (`pubsub#node_type=collection`), but explicit child-membership
  tools aren't exposed.
- **Publish preconditions / publish-options form** (XEP-0060 §7.1.5):
  not exposed.
- **XEP-0313 MAM (Message Archive Management)**: `mam_query` tool exists and
  the wiring is correct, but Openfire Monitoring 2.6.1 silently fails the
  requests (see gotcha #16). Live in-memory `search_messages` is the reliable
  path. Buffer size is `XMPP_INBOX_SIZE` (default 500, sliding window).
- **TLS hardening in the lab**: lab Openfire speaks STARTTLS with a
  self-signed cert and the test env sets `XMPP_TLS_INSECURE=true`. Production
  deployments should leave it `false`.
- **Single bot account per MCP server instance.** Multi-tenant is out of
  scope — run separate instances per identity if needed.
