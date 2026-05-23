"""Scripted multi-room chat scenario + search_messages assertions.

This test models a small team's chat across three rooms:

* ``r1`` (#engineering): alice/bob/carol discuss a deploy and an auth-suite bug
* ``r2`` (#lunch):       alice/bob banter about food
* ``r3`` (#help):        carol and bob trace a customer 500 back to the same
                          auth bug from #engineering

The bot is joined to all three rooms *before* the chat starts so its in-memory
buffer captures every line. We then drive ``search_messages`` with the kinds
of questions a user would actually ask Claude ("who's talking about auth?",
"what was said in #lunch?", "did Alice mention the deploy?") and assert the
right messages come back.

To eyeball this from a real Claude Code session: bring the lab up by hand
(``docker compose -f tests/integration/docker/docker-compose.yml up -d`` plus
the REST-API-enable handshake the fixture runs), launch the conversation
helper (or just paste the script into raw clients), then point a Claude Code
MCP config at the local server with ``XMPP_*`` set to ``bot@xmpp.test``.
"""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client

from .conftest import OpenfireHandle
from .helpers.chat_script import ChatScript, Line
from .helpers.raw_client import RawXMPPClient

pytestmark = pytest.mark.docker


_SCRIPT: list[Line] = [
    # --- #engineering -------------------------------------------------------
    Line("alice", "R_ENG", "Ready to deploy v2.1 to staging in 10 minutes"),
    Line("bob",   "R_ENG", "Hold on — I see a flaky test in the auth suite"),
    Line("carol", "R_ENG", "Pushing the auth fix now, should unblock the deploy"),
    Line("alice", "R_ENG", "Thanks Carol, deploy is going out at the top of the hour"),
    # --- #lunch -------------------------------------------------------------
    Line("alice", "R_LUNCH", "Anyone want sushi today?"),
    Line("bob",   "R_LUNCH", "I'm thinking pizza, the place on 3rd is fast"),
    Line("alice", "R_LUNCH", "Pizza works for me, see you at noon"),
    # --- #help --------------------------------------------------------------
    Line("carol", "R_HELP", "Customer is hitting a 500 on /api/orders"),
    Line("bob",   "R_HELP", "Stack trace looks like the same auth bug we just fixed"),
    Line("carol", "R_HELP", "Once the deploy lands they should be unblocked too"),
]


def _resolve(line: Line, openfire: OpenfireHandle) -> Line:
    """Replace the symbolic ``R_*`` targets in :data:`_SCRIPT` with real JIDs."""
    mapping = {
        "R_ENG":   openfire.room_jid("r1"),
        "R_LUNCH": openfire.room_jid("r2"),
        "R_HELP":  openfire.room_jid("r3"),
    }
    return Line(line.speaker, mapping.get(line.target, line.target), line.body)


@pytest.fixture
async def scripted_chat(
    mcp: Client,
    raw_alice: RawXMPPClient,
    raw_bob: RawXMPPClient,
    raw_carol: RawXMPPClient,
    openfire: OpenfireHandle,
) -> dict[str, str]:
    """Have bot + the three speakers join all rooms, then run the script.

    Returns a mapping of room labels (``"engineering"``, ``"lunch"``, ``"help"``)
    to their full JIDs so tests can refer to them by name.
    """
    rooms = {
        "engineering": openfire.room_jid("r1"),
        "lunch": openfire.room_jid("r2"),
        "help": openfire.room_jid("r3"),
    }

    # Bot joins everywhere first so its buffer captures every line.
    for r in rooms.values():
        await mcp.call_tool("join_room", {"room_jid": r})

    script = ChatScript(
        {"alice": raw_alice, "bob": raw_bob, "carol": raw_carol},
        muc_service=openfire.muc_service,
    )
    # Each speaker only joins the rooms they speak in — keeps the participant
    # list realistic and matches what a Claude session would see.
    await script.join_all(rooms["engineering"], ["alice", "bob", "carol"])
    await script.join_all(rooms["lunch"], ["alice", "bob"])
    await script.join_all(rooms["help"], ["bob", "carol"])
    await asyncio.sleep(0.3)  # let join presences propagate

    await script.run([_resolve(line, openfire) for line in _SCRIPT])
    # Give the last few stanzas a beat to reach the bot's inbox before assertions.
    await asyncio.sleep(0.8)
    return rooms


async def test_who_is_talking_about_auth(
    mcp: Client, scripted_chat: dict[str, str]
) -> None:
    """The headline 'ask the AI who's saying what' query."""
    result = await mcp.call_tool("search_messages", {"query": "auth"})
    msgs = result.data["messages"]
    assert msgs, "no matches for 'auth' — buffer empty?"

    # Bob and Carol both mention 'auth' (bob in #engineering and #help; carol
    # in #engineering). Alice does not.
    speakers = {m["nick"] for m in msgs}
    assert speakers == {"bob", "carol"}, f"unexpected speakers: {speakers}"

    rooms = {m["room"] for m in msgs}
    assert rooms == {scripted_chat["engineering"], scripted_chat["help"]}


async def test_full_thread_in_engineering(
    mcp: Client, scripted_chat: dict[str, str]
) -> None:
    result = await mcp.call_tool(
        "search_messages", {"room": scripted_chat["engineering"], "limit": 20}
    )
    msgs = result.data["messages"]
    assert len(msgs) == 4, f"expected 4 lines in #engineering, got {len(msgs)}"
    assert {m["nick"] for m in msgs} == {"alice", "bob", "carol"}


async def test_what_did_alice_say(
    mcp: Client, scripted_chat: dict[str, str]
) -> None:
    result = await mcp.call_tool("search_messages", {"participant": "alice"})
    msgs = result.data["messages"]
    # Alice spoke 4 times — twice in #engineering, twice in #lunch.
    assert len(msgs) == 4
    bodies = {m["body"] for m in msgs}
    assert any("deploy" in b.lower() for b in bodies)
    assert any("sushi" in b.lower() or "pizza" in b.lower() for b in bodies)


async def test_lunch_room_contents(
    mcp: Client, scripted_chat: dict[str, str]
) -> None:
    result = await mcp.call_tool(
        "search_messages", {"room": scripted_chat["lunch"]}
    )
    msgs = result.data["messages"]
    assert len(msgs) == 3
    # Carol never spoke in #lunch.
    speakers = {m["nick"] for m in msgs}
    assert "carol" not in speakers
    assert speakers == {"alice", "bob"}


async def test_combined_query_and_room(
    mcp: Client, scripted_chat: dict[str, str]
) -> None:
    """Did anyone mention the deploy specifically in #engineering?"""
    result = await mcp.call_tool(
        "search_messages",
        {"query": "deploy", "room": scripted_chat["engineering"]},
    )
    msgs = result.data["messages"]
    # Alice mentions "deploy" twice; Carol once ("unblock the deploy"). Bob's
    # auth-bug line does not contain the word.
    speakers_to_count: dict[str, int] = {}
    for m in msgs:
        speakers_to_count[m["nick"]] = speakers_to_count.get(m["nick"], 0) + 1
    assert speakers_to_count.get("alice", 0) >= 2
    assert "bob" not in speakers_to_count


async def test_search_is_repeatable(
    mcp: Client, scripted_chat: dict[str, str]
) -> None:
    """Calling search twice returns the same data — non-destructive contract."""
    first = await mcp.call_tool("search_messages", {"query": "auth"})
    second = await mcp.call_tool("search_messages", {"query": "auth"})
    assert first.data["count"] == second.data["count"]
    assert first.data["count"] > 0
