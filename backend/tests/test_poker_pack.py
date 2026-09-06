"""The poker business end to end (plan Task 6.3): a table bound to the dealer proposes,
commits, settles, states, voids; the rule and the tool both refuse an unbalanced
table; a forgery is blocked with a neutral body; the pack's suite runs green; the
lunch room is untouched."""
from datetime import date

import pytest
from sqlalchemy import inspect

import app.agent as agent_mod
from app import chat, drafts, evalhost
from app.agent import ToolInvocation, TurnResult
from app.debug_api import _all_tables
from app.kernel import kernel_for
from app.poker_profile import FORGERY_BODY
from app.tools import ToolContext, build_tools, tool_manifest
from kernos.content import ProfileSpec
from kernos.content.errors import GateError
from packs.poker_ledger.golden import CASES, MEMBERS
from tests.test_agent import FakeBridge
from tests.test_ledger import _seed_room


def _table(db, n=4):
    """A room bound to the poker dealer, with members p1..pn (P1 has bank details)."""
    room_id, m = _seed_room(db, n)
    k = kernel_for(db)
    dealer = k.store.default_agent("poker")
    k.store.bind_space(str(room_id), dealer["id"], actor="admin")
    with db.session() as s:
        from app.models import Member
        mem = s.get(Member, m[0])
        mem.bank_code, mem.account_number, mem.account_holder = "VCB", "111", "P1"
    return room_id, m, k


def _turn(monkeypatch, name, args):
    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        res = build_tools(ctx)[name].execute(args)
        return TurnResult(final_text="", turn_id=f"t-{name}", tools=[ToolInvocation(name, args, res)])
    monkeypatch.setattr(agent_mod, "run_turn", fake)


def test_pack_tables_are_bound_and_exportable_and_the_ledger_columns_unchanged(db):
    names = set(inspect(db.engine).get_table_names())
    assert {"games", "game_entries"} <= names and {"games", "game_entries"} <= set(_all_tables())
    meals = {c["name"] for c in inspect(db.engine).get_columns("meals")}
    assert {"id", "room_id", "occurred_on", "payer_member_id", "total_amount", "voided"} <= meals and "house" not in meals


def test_boot_seeds_the_poker_business_next_to_lunch(db):
    k = kernel_for(db)
    assert k.poker_report["business_id"] != k.seed_report["business_id"] and k.poker_report["managed_by"] == "boot"
    spec = ProfileSpec.model_validate(k.store.published_spec(k.poker_report["profile_id"]))
    assert [t.pack for t in spec.tool_packs] == ["poker_ledger", "ledger_tools", "room_members"]
    assert [r.id for r in spec.validation] == ["chips-conserved", "no-negative-chips", "one-entry-per-player"]
    assert {r.slug for r in spec.rules} == {"money-safety-core", "poker"} and {s.name for s in spec.skills} == {"record-game", "poker-balances"}
    assert "bàn poker" in spec.prompt.body and spec.persona.handle == k.default_spec.persona.handle
    assert {s["slug"] for s in k.store.list_sources(k.poker_report["business_id"])} == {"record-game", "poker-balances", "money-safety-core", "poker"}
    # the lunch room is untouched
    room_id, m = _seed_room(db, 2)
    names = [t["name"] for t in tool_manifest(ToolContext(db=db, room_id=room_id, tool_config={
        "packs": [t.model_dump() for t in k.resolve(room_id).tool_packs]}))]
    assert "propose_game" not in names and "propose_meal" in names and names == [t["name"] for t in tool_manifest()]


