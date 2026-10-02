"""Per-room lunch memory: prose that decays, and standing rules that do not.

Stored in the ``notes`` internal collection (:mod:`app.store`, plan 2026-10-02 S2),
one document per fact. It used to be one file per room,
``{DATA_DIR}/rooms/{room_id}/observations.md``, one line per fact in four
pipe-separated fields — still the format :func:`parse_file` reads, for the one-time
import (:mod:`app.migrate_storage`) and for the seed installer::

    - 2026-03-03 | place:com-ga-thinh-lo | -              | Very slow, an hour before the food came.
    - always     | place:com-ga-thinh-lo | order-by@11:30 | Must order ahead — call by phone.
    - always     | member:nhim           | -              | Suggests a place, then changes their mind.

Why these four and not a table (design D4):

``when``    a date, or ``always``. Dated lines are *observations* and decay — a
            complaint from eight months ago is weak evidence. ``always`` lines
            are *standing rules* and never age out: "must order before 11:30" is as
            true next year as today. One field separates two lifetimes.
``subject`` ``place:<slug>`` joins :mod:`app.places`; ``member:<nickname>`` joins
            :mod:`app.roster`. This is what makes the numbers and the prose
            describe the same thing.
``gate``    ``-``, or a clock rule Python evaluates (:func:`gate_status`).
``text``    free prose as the room wrote it (usually Vietnamese), untouched.
            "Nhím suggested a place, then changed her mind" is not
            table-shaped and schematising it would destroy the meaning.

A fact's id is still its ``line_id`` — a hash of its four fields — so identical
facts are one fact and the knowledge panel's contract is unchanged. ``seq`` keeps
the order the room wrote them in. Every function takes the caller's ``session``: a
note and whatever triggered it (a memo card's status) commit together.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, replace
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app import store
from app.memory import room_memory_dir

logger = logging.getLogger("chiatienan")

#: How far back a dated observation still counts. Rules ignore this entirely.
DEFAULT_SINCE_DAYS = 180

#: The hour is bounded at 23, not at ``[0-2]\d``: the file is hand-editable, and a
#: stray ``busy@25:00`` used to satisfy the pattern and then reach
#: ``now.replace(hour=25)`` in :func:`gate_status` — one typo crashing every lunch
#: suggestion for the room. Failing the regex instead demotes the gate to prose,
#: which is what :func:`_parse_line` already does with anything it cannot read.
_GATE_RE = re.compile(r"^(busy|order-by|closes)@([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class Observation:
    when: date | None            # None == "always" (a standing rule)
    subject: str                 # "place:<slug>" | "member:<nickname>"
    gate: str | None             # "busy@12:00" | "order-by@11:30" | "closes@12:30"
    text: str

    @property
    def is_rule(self) -> bool:
        return self.when is None

    def to_line(self) -> str:
        when = self.when.isoformat() if self.when else "always"
        return f"- {when} | {self.subject} | {self.gate or '-'} | {self.text}"

    @property
    def line_id(self) -> str:
        """A stable handle for this fact, computed from its own four fields.

        The UI needs to name one line in order to edit or delete it, and the file
        has no id column — deliberately, because it is meant to stay hand-editable
        (design D4) and the seed installer writes it by hand. Hashing the rendered
        line gives an id that survives a reload, survives lines being added above
        it, and costs the format nothing.

        Two byte-identical lines collide. That is correct: they are the same fact
        written twice, and removing either one satisfies the request.
        """
        return hashlib.sha1(self.to_line().encode("utf-8")).hexdigest()[:12]


def _parse_line(raw: str, lineno: int) -> Observation | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    if not line.startswith("- "):
        logger.warning("[observations] line %s: no leading '- ', skipped", lineno)
        return None
    # maxsplit=3 so the prose may itself contain a pipe.
    parts = [p.strip() for p in line[2:].split("|", 3)]
    if len(parts) != 4:
        logger.warning("[observations] line %s: expected 4 fields, got %s", lineno, len(parts))
        return None
    when_raw, subject, gate_raw, text = parts
    if when_raw == "always":
        when = None
    else:
        try:
            when = date.fromisoformat(when_raw)
        except ValueError:
            logger.warning("[observations] line %s: bad date %r, skipped", lineno, when_raw)
            return None
    if not subject.startswith(("place:", "member:")) or len(subject.split(":", 1)[1]) == 0:
        logger.warning("[observations] line %s: bad subject %r, skipped", lineno, subject)
        return None
    gate = None if gate_raw in ("-", "") else gate_raw
    if gate and not _GATE_RE.match(gate):
        logger.warning("[observations] line %s: unknown gate %r, kept as prose", lineno, gate)
        gate = None
    if not text:
        logger.warning("[observations] line %s: empty text, skipped", lineno)
        return None
    return Observation(when=when, subject=subject, gate=gate, text=text)


# ------------------------------------------------------------------ storage

def _docs(session: Session, room_id: int) -> list[tuple[dict, Observation]]:
    """``(document, observation)`` in the room's order (``seq``)."""
    out = []
    for doc in store.DATA.read_all(store.collection(session, "notes"), room_id, session=session):
        d = doc["data"]
        out.append((d, Observation(
            when=None if d["when"] == "always" else date.fromisoformat(d["when"]),
            subject=d["subject"], gate=d.get("gate") or None, text=d["text"])))
    out.sort(key=lambda pair: pair[0]["seq"])
    return out


