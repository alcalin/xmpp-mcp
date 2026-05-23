"""Seed the lab's rooms with realistic chatter so the bot (and Claude) has
something to find.

Connects alice / bob / carol as raw XMPP clients and posts a scripted
conversation into the four default rooms (medops, r1, r2, r3). The bot's
``search_messages`` buffer captures lines it receives while joined — so this
only "works" for a Claude session whose bot is **currently joined to the
target rooms**. Run this from a second terminal while ``claude`` is open in
the first.

Usage:

    python scripts/seed_chat.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from tests.integration.helpers.raw_client import RawXMPPClient  # noqa: E402

ROOM = lambda local: f"{local}@conference.xmpp.test"  # noqa: E731

# Targets only the rooms `start-lab.py` pre-configures (r1/r2/r3). Rooms a
# bot creates ad-hoc by joining (like Claude's `medops`) come up *locked* on
# Openfire until the owner submits a configuration form — alice can't join.
#
# Roles for the chatter:
#   r1 = #medical-ops (medic + commander coordinate)
#   r2 = #field-alpha (field team, lots of medevac context)
#   r3 = #command (CO, occasional medevac mention)
SCRIPT = [
    ("alice", "r1", "Doc-Alpha online. Standing by for medevac requests from field teams."),
    ("carol", "r2", "Lead-A: 2 WIA at grid 38S MB 1234 5678, one urgent surgical."),
    ("carol", "r2", "Need medical evacuation — preparing LZ with green smoke."),
    ("alice", "r1", "Got it Carol, working the 9-line MEDEVAC on pubsub now."),
    ("bob",   "r1", "Actual-6: CASEVAC coordination underway. Bird wheels-up in 10."),
    ("bob",   "r3", "Stand by, eyes on the LZ. Initiating CASEVAC coordination with medops."),
    ("carol", "r2", "LZ marked. Smoke is green, standing by for pickup."),
    ("alice", "r1", "Patient status: 2 litter + 1 ambulatory, security PROBABLE enemy."),
    ("bob",   "r1", "Roger Doc, RWY clear, ETA 8 min."),
    ("bob",   "r3", "Comms check at the top of the hour."),
]

# Which rooms each speaker needs to join. Each speaker only joins where they speak.
SPEAKER_ROOMS = {
    "alice": {"r1"},
    "bob":   {"r1", "r3"},
    "carol": {"r2"},
}

ACCOUNTS = {
    "alice": "alicepw",
    "bob":   "bobpw",
    "carol": "carolpw",
}


async def main() -> None:
    clients: dict[str, RawXMPPClient] = {}
    for name, password in ACCOUNTS.items():
        c = RawXMPPClient(f"{name}@xmpp.test", password, "127.0.0.1", 5222)
        await c.connect()
        clients[name] = c
        print(f"  connected: {name}")

    try:
        for name, rooms in SPEAKER_ROOMS.items():
            for r in rooms:
                await clients[name].join_muc(ROOM(r), name)
        await asyncio.sleep(0.3)
        print(f"  joined: {sum(len(r) for r in SPEAKER_ROOMS.values())} MUC sessions across "
              f"{len({r for rs in SPEAKER_ROOMS.values() for r in rs})} rooms")

        for who, room_local, body in SCRIPT:
            clients[who].send_groupchat(ROOM(room_local), body)
            await asyncio.sleep(0.15)
        await asyncio.sleep(0.6)  # let the bot's inbox capture every line

        print(f"\nseeded {len(SCRIPT)} messages across "
              f"{len({r for _, r, _ in SCRIPT})} rooms")
        print("Go back to Claude Code and re-run:")
        print('  "Search every room for mentions of medevac and tell me who said what."')
    finally:
        for c in clients.values():
            await c.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
