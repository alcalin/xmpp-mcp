"""Tests for the Openfire REST API admin client (HTTP mocked with respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from xmpp_mcp.config import Settings
from xmpp_mcp.openfire_admin import OpenfireAdmin, OpenfireError

_BASE_URL = "http://openfire.test:9090"
_API = f"{_BASE_URL}/plugins/restapi/v1"


def _admin() -> OpenfireAdmin:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        xmpp_jid="bot@example.com",
        xmpp_password="secret",
        openfire_base_url=_BASE_URL,
        openfire_secret_key="s3cret",
    )
    return OpenfireAdmin(settings)


@respx.mock
async def test_list_users_returns_json() -> None:
    respx.get(f"{_API}/users").mock(
        return_value=httpx.Response(200, json={"users": [{"username": "alice"}]})
    )
    admin = _admin()
    try:
        result = await admin.list_users()
    finally:
        await admin.aclose()
    assert result == {"users": [{"username": "alice"}]}


@respx.mock
async def test_create_user_sends_expected_body() -> None:
    route = respx.post(f"{_API}/users").mock(return_value=httpx.Response(201))
    admin = _admin()
    try:
        await admin.create_user("bob", "pw", name="Bob", email="bob@example.com")
    finally:
        await admin.aclose()
    assert route.called
    sent = route.calls.last.request
    import json

    assert json.loads(sent.content) == {
        "username": "bob",
        "password": "pw",
        "name": "Bob",
        "email": "bob@example.com",
    }


@respx.mock
async def test_delete_user_issues_delete() -> None:
    route = respx.delete(f"{_API}/users/bob").mock(return_value=httpx.Response(200))
    admin = _admin()
    try:
        await admin.delete_user("bob")
    finally:
        await admin.aclose()
    assert route.called


@respx.mock
async def test_secret_key_sent_as_authorization_header() -> None:
    route = respx.get(f"{_API}/users").mock(return_value=httpx.Response(200, json=[]))
    admin = _admin()
    try:
        await admin.list_users()
    finally:
        await admin.aclose()
    assert route.calls.last.request.headers["Authorization"] == "s3cret"


@respx.mock
async def test_error_status_raises_openfire_error() -> None:
    respx.get(f"{_API}/users/ghost").mock(
        return_value=httpx.Response(404, text="User not found")
    )
    admin = _admin()
    try:
        with pytest.raises(OpenfireError, match="404"):
            await admin.get_user("ghost")
    finally:
        await admin.aclose()


@respx.mock
async def test_list_rooms_passes_service_name() -> None:
    route = respx.get(f"{_API}/chatrooms").mock(
        return_value=httpx.Response(200, json={"chatRooms": []})
    )
    admin = _admin()
    try:
        await admin.list_rooms(service_name="conference")
    finally:
        await admin.aclose()
    assert route.calls.last.request.url.params["servicename"] == "conference"
