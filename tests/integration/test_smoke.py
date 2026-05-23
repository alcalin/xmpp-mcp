"""Smoke test: bot connects to the lab Openfire and discovery works.

Proves the whole fixture stack (docker compose, REST-API enable, FastMCP
in-process client, XMPP session) wires together before the heavier
behavioural tests in this directory run.
"""

from __future__ import annotations

import pytest
from fastmcp import Client

from .conftest import OpenfireHandle

pytestmark = pytest.mark.docker


async def test_bot_connects_and_lists_tools(mcp: Client, openfire: OpenfireHandle) -> None:
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert {"send_message", "join_room", "discover_features", "of_list_users"} <= names


async def test_discover_features_against_server(mcp: Client, openfire: OpenfireHandle) -> None:
    result = await mcp.call_tool("discover_features", {})
    data = result.data
    assert data["jid"] == openfire.domain
    # Openfire always advertises disco itself.
    assert "http://jabber.org/protocol/disco#info" in data["features"]
    # The vanilla Openfire build does not implement XEP-0258.
    assert data["supports_security_labels"] is False
