from datetime import timedelta

import pytest

import app.agent as agent_mod
from app import chat
from app import memory as mem
from app.agent import ToolInvocation, TurnResult
from app.clock import now_ict
from app.db import Database
from app.models import Room, Member
from tests.test_ledger import _seed_room


def test_mentions_bot():
    assert chat.mentions_bot("@bot ai trả tuần này")
    assert chat.mentions_bot("hey @Bot log 100k")
    assert not chat.mentions_bot("just chatting")


def test_mentions_bot_ignores_email_like_text():
    assert not chat.mentions_bot("email me at user@bot.com")
    assert chat.mentions_bot("@bot hi")


def test_post_and_list_since():
    d = Database("sqlite://"); d.create_all()
    with d.session() as s:
        r = Room(name="A", invite_token="t"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="An", nickname="an", pin="1"); s.add(m); s.flush()
        a = chat.post_message(s, r.id, m.id, "hi")
        b = chat.post_message(s, r.id, m.id, "again")
        rows = chat.list_messages(s, r.id, since_id=a.id)
        assert [x["id"] for x in rows] == [b.id]
        assert rows[0]["author"]["nickname"] == "an"


# --- render_bot_attachments precedence -------------------------------------- #

class _FakeResult:
    """Minimal stand-in for TurnResult exposing only `.last_result(name)`."""

    def __init__(self, results: dict):
        self._results = results

    def last_result(self, name):
        return self._results.get(name)


def test_render_bot_attachments_settlement_ignores_unrelated_results():
    # A stray non-settlement tool result in the same turn (e.g. a leftover
    # propose_meal call) must never leak into the rendered attachment —
    # meals flow only through drafts now, never through this function.
    fake = _FakeResult({
        "settle_period": {"ok": True, "transfers": []},
        "propose_meal": {"ok": True, "bill_total": 1},
    })
    assert chat.render_bot_attachments(fake) == {"type": "settlement", "ok": True, "transfers": []}


def test_render_bot_attachments_ignores_non_settlement_results():
    # meals are proposed via propose_meal and handled by run_bot_turn's draft
    # path before render_bot_attachments is ever consulted; this function
    # itself renders nothing for a non-settlement result.
    fake = _FakeResult({"propose_meal": {"ok": True, "bill_total": 1}})
    assert chat.render_bot_attachments(fake) is None


def test_render_bot_attachments_neither():
    fake = _FakeResult({})
    assert chat.render_bot_attachments(fake) is None


# --- run_bot_turn error-path body -------------------------------------------- #

async def test_run_bot_turn_posts_error_body_on_agent_error(monkeypatch, db):
    with db.session() as s:
        r = Room(name="A", invite_token="t"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="An", nickname="an", pin="1"); s.add(m); s.flush()
        room_id, member_id = r.id, m.id

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(final_text="", error="boom")

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)

    msg = await chat.run_bot_turn(db, room_id, member_id, "An", "@bot ai trả tuần này")

    assert msg.kind == "bot"
    assert "boom" in msg.body
    assert "⚠️" in msg.body


# --- run_bot_turn money-safety: body built from tool result, not final_text - #

async def test_run_bot_turn_settlement_body_uses_tool_amounts(monkeypatch, db):
    with db.session() as s:
        r = Room(name="A", invite_token="t-settle"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="An", nickname="an-settle", pin="1"); s.add(m); s.flush()
        room_id, member_id = r.id, m.id

    settle_result = {
        "ok": True,
        "period": {"from": "2026-07-01", "to": "2026-07-20"},
        "transfers": [
            {"from_id": 1, "from_name": "Bình", "to_id": 2, "to_name": "An",
             "amount": 123456, "note": "x", "qr_url": None},
        ],
        "warnings": [],
        "committed": False,
    }

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(
            final_text="Đã chốt xong nhé, Bình nợ An 999đ thôi",  # deliberately wrong
            tools=[ToolInvocation(name="settle_period", args={}, result=settle_result)],
        )

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)

    msg = await chat.run_bot_turn(db, room_id, member_id, "An", "@bot chốt kỳ")

    assert msg.kind == "bot"
    assert "Bình" in msg.body and "An" in msg.body
    assert "123,456đ" in msg.body
    assert "999" not in msg.body


