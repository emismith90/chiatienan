"""A manager agent asks its sub-agents (design §6, plan Task 7.1).

The proof runs the **real** ``agent.run_turn`` twice on one scripted bridge — the
manager's turn and, nested inside its ``ask_auditor`` call, the sub's — and checks the
three things the mechanism exists for: the sub's structured results reach the record
while its prose never backs a number, the sub runs inside the manager's budget, and
the room sees one turn.
"""
import json
import time
from datetime import date

import pytest

from app import agent, chat, drafts, ledger
from app.kernel import kernel_for
from app.plugins.validate import _FABRICATED_COMMIT_BODY
from app.tools import ToolContext
from kernos.agents import DelegationPack, FLOOR_SECONDS
from kernos.content import Invalid
from kernos.engine import ToolInvocation, TurnResult
from kernos.engine.pi import PiEngine
from kernos.engine.base import EngineSpec
from kernos.kernel import Stage
from tests.test_ledger import _seed_room


class ScriptedBridge:
    """One bridge, several runs: the first ``run`` command gets ``scripts[0]`` (the
    manager), every later one the next script (the subs, in call order). Records
    every command and everything Python sent back."""

    def __init__(self, *scripts):
        self._scripts = list(scripts)
        self.runs: list[dict] = []
        self.sent: list[dict] = []

    async def request(self, command):
        self.runs.append(command)
        script = self._scripts.pop(0)
        for message in script:
            yield dict(message, req_id=command["req_id"])

    async def send(self, message):
        self.sent.append(message)

    def tool_result(self, call_id: str) -> dict:
        for m in self.sent:
            if m["type"] == "tool_result" and m["call_id"] == call_id:
                return json.loads(m["content"])
        raise AssertionError(f"no tool_result for {call_id}")


def _room(db, n=3):
    """A lunch room with one 300k meal (M1 paid, everyone shared) and the default
    manager `phoenix` delegating to a `sub` called `auditor` on the same profile."""
    room_id, m = _seed_room(db, n)
    with db.session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=m[0], participants=m,
                           total_amount=300_000, occurred_on=date(2026, 9, 1))
    k = kernel_for(db)
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    sub = k.store.create_agent(bid, "auditor", "Auditor", profile_id=pid, role="sub",
                               description="checks who owes whom")
    manager = k.store.default_agent(bid)
    k.store.update_agent(manager["id"], {"delegates_to": [sub["id"]]})
    return room_id, m, k, sub


def _install(monkeypatch, *scripts) -> ScriptedBridge:
    fake = ScriptedBridge(*scripts)
    monkeypatch.setattr("app.pi_bridge.get_bridge", lambda: fake)
    return fake


def _turn_done(text, **extra):
    return {"type": "turn_done", "final_text": text, "error": None, "capped": False, "stats": None, **extra}


def _tool_names(run_command):
    return [t["name"] for t in run_command["tools"]]


async def _run(db, room_id, m, text, emit_to=None):
    async def emit(e):
        if emit_to is not None:
            emit_to.append(e)
    return await chat.run_bot_turn(db, room_id, m[0], "M1", text, emit=emit)


# --------------------------------------------------------------------- the mechanism

