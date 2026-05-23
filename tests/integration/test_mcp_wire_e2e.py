"""MCP wire-protocol tests — drive the shipped ``dist/xmpp-mcp.exe`` over
real stdio JSON-RPC. Proves the PyInstaller bundle works end-to-end with any
MCP client (Claude Desktop, Claude Code, etc.) rather than just the
in-process FastMCP harness used elsewhere in this suite.

Build the binary first:

    .\\.venv\\Scripts\\pyinstaller.exe xmpp-mcp.spec   # → dist\\xmpp-mcp.exe

then run:

    pytest -m wire

If the exe is missing — or older than the source under ``src/`` — the tests
skip with a clear message instead of failing, so a stale bundle never silently
passes for last-build's code. Same skip-don't-fail workflow as the rest of the
docker suite.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from .conftest import BOT_NICK, OpenfireHandle
from .helpers.raw_client import RawXMPPClient

pytestmark = [pytest.mark.docker, pytest.mark.wire]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EXE_PATH = REPO_ROOT / "dist" / "xmpp-mcp.exe"
SRC_DIR = REPO_ROOT / "src" / "xmpp_mcp"


def _stale_exe_reason() -> str | None:
    """Return a skip reason if the exe is missing or older than any source file.

    Wire tests against a stale binary give false confidence — they'd pass
    against last-build's code. Skipping (not failing) keeps parity with the
    "build it first" workflow while refusing to vouch for an outdated bundle.
    """
    if not EXE_PATH.exists():
        return f"build the exe first: pyinstaller xmpp-mcp.spec (expected at {EXE_PATH})"
    exe_mtime = EXE_PATH.stat().st_mtime
    newest_src = max(
        (p.stat().st_mtime for p in SRC_DIR.rglob("*.py")), default=0.0
    )
    if newest_src > exe_mtime:
        return (
            "dist/xmpp-mcp.exe is older than src/ — rebuild it: "
            "pyinstaller xmpp-mcp.spec (else wire tests vouch for stale code)"
        )
    return None


def _env_for(openfire: OpenfireHandle) -> dict[str, str]:
    bot = openfire.accounts["bot"]
    # Subprocess on Windows needs SYSTEMROOT / PATH or it can't load DLLs.
    env: dict[str, str] = {
        "XMPP_JID": bot.jid,
        "XMPP_PASSWORD": bot.password,
        "XMPP_HOST": openfire.host,
        "XMPP_PORT": str(openfire.c2s_port),
        "XMPP_TLS_INSECURE": "true",
        "XMPP_NICK": BOT_NICK,  # hermetic: don't inherit a developer's .env
        "OPENFIRE_BASE_URL": openfire.admin_url,
        "OPENFIRE_ADMIN_USER": openfire.admin_user,
        "OPENFIRE_ADMIN_PASSWORD": openfire.admin_password,
    }
    for passthrough in ("SYSTEMROOT", "PATH", "TEMP", "TMP", "USERPROFILE"):
        if (value := os.environ.get(passthrough)):
            env[passthrough] = value
    return env


@pytest_asyncio.fixture
async def wire_mcp(openfire: OpenfireHandle) -> AsyncIterator[Client]:
    """A fastmcp Client speaking real stdio JSON-RPC to dist/xmpp-mcp.exe."""
    if (reason := _stale_exe_reason()) is not None:
        pytest.skip(reason)
    transport = StdioTransport(
        command=str(EXE_PATH),
        args=[],
        env=_env_for(openfire),
        keep_alive=False,
    )
    async with Client(transport) as client:
        yield client


async def test_wire_list_tools_includes_full_surface(wire_mcp: Client) -> None:
    """The bundled binary must register every tool the in-process server does."""
    tools = await wire_mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        # spot-check coverage across categories
        "send_message", "get_recent_messages", "search_messages",
        "join_room", "send_room_message", "list_room_occupants",
        "set_presence", "get_roster", "add_contact",
        "discover_features", "list_security_labels",
        "pubsub_create_node", "pubsub_publish_form_template",
        "pubsub_submit_form", "pubsub_read_forms",
        "pubsub_get_recent_events", "pubsub_publish_raw",
        "of_list_users", "of_create_user",
    }
    missing = expected - names
    assert not missing, f"tools missing over the wire: {sorted(missing)}"


async def test_wire_send_message_delivers_to_alice(
    wire_mcp: Client, raw_alice: RawXMPPClient, openfire: OpenfireHandle
) -> None:
    """A round-trip over the real MCP transport: bot → alice."""
    alice_jid = openfire.accounts["alice"].jid
    r = await wire_mcp.call_tool(
        "send_message", {"to": alice_jid, "body": "hello over the wire"}
    )
    assert r.data["sent"] is True
    msg = await raw_alice.wait_for_message(timeout=5.0)
    assert msg.body == "hello over the wire"


async def test_wire_pubsub_form_round_trip(
    wire_mcp: Client, openfire: OpenfireHandle
) -> None:
    """Exercise a multi-step pubsub workflow through the subprocess."""
    service = f"pubsub.{openfire.domain}"
    node = f"wire-{uuid.uuid4().hex[:8]}"
    try:
        await wire_mcp.call_tool(
            "pubsub_create_node",
            {
                "service": service, "node": node,
                "config_values": {
                    "pubsub#persist_items": True,
                    "pubsub#max_items": "10",
                },
            },
        )
        await wire_mcp.call_tool(
            "pubsub_publish_form_template",
            {
                "service": service, "node": node,
                "fields": [
                    {"var": "q", "type": "text-single", "label": "Q", "required": True},
                ],
                "title": "Wire survey",
                "item_id": "tpl",
            },
        )
        read = await wire_mcp.call_tool(
            "pubsub_read_forms", {"service": service, "node": node}
        )
        assert read.data["count"] == 1
        item = read.data["items"][0]
        assert item["id"] == "tpl"
        assert item["form"]["title"] == "Wire survey"
        assert item["form"]["fields"][0]["var"] == "q"
    finally:
        try:
            await wire_mcp.call_tool(
                "pubsub_delete_node", {"service": service, "node": node}
            )
        except Exception:  # noqa: BLE001
            pass


async def test_wire_list_resources_exposes_both(wire_mcp: Client) -> None:
    """Resources also need to round-trip over the wire."""
    resources = await wire_mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert "xmpp://roster" in uris
    assert "xmpp://server-info" in uris
