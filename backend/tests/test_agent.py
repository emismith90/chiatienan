import json
import types
from types import SimpleNamespace

import pytest

import app.agent as agent_mod
from app import agui
from app.agent import (
    ToolInvocation,
    TurnResult,
    _render_prompt,
    _unwrap_tool_args,
    _unwrap_tool_name,
    _unwrap_tool_result,
    run_turn,
)
from app.prompt import build_system_prompt
from app.tools import ToolContext
from tests.test_ledger import _seed_room


# --- pure helpers ---------------------------------------------------------- #

def test_unwrap_tool_name_from_mcp_wrapper():
    assert _unwrap_tool_name("mcp", {"toolName": "record_meal"}) == "record_meal"
    assert _unwrap_tool_name("record_meal", {}) == "record_meal"
    assert _unwrap_tool_name(None, None) == "tool"


def test_unwrap_tool_args_from_wrapper():
    assert _unwrap_tool_args({"toolName": "x", "args": {"a": 1}}) == {"a": 1}
    assert _unwrap_tool_args({"a": 1}) == {"a": 1}


def test_unwrap_result_direct_dict():
    assert _unwrap_tool_result({"ok": True, "meal_id": 1}) == {"ok": True, "meal_id": 1}


def test_unwrap_result_from_mcp_envelope():
    payload = {"ok": True, "amount": 100}
    envelope = {"value": {"content": [{"text": {"text": json.dumps(payload)}}]}}
    assert _unwrap_tool_result(envelope) == payload


def test_turn_result_last_result_picks_last_ok():
    tr = TurnResult()
    tr.tools = [
        ToolInvocation("settle_period", {}, {"ok": False, "error": "x"}),
        ToolInvocation("settle_period", {}, {"ok": True, "transfers": []}),
    ]
    assert tr.last_result("settle_period") == {"ok": True, "transfers": []}
    assert tr.last_result("missing") is None


def test_turn_result_all_results_returns_ok_in_order():
    tr = TurnResult()
    tr.tools = [
        ToolInvocation("propose_payment", {}, {"ok": True, "amount": 100}),
        ToolInvocation("propose_payment", {}, {"ok": False, "error": "x"}),
        ToolInvocation("propose_payment", {}, {"ok": True, "amount": 200}),
    ]
    assert tr.all_results("propose_payment") == [
        {"ok": True, "amount": 100},
        {"ok": True, "amount": 200},
    ]
    assert tr.all_results("missing") == []


def test_system_prompt_keeps_money_invariant_and_points_to_skills():
    p = build_system_prompt()
    # Money-safety invariant stays in the always-sent prompt itself.
    assert "KHÔNG BAO GIỜ tự tính toán" in p
    # Detailed procedures moved to workspace skills; the slim prompt points at them.
    assert "record-payment" in p
    assert "balances" in p
    # The removed monolithic guidance (old record_payment tool name) is gone.
    assert "record_payment" not in p


def test_render_prompt_baseline_unchanged():
    # No memory/history → identical to the pre-memory assembly.
    expected = f"{build_system_prompt(sender_name='An')}\n\n# Tin nhắn người dùng\nxin chào"
    assert _render_prompt("  xin chào  ", sender_name="An") == expected


def test_render_prompt_includes_sections_in_order():
    out = _render_prompt("ai trả", sender_name="An",
                         memory="- An hay trả", history="«An»: hôm qua 100k")
    assert "# Bộ nhớ dài hạn\n- An hay trả" in out
    assert "# Lịch sử hội thoại (gần đây)\n«An»: hôm qua 100k" in out
    # order: memory before history before the user message
    assert out.index("Bộ nhớ dài hạn") < out.index("Lịch sử hội thoại") < out.index("Tin nhắn người dùng")


# --- mocked run_turn ------------------------------------------------------- #

class _FakeAgent:
    def __init__(self, run):
        self._run = run

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, message, opts):
        return self._run


class _FakeAgents:
    def __init__(self, run):
        self._run = run

    async def create(self, options):
        return _FakeAgent(self._run)


