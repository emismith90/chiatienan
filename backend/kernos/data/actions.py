"""Agent writes as proposed actions a person confirms (generic + confirm by default).

The agent never writes a collection directly. A generated write tool —
``{slug}_create``, ``{slug}_update``, ``{slug}_delete``, ``{slug}_append`` — turns
the request into an **action**: what would change, checked against the schema and
the collection's rules *now*, with the record as it stands (``before``) and as it
would be (``after``). The turn's actions become one ``record_draft`` card; nothing
is written until someone presses Confirm, and then :func:`apply` writes them all in
one transaction — refusing any whose record changed since it was proposed.

A collection says how it may be changed in its ``options`` (:func:`check_options`):

* ``confirm`` (default ``True``) — ``False`` writes at once, for low-stakes data;
* ``soft_delete`` — a boolean field: delete sets it ``False`` (hide), and find /
  search skip hidden records. Without it, delete removes the record;
* ``ids`` — ``"client"`` (the default: the create names its key) or ``"server"``
  (the key is the next number, drawn when the action is applied);
* ``editable`` — the fields create and update may set (default: every property but
  the key);
* ``agent_tools`` — for an internal collection only: which generated tools its
  owner hands the agent (``find``, ``search``, ``create``, ``update``, ``delete``).

A collection whose rules are code — an internal store with a domain model behind it
— registers a :class:`Writer`: ``check`` normalises and validates an action when it
is proposed, ``apply`` performs it. Without one, the generic document writes apply.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from kernos.content import models as m
from kernos.content.errors import Conflict, ContentError, Invalid, NotFound

OPS = ("create", "update", "delete", "append")
TOOLS = ("find", "search", "create", "update", "delete")
DEFAULT_OPTIONS = {"confirm": True, "soft_delete": None, "ids": "client", "editable": None, "agent_tools": None}
#: The card kind a turn's proposed actions become.
KIND = "record_draft"
#: The tool-result ``type`` of a proposed (not yet applied) action.
PROPOSED = "record_action"


class ActionRefused(ValueError):
    """An action that cannot be applied any more (its record changed, a rule refuses it).
    ``ValueError`` so the host's draft store leaves the card pending and answers 409."""


# ------------------------------------------------------------------- options

def check_options(options: dict | None, *, schema: dict, key: str, mode: str) -> dict | None:
    """Validate a collection's ``options``; returns them as given (``None`` stays ``None``,
    so a declaration compares equal to what was stored)."""
    if options is None:
        return None
    if not isinstance(options, dict):
        raise Invalid("options must be an object")
    unknown = sorted(set(options) - set(DEFAULT_OPTIONS))
    if unknown:
        raise Invalid(f"unknown collection options: {unknown}")
    props = (schema.get("properties") or {})
    if "confirm" in options and not isinstance(options["confirm"], bool):
        raise Invalid("options.confirm must be true or false")
    soft = options.get("soft_delete")
    if soft is not None:
        if mode == "journal":
            raise Invalid("a journal's entries are never deleted: soft_delete does not apply")
        if (props.get(soft) or {}).get("type") != "boolean":
            raise Invalid(f"options.soft_delete must name a boolean property, not {soft!r}")
    if options.get("ids", "client") not in ("client", "server"):
        raise Invalid("options.ids must be 'client' or 'server'")
    editable = options.get("editable")
    if editable is not None:
        if not isinstance(editable, list) or not all(isinstance(f, str) for f in editable):
            raise Invalid("options.editable must be a list of property names")
        bad = sorted(set(editable) - set(props)) + sorted({key} & set(editable))
        if bad:
            raise Invalid(f"options.editable: not editable properties: {bad}")
    tools = options.get("agent_tools")
    if tools is not None:
        if not isinstance(tools, list) or set(tools) - set(TOOLS):
            raise Invalid(f"options.agent_tools must be a list drawn from {list(TOOLS)}")
    return copy.deepcopy(options)


def options_of(collection: dict) -> dict:
    return {**DEFAULT_OPTIONS, **(collection.get("options") or {})}


def editable_fields(collection: dict) -> list[str]:
    opts = options_of(collection)
    if opts["editable"] is not None:
        return list(opts["editable"])
    return [f for f in (collection["schema"].get("properties") or {}) if f != collection["key"]]


def hidden(collection: dict, data: dict) -> bool:
    """Is this record soft-deleted?"""
    soft = options_of(collection)["soft_delete"]
    return bool(soft) and data.get(soft) is False


# ------------------------------------------------------------------- writers

@dataclass(frozen=True)
class Writer:
    """Code-owned rules for one internal collection.

    ``check(session, space_id, op, before, payload) -> after`` normalises and validates a
    proposal (raising :class:`ContentError`); ``after`` is the record as it would be, or
    ``None`` for a removal. ``apply(session, space_id, op, before, payload, actor) -> doc_id``
    performs it inside the caller's transaction."""
    check: Callable[..., dict | None]
    apply: Callable[..., str]


