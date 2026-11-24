"""What "required" means for indexed data: the field has a value.

JSON Schema's own ``required`` only checks that a key is present, so ``""``,
``[]`` and ``{}`` all satisfy it — and the indexing form pre-fills every list
with ``[]`` and every object with ``{}``, which made a required list or
object impossible to fail. Here a field named in a ``required`` array, at any
depth, must hold a value:

- missing or ``null`` → missing (``null`` counts as a value only when the
  property's ``type`` explicitly allows ``"null"``)
- string → not blank after trimming
- list → at least one entry that itself holds a value (a list of blank rows
  is empty)
- object → at least one property that holds a value
- numbers and booleans → any value, including ``0`` and ``false``

Required fields inside list items are checked for every entry that exists; an
optional list may still be empty. Properties marked ``"x-hidden"`` are exempt
— the form renders no widget for them, so they can never be filled in (the
form's ``preprocessSchema`` strips them from ``required`` the same way).

The frontend applies the identical rules before submit
(``frontend/src/shared/components/requiredRules.ts``); both are exercised by
``tests/fixtures/required_fields_cases.json``. Only ``properties`` /
``required`` / ``items`` are walked — conditional branches (``if``/``then``,
``anyOf``) are left to the form's JSON Schema validator.
"""
from typing import Any

Path = list[str | int]


def _resolve(node: Any, defs: dict) -> Any:
    """Inline a local ``#/definitions/x`` or ``#/$defs/x`` ref, keeping any
    sibling keys (they win over the definition's), as the form does."""
    if not isinstance(node, dict) or "$ref" not in node:
        return node
    ref = node["$ref"]
    for prefix in ("#/definitions/", "#/$defs/"):
        if isinstance(ref, str) and ref.startswith(prefix):
            target = defs.get(ref[len(prefix):])
            if isinstance(target, dict):
                siblings = {k: v for k, v in node.items() if k != "$ref"}
                return {**target, **siblings}
    return node


def _allows_null(schema: Any) -> bool:
    t = schema.get("type") if isinstance(schema, dict) else None
    return t == "null" or (isinstance(t, list) and "null" in t)


def has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, list):
        return any(has_value(v) for v in value)
    if isinstance(value, dict):
        return any(has_value(v) for v in value.values())
    return True


def _walk(schema: Any, data: Any, path: Path, defs: dict, out: list[Path]) -> None:
    schema = _resolve(schema, defs)
    if not isinstance(schema, dict):
        return

    if isinstance(data, list):
        items = _resolve(schema.get("items"), defs)
        if isinstance(items, dict):
            for i, item in enumerate(data):
                _walk(items, item, [*path, i], defs, out)
        return

    if not isinstance(data, dict):
        return
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return

    required = schema.get("required")
    for key in required if isinstance(required, list) else []:
        prop = _resolve(properties.get(key), defs)
        if isinstance(prop, dict) and prop.get("x-hidden"):
            continue
        value = data.get(key)
        if value is None and key in data and _allows_null(prop):
            continue
        if not has_value(value):
            out.append([*path, key])

    for key, prop in properties.items():
        if key in data:
            _walk(prop, data[key], [*path, key], defs, out)


def find_missing_required(schema: dict | None, data: dict | None) -> list[Path]:
    """Paths (keys and list indexes) of required fields without a value."""
    if not isinstance(schema, dict):
        return []
    defs = {
        **(schema.get("definitions") if isinstance(schema.get("definitions"), dict) else {}),
        **(schema.get("$defs") if isinstance(schema.get("$defs"), dict) else {}),
    }
    out: list[Path] = []
    _walk(schema, data if isinstance(data, dict) else {}, [], defs, out)
    return out


def _title(schema: Any, key: str) -> str:
    if isinstance(schema, dict) and isinstance(schema.get("title"), str):
        return schema["title"]
    return key.replace("_", " ").title()


def describe_path(schema: dict, path: Path) -> str:
    """Human label for a path, e.g. ``Registered Titles › 2 › Folio Number``."""
    defs = {**(schema.get("definitions") or {}), **(schema.get("$defs") or {})}
    node: Any = schema
    labels = []
    for seg in path:
        node = _resolve(node, defs)
        if isinstance(seg, int):
            labels.append(str(seg + 1))
            node = node.get("items") if isinstance(node, dict) else None
        else:
            props = node.get("properties") if isinstance(node, dict) else None
            node = props.get(seg) if isinstance(props, dict) else None
            labels.append(_title(_resolve(node, defs), seg))
    return " › ".join(labels)
