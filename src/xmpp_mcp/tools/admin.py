"""Openfire REST API admin tools (prefixed `of_`).

These are disabled unless OPENFIRE_* settings are configured; in that case each
tool fails with a clear message via :func:`get_openfire`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from ..openfire_admin import OpenfireError
from . import get_openfire


def register(mcp: FastMCP) -> None:
    """Register Openfire admin tools on the FastMCP app."""

    @mcp.tool
    async def of_list_users(ctx: Context) -> Any:
        """List all user accounts on the Openfire server (REST API plugin required)."""
        try:
            return await get_openfire(ctx).list_users()
        except OpenfireError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    async def of_get_user(
        ctx: Context,
        username: Annotated[str, Field(description="Username to look up")],
    ) -> Any:
        """Get a single Openfire user account by username."""
        try:
            return await get_openfire(ctx).get_user(username)
        except OpenfireError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    async def of_create_user(
        ctx: Context,
        username: Annotated[str, Field(description="Username for the new account")],
        password: Annotated[str, Field(description="Password for the new account")],
        name: Annotated[str | None, Field(description="Display name")] = None,
        email: Annotated[str | None, Field(description="Email address")] = None,
    ) -> dict[str, Any]:
        """Create a new user account on the Openfire server."""
        try:
            await get_openfire(ctx).create_user(username, password, name=name, email=email)
        except OpenfireError as exc:
            raise ToolError(str(exc)) from exc
        return {"created": True, "username": username}

    @mcp.tool
    async def of_delete_user(
        ctx: Context,
        username: Annotated[str, Field(description="Username of the account to delete")],
    ) -> dict[str, Any]:
        """Delete a user account from the Openfire server."""
        try:
            await get_openfire(ctx).delete_user(username)
        except OpenfireError as exc:
            raise ToolError(str(exc)) from exc
        return {"deleted": True, "username": username}

    @mcp.tool
    async def of_add_group_member(
        ctx: Context,
        username: Annotated[str, Field(description="Existing username to add")],
        group: Annotated[str, Field(description="Existing group name")],
    ) -> dict[str, Any]:
        """Add an existing user to an existing Openfire group."""
        try:
            await get_openfire(ctx).add_user_to_group(username, group)
        except OpenfireError as exc:
            raise ToolError(str(exc)) from exc
        return {"added": True, "username": username, "group": group}

    @mcp.tool
    async def of_list_rooms(
        ctx: Context,
        service_name: Annotated[
            str, Field(description="MUC service name")
        ] = "conference",
    ) -> Any:
        """List persistent chat rooms on an Openfire MUC service."""
        try:
            return await get_openfire(ctx).list_rooms(service_name=service_name)
        except OpenfireError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    async def of_create_room(
        ctx: Context,
        room_name: Annotated[str, Field(description="Room name (the JID local part)")],
        natural_name: Annotated[str, Field(description="Human-readable room name")],
        description: Annotated[str, Field(description="Room description")],
        subject: Annotated[str | None, Field(description="Initial room subject")] = None,
    ) -> dict[str, Any]:
        """Create a persistent MUC room on the Openfire server."""
        try:
            await get_openfire(ctx).create_room(
                room_name, natural_name, description, subject=subject
            )
        except OpenfireError as exc:
            raise ToolError(str(exc)) from exc
        return {"created": True, "room_name": room_name}
