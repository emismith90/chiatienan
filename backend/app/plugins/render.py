from app import chat
from kernos.kernel import BasePlugin, Body, Draft, Stage, TurnContext


class LunchRender(BasePlugin):
    """Decide the turn's outcome from the structured tool results — never from prose.

    A meal turn never writes directly: the LLM only proposes, and the turn ends with
    an editable draft card for the human to confirm (design D3, money-safety). Money
    turns get a body built server-side from the tool-result dict, so the visible
    text can never disagree with the QR/attachment numbers; only what falls through
    is the model's own prose, and only that is what the reply validators see.
    """

    id, version, stage = "app.render.lunch", "1", Stage.render
    config_schema = {"type": "object", "additionalProperties": False, "properties": {}}

    async def run(self, ctx: TurnContext, config: dict) -> None:
        result = ctx.result
        proposal = result.last_result("propose_meal")
        # Collapse multiple proposals for the SAME (from,to) pair to the LAST
        # one (a model self-correction "100k… actually 150k"), preserving order.
        # Distinct pairs (real multi-payer) are untouched.
        _by_pair: dict[tuple[int, int], dict] = {}
        for p in result.all_results("propose_payment"):
            if p.get("type") == "payment_draft":
                _by_pair[(p["from_member_id"], p["to_member_id"])] = {
                    "from_member_id": p["from_member_id"], "to_member_id": p["to_member_id"],
                    "amount": p["amount"], "note": p.get("note")}
        payment_transfers = list(_by_pair.values())

        if proposal:
            payload = {k: proposal.get(k) for k in (
                "payer_member_id", "member_participants", "guests", "bill_total",
                "adjustments", "items", "discount_split", "dish", "initiator", "note",
                "per_head_preview", "occurred_on")}
            payload["raw_input"] = ctx.text
            payload["logged_by"] = str(ctx.principal.id)
            payload["turn_id"] = result.turn_id
            ctx.outcome = Draft("expense_draft", payload)
            return
        if payment_transfers:
            ctx.outcome = Draft("payment_draft", {"transfers": payment_transfers, "turn_id": result.turn_id})
            return

        attachments = chat.render_bot_attachments(result)
        kind = attachments.get("type") if attachments else None
        if kind == "settlement":
            body = chat._settlement_body(attachments)
        elif kind == "settle_blocked":
            body = chat._settle_blocked_body(attachments)
        elif kind == "statement":
            body = chat._statement_body(attachments)
        elif kind == "summary":
            body = chat._summary_body(attachments)
        elif kind == "random_pick":
            body = chat._random_pick_body(attachments)
        else:
            # The one path where the model's prose reaches the room. An empty
            # completion / error / cap gets a body of ours instead, which is why
            # `claimed_by_pack` is true exactly when there is no prose to validate.
            ctx.outcome = Body(result.final_text or chat._empty_turn_body(result), attachments,
                               claimed_by_pack=not bool(result.final_text))
            return
        ctx.outcome = Body(body, attachments, claimed_by_pack=True)