async def test_the_manager_asks_and_gets_results_but_never_the_text_as_evidence(db, monkeypatch):
    room_id, m, k, _sub = _room(db)
    manager = [
        {"type": "agent.run.started", "turn_id": "t"},
        *[{"type": "tool_call", "call_id": f"c{i}", "name": "resolve_period", "args": {}} for i in (1, 2, 3)],
        {"type": "tool_call", "call_id": "c4", "name": "ask_auditor", "args": {"task": "ai nợ ai bao nhiêu?"}},
        {"type": "agent.text.delta", "turn_id": "t", "delta": "M2"},
        {"type": "agent.run.finished", "turn_id": "t"},
        _turn_done("M2 và M3 mỗi người nợ M1 100,000đ."),
    ]
    auditor = [
        {"type": "agent.run.started", "turn_id": "s"},
        {"type": "agent.tool.start", "turn_id": "s", "name": "settle_period"},
        {"type": "tool_call", "call_id": "c1", "name": "settle_period", "args": {}},
        {"type": "agent.tool.result", "turn_id": "s", "name": "settle_period", "status": "completed"},
        {"type": "agent.text.delta", "turn_id": "s", "delta": "Bàn lệch"},
        {"type": "agent.run.finished", "turn_id": "s"},
        _turn_done("Bàn lệch 999k, M2 và M3 nợ M1."),
    ]
    fake = _install(monkeypatch, manager, auditor)
    events = []
    reply = await _run(db, room_id, m, "@phoenix ai nợ ai?", events)

    # the manifests: the manager has `ask_auditor`, the sub (depth 1 of max_depth 2) has no ask_*
    assert len(fake.runs) == 2
    assert "ask_auditor" in _tool_names(fake.runs[0]) and _tool_names(fake.runs[0])[:19] == _tool_names(fake.runs[1])[:19]
    assert not any(n.startswith("ask_") for n in _tool_names(fake.runs[1]))
    ask = next(t for t in fake.runs[0]["tools"] if t["name"] == "ask_auditor")
    assert "Auditor" in ask["description"] and "checks who owes whom" in ask["description"]
    assert "not a card" in ask["description"] and ask["schema"]["required"] == ["task"]

    # the sub's run carries the clamped caps: the manager's tools − the 3 made (40 → 37 with the
    # default cap); seconds under the manager's, less the margin
    sub_run = fake.runs[1]
    assert fake.runs[0]["max_tools"] == 40 and sub_run["max_tools"] == 37
    assert 0 < sub_run["max_seconds"] <= fake.runs[0]["max_seconds"] - 15
    assert sub_run["message"].startswith("ai nợ ai bao nhiêu?") or "ai nợ ai bao nhiêu?" in sub_run["message"]
    assert sub_run["req_id"] != fake.runs[0]["req_id"]

    # what the model read carries the sub's text — here its own pack's settlement body, since
    # the sub's render stage ran (its prose "Bàn lệch 999k" was never the outcome); what was
    # recorded carries no text at all
    content = fake.tool_result("c4")
    assert content["ok"] is True and content["text"].startswith("Provisional through") and "_record" not in content
    assert "100,000đ" in content["text"] and "999" not in content["text"]
    assert content["results"][0]["name"] == "settle_period" and content["results"][0]["result"]["ok"] is True
    trace = k.store.get_trace(str(room_id), k.store.list_traces(str(room_id))[0]["id"])
    recorded = trace["tools"]
    assert [(t["name"], t["from_agent"]) for t in recorded] == [
        ("resolve_period", None), ("resolve_period", None), ("resolve_period", None),
        ("ask_auditor", None), ("settle_period", "auditor")]
    assert set(recorded[3]["result"]) == {"ok", "agent", "results"} and recorded[3]["result"]["agent"] == "auditor"
    assert "999" not in json.dumps(recorded[3]["result"])

    # the reply quoted an amount from the sub's *result*: backed, posted as prose, no card
    assert reply.kind == "bot" and reply.body.startswith("M2 và M3")
    assert trace["summary"]["verdicts"] == []
    # the trace: the sub's rows are a span, the summary counts own rows and names the sub's call
    spans = [t for t in trace["trace"] if t.get("span") == "auditor"]
    assert spans and all(t["depth"] == 1 for t in spans)
    assert {t["stage"] for t in spans} <= {"context", "prompt", "model", "run", "render", "validate"}
    own_ms = sum(t["ms"] for t in trace["trace"] if "span" not in t)
    assert trace["summary"]["elapsed_ms"] == round(own_ms, 1)
    assert trace["summary"]["tools"] == ["resolve_period"] * 3 + ["ask_auditor", "auditor:settle_period"]

    # the room saw one turn (F3)
    kinds = [e["type"] for e in events]
    assert kinds.count("agent.run.started") == 1 and kinds.count("agent.run.finished") == 1
    assert [e["delta"] for e in events if e["type"] == "agent.text.delta"] == ["M2"]
    manager_turn = trace["turn_id"]
    started = next(e for e in events if e["type"] == "agent.sub.started")
    finished = next(e for e in events if e["type"] == "agent.sub.finished")
    assert started["turn_id"] == manager_turn and started["agent"] == "auditor" and started["task"] == "ai nợ ai bao nhiêu?"
    assert finished["turn_id"] == manager_turn and finished["tools"] == ["settle_period"] and finished["error"] is None
    forwarded = [e for e in events if e.get("agent") == "auditor" and e["type"].startswith("agent.tool.")]
    assert [e["type"] for e in forwarded] == ["agent.tool.start", "agent.tool.result"]
    assert all(e["turn_id"] == manager_turn for e in forwarded)


