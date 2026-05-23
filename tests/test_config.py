"""Tests for environment-driven settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from xmpp_mcp.config import Settings

_BASE = {"xmpp_jid": "bot@example.com", "xmpp_password": "secret"}


def _settings(**overrides: object) -> Settings:
    # _env_file=None keeps tests isolated from any real .env on disk.
    return Settings(_env_file=None, **{**_BASE, **overrides})  # type: ignore[arg-type]


def test_required_fields_have_defaults() -> None:
    s = _settings()
    assert s.xmpp_jid == "bot@example.com"
    assert s.xmpp_port == 5222
    assert s.xmpp_tls_insecure is False
    assert s.xmpp_nick == "xmpp-mcp"


def test_missing_required_field_raises() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, xmpp_jid="bot@example.com")  # type: ignore[call-arg]


def test_openfire_disabled_without_base_url() -> None:
    assert _settings(openfire_secret_key="k").openfire_enabled is False


def test_openfire_enabled_with_secret_key() -> None:
    s = _settings(openfire_base_url="http://of:9090", openfire_secret_key="k")
    assert s.openfire_enabled is True


def test_openfire_enabled_with_basic_auth() -> None:
    s = _settings(
        openfire_base_url="http://of:9090",
        openfire_admin_user="admin",
        openfire_admin_password="pw",
    )
    assert s.openfire_enabled is True


def test_openfire_disabled_with_partial_basic_auth() -> None:
    s = _settings(openfire_base_url="http://of:9090", openfire_admin_user="admin")
    assert s.openfire_enabled is False


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XMPP_JID", "env@example.com")
    monkeypatch.setenv("XMPP_PASSWORD", "envpw")
    monkeypatch.setenv("XMPP_PORT", "5269")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.xmpp_jid == "env@example.com"
    assert s.xmpp_port == 5269
