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

:func:`unbacked_amounts` only *reports*: blocking a reply on a false positive
would be worse than the disease, and the numbers a user reads off a bill image
are unattributable by construction — correct, but untraceable.

:func:`fabricated_commit` is the enforcing half, and it is narrow on purpose. It
fires only on the one class that is wrong no matter where the numbers came from:
a reply that *claims the ledger was written* when no tool wrote it. That claim is
never the model's to make — the room's confirmations ("Đã ghi #14 — …", "💸 A trả
B 50,000đ") are rendered server-side by :mod:`app.chat` from a commit's own
result dict, and only on the commit routes, never on the fallback path that
posts model prose.
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


#: Separators only count as thousands grouping when they sit *between* digits in
#: threes. "793,760" is money; "#13," is a meal id followed by a comma, and
#: treating that trailing comma as grouping invented a 13đ amount out of
#: ordinary prose — enough to condemn an honest sentence once the guard started
#: blocking on the result.
_GROUPED = re.compile(r"\d{1,3}(?:[.,]\d{3})+")


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
        # A separator the number merely ends on ("#13,") is punctuation.
        digits = digits.rstrip(".,")
        value = _as_int(digits)
        if value is None:
            continue
        suffix = (unit or "").lower()
        if suffix == "k":
            found.add(value * 1_000)
        elif suffix in ("tr", "triệu"):
            found.add(value * 1_000_000)
        elif suffix in ("đ", "d", "vnd") or _GROUPED.fullmatch(digits):
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


#: Wording that tells the room a ledger entry now exists. Deliberately only the
#: *past-tense* forms: "chưa ghi được" (couldn't record) and "mình sẽ ghi" (I'll
#: record) are the honest replies this guard must never touch.
_COMMIT_CLAIM = re.compile(
    r"đã\s+ghi\b|đã\s+lưu\b|đã\s+ghi\s+sổ|đã\s+cập\s+nhật\s+sổ|đã\s+vào\s+sổ"
    r"|\brecorded\b|\blogged\s+(?:it|this|that)\b",
    re.IGNORECASE,
)

#: ``Đã ghi #14`` — the meal-id form of ``chat._meal_body``. A claim that names a
#: number is checkable against the ledger itself, which is the only test a
#: forgery cannot launder its way past (see :func:`fabricated_commit`).
_CLAIMED_MEAL_ID = re.compile(r"đã\s+ghi\s*#\s*(\d+)", re.IGNORECASE)


def claimed_meal_ids(body: str) -> list[int]:
    """Meal ids ``body`` asserts were written, in order of appearance."""
    return [int(n) for n in _CLAIMED_MEAL_ID.findall(body or "")]


#: The only tools whose success can make a commit claim true. A *successful*
#: ``propose_meal``/``propose_payment`` never reaches the guard anyway — those
#: turns end on the draft-card branch — so in practice this admits the turns
#: where a write was genuinely attempted and lets the failed ones through to it.
COMMIT_TOOLS = frozenset({"propose_meal", "propose_payment", "void_meal", "cancel_draft"})


def fabricated_commit(body: str, user_text: str, tools, *, meal_exists=None,
                      commit_tools: frozenset[str] | set[str] | None = None) -> list[int] | None:
    """Why ``body`` is a forged write claim, or ``None`` if it is not.

    Returns ``None`` for a reply that may be posted, otherwise the offending
    amounts — **possibly an empty list**, when the giveaway was a meal id rather
    than an amount. Callers must test ``is not None``; an emptiness test would
    post exactly the forgeries test 1 exists to catch.

    A reply must first claim the ledger was written (:data:`_COMMIT_CLAIM`) with
    no :data:`COMMIT_TOOLS` call succeeding this turn. Past that gate there are
    two independent tests, and either one condemns it:

    1. **The ledger disagrees.** ``meal_exists`` — ``Callable[[int], bool]``
       scoped to this room — is asked about every ``Đã ghi #N``. A meal id that
       is not in the ledger cannot have been recorded, whatever the prose says.
    2. **The money is untraceable.** Amounts that came from neither the user, the
       conversation, nor a tool this turn.

    Test 2 alone is not enough, and production proved it. The 2026-08-14 forgery
    was posted, so its numbers entered the room's own history — and the history
    is part of the allow-set, by design, so the bot can quote what the room
    already said. Every *repeat* of that forgery therefore came back clean:
    asked three more times over the next 44 minutes, the model reproduced the
    same 157 characters with ``tools=0`` and test 2 stayed silent each time. A
    forgery that is posted once launders itself into evidence for its own
    repeats. Test 1 does not care: meal #14 was never in ``meals``, so it is
    condemned on the first telling and on the thousandth.

    Test 2 still earns its place — it catches a claim that names no id — and it
    is still what keeps honest recall working: "bữa qua mình đã ghi rồi,
    175,000đ" quotes a total from the handed-in history, names no meal id, and
    passes.
    """
    if not body or not _COMMIT_CLAIM.search(body):
        return None
    admitted = COMMIT_TOOLS if commit_tools is None else commit_tools
    for inv in tools or []:
        if inv.name in admitted and isinstance(inv.result, dict) and inv.result.get("ok"):
            return None
    if meal_exists is not None:
        for meal_id in claimed_meal_ids(body):
            if not meal_exists(meal_id):
                return unbacked_amounts(body, user_text, tools)
    return unbacked_amounts(body, user_text, tools) or None