async def test_an_amount_that_appears_only_in_the_subs_text_is_unbacked(db, monkeypatch):
    room_id, m, k, _ = _room(db)
    manager = [{"type": "tool_call", "call_id": "a1", "name": "ask_auditor", "args": {"task": "kiểm tra"}},
               _turn_done("Bàn lệch 999k đó.")]
    auditor = [{"type": "tool_call", "call_id": "s1", "name": "resolve_period", "args": {}},      # no pack body: prose
               _turn_done("Bàn lệch 999k.")]
    fake = _install(monkeypatch, manager, auditor)
    reply = await _run(db, room_id, m, "@phoenix kiểm tra")
    content = fake.tool_result("a1")
    assert content["text"] == "Bàn lệch 999k." and "999" not in json.dumps(content["results"])   # the model read it…
    trace = k.store.get_trace(str(room_id), k.store.list_traces(str(room_id))[0]["id"])
    assert reply.kind == "bot"
    # …the sub's own validator flagged its prose (a span verdict), and the manager's flagged the copy
    assert [(v["plugin"], v["reason"], v.get("span")) for v in trace["summary"]["verdicts"]] == [
        ("app.validate.unbacked_amounts", "unbacked amounts [999000]", "auditor"),
        ("app.validate.unbacked_amounts", "unbacked amounts [999000]", None)]


async def test_a_commit_claim_backed_only_by_the_subs_proposal_is_blocked(db, monkeypatch):
    room_id, m, k, _ = _room(db)
    manager = [{"type": "tool_call", "call_id": "a1", "name": "ask_auditor", "args": {"task": "ghi bữa 300k"}},
               _turn_done("Đã ghi #2 — cả nhóm 300,000đ.")]                  # no meal #2 exists
    auditor = [{"type": "tool_call", "call_id": "s1", "name": "propose_meal",
                "args": {"payer": m[0], "participants": m, "total": 300_000}},
               _turn_done("Đề xuất xong.")]
    fake = _install(monkeypatch, manager, auditor)
    reply = await _run(db, room_id, m, "@phoenix ghi bữa 300k")
    # the sub's proposal reached the manager as data…
    assert fake.tool_result("a1")["results"][0]["result"]["type"] == "expense_draft"
    # …made no card, and is no evidence of a write (F2)
    with db.session() as s:
        assert drafts.list_pending_drafts(s, room_id) == []
    assert reply.kind == "bot" and reply.body == _FABRICATED_COMMIT_BODY
    trace = k.store.get_trace(str(room_id), k.store.list_traces(str(room_id))[0]["id"])
    assert [v["plugin"] for v in trace["summary"]["verdicts"]] == ["app.validate.fabricated_commit"]
    assert trace["tools"][1] == {"name": "propose_meal", "args": trace["tools"][1]["args"],
                                 "result": trace["tools"][1]["result"], "from_agent": "auditor"}


