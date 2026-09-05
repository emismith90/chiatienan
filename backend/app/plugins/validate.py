import logging

from app import chat, moneyguard
from kernos.kernel import BasePlugin, Body, Stage, TurnContext, Verdict

log = logging.getLogger("chiatienan")

_EMPTY = {"type": "object", "additionalProperties": False, "properties": {}}


def _prose(ctx: TurnContext) -> Body | None:
    out = ctx.outcome
    if not isinstance(out, Body) or out.claimed_by_pack or not ctx.result or not ctx.result.final_text:
        return None
    return out


class FabricatedCommit(BasePlugin):
    """The one class of prose that is wrong however the numbers were obtained:
    text that says the ledger was written when no tool wrote it.

    2026-08-14, room 3 — a bill photo and "log this for all" came back in 6.1s with
    `tools=0` as a word-perfect forgery of `_meal_body`: "Đã ghi #14 — Texas
    Chicken: Bạch Mai trả tổng 793,760đ • …". There was no meal #14, "Bạch Mai" is
    the branch on the receipt rather than anyone in the room, and the split listed
    six of seven members. Nothing distinguished it from a real confirmation, so the
    room believed it, and asking again just reproduced it. Reporting is not enough
    for this one: the message must not be posted.

    `meal_exists` is what makes that stick across repeats. The forgery was posted,
    so its numbers joined the room's own history, and the history legitimately
    backs amounts — which quietly cleared every retelling (three more over 44
    minutes, `tools=0` each time). The ledger cannot be talked round: "Đã ghi #14"
    is checked against `meals`.
    """

    id, version, stage = "app.validate.fabricated_commit", "1", Stage.validate
    config_schema = _EMPTY
    handles_money = True

    async def run(self, ctx: TurnContext, config: dict) -> Verdict | None:
        out = _prose(ctx)
        if out is None:
            return None
        db, room_id = ctx.tool_ctx.db, ctx.tool_ctx.room_id
        forged = moneyguard.fabricated_commit(
            out.text, f"{ctx.text}\n{ctx.history or ''}", ctx.result.tools,
            meal_exists=lambda mid: chat._meal_exists(db, room_id, mid),
        )
        if forged is None:
            return None
        log.error(
            "suppressed fabricated commit: room=%s turn=%s amounts=%s "
            "images=%d tools=%s text=%r",
            room_id, ctx.result.turn_id, forged, len(ctx.images or []),
            [inv.name for inv in ctx.result.tools], out.text[:400],
        )
        return Verdict(False, "block", "fabricated commit",
                       Body(chat._FABRICATED_COMMIT_BODY, out.attachments, claimed_by_pack=True))


class UnbackedAmounts(BasePlugin):
    """Report money in the reply that no tool produced (see ``app.moneyguard``).

    The history counts as the user having said it. A number the room stated two
    messages ago and the bot repeats back ("bạn nói tổng 324k") is not invented
    money, and flagging it buries the alerts that matter. `images=N` matters for
    triage: of the alerts that survive a tool-output allow-set, all but one were
    prices the model read off a bill photo — correct, but unattributable by
    construction because image content is not text. Those stay a warning.
    """

    id, version, stage = "app.validate.unbacked_amounts", "1", Stage.validate
    config_schema = _EMPTY
    handles_money = True

    async def run(self, ctx: TurnContext, config: dict) -> Verdict | None:
        out = _prose(ctx)
        if out is None:
            return None
        room_id = ctx.tool_ctx.room_id
        stray = moneyguard.unbacked_amounts(out.text, f"{ctx.text}\n{ctx.history or ''}", ctx.result.tools)
        if not stray:
            return None
        log.warning(
            "unbacked money in reply: room=%s turn=%s amounts=%s images=%d tools=%s",
            room_id, ctx.result.turn_id, stray, len(ctx.images or []),
            [inv.name for inv in ctx.result.tools],
        )
        return Verdict(False, "warn", f"unbacked amounts {stray}")
