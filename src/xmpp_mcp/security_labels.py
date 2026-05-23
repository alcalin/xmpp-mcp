"""XEP-0258 Security Labels — the Isode M-Link support layer.

M-Link's defining feature is clearance-based message access control via XEP-0258
security labels. slixmpp ships no XEP-0258 plugin, so this module implements the
two pieces the MCP tools need:

* :func:`fetch_catalog` — query a destination's label catalog (the set of labels
  a sender may apply) and cache the exact ``<securitylabel/>`` elements.
* :func:`build_label` — produce a ready-to-attach ``<securitylabel/>`` element
  for a previously discovered selector, so outgoing messages carry a *real*
  catalog label rather than a hand-rolled one.

The label payload (``<label/>`` child — ESS or Isode XML) is treated as opaque:
we round-trip the server's own element verbatim, which is what M-Link expects.
"""

from __future__ import annotations

import copy
import logging
from typing import Any
from xml.etree import ElementTree as etree

from slixmpp.exceptions import IqError, IqTimeout

logger = logging.getLogger("xmpp_mcp.seclabel")

SEC_LABEL_NS = "urn:xmpp:sec-label:0"
CATALOG_NS = "urn:xmpp:sec-label:catalog:2"

# destination bare JID -> {selector: <securitylabel> lxml element}
_catalog_cache: dict[str, dict[str, Any]] = {}


class SecurityLabelError(RuntimeError):
    """Raised for XEP-0258 catalog/label problems worth surfacing to the caller."""


def _qn(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _bare(jid: str) -> str:
    return jid.split("/", 1)[0]


def supports_security_labels(features: list[str]) -> bool:
    """True if a disco#info feature list advertises XEP-0258 support."""
    return SEC_LABEL_NS in features or CATALOG_NS in features


async def fetch_catalog(
    xmpp: Any,
    to: str,
    via: str | None = None,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """Query the XEP-0258 label catalog for ``to`` and cache the result.

    Returns a list of ``{selector, display_marking, fg_color, bg_color}`` dicts.
    The raw ``<securitylabel/>`` elements are cached so :func:`build_label` can
    reattach the exact element the server offered.

    ``via`` is the JID of the catalog service (XEP-0258 §6.1). When omitted,
    the request is sent to the destination's server domain — that's how M-Link
    deployments work, since M-Link *is* the server. Pass an explicit ``via``
    when a separate catalog component runs on a different JID.
    """
    dest = _bare(to)
    target_service = via or dest.split("@")[-1]
    iq = xmpp.make_iq_get(ito=target_service)
    catalog_req = etree.Element(_qn(CATALOG_NS, "catalog"))
    catalog_req.set("to", dest)
    iq.appendxml(catalog_req)

    try:
        resp = await iq.send(timeout=timeout)
    except (IqError, IqTimeout) as exc:
        raise SecurityLabelError(
            f"Security label catalog request for {dest} failed: {exc}. "
            "The server may not support XEP-0258."
        ) from exc

    catalog_el = resp.xml.find(_qn(CATALOG_NS, "catalog"))
    if catalog_el is None:
        raise SecurityLabelError(f"No <catalog/> element in the response from {dest}")

    items, by_selector = parse_catalog(catalog_el)
    _catalog_cache[dest] = by_selector
    logger.info("Cached %d security label(s) for %s", len(by_selector), dest)
    return items


def parse_catalog(catalog_el: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse a ``<catalog/>`` element into display items and a selector->element map.

    Pure function (no network, no cache) so it can be unit-tested directly.
    """
    items: list[dict[str, Any]] = []
    by_selector: dict[str, Any] = {}
    for index, item in enumerate(catalog_el.findall(_qn(CATALOG_NS, "item"))):
        seclabel = item.find(_qn(SEC_LABEL_NS, "securitylabel"))
        if seclabel is None:
            continue
        dm = seclabel.find(_qn(SEC_LABEL_NS, "displaymarking"))
        display = (dm.text or "").strip() if dm is not None and dm.text else ""
        selector = item.get("selector") or display or str(index)
        by_selector[selector] = copy.deepcopy(seclabel)
        items.append(
            {
                "selector": selector,
                "display_marking": display,
                "fg_color": dm.get("fgcolor") if dm is not None else None,
                "bg_color": dm.get("bgcolor") if dm is not None else None,
            }
        )
    return items, by_selector


def build_label(to: str, selector: str) -> Any:
    """Return a raw ``<securitylabel/>`` lxml element for a cached selector.

    Requires :func:`fetch_catalog` to have been called for the destination first
    — this guarantees the message carries a label the server actually offered.
    """
    dest = _bare(to)
    labels = _catalog_cache.get(dest)
    if not labels:
        raise SecurityLabelError(
            f"No security label catalog cached for {dest}. "
            "Call list_security_labels for this destination first."
        )
    element = labels.get(selector)
    if element is None:
        raise SecurityLabelError(
            f"Unknown security label selector {selector!r} for {dest}. "
            f"Known selectors: {sorted(labels)}"
        )
    return copy.deepcopy(element)
