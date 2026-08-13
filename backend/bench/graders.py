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
#: `guests` and `adjustments` are here although the plan's list omits both, and
#: each omission would have hidden a real money regression:
#:
#: * a guest pays cash, so dropping one divides the bill by too few heads and
#:   overcharges every member (golden `G6` is a 400k bill with a 300k tracked
#:   total for exactly this reason). Only the guest **count** is graded — the
#:   names are the model reading prose, but the count is arithmetic.
#: * an adjustment is what one person ordered extra of, so dropping one splits
#:   that cost across everybody. Golden `G4` is the case: 250k over two people is
#:   100k/150k with the adjustment and 125k/125k without, and a grader blind to
#:   `adjustments` calls the wrong one correct.
MONEY_ARGS = ("total", "payer", "participants", "from", "to", "amount", "items",
              "guests", "adjustments")

#: Money args that are lists of `{member, amount}`, compared as a multiset — the
#: order the model happens to emit them in carries no meaning.
_MEMBER_AMOUNT_LISTS = ("items", "adjustments")

#: Money args whose order carries no meaning.
_UNORDERED = ("participants",)

#: Args the schema says may be omitted when they equal the sender:
#: `propose_meal.payer` — "member id of the payer; blank = the sender"
#: `propose_payment.from` — "member id who paid; blank = the sender"
#: Omitting one is correct behavior, not a missing argument, and the tool fills it
#: in identically. Grading it as a failure marked a *correct* turn wrong — G1's
#: first real Pi run passed `ledger_state` while failing `tool_selection` for
#: exactly this.
_SENDER_DEFAULTED = ("payer", "from")


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


def _share_map(args: dict) -> dict | None:
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
    from app.money import MoneyError, prorate_items, split_shares

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


def _recorded(call: dict, key: str):
    """What the **tool** put in the draft for `key`, if anything.

    The expectation describes the money that reached the ledger, and the tool's
    result *is* that money — so an argument the model left out is still gradeable
    whenever the tool worked it out. `amount` has one special case: a payment draft
    may carry `transfers` instead, and a single transfer is still one amount.
    """
    result = call.get("result")
    if not isinstance(result, dict):
        return None
    if result.get(key) not in (None, [], {}):
        return result[key]
    if key == "amount":
        transfers = result.get("transfers")
        if isinstance(transfers, list) and len(transfers) == 1 \
                and isinstance(transfers[0], dict):
            return transfers[0].get("amount")
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
    if key in _MEMBER_AMOUNT_LISTS and isinstance(want, list) and isinstance(got, list):
        if sorted(map(_item_key, want)) != sorted(map(_item_key, got)):
            return (f"{key}: expected {[_item_key(i) for i in want]}, "
                    f"got {[_item_key(i) for i in got]}")
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
        # `tools_ok` — a read-only tool a human judged to answer the same question.
        # Prod cases expect what production did, and production was the engine being
        # replaced: "@bot how much do I owe" answered with the whole group's
        # settlement is *an* answer, and `member_statement` is a better one. The
        # alternatives live in `corpus/prod_judgements.py` with a reason each, may
        # never name a money-writing tool, and are counted in the report.
        alternatives = [t for t in (case.expect or {}).get("tools_ok") or [] if t in called]
        if alternatives:
            return Verdict(True, f"called {alternatives} — a judged alternative to "
                                 f"{expected} (see corpus/prod_judgements.py)")
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
            if key in ("adjustments", "items") and tool_name == "propose_meal":
                # **Compare the money, not the encoding.** `p120`: the expectation's
                # `adjustments` are production's *final shares* (they sum to the
                # total, so `split_shares` computes a base of 0 and reproduces them),
                # while our turn passed the bill's list prices as `items` with
                # `discount_split="equal"` and the tool prorated them to the same six
                # shares around a base of 54,033. Identical money, unrecognizably
                # different arguments. Only the share map decides.
                want_shares = _share_map({**want_args, key: want})
                got_shares = _share_map(got_args)
                if want_shares is not None and got_shares is not None:
                    if want_shares != got_shares:
                        problems.append(
                            f"{tool_name}: shares expected {want_shares}, got {got_shares}")
                    continue

            if key not in got_args:
                if key in _SENDER_DEFAULTED and want == record.get("sender_member_id"):
                    # The schema permits omitting it when it is the sender, and the
                    # tool resolves it to the same id.
                    continue
                if _args_differ(key, want, _recorded(call, key)) is None:
                    # **The tool worked it out, which is the preferred path.**
                    # `p129` "tôi đã trả tiền A1" (expecting 27,000đ) called
                    # `propose_payment(to=A1)` with no `amount`, exactly as
                    # `record-payment` says to — the tool then reads the debt off the
                    # ledger and the model transcribes nothing (design D3). An absent
                    # argument passes only when the tool's own result matches the
                    # expectation: checked, never assumed.
                    continue
                # Any other omitted money arg is a failure, not a comparison to skip.
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


# --------------------------------------------------------------------------- #
# prose_quality
# --------------------------------------------------------------------------- #

#: What the judge is asked to check. A module constant so the baseline run and
#: the Pi run are graded against identical wording — a rubric that drifted
#: between the two would make the comparison meaningless (design §11.5).
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

#: How the room saw each kind of reply, for the "not graded" reason string.
_CARD_LABELS = {
    "expense_draft": "an expense draft card",
    "payment_draft": "a payment draft card",
    "settlement": "a server-rendered settlement body",
    "settle_blocked": "a server-rendered blocked-settle body",
    "statement": "a server-rendered statement body",
    "summary": "a server-rendered summary body",
    "random_pick": "a server-rendered random-pick body",
}


