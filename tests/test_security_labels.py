"""Tests for the XEP-0258 security label parsing and label builder."""

from __future__ import annotations

from xml.etree import ElementTree as etree

import pytest

from xmpp_mcp import security_labels as sl
from xmpp_mcp.security_labels import (
    CATALOG_NS,
    SEC_LABEL_NS,
    SecurityLabelError,
    build_label,
    parse_catalog,
    supports_security_labels,
)

_CATALOG_XML = f"""
<catalog xmlns="{CATALOG_NS}" to="room@conf.example.com" name="Default">
  <item selector="UNCLASSIFIED">
    <securitylabel xmlns="{SEC_LABEL_NS}">
      <displaymarking fgcolor="black" bgcolor="green">UNCLASSIFIED</displaymarking>
      <label><esssecuritylabel xmlns="urn:xmpp:sec-label:ess:0">aGVsbG8=</esssecuritylabel></label>
    </securitylabel>
  </item>
  <item selector="SECRET">
    <securitylabel xmlns="{SEC_LABEL_NS}">
      <displaymarking fgcolor="white" bgcolor="red">SECRET</displaymarking>
      <label><esssecuritylabel xmlns="urn:xmpp:sec-label:ess:0">d29ybGQ=</esssecuritylabel></label>
    </securitylabel>
  </item>
</catalog>
"""


def _catalog_element() -> etree.Element:
    return etree.fromstring(_CATALOG_XML)


def test_supports_security_labels() -> None:
    assert supports_security_labels([SEC_LABEL_NS]) is True
    assert supports_security_labels([CATALOG_NS]) is True
    assert supports_security_labels(["urn:xmpp:ping"]) is False


def test_parse_catalog_extracts_items() -> None:
    items, by_selector = parse_catalog(_catalog_element())

    assert [i["selector"] for i in items] == ["UNCLASSIFIED", "SECRET"]
    assert items[0]["display_marking"] == "UNCLASSIFIED"
    assert items[1]["fg_color"] == "white"
    assert items[1]["bg_color"] == "red"
    assert set(by_selector) == {"UNCLASSIFIED", "SECRET"}


def test_parse_catalog_falls_back_to_display_when_no_selector() -> None:
    xml = f"""
    <catalog xmlns="{CATALOG_NS}">
      <item>
        <securitylabel xmlns="{SEC_LABEL_NS}">
          <displaymarking>RESTRICTED</displaymarking>
        </securitylabel>
      </item>
    </catalog>
    """
    items, by_selector = parse_catalog(etree.fromstring(xml))
    assert items[0]["selector"] == "RESTRICTED"
    assert "RESTRICTED" in by_selector


def test_build_label_returns_independent_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    _, by_selector = parse_catalog(_catalog_element())
    monkeypatch.setitem(sl._catalog_cache, "room@conf.example.com", by_selector)

    el = build_label("room@conf.example.com/nick", "SECRET")
    assert el.tag == f"{{{SEC_LABEL_NS}}}securitylabel"
    # A fresh deep copy — mutating it must not poison the cache.
    assert el is not by_selector["SECRET"]


def test_build_label_unknown_selector_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _, by_selector = parse_catalog(_catalog_element())
    monkeypatch.setitem(sl._catalog_cache, "room@conf.example.com", by_selector)

    with pytest.raises(SecurityLabelError, match="Unknown security label selector"):
        build_label("room@conf.example.com", "TOP-SECRET")


def test_build_label_without_cached_catalog_raises() -> None:
    with pytest.raises(SecurityLabelError, match="No security label catalog cached"):
        build_label("never-fetched@example.com", "SECRET")
