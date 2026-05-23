"""Presence and roster (contact list) tools."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from ..xmpp_client import XMPPError
from . import get_xmpp

# RFC 6121 <show> values; absence means plain "available".
_VALID_SHOW = {"away", "chat", "dnd", "xa"}


def register(mcp: FastMCP) -> None:
    """Register presence/roster tools and the roster resource on the FastMCP app."""

    @mcp.tool
    def set_presence(
        ctx: Context,
        show: Annotated[
            str | None,
            Field(description="Presence state: away, chat, dnd, or xa. Omit for 'available'."),
        ] = None,
        status: Annotated[
            str | None, Field(description="Free-text status message")
        ] = None,
    ) -> dict[str, Any]:
        """Set the bot account's presence (availability) and status text."""
        if show is not None and show not in _VALID_SHOW:
            raise ToolError(f"show must be one of {sorted(_VALID_SHOW)} or omitted")
        xmpp = get_xmpp(ctx)
        try:
            xmpp.set_presence(show=show, status=status)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"show": show or "available", "status": status}

    @mcp.tool
    def get_roster(ctx: Context) -> dict[str, Any]:
        """List the bot account's roster (contacts) with subscription state."""
        xmpp = get_xmpp(ctx)
        contacts = xmpp.get_roster()
        return {"count": len(contacts), "contacts": contacts}

    @mcp.tool
    async def add_contact(
        ctx: Context,
        jid: Annotated[str, Field(description="Bare JID of the contact to add")],
        name: Annotated[
            str | None, Field(description="Optional display name for the contact")
        ] = None,
    ) -> dict[str, Any]:
        """Add a contact to the roster and send a presence subscription request."""
        xmpp = get_xmpp(ctx)
        try:
            await xmpp.add_contact(jid, name=name)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"added": True, "jid": jid, "name": name}

    @mcp.tool
    async def remove_contact(
        ctx: Context,
        jid: Annotated[str, Field(description="Bare JID of the contact to remove")],
    ) -> dict[str, Any]:
        """Remove a contact from the roster and cancel subscriptions."""
        xmpp = get_xmpp(ctx)
        try:
            await xmpp.remove_contact(jid)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"removed": True, "jid": jid}

    @mcp.resource("xmpp://roster")
    def roster_resource(ctx: Context) -> dict[str, Any]:
        """The current roster snapshot for the connected bot account."""
        xmpp = get_xmpp(ctx)
        contacts = xmpp.get_roster()
        return {"count": len(contacts), "contacts": contacts}
