"""``lunch_ledger``: what the shared-meal business *adds* to the ledger (plan Tasks
3.3, 3.4, 6.1): `propose_meal`/`void_meal`, the `expense_draft` kind with its
committed-meal card, the meals' debt edges and timeline events, its fixtures and its
eval graders. Everything a ledger business shares — statements, settlement, payments,
the random draw — is ``packs.ledger_tools``, enabled next to this pack. Injected: the
place resolver at registration; the card store, clock and draw on the per-turn context.
"""
from __future__ import annotations

from typing import Any, Callable

from kernos.packs import BasePack, DraftKind, PackTool
from ledger_core import drafts as core
from ledger_core import ledger, roster
from packs.lunch_ledger import fixtures, render, tools

LUNCH_TOOLS = frozenset({"propose_meal", "void_meal"})


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


def meal_summary(session, space_id, att: dict) -> dict:
    """A pending meal draft, for another pack's blocked-settle listing."""
    names = _names(session, space_id)
    return {"kind": "meal", "payer_name": names.get(att.get("payer_member_id"), "?"),
            "bill_total": att.get("bill_total", 0),
            "participant_count": len(att.get("member_participants") or [])}


class LunchLedgerPack(BasePack):
    id, version, handles_money = "lunch_ledger", "1", True
    money_tools = LUNCH_TOOLS

    def __init__(self, *, place_resolver: Callable[[Any, Any, str], tuple[dict | None, bool]] | None = None) -> None:
        self._place_resolver = place_resolver

    def tools(self, ctx) -> dict[str, PackTool]:
        return tools.build(ctx, place_resolver=self._place_resolver)

    def draft_kinds(self) -> dict[str, DraftKind]:
        return {
            "expense_draft": DraftKind(
                "expense_draft", _commit_meal, editable=frozenset(core.EDITABLE),
                stamps=frozenset({"raw_input", "logged_by", "turn_id"}),
                card=meal_card, prepare=core.sync_items, signature=core.draft_signature,
                summary=meal_summary),
        }

    def render(self, result):
        return render.decide(result)

    def contributions(self, session, space_id) -> list:
        """The meals' gross debt edges, the whole ledger, unwindowed (review F4)."""
        return ledger.meal_edges(session, space_id)

    def timeline(self, session, space_id, from_date, to_date) -> list[dict]:
        """The meals in the window, for the period summary (Phase 6 review F2)."""
        return ledger.meal_timeline(session, space_id, from_date, to_date)

    def fixtures(self):
        return dict(fixtures.FIXTURES)

    def graders(self):
        from packs.lunch_ledger import eval as lunch_eval
        return dict(lunch_eval.GRADERS)
