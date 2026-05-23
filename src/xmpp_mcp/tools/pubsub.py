"""Pubsub + data-form MCP tools.

Every tool exchanges Python dicts only — the AI never sees raw XML. The
underlying form shape is :class:`xmpp_mcp.data_forms.DataForm`, the same
representation used for node config and subscription options.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from ..data_forms import DataForm, FormField
from ..pubsub import PubSubClient
from ..xmpp_client import XMPPError
from . import get_xmpp


def _ps(ctx: Context) -> PubSubClient:
    return get_xmpp(ctx).pubsub


def _wrap(label: str, func: Any) -> Any:
    """Decorator helper turning XMPPError into a clean ToolError."""

    async def runner(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except XMPPError as exc:
            raise ToolError(f"{label}: {exc}") from exc

    runner.__name__ = label
    return runner


def register(mcp: FastMCP) -> None:  # noqa: C901 — flat tool registration
    """Register every pubsub + form tool on the FastMCP app."""

    # --- nodes ---------------------------------------------------------

    @mcp.tool
    async def pubsub_list_nodes(
        ctx: Context,
        service: Annotated[
            str, Field(description="Pubsub service JID, e.g. pubsub.xmpp.test")
        ],
    ) -> dict[str, Any]:
        """List the top-level nodes hosted by a pubsub service."""
        try:
            nodes = await _ps(ctx).list_nodes(service)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"service": service, "count": len(nodes), "nodes": nodes}

    @mcp.tool
    async def pubsub_create_node(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node name to create")],
        config_values: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "Optional `pubsub#*` config field values to apply at creation, "
                    "as a flat `{var: value}` map. Call `pubsub_get_node_config` "
                    "for the available fields."
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Create a new pubsub node, optionally with non-default configuration."""
        from ..data_forms import build_submit_form
        from ..pubsub import PUBSUB_NODE_CONFIG_URI

        config = (
            build_submit_form(config_values, form_type_uri=PUBSUB_NODE_CONFIG_URI)
            if config_values
            else None
        )
        try:
            await _ps(ctx).create_node(service, node, config=config)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"created": True, "service": service, "node": node}

    @mcp.tool
    async def pubsub_delete_node(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node to delete")],
    ) -> dict[str, Any]:
        """Delete a pubsub node (owner-only)."""
        try:
            await _ps(ctx).delete_node(service, node)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"deleted": True, "service": service, "node": node}

    @mcp.tool
    async def pubsub_get_node_config(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node to inspect")],
    ) -> dict[str, Any]:
        """Fetch the current node configuration as a XEP-0004 form.

        The returned shape matches :class:`xmpp_mcp.data_forms.DataForm`
        (`type`, `title`, `instructions`, `fields[]`).
        """
        try:
            return dict(await _ps(ctx).get_node_config(service, node))
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    async def pubsub_configure_node(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node to configure")],
        values: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Flat `{var: value}` map matching field vars from "
                    "`pubsub_get_node_config` (e.g. `pubsub#title`)."
                )
            ),
        ],
    ) -> dict[str, Any]:
        """Submit a node configuration update."""
        try:
            await _ps(ctx).set_node_config(service, node, values)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"configured": True, "service": service, "node": node}

    # --- form items ----------------------------------------------------

    @mcp.tool
    async def pubsub_publish_form_template(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node to publish to")],
        fields: Annotated[
            list[FormField],
            Field(
                description=(
                    "Field definitions for the form template. Each entry is a "
                    "dict with `var`, `type`, optional `label`, `required`, "
                    "`description`, `options`, and default `value`."
                )
            ),
        ],
        title: Annotated[
            str | None, Field(description="Optional form title")
        ] = None,
        instructions: Annotated[
            str | None, Field(description="Optional form instructions")
        ] = None,
        item_id: Annotated[
            str | None,
            Field(description="Optional item id; server may assign one if omitted"),
        ] = None,
    ) -> dict[str, Any]:
        """Publish a form template (a `type=form` data form) as a pubsub item.

        Subscribers receive the form; another party can later submit values
        against it via `pubsub_submit_form`.
        """
        try:
            published_id = await _ps(ctx).publish_form_template(
                service, node, fields,
                title=title, instructions=instructions, item_id=item_id,
            )
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {
            "published": True,
            "service": service,
            "node": node,
            "item_id": published_id,
        }

    @mcp.tool
    async def pubsub_submit_form(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node to publish the submission to")],
        values: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "`{var: value}` map matching the template's field vars. "
                    "Bools, lists, and strings are auto-typed; pass everything "
                    "else as strings."
                )
            ),
        ],
        item_id: Annotated[
            str | None, Field(description="Optional item id for the submission")
        ] = None,
        form_type_uri: Annotated[
            str | None,
            Field(
                description=(
                    "Optional XEP-0068 FORM_TYPE URI matching the template's "
                    "domain (e.g. an application-specific URI)."
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Submit values against a form (publishes a `type=submit` form item)."""
        try:
            published_id = await _ps(ctx).submit_form(
                service, node, values,
                item_id=item_id, form_type_uri=form_type_uri,
            )
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {
            "submitted": True,
            "service": service,
            "node": node,
            "item_id": published_id,
        }

    @mcp.tool
    async def pubsub_read_forms(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node to read items from")],
        max_items: Annotated[
            int, Field(description="Maximum items to fetch", ge=1, le=200)
        ] = 10,
    ) -> dict[str, Any]:
        """Fetch items from a node and parse each as a XEP-0004 data form.

        Non-form items are skipped. Each result has `id` and the parsed `form`.
        """
        try:
            results = await _ps(ctx).read_forms(service, node, max_items=max_items)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {
            "service": service, "node": node,
            "count": len(results), "items": results,
        }

    # --- raw (non-form) payloads ---------------------------------------

    @mcp.tool
    async def pubsub_publish_raw(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node to publish to")],
        payload_xml: Annotated[
            str,
            Field(
                description=(
                    "Raw XML payload as a string. Use this for non-form items "
                    "(PEP avatars, ATOM, JSON-in-pubsub, etc.). Use "
                    "pubsub_publish_form_template / pubsub_submit_form for "
                    "XEP-0004 forms."
                )
            ),
        ],
        item_id: Annotated[
            str | None,
            Field(description="Optional item id; server may assign one if omitted"),
        ] = None,
    ) -> dict[str, Any]:
        """Publish an arbitrary XML payload as a pubsub item."""
        try:
            published_id = await _ps(ctx).publish_raw(
                service, node, payload_xml, item_id=item_id
            )
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {
            "published": True, "service": service, "node": node,
            "item_id": published_id,
        }

    @mcp.tool
    async def pubsub_get_items_raw(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node to read items from")],
        max_items: Annotated[
            int, Field(description="Maximum items to fetch", ge=1, le=200)
        ] = 10,
        item_ids: Annotated[
            list[str] | None,
            Field(description="Optional list of specific item IDs to fetch"),
        ] = None,
    ) -> dict[str, Any]:
        """Fetch items with raw XML payloads (no form parsing)."""
        try:
            items = await _ps(ctx).get_items(
                service, node, max_items=max_items, item_ids=item_ids
            )
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"service": service, "node": node, "count": len(items), "items": items}

    @mcp.tool
    async def pubsub_get_item(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node holding the item")],
        item_id: Annotated[str, Field(description="ID of the item to fetch")],
    ) -> dict[str, Any]:
        """Fetch a single item by id.

        If the payload is a XEP-0004 data form, the response carries `form`;
        otherwise it carries `payload_xml` (a string).
        """
        try:
            return await _ps(ctx).get_item(service, node, item_id)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    async def pubsub_purge_node(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node to purge")],
    ) -> dict[str, Any]:
        """Delete every item on a node, keeping the node itself (owner-only)."""
        try:
            await _ps(ctx).purge_node(service, node)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"purged": True, "service": service, "node": node}

    # --- live notifications --------------------------------------------

    @mcp.tool
    def pubsub_get_recent_events(
        ctx: Context,
        node: Annotated[
            str | None,
            Field(description="Filter to events from one node (bare node name)"),
        ] = None,
        kind: Annotated[
            str | None,
            Field(
                description=(
                    "Filter to one event kind: publish / retract / purge / "
                    "delete / config / subscription."
                )
            ),
        ] = None,
        limit: Annotated[
            int, Field(description="Maximum events to return", ge=1, le=500)
        ] = 50,
    ) -> dict[str, Any]:
        """Drain buffered pubsub notifications.

        Items remain in the buffer until they're matched by a filter that
        returns them. Use this to react to publish/retract events on nodes the
        bot is subscribed to.
        """
        events = get_xmpp(ctx).drain_pubsub_events(node=node, kind=kind, limit=limit)
        return {"count": len(events), "events": events}

    @mcp.tool
    async def pubsub_retract_item(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node holding the item")],
        item_id: Annotated[str, Field(description="ID of the item to retract")],
        notify: Annotated[
            bool, Field(description="Send retraction notification to subscribers")
        ] = False,
    ) -> dict[str, Any]:
        """Delete a single item from a pubsub node."""
        try:
            await _ps(ctx).retract_item(service, node, item_id, notify=notify)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"retracted": True, "service": service, "node": node, "item_id": item_id}

    # --- subscriptions -------------------------------------------------

    @mcp.tool
    async def pubsub_subscribe(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node to subscribe to")],
    ) -> dict[str, Any]:
        """Subscribe the bot to a node."""
        try:
            return await _ps(ctx).subscribe(service, node)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    async def pubsub_unsubscribe(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node to unsubscribe from")],
        subid: Annotated[
            str | None,
            Field(description="Specific subscription id (only needed with multiple)"),
        ] = None,
    ) -> dict[str, Any]:
        """Unsubscribe the bot from a node."""
        try:
            await _ps(ctx).unsubscribe(service, node, subid=subid)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"unsubscribed": True, "service": service, "node": node}

    @mcp.tool
    async def pubsub_list_subscriptions(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[
            str | None,
            Field(description="Optional node to restrict the listing to"),
        ] = None,
    ) -> dict[str, Any]:
        """List the bot's own subscriptions on a pubsub service."""
        try:
            subs = await _ps(ctx).list_subscriptions(service, node=node)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"service": service, "count": len(subs), "subscriptions": subs}

    @mcp.tool
    async def pubsub_list_node_subscriptions(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node to inspect (owner only)")],
    ) -> dict[str, Any]:
        """List every subscriber of a node (owner-only)."""
        try:
            subs = await _ps(ctx).list_node_subscriptions(service, node)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"service": service, "node": node, "subscriptions": subs}

    @mcp.tool
    async def pubsub_get_subscription_options(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node holding the subscription")],
        user_jid: Annotated[
            str | None,
            Field(
                description="Subscriber JID; defaults to the bot's own bare JID."
            ),
        ] = None,
        defaults: Annotated[
            bool,
            Field(
                description=(
                    "If true, fetch the service-/node-wide default subscription "
                    "options instead of a specific subscriber's options."
                )
            ),
        ] = False,
        subid: Annotated[
            str | None,
            Field(
                description=(
                    "Subscription id. Openfire requires it when the subscription "
                    "has one; omit to auto-resolve the bot's own subid. Get it "
                    "from pubsub_subscribe / pubsub_list_subscriptions."
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Fetch a subscription's options form (or the service-wide defaults).

        The returned shape matches :class:`xmpp_mcp.data_forms.DataForm`.
        """
        try:
            return dict(
                await _ps(ctx).get_subscription_options(
                    service, node, user_jid=user_jid, defaults=defaults,
                    subid=subid,
                )
            )
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    async def pubsub_set_subscription_options(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node holding the subscription")],
        values: Annotated[
            dict[str, Any],
            Field(description="`{var: value}` of subscription option fields"),
        ],
        user_jid: Annotated[
            str | None,
            Field(description="Subscriber JID; defaults to the bot's own bare JID."),
        ] = None,
        subid: Annotated[
            str | None,
            Field(
                description=(
                    "Subscription id. Openfire requires it when the subscription "
                    "has one; omit to auto-resolve the bot's own subid. Get it "
                    "from pubsub_subscribe / pubsub_list_subscriptions."
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Update a subscription's options."""
        try:
            await _ps(ctx).set_subscription_options(
                service, node, values, user_jid=user_jid, subid=subid
            )
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"updated": True, "service": service, "node": node}

    # --- affiliations --------------------------------------------------

    @mcp.tool
    async def pubsub_list_my_affiliations(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[
            str | None, Field(description="Optional node to restrict to")
        ] = None,
    ) -> dict[str, Any]:
        """List the bot's affiliations across nodes (or one specific node)."""
        try:
            affs = await _ps(ctx).list_my_affiliations(service, node=node)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"service": service, "affiliations": affs}

    @mcp.tool
    async def pubsub_list_node_affiliations(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node to inspect (owner only)")],
    ) -> dict[str, Any]:
        """List every JID's affiliation on a node (owner-only)."""
        try:
            affs = await _ps(ctx).list_node_affiliations(service, node)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"service": service, "node": node, "affiliations": affs}

    @mcp.tool
    async def pubsub_set_affiliations(
        ctx: Context,
        service: Annotated[str, Field(description="Pubsub service JID")],
        node: Annotated[str, Field(description="Node to update (owner only)")],
        affiliations: Annotated[
            list[dict[str, str]],
            Field(
                description=(
                    "List of `{jid, affiliation}` where affiliation is one of "
                    "`owner`, `publisher`, `publish-only`, `member`, `none`, "
                    "`outcast`."
                )
            ),
        ],
    ) -> dict[str, Any]:
        """Assign affiliations on a node."""
        try:
            await _ps(ctx).set_affiliations(service, node, affiliations)
        except XMPPError as exc:
            raise ToolError(str(exc)) from exc
        return {"updated": True, "service": service, "node": node}
