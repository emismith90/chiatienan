"""How a replayed turn is judged — now a shim (plan Task 4.2).

The graders are plugins: the business-neutral ``ToolSelection`` and ``Prose`` live in
``kernos.eval``, the lunch knowledge they need (money args, the ``propose_meal`` share
equivalence, which results the room sees as a card, the rubric) and the
``ledger_state`` grader live in ``packs.lunch_ledger.eval``. The names below keep
today's signatures for ``bench.run``, ``bench.regrade``, ``bench.report`` and the tests,
and are the oracle that the relocation changed nothing.

Four graders, and the discipline that keeps them honest: ``passed`` is tri-state
(``None`` is *not graded*, never a pass); only the money is compared; the judge is
injected, never constructed here; and member references in a case's ``expect`` are
corpus keys — ``grade_ledger_state`` resolves them against ``ids``, while
``grade_tool_selection`` expects ``bench.corpus.resolve_args`` to have rewritten
``expect["args"]`` to ids first.
"""
from __future__ import annotations

from types import SimpleNamespace

from kernos.eval import Verdict, summarize_cost_latency  # noqa: F401
from packs.lunch_ledger.eval import (  # noqa: F401
    CARD_LABELS as _CARD_LABELS,
    MONEY_ARGS,
    PROSE_RUBRIC,
    LedgerState,
    balances_by_member,
    compare_settlement,
    ledger_state as _ledger_state,
    posted_body_kind,
    prose as _prose,
    share_map as _share_map,
    tool_selection as _tool_selection,
)

_TOOL_SELECTION = _tool_selection({})
_LEDGER_STATE = _ledger_state({})


def grade_tool_selection(case, record: dict) -> Verdict:
    """Did the model reach for the right tool, with the right money in it?"""
    return _TOOL_SELECTION.grade(case, record, None)


def grade_ledger_state(case, record: dict, db, ids: dict) -> Verdict:
    """Did the turn put the room's money where the golden dataset says?"""
    return _LEDGER_STATE.grade(case, record, SimpleNamespace(db=db, ids=ids))


def grade_prose(case, record: dict, judge=None) -> Verdict:
    """Was the reply the room actually saw a good reply?"""
    return _prose({}, judge=judge).grade(case, record, None)
