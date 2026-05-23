"""XEP-0313 Message Archive Management — query historical MUC room messages.

Backed by Openfire's "Monitoring Service" plugin. Required system properties
are set in ``tests/integration/conftest.py _enable_restapi``:

* ``conversation.metadataArchiving=true``
* ``conversation.messageArchiving=true``
* ``conversation.roomArchiving=true``

Together they enable per-room message archiving and let MAM queries return
historical messages — including from before the bot even joined.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from slixmpp.exceptions import IqError, IqTimeout

from .xmpp_client import XMPPError

logger = logging.getLogger("xmpp_mcp.mam")


class MAMClient:
    """Async wrapper around slixmpp's ``xep_0313`` plugin.

    All results are plain Python dicts; XML never leaks to the MCP layer.
    Text filtering is applied client-side (XEP-0313 v2 has no built-in full-
    text search and Openfire's Monitoring plugin's full-text variant isn't
    portable).
    """

    def __init__(self, xmpp: Any) -> None:
        self._xmpp = xmpp
        self._plugin = xmpp.plugin["xep_0313"]

    async def query_room(
        self,
        room_jid: str,
        since: datetime | None = None,
        until: datetime | None = None,
        with_jid: str | None = None,
        query_text: str | None = None,
        limit: int = 50,
        timeout: float = 30.0,
    ) -> list[dict[str, Any]]:
        """Query a MUC room's MAM archive.

        ``since``/``until`` constrain the time range. ``with_jid`` filters to
        a specific participant. ``query_text`` is a case-insensitive
        substring applied to the message body *after* the server response —
        the server doesn't filter on body text.
        """
        try:
            iq = await self._plugin.retrieve(
                jid=room_jid,
                start=since,
                end=until,
                with_jid=with_jid,
                rsm={"max": str(limit)},
                timeout=timeout,
            )
        except (IqError, IqTimeout) as exc:
            raise XMPPError(
                f"mam_query({room_jid!r}) failed: {exc}"
            ) from exc

        return _normalise_results(iq, query_text=query_text)


def _normalise_results(
    iq: Any, query_text: str | None = None
) -> list[dict[str, Any]]:
    """Pull message records out of an iq[mam][results] response."""
    needle = query_text.lower() if query_text else None
    out: list[dict[str, Any]] = []
    for msg in iq["mam"]["results"]:
        forwarded = msg["mam_result"]["forwarded"]
        inner = forwarded["stanza"]
        body = inner["body"] or ""
        if not body:
            continue
        if needle and needle not in body.lower():
            continue
        is_muc = inner["type"] == "groupchat"
        timestamp_obj = forwarded["delay"]["stamp"]
        out.append(
            {
                "from": inner["from"].full,
                "to": inner["to"].full,
                "type": inner["type"],
                "body": body,
                "room": inner["from"].bare if is_muc else None,
                "nick": inner["from"].resource if is_muc else None,
                "timestamp": (
                    timestamp_obj.isoformat()
                    if hasattr(timestamp_obj, "isoformat") else
                    str(timestamp_obj) if timestamp_obj else None
                ),
            }
        )
    return out
