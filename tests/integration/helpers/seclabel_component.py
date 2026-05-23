"""A stub XEP-0258 ('Security Labels in XMPP') catalog responder.

Stands in for Isode M-Link in integration tests. Connects to the lab Openfire
as an XEP-0114 external component on ``seclabel.xmpp.test``, advertises
XEP-0258 support over disco#info, and serves a hand-built ``<catalog/>``
in response to ``iq[catalog]`` requests.

Openfire must have external components enabled with the matching shared
secret (``component-secret``) on port 5275 — see ``_enable_restapi`` in
``conftest.py``.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any
from xml.etree import ElementTree as etree

from slixmpp import ComponentXMPP
from slixmpp.xmlstream.handler import CoroutineCallback
from slixmpp.xmlstream.matcher import MatchXPath

from xmpp_mcp.security_labels import CATALOG_NS, SEC_LABEL_NS

logger = logging.getLogger("xmpp_mcp.test.seclabel")

# A small, realistic XEP-0258 catalog with three clearance tiers. The exact
# `<securitylabel/>` elements echo back to the requester verbatim — that's
# what M-Link does and what xmpp_mcp.security_labels.build_label relies on.
_CATALOG_TEMPLATE = f"""
<catalog xmlns="{CATALOG_NS}" name="Test labels" desc="Stub for tests">
  <item selector="UNCLASSIFIED">
    <securitylabel xmlns="{SEC_LABEL_NS}">
      <displaymarking fgcolor="black" bgcolor="green">UNCLASSIFIED</displaymarking>
      <label>
        <esssecuritylabel xmlns="urn:xmpp:sec-label:ess:0">MQYBAQEBAQ==</esssecuritylabel>
      </label>
    </securitylabel>
  </item>
  <item selector="RESTRICTED">
    <securitylabel xmlns="{SEC_LABEL_NS}">
      <displaymarking fgcolor="black" bgcolor="yellow">RESTRICTED</displaymarking>
      <label>
        <esssecuritylabel xmlns="urn:xmpp:sec-label:ess:0">MQYBAQEBAg==</esssecuritylabel>
      </label>
    </securitylabel>
  </item>
  <item selector="SECRET">
    <securitylabel xmlns="{SEC_LABEL_NS}">
      <displaymarking fgcolor="white" bgcolor="red">SECRET</displaymarking>
      <label>
        <esssecuritylabel xmlns="urn:xmpp:sec-label:ess:0">MQYBAQEBAw==</esssecuritylabel>
      </label>
    </securitylabel>
  </item>
</catalog>
"""

DEFAULT_DOMAIN = "seclabel.xmpp.test"
DEFAULT_SECRET = "component-secret"


class SecurityLabelComponent(ComponentXMPP):
    """Component that answers XEP-0258 catalog queries with a static catalog."""

    def __init__(
        self,
        jid: str = DEFAULT_DOMAIN,
        secret: str = DEFAULT_SECRET,
        server: str = "127.0.0.1",
        port: int = 5275,
    ) -> None:
        super().__init__(jid, secret, server, port)
        self.register_plugin("xep_0030")
        self.add_event_handler("session_start", self._on_session_start)
        self.register_handler(
            CoroutineCallback(
                "SecLabel Catalog",
                MatchXPath(f"{{jabber:component:accept}}iq/{{{CATALOG_NS}}}catalog"),
                self._on_catalog,
            )
        )
        self._ready: asyncio.Future[bool] = (
            asyncio.get_running_loop().create_future()
        )

    async def _on_session_start(self, _event: Any) -> None:
        disco = self.plugin["xep_0030"]
        disco.add_identity(
            category="auth", itype="catalog", name="Test security label catalog"
        )
        for feat in (SEC_LABEL_NS, CATALOG_NS):
            disco.add_feature(feature=feat)
        if not self._ready.done():
            self._ready.set_result(True)
        logger.info("Security label component online at %s", self.boundjid.bare)

    async def _on_catalog(self, iq: Any) -> None:
        """Reply with a copy of the stub catalog, ``to`` echoed from the request."""
        request = iq.xml.find(f"{{{CATALOG_NS}}}catalog")
        requested_to = (
            request.get("to", "") if request is not None else ""
        )

        catalog_el = etree.fromstring(_CATALOG_TEMPLATE)
        if requested_to:
            catalog_el.set("to", requested_to)

        reply = iq.reply()
        # Clear any auto-built payload, then attach our hand-built catalog.
        for child in list(reply.xml):
            if child.tag.endswith("}catalog"):
                reply.xml.remove(child)
        reply.append_xml(copy.deepcopy(catalog_el)) if hasattr(reply, "append_xml") else reply.appendxml(catalog_el)
        reply.send()

    async def wait_ready(self, timeout: float = 15.0) -> None:
        await asyncio.wait_for(self._ready, timeout=timeout)