async def test_a_subs_cancel_takes_effect_and_republishes_while_its_proposal_makes_no_card(db, monkeypatch):
    room_id, m, k, _ = _room(db)
    with db.session() as s:
        card, _ = drafts.create_payment_draft(s, room_id, {"transfers": [
            {"from_member_id": m[1], "to_member_id": m[0], "amount": 100_000, "note": None}]})
        card_id = card.id
    manager = [{"type": "tool_call", "call_id": "c1", "name": "ask_auditor", "args": {"task": f"huỷ thẻ #{card_id}, rồi đề xuất M3 trả M1"}},
               _turn_done(f"Đã huỷ thẻ #{card_id}. M3 trả M1 100,000đ nhé?")]
    auditor = [{"type": "tool_call", "call_id": "c1", "name": "cancel_draft", "args": {"draft_id": card_id}},
               {"type": "tool_call", "call_id": "c2", "name": "propose_payment",
                "args": {"from": m[2], "to": m[0], "amount": 100_000}},
               _turn_done("Đã huỷ và đề xuất.")]
    _install(monkeypatch, manager, auditor)
    events = []
    reply = await _run(db, room_id, m, f"@phoenix huỷ #{card_id}", events)
    with db.session() as s:
        assert drafts.list_pending_drafts(s, room_id) == []                # cancelled, and no payment card (F4)
    assert reply.kind == "bot" and reply.body.startswith("Đã huỷ")
    republished = [e for e in events if e["type"] == "message" and e["id"] == card_id]
    assert len(republished) == 1 and republished[0]["attachments"]["status"] == "cancelled"


async def test_a_sub_whose_prose_is_a_forgery_hands_the_manager_the_replacement(db, monkeypatch):
    room_id, m, k, _ = _room(db)
    manager = [{"type": "tool_call", "call_id": "c1", "name": "ask_auditor", "args": {"task": "ghi đi"}},
               _turn_done("Chưa ghi gì cả.")]
    auditor = [_turn_done("Đã ghi #7 — 500,000đ.")]
    fake = _install(monkeypatch, manager, auditor)
    await _run(db, room_id, m, "@phoenix ghi đi")
    content = fake.tool_result("c1")
    assert content["ok"] is True and content["text"] == _FABRICATED_COMMIT_BODY and content["results"] == []
    trace = k.store.get_trace(str(room_id), k.store.list_traces(str(room_id))[0]["id"])
    verdicts = trace["summary"]["verdicts"]
    assert verdicts == [{"plugin": "app.validate.fabricated_commit", "outcome": "block",
                         "reason": "fabricated commit", "span": "auditor"}]
    assert trace["summary"]["stopped"] is False and trace["summary"]["error"] is None


async def test_no_time_budget_left_refuses_without_a_nested_run():
    ran = []

    async def run_sub(ctx, sub, task, *, budget):
        ran.append(budget)
        return {"text": "", "results": [], "capped": False, "invocations": []}

    pack = DelegationPack(lambda agent: [{"id": 2, "slug": "auditor", "name": "Auditor", "description": None}], run_sub)
    spec = EngineSpec(model="m", vision_model=None, thinking="off", builtin_tools=[], max_tools=40,
                      max_seconds=120, cwd="/c", agent_dir="/a")
    ctx = ToolContext(db=None, room_id=1, agent={"delegates_to": [2], "max_depth": 2}, engine_spec=spec,
                      started_at=time.monotonic() - (120 - 15 - FLOOR_SECONDS + 1), calls_made=1)
    tools = pack.tools(ctx)
    assert list(tools) == ["ask_auditor"]
    assert await tools["ask_auditor"].execute({"task": "x"}) == {"ok": False, "error": "no time budget left to delegate"}
    assert ran == []
    ctx.started_at = time.monotonic() - 10
    ctx.calls_made = 4                                     # the ask itself is the 4th call: 3 made before it
    out = await tools["ask_auditor"].execute({"task": "x"})
    assert out["ok"] is True and ran[0]["max_tools"] == 37 and 90 <= ran[0]["max_seconds"] <= 95
    assert await tools["ask_auditor"].execute({}) == {"ok": False, "error": "Missing task: say what the sub-agent should find out or do."}
    # depth: no ask_* tools once depth + 1 reaches the root's limit
    assert pack.tools(ToolContext(db=None, room_id=1, agent={"delegates_to": [2]}, depth=1, max_depth=2)) == {}
    assert list(pack.tools(ToolContext(db=None, room_id=1, agent={"delegates_to": [2]}, depth=1, max_depth=3))) == ["ask_auditor"]
    assert pack.tools(ToolContext(db=None, room_id=1)) == {}


