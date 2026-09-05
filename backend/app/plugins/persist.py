from kernos.adapters import HostAdapters
from kernos.kernel import BasePlugin, Draft, Stage, TurnContext


class Cards(BasePlugin):
    """Write the outcome and collect the cards whose buttons must disappear.

    A card this turn retired by re-proposing it, or cancelled on request, is
    republished so open clients stop showing its buttons instead of leaving a
    pending draft that blocks the next settle. The republish events are queued on
    ``pending_events``; the host emits them after it releases the writer lock,
    exactly where ``run_bot_turn`` emitted them before.
    """

    id, version, stage = "app.persist.cards", "1", Stage.persist
    config_schema = {"type": "object", "additionalProperties": False, "properties": {}}

    def __init__(self, adapters: HostAdapters) -> None:
        self._a = adapters

    async def run(self, ctx: TurnContext, config: dict) -> None:
        outcome = ctx.outcome
        superseded_payloads: list[dict] = []
        if isinstance(outcome, Draft):
            card, superseded = self._a.cards.create(ctx.space_id, outcome.kind, outcome.payload)
            ctx.persisted = card
            superseded_payloads = [self._a.messages.to_payload(m) for m in superseded]
        else:
            ctx.persisted = self._a.messages.post(
                ctx.space_id, author=None, kind="bot", body=outcome.text, attachments=outcome.attachments)

        # A draft the bot cancelled on request is the same situation as a
        # superseded one: open clients still show its buttons until the card
        # itself is republished.
        for r in ctx.result.all_results("cancel_draft") if ctx.result else []:
            card = self._a.cards.get(ctx.space_id, r.get("draft_id"))
            if card is not None:
                superseded_payloads.append(self._a.messages.to_payload(card))

        ctx.superseded = superseded_payloads
        ctx.pending_events.extend({"type": "message", **stale} for stale in superseded_payloads)
