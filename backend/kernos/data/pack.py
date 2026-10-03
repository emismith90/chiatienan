"""``CollectionsPack``: the generated tools of a business's collections (design §5.3).

Per collection, by mode — a ``table`` gets ``{slug}_find``, ``{slug}_search``,
``{slug}_create``, ``{slug}_update`` (only the fields given) and ``{slug}_delete`` (a
hide when the collection has ``soft_delete``); a ``journal`` gets ``{slug}_find``,
``{slug}_append`` and ``{slug}_search`` (no way to change or remove an entry). Their
descriptions and input schemas come from the definition, so the engine validates the
shape before the tool does (the schema is in the sidecar-safe subset). ``find`` returns
documents as stored, at most ``FIND_LIMIT`` with a ``more`` flag; ``search`` ranks them
by words and meaning and returns no score; no tool computes an aggregate — a derived
amount in the model's reply is caught by the reply validators like any other (Phase 5
review F4).

Writes **propose** (:mod:`kernos.data.actions`): the turn ends on one ``record_draft``
card listing every change, and nothing is saved until a person confirms it — the
default for every collection; ``options.confirm: false`` opts a collection out.

``tools(ctx)`` reads the definitions per turn (live content, F2) and never raises:
a definition it cannot turn into tools is logged and skipped, so one bad row cannot
take a business's other tools down (F6).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from kernos.content.errors import ContentError
from kernos.data import actions
from kernos.data.store import FIND_LIMIT, SEARCH_LIMIT, DataStore, generated_tool_names
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


def _props(collection: dict, fields: list[str]) -> dict:
    props = collection["schema"].get("properties") or {}
    return {f: props[f] for f in fields if f in props}


def tools_for(collection: dict, data: DataStore, space_id: Any, actor: str | None, *,
              session_factory: Callable[[], Any] | None = None,
              only: list[str] | None = None,
              resolve: Callable[[], dict] | None = None) -> dict[str, PackTool]:
    """The generated tools of one collection. Reads answer at once; writes **propose**
    an action (:mod:`kernos.data.actions`) that becomes the turn's confirm card — unless
    the collection's ``options.confirm`` is ``False``, when they apply at once.
    ``only`` limits them to some of ``find``/``search``/``create``/``update``/``delete``
    (an internal collection's ``agent_tools``). ``resolve`` returns the stored definition
    when a tool runs — for a collection declared in code, whose tools are described
    before any database exists (the static manifest)."""
    slug, key, name = collection["slug"], collection["key"], collection["name"]
    journal = collection.get("mode") == "journal"
    indexed = list(collection["indexed"])
    about = f"{name}" + (f" — {collection['description']}" if collection.get("description") else "")
    fields = _describe_properties(collection["schema"])
    opts = actions.options_of(collection)
    confirm, soft = opts["confirm"], opts["soft_delete"]
    who = str(actor) if actor is not None else "agent"
    sessions = session_factory or data._session

    def live() -> dict:
        return resolve() if resolve is not None else collection

    def _visible(docs: list[dict]) -> list[dict]:
        return [d for d in docs if not actions.hidden(collection, d["data"])]

    def find(args, _tool_ctx=None) -> dict:
        args = args or {}
        where = args.get("where") or {}
        if not isinstance(where, dict):
            return err("where must be an object of field: value pairs.")
        try:
            with sessions() as s:
                out = data.find_documents(live(), space_id, where=where,
                                          limit=args.get("limit") or FIND_LIMIT, session=s)
        except ContentError as exc:
            return err(str(exc))
        out["documents"] = _visible(out["documents"])
        return {"ok": True, "type": f"{slug}_documents", "collection": slug, **out}

    def search(args, _tool_ctx=None) -> dict:
        args = args or {}
        try:
            # its own sessions: the embedder is called with none open (store.search_documents)
            out = data.search_documents(live(), space_id, args.get("query"),
                                        limit=args.get("limit") or SEARCH_LIMIT)
        except ContentError as exc:
            return err(str(exc))
        out["documents"] = _visible(out["documents"])
        return {"ok": True, "type": f"{slug}_search_results", "collection": slug, **out}

    def _write(op: str, *, doc_id=None, payload=None, corrects=None) -> dict:
        try:
            with sessions() as s:
                action = actions.propose(data, live(), space_id, op, doc_id=doc_id, payload=payload,
                                         corrects=corrects, session=s)
                if not confirm:
                    done = actions.apply(data, action, actor=who, session=s)
                    return {"ok": True, "type": f"{slug}_saved", "collection": slug, **done}
        except (ContentError, actions.ActionRefused) as exc:
            return err(str(exc))
        return {"ok": True, "type": actions.PROPOSED, "action": action,
                "note": f"Proposed, NOT saved: {action['headline']}. It is saved only when "
                        "someone presses Confirm on the card — say so; never say it is done."}

    def create(args, _tool_ctx=None) -> dict:
        doc = (args or {}).get("data")
        if not isinstance(doc, dict):
            return err(f"data must be an object of {slug} fields.")
        return _write("create", payload=doc)

    def update(args, _tool_ctx=None) -> dict:
        args = args or {}
        changes = args.get("changes")
        if not isinstance(changes, dict) or not changes:
            return err("changes must be an object of the fields to change, with their new values.")
        return _write("update", doc_id=args.get("doc_id"), payload=changes)

    def delete(args, _tool_ctx=None) -> dict:
        doc_id = (args or {}).get("doc_id")
        if not isinstance(doc_id, str) or not doc_id:
            return err(f"Missing doc_id (the {key} of the {slug} record).")
        return _write("delete", doc_id=doc_id)

    def append(args, _tool_ctx=None) -> dict:
        args = args or {}
        doc = args.get("data")
        if not isinstance(doc, dict):
            return err(f"data must be an object matching the {slug} schema.")
        corrects = args.get("corrects")
        if corrects is not None and (not isinstance(corrects, str) or not corrects):
            return err("corrects must be the doc_id of an earlier entry.")
        return _write("append", payload=doc, corrects=corrects)

    how = ("Proposes it — a person confirms it on a card before anything is saved."
           if confirm else "Writes immediately.")
    where_schema = {"type": "object", "description": f"Equality filters on: {', '.join(indexed) or 'nothing (list all)'}.",
                    "properties": {f: collection["schema"]["properties"][f] for f in indexed}}
    tools: dict[str, PackTool] = {}
    names = dict(zip(("find", "create", "update", "delete", "search") if not journal else ("find", "append", "search"),
                     generated_tool_names(slug, collection.get("mode", "table"))))
    tools["find"] = PackTool(
        names["find"],
        f"Look up {about}. Returns the stored documents as they are (at most {FIND_LIMIT}, "
        f"`more` when there are others{', newest first' if journal else ''}) — never a count or a total. "
        f"Fields: {fields}.",
        {"type": "object", "properties": {"where": where_schema,
                                          "limit": {"type": "integer", "description": f"1–{FIND_LIMIT}."}}},
        find)
    tools["search"] = PackTool(
        names["search"],
        f"Search {about} by words and by meaning (a question or a description works, in any language); "
        f"best match first, at most {SEARCH_LIMIT}. For discovering what is there — never to decide which "
        f"exact record a money action refers to. Fields: {fields}.",
        {"type": "object", "properties": {"query": {"type": "string", "description": "What to look for."},
                                          "limit": {"type": "integer", "description": f"1–{SEARCH_LIMIT}."}},
         "required": ["query"]},
        search)
    if journal:
        tools["append"] = PackTool(
            names["append"],
            f"Add one entry to {about}. Entries are permanent: nothing can change or delete one, so to fix a "
            f"mistake add a new entry with `corrects` set to the wrong entry's doc_id. {how} Fields: {fields}.",
            {"type": "object", "properties": {
                "data": collection["schema"],
                "corrects": {"type": "string", "description": "doc_id of an earlier entry this one corrects."}},
             "required": ["data"]},
            append)
    else:
        editable = actions.editable_fields(collection)
        create_fields = editable + ([key] if opts["ids"] == "client" else [])
        required = [f for f in collection["schema"].get("required") or [] if f in create_fields]
        tools["create"] = PackTool(
            names["create"],
            f"Add one new record to {about}. {how}"
            + (f" Give its `{key}`." if opts["ids"] == "client" else " Its id is assigned when it is saved.")
            + f" Fields: {fields}.",
            {"type": "object", "properties": {"data": {
                "type": "object", "properties": _props(collection, create_fields),
                **({"required": required} if required else {})}},
             "required": ["data"]},
            create)
        tools["update"] = PackTool(
            names["update"],
            f"Change some fields of one {about} record, found first with find/search. Pass only the fields "
            f"that change; the rest stay as they are. {how} Editable: {', '.join(editable)}.",
            {"type": "object", "properties": {
                "doc_id": {"type": "string", "description": f"The record's {key}."},
                "changes": {"type": "object", "properties": _props(collection, editable)}},
             "required": ["doc_id", "changes"]},
            update)
        tools["delete"] = PackTool(
            names["delete"],
            (f"Hide one {about} record by its `{key}` — it disappears from lists and suggestions, and "
             f"everything that refers to it keeps working. {how}") if soft else
            f"Permanently delete one {about} record by its `{key}`. {how}",
            {"type": "object", "properties": {"doc_id": {"type": "string", "description": f"The record's {key}."}},
             "required": ["doc_id"]},
            delete)
    wanted = list(only) if only is not None else list(tools)
    return {tools[k].name: tools[k] for k in names if k in wanted}


class CollectionsPack(BasePack):
    id, version, handles_money = "collections", "1", False
    #: The tools are the business's collections, so they are per space and their very
    #: names come from the database — the catalogue can only say "per turn".
    dynamic = True

    def __init__(self, data: DataStore, business_of: Callable[[Any], int]) -> None:
        self._data, self._business_of = data, business_of

    def draft_kinds(self) -> dict:
        return {actions.KIND: actions.draft_kind(self._data)}

    def render(self, result):
        return actions.render(result)

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
            if col.get("internal"):       # defined in code; never agent tools (D2)
                continue
            try:
                out.update(tools_for(col, self._data, space_id, getattr(ctx, "sender_member_id", None)))
            except Exception as exc:  # noqa: BLE001 — one bad definition must not take the others down
                log.warning("collections: skipping %r: %s", col.get("slug"), exc)
        return out
