from datetime import date, timedelta

import pytest

import app.agent as agent_mod
from app import chat
from app import memory as mem
from app.agent import ToolInvocation, TurnResult
from app.clock import now_ict
from app.db import Database
from app.models import Meal, Room, Member
from tests.test_ledger import _seed_room


def test_mentions_bot():
    assert chat.mentions_bot("@phoenix ai trả tuần này")
    assert chat.mentions_bot("hey @Phoenix log 100k")
    # `@bot` predates the Phoenix rebrand and stays a working alias.
    assert chat.mentions_bot("@bot ai trả tuần này")
    assert chat.mentions_bot("hey @Bot log 100k")
    assert not chat.mentions_bot("just chatting")


def test_mentions_bot_ignores_email_like_text():
    assert not chat.mentions_bot("email me at user@bot.com")
    assert chat.mentions_bot("@phoenix hi")


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


def test_list_messages_page_windows_by_days_and_paginates(db):
    room_id, m = _seed_room(db, 1)
    with db.session() as s:
        old = chat.post_message(s, room_id, m[0], "old")
        recent = chat.post_message(s, room_id, m[0], "recent")
        old.created_at = now_ict() - timedelta(days=10)
        s.flush()
        old_id, recent_id = old.id, recent.id

    with db.session() as s:
        # Initial window: only the last 3 days, but older history still exists.
        msgs, has_more = chat.list_messages_page(s, room_id, days=3)
        assert [x["id"] for x in msgs] == [recent_id]
        assert has_more is True

        # Load earlier: page below the recent window, no time bound.
        older, has_more2 = chat.list_messages_page(s, room_id, before_id=recent_id)
        assert [x["id"] for x in older] == [old_id]
        assert has_more2 is False


def test_list_messages_page_limit_reports_has_more(db):
    room_id, m = _seed_room(db, 1)
    with db.session() as s:
        ids = [chat.post_message(s, room_id, m[0], f"m{i}").id for i in range(5)]
    with db.session() as s:
        msgs, has_more = chat.list_messages_page(s, room_id, days=3, limit=2)
        assert [x["id"] for x in msgs] == ids[-2:]  # most-recent 2, oldest→newest
        assert has_more is True


def test_list_messages_page_empty_window_still_reports_older(db):
    room_id, m = _seed_room(db, 1)
    with db.session() as s:
        old = chat.post_message(s, room_id, m[0], "old")
        old.created_at = now_ict() - timedelta(days=30)
        s.flush()
    with db.session() as s:
        msgs, has_more = chat.list_messages_page(s, room_id, days=3)
        assert msgs == []          # nothing in the last 3 days
        assert has_more is True    # but there is older history to pull in


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


def test_render_bot_attachments_dispatch():
    assert chat.render_bot_attachments(
        _FakeResult({"settle_period": {"type": "settle_blocked", "pending": []}})
    )["type"] == "settle_blocked"
    assert chat.render_bot_attachments(
        _FakeResult({"settle_period": {"transfers": []}})
    )["type"] == "settlement"


def test_payment_body_renders_from_dict():
    body = chat._payment_body({"transfers": [
        {"from": {"name": "An"}, "to": {"name": "Bình"}, "amount": 125000}]})
    assert "An" in body and "Bình" in body and "125,000" in body


def test_payment_body_renders_multiple_transfers():
    body = chat._payment_body({"transfers": [
        {"from": {"name": "An"}, "to": {"name": "Linh"}, "amount": 30000},
        {"from": {"name": "Bình"}, "to": {"name": "Linh"}, "amount": 20000}]})
    assert "An" in body and "Bình" in body and "Linh" in body
    assert "30,000" in body and "20,000" in body


def test_settle_blocked_body_lists_pending():
    body = chat._settle_blocked_body({
        "message": "Có 1 đề xuất chưa xác nhận — xác nhận hoặc huỷ trước khi chốt.",
        "pending": [{"draft_id": 7, "payer_name": "An", "bill_total": 400000, "participant_count": 4}],
    })
    assert "#7" in body and "An" in body and "400,000" in body


def test_settlement_body_calls_itself_a_running_total():
    """Nothing is recorded and no period closes, so a "Chốt kỳ" header was telling
    the room the books had been ruled off when `settlements` has always been empty."""
    body = chat._settlement_body({
        "period": {"from": None, "to": "2026-07-27"},
        "transfers": [{"from_name": "Kun", "to_name": "Emi", "amount": 54500, "note": None}],
    })
    assert body.startswith("Provisional through 2026-07-27:")
    assert "Chốt" not in body


