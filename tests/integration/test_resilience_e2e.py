"""Resilience tests — bot survives short Openfire blips and resumes operating.

Scope: short network-level interruptions (``docker pause``/``unpause``) where
the TCP connection survives but the server briefly stops processing stanzas.
This covers the realistic "network blip" / "container GC pause" / "host
scheduling stall" scenarios.

**Not in scope here**: full restart-and-reconnect. slixmpp's reconnect
backoff grows exponentially uncapped (``min(300, n*2+1)``), so a server bounce
that takes ~7 seconds can land in a 15-31 second wait window — the bot won't
notice it's back until much later. Production deployments that need fast
restart-recovery should override ``reschedule_connection_attempt`` to cap the
backoff. The bot stays correct; it's just slow to notice.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest
from fastmcp import Client

from .conftest import OpenfireHandle

pytestmark = pytest.mark.docker

CONTAINER = "xmpp-mcp-test-openfire-1"


def _docker(*args: str) -> None:
    subprocess.run(["docker", *args, CONTAINER], check=True, capture_output=True)


async def test_bot_resumes_after_short_pause(
    mcp: Client, openfire: OpenfireHandle
) -> None:
    """Pause the container, unpause it, verify the bot's next call succeeds."""
    # Baseline call works.
    r = await mcp.call_tool("discover_features", {})
    assert r.data["jid"] == openfire.domain

    try:
        _docker("pause")
        # Hold the pause briefly — long enough to be a real blip but short
        # enough that slixmpp's keepalive doesn't time out.
        await asyncio.sleep(2.0)
    finally:
        _docker("unpause")

    # Give Openfire a beat to start processing again, then verify recovery.
    await asyncio.sleep(0.5)
    r = await mcp.call_tool("discover_features", {})
    assert r.data["jid"] == openfire.domain


async def test_inbox_survives_pause(
    mcp: Client, openfire: OpenfireHandle
) -> None:
    """The in-memory inbox is process-local — must survive a server blip."""
    # Drain anything stale.
    await mcp.call_tool("get_recent_messages", {})

    try:
        _docker("pause")
        await asyncio.sleep(1.5)
    finally:
        _docker("unpause")
    await asyncio.sleep(0.5)

    # The inbox is owned by the bot's Python process; the pause/unpause
    # doesn't touch it. No new messages should have arrived during the blip.
    r = await mcp.call_tool("get_recent_messages", {})
    assert r.data["messages"] == []


async def test_pubsub_event_buffer_survives_pause(
    mcp: Client, raw_alice, openfire: OpenfireHandle
) -> None:
    """Bot subscribes; brief pause; alice publishes after unpause; the
    pubsub-event buffer captures the post-pause publish without losing the
    subscription."""
    import uuid

    service = f"pubsub.{openfire.domain}"
    node = f"resil-{uuid.uuid4().hex[:8]}"
    try:
        # Set up a publish-and-deliver path: bot is subscribed + alice can publish.
        await mcp.call_tool(
            "pubsub_create_node",
            {
                "service": service, "node": node,
                "config_values": {
                    "pubsub#persist_items": True,
                    "pubsub#max_items": "10",
                },
            },
        )
        await mcp.call_tool(
            "pubsub_set_affiliations",
            {
                "service": service, "node": node,
                "affiliations": [
                    {
                        "jid": openfire.accounts["alice"].jid,
                        "affiliation": "publisher",
                    }
                ],
            },
        )
        await mcp.call_tool(
            "pubsub_subscribe", {"service": service, "node": node}
        )
        # Drain whatever subscription event may have been recorded.
        await mcp.call_tool("pubsub_get_recent_events", {})

        try:
            _docker("pause")
            await asyncio.sleep(1.5)
        finally:
            _docker("unpause")
        await asyncio.sleep(0.5)

        # Alice publishes after the pause.
        await raw_alice.pubsub_submit_form(
            service, node, {"q": "after-pause publish"}
        )

        # Bot's event buffer eventually has the publish event — subscription
        # was preserved server-side, delivery resumed after unpause.
        events: list[dict] = []
        for _ in range(20):
            await asyncio.sleep(0.3)
            r = await mcp.call_tool(
                "pubsub_get_recent_events",
                {"node": node, "kind": "publish"},
            )
            if r.data["events"]:
                events = r.data["events"]
                break
        assert events, "publish event never arrived after unpause"
        form = events[0].get("form")
        assert form is not None
        fields = {f["var"]: f.get("value") for f in form["fields"]}
        assert fields.get("q") == "after-pause publish"
    finally:
        try:
            await mcp.call_tool(
                "pubsub_delete_node", {"service": service, "node": node}
            )
        except Exception:  # noqa: BLE001
            pass
