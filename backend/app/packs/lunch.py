"""``lunch_ledger`` as a pack wrapping today's modules (plan Task 3.1, PR 3a)."""
from __future__ import annotations

from app import chat, drafts
from app.packs import lunch_fixtures
from kernos.kernel import Body, Draft
from kernos.packs import BasePack, DraftKind, PackTool

MONEY_TOOLS = frozenset({
    "find_members", "propose_meal", "void_meal", "cancel_draft", "pick_random",
    "resolve_period", "resolve_date", "member_statement", "get_period_summary",
    "settle_period", "add_member", "update_member", "delete_member", "propose_payment",
})

#: Fields the render stage copies from a `propose_meal` result into the card.
_DRAFT_FIELDS = ("payer_member_id", "member_participants", "guests", "bill_total",
                 "adjustments", "items", "discount_split", "dish", "initiator", "note",
                 "per_head_preview", "occurred_on")


def decide(result) -> Draft | Body | None:
    """The lunch outcome from the structured tool results — never from prose.

    A meal turn never writes directly: the LLM only proposes, and the turn ends with
    an editable draft card for the human to confirm (design D3). Money turns get a
    body built server-side from the tool-result dict, so the visible text can never
    disagree with the QR/attachment numbers. Anything else is not this pack's call.
    """
    proposal = result.last_result("propose_meal")
    # Collapse multiple proposals for the SAME (from,to) pair to the LAST one (a
    # model self-correction "100k… actually 150k"), preserving order.
    by_pair: dict[tuple[int, int], dict] = {}
    for p in result.all_results("propose_payment"):
        if p.get("type") == "payment_draft":
            by_pair[(p["from_member_id"], p["to_member_id"])] = {
                "from_member_id": p["from_member_id"], "to_member_id": p["to_member_id"],
                "amount": p["amount"], "note": p.get("note")}
    if proposal:
        return Draft("expense_draft", {k: proposal.get(k) for k in _DRAFT_FIELDS})
    if by_pair:
        return Draft("payment_draft", {"transfers": list(by_pair.values())})

    attachments = chat.render_bot_attachments(result)
    kind = attachments.get("type") if attachments else None
    body = {
        "settlement": chat._settlement_body, "settle_blocked": chat._settle_blocked_body,
        "statement": chat._statement_body, "summary": chat._summary_body,
        "random_pick": chat._random_pick_body,
    }.get(kind)
    if body is None:
        return None
    return Body(body(attachments), attachments, claimed_by_pack=True)


class LunchLedgerPack(BasePack):
    id, version, handles_money = "lunch_ledger", "1", True
    cancel_tools = frozenset({"cancel_draft"})

    def tools(self, ctx) -> dict[str, PackTool]:
        from app.tools import _legacy_build_tools
        return {name: PackTool(name, t.description, t.input_schema, t.execute)
                for name, t in _legacy_build_tools(ctx).items() if name in MONEY_TOOLS}

    def draft_kinds(self) -> dict[str, DraftKind]:
        return {
            "expense_draft": DraftKind("expense_draft", drafts.commit_draft, editable=frozenset(drafts._EDITABLE),
                                       stamps=frozenset({"raw_input", "logged_by", "turn_id"})),
            "payment_draft": DraftKind("payment_draft", drafts.commit_payment_draft, stamps=frozenset({"turn_id"})),
        }

    def render(self, result):
        return decide(result)

    def fixtures(self):
        return dict(lunch_fixtures.FIXTURES)