def test_settlement_body_names_both_ends_of_a_bounded_period():
    body = chat._settlement_body({
        "period": {"from": "2026-07-20", "to": "2026-07-26"},
        "transfers": [],
        "message": "Mọi người đã cân bằng — không ai nợ ai trong kỳ này.",
    })
    assert body.startswith("Provisional 2026-07-20 → 2026-07-26:")
    assert "cân bằng" in body


def test_settlement_body_shows_the_transfer_memo():
    """The memo is the bank's addInfo and the thing people dispute ("sai nội dung
    chuyển khoản r"); it used to live only in the attachment."""
    body = chat._settlement_body({
        "period": {"from": None, "to": "2026-07-27"},
        "transfers": [{"from_name": "Giang Hoàng", "to_name": "Linh Nguyen",
                       "amount": 107000, "note": "Giang Hoang: T5 bun cha rua xe"}],
    })
    assert "107,000đ" in body
    assert "ref: Giang Hoang: T5 bun cha rua xe" in body


def test_settlement_body_without_a_memo_is_unchanged():
    body = chat._settlement_body({
        "period": {"from": None, "to": "2026-07-27"},
        "transfers": [{"from_name": "A", "to_name": "B", "amount": 1000, "note": None}],
    })
    assert body.endswith("A → B: 1,000đ")


def test_settle_blocked_body_says_how_to_clear_it():
    """Production: it listed the blocking draft and stopped, so four people asked
    the bot to close it instead of finding the card."""
    body = chat._settle_blocked_body({
        "message": "Có 1 đề xuất chưa xác nhận — xác nhận hoặc huỷ trước khi chốt.",
        "pending": [{"draft_id": 101, "payer_name": "Emi", "bill_total": 324000,
                     "participant_count": 6}],
    })
    assert "Confirm" in body and "Cancel" in body
    assert "huỷ đề xuất" in body


def test_settle_blocked_body_omits_the_hint_when_nothing_is_pending():
    body = chat._settle_blocked_body({"message": "Không có gì để chốt.", "pending": []})
    assert "huỷ đề xuất" not in body


# --- run_bot_turn error-path body -------------------------------------------- #

async def test_run_bot_turn_posts_error_body_on_agent_error(monkeypatch, db):
    with db.session() as s:
        r = Room(name="A", invite_token="t"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="An", nickname="an", pin="1"); s.add(m); s.flush()
        room_id, member_id = r.id, m.id

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(final_text="", error="boom")

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)

    msg = await chat.run_bot_turn(db, room_id, member_id, "An", "@phoenix ai trả tuần này")

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

    msg = await chat.run_bot_turn(db, room_id, member_id, "An", "@phoenix chốt kỳ")

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

    msg = await chat.run_bot_turn(db, room_id, member_id, "An", "@phoenix chốt kỳ")

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
        "occurred_on": None,
    }

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(
            final_text="ghi rồi nhé, mỗi người 1đ thôi",  # must be ignored entirely
            tools=[ToolInvocation(name="propose_meal", args={}, result=proposal_result)],
        )

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)

    msg = await chat.run_bot_turn(db, room_id, member_id, "An", "@phoenix ghi 300k An Bình")

    assert msg.kind == "expense_draft"
    assert msg.attachments["status"] == "pending"
    assert msg.attachments["bill_total"] == 300000
    assert msg.attachments["dish"] == "phở"
    assert msg.attachments["member_participants"] == [member_id, member2_id]
    assert msg.attachments["raw_input"] == "@phoenix ghi 300k An Bình"
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
        "occurred_on": None,
    }

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(
            turn_id="turn-abc123",
            tools=[ToolInvocation(name="propose_meal", args={}, result=proposal_result)],
        )

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)

    msg = await chat.run_bot_turn(db, room_id, member_id, "An", "@phoenix ghi 100k")

    assert msg.attachments["turn_id"] == "turn-abc123"


