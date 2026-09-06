"""``kernos.persist.cards``: write the outcome, collect the cards to republish."""
from __future__ import annotations

from kernos.adapters.protocols import HostAdapters
from kernos.kernel.context import Draft, Stage, TurnContext
from kernos.kernel.plugin import BasePlugin
from kernos.packs import PackRegistry


class Cards(BasePlugin):
    """A card this turn retired by re-proposing it, or cancelled on request, is
    republished so open clients stop showing its buttons instead of leaving a
    pending draft that blocks the next settle. The republish events are queued on
    ``pending_events``; the host emits them after it releases the writer lock."""

    id, version, stage = "kernos.persist.cards", "1", Stage.persist
    config_schema = {"type": "object", "additionalProperties": False, "properties": {}}

    def __init__(self, adapters: HostAdapters, packs: PackRegistry) -> None:
        self._a = adapters
        self._packs = packs

    def _cancel_tools(self, ctx: TurnContext) -> set[str]:
        tool_packs = ctx.profile.tool_packs if ctx.profile is not None else []
        names: set[str] = set()
        for pack, _ in self._packs.enabled(tool_packs):
            names |= set(pack.cancel_tools)
        return names

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

        if ctx.result is not None:
            for name in sorted(self._cancel_tools(ctx)):
                for r in _results(ctx.result, name):
                    card = self._a.cards.get(ctx.space_id, r.get("draft_id"))
                    if card is not None:
                        superseded_payloads.append(self._a.messages.to_payload(card))

        ctx.superseded = superseded_payloads
        ctx.pending_events.extend({"type": "message", **stale} for stale in superseded_payloads)


def _results(result, name: str) -> list[dict]:
    """Every successful result for ``name`` — a sub-agent's included, since its
    ``cancel_draft`` took effect too (Phase 7 review F4). A host result shape without the
    ``include_sub`` keyword (a duck-typed fake) is read the old way."""
    try:
        return result.all_results(name, include_sub=True)
    except TypeError:
        return result.all_results(name)
