"""End-to-end MUC tests: bot joins multiple pre-created rooms, sends messages
to each, and a second client verifies cross-room isolation."""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client

from .conftest import BOT_NICK, OpenfireHandle
from .helpers.raw_client import RawXMPPClient

pytestmark = pytest.mark.docker


async def test_join_three_rooms_and_list_occupants(
    mcp: Client, openfire: OpenfireHandle
) -> None:
    rooms = [openfire.room_jid(n) for n in openfire.room_names]
    joined_nicks = set()
    for r in rooms:
        result = await mcp.call_tool("join_room", {"room_jid": r})
        assert result.data["room"] == r
        assert result.data["nick"] == BOT_NICK  # pinned by the mcp fixture
        joined_nicks.add(result.data["nick"])
    # join must report one consistent nick across rooms — guards against a
    # regression where the nick is derived per-room rather than from config.
    assert joined_nicks == {BOT_NICK}

    # Bot must appear as an occupant of every room under that same nick.
    for r in rooms:
        result = await mcp.call_tool("list_room_occupants", {"room_jid": r})
        nicks = {o["nick"] for o in result.data["occupants"]}
        assert BOT_NICK in nicks, f"bot missing from occupants of {r}: {nicks}"


async def test_send_room_message_isolated_to_joined_room(
    mcp: Client, raw_carol: RawXMPPClient, openfire: OpenfireHandle
) -> None:
    r1, r2, r3 = (openfire.room_jid(n) for n in openfire.room_names)

    # Bot joins all three; carol joins only r2.
    for r in (r1, r2, r3):
        await mcp.call_tool("join_room", {"room_jid": r})
    await raw_carol.join_muc(r2, "carol")
    await asyncio.sleep(0.5)  # let occupant presences settle

    # Send distinct messages to each room.
    await mcp.call_tool("send_room_message", {"room_jid": r1, "body": "for r1 only"})
    await mcp.call_tool("send_room_message", {"room_jid": r2, "body": "for r2 only"})
    await mcp.call_tool("send_room_message", {"room_jid": r3, "body": "for r3 only"})

    # Carol must see only r2's message — collect for ~2s and assert no leakage.
    received: list = []
    deadline = asyncio.get_event_loop().time() + 2.0
    while asyncio.get_event_loop().time() < deadline:
        try:
            received.append(await raw_carol.wait_for_message(timeout=0.5))
        except TimeoutError:
            break
    bodies = [m.body for m in received]
    assert "for r2 only" in bodies, f"carol did not receive r2 message; got {bodies}"
    assert "for r1 only" not in bodies, "carol leaked an r1 message"
    assert "for r3 only" not in bodies, "carol leaked an r3 message"


async def test_send_room_message_after_leave_fails(
    mcp: Client, openfire: OpenfireHandle
) -> None:
    r2 = openfire.room_jid(openfire.room_names[1])
    await mcp.call_tool("join_room", {"room_jid": r2})
    leave_result = await mcp.call_tool("leave_room", {"room_jid": r2})
    assert leave_result.data["left"] is True

    with pytest.raises(Exception, match="Not joined to room"):
        await mcp.call_tool(
            "send_room_message", {"room_jid": r2, "body": "should fail"}
        )


async def test_server_info_resource_reflects_joined_rooms(
    mcp: Client, openfire: OpenfireHandle
) -> None:
    r1, r2, r3 = (openfire.room_jid(n) for n in openfire.room_names)
    await mcp.call_tool("join_room", {"room_jid": r1})
    await mcp.call_tool("join_room", {"room_jid": r3})

    resource = await mcp.read_resource("xmpp://server-info")
    # FastMCP returns a list of TextResourceContents; .data carries the dict.
    payload = resource[0].text if hasattr(resource[0], "text") else resource[0]
    import json

    info = json.loads(payload) if isinstance(payload, str) else payload
    assert set(info["joined_rooms"]) == {r1, r3}
