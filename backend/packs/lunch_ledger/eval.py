"""The lunch ledger's eval knowledge (plan Task 4.2, review F1): what its money
arguments are, when two ``propose_meal`` argument sets mean the same shares, which
tool results the room sees as a card instead of prose, the prose rubric, and the
``ledger_state`` grader that reads the world the fixtures built. Bodies are
chiatienan's ``bench.graders`` before Task 4.2, unchanged; ``bench.graders`` now
re-exports them.
"""
from __future__ import annotations

from typing import Callable

from kernos.eval import Prose, ToolSelection, Verdict, _ok_results
from ledger_core import clock, ledger
from ledger_core.money import MoneyError, prorate_items, split_shares
from ledger_core.moneyguard import unbacked_amounts
from ledger_core.periods import resolve_period
from packs.ledger_tools.eval import SHARED_CARD_LABELS, shared_body_kind
from packs.ledger_tools import render

#: Arguments whose value is money, or decides who owes it. Everything else the
#: model sends is free-form and deliberately not compared. `guests` and
#: `adjustments` are here although the plan's list omits both: a guest pays cash, so
#: dropping one divides the bill by too few heads; an adjustment is what one person
#: ordered extra of, so dropping one splits that cost across everybody.
MONEY_ARGS = ("total", "payer", "participants", "from", "to", "amount", "items",
              "guests", "adjustments")

TOOL_SELECTION_CONFIG = {
    "compared_args": list(MONEY_ARGS),
    "unordered": ["participants"],
    "member_amount_lists": ["items", "adjustments"],
    "count_only": ["guests"],
    "sender_defaulted": ["payer", "from"],
    "equivalence_keys": ["adjustments", "items"],
}


def share_map(args: dict) -> dict | None:
    """What each participant ends up owing, given one `propose_meal` argument set.

    **The invariant is the money, not the encoding.** `p120` is the case: the
    expectation (read off production's card) carries
    `adjustments=[54500, 54500, 79200, 54500, 54500, 27000]` — the *final shares*,
    which happen to sum to the total, so `split_shares` computes a base of 0 and
    reproduces them. Our turn passed the bill's list prices as `items` with
    `discount_split="equal"`, and the tool prorated them to the same six shares
    around a base of 54,033 — so its `adjustments` read `[467, …, -27033, 25167]`.
    Identical money, unrecognizably different arguments.

    Returns None when the split cannot be reproduced here (guests change the
    per-head, and a malformed argument set is not this function's business).
    """
    total = args.get("total")
    participants = [int(p) for p in args.get("participants") or [] if str(p).lstrip("-").isdigit()]
    if not isinstance(total, int) or not participants or args.get("guests"):
        return None
    try:
        items = args.get("items")
        if items:
            prices = {int(i["member"]): int(i["amount"]) for i in items
                      if isinstance(i, dict) and "member" in i and "amount" in i}
            if sorted(prices) != sorted(participants):
                return None
            return prorate_items(total, prices,
                                 discount_split=args.get("discount_split") or "proportional")
        adjustments = {int(a["member"]): int(a["amount"]) for a in args.get("adjustments") or []
                       if isinstance(a, dict) and "member" in a and "amount" in a}
        return split_shares(total, participants, adjustments)
    except (MoneyError, KeyError, TypeError, ValueError):
        return None


PROSE_RUBRIC = """\
You are grading one reply from a Vietnamese lunch-splitting bot.

Judge the reply on its own merits against the four rules below. Do not compare it
to what some other assistant might have said, and do not reward length: these
replies are meant to be terse, and a short correct answer is a good answer.

Pass the reply only if all of these hold:
1. It is written in Vietnamese, in the room's casual register. (A Vietnamese reply
   to an English question is fine — the room mixes both.)
2. It answers what the user actually asked.
3. It does not narrate its own machinery — no "mình đọc skill…", no listing the
   tools it called, no describing what it is about to do.
4. It states no amount that is obviously invented. Stating a number the bot
   computed or recorded is correct and expected — telling the user which amount was
   logged is the point of the reply, not a fault.

Reply with JSON only: {"ok": true|false, "reason": "<one short sentence>"}.
"""

# Rule 4 used to read "does not restate amounts the card already shows", and it
# had to go: the judge is shown the user's message and the reply, never the card,
# so it was being asked a question the prompt gives it no way to answer — and it
# answered anyway, failing correct replies for "restating an amount already in the
# user's message" when the user's message contained no amount at all. Amount
# provenance is `moneyguard`'s job, deterministically, in stage 1.

CARD_LABELS = {"expense_draft": "an expense draft card", **SHARED_CARD_LABELS}


def posted_body_kind(record: dict) -> str | None:
    """Which card the room would see for this turn, or None for plain prose — in the
    render decision's own precedence: the meal proposal (this pack, first in profile
    order), then the shared bodies (`ledger_tools`, next)."""
    if _ok_results(record, "propose_meal"):
        return "expense_draft"
    return shared_body_kind(record)


# --------------------------------------------------------------------------- #
# ledger_state
# --------------------------------------------------------------------------- #
#
# Extracted from `tests/test_scenario_week.py`, which imports these back, so
# there is exactly one implementation of the money comparison. **Member references
# here are corpus keys, resolved against `ids`** — the opposite convention from
# `ToolSelection`, whose `expect["args"]` the runner resolves to database ids first.

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