_writers: dict[str, Writer] = {}


def register_writer(slug: str, writer: Writer) -> None:
    """Register the rules of the **internal** collection ``slug``."""
    _writers[slug] = writer


def _writer(collection: dict) -> Writer | None:
    return _writers.get(collection["slug"]) if collection.get("internal") else None


# ------------------------------------------------------------------- propose

def _label(collection: dict, doc: dict | None, doc_id: str | None) -> str:
    doc = doc or {}
    for f in ("name", "title", "label"):
        if isinstance(doc.get(f), str) and doc[f].strip():
            return doc[f].strip()
    return str(doc_id or doc.get(collection["key"]) or "new")


def propose(data, collection: dict, space_id: Any, op: str, *, doc_id: str | None = None,
            payload: dict | None = None, corrects: str | None = None, session: Session) -> dict:
    """Check one write and describe it, writing nothing. Raises :class:`ContentError` with
    a message the agent can act on (a schema error, a field it may not set, no such record,
    a change that changes nothing)."""
    if op not in OPS:
        raise Invalid(f"unknown action {op!r}")
    journal = collection.get("mode") == "journal"
    if journal != (op == "append"):
        raise Invalid(f"{collection['slug']} is a {'journal: append entries' if journal else 'table'}"
                      + ("" if journal else f"; {op!r} is not one of its actions"))
    opts, key = options_of(collection), collection["key"]
    payload = copy.deepcopy(payload or {})
    if op in ("create", "update"):
        if not isinstance(payload, dict):
            raise Invalid("the fields must be an object")
        allowed = set(editable_fields(collection)) | ({key} if op == "create" and opts["ids"] == "client" else set())
        refused = sorted(set(payload) - allowed)
        if refused:
            raise Invalid(f"{collection['slug']}: {refused} cannot be set here; you may set {sorted(allowed)}")
    before = None
    if op in ("update", "delete"):
        if not doc_id:
            raise Invalid(f"which {collection['slug']} record? pass doc_id (its {key})")
        row = data.get_document(collection, space_id, str(doc_id), session=session)
        if row is None:
            raise NotFound(f"no {collection['slug']} record {doc_id!r} here")
        before = row["data"]
    writer = _writer(collection)
    if writer is not None:
        after = writer.check(session, space_id, op, before, payload)
    elif op == "create":
        after = payload
        if opts["ids"] == "server":
            probe = {**after, key: "0"}
        else:
            if key not in after:
                raise Invalid(f"a new {collection['slug']} record needs its {key}")
            probe = after
            if data.get_document(collection, space_id, str(after[key]), session=session) is not None:
                raise Conflict(f"{collection['slug']} already has {after[key]!r}; update it instead")
        problem = data.validate(collection, probe)
        if problem:
            raise Invalid(f"does not match the {collection['slug']} schema: {problem}")
    elif op == "update":
        after = {**before, **payload}
        problem = data.validate(collection, after)
        if problem:
            raise Invalid(f"does not match the {collection['slug']} schema: {problem}")
    elif op == "delete":
        soft = opts["soft_delete"]
        if soft and before.get(soft) is False:
            raise Invalid(f"«{_label(collection, before, doc_id)}» is already hidden")
        after = {**before, soft: False} if soft else None
    else:  # append
        after = payload
        problem = data.validate(collection, after)
        if problem:
            raise Invalid(f"does not match the {collection['slug']} schema: {problem}")
        if corrects is not None and data.get_document(collection, space_id, str(corrects), session=session) is None:
            raise Invalid(f"corrects={corrects!r}: no such entry")
    if op == "update" and after == before:
        raise Invalid(f"that would not change «{_label(collection, before, doc_id)}»")
    action = {"collection_id": collection["id"], "collection": collection["slug"],
              "collection_name": collection["name"], "space_id": str(space_id), "op": op,
              "doc_id": None if doc_id is None else str(doc_id), "payload": payload,
              "before": before, "after": after, "label": _label(collection, after or before, doc_id),
              "soft": bool(opts["soft_delete"]) and op == "delete"}
    if corrects is not None:
        action["corrects"] = str(corrects)
    # what the card shows, computed once, here, from the same before/after it applies
    action["headline"], action["changes"] = headline(action), changes(action)
    return action


def changes(action: dict) -> list[dict]:
    """``[{field, before, after}]`` — what the card shows, field by field."""
    before, after = action.get("before") or {}, action.get("after") or {}
    if action["op"] == "delete" and not action.get("soft"):
        return []
    fields = list(after) + [f for f in before if f not in after]
    out = []
    for f in fields:
        b, a = before.get(f), after.get(f)
        if b != a and not (b in (None, "", []) and a in (None, "", [])):
            out.append({"field": f, "before": b, "after": a})
    return out


# ------------------------------------------------------------------- apply

