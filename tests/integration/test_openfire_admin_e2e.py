"""End-to-end tests for the of_* Openfire REST admin tools.

Each test that mutates server state cleans up after itself so the suite can be
re-run repeatedly without manual reset.
"""

from __future__ import annotations

import httpx
import pytest
from fastmcp import Client

from .conftest import OpenfireHandle

pytestmark = pytest.mark.docker


def _all_usernames(payload: object) -> set[str]:
    """The REST API returns either ``{"users":[...]}`` or a bare list, depending
    on body shape; normalise to a set of usernames."""
    if isinstance(payload, dict):
        users = payload.get("users", payload.get("user", []))
    else:
        users = payload
    if isinstance(users, dict):
        users = [users]
    return {u["username"] for u in users}


async def test_of_list_users_includes_seeded_accounts(
    mcp: Client, openfire: OpenfireHandle
) -> None:
    result = await mcp.call_tool("of_list_users", {})
    names = _all_usernames(result.data)
    for expected in ("bot", "alice", "bob", "carol", "admin"):
        assert expected in names, f"missing {expected!r}, got {names}"


async def test_of_create_get_delete_user_round_trip(mcp: Client) -> None:
    username = "dave"
    try:
        create = await mcp.call_tool(
            "of_create_user",
            {
                "username": username,
                "password": "davepw",
                "name": "Dave Doe",
                "email": "dave@xmpp.test",
            },
        )
        assert create.data == {"created": True, "username": username}

        get = await mcp.call_tool("of_get_user", {"username": username})
        assert get.data["username"] == username
        assert get.data.get("name") == "Dave Doe"

        delete = await mcp.call_tool("of_delete_user", {"username": username})
        assert delete.data == {"deleted": True, "username": username}

        with pytest.raises(Exception, match="404"):
            await mcp.call_tool("of_get_user", {"username": username})
    finally:
        # Belt-and-braces cleanup if a mid-test failure left dave behind.
        try:
            await mcp.call_tool("of_delete_user", {"username": username})
        except Exception:
            pass


async def test_of_create_room_then_list_includes_it(
    mcp: Client, openfire: OpenfireHandle
) -> None:
    room_local = "admintest1"
    room_jid = f"{room_local}@{openfire.muc_service}"
    try:
        create = await mcp.call_tool(
            "of_create_room",
            {
                "room_name": room_local,
                "natural_name": "Admin Test 1",
                "description": "Created by test_of_create_room_then_list_includes_it",
            },
        )
        assert create.data == {"created": True, "room_name": room_local}

        listing = await mcp.call_tool("of_list_rooms", {})
        # listing.data is either {"chatRooms":[{...},...]} or a list.
        rooms = (
            listing.data.get("chatRooms", [])
            if isinstance(listing.data, dict)
            else listing.data
        )
        room_jids = {r.get("roomName") or r.get("roomName".lower()) for r in rooms}
        assert room_local in room_jids, (
            f"created room {room_local!r} not in listing: {room_jids}"
        )
    finally:
        # Tear the room down directly via the REST API (no MCP tool exposes
        # delete_room today).
        httpx.delete(
            f"{openfire.admin_url}/plugins/restapi/v1/chatrooms/{room_local}",
            auth=(openfire.admin_user, openfire.admin_password),
            headers={"Accept": "application/json"},
            timeout=10.0,
        )
