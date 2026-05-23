"""End-to-end 1:1 messaging tests against the lab Openfire."""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client

from .conftest import OpenfireHandle
from .helpers.raw_client import RawXMPPClient

pytestmark = pytest.mark.docker


async def test_send_message_delivers_to_recipient(
    mcp: Client, raw_alice: RawXMPPClient, openfire: OpenfireHandle
) -> None:
    result = await mcp.call_tool(
        "send_message", {"to": openfire.accounts["alice"].jid, "body": "hello alice"}
    )
    assert result.data["sent"] is True

    msg = await raw_alice.wait_for_message(timeout=5.0)
    assert msg.body == "hello alice"
    assert msg.from_jid.startswith(openfire.accounts["bot"].jid)
    assert msg.type == "chat"


async def test_inbound_message_arrives_via_get_recent_messages(
    mcp: Client, raw_alice: RawXMPPClient, openfire: OpenfireHandle
) -> None:
    bot_jid = openfire.accounts["bot"].jid

    # Wait briefly so the bot has rebound its session before alice sends.
    await asyncio.sleep(0.5)
    raw_alice.send_chat(bot_jid, "ping from alice")

    # The MCP server buffers inbound messages; poll a couple of times in case
    # the stanza lands a moment after the call.
    messages: list[dict] = []
    for _ in range(10):
        result = await mcp.call_tool("get_recent_messages", {"limit": 10})
        messages = result.data["messages"]
        if messages:
            break
        await asyncio.sleep(0.3)

    assert messages, "no inbound message arrived within ~3s"
    assert any(
        m["body"] == "ping from alice"
        and m["from"].startswith(openfire.accounts["alice"].jid)
        for m in messages
    )


async def test_get_recent_messages_drains_buffer(
    mcp: Client, raw_alice: RawXMPPClient, openfire: OpenfireHandle
) -> None:
    bot_jid = openfire.accounts["bot"].jid
    await asyncio.sleep(0.5)
    raw_alice.send_chat(bot_jid, "drain me")
    # First read drains the buffer.
    for _ in range(10):
        r = await mcp.call_tool("get_recent_messages", {})
        if r.data["messages"]:
            break
        await asyncio.sleep(0.3)
    assert r.data["messages"], "message never arrived"
    # Second read should now return empty.
    r2 = await mcp.call_tool("get_recent_messages", {})
    assert r2.data["messages"] == []


async def test_get_recent_messages_filters_by_from_jid(
    mcp: Client,
    raw_alice: RawXMPPClient,
    raw_bob: RawXMPPClient,
    openfire: OpenfireHandle,
) -> None:
    bot_jid = openfire.accounts["bot"].jid
    alice_jid = openfire.accounts["alice"].jid
    await asyncio.sleep(0.5)
    raw_alice.send_chat(bot_jid, "from alice")
    raw_bob.send_chat(bot_jid, "from bob")

    # Wait for both to arrive (the bot now has a buffer of >=2 messages).
    deadline = asyncio.get_event_loop().time() + 5
    while asyncio.get_event_loop().time() < deadline:
        # Peek by asking for filtered alice. If alice present, both have arrived
        # (alice sent first; same TCP flush ordering).
        r = await mcp.call_tool(
            "get_recent_messages", {"from_jid": alice_jid, "limit": 10}
        )
        if r.data["messages"]:
            break
        await asyncio.sleep(0.3)
    assert r.data["messages"], "alice message never arrived"
    for m in r.data["messages"]:
        assert m["from"].startswith(alice_jid)

    # Bob's message must remain in the buffer.
    r_bob = await mcp.call_tool("get_recent_messages", {"limit": 10})
    assert any(m["body"] == "from bob" for m in r_bob.data["messages"]), (
        f"bob's message was drained by alice-filtered call; got {r_bob.data['messages']}"
    )