async def test_depth_limit_ends_a_cycle(db, monkeypatch):
    room_id, m, k, auditor = _room(db)
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    checker = k.store.create_agent(bid, "checker", "Checker", profile_id=pid, role="sub")
    k.store.update_agent(auditor["id"], {"delegates_to": [checker["id"]]})
    k.store.update_agent(checker["id"], {"delegates_to": [auditor["id"]]})          # B → C → B is legal
    manager = k.store.default_agent(bid)
    k.store.update_agent(manager["id"], {"max_depth": 3})
    scripts = [
        [{"type": "tool_call", "call_id": "a1", "name": "ask_auditor", "args": {"task": "hỏi checker"}}, _turn_done("ok")],
        [{"type": "tool_call", "call_id": "b1", "name": "ask_checker", "args": {"task": "hỏi lại auditor"}}, _turn_done("b")],
        [{"type": "tool_call", "call_id": "c1", "name": "ask_auditor", "args": {"task": "loop"}}, _turn_done("c")],
    ]
    fake = _install(monkeypatch, *scripts)
    await _run(db, room_id, m, "@phoenix hỏi vòng")
    assert len(fake.runs) == 3
    asks = [[n for n in _tool_names(r) if n.startswith("ask_")] for r in fake.runs]
    assert asks == [["ask_auditor"], ["ask_checker"], []]                   # depth 2 of 3: no ask_* tools
    assert fake.tool_result("a1")["ok"] is True and fake.tool_result("b1")["ok"] is True
    assert fake.tool_result("c1")["ok"] is False                             # no such tool at depth 2
    trace = k.store.get_trace(str(room_id), k.store.list_traces(str(room_id))[0]["id"])
    assert [(t["name"], t["from_agent"], t["result"]["ok"]) for t in trace["tools"]] == [
        ("ask_auditor", None, True), ("ask_checker", "auditor", True), ("ask_auditor", "checker", False)]
    assert "unknown tool" in trace["tools"][2]["result"]["error"]
    assert {t.get("span") for t in trace["trace"]} == {None, "auditor", "checker"}


# ------------------------------------------------------------------------ the pieces

def test_turn_result_reads_own_invocations_unless_asked():
    own = ToolInvocation("propose_payment", {}, {"ok": True, "who": "own"})
    sub = ToolInvocation("propose_payment", {}, {"ok": True, "who": "sub"}, from_agent="auditor")
    tr = TurnResult(tools=[own, sub])
    assert tr.last_result("propose_payment") == {"ok": True, "who": "own"}
    assert tr.all_results("propose_payment") == [{"ok": True, "who": "own"}]
    assert tr.last_result("propose_payment", include_sub=True) == {"ok": True, "who": "sub"}
    assert TurnResult(tools=[sub]).last_result("propose_payment") is None