class _FakeClient:
    def __init__(self, run):
        self.agents = _FakeAgents(run)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeRun:
    def __init__(self, messages):
        self._messages = messages

    async def messages(self):
        for m in self._messages:
            yield m

    def supports(self, _op):
        return False


def _text_msg(text):
    block = types.SimpleNamespace(type="text", text=text)
    return types.SimpleNamespace(type="assistant", message=types.SimpleNamespace(content=[block]))


def _tool_msg(tool_name, args, result):
    return types.SimpleNamespace(
        type="tool_call",
        status="completed",
        name="mcp",
        args={"toolName": tool_name, "args": args},
        result=result,
    )


@pytest.mark.asyncio
async def test_run_turn_collects_text_and_tool_results(monkeypatch, db):
    fake_run = _FakeRun([
        _tool_msg("settle_period", {"keyword": "since_last"}, {"ok": True, "transfers": [], "committed": False}),
        _text_msg("Mọi người đã cân bằng ✅"),
    ])

    monkeypatch.setattr(agent_mod, "_ensure_workspace", lambda: "/tmp/chiatienan-test")
    monkeypatch.setattr(
        "app.cursor_runner.resolve_cursor_api_key", lambda *a, **k: "k", raising=False
    )
    monkeypatch.setattr(
        "app.cursor_runner.resolve_model_selection", lambda *a, **k: types.SimpleNamespace(id="composer-2.5", params=None), raising=False
    )

    async def _fake_launch(AsyncClient, workspace, local):
        return _FakeClient(fake_run)

    monkeypatch.setattr(agent_mod, "_launch_bridge_resilient", _fake_launch)

    ctx = ToolContext(db=db, room_id=1, sender_member_id=1, sender_name="An")
    result = await run_turn("ai trả tuần này", ctx)

    assert result.error is None
    assert "cân bằng" in result.final_text
    settle = result.last_result("settle_period")
    assert settle is not None and settle["transfers"] == []


# --- emit contract ---------------------------------------------------------- #

@pytest.mark.asyncio
async def test_emit_receives_events_for_messages():
    # Exercise the same loop shape run_turn uses: translate + await emit.
    seen = []
    async def emit(ev): seen.append(ev)
    msgs = [
        SimpleNamespace(type="assistant",
                        message=SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])),
        SimpleNamespace(type="tool_call", call_id="c1", name="propose_meal",
                        status="completed", args={"total": 1}, result={"ok": True}),
    ]
    turn_id = "t1"
    for ev in agui.start(turn_id):
        await emit(ev)
    for m in msgs:
        for ev in agui.translate(m, turn_id):
            await emit(ev)
    for ev in agui.finish(turn_id):
        await emit(ev)
    kinds = [e["type"] for e in seen]
    assert kinds[0] == "agent.run.started" and kinds[-1] == "agent.run.finished"
    assert "agent.text.delta" in kinds and "agent.tool.result" in kinds


@pytest.mark.asyncio
async def test_run_turn_emits_finish_on_setup_failure(monkeypatch, db):
    """A setup-time failure (e.g. resolve_cursor_api_key raising because
    CURSOR_API_KEY is unset) must still reach agui.finish — otherwise a
    consumer sees agent.run.started with no terminal event and the timeline
    UI hangs forever."""

    def _boom(*a, **k):
        raise RuntimeError("CURSOR_API_KEY is not set")

    monkeypatch.setattr(agent_mod, "_ensure_workspace", lambda: "/tmp/chiatienan-test")
    monkeypatch.setattr(
        "app.cursor_runner.resolve_cursor_api_key", _boom, raising=False
    )

    room_id, member_ids = _seed_room(db, 1)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=member_ids[0], sender_name="An")

    seen = []

    async def emit(ev):
        seen.append(ev)

    result = await run_turn("ai trả tuần này", ctx, emit=emit)

    kinds = [e["type"] for e in seen]
    assert "agent.run.started" in kinds
    assert kinds[-1] in ("agent.run.finished", "agent.run.error")
    turn_ids = {e["turn_id"] for e in seen}
    assert len(turn_ids) == 1  # same turn_id on start and finish
    assert result.error is not None