@pytest.mark.parametrize("text,expected", [
    ("/clear", True),
    ("  /clear  ", True),
    ("/CLEAR", True),
    ("@phoenix /clear", True),
    ("@phoenix   /clear", True),
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
        cur = chat.post_message(s, room_id, m[1], "@phoenix ai trả")            # excluded (before_id)
        out = chat.build_history(s, room_id, watermark=0, before_id=cur.id, limit=200)
    assert out == "«M1»: 840k cả nhóm\nphoenix: Đã ghi #1"


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


# --- bare replies to the bot's own question --------------------------------- #

def _room_with_two(db):
    with db.session() as s:
        r = Room(name="A", invite_token="tok-reply"); s.add(r); s.flush()
        emi = Member(room_id=r.id, display_name="Emi", nickname="emi", pin="1")
        kun = Member(room_id=r.id, display_name="Kun", nickname="kun", pin="2")
        s.add_all([emi, kun]); s.flush()
        return r.id, emi.id, kun.id


def test_bare_reply_to_a_bot_question_reaches_the_bot(db):
    """Production: "1", "2", "b" and "tôi đã trả tiền Emi" were all dropped for
    lacking @phoenix, then retyped with one seconds later."""
    room_id, emi, _kun = _room_with_two(db)
    with db.session() as s:
        chat.post_message(s, room_id, emi, "@phoenix giang paid Linh")
        chat.post_message(s, room_id, None, "Emi muốn ghi kiểu nào?", kind="bot")
        answer = chat.post_message(s, room_id, emi, "1")
        assert chat.replies_to_bot_question(s, room_id, emi, before_id=answer.id)


def test_a_numbered_choice_counts_as_a_question(db):
    room_id, emi, _kun = _room_with_two(db)
    with db.session() as s:
        chat.post_message(s, room_id, emi, "@phoenix ghi theo món")
        chat.post_message(s, room_id, None, "Chọn cách ghi:\n1. Chia đều\n2. Theo món",
                          kind="bot")
        answer = chat.post_message(s, room_id, emi, "2")
        assert chat.replies_to_bot_question(s, room_id, emi, before_id=answer.id)


def test_someone_else_answering_does_not_wake_the_bot(db):
    """The bot asked Emi. Kun talking is Kun talking."""
    room_id, emi, kun = _room_with_two(db)
    with db.session() as s:
        chat.post_message(s, room_id, emi, "@phoenix giang paid Linh")
        chat.post_message(s, room_id, None, "Emi muốn ghi kiểu nào?", kind="bot")
        answer = chat.post_message(s, room_id, kun, "1")
        assert not chat.replies_to_bot_question(s, room_id, kun, before_id=answer.id)


def test_a_statement_is_not_an_open_question(db):
    room_id, emi, _kun = _room_with_two(db)
    with db.session() as s:
        chat.post_message(s, room_id, emi, "@phoenix số dư")
        chat.post_message(s, room_id, None, "💸 Đã ghi: Emi trả Linh 61,000đ", kind="bot")
        answer = chat.post_message(s, room_id, emi, "ok cảm ơn")
        assert not chat.replies_to_bot_question(s, room_id, emi, before_id=answer.id)


def test_human_chatter_after_a_bot_question_does_not_wake_it(db):
    """Only the message *immediately* before counts — otherwise the bot joins a
    conversation that moved on without it."""
    room_id, emi, kun = _room_with_two(db)
    with db.session() as s:
        chat.post_message(s, room_id, emi, "@phoenix giang paid Linh")
        chat.post_message(s, room_id, None, "Emi muốn ghi kiểu nào?", kind="bot")
        chat.post_message(s, room_id, kun, "trưa nay ăn gì mọi người")
        answer = chat.post_message(s, room_id, emi, "bún chả đi")
        assert not chat.replies_to_bot_question(s, room_id, emi, before_id=answer.id)


# --- the money-safety instrument must actually fire ------------------------- #

async def test_unbacked_money_in_a_reply_is_logged(monkeypatch, db, caplog):
    """app.moneyguard is unit-tested, but a detector wired up wrong stays silent
    forever — and silence reads exactly like a clean bill of health. This asserts
    the warning reaches the log through run_bot_turn."""
    with db.session() as s:
        r = Room(name="A", invite_token="t-guard"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="An", nickname="an-guard", pin="1"); s.add(m); s.flush()
        room_id, member_id = r.id, m.id

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        # A balance table typed by the model, with no tool behind it.
        return TurnResult(final_text="Bùi Trang −75,000đ · Giang Hoàng +89,000đ", turn_id="t-abc")

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)

    with caplog.at_level("WARNING", logger="chiatienan"):
        msg = await chat.run_bot_turn(db, room_id, member_id, "An", "@phoenix tóm tắt số dư")

    assert "unbacked money in reply" in caplog.text
    assert "75000" in caplog.text and "89000" in caplog.text
    assert "t-abc" in caplog.text
    # images=N is the triage key: bill-photo prices are unattributable by
    # construction, a bash-computed split is not.
    assert "images=0" in caplog.text
    # Reporting only — the reply is still delivered.
    assert "75,000đ" in msg.body


async def test_a_tool_backed_reply_logs_nothing(monkeypatch, db, caplog):
    with db.session() as s:
        r = Room(name="A", invite_token="t-guard2"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="An", nickname="an-guard2", pin="1"); s.add(m); s.flush()
        room_id, member_id = r.id, m.id

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        tr = TurnResult(final_text="Đã ghi 305,000đ nhé.", turn_id="t-def")
        tr.tools = [ToolInvocation("propose_meal", {"total": 305_000}, {"ok": True})]
        return tr

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)

    with caplog.at_level("WARNING", logger="chiatienan"):
        await chat.run_bot_turn(db, room_id, member_id, "An", "@phoenix bún bò 305k")

    assert "unbacked money" not in caplog.text


