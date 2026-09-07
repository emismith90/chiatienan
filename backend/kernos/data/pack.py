"""``CollectionsPack``: the generated tools of a business's collections (design §5.3).

Three tools per collection — ``{slug}_find``, ``{slug}_upsert``, ``{slug}_delete`` —
whose descriptions and input schemas come from the definition, so the engine
validates the shape before the tool does (the schema is in the sidecar-safe subset).
``find`` returns documents as stored, in ``doc_id`` order, at most ``FIND_LIMIT`` with a
``more`` flag; no tool computes an aggregate — a derived amount in the model's reply
is caught by the reply validators like any other (Phase 5 review F4). Writes are
immediate: these are facts the room asked the agent to remember, not money.

``tools(ctx)`` reads the definitions per turn (live content, F2) and never raises:
a definition it cannot turn into tools is logged and skipped, so one bad row cannot
take a business's other tools down (F6).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from kernos.content.errors import ContentError
from kernos.data.store import FIND_LIMIT, DataStore, generated_tool_names
from kernos.packs import BasePack, PackTool, err

log = logging.getLogger("kernos.data")


def _describe_properties(schema: dict) -> str:
    parts = []
    for name, prop in (schema.get("properties") or {}).items():
        kind = prop.get("type")
        kind = "|".join(kind) if isinstance(kind, list) else str(kind)
        if prop.get("enum"):
            kind += " (" + "/".join(prop["enum"]) + ")"
        desc = f" — {prop['description']}" if prop.get("description") else ""
        parts.append(f"{name}: {kind}{desc}")
    return "; ".join(parts)


def tools_for(collection: dict, data: DataStore, space_id: Any, actor: str | None) -> dict[str, PackTool]:
    slug, key, name = collection["slug"], collection["key"], collection["name"]
    indexed = list(collection["indexed"])
    about = f"{name}" + (f" — {collection['description']}" if collection.get("description") else "")
    fields = _describe_properties(collection["schema"])
    find_name, upsert_name, delete_name = generated_tool_names(slug)
    who = str(actor) if actor is not None else "agent"

    def find(args, _tool_ctx=None) -> dict:
        args = args or {}
        where = args.get("where") or {}
        if not isinstance(where, dict):
            return err("where must be an object of field: value pairs.")
        try:
            out = data.find_documents(collection, space_id, where=where, limit=args.get("limit") or FIND_LIMIT)
        except ContentError as exc:
            return err(str(exc))
        return {"ok": True, "type": f"{slug}_documents", "collection": slug, **out}

    def upsert(args, _tool_ctx=None) -> dict:
        args = args or {}
        doc = args.get("data")
        if not isinstance(doc, dict):
            return err(f"data must be an object matching the {slug} schema.")
        try:
            row = data.upsert_document(collection, space_id, doc, actor=who)
        except ContentError as exc:
            return err(str(exc))
        return {"ok": True, "type": f"{slug}_document", "collection": slug, "doc_id": row["doc_id"], "data": row["data"]}

    def delete(args, _tool_ctx=None) -> dict:
        args = args or {}
        doc_id = args.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id:
            return err(f"Missing doc_id (the {key} of the {slug} document).")
        try:
            row = data.delete_document(collection, space_id, doc_id, actor=who)
        except ContentError as exc:
            return err(str(exc))
        return {"ok": True, "type": f"{slug}_deleted", "collection": slug, "doc_id": row["doc_id"], "data": row["data"]}

    where_schema = {"type": "object", "description": f"Equality filters on: {', '.join(indexed) or 'nothing (list all)'}.",
                    "properties": {f: collection["schema"]["properties"][f] for f in indexed}}
    return {
        find_name: PackTool(
            find_name,
            f"Look up {about}. Returns the stored documents as they are (at most {FIND_LIMIT}, "
            f"`more` when there are others) — never a count or a total. Fields: {fields}.",
            {"type": "object", "properties": {"where": where_schema,
                                              "limit": {"type": "integer", "description": f"1–{FIND_LIMIT}."}}},
            find),
        upsert_name: PackTool(
            upsert_name,
            f"Create or replace one {about} document; `{key}` is its id. Writes immediately. Fields: {fields}.",
            {"type": "object", "properties": {"data": collection["schema"]}, "required": ["data"]},
            upsert),
        delete_name: PackTool(
            delete_name,
            f"Permanently delete one {about} document by its `{key}`.",
            {"type": "object", "properties": {"doc_id": {"type": "string", "description": f"The document's {key}."}},
             "required": ["doc_id"]},
            delete),
    }


class CollectionsPack(BasePack):
    id, version, handles_money = "collections", "1", False

    def __init__(self, data: DataStore, business_of: Callable[[Any], int]) -> None:
        self._data, self._business_of = data, business_of

    def tools(self, ctx) -> dict[str, PackTool]:
        space_id = str(ctx.space_id)
        try:
            business_id = self._business_of(space_id)
            collections = self._data.list_collections(business_id)
        except Exception as exc:  # noqa: BLE001 — no data plane for this space is not a broken turn
            log.warning("collections: no definitions for space %s: %s", space_id, exc)
            return {}
        out: dict[str, PackTool] = {}
        for col in collections:
            try:
                out.update(tools_for(col, self._data, space_id, getattr(ctx, "sender_member_id", None)))
            except Exception as exc:  # noqa: BLE001 — one bad definition must not take the others down
                log.warning("collections: skipping %r: %s", col.get("slug"), exc)
        return out
