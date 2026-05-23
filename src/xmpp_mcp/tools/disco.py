"""Service discovery and XEP-0258 security label tools."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from ..security_labels import SecurityLabelError, fetch_catalog, supports_security_labels
from ..xmpp_client import XMPPError
from . import get_settings, get_xmpp


def register(mcp: FastMCP) -> None:
    """Register discovery/security-label tools and the server-info resource."""

    @mcp.tool
    async def discover_features(
        ctx: Context,
        jid: Annotated[
            str | None,
            Field(
                description=(
                    "Entity to query (user, room, or service JID). "
                    "Defaults to the connected server's domain."
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Run XEP-0030 service discovery on an entity (or the server).

        Reports advertised features, identities, child services, and whether
        XEP-0258 security labels (Isode M-Link) are supported.
        """
        xmpp = get_xmpp(ctx)
        try:
            info = await xmpp.disco_info(jid)
            items = await xmpp.disco_items(jid)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {
            "jid": info["jid"],
            "identities": info["identities"],
            "features": info["features"],
            "items": items,
            "supports_security_labels": supports_security_labels(info["features"]),
        }

    @mcp.tool
    async def list_security_labels(
        ctx: Context,
        to: Annotated[
            str,
            Field(
                description=(
                    "Destination JID (user or room) to fetch the XEP-0258 label "
                    "catalog for."
                )
            ),
        ],
        via: Annotated[
            str | None,
            Field(
                description=(
                    "Optional JID of the catalog service. Default routes to the "
                    "destination's server domain (how M-Link works since M-Link is "
                    "the server). Set this when the catalog lives on a separate "
                    "component, e.g. via=seclabel.example.com."
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Fetch the XEP-0258 security label catalog for a destination (Isode M-Link).

        Returns the selectable labels with their selectors. Pass a returned
        `selector` as the `security_label` argument of `send_message` or
        `send_room_message` to send under that label.
        """
        xmpp = get_xmpp(ctx)
        settings = get_settings(ctx)
        try:
            labels = await fetch_catalog(
                xmpp.xmpp, to, via=via, timeout=settings.xmpp_connect_timeout
            )
        except SecurityLabelError as exc:
            raise ToolError(str(exc)) from exc
        return {"to": to, "count": len(labels), "labels": labels}

    @mcp.resource("xmpp://server-info")
    async def server_info_resource(ctx: Context) -> dict[str, Any]:
        """Discovery summary for the connected XMPP server."""
        xmpp = get_xmpp(ctx)
        try:
            info = await xmpp.disco_info(None)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {
            "account_jid": xmpp.xmpp.boundjid.full,
            "server": info["jid"],
            "identities": info["identities"],
            "features": info["features"],
            "supports_security_labels": supports_security_labels(info["features"]),
            "joined_rooms": xmpp.joined_rooms,
        }