class _Invocation:
    """Adapter for `app.moneyguard`, which reads `.args`/`.result` via getattr.

    Handing it the runner's plain dicts would leave `backed_amounts` with only
    the user's own text, so every tool-produced amount would read as unbacked —
    a grader that fails almost everything is as useless as one that passes it.
    """

    __slots__ = ("name", "args", "result")

    def __init__(self, call: dict):
        self.name = call.get("name")
        self.args = call.get("args")
        self.result = call.get("result")


def _ok_results(record: dict, name: str) -> list[dict]:
    """Successful result dicts for one tool, in call order.

    Same admission rule as `TurnResult.all_results`: a dict whose `ok` is truthy.
    """
    return [c["result"] for c in record.get("tools") or []
            if c.get("name") == name and isinstance(c.get("result"), dict)
            and c["result"].get("ok")]


def posted_body_kind(record: dict) -> str | None:
    """Which card `chat.py` would post for this turn, or None for plain prose.

    Mirrors the selection at `chat.py:511-558` **in chat's own precedence order**,
    not in tool-call order: a `propose_meal` proposal wins, then a
    `propose_payment` draft, then `render_bot_attachments`
    (`chat.py:304-319`) in its order.

    Note that a successful `settle_period` result does **not** carry
    `type: "settlement"` — `render_bot_attachments` stamps that on. Matching
    result types alone would therefore miss every settlement, leave its discarded
    prose graded, and fail it as an empty reply.
    """
    if _ok_results(record, "propose_meal"):
        return "expense_draft"
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


def grade_prose(case, record: dict, judge=None) -> Verdict:
    """Was the reply the room actually saw a good reply?

    Two stages, cheap first:

    1. `app.moneyguard.unbacked_amounts` — deterministic, offline, and already
       production code (wired as a report-only warning at `chat.py:562`). An
       amount neither the user nor any tool produced is a D3 violation, and it
       short-circuits before any judge spend.
    2. An **injected** LLM judge. Never constructed here, so this stays offline
       and the caller pins the model (`BENCH_JUDGE_MODEL`).

    Turns whose reply `chat.py` builds itself are **not graded**: their
    `final_text` is discarded before it reaches the room, so judging it would
    grade text nobody reads and would flag "unbacked" amounts in a reply that was
    never posted. On the golden corpora that is nearly every case — see the
    plan's Task 4 note.
    """
    from app import moneyguard

    if record.get("error"):
        return Verdict(False, f"turn errored: {record['error']}")

    card = posted_body_kind(record)
    if card:
        return Verdict(None, f"not graded: the room saw {_CARD_LABELS[card]}, "
                             "not the model's prose")

    body = record.get("final_text") or ""
    if not body.strip():
        return Verdict(False, "empty reply")

    stray = moneyguard.unbacked_amounts(
        # The turn's history backs an amount as much as its message does: the room
        # said "tổng 324k" a message ago, the model was handed it, and repeating it
        # is not invented money. `chat.py` passes the history for the same reason.
        body, f"{case.message}\n{case.history or ''}",
        [_Invocation(c) for c in record.get("tools") or []])
    if stray:
        return Verdict(False, f"unbacked amounts in the reply: {stray}")

    if judge is None:
        # Not a pass. A baseline graded with no judge against a Pi run graded
        # with one is not a comparison (design §11.5).
        return Verdict(None, "not graded: no judge configured")

    answer = judge(case, record, PROSE_RUBRIC)
    if not isinstance(answer, dict) or "ok" not in answer:
        return Verdict(None, f"not graded: judge returned {answer!r}")
    reason = str(answer.get("reason") or "")
    return Verdict(bool(answer["ok"]), reason or "judge gave no reason")


# --------------------------------------------------------------------------- #
# cost_latency — reported, never pass/fail
# --------------------------------------------------------------------------- #

def _percentile(sorted_values: list[float], p: float) -> float | None:
    """Nearest-rank percentile: index `ceil(p * n) - 1`.

    No interpolation. With corpora this small an interpolated p95 would invent a
    latency no turn actually took, and the report is read as "how slow does this
    get", not as a distribution fit.
    """
    if not sorted_values:
        return None
    import math
    index = max(0, math.ceil(p * len(sorted_values)) - 1)
    return sorted_values[index]


def summarize_cost_latency(records: list[dict]) -> dict:
    """Latency, tool volume, tokens and cost across a run.

    **Reported, never pass/fail.** A slower engine that is correct is a business
    decision, not a test failure.

    `total_tokens` and `total_cost_usd` are `None` when nothing reported `stats`
    — Cursor exposes no cost, and printing `0` would claim "free" where the truth
    is "unknown", making any Pi figure look like a rise from nothing. When only
    some records carry stats the known ones are summed and `stats_n` says how
    many contributed, so a partial total can never be mistaken for a full one.
    """
    n = len(records)
    if not n:
        return {"n": 0, "error_n": 0, "p50_s": None, "p95_s": None,
                "mean_tool_calls": None, "total_tokens": None,
                "total_cost_usd": None, "stats_n": 0}

    # An errored turn's elapsed time is a latency fact, not a gap in the data.
    elapsed = sorted(float(r.get("elapsed_s") or 0.0) for r in records)
    stats = [r["stats"] for r in records if isinstance(r.get("stats"), dict)]

    return {
        "n": n,
        "error_n": sum(1 for r in records if r.get("error")),
        "p50_s": _percentile(elapsed, 0.50),
        "p95_s": _percentile(elapsed, 0.95),
        "mean_tool_calls": sum(len(r.get("tools") or []) for r in records) / n,
        "total_tokens": sum(int(s.get("tokens") or 0) for s in stats) if stats else None,
        "total_cost_usd": sum(float(s.get("cost") or 0.0) for s in stats) if stats else None,
        "stats_n": len(stats),
    }