async def test_run_bot_turn_settlement_body_no_transfers_uses_tool_message(monkeypatch, db):
    with db.session() as s:
        r = Room(name="A", invite_token="t-settle2"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="An", nickname="an-settle2", pin="1"); s.add(m); s.flush()
        room_id, member_id = r.id, m.id

    settle_result = {
        "ok": True,
        "period": {"from": None, "to": "2026-07-20"},
        "transfers": [],
        "committed": False,
        "message": "Không có gì để chốt trong kỳ này (mọi người đã cân bằng).",
    }

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(
            final_text="mọi người xong hết rồi",
            tools=[ToolInvocation(name="settle_period", args={}, result=settle_result)],
        )

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)

    msg = await chat.run_bot_turn(db, room_id, member_id, "An", "@bot chốt kỳ")

    assert "Không có gì để chốt" in msg.body


async def test_run_bot_turn_meal_proposal_creates_pending_draft(monkeypatch, db):
    """After Task 5, a meal turn never writes/replies immediately: the agent
    only proposes (``propose_meal``), and run_bot_turn turns that proposal
    into a pending, editable expense_draft message — the LLM's `final_text`
    is discarded entirely for this path (money-safety, design D3)."""
    with db.session() as s:
        r = Room(name="A", invite_token="t-meal"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="An", nickname="an-meal", pin="1"); s.add(m); s.flush()
        m2 = Member(room_id=r.id, display_name="Bình", nickname="binh-meal", pin="1"); s.add(m2); s.flush()
        room_id, member_id, member2_id = r.id, m.id, m2.id

    proposal_result = {
        "ok": True,
        "type": "expense_draft",
        "payer_member_id": member_id,
        "member_participants": [member_id, member2_id],
        "guests": [],
        "bill_total": 300000,
        "adjustments": [],
        "dish": "phở",
        "initiator": None,
        "note": None,
        "per_head_preview": 150000,
    }

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(
            final_text="ghi rồi nhé, mỗi người 1đ thôi",  # must be ignored entirely
            tools=[ToolInvocation(name="propose_meal", args={}, result=proposal_result)],
        )

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)

    msg = await chat.run_bot_turn(db, room_id, member_id, "An", "@bot ghi 300k An Bình")

    assert msg.kind == "expense_draft"
    assert msg.attachments["status"] == "pending"
    assert msg.attachments["bill_total"] == 300000
    assert msg.attachments["dish"] == "phở"
    assert msg.attachments["member_participants"] == [member_id, member2_id]
    assert msg.attachments["raw_input"] == "@bot ghi 300k An Bình"
    assert msg.body == ""  # draft cards render from attachments, never LLM prose


async def test_run_bot_turn_meal_proposal_carries_turn_id(monkeypatch, db):
    """Fix 10: the draft attachment carries the turn's id, so the frontend can
    attach that turn's agent.* timeline to the draft card."""
    with db.session() as s:
        r = Room(name="A", invite_token="t-turnid"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="An", nickname="an-turnid", pin="1"); s.add(m); s.flush()
        m2 = Member(room_id=r.id, display_name="Bình", nickname="binh-turnid", pin="1"); s.add(m2); s.flush()
        room_id, member_id, member2_id = r.id, m.id, m2.id

    proposal_result = {
        "ok": True, "type": "expense_draft",
        "payer_member_id": member_id, "member_participants": [member_id, member2_id],
        "guests": [], "bill_total": 100000, "adjustments": [], "dish": None,
        "initiator": None, "note": None, "per_head_preview": 50000,
    }

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(
            turn_id="turn-abc123",
            tools=[ToolInvocation(name="propose_meal", args={}, result=proposal_result)],
        )

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)

    msg = await chat.run_bot_turn(db, room_id, member_id, "An", "@bot ghi 100k")

    assert msg.attachments["turn_id"] == "turn-abc123"


@pytest.mark.parametrize("text,expected", [
    ("/clear", True),
    ("  /clear  ", True),
    ("/CLEAR", True),
    ("@bot /clear", True),
    ("@bot   /clear", True),
    ("/cleared", False),
    ("/clear now", False),
    ("clear", False),
    ("", False),
    ("please /clear", False),
])
def test_is_clear_command(text, expected):
    assert chat.is_clear_command(text) is expected


