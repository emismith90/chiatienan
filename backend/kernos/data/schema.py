"""The sidecar-safe JSON Schema subset (Phase 5 review F1).

The Pi sidecar converts tool schemas to TypeBox with **exactly six keywords** —
``type, properties, required, items, description, enum`` (``agent_sidecar/schema.js``)
— and throws on anything else while converting the whole manifest, so one
collection schema with ``additionalProperties`` or ``minimum`` would take every tool
of the business down. A collection's schema is therefore checked here, on write,
against the same rules; widening this subset means widening ``schema.js`` and its
tests together.
"""
from __future__ import annotations

import re

SUPPORTED_KEYWORDS = frozenset({"type", "properties", "required", "items", "description", "enum"})
SCALARS = ("string", "integer", "number", "boolean")

SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,56}$")          # `{slug}_upsert` stays ≤ 64 chars
DOC_ID_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,80}$")       # a document id is a URL path segment


class SchemaError(ValueError):
    """A schema outside the sidecar-safe subset; the message names the path and keyword."""


def check_schema(node, path: str = "schema") -> None:
    """Raise :class:`SchemaError` unless ``node`` uses only the safe subset, exactly as
    the sidecar's converter would accept it."""
    if not isinstance(node, dict):
        raise SchemaError(f"{path}: expected a JSON Schema object, got {type(node).__name__}")
    for keyword in node:
        if keyword not in SUPPORTED_KEYWORDS:
            raise SchemaError(f"{path}: unsupported JSON Schema keyword {keyword!r} "
                              f"(the sidecar converts only {sorted(SUPPORTED_KEYWORDS)})")
    declared = node.get("type")
    if isinstance(declared, list):
        for name in declared:
            if name not in SCALARS:
                raise SchemaError(f"{path}: union member {name!r} is not a scalar type")
        if "enum" in node:
            raise SchemaError(f"{path}: enum is only supported on strings")
        return
    if declared == "object":
        props = node.get("properties")
        if not isinstance(props, dict):
            raise SchemaError(f"{path}: an object must declare properties")
        for req in node.get("required") or []:
            if req not in props:
                raise SchemaError(f"{path}: required names unknown property {req!r}")
        for key, child in props.items():
            check_schema(child, f"{path}.{key}")
        return
    if declared == "array":
        if "items" not in node:
            raise SchemaError(f"{path}: an array must declare items")
        check_schema(node["items"], f"{path}[]")
        return
    if "enum" in node:
        if declared != "string":
            raise SchemaError(f"{path}: enum is only supported on strings, not {declared!r}")
        if not isinstance(node["enum"], list) or not all(isinstance(v, str) for v in node["enum"]):
            raise SchemaError(f"{path}: enum values must be strings")
        return
    if declared not in SCALARS:
        raise SchemaError(f"{path}: unsupported type {declared!r}")


def check_collection_schema(schema, *, key: str, indexed: list[str]) -> None:
    """A collection's schema: an object in the safe subset whose ``key`` is a required
    string property and whose ``indexed`` fields are properties."""
    check_schema(schema)
    if schema.get("type") != "object":
        raise SchemaError("schema: a collection schema must be an object")
    props = schema["properties"]
    if key not in props:
        raise SchemaError(f"key {key!r} is not a property of the schema")
    if props[key].get("type") != "string":
        raise SchemaError(f"key {key!r} must be a string property")
    if key not in (schema.get("required") or []):
        raise SchemaError(f"key {key!r} must be required")
    for field in indexed:
        if field not in props:
            raise SchemaError(f"indexed field {field!r} is not a property of the schema")
