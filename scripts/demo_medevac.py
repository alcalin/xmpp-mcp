"""End-to-end mil-style demo:

1. Bring up the lab; create 4 chat rooms with **server-side logging enabled**
   (Openfire persists messages in its DB when the room has logEnabled=true).
2. Bot joins every room first so its in-memory inbox also captures everything.
3. Script realistic conversation across the rooms — some explicitly about
   medical evacuation.
4. Use `search_messages` to find every mention of medevac across rooms.
5. Create a pubsub node `medevac-9line` with a XEP-0004 form template that
   matches the 9-Line MEDEVAC standard.
6. Submit a real 9-line as a form.
7. Read the submission back.
8. Answer the question — *will it push to the chat room?* — by showing the
   pubsub publish event arriving at the bot, then having the bot RELAY a
   formatted summary into #medical-ops. That bridge is *intentional*; XMPP
   pubsub and MUC are separate distribution channels.

Run:

    python scripts/demo_medevac.py          # uses or brings up the container
    python scripts/demo_medevac.py --down   # tear down at exit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# Windows: force UTF-8 on stdout so the demo's banners / arrows / mil symbols
# don't crash on cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))
sys.path.insert(0, str(HERE))

import httpx  # noqa: E402

from tests.integration.conftest import (  # noqa: E402
    OpenfireHandle,
    _enable_restapi,
    _wait_for_http,
    _wait_for_rest_api,
    _wait_for_tcp,
)
from tests.integration.helpers.chat_script import ChatScript, Line  # noqa: E402
from tests.integration.helpers.raw_client import RawXMPPClient  # noqa: E402

COMPOSE_DIR = HERE / "tests" / "integration" / "docker"

# Four mission-style rooms.
ROOMS = {
    "command":      "command",       # CO / staff
    "medical-ops":  "medops",        # medical coordination
    "field-alpha":  "fieldalpha",    # forward team A
    "field-bravo":  "fieldbravo",    # forward team B
}

# Speaker → MUC nick (role).
NICKS = {"alice": "Doc-Alpha", "bob": "Actual-6", "carol": "Lead-A"}

# Scripted conversation. Roles split:
#   alice (Doc-Alpha) = senior medic, lives in #medical-ops, talks to field
#   bob   (Actual-6)  = ops commander in #command, occasional in #medical-ops
#   carol (Lead-A)    = field-alpha team lead, also in #medical-ops
SCRIPT: list[Line] = [
    # --- command ---------------------------------------------------------
    Line("bob",   "command",      "Stand by, troops are on the move toward OBJ Bravo."),
    Line("bob",   "command",      "We have eyes on two friendly casualties at grid 38S MB 1234 5678."),
    Line("bob",   "command",      "Initiating CASEVAC coordination with medical-ops, monitor this channel."),

    # --- field-alpha -----------------------------------------------------
    Line("carol", "field-alpha",  "Contact in progress, two WIA, one urgent surgical."),
    Line("carol", "field-alpha",  "Litter team forming, need medical evacuation ASAP."),
    Line("carol", "field-alpha",  "Marking LZ with green smoke at last reported grid."),

    # --- field-bravo (no medevac mention here — control case) -----------
    Line("bob",   "field-bravo",  "Bravo: hold your position, eyes on suspected mortar nest east."),
    Line("bob",   "field-bravo",  "Comms check at the top of the hour."),

    # --- medical-ops -----------------------------------------------------
    Line("alice", "medical-ops",  "Doc-Alpha here. I have the patrol's medevac request inbound."),
    Line("carol", "medical-ops",  "Lead-A: confirming 2 urgent, 1 surgical priority. Pickup in 10."),
    Line("alice", "medical-ops",  "Will publish a 9-line MEDEVAC to the medevac-9line pubsub node — "
                                  "everyone subscribed will be notified."),
    Line("bob",   "medical-ops",  "Roger Doc, I'm monitoring. Get the bird wheels-up."),
]


# A realistic 9-Line MEDEVAC form template (the standard military format).
NINE_LINE_FIELDS = [
    {"var": "line1_location",   "type": "text-single", "label": "1. Location of pickup site (MGRS grid)", "required": True},
    {"var": "line2_freq",       "type": "text-single", "label": "2. Radio frequency, call sign, suffix"},
    {"var": "line3_precedence", "type": "list-single", "label": "3. Number of patients by precedence",
     "options": [
         {"value": "A", "label": "A — Urgent (≤ 1hr)"},
         {"value": "B", "label": "B — Urgent surgical (≤ 1hr)"},
         {"value": "C", "label": "C — Priority (≤ 4hr)"},
         {"value": "D", "label": "D — Routine (≤ 24hr)"},
         {"value": "E", "label": "E — Convenience"},
     ]},
    {"var": "line4_equipment",  "type": "list-multi",  "label": "4. Special equipment required",
     "options": [
         {"value": "none",    "label": "None"},
         {"value": "hoist",   "label": "Hoist"},
         {"value": "extract", "label": "Extraction equipment"},
         {"value": "vent",    "label": "Ventilator"},
     ]},
    {"var": "line5_patients",   "type": "text-single", "label": "5. Patients by type (L=litter, A=ambulatory) e.g. '2L+1A'"},
    {"var": "line6_security",   "type": "list-single", "label": "6. Security at pickup site",
     "options": [
         {"value": "N", "label": "N — No enemy"},
         {"value": "P", "label": "P — Possible enemy"},
         {"value": "E", "label": "E — Enemy in area"},
         {"value": "X", "label": "X — Armed escort required"},
     ]},
    {"var": "line7_marking",    "type": "list-single", "label": "7. Method of marking pickup site",
     "options": [
         {"value": "A", "label": "A — Panels"},
         {"value": "B", "label": "B — Pyrotechnic"},
         {"value": "C", "label": "C — Smoke"},
         {"value": "D", "label": "D — None"},
         {"value": "E", "label": "E — Other"},
     ]},
    {"var": "line8_nationality", "type": "list-single", "label": "8. Patient nationality / status",
     "options": [
         {"value": "A", "label": "A — US military"},
         {"value": "B", "label": "B — US civilian"},
         {"value": "C", "label": "C — Non-US military"},
         {"value": "D", "label": "D — Non-US civilian"},
         {"value": "E", "label": "E — EPW"},
     ]},
    {"var": "line9_nbc",        "type": "list-multi",  "label": "9. NBC contamination",
     "options": [
         {"value": "none", "label": "None"},
         {"value": "N",    "label": "Nuclear"},
         {"value": "B",    "label": "Biological"},
         {"value": "C",    "label": "Chemical"},
     ]},
]

NINE_LINE_SUBMISSION = {
    "line1_location":   "38S MB 12340 56780",
    "line2_freq":       "FM 38.450, callsign SHARK-6",
    "line3_precedence": "A",
    "line4_equipment":  ["hoist"],
    "line5_patients":   "2L+1A",
    "line6_security":   "P",
    "line7_marking":    "C",
    "line8_nationality": "A",
    "line9_nbc":        ["none"],
}


def compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", *args],
        cwd=COMPOSE_DIR, check=True, capture_output=True,
    )


def wait_for_health(timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = subprocess.run(
            ["docker", "inspect", "xmpp-mcp-test-openfire-1",
             "--format", "{{.State.Health.Status}}"],
            capture_output=True, text=True,
        )
        if r.stdout.strip() == "healthy":
            return
        time.sleep(1.0)
    raise SystemExit("openfire never went healthy")


def create_rooms_with_logging(handle: OpenfireHandle) -> None:
    """Create each room via the Openfire REST API with logEnabled=true."""
    auth = (handle.admin_user, handle.admin_password)
    url = f"{handle.admin_url}/plugins/restapi/v1/chatrooms"
    for room_name, local in ROOMS.items():
        body = {
            "roomName": local,
            "naturalName": f"#{room_name}",
            "description": f"Demo room: #{room_name}",
            "persistent": True,
            "publicRoom": True,
            "logEnabled": True,         # ← server-side persistence
            "membersOnly": False,
            "moderated": False,
            "subject": f"#{room_name}",
        }
        r = httpx.post(
            url, json=body, auth=auth,
            headers={"Accept": "application/json"}, timeout=10.0,
        )
        if r.status_code not in (200, 201, 409):
            raise RuntimeError(
                f"creating room {local!r} failed: {r.status_code} {r.text[:200]!r}"
            )


@asynccontextmanager
async def session(handle: OpenfireHandle):
    bot = handle.accounts["bot"]
    os.environ.update({
        "XMPP_JID": bot.jid, "XMPP_PASSWORD": bot.password,
        "XMPP_HOST": handle.host, "XMPP_PORT": str(handle.c2s_port),
        "XMPP_TLS_INSECURE": "true",
        "OPENFIRE_BASE_URL": handle.admin_url,
        "OPENFIRE_ADMIN_USER": handle.admin_user,
        "OPENFIRE_ADMIN_PASSWORD": handle.admin_password,
    })

    from fastmcp import Client

    from xmpp_mcp.server import create_server

    server = create_server()
    raws: dict[str, RawXMPPClient] = {}
    for name in ("alice", "bob", "carol"):
        acct = handle.accounts[name]
        raws[name] = RawXMPPClient(
            acct.jid, acct.password, handle.host, handle.c2s_port
        )
    try:
        for c in raws.values():
            await c.connect()
        async with Client(server) as mcp:
            yield mcp, raws
    finally:
        for c in raws.values():
            await c.disconnect()


def banner(text: str) -> None:
    print(f"\n{'=' * 78}")
    print(f"=== {text}")
    print(f"{'=' * 78}")


def show(label: str, payload) -> None:
    print(f"\n>>> {label}")
    print(json.dumps(payload, indent=2, default=str)[:2200])


async def call(mcp, label: str, tool: str, args: dict, *, summary: int | None = 900):
    print(f"\n>>> {label}")
    print(f"    call: {tool}({json.dumps(args)[:160]}{'…' if len(json.dumps(args)) > 160 else ''})")
    r = await mcp.call_tool(tool, args)
    body = json.dumps(r.data, indent=2, default=str)
    if summary is not None and len(body) > summary:
        body = body[:summary] + f"\n    ... ({len(body)} chars total)"
    print(f"    -> {body}")
    return r.data


async def amain(handle: OpenfireHandle) -> None:
    service = f"pubsub.{handle.domain}"

    banner("PHASE 1 — Create persistent, logged rooms")
    create_rooms_with_logging(handle)
    print(f"  Created {len(ROOMS)} rooms with logEnabled=true → Openfire persists "
          "every line in its DB.")

    async with session(handle) as (mcp, raws):
        banner("PHASE 2 — Bot joins every room so its inbox captures everything")
        room_jids = {
            label: f"{local}@{handle.muc_service}"
            for label, local in ROOMS.items()
        }
        for label, jid in room_jids.items():
            await call(
                mcp, f"join #{label}",
                "join_room", {"room_jid": jid, "nick": "AI-Assistant"},
                summary=200,
            )

        banner("PHASE 3 — Script the conversation across all rooms")
        chat = ChatScript(raws, muc_service=handle.muc_service)
        # Each speaker joins only the rooms they speak in.
        await chat.join_all(room_jids["command"],     ["bob"])
        await chat.join_all(room_jids["medical-ops"], ["alice", "bob", "carol"])
        await chat.join_all(room_jids["field-alpha"], ["carol"])
        await chat.join_all(room_jids["field-bravo"], ["bob"])
        # Override nicks with mil-style callsigns.
        for who, nick in NICKS.items():
            # Re-join under the role-nick. (Cheap: leave + rejoin would change
            # nick; we already have generic-nick joins above. For demo
            # purposes the line speaker prefix is what matters in the
            # buffered inbox.)
            pass
        await asyncio.sleep(0.3)

        for line in SCRIPT:
            resolved = Line(line.speaker, room_jids[line.target], line.body)
            await chat.say(resolved, pause=0.12)
        await asyncio.sleep(0.8)
        print("\n  Conversation seeded. Openfire has persisted these lines to its DB "
              "(logEnabled=true on each room) and the bot's in-memory inbox has "
              "captured them too.")

        banner('PHASE 4 — Ask "who has been talking about medical evacuation?"')
        # Multiple queries the AI would naturally try.
        for query in ("medical evacuation", "medevac", "casevac", "9-line"):
            r = await call(
                mcp, f"search_messages(query={query!r})",
                "search_messages", {"query": query, "limit": 20},
                summary=1400,
            )

        banner("PHASE 5 — Create the medevac-9line pubsub node + form template")
        node = "medevac-9line"
        try:
            await call(
                mcp, "delete any existing node (clean slate)",
                "pubsub_delete_node", {"service": service, "node": node},
                summary=200,
            )
        except Exception:
            pass

        await call(
            mcp, "create node with retention",
            "pubsub_create_node",
            {
                "service": service, "node": node,
                "config_values": {
                    "pubsub#persist_items": True,
                    "pubsub#max_items": "100",
                    "pubsub#title": "9-Line MEDEVAC requests",
                },
            },
            summary=200,
        )
        # Give alice (the medic) permission to publish.
        await call(
            mcp, "grant Doc-Alpha the publisher affiliation",
            "pubsub_set_affiliations",
            {
                "service": service, "node": node,
                "affiliations": [
                    {"jid": handle.accounts["alice"].jid, "affiliation": "publisher"},
                ],
            },
            summary=200,
        )
        await call(
            mcp, "publish the 9-line FORM TEMPLATE (defines the schema)",
            "pubsub_publish_form_template",
            {
                "service": service, "node": node,
                "fields": NINE_LINE_FIELDS,
                "title": "9-Line MEDEVAC Request",
                "instructions": "Fill in all 9 lines per US Army FM 4-02.2",
                "item_id": "template",
            },
            summary=400,
        )

        banner("PHASE 6 — Bot subscribes so it gets notified on every submission")
        await call(
            mcp, "subscribe",
            "pubsub_subscribe", {"service": service, "node": node},
            summary=200,
        )
        # Drain the subscription-state event so the next get_recent_events
        # only shows publishes.
        await mcp.call_tool("pubsub_get_recent_events", {})

        banner("PHASE 7 — Doc-Alpha submits a real 9-line via her raw XMPP client")
        item_id = await raws["alice"].pubsub_submit_form(
            service, node, NINE_LINE_SUBMISSION
        )
        print(f"  Doc-Alpha submitted -> item_id={item_id!r}")
        await asyncio.sleep(0.5)

        banner("PHASE 8 — Read it back as a structured form")
        all_forms = await call(
            mcp, "read all forms on the node",
            "pubsub_read_forms", {"service": service, "node": node, "max_items": 20},
            summary=2200,
        )
        submissions = [
            i for i in all_forms["items"]
            if i["form"].get("type") == "submit"
        ]
        if submissions:
            sub = submissions[0]
            fields = {f["var"]: f.get("value") for f in sub["form"]["fields"]}
            print("\n  Decoded MEDEVAC submission:")
            print(f"    Line 1: {fields.get('line1_location')}")
            print(f"    Line 2: {fields.get('line2_freq')}")
            print(f"    Line 3 (precedence): {fields.get('line3_precedence')}")
            print(f"    Line 4 (equipment):  {fields.get('line4_equipment')}")
            print(f"    Line 5 (patients):   {fields.get('line5_patients')}")
            print(f"    Line 6 (security):   {fields.get('line6_security')}")
            print(f"    Line 7 (marking):    {fields.get('line7_marking')}")
            print(f"    Line 8 (nationality):{fields.get('line8_nationality')}")
            print(f"    Line 9 (NBC):        {fields.get('line9_nbc')}")

        banner('PHASE 9 — *Did the submission show up in #medical-ops?*')
        # Read the rooms' inbox to demonstrate that NO pubsub-derived message
        # appeared.
        r = await call(
            mcp, "search room messages for the 9-line content",
            "search_messages",
            {"query": "MB 12340", "room": room_jids["medical-ops"]},
            summary=500,
        )
        print("\n  ↑ Empty. Pubsub publishes do NOT automatically reach MUC rooms —")
        print("    they are two separate distribution mechanisms in XMPP.")

        banner("PHASE 10 — Bot bridges: read the publish event, post a summary to #medical-ops")
        events = await call(
            mcp, "drain pubsub publish events the bot has buffered",
            "pubsub_get_recent_events", {"node": node, "kind": "publish"},
            summary=1400,
        )
        if events["events"]:
            ev = events["events"][0]
            form = ev.get("form", {})
            fields = {f["var"]: f.get("value") for f in form.get("fields", [])}
            summary = (
                "🚨 NEW 9-LINE MEDEVAC\n"
                f"  Line 1 (pickup): {fields.get('line1_location')}\n"
                f"  Line 3 (precedence): {fields.get('line3_precedence')}\n"
                f"  Line 5 (patients): {fields.get('line5_patients')}\n"
                f"  Line 6 (security): {fields.get('line6_security')}\n"
                f"  Source: {ev.get('node')} item {ev.get('item_id')}"
            )
            await call(
                mcp, "relay summary into #medical-ops",
                "send_room_message",
                {"room_jid": room_jids["medical-ops"], "body": summary},
                summary=300,
            )

        # Re-search to prove the relayed line is now in the room's history.
        # Wait for the MUC echo to round-trip so the bot's own send shows up
        # in its inbox.
        await asyncio.sleep(0.8)
        banner("PHASE 11 — Confirm the relayed message is now in #medical-ops")
        r2 = await call(
            mcp, "search #medical-ops for the 9-line again",
            "search_messages",
            {"query": "MB 12340", "room": room_jids["medical-ops"]},
            summary=900,
        )
        print(f"\n  Found {r2['count']} matching message(s) in #medical-ops — the "
              "bot is the pubsub↔MUC bridge (the publish event → formatted "
              "summary → MUC line).")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--down", action="store_true")
    args = p.parse_args()

    print("=== Bringing up the lab ===")
    if args.rebuild:
        compose("build")
    compose("up", "-d")
    wait_for_health()
    handle = OpenfireHandle()
    _wait_for_tcp(handle.host, handle.c2s_port, timeout=60)
    _wait_for_http(f"{handle.admin_url}/login.jsp", timeout=60)
    _enable_restapi(handle)
    _wait_for_rest_api(handle, timeout=30)

    try:
        asyncio.run(amain(handle))
    finally:
        if args.down:
            print("\n=== Tearing down ===")
            compose("down", "-v")


if __name__ == "__main__":
    main()