def test_build_history_renders_window(db):
    room_id, m = _seed_room(db, 2)  # M1, M2
    with db.session() as s:
        a = chat.post_message(s, room_id, m[0], "840k cả nhóm")
        b = chat.post_message(s, room_id, None, "Đã ghi #1", kind="bot")
        chat.post_message(s, room_id, None, "reset", kind="context_reset")  # skipped
        cur = chat.post_message(s, room_id, m[1], "@bot ai trả")            # excluded (before_id)
        out = chat.build_history(s, room_id, watermark=0, before_id=cur.id, limit=200)
    assert out == "«M1»: 840k cả nhóm\nchiatienan: Đã ghi #1"


def test_build_history_respects_watermark_and_limit(db):
    room_id, m = _seed_room(db, 1)
    with db.session() as s:
        first = chat.post_message(s, room_id, m[0], "một")
        chat.post_message(s, room_id, m[0], "hai")
        chat.post_message(s, room_id, m[0], "ba")
        # watermark drops "một"; limit keeps the most recent 1 -> "ba"
        out = chat.build_history(s, room_id, watermark=first.id, before_id=None, limit=1)
    assert out == "«M1»: ba"


def test_build_history_empty_returns_blank(db):
    room_id, _ = _seed_room(db, 1)
    with db.session() as s:
        assert chat.build_history(s, room_id, watermark=0, before_id=None, limit=200) == ""


# --- clear_context / _maybe_rollover ----------------------------------------- #


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_clear_context_summarizes_and_resets(db, ws, monkeypatch):
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        chat.post_message(s, room_id, m[0], "840k cả nhóm")
        chat.post_message(s, room_id, None, "Đã ghi #1", kind="bot")
        clear_line = chat.post_message(s, room_id, m[1], "/clear")
        clear_id = clear_line.id

    seen = {}

    async def fake_summarize(rendered, *, kind="clear"):
        seen["rendered"] = rendered
        seen["kind"] = kind
        return "- An trả 840k cho cả nhóm"

    monkeypatch.setattr("app.chat.summarize_messages", fake_summarize, raising=False)

    div = await chat.clear_context(db, room_id, up_to_id=clear_id)

    assert div.kind == "context_reset"
    assert seen["kind"] == "clear"
    # the /clear line itself is excluded from the summarized text
    assert "840k cả nhóm" in seen["rendered"] and "/clear" not in seen["rendered"]
    assert "An trả 840k" in mem.load_memory(room_id)
    assert mem.read_watermark(room_id) == clear_id


@pytest.mark.asyncio
async def test_clear_context_posts_divider_even_when_summary_blank(db, ws, monkeypatch):
    room_id, m = _seed_room(db, 1)
    with db.session() as s:
        chat.post_message(s, room_id, m[0], "một")
        clear_line = chat.post_message(s, room_id, m[0], "/clear")
        clear_id = clear_line.id

    async def blank_summarize(rendered, *, kind="clear"):
        return ""

    monkeypatch.setattr("app.chat.summarize_messages", blank_summarize, raising=False)

    div = await chat.clear_context(db, room_id, up_to_id=clear_id)
    assert div.kind == "context_reset"
    assert mem.load_memory(room_id) == ""          # nothing appended
    assert mem.read_watermark(room_id) == clear_id  # but window still reset


@pytest.mark.asyncio
async def test_maybe_rollover_folds_aged_messages(db, ws, monkeypatch):
    room_id, m = _seed_room(db, 1)
    with db.session() as s:
        old1 = chat.post_message(s, room_id, m[0], "cũ 1")
        old2 = chat.post_message(s, room_id, m[0], "cũ 2")
        recent = chat.post_message(s, room_id, m[0], "mới")
        old1.created_at = now_ict() - timedelta(weeks=20)
        old2.created_at = now_ict() - timedelta(weeks=20)
        s.flush()
        aged_id = old2.id
        recent_id = recent.id

    calls = {}

    async def fake_summarize(rendered, *, kind="clear"):
        calls["kind"] = kind
        calls["rendered"] = rendered
        return "- tóm tắt cũ"

    monkeypatch.setattr("app.chat.summarize_messages", fake_summarize, raising=False)

    await chat._maybe_rollover(db, room_id)

    assert calls["kind"] == "rollover"
    assert "cũ 1" in calls["rendered"] and "mới" not in calls["rendered"]
    assert mem.read_watermark(room_id) == aged_id
    assert "tóm tắt cũ" in mem.load_memory(room_id)
    # the recent message survives in the window
    with db.session() as s:
        hist = chat.build_history(s, room_id, watermark=mem.read_watermark(room_id),
                                  before_id=None, limit=200)
    assert "mới" in hist and "cũ 1" not in hist
