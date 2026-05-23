"""FastMCP server wiring for xmpp-mcp.

Builds the FastMCP app, manages the XMPP connection lifecycle in the server
lifespan, and registers every tool/resource module.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP

from .config import Settings, load_settings
from .openfire_admin import OpenfireAdmin
from .tools import (
    CTX_OPENFIRE, CTX_SETTINGS, CTX_XMPP,
    admin, disco, mam, messaging, muc, presence, pubsub,
)
from .xmpp_client import XMPPClient

logger = logging.getLogger("xmpp_mcp")


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Open the XMPP connection (and optional Openfire client) for the server's life."""
    settings: Settings = load_settings()

    xmpp = XMPPClient(settings)
    await xmpp.start()

    openfire = OpenfireAdmin(settings) if settings.openfire_enabled else None
    if openfire is None:
        logger.info("Openfire admin tools disabled (OPENFIRE_* not configured)")

    try:
        yield {
            CTX_XMPP: xmpp,
            CTX_SETTINGS: settings,
            CTX_OPENFIRE: openfire,
        }
    finally:
        await xmpp.stop()
        if openfire is not None:
            await openfire.aclose()


def create_server() -> FastMCP:
    """Construct the FastMCP app with all tools and resources registered."""
    mcp: FastMCP = FastMCP(
        "xmpp-mcp",
        instructions=(
            "Operate over XMPP: send/receive direct and MUC messages, manage "
            "presence and rosters, discover server features, and apply XEP-0258 "
            "security labels (Isode M-Link). When OPENFIRE_* is configured, the "
            "of_* tools administer an Openfire server over its REST API."
        ),
        lifespan=_lifespan,
    )

    messaging.register(mcp)
    presence.register(mcp)
    muc.register(mcp)
    disco.register(mcp)
    pubsub.register(mcp)
    mam.register(mcp)
    admin.register(mcp)

    return mcp


def main() -> None:
    """Console-script entry point.

    Default transport is **stdio** (the MCP norm — Claude Desktop / Claude Code
    spawn the binary as a subprocess and talk JSON-RPC to it). Set
    ``XMPP_MCP_TRANSPORT=http`` to run a long-lived HTTP server instead, useful
    for poking at the tool surface from a browser, curl, or a remote client.

    HTTP knobs:
      * ``XMPP_MCP_HTTP_HOST`` (default 127.0.0.1)
      * ``XMPP_MCP_HTTP_PORT`` (default 8765)
    """
    import os

    transport = os.environ.get("XMPP_MCP_TRANSPORT", "stdio").lower()
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,  # stdout is reserved for the MCP stdio transport
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    server = create_server()
    try:
        if transport == "http":
            host = os.environ.get("XMPP_MCP_HTTP_HOST", "127.0.0.1")
            port = int(os.environ.get("XMPP_MCP_HTTP_PORT", "8765"))
            logger.info("Running MCP server on http://%s:%s/mcp", host, port)
            server.run(transport="http", host=host, port=port, show_banner=False)
        else:
            # show_banner=False: the stdio transport reserves stdout for the MCP
            # protocol — a banner printed there would corrupt the stream.
            server.run(show_banner=False)
    except KeyboardInterrupt:
        # Ctrl-C when run by hand: the lifespan already tore the XMPP session
        # down cleanly, so exit quietly instead of dumping an anyio/asyncio
        # cancellation traceback.
        logger.info("Interrupted — shutting down")


if __name__ == "__main__":
    main()
