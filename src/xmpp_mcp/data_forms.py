"""XEP-0004 Data Forms — parse to / build from a plain JSON-shaped dict.

The MCP tool surface never exchanges XML with the AI; everything flows through
the ``DataForm`` shape defined here. Used by the pubsub tools to publish form
templates, submit fills, and read them back, as well as for pubsub node
configuration and subscription options (both of which are also XEP-0004 forms).

XEP-0004 reference: https://xmpp.org/extensions/xep-0004.html
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict
from xml.etree import ElementTree as etree

NS = "jabber:x:data"
QN = f"{{{NS}}}"

FormType = Literal["form", "submit", "result", "cancel"]


class FieldOption(TypedDict, total=False):
    """One ``<option/>`` in a list-single / list-multi field."""

    value: str
    label: str


class FormField(TypedDict, total=False):
    """A single ``<field/>`` element.

    ``value`` carries the field's value(s) in a shape that matches its ``type``:

    * ``boolean`` → ``bool``
    * ``text-multi`` / ``list-multi`` / ``jid-multi`` → ``list[str]``
    * everything else → ``str`` (or absent)
    """

    var: str
    type: str
    label: str
    required: bool
    description: str
    value: Any
    options: list[FieldOption]


class DataForm(TypedDict, total=False):
    """A whole ``<x xmlns='jabber:x:data'/>`` element."""

    type: FormType
    title: str
    instructions: str
    fields: list[FormField]


_MULTI_TYPES = {"text-multi", "list-multi", "jid-multi"}


# --- parsing -------------------------------------------------------------


def parse_form(element: etree.Element) -> DataForm:
    """Parse an ``<x xmlns='jabber:x:data'/>`` element into a :class:`DataForm`."""
    if element.tag != f"{QN}x":
        raise ValueError(f"expected <{{{NS}}}x>, got {element.tag!r}")

    form: DataForm = {"type": element.get("type", "form")}  # type: ignore[typeddict-item]
    title = element.find(f"{QN}title")
    if title is not None and title.text:
        form["title"] = title.text
    instructions = element.find(f"{QN}instructions")
    if instructions is not None and instructions.text:
        form["instructions"] = instructions.text

    fields: list[FormField] = []
    for field_el in element.findall(f"{QN}field"):
        fields.append(_parse_field(field_el))
    if fields:
        form["fields"] = fields
    return form


def _parse_field(field_el: etree.Element) -> FormField:
    ftype = field_el.get("type", "text-single")
    field: FormField = {"type": ftype}
    if (var := field_el.get("var")) is not None:
        field["var"] = var
    if (label := field_el.get("label")) is not None:
        field["label"] = label
    if field_el.find(f"{QN}required") is not None:
        field["required"] = True
    desc = field_el.find(f"{QN}desc")
    if desc is not None and desc.text:
        field["description"] = desc.text

    options: list[FieldOption] = []
    for opt in field_el.findall(f"{QN}option"):
        opt_value = opt.find(f"{QN}value")
        entry: FieldOption = {}
        if opt_value is not None and opt_value.text is not None:
            entry["value"] = opt_value.text
        if (opt_label := opt.get("label")) is not None:
            entry["label"] = opt_label
        if entry:
            options.append(entry)
    if options:
        field["options"] = options

    values = [v.text or "" for v in field_el.findall(f"{QN}value")]
    if ftype == "boolean":
        if values:
            field["value"] = values[0].strip().lower() in ("1", "true")
    elif ftype in _MULTI_TYPES:
        if values:
            field["value"] = values
    else:
        if values:
            field["value"] = values[0]

    return field


# --- building ------------------------------------------------------------


def build_form_element(form: DataForm) -> etree.Element:
    """Build an ``<x xmlns='jabber:x:data'/>`` element from a :class:`DataForm`."""
    x = etree.Element(f"{QN}x", attrib={"type": form.get("type", "form")})
    if "title" in form:
        title = etree.SubElement(x, f"{QN}title")
        title.text = form["title"]
    if "instructions" in form:
        instr = etree.SubElement(x, f"{QN}instructions")
        instr.text = form["instructions"]
    for field in form.get("fields", []):
        x.append(_build_field_element(field))
    return x


def _build_field_element(field: FormField) -> etree.Element:
    attribs: dict[str, str] = {}
    if "var" in field:
        attribs["var"] = field["var"]
    if "type" in field:
        attribs["type"] = field["type"]
    if "label" in field:
        attribs["label"] = field["label"]
    el = etree.Element(f"{QN}field", attrib=attribs)

    if field.get("required"):
        etree.SubElement(el, f"{QN}required")
    if "description" in field:
        desc = etree.SubElement(el, f"{QN}desc")
        desc.text = field["description"]

    for opt in field.get("options", []):
        opt_attrib: dict[str, str] = {}
        if "label" in opt:
            opt_attrib["label"] = opt["label"]
        opt_el = etree.SubElement(el, f"{QN}option", attrib=opt_attrib)
        if "value" in opt:
            v = etree.SubElement(opt_el, f"{QN}value")
            v.text = opt["value"]

    if "value" in field:
        ftype = field.get("type", "text-single")
        raw = field["value"]
        values = _coerce_values(raw, ftype)
        for v in values:
            value_el = etree.SubElement(el, f"{QN}value")
            value_el.text = v

    return el


def _coerce_values(value: Any, ftype: str) -> list[str]:
    """Normalise a Python field value into a list of ``<value/>`` text strings."""
    if ftype == "boolean":
        return ["true" if value else "false"]
    if ftype in _MULTI_TYPES:
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        return [str(value)]
    return [str(value)]


# --- convenience: a submit form from a values dict -----------------------


def build_submit_form(
    values: dict[str, Any],
    *,
    form_type_uri: str | None = None,
) -> DataForm:
    """Produce a ``type=submit`` form from a plain ``{var: value}`` dict.

    A submit form omits labels, options, and field types — the receiving side
    already knows the schema from the matching ``type=form`` it issued.
    Booleans are auto-tagged ``boolean``; everything else is left untyped so
    the receiver applies its declared field type.

    Pass ``form_type_uri`` when answering a domain-specific form (e.g.
    ``http://jabber.org/protocol/pubsub#node_config``) — XEP-0068 requires a
    matching ``FORM_TYPE`` field of type ``hidden`` on submissions.
    """
    fields: list[FormField] = []
    if form_type_uri:
        fields.append(
            {"var": "FORM_TYPE", "type": "hidden", "value": form_type_uri}
        )
    for var, value in values.items():
        f: FormField = {"var": var, "value": value}
        if isinstance(value, bool):
            f["type"] = "boolean"
        elif isinstance(value, (list, tuple)):
            f["type"] = "text-multi"
        fields.append(f)
    return {"type": "submit", "fields": fields}
