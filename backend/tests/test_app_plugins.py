"""chiatienan's plugins reproduce the blocks of run_bot_turn they were moved from."""
from app import chat
from app.agent import ToolInvocation, TurnResult
from app.hostadapters import build_adapters
from app.default_profile import build_default_spec
from app.packs import host_packs
from app.plugins.prompt import PhoenixSystemPrompt
from app.plugins.run import LegacyRunTurn
from app.plugins.validate import FabricatedCommit, UnbackedAmounts
from app.prompt import build_system_prompt
from app.tools import ToolContext
from kernos.kernel import Body, Draft, Principal, TurnContext
from kernos.packs import PackRegistry
from kernos.plugins import Cards as KernelCards, PackRender
from tests.test_ledger import _seed_room


def _packs():
    reg = PackRegistry()
    reg.register_all(host_packs())
    return reg


def _ctx(db, room_id, m, **kw):
    kw.setdefault("profile", build_default_spec())
    return TurnContext(space_id=str(room_id), principal=Principal(m[0], "M1"), text="@phoenix x",
                       tool_ctx=ToolContext(db=db, room_id=room_id, sender_member_id=m[0], sender_name="M1"), **kw)


async def test_phoenix_prompt_is_todays_system_prompt(db):
    room_id, m = _seed_room(db, 2)
    ctx = _ctx(db, room_id, m)
    await PhoenixSystemPrompt().run(ctx, {})
    assert ctx.system == build_system_prompt(sender_name="M1", sender_id=m[0])


async def test_render_decides_draft_payment_typed_body_or_prose(db):
    room_id, m = _seed_room(db, 2)
    r = PackRender(_packs())
    ctx = _ctx(db, room_id, m)
    ctx.result = TurnResult(final_text="ignored", turn_id="t1", tools=[ToolInvocation("propose_meal", {}, {
        "ok": True, "payer_member_id": m[0], "member_participants": m, "bill_total": 1000})])
    await r.run(ctx, {})
    assert isinstance(ctx.outcome, Draft) and ctx.outcome.kind == "expense_draft"
    assert ctx.outcome.payload["raw_input"] == "@phoenix x" and ctx.outcome.payload["logged_by"] == str(m[0])
    assert ctx.outcome.payload["turn_id"] == "t1" and ctx.outcome.payload["dish"] is None

    ctx.result = TurnResult(final_text="x", tools=[
        ToolInvocation("propose_payment", {}, {"ok": True, "type": "payment_draft", "from_member_id": m[0], "to_member_id": m[1], "amount": 100, "note": None}),
        ToolInvocation("propose_payment", {}, {"ok": True, "type": "payment_draft", "from_member_id": m[0], "to_member_id": m[1], "amount": 150, "note": None})])
    await r.run(ctx, {})
    assert ctx.outcome.kind == "payment_draft" and [t["amount"] for t in ctx.outcome.payload["transfers"]] == [150]

    settle = {"ok": True, "period": {"from": "2026-07-01", "to": "2026-07-20"}, "transfers": [], "warnings": [], "committed": False}
    ctx.result = TurnResult(final_text="999đ", tools=[ToolInvocation("settle_period", {}, settle)])
    await r.run(ctx, {})
    assert isinstance(ctx.outcome, Body) and ctx.outcome.claimed_by_pack and "999" not in ctx.outcome.text
    assert ctx.outcome.attachments["type"] == "settlement"

    ctx.result = TurnResult(final_text="chào bạn")
    await r.run(ctx, {})
    assert ctx.outcome == Body("chào bạn", None, claimed_by_pack=False)

    ctx.result = TurnResult(final_text="", error="boom")
    await r.run(ctx, {})
    assert ctx.outcome.claimed_by_pack and "boom" in ctx.outcome.text


