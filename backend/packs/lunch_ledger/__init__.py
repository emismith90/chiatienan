"""``lunch_ledger``: the shared-meal ledger as a portable pack (plan Task 3.3).

Tools (`tools.py`), the outcome decision and the deterministic reply bodies
(`render.py`), the two draft kinds it can commit, and the world-building fixtures
the eval bench replays (`fixtures.py`). Everything the pack needs from a host is
injected: the QR builder and the place resolver here at registration, the card
store, clock and draw on the per-turn context (see ``tools.py``).
"""
from __future__ import annotations

from typing import Any, Callable

from kernos.packs import BasePack, DraftKind, PackTool
from ledger_core import drafts as core
from packs.lunch_ledger import fixtures, render, tools

MONEY_TOOLS = frozenset({
    "find_members", "propose_meal", "void_meal", "cancel_draft", "pick_random",
    "resolve_period", "resolve_date", "member_statement", "get_period_summary",
    "settle_period", "propose_payment",
})


def _commit_payment(session, space_id, att: dict, *, logged_by) -> None:
    core.record_payment_transfers(session, space_id, core.require_transfers(att), logged_by=logged_by)


class LunchLedgerPack(BasePack):
    id, version, handles_money = "lunch_ledger", "1", True
    cancel_tools = frozenset({"cancel_draft"})

    def __init__(self, *, qr: Callable[[Any, int, str], str],
                 place_resolver: Callable[[Any, Any, str], tuple[dict | None, bool]] | None = None) -> None:
        self._qr = qr
        self._place_resolver = place_resolver

    def tools(self, ctx) -> dict[str, PackTool]:
        return tools.build(ctx, qr=self._qr, place_resolver=self._place_resolver)

    def draft_kinds(self) -> dict[str, DraftKind]:
        return {
            "expense_draft": DraftKind("expense_draft", core.record_meal_payload, editable=frozenset(core.EDITABLE),
                                       stamps=frozenset({"raw_input", "logged_by", "turn_id"})),
            "payment_draft": DraftKind("payment_draft", _commit_payment, stamps=frozenset({"turn_id"})),
        }

    def render(self, result):
        return render.decide(result)

    def fixtures(self):
        return dict(fixtures.FIXTURES)
