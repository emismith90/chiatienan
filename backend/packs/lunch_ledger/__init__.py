"""``lunch_ledger``: the shared-meal ledger as a portable pack (plan Tasks 3.3, 3.4).

Tools (`tools.py`), the outcome decision and the deterministic reply bodies
(`render.py`), the two draft kinds it can commit — with the confirmation cards the
room sees afterwards — the debt edges it contributes to balances, and the
world-building fixtures the eval bench replays (`fixtures.py`). Everything the
pack needs from a host is injected: the QR builder and the place resolver here at
registration, the card store, clock and draw on the per-turn context (see
``tools.py``).
"""
from __future__ import annotations

from typing import Any, Callable

from kernos.packs import BasePack, DraftKind, PackTool
from ledger_core import drafts as core
from ledger_core import ledger, roster
from packs.lunch_ledger import fixtures, render, tools

MONEY_TOOLS = frozenset({
    "find_members", "propose_meal", "void_meal", "cancel_draft", "pick_random",
    "resolve_period", "resolve_date", "member_statement", "get_period_summary",
    "settle_period", "propose_payment",
})


def _names(session, space_id) -> dict[int, str]:
    """Display names for EVERY member, active or not: a card shown to humans must
    still name a since-deactivated member instead of "?" — display only, the share
    math is unaffected."""
    return {m.id: m.display_name for m in roster.list_members(session, space_id, include_inactive=True)}


def _commit_meal(session, space_id, att: dict, *, logged_by) -> dict:
    core.require_meal_fields(att)
    return core.record_meal_payload(session, space_id, att, logged_by=logged_by)


def meal_card(session, space_id, att: dict, res: dict) -> tuple[str, dict]:
    """The committed-meal card from a ``record_meal`` result."""
    names = _names(session, space_id)
    meal_att = {
        "type": "meal",
        "meal_id": res["meal_id"],
        "occurred_on": res["occurred_on"],
        "bill_total": res["bill_total"],
        "tracked_total": res["tracked_total"],
        "guests": res["guests"],
        "dish": att.get("dish"),
        "initiator": att.get("initiator"),
        "note": att.get("note"),
        "items": att.get("items") or None,
        "payer": {"id": res["payer_member_id"], "name": names.get(res["payer_member_id"], "?")},
        "shares": [{"id": mid, "name": names.get(mid, "?"), "amount": amt}
                   for mid, amt in res["shares"].items()],
    }
    return render._meal_body(meal_att), meal_att


def _commit_payment(session, space_id, att: dict, *, logged_by) -> dict:
    transfers = core.require_transfers(att)
    core.record_payment_transfers(session, space_id, transfers, logged_by=logged_by)
    return {"transfers": transfers}


def payment_card(session, space_id, att: dict, res: dict) -> tuple[str, dict]:
    names = _names(session, space_id)
    pay_att = {
        "type": "payment",
        "transfers": [
            {"from": {"id": t["from_member_id"], "name": names.get(t["from_member_id"], "?")},
             "to": {"id": t["to_member_id"], "name": names.get(t["to_member_id"], "?")},
             "amount": t["amount"]}
            for t in res["transfers"]
        ],
    }
    return render._payment_body(pay_att), pay_att


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
            "expense_draft": DraftKind(
                "expense_draft", _commit_meal, editable=frozenset(core.EDITABLE),
                stamps=frozenset({"raw_input", "logged_by", "turn_id"}),
                card=meal_card, prepare=core.sync_items, signature=core.draft_signature),
            "payment_draft": DraftKind(
                "payment_draft", _commit_payment, stamps=frozenset({"turn_id"}),
                card=payment_card, signature=core.draft_signature),
        }

    def render(self, result):
        return render.decide(result)

    def contributions(self, session, space_id) -> list:
        """The meals' gross debt edges, the whole ledger, unwindowed (review F4)."""
        return ledger.meal_edges(session, space_id)

    def fixtures(self):
        return dict(fixtures.FIXTURES)
