"""``ledger_tools``: the tools two ledger businesses share (Phase 6, Task 6.1).

Who is who, periods, statements, the group summary, the settlement with QR codes, the
random draw, cash payments proposed as a `payment_draft` card, cancelling a pending
card — everything ``ledger_core`` gives a business that is not the business itself.
Injected at registration: the QR builder, the bank-memo fallback, and how another
pack's pending card is described (``draft_kinds`` — the host's registry, so a blocked
settle can list a meal draft or a game draft without this pack knowing either).
"""
from __future__ import annotations

from typing import Any, Callable

from kernos.packs import BasePack, DraftKind, PackTool
from ledger_core import drafts as core
from ledger_core import roster
from packs.ledger_tools import fixtures, render, tools

LEDGER_TOOLS = frozenset({
    "find_members", "cancel_draft", "pick_random", "resolve_period", "resolve_date",
    "member_statement", "get_period_summary", "settle_period", "propose_payment",
})


def _names(session, space_id) -> dict[int, str]:
    return {m.id: m.display_name for m in roster.list_members(session, space_id, include_inactive=True)}


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


def payment_summary(session, space_id, att: dict) -> dict:
    """A pending payment draft, for the blocked-settle listing."""
    tf = att.get("transfers") or []
    names = _names(session, space_id)
    return {"kind": "payment",
            "transfers": [{"from_name": names.get(t.get("from_member_id"), "?"),
                           "to_name": names.get(t.get("to_member_id"), "?"),
                           "amount": t.get("amount", 0)} for t in tf]}


class LedgerToolsPack(BasePack):
    id, version, handles_money = "ledger_tools", "1", True
    cancel_tools = frozenset({"cancel_draft"})
    #: The calls that are a turn's money answer (eval capture records them); the lookups
    #: `find_members`, `resolve_period`, `resolve_date` are scaffolding.
    money_tools = frozenset({"propose_payment", "settle_period", "member_statement", "get_period_summary",
                             "pick_random", "cancel_draft"})

    def __init__(self, *, qr: Callable[[Any, int, str], str], fallback_note: Callable[[Any], str],
                 draft_kinds: Callable[[], dict[str, DraftKind]]) -> None:
        self._qr, self._fallback_note, self._draft_kinds = qr, fallback_note, draft_kinds

    def describe_pending(self, session, space_id, kind: str, payload: dict) -> dict:
        dk = self._draft_kinds().get(kind)
        if dk is None:
            return {"kind": kind, "label": kind}
        return dk.describe_pending(session, space_id, payload)

    def tools(self, ctx) -> dict[str, PackTool]:
        return tools.build(ctx, qr=self._qr, fallback_note=self._fallback_note, describe_pending=self.describe_pending)

    def draft_kinds(self) -> dict[str, DraftKind]:
        return {"payment_draft": DraftKind("payment_draft", _commit_payment, stamps=frozenset({"turn_id"}),
                                           card=payment_card, signature=core.draft_signature, summary=payment_summary)}

    def render(self, result):
        return render.decide(result)

    def fixtures(self):
        return dict(fixtures.FIXTURES)
