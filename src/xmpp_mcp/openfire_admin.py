"""Openfire REST API admin client — the Openfire support layer.

Operations like creating users, listing rooms or managing group membership are
*not* possible over a plain XMPP C2S connection. Openfire exposes them through
its "REST API" plugin; this module is a thin async wrapper over that plugin.

M-Link has no equivalent public REST admin API, so these tools are Openfire-only
and are simply disabled when ``OPENFIRE_*`` settings are absent.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import Settings

logger = logging.getLogger("xmpp_mcp.openfire")

_API_PREFIX = "/plugins/restapi/v1"


class OpenfireError(RuntimeError):
    """Raised when an Openfire REST API call fails."""


class OpenfireAdmin:
    """Async client for the Openfire REST API plugin.

    Construct only when :attr:`Settings.openfire_enabled` is true. Authentication
    uses the shared secret key when set, otherwise HTTP Basic with the admin
    username/password.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.openfire_base_url:
            raise OpenfireError("OPENFIRE_BASE_URL is not set")

        headers = {"Accept": "application/json"}
        auth: httpx.Auth | None = None
        if settings.openfire_secret_key:
            headers["Authorization"] = settings.openfire_secret_key
        elif settings.openfire_admin_user and settings.openfire_admin_password:
            auth = httpx.BasicAuth(
                settings.openfire_admin_user, settings.openfire_admin_password
            )
        else:  # pragma: no cover - guarded by Settings.openfire_enabled
            raise OpenfireError("No Openfire REST API credentials configured")

        self._client = httpx.AsyncClient(
            base_url=settings.openfire_base_url.rstrip("/") + _API_PREFIX,
            headers=headers,
            auth=auth,
            verify=settings.openfire_verify_tls,
            timeout=30.0,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise OpenfireError(f"Openfire REST request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise OpenfireError(
                f"Openfire REST {method} {path} returned {resp.status_code}: "
                f"{resp.text.strip() or '<empty body>'}"
            )
        return resp

    @staticmethod
    def _json(resp: httpx.Response) -> Any:
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # --- users ----------------------------------------------------------------

    async def list_users(self) -> Any:
        """Return all users known to the Openfire server."""
        resp = await self._request("GET", "/users")
        return self._json(resp)

    async def get_user(self, username: str) -> Any:
        """Return a single user by username."""
        resp = await self._request("GET", f"/users/{username}")
        return self._json(resp)

    async def create_user(
        self,
        username: str,
        password: str,
        name: str | None = None,
        email: str | None = None,
    ) -> None:
        """Create a new user account."""
        body: dict[str, Any] = {"username": username, "password": password}
        if name:
            body["name"] = name
        if email:
            body["email"] = email
        await self._request("POST", "/users", json=body)

    async def delete_user(self, username: str) -> None:
        """Delete a user account."""
        await self._request("DELETE", f"/users/{username}")

    async def add_user_to_group(self, username: str, group_name: str) -> None:
        """Add an existing user to an existing group."""
        await self._request("POST", f"/users/{username}/groups/{group_name}")

    # --- chat rooms -----------------------------------------------------------

    async def list_rooms(self, service_name: str = "conference") -> Any:
        """Return persistent chat rooms for a MUC service (default: conference)."""
        resp = await self._request(
            "GET", "/chatrooms", params={"servicename": service_name}
        )
        return self._json(resp)

    async def create_room(
        self,
        room_name: str,
        natural_name: str,
        description: str,
        subject: str | None = None,
        persistent: bool = True,
        public_room: bool = True,
    ) -> None:
        """Create a persistent MUC room."""
        body: dict[str, Any] = {
            "roomName": room_name,
            "naturalName": natural_name,
            "description": description,
            "persistent": persistent,
            "publicRoom": public_room,
        }
        if subject:
            body["subject"] = subject
        await self._request("POST", "/chatrooms", json=body)
