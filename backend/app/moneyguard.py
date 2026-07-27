"""Detect money in a bot reply that no tool produced.

Design D3 says the ledger's numbers reach the room from tool results, never
retyped by the model — and :mod:`app.chat` enforces that for every reply it
builds itself (settlement, statement, summary, meal, payment). The gap is the
fallback: a turn that runs no money tool posts ``TurnResult.final_text``
verbatim, and the model will happily type amounts into it. Production has two
examples from one afternoon — a six-row balance table assembled from
conversation history, and six post-discount shares the model worked out with
bash and then recorded.

So: pull every amount out of the reply, and out of everything that legitimately
knows an amount this turn (the user's own message, and every tool call's args and
results). What is left is untraceable.

This module only *reports*. Blocking a reply on a false positive would be worse
than the disease, and the false-positive rate is unknown until it has run
against real traffic — the numbers a user reads off a bill image, for one, are
unattributable by construction. :func:`unbacked_amounts` feeds a log warning
today; enforcement is a second step, once the log is quiet.
"""
from __future__ import annotations

import json
import re

#: ISO dates first — "2026-07-27" would otherwise donate a bare 2026.
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: A number with optional thousands separators and an optional VND unit.
_AMOUNT = re.compile(
    r"(?<![\w.,])(\d[\d.,]*)\s*(k|tr|triệu|đ|vnd|d)?\b",
    re.IGNORECASE,
)

#: Below this, a bare number is far more likely to be a count, an id or a
#: weekday ("6 người", "#101", "T5") than an amount of money.
_BARE_MIN = 1000


def _as_int(digits: str) -> int | None:
    """``"324.200"`` / ``"324,200"`` / ``"324200"`` -> 324200; junk -> None."""
    cleaned = digits.replace(".", "").replace(",", "")
    return int(cleaned) if cleaned.isdigit() else None


def money_tokens(text: str) -> set[int]:
    """Every VND amount a reader would take away from ``text``.

    Counts a number as money when it carries a unit (``đ``, ``k``, ``tr``), or
    is grouped with separators, or is a bare integer of at least
    :data:`_BARE_MIN`. ``840k`` and ``1tr`` are expanded, so the set holds
    comparable integers however the text spelled them.
    """
    found: set[int] = set()
    for digits, unit in _AMOUNT.findall(_ISO_DATE.sub(" ", text or "")):
        value = _as_int(digits)
        if value is None:
            continue
        suffix = (unit or "").lower()
        if suffix == "k":
            found.add(value * 1_000)
        elif suffix in ("tr", "triệu"):
            found.add(value * 1_000_000)
        elif suffix in ("đ", "d", "vnd") or "." in digits or "," in digits:
            found.add(value)
        elif value >= _BARE_MIN:
            found.add(value)
    return found


def _numbers_in(obj) -> set[int]:
    """Every integer anywhere in a tool's args/result, however nested."""
    try:
        blob = json.dumps(obj, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = str(obj)
    return {int(m) for m in re.findall(r"\d+", blob)}


def backed_amounts(user_text: str, tools) -> set[int]:
    """Amounts this turn can account for: the user's own, and every tool's.

    ``tools`` is :attr:`app.agent.TurnResult.tools`. Both args and results count
    — an amount the model passed *into* a tool was the user's to begin with.
    """
    allowed = money_tokens(user_text)
    for inv in tools or []:
        allowed |= _numbers_in(getattr(inv, "args", None))
        allowed |= _numbers_in(getattr(inv, "result", None))
    return allowed


def unbacked_amounts(body: str, user_text: str, tools) -> list[int]:
    """Amounts in ``body`` that neither the user nor any tool this turn produced."""
    return sorted(money_tokens(body) - backed_amounts(user_text, tools))