async def test_a_fabricated_commit_never_reaches_the_room(monkeypatch, db, caplog):
    """Room 3, 2026-08-14: `tools=0`, and the room read a forged `_meal_body` as
    a real ledger entry for three days. The reply the room gets must say the
    ledger is untouched, and must not carry the invented numbers."""
    with db.session() as s:
        r = Room(name="A", invite_token="t-forge"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="Emi", nickname="emi-forge", pin="1"); s.add(m); s.flush()
        room_id, member_id = r.id, m.id

    forgery = (
        "Đã ghi #14 — Texas Chicken: Bạch Mai trả tổng 793,760đ • Emi 132,293đ, "
        "Nhím 132,293đ, Giang Hoàng 132,293đ"
    )

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(final_text=forgery, turn_id="t-forge1")

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)

    with caplog.at_level("ERROR", logger="chiatienan"):
        msg = await chat.run_bot_turn(db, room_id, member_id, "Emi", "@phoenix log this for all")

    assert "Đã ghi #14" not in msg.body
    assert "793,760" not in msg.body and "132,293" not in msg.body
    assert "not recorded" in msg.body.lower()
    # And it has to be findable afterwards — the forged text only survives in the log.
    assert "suppressed fabricated commit" in caplog.text
    assert "t-forge1" in caplog.text and "Texas Chicken" in caplog.text


async def test_an_honest_reply_about_a_past_meal_still_gets_through(monkeypatch, db):
    """The guard's blast radius: 'đã ghi' about a meal the conversation already
    records is a normal answer, not a forgery."""
    with db.session() as s:
        r = Room(name="A", invite_token="t-forge2"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="Emi", nickname="emi-forge2", pin="1"); s.add(m); s.flush()
        room_id, member_id = r.id, m.id
        chat.post_message(s, room_id, None, "Đã ghi #13 — bún cá: Giang trả tổng 175,000đ", kind="bot")

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(final_text="Rồi nhé, bữa bún cá mình đã ghi rồi — tổng 175,000đ.",
                          turn_id="t-forge2")

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)
    msg = await chat.run_bot_turn(db, room_id, member_id, "Emi", "@phoenix ghi bún cá chưa")
    assert "175,000đ" in msg.body


async def test_the_forgery_stays_blocked_when_asked_a_fourth_time(monkeypatch, db):
    """Room 3 asked four times over 44 minutes and got the same 157 characters
    back every time. The first block must not depend on the room being clean —
    once earlier forgeries sit in the history, their amounts read as 'backed',
    so only the ledger check keeps the later ones out."""
    forgery = (
        "Đã ghi #14 — Texas Chicken: Bạch Mai trả tổng 793,760đ • Emi 132,293đ, "
        "Nhím 132,293đ, Giang Hoàng 132,293đ"
    )
    with db.session() as s:
        r = Room(name="A", invite_token="t-loop"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="Emi", nickname="emi-loop", pin="1"); s.add(m); s.flush()
        room_id, member_id = r.id, m.id
        # The three earlier forgeries the room already believes.
        for _ in range(3):
            chat.post_message(s, room_id, None, forgery, kind="bot")

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(final_text=forgery, turn_id="t-loop4")

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)
    msg = await chat.run_bot_turn(
        db, room_id, member_id, "Emi",
        "@phoenix nay ăn Texas chicken hết 793.760, chia đều cả 7 người")

    assert "Đã ghi #14" not in msg.body
    assert "not recorded" in msg.body.lower()


async def test_a_confirmation_naming_a_real_meal_is_left_alone(monkeypatch, db):
    """The ledger check must clear what the ledger supports, or the bot loses the
    ability to answer "did you record it?"."""
    with db.session() as s:
        r = Room(name="A", invite_token="t-real"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="Emi", nickname="emi-real", pin="1"); s.add(m); s.flush()
        room_id, member_id = r.id, m.id
        meal = Meal(room_id=room_id, occurred_on=date(2026, 8, 13), payer_member_id=m.id,
                    total_amount=175_000, dish="bún cá")
        s.add(meal); s.flush()
        meal_id = meal.id
        chat.post_message(s, room_id, None,
                          f"Đã ghi #{meal_id} — bún cá: Emi trả tổng 175,000đ", kind="bot")

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(final_text=f"Rồi nhé — Đã ghi #{meal_id}, tổng 175,000đ.", turn_id="t-real1")

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)
    msg = await chat.run_bot_turn(db, room_id, member_id, "Emi", "@phoenix ghi bún cá chưa")
    assert f"Đã ghi #{meal_id}" in msg.body
