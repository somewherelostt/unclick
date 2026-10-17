"""Infer a JSON Schema (draft 2020-12 subset) from the verified sample output of a client.

Used at publish time: the schema is written to the API sandbox as schema.json, served in
/openapi.json, and checked by /health/verify. Stdlib only.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 50  # array items inspected when merging object shapes


def _type_of(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "string"


def _types(schema: dict) -> list[str]:
    t = schema.get("type", [])
    return list(t) if isinstance(t, list) else [t]


def _with_types(schema: dict, types: list[str]) -> dict:
    types = list(dict.fromkeys(types))
    if "number" in types and "integer" in types:
        types.remove("integer")
    out = dict(schema)
    out["type"] = types[0] if len(types) == 1 else types
    return out


def infer_schema(value: Any) -> dict:
    t = _type_of(value)
    if t == "object":
        props = {str(k): infer_schema(v) for k, v in value.items()}
        return {"type": "object", "properties": props,
                "required": [str(k) for k, v in value.items() if v is not None]}
    if t == "array":
        items = value[:MAX_ITEMS]
        if not items:
            return {"type": "array", "items": {}}
        merged = infer_schema(items[0])
        for it in items[1:]:
            merged = merge(merged, infer_schema(it))
        return {"type": "array", "items": merged}
    if t == "string" and value.startswith(("http://", "https://")):
        return {"type": "string", "format": "uri"}
    return {"type": t}


def merge(a: dict, b: dict) -> dict:
    """Least upper bound of two inferred schemas (nullable fields, optional keys, mixed types)."""
    if not a:
        return b
    if not b:
        return a
    ta, tb = _types(a), _types(b)
    if ta == ["object"] and tb == ["object"]:
        pa, pb = a.get("properties", {}), b.get("properties", {})
        props = {k: merge(pa.get(k, {}), pb.get(k, {})) for k in [*pa, *[k for k in pb if k not in pa]]}
        # a key missing on either side drops out of required (it is optional)
        req =[k for k in a.get("required", []) if k in set(b.get("required", []))]
        return {"type": "object", "properties": props, "required": req}
    if ta == ["array"] and tb == ["array"]:
        return {"type": "array", "items": merge(a.get("items", {}), b.get("items", {}))}
    base_a = {k: v for k, v in a.items() if k != "format"} if a.get("format") != b.get("format") else a
    if "null" in tb and set(tb) == {"null"}:
        return _with_types(a, [*ta, "null"])
    if "null" in ta and set(ta) == {"null"}:
        return _with_types(b, [*tb, "null"])
    return _with_types(base_a, [*ta, *tb])


def describe(schema: dict) -> list[dict]:
    """Flat field list for UIs: [{name, type}] of the record objects (top level or array items)."""
    s = schema.get("items", {}) if _types(schema) == ["array"] else schema
    req = set(s.get("required", []))
    return [{"name": k, "type": "|".join(_types(v)) or "any", "required": k in req}
            for k, v in (s.get("properties") or {}).items()]
