"""Environment-driven configuration for the XMPP MCP server."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from environment variables (and an optional .env file).

    The XMPP_* group configures the client connection used for all messaging,
    MUC, presence and discovery tools — it works against any RFC 6120/6121
    server (Openfire, Isode M-Link, ejabberd, Prosody).

    The OPENFIRE_* group is optional and only enables the Openfire REST admin
    tools. When unset, those tools fail with a clear message.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- XMPP client connection ------------------------------------------------
    xmpp_jid: str = Field(..., description="Bare JID of the service account, e.g. bot@example.com")
    xmpp_password: str = Field(..., description="Password for the service account")
    xmpp_host: str | None = Field(
        None,
        description="Server host to connect to, if it differs from the JID domain",
    )
    xmpp_port: int = Field(5222, description="C2S port")
    xmpp_tls_insecure: bool = Field(
        False,
        description="Skip TLS certificate verification (lab/self-signed servers only)",
    )
    xmpp_nick: str = Field("xmpp-mcp", description="Default nickname used when joining MUC rooms")
    xmpp_connect_timeout: float = Field(
        30.0, description="Seconds to wait for the XMPP session to establish"
    )
    xmpp_inbox_size: int = Field(
        500, description="Max number of inbound messages buffered in memory"
    )

    # --- Openfire REST API admin (optional) -----------------------------------
    openfire_base_url: str | None = Field(
        None,
        description="Base URL of the Openfire REST API plugin, e.g. http://openfire:9090",
    )
    openfire_secret_key: str | None = Field(
        None, description="Openfire REST API shared secret key (Authorization header)"
    )
    openfire_admin_user: str | None = Field(
        None, description="Openfire admin username (alternative to secret key)"
    )
    openfire_admin_password: str | None = Field(
        None, description="Openfire admin password (used with openfire_admin_user)"
    )
    openfire_verify_tls: bool = Field(
        True, description="Verify TLS certificates when calling the Openfire REST API"
    )

    @property
    def openfire_enabled(self) -> bool:
        """True when enough Openfire settings are present to attempt admin calls."""
        if not self.openfire_base_url:
            return False
        has_secret = bool(self.openfire_secret_key)
        has_basic = bool(self.openfire_admin_user and self.openfire_admin_password)
        return has_secret or has_basic


def load_settings() -> Settings:
    """Load settings from the environment. Raises if required XMPP_* vars are missing."""
    return Settings()  # type: ignore[call-arg]
