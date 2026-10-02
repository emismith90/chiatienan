"""The ledger journal: every money-row change, as an append-only event (plan 2026-10-02, B).

**Mirrored, not called.** A SQLAlchemy ``after_flush`` listener (registered on
:class:`sqlalchemy.orm.Session`, so every session of every engine) turns each insert or
change of a mirrored row into a journal event, written with a raw insert on the flush's
own connection: the event commits or rolls back with the row. Every writer is therefore
mirrored without being touched — drafts, quick-pay, the void routes and tools, poker,
seeds, and tests that insert rows directly.

**The guard.** A mirrored model declares which columns may change after insert and the
event each change becomes (a void, a payment re-pointed, a meal linked to a place). Any
other change to a money row — or any delete — raises :class:`LedgerImmutable`. Meals,
payments and settlements were immutable by convention; from here it is enforced.

Events carry business ids (``meal_id``, ``payment_id``, …) as integers; the journal's own
``doc_id`` (``000001``…) only orders them. Datetimes are stored as the naive local wall
time the tables store and return (a tz-aware ``created_at`` comes back naive from SQLite),
so a projection reads back exactly what a table read would.
"""
from __future__ import annotations

import json
import weakref
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from sqlalchemy import event, inspect, text
from sqlalchemy.orm import Session

from kernos.content.models import utcnow


class LedgerImmutable(RuntimeError):
    """A money row was changed in a way the ledger does not allow."""


# ----------------------------------------------------------------------- values

def dump_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def load_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def load_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def columns(obj, names: tuple[str, ...]) -> dict:
    return {n: dump_value(getattr(obj, n)) for n in names}


# --------------------------------------------------------------------- registry

@dataclass(frozen=True)
class Mirror:
    """How one ORM model is journaled."""
    model: type
    journal: str                                     # internal collection slug
    space: Callable[[Any], Any]                      # row -> space id (the room)
    on_insert: Callable[[Any], list[dict]]           # row -> events ([] = covered by its parent)
    #: column -> event kind; columns changed together that map to one kind become one event
    mutable: dict[str, str]
    on_update: Callable[[Any, str], dict] | None = None   # (row, kind) -> event


_mirrors: dict[type, Mirror] = {}


def register(mirror: Mirror) -> None:
    _mirrors[mirror.model] = mirror


# ---------------------------------------------------------------------- appends

_collection_ids: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _collection_id(session: Session, slug: str) -> int:
    engine = session.get_bind()
    ids = _collection_ids.setdefault(engine, {})
    if slug not in ids:
        row = session.connection().execute(text(
            "SELECT c.id FROM kn_collections c JOIN kn_businesses b ON b.id = c.business_id "
            "WHERE b.slug = '_system' AND c.slug = :slug AND c.internal = 1"), {"slug": slug}).first()
        if row is None:
            raise LedgerImmutable(f"the {slug!r} journal is missing: was the schema bound (ledger_core.bind)?")
        ids[slug] = row[0]
    return ids[slug]


def append_raw(session: Session, slug: str, space_id: Any, data: dict) -> str:
    """Append one event inside the current transaction, without a flush (this runs
    *during* one). Doc ids number per space, as ``DataStore.append_entry`` does."""
    conn = session.connection()
    cid = _collection_id(session, slug)
    space = str(space_id)
    n = conn.execute(text("SELECT count(*) FROM kn_documents WHERE collection_id = :c AND space_id = :s"),
                     {"c": cid, "s": space}).scalar_one()
    doc_id = f"{n + 1:06d}"
    now = utcnow()
    conn.execute(text(
        "INSERT INTO kn_documents (collection_id, space_id, doc_id, data, created_at, created_by, "
        "updated_at, updated_by, search_text) VALUES (:c, :s, :d, :data, :now, 'ledger', :now, 'ledger', '')"),
        {"c": cid, "s": space, "d": doc_id, "data": json.dumps(data, ensure_ascii=False), "now": now})
    return doc_id


# ---------------------------------------------------------------------- listener

def _changed(obj) -> dict[str, tuple]:
    out = {}
    for attr in inspect(obj).attrs:
        hist = attr.history
        if hist.has_changes():
            out[attr.key] = (hist.deleted, hist.added)
    return out


_unmirrored: ContextVar[bool] = ContextVar("ledger_unmirrored", default=False)


@contextmanager
def unmirrored():
    """Write ledger rows without journaling them — **only** to build a database shaped
    like one from before the journal existed (the deploy rehearsal test). Production
    code never needs it: the migration reads tables and appends events itself."""
    token = _unmirrored.set(True)
    try:
        yield
    finally:
        _unmirrored.reset(token)


