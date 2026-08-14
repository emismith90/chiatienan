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


#: Wording that tells the room a ledger entry now exists. Deliberately only the
#: *past-tense* forms: "chưa ghi được" (couldn't record) and "mình sẽ ghi" (I'll
#: record) are the honest replies this guard must never touch.
_COMMIT_CLAIM = re.compile(
    r"đã\s+ghi\b|đã\s+lưu\b|đã\s+ghi\s+sổ|đã\s+cập\s+nhật\s+sổ|đã\s+vào\s+sổ"
    r"|\brecorded\b|\blogged\s+(?:it|this|that)\b",
    re.IGNORECASE,
)

#: The only tools whose success can make a commit claim true. A *successful*
#: ``propose_meal``/``propose_payment`` never reaches the guard anyway — those
#: turns end on the draft-card branch — so in practice this admits the turns
#: where a write was genuinely attempted and lets the failed ones through to it.
COMMIT_TOOLS = frozenset({"propose_meal", "propose_payment", "void_meal", "cancel_draft"})


def fabricated_commit(body: str, user_text: str, tools) -> list[int] | None:
    """The unbacked amounts in ``body``, if ``body`` claims a write nothing made.

    Returns ``None`` when the reply is fine. Three conditions must hold together,
    because each alone has honest explanations:

    1. the reply claims the ledger was written (:data:`_COMMIT_CLAIM`);
    2. no :data:`COMMIT_TOOLS` call succeeded this turn, so nothing wrote;
    3. money in the reply is unbacked — the amounts came from neither the user,
       the conversation, nor a tool.

    (3) is what keeps the bot able to talk about the past. "Bữa qua mình đã ghi
    rồi, 175,000đ" quotes a total the handed-in history already contains, so its
    amounts are backed and the reply stands. A confirmation assembled out of a
    bill photo cannot clear it — which is the point, since that is the one the
    room cannot tell apart from a real one.
    """
    if not body or not _COMMIT_CLAIM.search(body):
        return None
    for inv in tools or []:
        if inv.name in COMMIT_TOOLS and isinstance(inv.result, dict) and inv.result.get("ok"):
            return None
    return unbacked_amounts(body, user_text, tools) or None
