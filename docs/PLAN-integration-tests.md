# Plan: Full integration tests against Dockerised Openfire

## Context

Today's tests are split into 19 fast unit tests (config, XEP-0258 parsing,
mocked Openfire REST) and one *opt-in* `XMPPClient.start()/stop()` integration
test that requires the caller to provide a live server in environment
variables. That leaves the bulk of the surface area — the MCP tools themselves
end-to-end, MUC across multiple rooms, the Openfire `of_*` admin tools,
discovery — verified only by hand.

This plan adds a self-contained integration test suite that brings up a real
Openfire server in Docker, drives the MCP server in-process through a FastMCP
client, uses a second raw XMPP client as "the other side" of every
conversation, and asserts on real wire behaviour. The deliverable answers:
*"does xmpp-mcp work against a real server, end to end, without anyone manually
clicking through the Openfire setup wizard?"*

## Goals & non-goals

In scope:

- A reproducible Openfire instance with the REST API plugin enabled, three
  pre-created users (`alice`, `bob`, `carol`) plus a `bot` account for the MCP
  server, and three pre-created persistent MUC rooms.
- pytest fixtures that boot the container, wait for it to be healthy, expose
  connection info, and tear it down.
- Tests covering: 1:1 messaging both directions, MUC across multiple rooms
  with cross-room isolation, presence/roster, service discovery, and every
  `of_*` admin tool.
- A pytest marker (`@pytest.mark.docker`) so the suite is **opt-in** and is
  not run by `pytest -m "not integration"`.

Out of scope:

- M-Link / XEP-0258 catalog over the wire — M-Link is commercial and not
  Dockerable. Security labels remain covered by unit tests (`parse_catalog`,
  `build_label`) and the `supports_security_labels` flag returned by
  `discover_features`. A mock-server track is sketched at the bottom for
  later.
- TLS/STARTTLS hardening — the lab container speaks plaintext on 5222 to keep
  the test surface small. Cert handling is exercised today by the
  `XMPP_TLS_INSECURE` config switch.

## Tech decisions

| Decision | Choice | Why |
|---|---|---|
| Container orchestration | `docker compose` driven by a session-scoped pytest fixture (`subprocess.run`) | One source of truth (`docker-compose.yml`) that humans can also run by hand; no extra Python dependency on `testcontainers` |
| MCP driver | `fastmcp.Client` in-process against `create_server()` | No subprocess, full type-checked tool calls, fast iteration |
| Second-side client | a raw `slixmpp.ClientXMPP` per test, awaited via short-lived helper class | Lets us prove messages actually traversed the server |
| Openfire auto-setup | `OPENFIRE_AUTO_SETUP_*` env vars on the official image | No setup wizard, no baked SQL, no custom Dockerfile if we can avoid one |
| REST API plugin | downloaded into `/opt/openfire/plugins/` via a tiny Dockerfile that extends the base image | The plugin is required for `of_*` tests; the base image does not ship it |
| Test marker | `@pytest.mark.docker` + `-m "not docker"` is the default exclusion | Devs without Docker keep a clean `pytest` run |

⚠ **One thing to verify in implementation:** the exact `OPENFIRE_AUTO_SETUP_*`
variable names and how the image expresses pre-created users. The official
image supports auto-setup but the variable spelling has shifted between
versions; the implementation step needs to confirm against the image we pin
(see open questions below).

## Layout

```
C:\work\xmpp\
  tests/
    integration/
      __init__.py
      conftest.py                      # session/function fixtures
      docker/
        Dockerfile.openfire            # base image + REST API plugin jar
        docker-compose.yml             # service + healthcheck + AUTO_SETUP env
        .env                           # version pins, admin pw, secret key
      helpers/
        __init__.py
        raw_client.py                  # tiny slixmpp helper: connect, send,
                                       #   wait_for_message(), join, leave
        mcp_runner.py                  # FastMCP in-process Client wiring
      test_messaging_e2e.py
      test_muc_e2e.py
      test_presence_e2e.py
      test_discovery_e2e.py
      test_openfire_admin_e2e.py
```

## Test inventory

`test_messaging_e2e.py` — direct 1:1 over the real server.

1. bot → alice: tool call `send_message`; raw alice client receives a stanza
   whose `from` is `bot@xmpp.test/...`, `body` matches.
2. alice → bot: raw client sends; `get_recent_messages` returns it; buffer is
   drained on read.
3. `get_recent_messages(from_jid="alice@xmpp.test")` filters: a message from
   bob stays in the buffer.

`test_muc_e2e.py` — three rooms, two MCP-side identities.