@event.listens_for(Session, "after_flush")
def _mirror(session: Session, _flush_context) -> None:
    if not _mirrors or _unmirrored.get():
        return
    pending: list[tuple[tuple, Mirror, Any, dict]] = []
    for obj in session.new:
        m = _mirrors.get(type(obj))
        if m is None:
            continue
        for ev in m.on_insert(obj):
            pending.append(((0, m.journal, ev.get("_order", 0)), m, obj, ev))
    for obj in session.dirty:
        m = _mirrors.get(type(obj))
        if m is None or not session.is_modified(obj, include_collections=True):
            continue
        changed = _changed(obj)
        bad = sorted(k for k in changed if k not in m.mutable)
        if bad:
            raise LedgerImmutable(f"{type(obj).__name__} #{getattr(obj, 'id', '?')}: "
                                  f"{', '.join(bad)} cannot change after it is recorded")
        for kind in sorted({m.mutable[k] for k in changed}):
            pending.append(((1, m.journal, getattr(obj, "id", 0)), m, obj, m.on_update(obj, kind)))
    for obj in session.deleted:
        if type(obj) in _mirrors:
            raise LedgerImmutable(f"{type(obj).__name__} #{getattr(obj, 'id', '?')} cannot be deleted; void it")
    pending.sort(key=lambda p: p[0])
    for _key, m, obj, ev in pending:
        ev.pop("_order", None)
        append_raw(session, m.journal, m.space(obj), ev)


# ------------------------------------------------------------------- the ledger

LEDGER_EVENTS = ["meal", "meal_void", "meal_place", "payment", "payment_void", "payment_retarget",
                 "settlement"]

LEDGER = {
    "slug": "ledger", "name": "Ledger", "mode": "journal", "key": "", "searchable": [],
    "description": "Every money event of a room, append-only (plan 2026-10-02, release B).",
    "schema": {"type": "object", "required": ["event"],
               "properties": {"event": {"type": "string", "enum": LEDGER_EVENTS},
                              "meal_id": {"type": "integer"}, "payment_id": {"type": "integer"},
                              "settlement_id": {"type": "integer"}}},
}

MEAL_COLUMNS = ("id", "room_id", "occurred_on", "payer_member_id", "total_amount", "note", "raw_input",
                "dish", "place_id", "initiator", "guests", "source", "logged_by", "voided", "voided_by",
                "voided_at", "created_at")
PAYMENT_COLUMNS = ("id", "room_id", "from_member_id", "to_member_id", "amount", "occurred_on", "meal_id",
                   "ref_kind", "note", "source", "logged_by", "voided", "voided_by", "voided_at", "created_at")
SETTLEMENT_COLUMNS = ("id", "room_id", "period_from", "period_to", "created_at", "requested_by", "transfers")


def _meal_inserted(meal) -> list[dict]:
    return [{"event": "meal", "_order": meal.id, **columns(meal, MEAL_COLUMNS),
             "shares": [{"member_id": s.member_id, "share_amount": s.share_amount}
                        for s in sorted(meal.shares, key=lambda s: s.id or 0)]}]


def _meal_updated(meal, kind: str) -> dict:
    if kind == "meal_void":
        return {"event": kind, "meal_id": meal.id, "voided": bool(meal.voided),
                "voided_by": meal.voided_by, "voided_at": dump_value(meal.voided_at)}
    return {"event": kind, "meal_id": meal.id, "place_id": meal.place_id}


def _share_inserted(share) -> list[dict]:
    """A share is recorded with its meal (the meal's event carries it). A new share for a
    meal recorded earlier would change that meal's split: refused."""
    from sqlalchemy.orm import object_session

    if share.meal is not None and share.meal in object_session(share).new:
        return []
    raise LedgerImmutable(f"meal #{share.meal_id}: shares are fixed when the meal is recorded")


def _payment_inserted(pay) -> list[dict]:
    return [{"event": "payment", "_order": pay.id, **columns(pay, PAYMENT_COLUMNS)}]


def _payment_updated(pay, kind: str) -> dict:
    if kind == "payment_void":
        return {"event": kind, "payment_id": pay.id, "voided": bool(pay.voided),
                "voided_by": pay.voided_by, "voided_at": dump_value(pay.voided_at)}
    return {"event": kind, "payment_id": pay.id, "meal_id": pay.meal_id}


def _settlement_inserted(st) -> list[dict]:
    return [{"event": "settlement", "_order": st.id, **columns(st, SETTLEMENT_COLUMNS)}]


def register_ledger() -> None:
    from ledger_core.models import Meal, MealShare, Payment, Settlement

    void = {"voided": "meal_void", "voided_by": "meal_void", "voided_at": "meal_void"}
    register(Mirror(Meal, "ledger", lambda r: r.room_id, _meal_inserted,
                    {**void, "place_id": "meal_place"}, _meal_updated))
    register(Mirror(MealShare, "ledger", lambda r: r.meal.room_id if r.meal else 0, _share_inserted, {}))
    register(Mirror(Payment, "ledger", lambda r: r.room_id, _payment_inserted,
                    {"voided": "payment_void", "voided_by": "payment_void", "voided_at": "payment_void",
                     "meal_id": "payment_retarget"}, _payment_updated))
    register(Mirror(Settlement, "ledger", lambda r: r.room_id, _settlement_inserted, {}))