def _put(session: Session, room_id: int, obs: Observation, seq: int) -> None:
    data = {"id": obs.line_id, "seq": seq, "when": obs.when.isoformat() if obs.when else "always",
            "subject": obs.subject, "text": obs.text}
    if obs.gate:
        data["gate"] = obs.gate
    store.DATA.upsert_document(store.collection(session, "notes"), room_id, data,
                               actor="notes", session=session)


def _drop(session: Session, room_id: int, line_id: str) -> None:
    store.DATA.delete_document(store.collection(session, "notes"), room_id, line_id,
                               actor="notes", session=session)


def load(session: Session, room_id: int) -> list[Observation]:
    return [o for _d, o in _docs(session, room_id)]


def etag(session: Session, room_id: int) -> str:
    """Fingerprint of the room's notes as they stand, for optimistic concurrency.

    The turn loop adds notes too, so an editor that writes blind loses whichever
    change landed first. Callers hand this back on write; a mismatch is a refusal,
    not a merge. Any add, edit (a new ``line_id``) or delete changes it.
    """
    raw = "|".join(f"{d['seq']}:{d['id']}" for d, _o in _docs(session, room_id))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def append(session: Session, room_id: int, obs: Observation) -> bool:
    """Add a fact at the end. False — and nothing written — when an identical fact
    is already there (same ``line_id``: they are the same fact)."""
    docs = _docs(session, room_id)
    if any(d["id"] == obs.line_id for d, _o in docs):
        return False
    _put(session, room_id, obs, max((d["seq"] for d, _o in docs), default=0) + 1)
    return True


def replace_line(session: Session, room_id: int, line_id: str, obs: Observation) -> bool:
    """Rewrite one fact in place (it keeps its position). False when ``line_id`` is no
    longer present. Rewriting it into an exact copy of another fact keeps that other
    one — two identical facts cannot both exist."""
    docs = _docs(session, room_id)
    current = next((d for d, _o in docs if d["id"] == line_id), None)
    if current is None:
        return False
    if obs.line_id != line_id:
        _drop(session, room_id, line_id)
        if any(d["id"] == obs.line_id for d, _o in docs):
            return True
    _put(session, room_id, obs, current["seq"])
    return True


def delete_line(session: Session, room_id: int, line_id: str) -> bool:
    if not any(d["id"] == line_id for d, _o in _docs(session, room_id)):
        return False
    _drop(session, room_id, line_id)
    return True


def remove(session: Session, room_id: int, *, subject: str, text: str) -> bool:
    """Delete the earliest fact matching ``subject`` + ``text``. True if one went.

    No tombstone and no history: this is a lunch note, not the ledger.
    """
    for d, o in _docs(session, room_id):
        if o.subject == subject and o.text == text:
            _drop(session, room_id, d["id"])
            return True
    return False


def retarget_subject(session: Session, room_id: int, *, old: str, new: str) -> dict:
    """Move every fact filed under ``old`` to ``new``. ``{"moved", "deduped"}``.

    **Duplicates are dropped, not written.** A fact's id is a hash of its fields, so
    a moved fact that lands exactly on one the room already holds *is* that fact: the
    moved copy goes. A pre-existing identical pair under ``old`` therefore collapses
    to one, which is a repair rather than a loss.
    """
    docs = _docs(session, room_id)
    held = {d["id"] for d, _o in docs}
    moved = deduped = 0
    for d, o in docs:
        if o.subject != old:
            continue
        target = replace(o, subject=new)
        _drop(session, room_id, d["id"])
        held.discard(d["id"])
        if target.line_id in held:
            deduped += 1
            continue
        _put(session, room_id, target, d["seq"])
        held.add(target.line_id)
        moved += 1
    return {"moved": moved, "deduped": deduped}


def for_subjects(session: Session, room_id: int, subjects: list[str], *,
                 since_days: int = DEFAULT_SINCE_DAYS, today=None) -> list[Observation]:
    """Rules for ``subjects`` (always), plus their observations inside the window.

    Recency filtering applies to dated facts only — a standing rule can never be
    aged out, which is the whole reason the two share a store but not a lifetime.
    """
    from app.clock import today_ict

    today = today or today_ict()
    cutoff = today - timedelta(days=since_days)
    wanted = set(subjects)
    return [o for o in load(session, room_id)
            if o.subject in wanted and (o.is_rule or o.when >= cutoff)]


def count_since(session: Session, room_id: int, subject: str, *, since: date) -> int:
    """How many dated observations about ``subject`` since ``since``.

    This is what lets Phoenix say "the 3rd time this month" without counting: Python
    counts and hands over the number. A model that tallies by eye gets it wrong
    eventually, and a confidently wrong count poisons trust in everything else it
    says — the same rule as money, applied to social facts.
    """
    return sum(1 for o in load(session, room_id)
               if o.subject == subject and not o.is_rule and o.when >= since)


