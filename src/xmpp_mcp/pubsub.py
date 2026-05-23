"""Pubsub (XEP-0060) helper layered on top of slixmpp's xep_0060 plugin.

Exposes async methods that operate on Python dicts — never raw XML — using
:mod:`xmpp_mcp.data_forms` for any field exchange. ``IqError`` /
``IqTimeout`` from slixmpp are converted to :class:`XMPPError` so tool code
can surface them as clean ``ToolError`` messages.

All "form" parameters and return values are :class:`xmpp_mcp.data_forms.DataForm`
shaped dicts. The MCP layer (``tools/pubsub.py``) is a thin wrapper over this.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from xml.etree import ElementTree as etree

from slixmpp.exceptions import IqError, IqTimeout
from slixmpp.plugins.xep_0004 import Form as SlixForm
from slixmpp.plugins.xep_0060.stanza import Item

from .data_forms import (
    NS as DATA_FORMS_NS,
    DataForm,
    FormField,
    build_form_element,
    build_submit_form,
    parse_form,
)
from .xmpp_client import XMPPError

logger = logging.getLogger("xmpp_mcp.pubsub")

PUBSUB_NODE_CONFIG_URI = "http://jabber.org/protocol/pubsub#node_config"
PUBSUB_SUBSCRIBE_OPTIONS_URI = "http://jabber.org/protocol/pubsub#subscribe_options"

# Hard wall-clock cap for any single pubsub IQ. slixmpp has its own default
# (~30s) but it can fail to fire if the connection wedges in a half-open
# state — see the 2026-05-19 hang where a stuck pubsub_subscribe blocked
# the MCP call for 2h+. This guarantees the wrapper returns within IQ_TIMEOUT.
IQ_TIMEOUT = 30.0


def _to_slix_form(form: DataForm) -> SlixForm:
    """Convert a :class:`DataForm` dict to a slixmpp :class:`Form` stanza."""
    sf = SlixForm()
    sf["type"] = form.get("type", "submit")
    if "title" in form:
        sf["title"] = form["title"]
    if "instructions" in form:
        sf["instructions"] = form["instructions"]
    for field in form.get("fields", []):
        options = (
            [{"value": opt.get("value", ""), "label": opt.get("label", "")}
             for opt in field.get("options", [])]
            if "options" in field else None
        )
        sf.add_field(
            var=field.get("var", ""),
            ftype=field.get("type"),
            label=field.get("label", ""),
            desc=field.get("description", ""),
            required=field.get("required", False),
            value=field.get("value"),
            options=options,
        )
    return sf


def _form_from_xml_subtree(element: etree.Element | None) -> DataForm | None:
    """Find an ``<x xmlns='jabber:x:data'/>`` anywhere under ``element``."""
    if element is None:
        return None
    x = element if element.tag == f"{{{DATA_FORMS_NS}}}x" else element.find(
        f".//{{{DATA_FORMS_NS}}}x"
    )
    if x is None:
        return None
    return parse_form(x)


class PubSubClient:
    """Thin async wrapper around slixmpp's xep_0060 plugin."""

    def __init__(self, xmpp: Any) -> None:
        self._xmpp = xmpp
        # The plugin must already be registered by XMPPClient.
        self._plugin = xmpp.plugin["xep_0060"]
        self._disco = xmpp.plugin["xep_0030"]

    async def _await_iq(self, coro: Any, label: str) -> Any:
        """Await an IQ-returning slixmpp coroutine with a hard wall-clock cap.

        Converts ``asyncio.TimeoutError``, ``IqError``, and ``IqTimeout`` into
        :class:`XMPPError` so the MCP tool layer surfaces them uniformly.
        """
        try:
            return await asyncio.wait_for(coro, timeout=IQ_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise XMPPError(f"{label} timed out after {IQ_TIMEOUT}s") from exc
        except (IqError, IqTimeout) as exc:
            raise XMPPError(f"{label} failed: {exc}") from exc

    # --- nodes ----------------------------------------------------------

    async def list_nodes(self, service: str) -> list[dict[str, str]]:
        """Return all top-level nodes on a pubsub service."""
        iq = await self._await_iq(
            self._disco.get_items(jid=service),
            f"list_nodes({service!r})",
        )
        return [
            {"node": item[1] or "", "name": item[2] or ""}
            for item in iq["disco_items"]["items"]
        ]

    async def create_node(
        self,
        service: str,
        node: str,
        config: DataForm | None = None,
    ) -> None:
        slix_config = _to_slix_form(config) if config else None
        await self._await_iq(
            self._plugin.create_node(jid=service, node=node, config=slix_config),
            f"create_node({service!r}, {node!r})",
        )

    async def delete_node(self, service: str, node: str) -> None:
        await self._await_iq(
            self._plugin.delete_node(jid=service, node=node),
            f"delete_node({service!r}, {node!r})",
        )

    async def get_node_config(self, service: str, node: str) -> DataForm:
        iq = await self._await_iq(
            self._plugin.get_node_config(jid=service, node=node),
            f"get_node_config({service!r}, {node!r})",
        )
        form = _form_from_xml_subtree(iq.xml)
        if form is None:
            raise XMPPError(f"no <x/> in get_node_config response for {node!r}")
        return form

    async def set_node_config(
        self, service: str, node: str, values: dict[str, Any]
    ) -> None:
        submit = build_submit_form(values, form_type_uri=PUBSUB_NODE_CONFIG_URI)
        await self._await_iq(
            self._plugin.set_node_config(
                jid=service, node=node, config=_to_slix_form(submit)
            ),
            f"set_node_config({service!r}, {node!r})",
        )

    # --- items / forms --------------------------------------------------

    async def publish_form_template(
        self,
        service: str,
        node: str,
        fields: list[FormField],
        title: str | None = None,
        instructions: str | None = None,
        item_id: str | None = None,
    ) -> str:
        """Publish an item containing a ``type=form`` data form.

        Returns the item ID assigned by the service (which may differ from
        ``item_id`` if the service rewrites IDs).
        """
        form: DataForm = {"type": "form", "fields": fields}
        if title:
            form["title"] = title
        if instructions:
            form["instructions"] = instructions
        payload = build_form_element(form)
        return await self._publish_payload(service, node, payload, item_id=item_id)

    async def submit_form(
        self,
        service: str,
        node: str,
        values: dict[str, Any],
        item_id: str | None = None,
        form_type_uri: str | None = None,
    ) -> str:
        """Publish an item containing a ``type=submit`` data form built from ``values``."""
        form = build_submit_form(values, form_type_uri=form_type_uri)
        payload = build_form_element(form)
        return await self._publish_payload(service, node, payload, item_id=item_id)

    async def _publish_payload(
        self,
        service: str,
        node: str,
        payload: etree.Element,
        item_id: str | None,
    ) -> str:
        iq = await self._await_iq(
            self._plugin.publish(jid=service, node=node, id=item_id, payload=payload),
            f"publish to {service}/{node}",
        )
        # Service may echo back the (possibly server-assigned) id.
        try:
            return iq["pubsub"]["publish"]["item"]["id"] or item_id or ""
        except Exception:  # noqa: BLE001
            return item_id or ""

    async def read_forms(
        self, service: str, node: str, max_items: int | None = 10
    ) -> list[dict[str, Any]]:
        """Fetch items and parse each payload as a XEP-0004 data form.

        Returns a list of ``{"id": str, "form": DataForm}`` dicts; items whose
        payload is not a data form are skipped.
        """
        items = await self.get_items_raw(service, node, max_items=max_items)
        results: list[dict[str, Any]] = []
        for entry in items:
            form = _form_from_xml_subtree(entry["payload_xml"])
            if form is not None:
                results.append({"id": entry["id"], "form": form})
        return results

    async def get_items_raw(
        self,
        service: str,
        node: str,
        max_items: int | None = None,
        item_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch items and return ``[{"id": ..., "payload_xml": <Element>}, ...]``."""
        iq = await self._await_iq(
            self._plugin.get_items(
                jid=service, node=node, max_items=max_items, item_ids=item_ids
            ),
            f"get_items({node!r})",
        )
        results: list[dict[str, Any]] = []
        for item in iq["pubsub"]["items"]:
            item: Item  # type: ignore[no-redef]
            payload = item["payload"]
            results.append({"id": item["id"], "payload_xml": payload})
        return results

    async def publish_raw(
        self,
        service: str,
        node: str,
        payload_xml: str,
        item_id: str | None = None,
    ) -> str:
        """Publish an arbitrary (non-form) XML payload as a pubsub item."""
        try:
            payload = etree.fromstring(payload_xml)
        except etree.ParseError as exc:
            raise XMPPError(f"payload_xml is not well-formed: {exc}") from exc
        return await self._publish_payload(service, node, payload, item_id=item_id)

    async def get_items(
        self,
        service: str,
        node: str,
        max_items: int | None = None,
        item_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Like :meth:`get_items_raw` but with payloads serialised to XML strings."""
        raw = await self.get_items_raw(
            service, node, max_items=max_items, item_ids=item_ids
        )
        return [
            {
                "id": entry["id"],
                "payload_xml": (
                    etree.tostring(entry["payload_xml"], encoding="unicode")
                    if entry["payload_xml"] is not None else ""
                ),
            }
            for entry in raw
        ]

    async def get_item(
        self, service: str, node: str, item_id: str
    ) -> dict[str, Any]:
        """Fetch a single item by id.

        Returns ``{"id", "form"}`` when the payload is a XEP-0004 data form,
        otherwise ``{"id", "payload_xml"}`` (serialised XML string).
        """
        iq = await self._await_iq(
            self._plugin.get_item(jid=service, node=node, item_id=item_id),
            f"get_item({node!r}, {item_id!r})",
        )
        items = list(iq["pubsub"]["items"])
        if not items:
            raise XMPPError(f"item {item_id!r} not found on {node!r}")
        payload = items[0]["payload"]
        if payload is None:
            return {"id": item_id, "payload_xml": ""}
        form = _form_from_xml_subtree(payload)
        if form is not None:
            return {"id": item_id, "form": form}
        return {
            "id": item_id,
            "payload_xml": etree.tostring(payload, encoding="unicode"),
        }

    async def purge_node(self, service: str, node: str) -> None:
        """Delete every item on a node (owner-only)."""
        await self._await_iq(
            self._plugin.purge(jid=service, node=node),
            f"purge_node({service!r}, {node!r})",
        )

    async def retract_item(
        self, service: str, node: str, item_id: str, notify: bool = False
    ) -> None:
        await self._await_iq(
            self._plugin.retract(jid=service, node=node, id=item_id, notify=notify),
            f"retract_item({node!r}, {item_id!r})",
        )

    # --- subscriptions --------------------------------------------------

    async def subscribe(self, service: str, node: str) -> dict[str, str]:
        iq = await self._await_iq(
            self._plugin.subscribe(jid=service, node=node),
            f"subscribe({node!r})",
        )
        sub = iq["pubsub"]["subscription"]
        return {
            "node": sub["node"],
            "jid": str(sub["jid"]),
            "subscription": sub["subscription"],
            "subid": sub["subid"] or "",
        }

    async def unsubscribe(
        self, service: str, node: str, subid: str | None = None
    ) -> None:
        await self._await_iq(
            self._plugin.unsubscribe(jid=service, node=node, subid=subid),
            f"unsubscribe({node!r})",
        )

    async def list_subscriptions(
        self, service: str, node: str | None = None
    ) -> list[dict[str, str]]:
        """List **my** subscriptions on this service (optionally one node)."""
        iq = await self._await_iq(
            self._plugin.get_subscriptions(jid=service, node=node),
            "get_subscriptions",
        )
        results: list[dict[str, str]] = []
        for sub in iq["pubsub"]["subscriptions"]:
            results.append(
                {
                    "node": sub["node"],
                    "jid": str(sub["jid"]),
                    "subscription": sub["subscription"],
                    "subid": sub["subid"] or "",
                }
            )
        return results

    async def list_node_subscriptions(
        self, service: str, node: str
    ) -> list[dict[str, str]]:
        """List **all** subscriptions on a node (owner-only)."""
        iq = await self._await_iq(
            self._plugin.get_node_subscriptions(jid=service, node=node),
            f"get_node_subscriptions({node!r})",
        )
        results: list[dict[str, str]] = []
        for sub in iq["pubsub_owner"]["subscriptions"]:
            results.append(
                {
                    "jid": str(sub["jid"]),
                    "subscription": sub["subscription"],
                    "subid": sub["subid"] or "",
                }
            )
        return results

    async def _resolve_own_subid(
        self, service: str, node: str, user_jid: str
    ) -> str | None:
        """Best-effort lookup of the bot's own ``subid`` for a node.

        Openfire issues subscriptions *with* a subid and then rejects
        options requests that omit it (``subid-required``); ejabberd, Prosody
        and M-Link use bare subscriptions where no subid is needed. We can only
        enumerate our *own* subscriptions, so this returns ``None`` for any
        other ``user_jid`` (the caller must pass ``subid`` explicitly) and
        ``None`` when there isn't exactly one owned subscription carrying a
        subid — in which case the request goes out bare, which is correct for
        the servers that don't require one.
        """
        if user_jid != self._xmpp.boundjid.bare:
            return None
        try:
            # Listed without a node filter: on Openfire the node-scoped query
            # omits the subid we need, while the service-wide listing carries it.
            subs = await self.list_subscriptions(service)
        except XMPPError:
            return None
        sids = [s["subid"] for s in subs if s.get("node") == node and s.get("subid")]
        return sids[0] if len(sids) == 1 else None

    async def get_subscription_options(
        self,
        service: str,
        node: str,
        user_jid: str | None = None,
        defaults: bool = False,
        subid: str | None = None,
    ) -> DataForm:
        """Fetch subscription options form.

        With ``defaults=True``, returns the service-/node-wide default
        subscription options instead of a specific subscriber's options.
        Otherwise ``user_jid`` defaults to the bot's own bare JID. ``subid``
        is sent on the ``<options/>`` element; when omitted it is resolved
        automatically for the bot's own subscription (required by Openfire).
        """
        # Built by hand rather than via ``plugin.get_subscription_options``
        # because slixmpp's helper has no way to set the ``subid`` attribute
        # Openfire demands.
        iq = self._xmpp.Iq(sto=service, stype="get")
        if defaults:
            iq["pubsub"]["default"]["node"] = node
        else:
            target = user_jid or self._xmpp.boundjid.bare
            if subid is None:
                subid = await self._resolve_own_subid(service, node, target)
            iq["pubsub"]["options"]["node"] = node
            iq["pubsub"]["options"]["jid"] = target
            if subid:
                iq["pubsub"]["options"].xml.attrib["subid"] = subid
        resp = await self._await_iq(
            iq.send(), f"get_subscription_options({node!r})"
        )
        form = _form_from_xml_subtree(resp.xml)
        if form is None:
            raise XMPPError(
                f"no <x/> in subscription options response for {node!r}"
            )
        return form

    async def set_subscription_options(
        self,
        service: str,
        node: str,
        values: dict[str, Any],
        user_jid: str | None = None,
        subid: str | None = None,
    ) -> None:
        """Update subscription options.

        ``subid`` is sent on the ``<options/>`` element; when omitted it is
        resolved automatically for the bot's own subscription (required by
        Openfire, ignored by servers that use bare subscriptions).
        """
        target = user_jid or self._xmpp.boundjid.bare
        if subid is None:
            subid = await self._resolve_own_subid(service, node, target)
        submit = build_submit_form(
            values, form_type_uri=PUBSUB_SUBSCRIBE_OPTIONS_URI
        )
        # Hand-built (vs the slixmpp helper) for two reasons: to attach the
        # ``subid`` attribute, and to send it as an iq-``set`` (XEP-0060 §6.3.5)
        # rather than the iq-``get`` slixmpp's helper mistakenly uses.
        iq = self._xmpp.Iq(sto=service, stype="set")
        iq["pubsub"]["options"]["node"] = node
        iq["pubsub"]["options"]["jid"] = target
        if subid:
            iq["pubsub"]["options"].xml.attrib["subid"] = subid
        iq["pubsub"]["options"].append(_to_slix_form(submit))
        await self._await_iq(
            iq.send(), f"set_subscription_options({node!r})"
        )

    # --- affiliations ---------------------------------------------------

    async def list_my_affiliations(
        self, service: str, node: str | None = None
    ) -> list[dict[str, str]]:
        iq = await self._await_iq(
            self._plugin.get_affiliations(jid=service, node=node),
            "get_affiliations",
        )
        results: list[dict[str, str]] = []
        for aff in iq["pubsub"]["affiliations"]:
            results.append({"node": aff["node"], "affiliation": aff["affiliation"]})
        return results

    async def list_node_affiliations(
        self, service: str, node: str
    ) -> list[dict[str, str]]:
        iq = await self._await_iq(
            self._plugin.get_node_affiliations(jid=service, node=node),
            f"get_node_affiliations({node!r})",
        )
        results: list[dict[str, str]] = []
        for aff in iq["pubsub_owner"]["affiliations"]:
            results.append(
                {"jid": str(aff["jid"]), "affiliation": aff["affiliation"]}
            )
        return results

    async def set_affiliations(
        self, service: str, node: str, affiliations: list[dict[str, str]]
    ) -> None:
        """``affiliations``: ``[{"jid": "alice@xmpp.test", "affiliation": "publisher"}, ...]``."""
        pairs = [(item["jid"], item["affiliation"]) for item in affiliations]
        await self._await_iq(
            self._plugin.modify_affiliations(
                jid=service, node=node, affiliations=pairs
            ),
            f"modify_affiliations({node!r})",
        )
