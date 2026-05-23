"""End-to-end discovery tests against the lab Openfire."""

from __future__ import annotations

import pytest
from fastmcp import Client

from .conftest import OpenfireHandle

pytestmark = pytest.mark.docker


async def test_discover_features_lists_muc_as_child(
    mcp: Client, openfire: OpenfireHandle
) -> None:
    result = await mcp.call_tool("discover_features", {})
    info = result.data
    assert info["jid"] == openfire.domain
    item_jids = {it["jid"] for it in info["items"]}
    assert openfire.muc_service in item_jids, (
        f"MUC service {openfire.muc_service!r} not listed as a child of the server: "
        f"items={info['items']}"
    )


async def test_discover_muc_service_features(
    mcp: Client, openfire: OpenfireHandle
) -> None:
    result = await mcp.call_tool("discover_features", {"jid": openfire.muc_service})
    info = result.data
    assert info["jid"] == openfire.muc_service
    # Any compliant MUC service must advertise the muc feature.
    assert "http://jabber.org/protocol/muc" in info["features"]


async def test_supports_security_labels_is_false_on_vanilla_openfire(
    mcp: Client,
) -> None:
    result = await mcp.call_tool("discover_features", {})
    assert result.data["supports_security_labels"] is False


async def test_list_security_labels_fails_cleanly_when_unsupported(
    mcp: Client, openfire: OpenfireHandle
) -> None:
    """Openfire has no XEP-0258 catalog; tool surfaces it as a ToolError, not a stacktrace."""
    with pytest.raises(Exception, match="(?i)security label|catalog"):
        await mcp.call_tool(
            "list_security_labels", {"to": openfire.accounts["alice"].jid}
        )
