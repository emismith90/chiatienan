"""Per-room lunch memory: prose that decays, and standing rules that do not.

One file, ``{DATA_DIR}/rooms/{room_id}/observations.md``, sibling to
``memory.md`` and reached through :func:`app.memory.room_memory_dir`. One line
per fact, four pipe-separated fields::

    - 2026-03-03 | place:com-ga-thinh-lo | -              | Làm quá chậm, 1 tiếng mới có món.
    - always     | place:com-ga-thinh-lo | order-by@11:30 | Phải đặt trước — gọi điện thoại.
    - always     | member:nhim           | -              | Đề xuất quán rồi lại đổi ý.

Why these four and not a table (design D4):

``when``    a date, or ``always``. Dated lines are *observations* and decay — a
            complaint from eight months ago is weak evidence. ``always`` lines
            are *standing rules* and never age out: "phải gọi trước 11h30" is as
            true next year as today. One field separates two lifetimes.
``subject`` ``place:<slug>`` joins :mod:`app.places`; ``member:<nickname>`` joins
            :mod:`app.roster`. This is what makes the numbers and the prose
            describe the same thing.
``gate``    ``-``, or a clock rule Python evaluates (:func:`gate_status`).
``text``    free Vietnamese prose, untouched. "Nhím đề xuất rồi lại đổi ý" is not
            table-shaped and schematising it would destroy the meaning.

The file is editable two ways — the knowledge panel (:mod:`app.knowledge`) and a
text editor on the box — so **a malformed line is skipped with a warning, never
raised**: one stray line must cost one fact, not lunch. Every write here edits the
raw line list in place, so the comments and unreadable lines the parser skips
survive an edit made through the UI.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.memory import room_memory_dir

logger = logging.getLogger("chiatienan")

_FILE = "observations.md"

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


def _path(room_id: int):
    return room_memory_dir(room_id) / _FILE


def _read_raw(room_id: int) -> list[str]:
    """Every line of the file, verbatim, newlines stripped."""
    path = _path(room_id)
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _write_raw(room_id: int, lines: list[str]) -> None:
    _path(room_id).write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")


def indexed(room_id: int) -> list[tuple[int, Observation]]:
    """``(raw line index, observation)`` for every line that parses.

    The index is the anchor every edit rides on. Rewriting the file from
    :func:`load`'s output instead — which is what :func:`remove` used to do —
    silently deletes the comments and malformed lines :func:`_parse_line`
    deliberately skips, turning "one stray line costs one fact" into "one edit
    costs every line the parser didn't like".
    """
    out = []
    for i, raw in enumerate(_read_raw(room_id)):
        if (o := _parse_line(raw, i + 1)) is not None:
            out.append((i, o))
    return out


def load(room_id: int) -> list[Observation]:
    return [o for _i, o in indexed(room_id)]


def file_etag(room_id: int) -> str:
    """Fingerprint of the file as it stands, for optimistic concurrency.

    The turn loop appends to this file too, so an editor that writes blind loses
    whichever change landed first. Callers hand this back on write; a mismatch is
    a refusal, not a merge.
    """
    path = _path(room_id)
    data = path.read_bytes() if path.exists() else b""
    return hashlib.sha256(data).hexdigest()[:16]


def append(room_id: int, obs: Observation) -> None:
    path = _path(room_id)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    prefix = "" if (not existing or existing.endswith("\n")) else "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(prefix + obs.to_line() + "\n")


def _find(room_id: int, line_id: str) -> int | None:
    for i, o in indexed(room_id):
        if o.line_id == line_id:
            return i
    return None


def replace_line(room_id: int, line_id: str, obs: Observation) -> bool:
    """Rewrite one line in place. False when ``line_id`` is no longer present."""
    i = _find(room_id, line_id)
    if i is None:
        return False
    lines = _read_raw(room_id)
    lines[i] = obs.to_line()
    _write_raw(room_id, lines)
    return True


def delete_line(room_id: int, line_id: str) -> bool:
    """Drop one line, leaving every other byte of the file alone."""
    i = _find(room_id, line_id)
    if i is None:
        return False
    lines = _read_raw(room_id)
    del lines[i]
    _write_raw(room_id, lines)
    return True


def remove(room_id: int, *, subject: str, text: str) -> bool:
    """Delete the first line matching ``subject`` + ``text``. True if one went.

    No tombstone and no history: this is a lunch note, not the ledger. Everything
    else in the file — comments, and lines the parser could not read — survives
    untouched; see :func:`indexed`.
    """
    for i, o in indexed(room_id):
        if o.subject == subject and o.text == text:
            lines = _read_raw(room_id)
            del lines[i]
            _write_raw(room_id, lines)
            return True
    return False


def for_subjects(room_id: int, subjects: list[str], *,
                 since_days: int = DEFAULT_SINCE_DAYS, today=None) -> list[Observation]:
    """Rules for ``subjects`` (always), plus their observations inside the window.

    Recency filtering applies to dated lines only — a standing rule can never be
    aged out, which is the whole reason the two share a file but not a lifetime.
    """
    from app.clock import today_ict

    today = today or today_ict()
    cutoff = today - timedelta(days=since_days)
    wanted = set(subjects)
    return [o for o in load(room_id)
            if o.subject in wanted and (o.is_rule or o.when >= cutoff)]


def count_since(room_id: int, subject: str, *, since: date) -> int:
    """How many dated observations about ``subject`` since ``since``.

    This is what lets Phoenix say "lần thứ 3 tháng này" without counting: Python
    counts the lines and hands over the number. A model that tallies by eye gets
    it wrong eventually, and a confidently wrong count poisons trust in
    everything else it says — the same rule as money, applied to social facts.
    """
    return sum(1 for o in load(room_id)
               if o.subject == subject and not o.is_rule and o.when >= since)


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
    "sẽ đông, ra sớm đi" is advice and "đóng cửa rồi" is a refusal, and a room
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