1. `join_room` for `r1`, `r2`, `r3` (pre-created persistent rooms).
2. `list_room_occupants(r2)` reports bot's nick.
3. `send_room_message` to each room; raw carol joined to `r2` only sees the
   `r2` message — no leakage across rooms.
4. `leave_room(r2)` succeeds; a subsequent `send_room_message(r2, ...)` fails
   with the "not joined" `ToolError`.
5. `xmpp://server-info` resource lists `r1` and `r3` under `joined_rooms`.

`test_presence_e2e.py` — presence and roster round-trip.

1. raw alice subscribes to bot; bot's roster (`get_roster` tool) eventually
   shows alice with `subscription == "both"` (allow a short polling loop).
2. `set_presence("away", "in a meeting")` → alice's raw client sees a presence
   stanza with `<show>away</show>` and the status text.
3. `remove_contact` removes alice from bot's roster.

`test_discovery_e2e.py` — XEP-0030 against a real server.

1. `discover_features()` against the server lists `http://jabber.org/protocol/disco#info`
   and includes the MUC service in `items`.
2. `supports_security_labels` is **false** on vanilla Openfire — confirms the
   negative path.
3. `list_security_labels(to=...)` returns a clean `ToolError` (Openfire does
   not implement the catalog), not a stacktrace.

`test_openfire_admin_e2e.py` — the `of_*` tools end-to-end.

1. `of_create_user("dave", "pw", name="Dave")` then `of_get_user("dave")`
   round-trips the name/email.
2. `of_list_users()` includes `dave`.
3. `of_delete_user("dave")` removes him; `of_get_user("dave")` raises
   `ToolError` containing `404`.
4. `of_create_room("test1", "Test 1", "desc")` then `of_list_rooms()` includes
   `test1@conference.xmpp.test`.
5. `of_add_group_member("alice", "Admins")` after `of_*`-creating an Admins
   group.

## Fixture design

`tests/integration/conftest.py`:

```python
# session-scoped: bring Openfire up once, tear down once
@pytest.fixture(scope="session")
def openfire():
    compose = Path(__file__).parent / "docker" / "docker-compose.yml"
    subprocess.run(["docker", "compose", "-f", compose, "up", "-d"], check=True)
    try:
        _wait_for(host="127.0.0.1", port=5222, timeout=90)
        _wait_for_http(f"http://127.0.0.1:9090/plugins/restapi/v1/system/properties",
                       headers={"Authorization": REST_SECRET}, timeout=60)
        yield OpenfireHandle(host="127.0.0.1", c2s=5222, rest=9090, secret=REST_SECRET)
    finally:
        subprocess.run(["docker", "compose", "-f", compose, "down", "-v"], check=False)

# function-scoped: a configured FastMCP in-process client connected as bot
@pytest.fixture
async def mcp(openfire, monkeypatch):
    monkeypatch.setenv("XMPP_JID", f"bot@{openfire.domain}")
    monkeypatch.setenv("XMPP_PASSWORD", "botpw")
    monkeypatch.setenv("XMPP_HOST", openfire.host)
    monkeypatch.setenv("XMPP_PORT", str(openfire.c2s))
    monkeypatch.setenv("OPENFIRE_BASE_URL", f"http://{openfire.host}:{openfire.rest}")
    monkeypatch.setenv("OPENFIRE_SECRET_KEY", openfire.secret)
    server = create_server()
    async with Client(server) as client:
        yield client
```

A `raw_client` fixture wraps `slixmpp.ClientXMPP` in a tiny helper class that
exposes `await wait_for_message(timeout=5.0)` (an `asyncio.Queue` fed by the
`message` handler) so tests can `assert (await alice.wait_for_message()).body == "..."`
without flake-prone sleeps.

## Container setup

`tests/integration/docker/Dockerfile.openfire` — extends the official image
just enough to drop the REST API plugin in:

```Dockerfile
FROM igniterealtime/openfire:4.8.1
USER root
RUN curl -fsSL -o /opt/openfire/plugins/restAPI.jar \
    https://www.igniterealtime.org/projects/openfire/plugins/1.10.2/restAPI.jar
USER openfire
```

`docker-compose.yml` — single service, healthcheck, AUTO_SETUP env (exact
variable names to be verified during implementation against the pinned image
tag — see open question 1):

