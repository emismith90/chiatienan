"""Prod cases where production's tool choice is **not** the only right answer.

The prod corpus derives every expectation from what production did, and production
was the engine being replaced — so an expectation here is *evidence of one working
answer*, never proof that it is the answer. Where a different read-only tool answers
the same question as well or better, that alternative is recorded here with the
reason, and `grade_tool_selection` accepts it.

Three rules keep this from becoming a way to make numbers look good:

1. **Read-only tools only** (`_WIDENABLE`). A judgement may never widen a tool that
   writes money — `propose_meal`, `propose_payment`, `add_member`, `void_meal`. If a
   money-writing turn was wrong, it was wrong, and the fix belongs in the prompt.
   `p20` ("paid my part") is the worked example: the model asked which creditor
   instead of proposing, and the answer was to teach `record-payment` to propose one
   payment per creditor — not to accept the question.
2. **Every entry carries `why`,** written after reading the conversation around it.
3. **`bench.report` prints the count,** so nobody reads a pass rate without knowing
   how many cases carry a judgement.

Money arguments are never touched. An entry only ever adds to the accepted tool set.
"""
from __future__ import annotations

#: Tools a judgement may add. All read-only: `settle_period` computes a settlement
#: preview and writes nothing (`tools.py` — "Nothing is written… a running total"),
#: and the other three are pure queries.
_WIDENABLE = frozenset(("settle_period", "get_period_summary", "member_statement",
                        "pick_random"))

#: `case_id -> {"tools_ok": [...], "why": "..."}`
JUDGEMENTS: dict[str, dict] = {
    "p10": {
        "tools_ok": ["get_period_summary"],
        "why": "'@bot room balance now' asks for the room's state, and prod happened "
               "to answer it with a settlement preview. `get_period_summary` is the "
               "state query — it is what `balances.SKILL.md` routes group-state "
               "questions to, and on an empty ledger both replies say the same "
               "thing. Either tool answers the question.",
    },
    "p23": {
        "tools_ok": ["member_statement"],
        "why": "'@bot how much do I owe' is first person. `member_statement` answers "
               "exactly that — per creditor, per meal, for the asker — and the "
               "system prompt has always routed first-person debt questions to it. "
               "Prod answered with the whole group's settlement, which is more than "
               "was asked for.",
    },
    "p28": {
        "tools_ok": ["get_period_summary"],
        "why": "'@bot show current state' is the phrase `balances.SKILL.md` routes to "
               "`get_period_summary` by name ('summary', 'current state', 'tổng "
               "kết'). Prod answered with a settlement preview. Both describe where "
               "the room stands; the summary is the one that lists it per meal.",
    },
    "p32": {
        "tools_ok": ["member_statement"],
        "why": "'tôi còn nợ bao nhiêu, nợ buổi nào và nợ ai?' asks three first-person "
               "questions, and `member_statement` is the tool that answers all three "
               "at once — amount, meal, creditor. A group settlement answers none of "
               "them directly.",
    },
    "p41": {
        "tools_ok": ["get_period_summary"],
        "why": "'@bot current balances' — same state query as p28, same reasoning.",
    },
    "p43": {
        "tools_ok": ["get_period_summary", "member_statement"],
        "why": "'sao A3 chỉ nợ tôi 14k' asks *why* a settlement number came out that "
               "way. The answer is the per-meal detail behind it, which is what "
               "`member_statement` (for the asker) and the summary's `outstanding` "
               "list both show. Re-running the settlement, as prod did, restates the "
               "14,000đ without explaining it.",
    },
    "p52": {
        "tools_ok": ["get_period_summary"],
        "why": "'@bot current balance' — same state query as p28, same reasoning.",
    },
    "p93": {
        "tools_ok": ["get_period_summary", "member_statement"],
        "why": "'sai mẹ nội A4 chuyển khoản r' disputes a settlement that was just "
               "printed. Answering it means looking at what the ledger actually "
               "holds for those two people, which is what the summary and the "
               "statement show; prod re-printed the same settlement.",
    },
    "p132": {
        "tools_ok": ["get_period_summary"],
        "why": "'#101: A1 trả 324,000đ (6 người)? registered ?????' asks whether a "
               "draft was recorded. The summary lists what *is* on the books, which "
               "is the answer; prod's settlement preview mentions the pending draft "
               "only as the reason it is blocked.",
    },
    "p152": {
        "tools_ok": ["get_period_summary"],
        "why": "'hôm nay ai trả tiền' asks who paid today — a period query, and "
               "`get_period_summary(keyword=\"today\")` answers it by name and "
               "amount. A settlement answers who owes whom instead.",
    },
    "p160": {
        "tools_ok": ["get_period_summary"],
        "why": "'grab food hôm nay à' asks to confirm today's meal; same period "
               "query as p152.",
    },
}

#: `case_id -> why` for cases whose recorded expectation **cannot be graded**, with
#: the reason. Unlike a judgement these do not accept an alternative — they remove
#: the tool expectation entirely, so `tool_selection` reports "not graded" rather
#: than a verdict nobody should believe.
UNGRADEABLE: dict[str, str] = {
    "p100": "'@bot i paid, 324k' — production read the per-dish prices off a bill "
            "photo. Prod images are stripped from the corpus by design, so the "
            "information the recorded turn used is not in the case. Asking for the "
            "prices, which is what the replay does, is the only honest answer "
            "available to it.",
    "p260": "'@bot A5 đã trả' produced a 930,000đ meal from an attached photo. Same "
            "reason as p100: without the image there is no total to log.",
    "p278": "'set A4 and A7 to be opted out by default' is a membership change, and "
            "the replay does exactly that (`update_member` twice). The recorded "
            "expectation is `settle_period`, which has nothing to do with the "
            "request — the log's own reply for this turn is a settlement card that "
            "belongs to a different exchange. The expectation is wrong, not the "
            "answer, and a wrong expectation should grade nothing.",
}


def check() -> None:
    """Fail loudly if a judgement breaks its own rules."""
    for case_id, why in UNGRADEABLE.items():
        if not why.strip():
            raise ValueError(f"{case_id}: an ungradeable case needs a reason")
        if case_id in JUDGEMENTS:
            raise ValueError(f"{case_id}: cannot be both judged and ungradeable")
    for case_id, entry in JUDGEMENTS.items():
        if not entry.get("why", "").strip():
            raise ValueError(f"{case_id}: a judgement needs a reason")
        stray = [t for t in entry.get("tools_ok") or [] if t not in _WIDENABLE]
        if stray:
            raise ValueError(
                f"{case_id}: {stray} writes money — a judgement may only widen "
                f"read-only tools ({sorted(_WIDENABLE)})")


def apply_to(case_id: str, expect: dict) -> dict:
    """Return `expect` with this case's alternatives — or with nothing to grade."""
    if case_id in UNGRADEABLE:
        # Drop the tool expectation *and* its args: there is nothing here anyone
        # should be graded against. `grade_tool_selection` then reports None.
        stripped = {k: v for k, v in expect.items() if k not in ("tools", "args")}
        stripped["ungradeable_why"] = UNGRADEABLE[case_id]
        return stripped
    entry = JUDGEMENTS.get(case_id)
    if not entry:
        return expect
    widened = dict(expect)
    widened["tools_ok"] = list(entry["tools_ok"])
    return widened


check()