# ------------------------------------------------------------- the old file

def legacy_path(room_id: int):
    """Where a room's notes lived before the import (``observations.md``)."""
    return room_memory_dir(room_id) / "observations.md"


def parse_text(text: str) -> tuple[list[Observation], dict]:
    """Every fact in ``observations.md``-format text, in order, plus a report of what
    was not a fact: ``{"comments", "blank", "dropped": [(lineno, line)]}``.

    A malformed line was always skipped with a warning (one stray line costs one fact,
    not lunch); the report says which, so an import loses nothing silently (review R8).
    """
    facts: list[Observation] = []
    report = {"comments": 0, "blank": 0, "dropped": []}
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            report["blank"] += 1
        elif line.startswith("#"):
            report["comments"] += 1
        elif (o := _parse_line(raw, i)) is not None:
            facts.append(o)
        else:
            report["dropped"].append((i, raw))
    return facts, report


def parse_file(path) -> tuple[list[Observation], dict]:
    """:func:`parse_text` over a file; a missing file is empty."""
    from pathlib import Path

    path = Path(path)
    return parse_text(path.read_text(encoding="utf-8") if path.exists() else "")


#: Every walk-to place sits a few minutes from the office, so one room-wide
#: constant beats a hundred hand-set numbers (design D17). ``Place.walk_minutes``
#: overrides it for the outlier that eventually needs one.
DEFAULT_WALK_MINUTES = 5

#: How close to a deadline counts as "go now" rather than "you're fine".
_ACT_NOW_BUSY = 15      # minutes
_ACT_NOW_ORDER = 20


def gate_kind(obs: Observation) -> str | None:
    """``"busy"`` / ``"order-by"`` / ``"closes"``, or None when ungated."""
    m = _GATE_RE.match(obs.gate or "")
    return m.group(1) if m else None


#: How each gate verb reads to a human. ``_GATE_RE`` is the schema, so this is the
#: only place a person should ever have to meet ``order-by@11:30``. Read by the
#: knowledge panel only — ``tools`` branches on :func:`gate_kind`, so these are
#: display strings and nothing decides on them.
_GATE_LABELS = {"busy": "Busy from", "order-by": "Order by", "closes": "Closes"}


def gate_at(obs: Observation) -> str | None:
    """``"11:30"`` for ``order-by@11:30``; None when ungated."""
    m = _GATE_RE.match(obs.gate or "")
    return f"{m.group(2)}:{m.group(3)}" if m else None


def gate_label(obs: Observation) -> str | None:
    """``"Order by 11:30"`` for ``order-by@11:30``; None when ungated."""
    m = _GATE_RE.match(obs.gate or "")
    if not m:
        return None
    return f"{_GATE_LABELS[m.group(1)]} {m.group(2)}:{m.group(3)}"


def parse_gate(kind: str | None, at: str | None) -> str | None:
    """Build a gate from a picker's ``(kind, "HH:MM")``, validating both.

    Raises :class:`ValueError` on anything ``_GATE_RE`` would not match, so a bad
    gate is a refusal at the edge rather than a field that quietly vanishes when
    the file is next read.
    """
    if not kind:
        return None
    gate = f"{kind}@{at or ''}"
    if not _GATE_RE.match(gate):
        raise ValueError(f"Unknown clock rule «{gate}».")
    return gate


def gate_status(obs: Observation, *, now, walk_minutes: int | None = None
                ) -> tuple[str, int | None]:
    """``(status, minutes_left)`` for a gated observation at wall-clock ``now``.

    Status is ``ok`` / ``act_now`` / ``too_late``. All the clock arithmetic
    happens here rather than in the model: "is 11:55 too late for an 11:30
    deadline" is exactly the sort of sum a model gets wrong on the day it
    matters, which is why ``resolve_date`` exists too.

    ``busy@`` and ``closes@`` are travel-aware — the question is whether you can
    *get there* in time, not whether the clock has passed. ``order-by@`` is not:
    you phone ahead, so the walk is irrelevant.

    The two travel-aware verbs share their arithmetic but not their meaning:
    "it'll be busy, leave early" is advice and "it's closed" is a refusal, and a room
    told the wrong one wastes a walk. Callers read :func:`gate_kind`.
    """
    m = _GATE_RE.match(obs.gate or "")
    if not m:
        return "ok", None
    kind, hh, mm = m.group(1), int(m.group(2)), int(m.group(3))
    deadline = now.replace(hour=hh, minute=mm, second=0, microsecond=0)

    if kind == "order-by":
        left = int((deadline - now).total_seconds() // 60)
        if left < 0:
            return "too_late", None
        return ("act_now", left) if left <= _ACT_NOW_ORDER else ("ok", left)

    walk = DEFAULT_WALK_MINUTES if walk_minutes is None else walk_minutes
    eta = now + timedelta(minutes=walk)
    left = int((deadline - eta).total_seconds() // 60)
    if left < 0:
        return "too_late", None
    return ("act_now", left) if left <= _ACT_NOW_BUSY else ("ok", left)
