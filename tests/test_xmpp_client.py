"""Integration test for the XMPP client wrapper.

Opt-in: requires a live XMPP server. Run with:

    pytest -m integration

and the XMPP_JID / XMPP_PASSWORD (and optionally XMPP_HOST/XMPP_PORT/
XMPP_TLS_INSECURE) environment variables set.
"""

from __future__ import annotations

import os

import pytest

from xmpp_mcp.config import Settings
from xmpp_mcp.xmpp_client import XMPPClient

pytestmark = pytest.mark.integration


@pytest.fixture
def settings() -> Settings:
    if not os.getenv("XMPP_JID") or not os.getenv("XMPP_PASSWORD"):
        pytest.skip("XMPP_JID / XMPP_PASSWORD not set — skipping integration test")
    return Settings()  # type: ignore[call-arg]


async def test_connect_disco_and_disconnect(settings: Settings) -> None:
    client = XMPPClient(settings)
    await client.start()
    try:
        info = await client.disco_info(None)
        assert "features" in info
        assert info["jid"]  # the server domain
        roster = client.get_roster()
        assert isinstance(roster, list)
    finally:
        await client.stop()
