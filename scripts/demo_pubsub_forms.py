"""End-to-end demo: create a pubsub form template, have several people submit
to it, then read everything back through the MCP server.

Same lab scaffolding as scripts/demo_chat_search.py — bring Openfire up,
prime the REST API plugin, then drive the bot through the FastMCP in-process
client. Three raw XMPP clients (alice/bob/carol) act as form respondents.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))
sys.path.insert(0, str(HERE))

from tests.integration.conftest import (  # noqa: E402
    OpenfireHandle,
    _enable_restapi,
    _wait_for_http,
    _wait_for_rest_api,
    _wait_for_tcp,
)
from tests.integration.helpers.raw_client import RawXMPPClient  # noqa: E402

COMPOSE_DIR = HERE / "tests" / "integration" / "docker"

NODE = "weekly-status"
TEMPLATE_ITEM_ID = "template"

FORM_FIELDS = [
    {
        "var": "name", "type": "text-single", "label": "Your name",
        "required": True,
    },
    {
        "var": "team", "type": "list-single", "label": "Team",
        "options": [
            {"value": "backend", "label": "Backend"},
            {"value": "frontend", "label": "Frontend"},
            {"value": "devops", "label": "DevOps"},
            {"value": "design", "label": "Design"},
        ],
    },
    {
        "var": "blocked", "type": "boolean", "label": "Blocked on anything?",
    },
    {
        "var": "tags", "type": "list-multi", "label": "Areas this week",
        "options": [
            {"value": "deploys",  "label": "Deploys"},
            {"value": "incidents", "label": "Incidents"},
            {"value": "features", "label": "Features"},
            {"value": "research", "label": "Research"},
        ],
    },
    {
        "var": "notes", "type": "text-multi", "label": "Notes",
    },
]


SUBMISSIONS = {
    "alice": {
        "name": "Alice",
        "team": "backend",
        "blocked": False,
        "tags": ["deploys", "features"],
        "notes": "Shipped v2.1 to staging, finishing migration tomorrow.",
    },
    "bob": {
        "name": "Bob",
        "team": "frontend",
        "blocked": True,
        "tags": ["incidents", "features"],
        "notes": "Need design review on the dashboard; waiting on Carol.",
    },
    "carol": {
        "name": "Carol",
        "team": "design",
        "blocked": False,
        "tags": ["research"],
        "notes": "Auth fix landed, customer 500s resolved.",
    },
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


@asynccontextmanager
async def session(handle: OpenfireHandle):
    """Yield (mcp_client, raw_clients_by_name)."""
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
    print(f"\n=== {text} ===")


def show(title: str, value) -> None:
    print(f"\n>>> {title}")
    print(json.dumps(value, indent=2, default=str))


async def call(mcp, label: str, tool: str, args: dict):
    print(f"\n>>> {label}")
    print(f"    call: {tool}({args})")
    r = await mcp.call_tool(tool, args)
    print(f"    -> {json.dumps(r.data, indent=2, default=str)[:1400]}")
    return r.data


async def amain(handle: OpenfireHandle) -> None:
    service = f"pubsub.{handle.domain}"
    async with session(handle) as (mcp, raws):
        # Clean slate.
        try:
            await mcp.call_tool(
                "pubsub_delete_node", {"service": service, "node": NODE}
            )
        except Exception:  # noqa: BLE001
            pass

        banner("Create a persistent node for weekly status updates")
        await call(
            mcp, "create node with retention",
            "pubsub_create_node",
            {
                "service": service, "node": NODE,
                "config_values": {
                    "pubsub#persist_items": True,
                    "pubsub#max_items": "100",
                    "pubsub#title": "Weekly status updates",
                },
            },
        )

        banner("Confirm the node config stuck")
        cfg = await call(
            mcp, "get node config (showing pubsub#title only)",
            "pubsub_get_node_config", {"service": service, "node": NODE},
        )
        title_field = next(f for f in cfg["fields"] if f["var"] == "pubsub#title")
        print(f"    pubsub#title now: {title_field.get('value')!r}")

        banner("Grant alice/bob/carol the 'publisher' affiliation")
        await call(
            mcp, "set_affiliations",
            "pubsub_set_affiliations",
            {
                "service": service, "node": NODE,
                "affiliations": [
                    {"jid": handle.accounts[n].jid, "affiliation": "publisher"}
                    for n in ("alice", "bob", "carol")
                ],
            },
        )
        await call(
            mcp, "list_node_affiliations (verify)",
            "pubsub_list_node_affiliations",
            {"service": service, "node": NODE},
        )

        banner("Publish the form template")
        await call(
            mcp, "publish_form_template",
            "pubsub_publish_form_template",
            {
                "service": service, "node": NODE,
                "fields": FORM_FIELDS,
                "title": "Weekly status",
                "instructions": "Tell us where you are this week",
                "item_id": TEMPLATE_ITEM_ID,
            },
        )

        banner("Alice, Bob, Carol each submit a fill")
        for who in ("alice", "bob", "carol"):
            item_id = await raws[who].pubsub_submit_form(
                service, NODE, SUBMISSIONS[who]
            )
            print(f"    {who} submitted -> item_id={item_id!r}")
        await asyncio.sleep(0.4)  # let server flush items

        banner("Bot reads every form on the node")
        all_forms = await call(
            mcp, "read_forms",
            "pubsub_read_forms",
            {"service": service, "node": NODE, "max_items": 20},
        )

        # Pretty summary an AI would surface to a user.
        banner("Summary (the kind of answer Claude would give)")
        template = next(
            (i for i in all_forms["items"] if i["id"] == TEMPLATE_ITEM_ID), None
        )
        submissions = [
            i for i in all_forms["items"]
            if i["form"].get("type") == "submit"
        ]
        if template:
            t = template["form"]
            print(f"\n  Template ({t.get('title')}): {len(t['fields'])} fields — "
                  f"{', '.join(f['var'] for f in t['fields'])}")
        print(f"  {len(submissions)} submission(s):\n")
        for item in submissions:
            fields = {f["var"]: f.get("value") for f in item["form"]["fields"]}
            print(
                f"    • {fields.get('name')} ({fields.get('team')})"
                f"  blocked={fields.get('blocked')}"
                f"  tags={fields.get('tags')}"
            )
            if fields.get("notes"):
                print(f"      notes: {fields['notes']}")

        banner("Retract Bob's submission and re-read")
        bob_items = [
            i for i in submissions
            if any(f.get("var") == "name" and f.get("value") == "Bob"
                   for f in i["form"]["fields"])
        ]
        if bob_items:
            await call(
                mcp, "retract_item",
                "pubsub_retract_item",
                {"service": service, "node": NODE, "item_id": bob_items[0]["id"]},
            )
            after = await call(
                mcp, "read_forms after retract",
                "pubsub_read_forms",
                {"service": service, "node": NODE, "max_items": 20},
            )
            names_after = []
            for i in after["items"]:
                if i["form"].get("type") != "submit":
                    continue
                fields = {f["var"]: f.get("value") for f in i["form"]["fields"]}
                names_after.append(fields.get("name"))
            print(f"    submissions still present: {names_after}")

        banner("Node listing on the pubsub service")
        await call(
            mcp, "list_nodes",
            "pubsub_list_nodes", {"service": service},
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rebuild", action="store_true", help="rebuild the openfire image")
    p.add_argument("--down", action="store_true", help="docker compose down at exit")
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
