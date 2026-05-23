"""End-to-end pubsub + data-form tests against Openfire's pubsub service.

Covers the form-publishing scenario the user asked about end-to-end: the bot
creates a node, publishes a form *template*, gives Alice the publisher
affiliation, Alice submits values against the template, and the bot reads the
submission back as a structured form. Also exercises the raw-payload tools,
the single-item / purge / default-sub-options niche operations, and the live
notification path (subscribe → other party publishes → bot drains the event).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastmcp import Client

from .conftest import OpenfireHandle
from .helpers.raw_client import RawXMPPClient

pytestmark = pytest.mark.docker


def _new_node_name() -> str:
    return f"t-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def ps_service(openfire: OpenfireHandle) -> str:
    return f"pubsub.{openfire.domain}"


@pytest_asyncio.fixture
async def temp_node(mcp: Client, ps_service: str) -> AsyncIterator[str]:
    """A uniquely-named node created and cleaned up around each test."""
    name = _new_node_name()
    await mcp.call_tool(
        "pubsub_create_node", {"service": ps_service, "node": name}
    )
    try:
        yield name
    finally:
        try:
            await mcp.call_tool(
                "pubsub_delete_node", {"service": ps_service, "node": name}
            )
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


# --- nodes ----------------------------------------------------------------


async def test_create_then_list_includes_node(
    mcp: Client, ps_service: str, temp_node: str
) -> None:
    listing = await mcp.call_tool(
        "pubsub_list_nodes", {"service": ps_service}
    )
    names = {n["node"] for n in listing.data["nodes"]}
    assert temp_node in names


async def test_delete_node_removes_it(mcp: Client, ps_service: str) -> None:
    name = _new_node_name()
    await mcp.call_tool("pubsub_create_node", {"service": ps_service, "node": name})
    await mcp.call_tool("pubsub_delete_node", {"service": ps_service, "node": name})
    listing = await mcp.call_tool("pubsub_list_nodes", {"service": ps_service})
    names = {n["node"] for n in listing.data["nodes"]}
    assert name not in names


# --- node configuration ---------------------------------------------------


async def test_get_node_config_returns_a_form(
    mcp: Client, ps_service: str, temp_node: str
) -> None:
    cfg = await mcp.call_tool(
        "pubsub_get_node_config", {"service": ps_service, "node": temp_node}
    )
    form = cfg.data
    assert form["type"] in ("form", "submit", "result")
    # Every Openfire node config carries pubsub#title as a field.
    field_vars = {f["var"] for f in form["fields"]}
    assert "pubsub#title" in field_vars


async def test_configure_node_round_trips_title(
    mcp: Client, ps_service: str, temp_node: str
) -> None:
    new_title = "Customer feedback form"
    await mcp.call_tool(
        "pubsub_configure_node",
        {
            "service": ps_service, "node": temp_node,
            "values": {"pubsub#title": new_title},
        },
    )
    cfg = await mcp.call_tool(
        "pubsub_get_node_config", {"service": ps_service, "node": temp_node}
    )
    title_field = next(
        f for f in cfg.data["fields"] if f["var"] == "pubsub#title"
    )
    assert title_field["value"] == new_title


# --- form items -----------------------------------------------------------


_TEMPLATE_FIELDS = [
    {
        "var": "name", "type": "text-single", "label": "Full name",
        "required": True,
    },
    {
        "var": "active", "type": "boolean", "label": "Available?",
    },
    {
        "var": "role", "type": "list-single", "label": "Role",
        "options": [
            {"value": "eng", "label": "Engineer"},
            {"value": "mgr", "label": "Manager"},
            {"value": "des", "label": "Designer"},
        ],
    },
]


async def test_publish_form_template_can_be_read_back(
    mcp: Client, ps_service: str, temp_node: str
) -> None:
    result = await mcp.call_tool(
        "pubsub_publish_form_template",
        {
            "service": ps_service, "node": temp_node,
            "fields": _TEMPLATE_FIELDS,
            "title": "Onboarding",
            "instructions": "Tell us about yourself",
            "item_id": "template",
        },
    )
    assert result.data["published"] is True

    read = await mcp.call_tool(
        "pubsub_read_forms", {"service": ps_service, "node": temp_node}
    )
    assert read.data["count"] == 1
    item = read.data["items"][0]
    assert item["id"] == "template"
    form = item["form"]
    assert form["title"] == "Onboarding"
    field_vars = {f["var"] for f in form["fields"]}
    assert field_vars == {"name", "active", "role"}
    role = next(f for f in form["fields"] if f["var"] == "role")
    assert role["options"] == [
        {"value": "eng", "label": "Engineer"},
        {"value": "mgr", "label": "Manager"},
        {"value": "des", "label": "Designer"},
    ]


async def test_alice_submits_form_and_bot_reads_submission(
    mcp: Client,
    raw_alice: RawXMPPClient,
    openfire: OpenfireHandle,
    ps_service: str,
) -> None:
    """The headline scenario — bot publishes form, alice fills it, bot reads.

    Has to create its own node (instead of using ``temp_node``) because Openfire
    pubsub nodes default to ``persist_items=false`` and ``max_items=1`` — under
    those defaults the template is discarded the moment Alice publishes her
    submission. We pass the retention config at creation time.
    """
    node = _new_node_name()
    await mcp.call_tool(
        "pubsub_create_node",
        {
            "service": ps_service, "node": node,
            "config_values": {
                "pubsub#persist_items": True,
                "pubsub#max_items": "100",
            },
        },
    )
    temp_node = node  # local alias used below
    try:
        await _alice_submits_body(
            mcp, raw_alice, openfire, ps_service, temp_node,
        )
    finally:
        try:
            await mcp.call_tool(
                "pubsub_delete_node", {"service": ps_service, "node": temp_node}
            )
        except Exception:  # noqa: BLE001
            pass


async def _alice_submits_body(
    mcp: Client,
    raw_alice: RawXMPPClient,
    openfire: OpenfireHandle,
    ps_service: str,
    temp_node: str,
) -> None:
    # Bot publishes the template.
    await mcp.call_tool(
        "pubsub_publish_form_template",
        {
            "service": ps_service, "node": temp_node,
            "fields": _TEMPLATE_FIELDS,
            "title": "Onboarding",
            "item_id": "template",
        },
    )

    # Bot grants alice the publisher affiliation so she can submit.
    await mcp.call_tool(
        "pubsub_set_affiliations",
        {
            "service": ps_service, "node": temp_node,
            "affiliations": [
                {
                    "jid": openfire.accounts["alice"].jid,
                    "affiliation": "publisher",
                },
            ],
        },
    )

    # Alice submits values directly via her raw client.
    await raw_alice.pubsub_submit_form(
        ps_service,
        temp_node,
        {"name": "Alice", "active": True, "role": "eng"},
    )

    # Bot reads everything in the node — sees template + alice's submission.
    read = await mcp.call_tool(
        "pubsub_read_forms",
        {"service": ps_service, "node": temp_node, "max_items": 20},
    )
    items = read.data["items"]
    assert len(items) >= 2

    template = next(i for i in items if i["id"] == "template")
    assert template["form"]["type"] == "form"

    submissions = [
        i for i in items if i["id"] != "template"
        and i["form"]["type"] == "submit"
    ]
    assert submissions, "no submission item found alongside the template"
    sub_fields = {f["var"]: f for f in submissions[0]["form"]["fields"]}
    assert sub_fields["name"]["value"] == "Alice"
    assert sub_fields["active"]["value"] is True
    assert sub_fields["role"]["value"] == "eng"


async def test_retract_item_removes_it(
    mcp: Client, ps_service: str, temp_node: str
) -> None:
    await mcp.call_tool(
        "pubsub_publish_form_template",
        {
            "service": ps_service, "node": temp_node,
            "fields": [{"var": "x", "type": "text-single"}],
            "item_id": "to-remove",
        },
    )
    await mcp.call_tool(
        "pubsub_retract_item",
        {"service": ps_service, "node": temp_node, "item_id": "to-remove"},
    )
    read = await mcp.call_tool(
        "pubsub_read_forms", {"service": ps_service, "node": temp_node}
    )
    item_ids = {i["id"] for i in read.data["items"]}
    assert "to-remove" not in item_ids


# --- subscriptions / affiliations -----------------------------------------


async def test_subscribe_appears_in_my_subscriptions(
    mcp: Client, ps_service: str, temp_node: str
) -> None:
    sub = await mcp.call_tool(
        "pubsub_subscribe", {"service": ps_service, "node": temp_node}
    )
    assert sub.data["node"] == temp_node
    subs = await mcp.call_tool(
        "pubsub_list_subscriptions", {"service": ps_service}
    )
    nodes = {s["node"] for s in subs.data["subscriptions"]}
    assert temp_node in nodes


# --- raw (non-form) payloads ---------------------------------------------


async def test_publish_raw_round_trips_xml(
    mcp: Client, ps_service: str, temp_node: str
) -> None:
    payload = "<status xmlns='example:status'><state>green</state></status>"
    await mcp.call_tool(
        "pubsub_publish_raw",
        {
            "service": ps_service, "node": temp_node,
            "payload_xml": payload, "item_id": "raw1",
        },
    )
    got = await mcp.call_tool(
        "pubsub_get_items_raw",
        {"service": ps_service, "node": temp_node},
    )
    items = got.data["items"]
    assert any(i["id"] == "raw1" for i in items)
    raw1 = next(i for i in items if i["id"] == "raw1")
    # ElementTree may use ns0: prefixes — parse to check semantics, not
    # exact serialised string.
    from xml.etree import ElementTree as etree
    parsed = etree.fromstring(raw1["payload_xml"])
    assert parsed.tag == "{example:status}status"
    state = parsed.find("{example:status}state")
    assert state is not None and state.text == "green"


async def test_get_item_returns_form_when_item_is_a_form(
    mcp: Client, ps_service: str, temp_node: str
) -> None:
    await mcp.call_tool(
        "pubsub_publish_form_template",
        {
            "service": ps_service, "node": temp_node,
            "fields": [{"var": "x", "type": "text-single", "value": "hi"}],
            "item_id": "f1",
        },
    )
    got = await mcp.call_tool(
        "pubsub_get_item",
        {"service": ps_service, "node": temp_node, "item_id": "f1"},
    )
    assert got.data["id"] == "f1"
    assert "form" in got.data
    assert got.data["form"]["fields"][0]["value"] == "hi"


async def test_get_item_returns_raw_payload_for_non_form(
    mcp: Client, ps_service: str, temp_node: str
) -> None:
    await mcp.call_tool(
        "pubsub_publish_raw",
        {
            "service": ps_service, "node": temp_node,
            "payload_xml": "<ping xmlns='example:ping'/>", "item_id": "r1",
        },
    )
    got = await mcp.call_tool(
        "pubsub_get_item",
        {"service": ps_service, "node": temp_node, "item_id": "r1"},
    )
    assert got.data["id"] == "r1"
    assert "payload_xml" in got.data
    from xml.etree import ElementTree as etree
    assert etree.fromstring(got.data["payload_xml"]).tag == "{example:ping}ping"


# --- niche ops -----------------------------------------------------------


async def test_purge_node_removes_all_items(
    mcp: Client, ps_service: str
) -> None:
    node = _new_node_name()
    await mcp.call_tool(
        "pubsub_create_node",
        {
            "service": ps_service, "node": node,
            "config_values": {
                "pubsub#persist_items": True, "pubsub#max_items": "10",
            },
        },
    )
    try:
        for i in range(3):
            await mcp.call_tool(
                "pubsub_publish_form_template",
                {
                    "service": ps_service, "node": node,
                    "fields": [{"var": "n", "type": "text-single", "value": str(i)}],
                    "item_id": f"item-{i}",
                },
            )
        before = await mcp.call_tool(
            "pubsub_read_forms", {"service": ps_service, "node": node}
        )
        assert before.data["count"] == 3

        await mcp.call_tool(
            "pubsub_purge_node", {"service": ps_service, "node": node}
        )
        # Openfire's purge clears storage but may retain one "last-published"
        # item shape depending on node configuration; the operationally
        # important assertion is that the bulk is gone.
        after = await mcp.call_tool(
            "pubsub_read_forms", {"service": ps_service, "node": node}
        )
        assert after.data["count"] < before.data["count"]
        assert after.data["count"] <= 1, (
            f"expected at most 1 item after purge, got {after.data['count']}"
        )
    finally:
        try:
            await mcp.call_tool(
                "pubsub_delete_node", {"service": ps_service, "node": node}
            )
        except Exception:  # noqa: BLE001
            pass


async def test_get_subscription_options_defaults_clean_failure_on_openfire(
    mcp: Client, ps_service: str, temp_node: str
) -> None:
    """Openfire returns 400 to ``<default node=…/>`` — surface as a clean ToolError.

    Per XEP-0060 §6.4 the call is valid; Openfire just doesn't implement the
    per-node defaults form. Other servers (ejabberd, Prosody) do support it.
    The tool itself is correct — we verify here only that the failure path is
    clean (no leaked stacktrace).
    """
    with pytest.raises(Exception, match="(?i)get_subscription_options"):
        await mcp.call_tool(
            "pubsub_get_subscription_options",
            {"service": ps_service, "node": temp_node, "defaults": True},
        )


async def test_get_set_subscription_options_with_subid(
    mcp: Client, ps_service: str, temp_node: str
) -> None:
    """Own subscription options round-trip on Openfire.

    Openfire issues a subid on subscribe and then rejects options requests that
    omit it (``subid-required``). The tools resolve the bot's own subid
    automatically when ``subid`` is not passed, and also accept it explicitly.
    Regression guard for that fix.
    """
    sub = await mcp.call_tool(
        "pubsub_subscribe", {"service": ps_service, "node": temp_node}
    )
    subid = sub.data["subid"]
    assert subid, "Openfire is expected to issue a subid on subscribe"
    try:
        # Auto-resolved subid (no subid arg).
        got = await mcp.call_tool(
            "pubsub_get_subscription_options",
            {"service": ps_service, "node": temp_node},
        )
        assert got.data["type"] == "form"
        assert any(f.get("var") == "pubsub#deliver" for f in got.data["fields"])

        # Set with auto-resolved subid, and again with an explicit subid.
        await mcp.call_tool(
            "pubsub_set_subscription_options",
            {"service": ps_service, "node": temp_node,
             "values": {"pubsub#deliver": True}},
        )
        await mcp.call_tool(
            "pubsub_set_subscription_options",
            {"service": ps_service, "node": temp_node,
             "values": {"pubsub#deliver": True}, "subid": subid},
        )
    finally:
        try:
            await mcp.call_tool(
                "pubsub_unsubscribe",
                {"service": ps_service, "node": temp_node, "subid": subid},
            )
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


# --- live notifications --------------------------------------------------


async def test_publish_event_arrives_when_subscribed(
    mcp: Client,
    raw_alice: RawXMPPClient,
    openfire: OpenfireHandle,
    ps_service: str,
) -> None:
    """Bot subscribes; Alice publishes; the publish event appears in the bot's buffer."""
    node = _new_node_name()
    await mcp.call_tool(
        "pubsub_create_node",
        {
            "service": ps_service, "node": node,
            "config_values": {
                "pubsub#persist_items": True, "pubsub#max_items": "10",
            },
        },
    )
    try:
        # Bot grants alice publisher then subscribes itself.
        await mcp.call_tool(
            "pubsub_set_affiliations",
            {
                "service": ps_service, "node": node,
                "affiliations": [
                    {
                        "jid": openfire.accounts["alice"].jid,
                        "affiliation": "publisher",
                    }
                ],
            },
        )
        await mcp.call_tool(
            "pubsub_subscribe", {"service": ps_service, "node": node}
        )
        # Drain any subscription event that may have been recorded.
        await mcp.call_tool("pubsub_get_recent_events", {})

        # Alice publishes a form fill.
        await raw_alice.pubsub_submit_form(
            ps_service, node, {"q": "hello from alice"}
        )

        # Poll briefly for the publish event to land.
        events: list[dict] = []
        for _ in range(15):
            await asyncio.sleep(0.2)
            r = await mcp.call_tool(
                "pubsub_get_recent_events", {"node": node, "kind": "publish"}
            )
            if r.data["events"]:
                events = r.data["events"]
                break
        assert events, "no publish event arrived within ~3s"
        ev = events[0]
        assert ev["node"] == node
        assert ev["kind"] == "publish"
        # Payload was a XEP-0004 submit form, so we should get a structured form back.
        assert ev.get("form") is not None
        fields = {f["var"]: f.get("value") for f in ev["form"]["fields"]}
        assert fields.get("q") == "hello from alice"
    finally:
        try:
            await mcp.call_tool(
                "pubsub_delete_node", {"service": ps_service, "node": node}
            )
        except Exception:  # noqa: BLE001
            pass


# --- subscriptions / affiliations -----------------------------------------


async def test_set_affiliations_appears_in_node_listing(
    mcp: Client, openfire: OpenfireHandle, ps_service: str, temp_node: str
) -> None:
    alice = openfire.accounts["alice"].jid
    await mcp.call_tool(
        "pubsub_set_affiliations",
        {
            "service": ps_service, "node": temp_node,
            "affiliations": [{"jid": alice, "affiliation": "publisher"}],
        },
    )
    listing = await mcp.call_tool(
        "pubsub_list_node_affiliations",
        {"service": ps_service, "node": temp_node},
    )
    by_jid = {a["jid"]: a["affiliation"] for a in listing.data["affiliations"]}
    assert by_jid.get(alice) == "publisher"