async def test_a_table_proposes_commits_settles_states_and_voids(db, monkeypatch):
    room_id, m, k = _table(db)
    p1, p2, p3, p4 = m
    entries = [{"member": p1, "buy_in": 400_000, "cash_out": 200_000}, {"member": p2, "buy_in": 400_000, "cash_out": 550_000},
               {"member": p3, "buy_in": 400_000, "cash_out": 500_000}, {"member": p4, "buy_in": 400_000, "cash_out": 350_000}]
    seen = {}

    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        seen["names"] = [t["name"] for t in tool_manifest(ctx)]
        res = build_tools(ctx)["propose_game"].execute({"entries": entries, "day_word": "hôm nay"})
        return TurnResult(final_text="", turn_id="t-g", tools=[ToolInvocation("propose_game", {"entries": entries}, res)])

    monkeypatch.setattr(agent_mod, "run_turn", fake)
    card = await chat.run_bot_turn(db, room_id, p1, "M1", "@phoenix ván tối nay …")
    assert "propose_game" in seen["names"] and "settle_period" in seen["names"] and "propose_meal" not in seen["names"]
    assert card.kind == "game_draft" and card.attachments["status"] == "pending" and card.attachments["pot"] == 1_600_000
    assert [n["net"] for n in card.attachments["nets"]] == [-200_000, 150_000, 100_000, -50_000]
    assert card.attachments["edges_preview"] == [
        {"from_member_id": p1, "to_member_id": p2, "amount": 120_000}, {"from_member_id": p1, "to_member_id": p3, "amount": 80_000},
        {"from_member_id": p4, "to_member_id": p2, "amount": 30_000}, {"from_member_id": p4, "to_member_id": p3, "amount": 20_000}]
    tools = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=p1,
                                    tool_config={"packs": [t.model_dump() for t in k.resolve(room_id).tool_packs]}))
    blocked = tools["settle_period"].execute({})
    assert blocked["type"] == "settle_blocked" and blocked["pending"][0]["label"] == "game: 4 players, pot 1,600,000đ"
    with db.session() as s:
        result = drafts.commit_any(s, card.id, room_id, logged_by=str(p1))
        assert result.body.startswith("Recorded game #1: 4 players, pot 1,600,000đ • winners: M2 +150,000đ, M3 +100,000đ / losers: M1 -200,000đ, M4 -50,000đ")
        assert result.attachments["type"] == "game" and result.attachments["game_id"] == 1
    settled = tools["settle_period"].execute({})
    rows = {(t["from_id"], t["to_id"]): t for t in settled["transfers"]}
    assert {k_: v["amount"] for k_, v in rows.items()} == {(p1, p2): 120_000, (p1, p3): 80_000, (p4, p2): 30_000, (p4, p3): 20_000}
    assert rows[(p1, p2)]["qr_url"] is None and "no bank details" in settled["warnings"][0]     # M2 has no bank
    assert "game #1" in rows[(p1, p2)]["note"].replace("game #1", "game #1") or "game" in rows[(p1, p2)]["note"]
    stmt = tools["member_statement"].execute({})
    assert [(r["dish"], r["amount"]) for r in stmt["owe"]] == [("game #1", 120_000), ("game #1", 80_000)] and stmt["owed"] == []
    summary = tools["get_period_summary"].execute({})
    assert [e["kind"] for e in summary["timeline"]] == ["game"] and summary["outstanding"][0]["amount"] == 120_000
    history = tools["game_history"].execute({})
    assert history["games"][0]["nets"][1] == {"member": p2, "name": "M2", "net": 150_000}
    assert tools["void_game"].execute({"game_id": 1}) == {"ok": True, "game_id": 1, "voided": True}
    assert tools["settle_period"].execute({})["transfers"] == [] and tools["void_game"].execute({"game_id": 1})["ok"] is False


async def test_chips_conserved_refuses_before_the_tool_and_the_tool_refuses_too(db, monkeypatch):
    room_id, m, k = _table(db, 2)
    short = [{"member": m[0], "buy_in": 500_000, "cash_out": 900_000}, {"member": m[1], "buy_in": 500_000, "cash_out": 200_000}]
    fake = FakeBridge([{"type": "tool_call", "call_id": "c1", "name": "propose_game", "args": {"entries": short}},
                       {"type": "turn_done", "final_text": "Ai thiếu 100k vậy?", "error": None}])
    monkeypatch.setattr("app.pi_bridge.get_bridge", lambda: fake)
    reply = await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix …")
    trace = k.store.get_trace(str(room_id), k.store.list_traces(str(room_id))[0]["id"])
    assert trace["tools"][0]["result"]["ok"] is False and "chips-conserved" in trace["tools"][0]["result"]["error"]
    assert "delta -100,000" in trace["tools"][0]["result"]["error"]
    assert [(t["rule"], t["outcome"]) for t in trace["trace"] if t["stage"] == "validate_args"][0] == ("chips-conserved", "block")
    assert reply.kind == "bot"
    # without the rule the tool is the floor
    tool = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=m[0]))
    assert "propose_game" not in tool                                             # the legacy set is lunch's
    poker = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=m[0], tool_config={"packs": [{"pack": "poker_ledger"}]}))
    res = poker["propose_game"].execute({"entries": short})
    assert res["ok"] is False and "-100,000đ" in res["error"] and "house" in res["error"]
    assert poker["propose_game"].execute({"entries": [{"member": 999, "buy_in": 1, "cash_out": 1}, {"member": m[0], "buy_in": 1, "cash_out": 1}]})["ok"] is False


