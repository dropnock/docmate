"""The fields a reviewer marks on a record (customer QC, the customer QC
supervisor's review, the digitizing supervisor's outgoing inspection), read
from the document type's JSON Schema and the record's indexed data.

- A plain top-level property is one field, keyed by its name (``caveat_number``).
- A top-level list of objects gives one field per part of each entry the record
  has: ``caveators/1/address`` is the second caveator's address, labelled
  "Caveators 2 › Address". A part that is itself a group (an address object, a
  nested list) is one field. A list with no entries is one field for the whole
  list, so a list left empty can still be marked missing.
- A top-level object gives one field per part: ``owner/name``.
- Any other list (of strings or numbers) is one field.

Keys are the path's segments joined with ``/``, each escaped as in a JSON
Pointer (``~`` → ``~0``, ``/`` → ``~1``), so property names may contain any
character, dots included. A field is critical (an identifier) when its
top-level property or its own part is marked ``x-critical``. Local ``$ref``s
to ``#/definitions/…`` or ``#/$defs/…`` are followed, as the form does.

The frontend derives the same list (``frontend/src/shared/reviewFields.ts``);
both follow ``tests/fixtures/review_fields_cases.json``.
"""
from typing import Any, TypedDict

from app.services.required_fields import _resolve, has_value

Segment = str | int


class ReviewField(TypedDict):
    key: str
    label: str
    is_critical: bool
    empty: bool


def _defs(schema: dict) -> dict:
    out: dict = {}
    for k in ("definitions", "$defs"):
        if isinstance(schema.get(k), dict):
            out.update(schema[k])
    return out


def _props(node: Any) -> dict:
    p = node.get("properties") if isinstance(node, dict) else None
    return p if isinstance(p, dict) else {}


def _title(node: Any, key: str) -> str:
    if isinstance(node, dict) and isinstance(node.get("title"), str) and node["title"].strip():
        return node["title"]
    return key.replace("_", " ").title()


def _critical(node: Any) -> bool:
    return isinstance(node, dict) and bool(node.get("x-critical"))


def _escape(seg: Segment) -> str:
    return str(seg).replace("~", "~0").replace("/", "~1")


def make_key(path: list[Segment]) -> str:
    return "/".join(_escape(s) for s in path)


def parse_key(key: str) -> list[Segment]:
    """Keys have one, two or three segments; only a three-segment key has a
    list position, always the middle one (list / entry / part)."""
    out: list[Segment] = [raw.replace("~1", "/").replace("~0", "~") for raw in key.split("/")]
    if len(out) == 3 and str(out[1]).isdigit():
        out[1] = int(out[1])
    return out


def _list_items(node: dict, defs: dict) -> dict | None:
    """The resolved item schema of a list of objects, else None."""
    if node.get("type") != "array":
        return None
    items = _resolve(node.get("items"), defs)
    return items if isinstance(items, dict) and _props(items) else None


def review_fields(schema: dict | None, data: dict | None) -> list[ReviewField]:
    if not isinstance(schema, dict):
        return []
    defs = _defs(schema)
    data = data if isinstance(data, dict) else {}
    out: list[ReviewField] = []

    def add(path: list[Segment], label: str, critical: bool, value: Any) -> None:
        out.append({"key": make_key(path), "label": label, "is_critical": critical, "empty": not has_value(value)})

    for key, raw in _props(schema).items():
        node = _resolve(raw, defs)
        if not isinstance(node, dict):
            node = {}
        title = _title(node, key)
        critical = _critical(node)
        value = data.get(key)
        items = _list_items(node, defs)
        if items is not None:
            entries = value if isinstance(value, list) else []
            if not entries:
                add([key], title, critical, value)
            for i, entry in enumerate(entries):
                entry = entry if isinstance(entry, dict) else {}
                for part, praw in _props(items).items():
                    pnode = _resolve(praw, defs)
                    add([key, i, part], f"{title} {i + 1} › {_title(pnode, part)}",
                        critical or _critical(pnode), entry.get(part))
        elif node.get("type") == "object" and _props(node):
            obj = value if isinstance(value, dict) else {}
            for part, praw in _props(node).items():
                pnode = _resolve(praw, defs)
                add([key, part], f"{title} › {_title(pnode, part)}", critical or _critical(pnode), obj.get(part))
        else:
            add([key], title, critical, value)
    return out


def label_for(schema: dict | None, key: str) -> str:
    """Human label for a stored field key, from the schema alone (so marks
    keep their label after the record's data changes)."""
    if not isinstance(schema, dict):
        return key
    defs = _defs(schema)
    node: Any = schema
    labels: list[str] = []
    for seg in parse_key(key):
        node = _resolve(node, defs)
        if isinstance(seg, int):
            if labels:
                labels[-1] = f"{labels[-1]} {seg + 1}"
            node = node.get("items") if isinstance(node, dict) else None
            continue
        child = _resolve(_props(node).get(seg), defs)
        labels.append(_title(child, seg))
        node = child
    return " › ".join(labels) or key


def group_key(key: str) -> str:
    """The key with list positions taken out (``caveators/*/address``): the
    same part of every entry, for tallies across records."""
    path = parse_key(key)
    return "/".join("*" if isinstance(s, int) else _escape(s) for s in path)


def group_label(schema: dict | None, key: str) -> str:
    """Label for a group key: "Caveators › Address"."""
    if not isinstance(schema, dict):
        return key
    defs = _defs(schema)
    node: Any = schema
    labels: list[str] = []
    for raw in key.split("/"):
        node = _resolve(node, defs)
        if raw == "*":
            node = node.get("items") if isinstance(node, dict) else None
            continue
        seg = raw.replace("~1", "/").replace("~0", "~")
        child = _resolve(_props(node).get(seg), defs)
        labels.append(_title(child, seg))
        node = child
    return " › ".join(labels) or key