def apply(data, action: dict, *, actor: str, session: Session) -> dict:
    """Perform one confirmed action in the caller's transaction. Raises
    :class:`ActionRefused` when its record changed since it was proposed, or a rule now
    refuses it — the caller rolls the whole card back."""
    row = session.get(m.Collection, action["collection_id"])
    if row is None:
        raise ActionRefused(f"the {action['collection']} collection no longer exists")
    from kernos.data.store import _row
    collection = _row(row)
    space_id, op, label = action["space_id"], action["op"], action.get("label") or ""
    if op in ("update", "delete"):
        now = data.get_document(collection, space_id, action["doc_id"], session=session)
        if now is None or now["data"] != action["before"]:
            raise ActionRefused(f"«{label}» changed since this was proposed — nothing was saved; ask again")
    writer = _writer(collection)
    try:
        if writer is not None:
            doc_id = writer.apply(session, space_id, op, action.get("before"), action["payload"], actor)
        elif op == "create":
            doc = copy.deepcopy(action["after"])
            if options_of(collection)["ids"] == "server":
                floor = _max_numeric_id(session, collection, space_id)
                doc[collection["key"]] = str(data.next_id(f"kn:{collection['id']}", session=session, floor=floor))
            doc_id = data.insert_document(collection, space_id, doc, actor=actor, session=session)["doc_id"]
        elif op == "update" or (op == "delete" and action.get("soft")):
            doc_id = data.upsert_document(collection, space_id, action["after"], actor=actor, session=session)["doc_id"]
        elif op == "delete":
            doc_id = data.delete_document(collection, space_id, action["doc_id"], actor=actor, session=session)["doc_id"]
        else:
            doc_id = data.append_entry(collection, space_id, action["after"], actor=actor,
                                       corrects=action.get("corrects"), session=session)["doc_id"]
    except ContentError as exc:
        raise ActionRefused(f"«{label}»: {exc}") from exc
    return {"op": op, "collection": action["collection"], "doc_id": doc_id, "label": label}


def _max_numeric_id(session: Session, collection: dict, space_id: Any) -> int:
    ids = session.scalars(select(m.Document.doc_id).where(m.Document.collection_id == collection["id"])).all()
    return max((int(i) for i in ids if str(i).isdigit()), default=0)


# ------------------------------------------------------------------- the card

def proposed(result) -> list[dict]:
    """Every action this agent proposed in the turn, in order, each once. A sub-agent's
    proposal is data for the manager, never this turn's card (as ``last_result``)."""
    if result is None:
        return []
    out = []
    for inv in getattr(result, "tools", None) or []:
        res = getattr(inv, "result", None)
        if getattr(inv, "from_agent", None) is None and isinstance(res, dict) and res.get("ok") \
                and res.get("type") == PROPOSED and res["action"] not in out:
            out.append(res["action"])
    return out


_VERB = {"create": "Add", "update": "Change", "append": "Add", "delete": "Delete"}


def headline(action: dict) -> str:
    verb = "Hide" if action.get("soft") else _VERB[action["op"]]
    return f"{verb} {action['collection_name'].rstrip('s').lower() or action['collection']} «{action['label']}»"


def body(payload: dict) -> str:
    """The pending card's text: every change, field by field, so a person sees exactly
    what they confirm."""
    lines = ["📝 **Confirm these changes?**", ""]
    for a in payload.get("actions") or []:
        lines.append(f"• {a['headline']}")
        for c in a["changes"]:
            lines.append(f"    {c['field']}: {_show(c['before'])} → {_show(c['after'])}")
    return "\n".join(lines)


def _show(v: Any) -> str:
    if v in (None, "", []):
        return "—"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def draft_kind(data):
    """The ``record_draft`` card kind, committed through ``data``."""
    from kernos.packs import DraftKind

    def commit(session, space_id, payload: dict, *, logged_by) -> dict:
        actor = f"member:{logged_by}" if logged_by else "member:unknown"
        actions = payload.get("actions") or []
        if any(str(a["space_id"]) != str(space_id) for a in actions):
            raise ActionRefused("these changes belong to another room")
        return {"applied": [apply(data, a, actor=actor, session=session) for a in actions], "by": actor}

    def card(session, space_id, payload: dict, result: dict) -> tuple[str, dict]:
        done = result.get("applied") or []
        names = "; ".join(a["headline"] for a in payload.get("actions") or [])
        return (f"✅ Saved — {names}.",
                {"type": "records_saved", "applied": done})

    def summary(session, space_id, payload: dict) -> dict:
        return {"kind": KIND, "label": "; ".join(a["headline"] for a in payload.get("actions") or [])}

    return DraftKind(KIND, commit, stamps=frozenset({"turn_id"}), card=card, summary=summary,
                     blocks_settlement=False, body=body)


def render(result):
    """A turn that proposed actions ends on their card."""
    from kernos.kernel.context import Draft

    actions = proposed(result)
    return Draft(KIND, {"actions": actions}) if actions else None