# --- final answer assembly -------------------------------------------------- #

def test_pre_tool_narration_is_dropped_from_the_reply():
    """Production: 'Mình đọc quy trình ghi bữa ăn rồi xử lý câu hỏi của Emi.'
    was glued to the actual answer. Once tools have run, the block after the
    last one is the reply."""
    out = agent_mod._final_answer(
        ["Mình đọc quy trình ghi bữa rồi xử lý.", "Được — ghi theo từng người."], 1
    )
    assert out == "Được — ghi theo từng người."


def test_token_fragments_concatenate_without_separators():
    """Production 17:29: the reply reached the room as
    'V  ẫn  không  được  đâu  Kun' because every streamed token was joined with
    a blank line. Fragments are a stream, not messages — they concatenate."""
    tokens = ["Không", " —", " hiện", " không", " hỗ", " trợ", " xác", " nhận"]
    assert agent_mod._final_answer(tokens, 0) == "Không — hiện không hỗ trợ xác nhận"


def test_fragment_join_preserves_the_models_own_paragraph_breaks():
    parts = ["Đã ghi xong nhé.", "\n\n", "Cần sửa gì thì nhắn mình."]
    assert agent_mod._final_answer(parts, 0) == "Đã ghi xong nhé.\n\nCần sửa gì thì nhắn mình."


def test_glued_narration_is_dropped_when_it_arrives_in_one_fragment():
    """Production 17:23 stored one fragment holding both the scaffolding and the
    answer: '…mình đọc skill phù hợp rồi xử lý.Mình **không xác nhận qua chat**…'
    — answer_from cannot split that, so the seam is where we cut."""
    glued = (
        "Emi muốn xác nhận đề xuất đang treo — mình đọc skill phù hợp rồi xử lý."
        "Mình **không xác nhận qua chat** được — đề xuất #101 cần Emi bấm "
        "**Xác nhận** (hoặc Huỷ) trên thẻ nháp."
    )
    assert agent_mod._final_answer([glued], 0) == (
        "Mình **không xác nhận qua chat** được — đề xuất #101 cần Emi bấm "
        "**Xác nhận** (hoặc Huỷ) trên thẻ nháp."
    )


def test_several_stacked_narrations_are_all_dropped():
    """Production 13:20: three plan sentences stacked ahead of the answer."""
    glued = (
        "Mình sẽ đọc quy trình ghi bữa và lấy giá từng món từ ảnh hoá đơn."
        "Đã thấy lần trước đọc hoá đơn — mình lấy lại ảnh và schema để ghi theo món."
        "Mình dùng giá món đã đọc từ hoá đơn Grab và đề xuất bữa theo từng người."
        "Giá món trên ảnh mình đọc được:"
    )
    assert agent_mod._final_answer([glued], 0) == "Giá món trên ảnh mình đọc được:"


def test_an_answer_that_mentions_the_tools_is_not_mistaken_for_narration():
    """'công cụ' and 'quy tắc' show up in real answers — only skill/process
    reading phrasings are scaffolding."""
    real = (
        "Cộng các món = **414.200đ**, trong khi Emi trả **324.200đ** (có giảm/ship) "
        "— công cụ không cho ghi thẳng giá món vì vượt tổng."
    )
    assert agent_mod._final_answer([real], 0) == real


def test_a_seam_between_two_real_sentences_only_gains_a_space():
    glued = "Đã ghi #6 — Grab Food.Cần sửa gì thì nhắn mình."
    assert agent_mod._final_answer([glued], 0) == (
        "Đã ghi #6 — Grab Food. Cần sửa gì thì nhắn mình."
    )


def test_narration_survives_when_it_is_the_only_text():
    """Better a little narration than an empty bubble."""
    assert agent_mod._final_answer(["Mình đang xem thử."], 1) == "Mình đang xem thử."


def test_blank_trailing_block_falls_back_to_what_there_is():
    assert agent_mod._final_answer(["Có 3 bữa chưa chốt.", "   "], 1) == "Có 3 bữa chưa chốt."


def test_no_text_at_all_is_empty():
    assert agent_mod._final_answer([], 0) == ""
