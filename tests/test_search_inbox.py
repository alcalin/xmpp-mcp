"""Unit tests for XMPPClient.search_inbox — no network required."""

from __future__ import annotations

from typing import Any

import pytest

from xmpp_mcp.config import Settings
from xmpp_mcp.xmpp_client import XMPPClient


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        xmpp_jid="bot@xmpp.test",
        xmpp_password="x",
        xmpp_inbox_size=50,
    )


def _seed(client: XMPPClient, messages: list[dict[str, Any]]) -> None:
    for m in messages:
        client._inbox.append(m)


def _make_inbox() -> list[dict[str, Any]]:
    """A realistic, mixed inbox: two rooms + one direct chat, ordered oldest→newest."""
    return [
        {
            "from": "r1@conf.xmpp.test/alice", "to": "bot@xmpp.test",
            "type": "groupchat", "body": "Ready to deploy v2.1 to staging",
            "security_label": None, "timestamp": "2026-05-15T10:00:00+00:00",
            "room": "r1@conf.xmpp.test", "nick": "alice",
        },
        {
            "from": "r1@conf.xmpp.test/bob", "to": "bot@xmpp.test",
            "type": "groupchat", "body": "I see a flaky test in the auth suite",
            "security_label": None, "timestamp": "2026-05-15T10:01:00+00:00",
            "room": "r1@conf.xmpp.test", "nick": "bob",
        },
        {
            "from": "r2@conf.xmpp.test/alice", "to": "bot@xmpp.test",
            "type": "groupchat", "body": "Anyone want sushi today?",
            "security_label": None, "timestamp": "2026-05-15T10:02:00+00:00",
            "room": "r2@conf.xmpp.test", "nick": "alice",
        },
        {
            "from": "carol@xmpp.test/laptop", "to": "bot@xmpp.test",
            "type": "chat", "body": "Hey bot — anything on the AUTH bug?",
            "security_label": None, "timestamp": "2026-05-15T10:03:00+00:00",
            "room": None, "nick": None,
        },
    ]


async def test_empty_inbox_returns_empty() -> None:
    c = XMPPClient(_settings())
    assert c.search_inbox(query="anything") == []


async def test_query_substring_case_insensitive() -> None:
    c = XMPPClient(_settings())
    _seed(c, _make_inbox())
    matches = c.search_inbox(query="auth")
    bodies = {m["body"] for m in matches}
    # one groupchat + one direct chat mention "auth"/"AUTH"
    assert bodies == {
        "I see a flaky test in the auth suite",
        "Hey bot — anything on the AUTH bug?",
    }


async def test_room_filter() -> None:
    c = XMPPClient(_settings())
    _seed(c, _make_inbox())
    matches = c.search_inbox(room="r1@conf.xmpp.test")
    assert len(matches) == 2
    assert {m["nick"] for m in matches} == {"alice", "bob"}


async def test_participant_by_nick_only_matches_muc() -> None:
    c = XMPPClient(_settings())
    _seed(c, _make_inbox())
    matches = c.search_inbox(participant="alice")
    # alice has two groupchat lines and no 1:1; carol's direct chat must not appear
    assert len(matches) == 2
    assert all(m["type"] == "groupchat" for m in matches)
    assert all(m["nick"] == "alice" for m in matches)


async def test_participant_by_bare_jid_matches_direct_chat() -> None:
    c = XMPPClient(_settings())
    _seed(c, _make_inbox())
    matches = c.search_inbox(participant="carol@xmpp.test")
    assert len(matches) == 1
    assert matches[0]["type"] == "chat"


async def test_since_filter_excludes_older_messages() -> None:
    c = XMPPClient(_settings())
    _seed(c, _make_inbox())
    matches = c.search_inbox(since="2026-05-15T10:02:00+00:00")
    # only the sushi line and the direct-chat AUTH line
    assert len(matches) == 2


async def test_limit_caps_results() -> None:
    c = XMPPClient(_settings())
    _seed(c, _make_inbox())
    matches = c.search_inbox(limit=1)
    assert len(matches) == 1


async def test_combined_query_and_room() -> None:
    c = XMPPClient(_settings())
    _seed(c, _make_inbox())
    matches = c.search_inbox(query="auth", room="r1@conf.xmpp.test")
    assert len(matches) == 1
    assert matches[0]["nick"] == "bob"


async def test_results_ordered_newest_first() -> None:
    c = XMPPClient(_settings())
    _seed(c, _make_inbox())
    matches = c.search_inbox()
    timestamps = [m["timestamp"] for m in matches]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_search_is_non_destructive() -> None:
    c = XMPPClient(_settings())
    _seed(c, _make_inbox())
    before = c.search_inbox()
    assert len(before) == 4
    # drain_inbox still sees every message
    drained = c.drain_inbox(limit=10)
    assert len(drained) == 4
