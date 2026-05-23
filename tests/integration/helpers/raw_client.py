"""A tiny slixmpp helper used by integration tests to act as the *other side*
of every conversation the MCP server has.

Each instance is a fresh XMPP login. ``await connect()`` blocks until the
session is established; received messages and presences land in
``asyncio.Queue`` so tests can ``await wait_for_message(timeout=...)`` without
polling.
"""

from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from typing import Any

from slixmpp import ClientXMPP


@dataclass
class ReceivedMessage:
    from_jid: str
    body: str
    type: str  # "chat" / "groupchat" / "normal"


@dataclass
class ReceivedPresence:
    from_jid: str
    show: str
    status: str


class RawXMPPClient:
    """A thin wrapper around ``slixmpp.ClientXMPP`` tailored for tests."""

    def __init__(self, jid: str, password: str, host: str, port: int = 5222) -> None:
        self.jid = jid
        self._client = ClientXMPP(jid, password)
        for xep in ("xep_0030", "xep_0045", "xep_0004", "xep_0060"):
            self._client.register_plugin(xep)
        # The lab Openfire speaks plaintext on 5222; skip cert verification
        # so this works even if the image is rebuilt with a self-signed cert.
        self._client.ssl_context.check_hostname = False
        self._client.ssl_context.verify_mode = ssl.CERT_NONE
        # See xmpp_client.py for why this is needed when host is pinned.
        self._client.enable_direct_tls = False
        # Lab servers may not offer STARTTLS (e.g. ejabberd without certs).
        # Allow SASL over plain TCP — test client only.
        try:
            mechs = self._client["feature_mechanisms"]
            mechs.unencrypted_plain = True
            mechs.unencrypted_scram = True
        except KeyError:
            pass
        self._host = host
        self._port = port
        self._ready: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self.messages: asyncio.Queue[ReceivedMessage] = asyncio.Queue()
        self.presences: asyncio.Queue[ReceivedPresence] = asyncio.Queue()
        self._joined_rooms: dict[str, str] = {}

        self._client.add_event_handler("session_start", self._on_session_start)
        # `failed_auth` fires per-mechanism; wait for the all-mechanisms event.
        self._client.add_event_handler("failed_all_auth", self._on_failed_auth)
        self._client.add_event_handler("connection_failed", self._on_connection_failed)
        # ``message`` covers groupchat too — subscribing to both doubles every
        # MUC line in our queue.
        self._client.add_event_handler("message", self._on_message)
        self._client.add_event_handler("changed_status", self._on_presence)

    async def connect(self, timeout: float = 15.0) -> None:
        self._client.connect(host=self._host, port=self._port)
        await asyncio.wait_for(self._ready, timeout=timeout)

    async def disconnect(self) -> None:
        for room, nick in list(self._joined_rooms.items()):
            try:
                self._client.plugin["xep_0045"].leave_muc(room, nick)
            except Exception:  # pragma: no cover - best-effort
                pass
        self._joined_rooms.clear()
        try:
            await self._client.disconnect()
        except Exception:  # pragma: no cover
            pass

    # --- event handlers ------------------------------------------------------

    async def _on_session_start(self, _event: Any) -> None:
        self._client.send_presence()
        try:
            await self._client.get_roster()
        except Exception:  # noqa: BLE001
            pass
        if not self._ready.done():
            self._ready.set_result(True)

    def _on_failed_auth(self, _event: Any) -> None:
        if not self._ready.done():
            self._ready.set_exception(RuntimeError(f"{self.jid}: auth failed"))

    def _on_connection_failed(self, reason: Any) -> None:
        if not self._ready.done():
            self._ready.set_exception(RuntimeError(f"{self.jid}: connect failed: {reason}"))

    def _on_message(self, msg: Any) -> None:
        if msg["type"] in ("chat", "groupchat", "normal") and msg["body"]:
            # MUC echoes the sender's own messages — drop them so tests don't
            # see "self" traffic.
            from_full = msg["from"].full
            if msg["type"] == "groupchat":
                room = msg["from"].bare
                own = self._joined_rooms.get(room)
                if own and msg["from"].resource == own:
                    return
            self.messages.put_nowait(
                ReceivedMessage(from_jid=from_full, body=msg["body"], type=msg["type"])
            )

    def _on_presence(self, presence: Any) -> None:
        self.presences.put_nowait(
            ReceivedPresence(
                from_jid=presence["from"].full,
                show=presence["show"] or "",
                status=presence["status"] or "",
            )
        )

    # --- conveniences --------------------------------------------------------

    def send_chat(self, to: str, body: str) -> None:
        self._client.send_message(mto=to, mbody=body, mtype="chat")

    def send_groupchat(self, room: str, body: str) -> None:
        self._client.send_message(mto=room, mbody=body, mtype="groupchat")

    def subscribe_to(self, jid: str) -> None:
        self._client.send_presence_subscription(pto=jid)

    async def join_muc(self, room: str, nick: str) -> None:
        # maxstanzas=0: opt out of room history; tests assert on live messages.
        await self._client.plugin["xep_0045"].join_muc_wait(
            room, nick, maxstanzas=0, timeout=15
        )
        self._joined_rooms[room] = nick

    def leave_muc(self, room: str) -> None:
        nick = self._joined_rooms.pop(room, None)
        if nick is not None:
            self._client.plugin["xep_0045"].leave_muc(room, nick)

    async def wait_for_message(self, timeout: float = 5.0) -> ReceivedMessage:
        return await asyncio.wait_for(self.messages.get(), timeout=timeout)

    async def pubsub_submit_form(
        self, service: str, node: str, values: dict[str, Any]
    ) -> str:
        """Publish a XEP-0004 ``type=submit`` form to a pubsub node.

        Used in integration tests where a non-bot account ("alice") fills out
        a form template published by the bot.
        """
        # Local imports — raw_client must not pull data_forms at module load
        # time so unit tests of the helper don't drag in the wider stack.
        from xmpp_mcp.data_forms import build_form_element, build_submit_form

        payload = build_form_element(build_submit_form(values))
        iq = await self._client.plugin["xep_0060"].publish(
            jid=service, node=node, payload=payload
        )
        try:
            return iq["pubsub"]["publish"]["item"]["id"] or ""
        except Exception:  # noqa: BLE001
            return ""

    async def wait_for_presence(self, timeout: float = 5.0) -> ReceivedPresence:
        return await asyncio.wait_for(self.presences.get(), timeout=timeout)

    async def __aenter__(self) -> "RawXMPPClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()
