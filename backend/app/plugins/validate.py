import logging

from app import chat, moneyguard
from app.db import Database
from app.models import Meal
from kernos.kernel import BasePlugin, Body, Stage, TurnContext, Verdict

log = logging.getLogger("chiatienan")

_EMPTY = {"type": "object", "additionalProperties": False, "properties": {}}


def _prose(ctx: TurnContext) -> Body | None:
    out = ctx.outcome
    if not isinstance(out, Body) or out.claimed_by_pack or not ctx.result or not ctx.result.final_text:
        return None
    return out


def _evidence(ctx: TurnContext, packs) -> list:
    """This turn's invocations minus those of enabled packs whose ``evidence`` is False:
    a CMS read or a log line never backs a number in the reply (Phase 8 review F1)."""
    tools = list(getattr(ctx.result, "tools", None) or [])
    if packs is None or ctx.profile is None:
        return tools
    excluded: set[str] = set()
    for pack, _ in packs.enabled(ctx.profile.tool_packs):
        if getattr(pack, "evidence", True) is False:
            excluded |= set(getattr(pack, "all_tool_names", None) or ())
    return [inv for inv in tools if inv.name not in excluded] if excluded else tools



def _meal_exists(db: Database, room_id: int, meal_id: int) -> bool:
    """Is ``meal_id`` a live (non-voided) meal of ``room_id``?

    Room-scoped and void-aware on purpose: "Đã ghi #14" is a claim about *this*
    room's ledger, and a voided meal is one the room decided never happened.
    """
    with db.session() as s:
        meal = s.get(Meal, meal_id)
        return meal is not None and meal.room_id == room_id and not meal.voided


#: What the room sees instead of a forged confirmation. It has to say the thing
#: the forgery hid — that the ledger is untouched — because the failure is
#: invisible otherwise: nothing was written, so no balance moves and no card
#: appears, and the next person to read the thread has only the bot's word.
_FABRICATED_COMMIT_BODY = (
    "⚠️ This meal was **not recorded** — nothing in the ledger changed.\n"
    "Please say it again (attach the bill photo if you have one, and say who paid and "
    "who shared) — it only reaches the ledger once a draft card appears and someone "
    "presses **Confirm**."
)


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
    config_schema = {"type": "object", "additionalProperties": False,
                     "properties": {"body": {"type": "string", "description": "what the room sees instead of the forgery"}}}
    handles_money = True

    def __init__(self, packs=None) -> None:
        """With a pack registry the commit tools and the "does #N exist" check come from
        the profile's enabled packs (`commit_tools`, `DraftKind.exists` — Phase 6 review
        F6); without one, today's lunch set."""
        self._packs = packs

    def _from_packs(self, ctx: TurnContext):
        if self._packs is None or ctx.profile is None:
            return None, None
        commit: set[str] = set()
        checks = []
        for pack, _ in self._packs.enabled(ctx.profile.tool_packs):
            commit |= set(getattr(pack, "commit_tools", ()) or ())
            checks += [dk.exists for dk in pack.draft_kinds().values() if dk.exists is not None]
        db, room_id = ctx.tool_ctx.db, ctx.tool_ctx.room_id

        def record_exists(record_id: int) -> bool:
            # a claimed id must be a live record of *some* enabled kind; no kind that can
            # say → treat the claim as forged (fail closed)
            with db.session() as s:
                return any(check(s, room_id, record_id) for check in checks)

        return frozenset(commit), record_exists

    async def run(self, ctx: TurnContext, config: dict) -> Verdict | None:
        out = _prose(ctx)
        if out is None:
            return None
        db, room_id = ctx.tool_ctx.db, ctx.tool_ctx.room_id
        commit_tools, record_exists = self._from_packs(ctx)
        tools = _evidence(ctx, self._packs)
        forged = moneyguard.fabricated_commit(
            out.text, f"{ctx.text}\n{ctx.history or ''}", tools,
            meal_exists=record_exists or (lambda mid: _meal_exists(db, room_id, mid)),
            commit_tools=commit_tools,
            # a sub-agent's propose_* made no card, so it proves no write (Phase 7 review F2)
            evidence=[inv for inv in tools if getattr(inv, "from_agent", None) is None],
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
                       Body(config.get("body") or _FABRICATED_COMMIT_BODY, out.attachments, claimed_by_pack=True))


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

    def __init__(self, packs=None) -> None:
        self._packs = packs

    async def run(self, ctx: TurnContext, config: dict) -> Verdict | None:
        out = _prose(ctx)
        if out is None:
            return None
        room_id = ctx.tool_ctx.room_id
        stray = moneyguard.unbacked_amounts(out.text, f"{ctx.text}\n{ctx.history or ''}", _evidence(ctx, self._packs))
        if not stray:
            return None
        log.warning(
            "unbacked money in reply: room=%s turn=%s amounts=%s images=%d tools=%s",
            room_id, ctx.result.turn_id, stray, len(ctx.images or []),
            [inv.name for inv in ctx.result.tools],
        )
        return Verdict(False, "warn", f"unbacked amounts {stray}")
