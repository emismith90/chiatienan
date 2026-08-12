"""How a replayed turn is judged.

Four graders, and the discipline that keeps them honest:

* **`passed` is tri-state.** `True`, `False`, or `None` for *not graded*. A case
  with no expectation, or a prose case with no judge, must never count as a pass
  — a corpus of vacuous passes reads exactly like equivalence, which is the one
  failure this harness exists to prevent (design §11).
* **Only the money is compared.** Prose, dish names, and notes are the model's
  business; the tool it picked and the amounts it passed are not.
* **The judge is injected, never constructed here**, so these stay offline and
  the caller pins the model.

Member references in a case's `expect` are **corpus keys** (`"a1"`, `"m3"`),
because database ids are assigned per run. Two graders resolve them differently,
and each touches only its own part of `expect`, so one `Case` can carry both:

* `grade_ledger_state` takes the key→id map and resolves `balances` / `shares` /
  `transfers` / `qr_payees` itself. That is what lets `compare_settlement` be
  the *same* code `tests/test_scenario_week.py` imports back.
* `grade_tool_selection` takes no map, so the runner rewrites `expect["args"]`
  to ids first, via `bench.corpus.resolve_args`.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Arguments whose value is money, or decides who owes it. Everything else the
#: model sends is free-form and deliberately not compared.
#:
#: `guests` is here although the plan's list omits it: a guest pays cash, so
#: dropping one divides the bill by too few heads and overcharges every member
#: (golden `G6` is a 400k bill with a 300k tracked total for exactly this
#: reason). Only the guest **count** is graded — the names are the model reading
#: prose, but the count is arithmetic.
MONEY_ARGS = ("total", "payer", "participants", "from", "to", "amount", "items", "guests")

#: Money args whose order carries no meaning.
_UNORDERED = ("participants",)


@dataclass
class Verdict:
    """`passed=None` means *not graded* — never *passed*."""

    passed: bool | None
    reason: str


def _last_call(record: dict, name: str) -> dict | None:
    """The last invocation of `name` in this turn, or None.

    Last rather than first: a model that corrects itself is judged on what the
    user ends up seeing, which is what `TurnResult.last_result()` returns.
    """
    for call in reversed(record.get("tools") or []):
        if call.get("name") == name:
            return call
    return None


def _item_key(entry) -> tuple:
    """An `items` entry reduced to its money: who ate it and what it cost.

    `label` is the model's prose and is not compared.
    """
    if isinstance(entry, dict):
        return (entry.get("member"), entry.get("amount"))
    return (entry,)


def _args_differ(key: str, want, got) -> str | None:
    """Return a human reason when `got` fails to match `want`, else None."""
    if key in _UNORDERED and isinstance(want, list) and isinstance(got, list):
        if set(map(_hashable, want)) != set(map(_hashable, got)):
            return f"{key}: expected {sorted(map(str, want))}, got {sorted(map(str, got))}"
        return None
    if key == "guests" and isinstance(want, list) and isinstance(got, list):
        # The count is the arithmetic; the names are the model reading prose.
        if len(want) != len(got):
            return f"guests: expected {len(want)}, got {len(got)} ({got})"
        return None
    if key == "items" and isinstance(want, list) and isinstance(got, list):
        if sorted(map(_item_key, want)) != sorted(map(_item_key, got)):
            return f"items: expected {[_item_key(i) for i in want]}, got {[_item_key(i) for i in got]}"
        return None
    if want != got:
        return f"{key}: expected {want!r}, got {got!r}"
    return None


def _hashable(value):
    return tuple(sorted(value.items())) if isinstance(value, dict) else value


def grade_tool_selection(case, record: dict) -> Verdict:
    """Did the model reach for the right tool, with the right money in it?

    Superset-tolerant on the tool list — the scaffolding calls a model makes on
    its way to the answer (`find_members`, `get_period_summary`) are its own
    business, and `tests/test_scenario_week_llm.py` has always tolerated them.
    Strict on `MONEY_ARGS`, because those are the numbers people pay each other.
    """
    expected = list((case.expect or {}).get("tools") or [])
    if not expected:
        return Verdict(None, "no tool expectation for this case")

    if record.get("error"):
        return Verdict(False, f"turn errored: {record['error']}")

    called = [c.get("name") for c in record.get("tools") or []]
    missing = [name for name in expected if name not in called]
    if missing:
        return Verdict(False, f"expected {missing} to be called, got {called or 'no tools'}")

    problems = []
    for tool_name, want_args in ((case.expect or {}).get("args") or {}).items():
        call = _last_call(record, tool_name)
        if call is None:
            problems.append(f"{tool_name} was never called")
            continue
        got_args = call.get("args") or {}
        for key, want in want_args.items():
            if key not in MONEY_ARGS:
                continue
            if key not in got_args:
                # An omitted money arg is a failure, not a comparison to skip.
                problems.append(f"{tool_name}.{key}: expected {want!r}, absent")
                continue
            problem = _args_differ(key, want, got_args[key])
            if problem:
                problems.append(f"{tool_name}.{problem}")

    if problems:
        return Verdict(False, "; ".join(problems))
    return Verdict(True, f"called {expected} with matching money args")


# --------------------------------------------------------------------------- #
# ledger_state
# --------------------------------------------------------------------------- #
#
# Extracted from `tests/test_scenario_week.py`, which imports these back, so
# there is exactly one implementation of the money comparison. If the extraction
# drifted, that test fails — which is the point of it.
#
# **Member references here are corpus keys, resolved against `ids`.** That is the
# opposite convention from `grade_tool_selection`, whose `expect["args"]` the
# runner resolves to database ids before grading (its signature takes no `ids`).
# The two conventions touch disjoint parts of `expect`, so one `Case` can carry
# both; `bench.corpus.resolve_args` is the only thing that rewrites `args`.

def balances_by_member(db, room_id: int) -> dict[int, int]:
    """`{member_id: balance}` over the open (`since_last`) period.

    Was `tests/test_scenario_week.py::_balances`. Reads the clock, so call it
    while the case's day is still frozen.
    """
    from app import ledger
    from app.clock import today_ict
    from app.periods import resolve_period
    with db.session() as s:
        last = ledger.last_settlement(s, room_id)
        period = resolve_period("since_last", today=today_ict(),
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
    from app import chat
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
            body = chat._settlement_body({"type": "settlement", **result})
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


def grade_ledger_state(case, record: dict, db, ids: dict) -> Verdict:
    """Did the turn put the room's money where the golden dataset says?

    Three shapes, chosen by what the case expects:

    * a **settlement** (`transfers` / `qr_payees` / `blocked_pending` / `empty`)
      is read-only, so its result is compared directly;
    * a **draft** (`balances` / `shares` / `tracked`) is projected onto the world
      `bench.world` built, because the turn itself writes nothing;
    * neither → `None`, not graded.
    """
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
            problems.extend(compare_settlement(result, expect, ids))

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
                problems.extend(_compare_draft(expect, result, delta, db, record, ids))

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
