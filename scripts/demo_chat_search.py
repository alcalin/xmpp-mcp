"""End-to-end demo: bring the lab up, script a multi-room conversation, then
exercise ``search_messages`` as if Claude were answering "who's saying what".

Usage:

    python scripts/demo_chat_search.py            # uses the running container
                                                  #   (or brings one up)
    python scripts/demo_chat_search.py --rebuild  # rebuild image first
    python scripts/demo_chat_search.py --down     # tear down at exit

This is the same orchestration tests/integration/conftest.py does, packaged so
you can run it once and watch the output instead of through pytest.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

# Make the in-repo packages importable without installing.
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))
sys.path.insert(0, str(HERE))

import httpx  # noqa: E402

from tests.integration.conftest import (  # noqa: E402
    OpenfireHandle,
    _create_rooms,
    _enable_restapi,
    _wait_for_http,
    _wait_for_rest_api,
    _wait_for_tcp,
)
from tests.integration.helpers.chat_script import ChatScript, Line  # noqa: E402
from tests.integration.helpers.raw_client import RawXMPPClient  # noqa: E402

COMPOSE_DIR = HERE / "tests" / "integration" / "docker"


SCRIPT: list[Line] = [
    # --- #engineering -------------------------------------------------------
    Line("alice", "R_ENG",   "Ready to deploy v2.1 to staging in 10 minutes"),
    Line("bob",   "R_ENG",   "Hold on — I see a flaky test in the auth suite"),
    Line("carol", "R_ENG",   "Pushing the auth fix now, should unblock the deploy"),
    Line("alice", "R_ENG",   "Thanks Carol, deploy is going out at the top of the hour"),
    # --- #lunch -------------------------------------------------------------
    Line("alice", "R_LUNCH", "Anyone want sushi today?"),
    Line("bob",   "R_LUNCH", "I'm thinking pizza, the place on 3rd is fast"),
    Line("alice", "R_LUNCH", "Pizza works for me, see you at noon"),
    # --- #help --------------------------------------------------------------
    Line("carol", "R_HELP",  "Customer is hitting a 500 on /api/orders"),
    Line("bob",   "R_HELP",  "Stack trace looks like the same auth bug we just fixed"),
    Line("carol", "R_HELP",  "Once the deploy lands they should be unblocked too"),
]


def compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", *args], cwd=COMPOSE_DIR, check=True, capture_output=True
    )


def wait_for_health(timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = subprocess.run(
            ["docker", "inspect", "xmpp-mcp-test-openfire-1",
             "--format", "{{.State.Health.Status}}"],
            capture_output=True, text=True,
        )
        if r.stdout.strip() == "healthy":
            return
        time.sleep(1.0)
    raise SystemExit("openfire container never went healthy")


@asynccontextmanager
async def populate_and_search(handle: OpenfireHandle):
    """Bring up bot + three raw clients, script the conversation, yield the MCP client."""
    # Configure environment for create_server() lifespan.
    bot = handle.accounts["bot"]
    os.environ.update({
        "XMPP_JID": bot.jid, "XMPP_PASSWORD": bot.password,
        "XMPP_HOST": handle.host, "XMPP_PORT": str(handle.c2s_port),
        "XMPP_TLS_INSECURE": "true",
        "OPENFIRE_BASE_URL": handle.admin_url,
        "OPENFIRE_ADMIN_USER": handle.admin_user,
        "OPENFIRE_ADMIN_PASSWORD": handle.admin_password,
    })

    from fastmcp import Client  # noqa: E402

    from xmpp_mcp.server import create_server  # noqa: E402

    server = create_server()
    raws: dict[str, RawXMPPClient] = {}
    for name in ("alice", "bob", "carol"):
        acct = handle.accounts[name]
        raws[name] = RawXMPPClient(
            acct.jid, acct.password, handle.host, handle.c2s_port
        )
    try:
        for c in raws.values():
            await c.connect()
        async with Client(server) as mcp:
            # Bot joins everything first so the buffer captures every line.
            rooms = {
                "engineering": handle.room_jid("r1"),
                "lunch":       handle.room_jid("r2"),
                "help":        handle.room_jid("r3"),
            }
            for r in rooms.values():
                await mcp.call_tool("join_room", {"room_jid": r})

            script = ChatScript(raws, muc_service=handle.muc_service)
            await script.join_all(rooms["engineering"], ["alice", "bob", "carol"])
            await script.join_all(rooms["lunch"],       ["alice", "bob"])
            await script.join_all(rooms["help"],        ["bob", "carol"])
            await asyncio.sleep(0.3)

            resolved = [
                Line(line.speaker, {
                    "R_ENG":   rooms["engineering"],
                    "R_LUNCH": rooms["lunch"],
                    "R_HELP":  rooms["help"],
                }.get(line.target, line.target), line.body)
                for line in SCRIPT
            ]
            await script.run(resolved)
            await asyncio.sleep(0.8)

            yield mcp, rooms
    finally:
        for c in raws.values():
            await c.disconnect()


def fmt_msg(m: dict) -> str:
    who = m.get("nick") or m["from"].split("/", 1)[0].split("@", 1)[0]
    where = m.get("room") or "(direct)"
    short_room = where.split("@", 1)[0] if where != "(direct)" else where
    return f"  [{short_room}] {who}: {m['body']}"


async def ask(mcp, label: str, query: dict) -> list[dict]:
    print(f"\n>>> {label}")
    print(f"    call: search_messages({query})")
    r = await mcp.call_tool("search_messages", query)
    msgs = r.data["messages"]
    print(f"    {len(msgs)} match(es):")
    for m in reversed(msgs):  # oldest-first for readability
        print(fmt_msg(m))
    return msgs


async def amain(handle: OpenfireHandle) -> None:
    async with populate_and_search(handle) as (mcp, rooms):
        await ask(mcp, "Who's talking about auth?", {"query": "auth"})
        await ask(
            mcp,
            "What was said in #engineering?",
            {"room": rooms["engineering"], "limit": 20},
        )
        await ask(mcp, "What did Alice say?", {"participant": "alice"})
        await ask(
            mcp,
            "Did anyone mention the deploy in #engineering?",
            {"query": "deploy", "room": rooms["engineering"]},
        )
        await ask(mcp, "What was the lunch plan?", {"room": rooms["lunch"]})
        await ask(
            mcp,
            "Anyone tracking the customer 500?",
            {"query": "500"},
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rebuild", action="store_true", help="rebuild the openfire image")
    p.add_argument("--down", action="store_true", help="docker compose down at exit")
    args = p.parse_args()

    print("=== Bringing up the lab ===")
    if args.rebuild:
        compose("build")
    compose("up", "-d")
    wait_for_health()
    handle = OpenfireHandle()
    _wait_for_tcp(handle.host, handle.c2s_port, timeout=60)
    _wait_for_http(f"{handle.admin_url}/login.jsp", timeout=60)
    _enable_restapi(handle)
    _wait_for_rest_api(handle, timeout=30)
    try:
        # _create_rooms is idempotent (409 = already exists is fine).
        _create_rooms(handle)
    except RuntimeError as exc:
        if "409" not in str(exc):
            raise

    print("\n=== Scripting the conversation across 3 rooms ===")
    try:
        asyncio.run(amain(handle))
    finally:
        if args.down:
            print("\n=== Tearing down ===")
            compose("down", "-v")


if __name__ == "__main__":
    main()