async def test_validators_block_forgeries_warn_on_unbacked_and_skip_claimed_bodies(db, caplog):
    room_id, m = _seed_room(db, 1)
    ctx = _ctx(db, room_id, m)
    forgery = "Đã ghi #14 — Texas Chicken: Bạch Mai trả tổng 793,760đ • M1 132,293đ"
    ctx.result = TurnResult(final_text=forgery, turn_id="t-forge")
    ctx.outcome = Body(forgery, None)
    with caplog.at_level("ERROR", logger="chiatienan"):
        v = await FabricatedCommit().run(ctx, {})
    assert v is not None and v.severity == "block" and "not recorded" in v.replacement.text.lower()
    assert v.replacement.claimed_by_pack and "suppressed fabricated commit" in caplog.text

    ctx.result = TurnResult(final_text="Bùi Trang −75,000đ", turn_id="t-abc")
    ctx.outcome = Body("Bùi Trang −75,000đ", None)
    assert await FabricatedCommit().run(ctx, {}) is None
    with caplog.at_level("WARNING", logger="chiatienan"):
        v = await UnbackedAmounts().run(ctx, {})
    assert v is not None and v.severity == "warn" and "unbacked money in reply" in caplog.text

    ctx.outcome = Body("Bùi Trang −75,000đ", {"type": "settlement"}, claimed_by_pack=True)
    assert await UnbackedAmounts().run(ctx, {}) is None and await FabricatedCommit().run(ctx, {}) is None


async def test_persist_writes_drafts_and_queues_superseded_and_cancelled_cards(db):
    room_id, m = _seed_room(db, 2)
    a = build_adapters(db)
    ctx = _ctx(db, room_id, m)
    payload = {"payer_member_id": m[0], "member_participants": m, "guests": [], "bill_total": 300000,
               "adjustments": [], "dish": None, "initiator": None, "note": None, "per_head_preview": 150000,
               "occurred_on": None, "raw_input": "x", "logged_by": str(m[0]), "turn_id": "t"}
    ctx.result = TurnResult(final_text="")
    ctx.outcome = Draft("expense_draft", dict(payload))
    await KernelCards(a, _packs()).run(ctx, {})
    first = ctx.persisted
    assert first.kind == "expense_draft" and ctx.pending_events == []

    ctx2 = _ctx(db, room_id, m)
    ctx2.result = TurnResult(final_text="", tools=[ToolInvocation("cancel_draft", {"draft_id": first.id},
                                                                   {"ok": True, "draft_id": first.id})])
    ctx2.outcome = Draft("expense_draft", dict(payload))
    await KernelCards(a, _packs()).run(ctx2, {})
    kinds = [(e["type"], e["id"], e["attachments"]["status"]) for e in ctx2.pending_events]
    # superseded by the re-proposal, then republished again as the cancelled card
    assert kinds == [("message", first.id, "superseded"), ("message", first.id, "superseded")]

    ctx3 = _ctx(db, room_id, m)
    ctx3.result = TurnResult(final_text="hi")
    ctx3.outcome = Body("hi", None)
    await KernelCards(a, _packs()).run(ctx3, {})
    assert ctx3.persisted.kind == "bot" and ctx3.persisted.body == "hi"


async def test_legacy_run_hands_spec_and_overrides_to_run_turn_looked_up_at_call_time(db, monkeypatch):
    room_id, m = _seed_room(db, 1)
    from app.default_profile import build_default_spec
    seen = {}

    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        seen.update(text=user_text, spec=ctx.engine_spec, system=ctx.system_override,
                    message=ctx.message_override, images=images, memory=memory, history=history)
        return TurnResult(final_text="ok", turn_id="t-run")

    monkeypatch.setattr("app.agent.run_turn", fake)
    ctx = _ctx(db, room_id, m, profile=build_default_spec(), system="S", message="M", history="H")
    await LegacyRunTurn().run(ctx, {})
    assert seen["spec"] == build_default_spec().to_engine_spec() and seen["system"] == "S"
    assert seen["message"] == "M" and seen["images"] is None and seen["memory"] is None and seen["history"] == "H"
    assert ctx.result.final_text == "ok" and ctx.turn_id == "t-run"
