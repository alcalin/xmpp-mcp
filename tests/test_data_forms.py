"""Unit tests for XEP-0004 data-form parse/build."""

from __future__ import annotations

from xml.etree import ElementTree as etree

import pytest

from xmpp_mcp.data_forms import (
    NS,
    build_form_element,
    build_submit_form,
    parse_form,
)

_FORM_XML = f"""
<x xmlns="{NS}" type="form">
  <title>Job application</title>
  <instructions>Please fill out all fields</instructions>
  <field var="FORM_TYPE" type="hidden">
    <value>http://example.com/jobs/apply</value>
  </field>
  <field var="name" type="text-single" label="Full name">
    <required/>
    <desc>Your full legal name</desc>
  </field>
  <field var="bio" type="text-multi" label="About you"/>
  <field var="active" type="boolean" label="Available?">
    <value>true</value>
  </field>
  <field var="role" type="list-single" label="Role">
    <option label="Engineer"><value>eng</value></option>
    <option label="Manager"><value>mgr</value></option>
    <value>eng</value>
  </field>
  <field var="tags" type="list-multi" label="Tags">
    <option label="Backend"><value>be</value></option>
    <option label="Frontend"><value>fe</value></option>
    <value>be</value>
    <value>fe</value>
  </field>
  <field var="manager" type="jid-single" label="Reports to">
    <value>alice@xmpp.test</value>
  </field>
</x>
"""


def _xml_to_str(el: etree.Element) -> str:
    return etree.tostring(el, encoding="unicode")


def test_parse_form_extracts_top_level_fields() -> None:
    form = parse_form(etree.fromstring(_FORM_XML))
    assert form["type"] == "form"
    assert form["title"] == "Job application"
    assert form["instructions"] == "Please fill out all fields"
    assert len(form["fields"]) == 7


def test_parse_form_field_types_value_shapes() -> None:
    form = parse_form(etree.fromstring(_FORM_XML))
    by_var = {f["var"]: f for f in form["fields"]}

    assert by_var["FORM_TYPE"]["type"] == "hidden"
    assert by_var["FORM_TYPE"]["value"] == "http://example.com/jobs/apply"

    name = by_var["name"]
    assert name["required"] is True
    assert name["description"] == "Your full legal name"
    assert "value" not in name  # not set

    bio = by_var["bio"]
    assert bio["type"] == "text-multi"
    assert "value" not in bio

    active = by_var["active"]
    assert active["type"] == "boolean"
    assert active["value"] is True  # bool, not "true"

    role = by_var["role"]
    assert role["type"] == "list-single"
    assert role["options"] == [
        {"value": "eng", "label": "Engineer"},
        {"value": "mgr", "label": "Manager"},
    ]
    assert role["value"] == "eng"

    tags = by_var["tags"]
    assert tags["type"] == "list-multi"
    assert tags["value"] == ["be", "fe"]

    manager = by_var["manager"]
    assert manager["type"] == "jid-single"
    assert manager["value"] == "alice@xmpp.test"


def test_round_trip_parse_build_parse() -> None:
    original = parse_form(etree.fromstring(_FORM_XML))
    rebuilt_xml = _xml_to_str(build_form_element(original))
    reparsed = parse_form(etree.fromstring(rebuilt_xml))
    # Compare via parsed shapes (XML string can differ in attribute order).
    assert reparsed == original


def test_build_submit_form_minimal() -> None:
    form = build_submit_form({"name": "Alice", "active": True, "tags": ["be", "fe"]})
    assert form["type"] == "submit"
    by_var = {f["var"]: f for f in form["fields"]}
    assert by_var["name"]["value"] == "Alice"
    assert by_var["name"].get("type") in (None,)  # not bool/list
    assert by_var["active"]["type"] == "boolean"
    assert by_var["active"]["value"] is True
    assert by_var["tags"]["type"] == "text-multi"
    assert by_var["tags"]["value"] == ["be", "fe"]


def test_build_submit_form_with_form_type_uri() -> None:
    form = build_submit_form(
        {"pubsub#title": "My node"},
        form_type_uri="http://jabber.org/protocol/pubsub#node_config",
    )
    first = form["fields"][0]
    assert first["var"] == "FORM_TYPE"
    assert first["type"] == "hidden"
    assert first["value"] == "http://jabber.org/protocol/pubsub#node_config"


def test_build_form_element_then_parse() -> None:
    form = {
        "type": "form",
        "title": "Test",
        "fields": [
            {"var": "v", "type": "boolean", "value": False, "required": True},
        ],
    }
    el = build_form_element(form)
    parsed = parse_form(el)
    assert parsed["title"] == "Test"
    assert parsed["fields"][0]["value"] is False
    assert parsed["fields"][0]["required"] is True


def test_parse_form_rejects_wrong_root() -> None:
    with pytest.raises(ValueError, match="expected"):
        parse_form(etree.Element("notaform"))


def test_field_without_explicit_type_defaults_to_text_single() -> None:
    xml = f'<x xmlns="{NS}" type="form"><field var="x"><value>v</value></field></x>'
    form = parse_form(etree.fromstring(xml))
    assert form["fields"][0]["type"] == "text-single"
    assert form["fields"][0]["value"] == "v"
