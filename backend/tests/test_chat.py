import app.agent as agent_mod
from app import chat
from app.agent import ToolInvocation, TurnResult
from app.db import Database
from app.models import Room, Member


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

    async def _fake_run_turn(user_text, ctx, images=None, emit=None):
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

    async def _fake_run_turn(user_text, ctx, images=None, emit=None):
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

    async def _fake_run_turn(user_text, ctx, images=None, emit=None):
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

    async def _fake_run_turn(user_text, ctx, images=None, emit=None):
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
