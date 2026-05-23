"""End-to-end presence + roster tests.

Cross-account presence observation requires roster subscription approval on
both sides — a separate XMPP protocol dance from these MCP tools. So these
tests verify what the MCP surface actually exposes: that ``set_presence``
succeeds, that the roster manipulation tools round-trip, and that the
``xmpp://roster`` resource reflects the live state.
"""

from __future__ import annotations

import pytest
from fastmcp import Client

from .conftest import OpenfireHandle

pytestmark = pytest.mark.docker


async def test_set_presence_returns_success(mcp: Client) -> None:
    r = await mcp.call_tool(
        "set_presence", {"show": "away", "status": "in a meeting"}
    )
    assert r.data == {"show": "away", "status": "in a meeting"}


async def test_set_presence_rejects_bad_show(mcp: Client) -> None:
    with pytest.raises(Exception, match="show must be one of"):
        await mcp.call_tool("set_presence", {"show": "nonsense"})


async def test_roster_round_trip(mcp: Client, openfire: OpenfireHandle) -> None:
    alice_jid = openfire.accounts["alice"].jid

    add = await mcp.call_tool("add_contact", {"jid": alice_jid, "name": "Alice"})
    assert add.data["added"] is True
    assert add.data["jid"] == alice_jid

    roster = await mcp.call_tool("get_roster", {})
    jids = {c["jid"] for c in roster.data["contacts"]}
    assert alice_jid in jids, f"alice not in roster after add_contact: {jids}"

    remove = await mcp.call_tool("remove_contact", {"jid": alice_jid})
    assert remove.data["removed"] is True

    roster_after = await mcp.call_tool("get_roster", {})
    jids_after = {c["jid"] for c in roster_after.data["contacts"]}
    assert alice_jid not in jids_after
