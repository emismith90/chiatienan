"""The lunch business's own bodies and outcome decision (design D3): the meal
proposal becomes an expense draft card; the committed-meal body. Everything shared
(settlement, statement, summary, random pick, payment) is ``packs.ledger_tools.render``.
"""
from __future__ import annotations

from kernos.kernel import Draft


def _meal_body(attachments: dict) -> str:
    """Deterministic summary of a committed meal, straight from the tool-result
    dict — never from LLM prose (design D3, money-safety)."""
    payer = attachments.get("payer") or {}
    shares = attachments.get("shares") or []
    shares_str = ", ".join(f"{s['name']} {s['amount']:,}đ" for s in shares)
    bill = attachments.get("bill_total", attachments.get("tracked_total", attachments.get("total_amount", 0)))
    guests = attachments.get("guests") or []
    guest_str = (f" (incl. {len(guests)} guest{'' if len(guests) == 1 else 's'} paying cash)"
                 if guests else "")
    dish = attachments.get("dish")
    dish_str = f" — {dish}" if dish else ""
    return (
        f"Recorded #{attachments.get('meal_id')}{dish_str}: {payer.get('name', '?')} paid "
        f"{bill:,}đ total{guest_str} • {shares_str}"
    )


_DRAFT_FIELDS = ("payer_member_id", "member_participants", "guests", "bill_total",
                 "adjustments", "items", "discount_split", "dish", "initiator", "note",
                 "per_head_preview", "occurred_on")


def decide(result) -> Draft | None:
    """A meal turn never writes directly: the LLM only proposes, and the turn ends with
    an editable draft card for the human to confirm (design D3). This pack claims the
    meal proposal only; ``ledger_tools`` decides the rest, next in profile order."""
    proposal = result.last_result("propose_meal")
    if proposal:
        return Draft("expense_draft", {k: proposal.get(k) for k in _DRAFT_FIELDS})
    return None
