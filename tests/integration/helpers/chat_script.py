"""Drive several :class:`RawXMPPClient`s through an ordered conversation.

A small wrapper that turns a static list of ``(speaker, target, body)`` tuples
into the real XMPP traffic, with a tiny pause between lines so Openfire emits
stanzas in script order rather than racing them.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .raw_client import RawXMPPClient


@dataclass
class Line:
    """One line of scripted conversation.

    ``target`` is a room bare JID (treated as MUC) or a user bare JID (treated
    as 1:1). ``body`` is the message text.
    """

    speaker: str
    target: str
    body: str


class ChatScript:
    """Owns several raw clients keyed by short name (``alice``, ``bob``, ...)."""

    def __init__(self, clients: dict[str, RawXMPPClient], muc_service: str) -> None:
        self._clients = clients
        self._muc_service = muc_service

    def _is_muc(self, target: str) -> bool:
        return target.endswith("@" + self._muc_service)

    async def join_room(self, speaker: str, room: str, nick: str | None = None) -> None:
        await self._clients[speaker].join_muc(room, nick or speaker)

    async def join_all(self, room: str, speakers: list[str]) -> None:
        for s in speakers:
            await self.join_room(s, room)

    async def say(self, line: Line, pause: float = 0.15) -> None:
        client = self._clients[line.speaker]
        if self._is_muc(line.target):
            client.send_groupchat(line.target, line.body)
        else:
            client.send_chat(line.target, line.body)
        if pause:
            await asyncio.sleep(pause)

    async def run(self, lines: list[Line], pause: float = 0.15) -> None:
        for line in lines:
            await self.say(line, pause=pause)