async def test_the_engine_records_the_record_and_sends_the_payload_without_it():
    fake = ScriptedBridge([{"type": "tool_call", "call_id": "c1", "name": "ask_x", "args": {"task": "t"}}, _turn_done("done")])
    spec = EngineSpec(model="m", vision_model=None, thinking="off", builtin_tools=[], max_tools=1,
                      max_seconds=1, cwd="/c", agent_dir="/a")

    async def call_tool(name, args):
        return {"ok": True, "text": "prose 999k", "results": [], "_record": {"ok": True, "agent": "x", "results": []}}

    result = await PiEngine(fake).run(spec, turn_id="t", message="m", images=None, tools=[], call_tool=call_tool, emit=None)
    assert result.tools[0].result == {"ok": True, "agent": "x", "results": []}
    assert json.loads(fake.sent[0]["content"]) == {"ok": True, "text": "prose 999k", "results": []}


def test_merge_keeps_each_subs_calls_after_the_ask_that_made_them():
    a1, a2 = ToolInvocation("ask_a", {}, {}), ToolInvocation("ask_a", {}, {})
    s1, s2 = ToolInvocation("x", {}, {}, from_agent="a"), ToolInvocation("y", {}, {}, from_agent="a")
    merged = agent._merge_sub_invocations([a1, ToolInvocation("own", {}, {}), a2], [(0, s1), (2, s2)])
    assert [(i.name, i.from_agent) for i in merged] == [("ask_a", None), ("x", "a"), ("own", None), ("ask_a", None), ("y", "a")]


def test_the_store_refuses_bad_delegation(db):
    k = kernel_for(db)
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    manager = k.store.default_agent(bid)
    sub = k.store.create_agent(bid, "auditor", "Auditor", profile_id=pid, role="sub", description="d")
    assert k.store.get_agent(sub["id"])["description"] == "d"
    other_manager = k.store.create_agent(bid, "boss", "Boss", profile_id=pid)
    for entries, needle in [([manager["id"]], "itself"), ([other_manager["id"]], "only a sub"),
                            ([sub["id"], 9_999], "not an agent"), (["auditor"], "not an agent id"),
                            ("auditor", "must be a list")]:
        with pytest.raises(Invalid, match=needle):
            k.store.update_agent(manager["id"], {"delegates_to": entries})
    with pytest.raises(Invalid, match="itself"):
        k.store.update_agent(sub["id"], {"delegates_to": [sub["id"]]})
    with pytest.raises(Invalid, match="only a sub"):
        k.store.create_agent(bid, "m2", "M2", profile_id=pid, delegates_to=[other_manager["id"]])
    k.store.update_agent(manager["id"], {"delegates_to": [sub["id"]]})
    assert [a["slug"] for a in k.store.referrers(sub["id"])] == [manager["slug"]] and k.store.referrers(manager["id"]) == []
    # a referenced sub cannot change role; neither can a default or bound agent
    with pytest.raises(Invalid, match="role cannot change"):
        k.store.update_agent(sub["id"], {"role": "manager"})
    with pytest.raises(Invalid, match="role cannot change"):
        k.store.update_agent(manager["id"], {"role": "sub"})
    k.store.bind_space("room-9", other_manager["id"])
    with pytest.raises(Invalid, match="role cannot change"):
        k.store.update_agent(other_manager["id"], {"role": "sub"})
    with pytest.raises(Invalid, match="only a manager can be the default"):
        k.store.update_agent(sub["id"], {"is_default": True})
    with pytest.raises(Invalid, match="role must be"):
        k.store.update_agent(sub["id"], {"role": "boss"})
    # an unreferenced, unbound, non-default agent may still change role
    free = k.store.create_agent(bid, "free", "Free", profile_id=pid, role="sub")
    assert k.store.update_agent(free["id"], {"role": "manager", "description": "now a manager"})["role"] == "manager"


async def test_a_room_without_delegation_runs_exactly_as_before(db, monkeypatch):
    room_id, m = _seed_room(db, 2)
    fake = _install(monkeypatch, [_turn_done("Chào.")])
    await _run(db, room_id, m, "@phoenix chào")
    assert not any(n.startswith("ask_") for n in _tool_names(fake.runs[0])) and len(_tool_names(fake.runs[0])) == 19
