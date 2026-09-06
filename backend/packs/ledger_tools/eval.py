"""What the shared ledger tools contribute to eval (Phase 6 review F2d): which tool
results the room sees as a server-rendered body instead of the model's prose, and
the labels the prose grader uses for "not graded: the room saw …". A business's eval
module composes these with its own kinds.
"""
from __future__ import annotations

from kernos.eval import _ok_results
from ledger_core import clock, ledger
from ledger_core.periods import resolve_period
from packs.ledger_tools import render

SHARED_CARD_LABELS = {
    "payment_draft": "a payment draft card",
    "settlement": "a server-rendered settlement body",
    "settle_blocked": "a server-rendered blocked-settle body",
    "statement": "a server-rendered statement body",
    "summary": "a server-rendered summary body",
    "random_pick": "a server-rendered random-pick body",
}




def shared_body_kind(record: dict) -> str | None:
    """Which shared body the room would see for this turn, in the render decision's own
    precedence: a `propose_payment` draft, then settle, statement, summary, random pick.
    A successful `settle_period` result does **not** carry `type: "settlement"` —
    `render_bot_attachments` stamps that on, so it is matched by tool here."""
    if any(r.get("type") == "payment_draft" for r in _ok_results(record, "propose_payment")):
        return "payment_draft"
    settle = _ok_results(record, "settle_period")
    if settle:
        return "settle_blocked" if settle[-1].get("type") == "settle_blocked" else "settlement"
    for tool_name, kind in (("member_statement", "statement"),
                            ("get_period_summary", "summary"),
                            ("pick_random", "random_pick")):
        results = _ok_results(record, tool_name)
        if results and results[-1].get("type") == kind:
            return kind
    return None


# --------------------------------------------------------------------------- #
# the shared ledger comparisons (settlement, balances) — any ledger business's graders
# --------------------------------------------------------------------------- #

def balances_by_member(db, room_id: int) -> dict[int, int]:
    """`{member_id: balance}` over the open (`since_last`) period.

    Was `tests/test_scenario_week.py::_balances`. Reads the clock, so call it
    while the case's day is still frozen.
    """
    with db.session() as s:
        last = ledger.last_settlement(s, room_id)
        period = resolve_period("since_last", today=clock.today(),
                                last_settlement_to=last.period_to if last else None)
        return {mid: v["balance"] for mid, v in
                ledger.period_balances(s, room_id, period["from"], period["to"]).items()}


def compare_settlement(result: dict, expect: dict, ids: dict) -> list[str]:
    """Compare a `settle_period` result against a step's expectation.

    Returns a list of problems; empty means it matches. Extracted verbatim in
    behavior from `tests/test_scenario_week.py:88-108` — the blocked branch, the
    **ordered** transfer comparison, the rendered-body check, and `qr_payees`.

    Transfer order is load-bearing, not cosmetic: the list is per-payer
    attribution (each debtor repays whoever fronted the meal they ate), so a
    reordered list is a different settlement.
    """
    problems: list[str] = []

    if not result or result.get("ok") is False:
        return [f"settle_period failed: {(result or {}).get('error')}"]

    if expect.get("blocked_pending") is not None:
        if result.get("type") != "settle_blocked":
            return [f"expected settle_blocked, got type={result.get('type')!r}"]
        if len(result.get("pending") or []) != expect["blocked_pending"]:
            problems.append(f"blocked_pending: expected {expect['blocked_pending']}, "
                            f"got {len(result.get('pending') or [])}")
        return problems

    if result.get("type") == "settle_blocked":
        return [f"settlement blocked by {len(result.get('pending') or [])} pending draft(s)"]

    transfers = result.get("transfers") or []

    if expect.get("empty") and transfers:
        problems.append(f"expected an empty settlement, got {len(transfers)} transfer(s)")

    if "transfers" in expect:
        got = [{"from": t["from_id"], "to": t["to_id"], "amount": t["amount"]}
               for t in transfers]
        want = [{"from": ids[t["from"]], "to": ids[t["to"]], "amount": t["amount"]}
                for t in expect["transfers"]]
        if got != want:
            problems.append(f"transfers: expected {want}, got {got}")
        else:
            # Every amount must also reach the card the room actually reads.
            body = render._settlement_body({"type": "settlement", **result})
            for t in expect["transfers"]:
                if f'{t["amount"]:,}' not in body:
                    problems.append(f"amount {t['amount']:,} missing from the rendered body")

    for payee_key in expect.get("qr_payees") or []:
        payee_id = ids[payee_key]
        rows = [t for t in transfers if t["to_id"] == payee_id]
        if not rows:
            problems.append(f"qr: no transfer to {payee_key}")
        elif not all(t.get("qr_url") for t in rows):
            problems.append(f"qr: a transfer to {payee_key} has no qr_url")

    return problems


