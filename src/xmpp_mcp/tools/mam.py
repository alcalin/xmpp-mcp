"""XEP-0313 MAM tools — query the room archive across MCP-server restarts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from ..xmpp_client import XMPPError
from . import get_xmpp


def _parse_iso(label: str, value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # ``fromisoformat`` accepts "Z" suffix on Python 3.11+.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError(f"{label}: invalid ISO 8601 timestamp {value!r}: {exc}") from exc


def register(mcp: FastMCP) -> None:
    """Register MAM tools on the FastMCP app."""

    @mcp.tool
    async def mam_query(
        ctx: Context,
        room: Annotated[
            str,
            Field(
                description=(
                    "MUC room bare JID (e.g. medops@conference.xmpp.test). "
                    "MAM is queried at the room itself."
                )
            ),
        ],
        query: Annotated[
            str | None,
            Field(
                description=(
                    "Optional case-insensitive substring filter applied to the "
                    "message body. XEP-0313 v2 has no server-side fulltext, so "
                    "this runs client-side after the server returns its window."
                )
            ),
        ] = None,
        since: Annotated[
            str | None,
            Field(
                description=(
                    "Optional ISO 8601 lower bound (e.g. 2026-05-18T00:00:00Z). "
                    "The server returns only messages at or after this time."
                )
            ),
        ] = None,
        until: Annotated[
            str | None,
            Field(description="Optional ISO 8601 upper bound."),
        ] = None,
        with_jid: Annotated[
            str | None,
            Field(description="Optional participant filter — server-side."),
        ] = None,
        limit: Annotated[
            int, Field(description="Maximum results to return", ge=1, le=500)
        ] = 50,
    ) -> dict[str, Any]:
        """Query a MUC room's XEP-0313 MAM archive.

        Returns historical messages from before this MCP-server process even
        started — the messages live in Openfire's DB. Use this to answer
        "what was said in room X yesterday about Y" *after* an MCP restart,
        where the in-memory `search_messages` buffer is empty.
        """
        since_dt = _parse_iso("since", since)
        until_dt = _parse_iso("until", until)
        xmpp = get_xmpp(ctx)
        try:
            results = await xmpp.mam.query_room(
                room_jid=room,
                since=since_dt,
                until=until_dt,
                with_jid=with_jid,
                query_text=query,
                limit=limit,
            )
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"room": room, "count": len(results), "messages": results}