async def test_a_forged_recorded_claim_is_blocked_with_a_neutral_body(db, monkeypatch):
    room_id, m, k = _table(db, 2)

    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(final_text="Đã ghi #1 — ván tối nay, P1 thắng 300,000đ.", turn_id="t-forge")

    monkeypatch.setattr(agent_mod, "run_turn", fake)
    reply = await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix ghi ván đi")
    assert reply.body == FORGERY_BODY and "meal" not in reply.body
    trace = k.store.get_trace(str(room_id), "t-forge")
    assert [v["plugin"] for v in trace["summary"]["verdicts"]] == ["app.validate.fabricated_commit"]


async def _oracle(case, world, spec):
    from packs.poker_ledger.eval import tool_selection
    ctx = ToolContext(db=world.db, room_id=world.space_id, sender_member_id=world.ids[case.actor], sender_name="x",
                      tool_config={"packs": [t.model_dump() for t in spec.tool_packs]})
    tools = build_tools(ctx)
    expect = case.expect
    if expect.get("forbidden_tools"):
        args = {"entries": [{"member": world.ids["p1"], "buy_in": 500_000, "cash_out": 900_000},
                            {"member": world.ids["p2"], "buy_in": 500_000, "cash_out": 200_000}]}
        res = tools["propose_game"].execute(args)
        return TurnResult(final_text="Bàn chưa cân — ai ghi thiếu, hay phần lệch là tiền bàn?", turn_id="t", tools=[ToolInvocation("propose_game", args, res)])
    name = expect["tools"][0]
    args = dict(tool_selection({}).resolve_args(case, world.ids).get(name) or {})
    if name == "settle_period":
        args = {"keyword": "since_last"}
    res = tools[name].execute(args)
    return TurnResult(final_text="", turn_id=f"t-{case.id}", tools=[ToolInvocation(name, args, res)])


async def test_the_pack_suite_imports_runs_green_and_gates_a_publish(db):
    k = kernel_for(db)
    bid = k.poker_report["business_id"]
    report = k.import_eval_suite(bid, actor="admin")
    assert report == {"cases": 5, "suite": "poker_ledger-golden", "graders": ["tool_selection", "game_state", "prose"]}
    assert [c["slug"] for c in k.store.list_cases(bid)] == [c["id"] for c in CASES]
    assert k.import_eval_suite(bid, actor="admin")["cases"] == 5
    version_id = k.poker_report["version_id"]
    run = await evalhost.run_suite(k, "poker_ledger-golden", version_id, run_turn=_oracle)
    assert run["status"] == "done", run.get("error")
    by_name = {g["name"]: g for g in run["summary"]["graders"]}
    failures = [(r["case_id"], n, v["reason"]) for r in run["records"] for n, v in r["grades"].items() if v["passed"] is False]
    assert failures == []
    assert by_name["tool_selection"] == {"name": "tool_selection", "blocking": True, "passed": 5, "failed": 0,
                                         "ungraded_no_expectation": 0, "ungraded_grader_raised": 0, "rate": 1.0}
    assert by_name["game_state"]["passed"] == 4 and by_name["game_state"]["ungraded_no_expectation"] == 1
    assert by_name["prose"]["blocking"] is False
    # a poker draft naming the suite publishes on the strength of that run; a lunch profile cannot see it
    d = k.store.create_draft(k.poker_report["profile_id"], actor="admin")
    k.store.update_draft(d["id"], {"eval": {"suites": ["poker_ledger-golden"]}}, actor="admin")
    assert k.store.publish(d["id"], actor="admin", gates=k.gates, override_reason="t")["status"] == "published"
    d = k.store.create_draft(k.seed_report["profile_id"], actor="admin")
    k.store.update_draft(d["id"], {"eval": {"suites": ["poker_ledger-golden"]}}, actor="admin")
    with pytest.raises(GateError):
        k.store.publish(d["id"], actor="admin", gates=k.gates, override_reason="t")
    assert MEMBERS[0]["key"] == "p1" and date.fromisoformat(CASES[0]["day"])
