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

Member references in a case's `expect` are corpus keys; `bench.run` resolves
them to database ids before grading, so by the time a grader sees them both
sides are ids.
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