```yaml
services:
  openfire:
    build: .
    ports: ["5222:5222", "9090:9090"]
    environment:
      OPENFIRE_AUTO_SETUP: "true"
      OPENFIRE_AUTO_SETUP_XMPP_DOMAIN: xmpp.test
      OPENFIRE_AUTO_SETUP_ADMIN_PASSWORD: adminpw
      OPENFIRE_AUTO_SETUP_USERS_1_USERNAME: bot
      OPENFIRE_AUTO_SETUP_USERS_1_PASSWORD: botpw
      OPENFIRE_AUTO_SETUP_USERS_2_USERNAME: alice
      OPENFIRE_AUTO_SETUP_USERS_2_PASSWORD: alicepw
      OPENFIRE_AUTO_SETUP_USERS_3_USERNAME: bob
      OPENFIRE_AUTO_SETUP_USERS_3_PASSWORD: bobpw
      OPENFIRE_AUTO_SETUP_USERS_4_USERNAME: carol
      OPENFIRE_AUTO_SETUP_USERS_4_PASSWORD: carolpw
      OPENFIRE_AUTO_SETUP_PROPERTY_plugin_restapi_secret: rest-secret-for-tests
      OPENFIRE_AUTO_SETUP_PROPERTY_plugin_restapi_enabled: "true"
    healthcheck:
      test: ["CMD", "curl", "-fsS", "-H", "Authorization: rest-secret-for-tests",
             "http://localhost:9090/plugins/restapi/v1/system/properties"]
      interval: 3s
      timeout: 3s
      retries: 30
```

Three persistent rooms (`r1`, `r2`, `r3` on `conference.xmpp.test`) get
created in the same session fixture *after* the container is healthy, via the
REST API — both because that fits cleanly inside the fixture and because it
exercises the `chatrooms` endpoint on the way in.

## Sequencing

Each step is independently mergeable / runnable.

1. **Container** — `Dockerfile.openfire`, `docker-compose.yml`, a single
   throwaway script that `docker compose up`s it and curls
   `/restapi/v1/users` to confirm auto-setup actually pre-created the users.
   *Stop here if the AUTO_SETUP env names are wrong — fix before going on.*
2. **Fixtures** — `conftest.py` with `openfire` (session) and `mcp`
   (function); `raw_client.py` helper. Smoke test: a stub
   `test_smoke.py::test_bot_connects` that uses the `mcp` fixture and calls
   `discover_features`. Once this passes, the wiring is proven.
3. **Messaging tests** (`test_messaging_e2e.py`).
4. **MUC tests** (`test_muc_e2e.py`) — also adds the room-precreation step
   to the session fixture.
5. **Presence + discovery** (`test_presence_e2e.py`, `test_discovery_e2e.py`).
6. **Openfire admin** (`test_openfire_admin_e2e.py`).
7. **CI** — a GitHub Actions workflow (Linux runner, `docker compose` is
   pre-installed) that runs `pytest -m docker`. Time budget: ≤ 3 min total.

## Verification

Local:

```powershell
docker compose -f tests/integration/docker/docker-compose.yml up -d
.\.venv\Scripts\python.exe -m pytest -m docker -v
docker compose -f tests/integration/docker/docker-compose.yml down -v
```

Should run end-to-end without manual setup, no Openfire wizard, no manual
user creation. Re-running the suite back-to-back must be idempotent (the
admin tests clean up their own `dave` user and `test1` room).

CI: a green run of the integration suite on a clean GitHub Actions runner.

## Open questions before implementation

1. **AUTO_SETUP variable spelling.** The plan uses
   `OPENFIRE_AUTO_SETUP_USERS_N_USERNAME` and
   `OPENFIRE_AUTO_SETUP_PROPERTY_plugin_restapi_secret`. The exact names
   shipped by the pinned `igniterealtime/openfire:4.8.1` image need to be
   confirmed by a five-minute experiment before step 1 is signed off — if
   they differ, the fallback is a baked `openfire.xml` + `embedded-db/*.sql`
   in the Dockerfile, which works but is uglier.
2. **CI scope.** Add a GitHub Actions workflow, or local-only for now? The
   plan assumes "add it" in step 7; happy to drop if there is no CI yet.
3. **Pin Openfire version.** `4.8.1` is the current LTS-ish; pin that and
   bump deliberately. If you have a deployed Openfire version, pin to match
   it instead.

## Future: M-Link / XEP-0258 integration coverage

Out of scope today, but the design that would close the loop without
requiring M-Link:

- A small Python "stub XMPP service" (built on slixmpp's `ComponentXMPP`)
  that joins the lab Openfire as a component on `seclabel.xmpp.test` and
  responds to `iq[catalog]` with a hand-written XEP-0258 catalog.
- One additional test (`test_security_labels_e2e.py`) that calls
  `list_security_labels(to="seclabel.xmpp.test")`, asserts on the parsed
  catalog, then `send_message` with a returned selector and confirms the
  outgoing stanza carried the `<securitylabel/>` element verbatim.

That's a useful follow-on but adds a second service to the compose file and
~100 lines of fixture, so it lives in its own ticket.