def _draft_delta(result: dict) -> dict[int, int] | None:
    """What a draft *would* do to the ledger, per member.

    A benchmark turn never confirms anything: `propose_meal` "never writes at
    all" and `propose_payment` returns a `payment_draft`. So the post-turn ledger
    equals the pre-turn ledger, and comparing database balances against a step's
    expectation would fail on every meal and payment case — **identically on both
    engines**, which `--compare` would report as "no change". Projecting the
    draft's own numbers instead grades the arithmetic the model actually produced.

    The payer is credited the **tracked** total (Σ shares), not the bill total: a
    cash-paying guest's share is never tracked, which is why golden `G6` is a
    400k bill with a 300k tracked total.
    """
    kind = result.get("type")
    if kind == "expense_draft":
        shares = {int(s["member"]): int(s["amount"])
                  for s in result.get("shares_preview") or []}
        delta = {mid: -amount for mid, amount in shares.items()}
        payer = int(result["payer_member_id"])
        delta[payer] = delta.get(payer, 0) + sum(shares.values())
        return delta
    if kind == "payment_draft":
        frm, to = int(result["from_member_id"]), int(result["to_member_id"])
        amount = int(result["amount"])
        return {frm: amount, to: -amount}
    return None


def _last_result(record: dict, *names: str) -> tuple[str | None, dict | None]:
    """The last result among calls to any of `names`."""
    for call in reversed(record.get("tools") or []):
        if call.get("name") in names:
            return call["name"], call.get("result")
    return None, None


class LedgerState:
    """Did the turn put the room's money where the golden dataset says?

    Three shapes, chosen by what the case expects: a **settlement** (`transfers` /
    `qr_payees` / `blocked_pending` / `empty`) is read-only, so its result is compared
    directly; a **draft** (`balances` / `shares` / `tracked`) is projected onto the
    world the fixtures built, because the turn itself writes nothing; neither →
    `None`, not graded. ``world`` carries ``db`` and the key→id map ``ids``.
    """

    blocking = True

    def grade(self, case, record: dict, world) -> Verdict:
        expect = case.expect or {}
        settle_keys = {"transfers", "qr_payees", "blocked_pending", "empty"} & expect.keys()
        draft_keys = {"balances", "shares", "tracked"} & expect.keys()
        if not settle_keys and not draft_keys:
            return Verdict(None, "no ledger expectation for this case")

        if record.get("error"):
            return Verdict(False, f"turn errored: {record['error']}")

        problems: list[str] = []

        if settle_keys:
            _, result = _last_result(record, "settle_period")
            if result is None:
                problems.append("settle_period was never called")
            else:
                problems.extend(compare_settlement(result, expect, world.ids))

        if draft_keys:
            name, result = _last_result(record, "propose_meal", "propose_payment")
            if result is None:
                problems.append("neither propose_meal nor propose_payment was called")
            elif result.get("ok") is False:
                problems.append(f"{name} failed: {result.get('error')}")
            else:
                delta = _draft_delta(result)
                if delta is None:
                    problems.append(f"{name} returned type={result.get('type')!r}, not a draft")
                else:
                    problems.extend(_compare_draft(expect, result, delta, world.db, record, world.ids))

        if problems:
            return Verdict(False, "; ".join(problems))
        graded = sorted(settle_keys | draft_keys)
        return Verdict(True, f"ledger matches on {graded}")


def _compare_draft(expect: dict, result: dict, delta: dict[int, int],
                   db, record: dict, ids: dict) -> list[str]:
    problems: list[str] = []

    if "shares" in expect:
        want = {ids[k]: v for k, v in expect["shares"].items()}
        got = {int(s["member"]): int(s["amount"])
               for s in result.get("shares_preview") or []}
        if got != want:
            problems.append(f"shares: expected {want}, got {got}")

    if "tracked" in expect:
        got_tracked = sum(int(s["amount"]) for s in result.get("shares_preview") or [])
        if got_tracked != expect["tracked"]:
            problems.append(f"tracked: expected {expect['tracked']:,}, got {got_tracked:,}")

    if "balances" in expect:
        before = balances_by_member(db, record["room_id"])
        projected = dict(before)
        for mid, change in delta.items():
            projected[mid] = projected.get(mid, 0) + change
        mismatches = {k: (v, projected.get(ids[k], 0))
                      for k, v in expect["balances"].items()
                      if projected.get(ids[k], 0) != v}
        if mismatches:
            problems.append("balances: " + ", ".join(
                f"{k} expected {want:,} got {got:,}" for k, (want, got) in mismatches.items()))

    return problems



def tool_selection(config: dict, *, judge=None) -> ToolSelection:
    return ToolSelection({**TOOL_SELECTION_CONFIG, **config}, equivalence={"propose_meal": share_map})


def prose(config: dict, *, judge=None) -> Prose:
    return Prose(unbacked_amounts, posted_body_kind, judge=judge,
                 rubric=config.get("rubric") or PROSE_RUBRIC, card_labels=CARD_LABELS)


def ledger_state(config: dict, *, judge=None) -> LedgerState:
    return LedgerState()


#: The pack's graders, by plugin id, for `GraderRegistry.register_all`.
GRADERS: dict[str, Callable] = {
    "lunch_ledger.eval.tool_selection": tool_selection,
    "lunch_ledger.eval.ledger_state": ledger_state,
    "lunch_ledger.eval.prose": prose,
}
