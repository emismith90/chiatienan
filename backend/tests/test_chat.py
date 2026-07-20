import app.agent as agent_mod
from app import chat
from app.agent import TurnResult
from app.db import Database
from app.models import Room, Member


def test_mentions_bot():
    assert chat.mentions_bot("@bot ai trả tuần này")
    assert chat.mentions_bot("hey @Bot log 100k")
    assert not chat.mentions_bot("just chatting")


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


def test_render_bot_attachments_settlement_wins_over_meal():
    fake = _FakeResult({
        "settle_period": {"ok": True, "transfers": []},
        "record_meal": {"ok": True, "meal_id": 1},
    })
    assert chat.render_bot_attachments(fake) == {"type": "settlement", "ok": True, "transfers": []}


def test_render_bot_attachments_meal_only():
    fake = _FakeResult({"record_meal": {"ok": True, "meal_id": 1}})
    assert chat.render_bot_attachments(fake) == {"type": "meal", "ok": True, "meal_id": 1}


def test_render_bot_attachments_neither():
    fake = _FakeResult({})
    assert chat.render_bot_attachments(fake) is None


# --- run_bot_turn error-path body -------------------------------------------- #

async def test_run_bot_turn_posts_error_body_on_agent_error(monkeypatch, db):
    with db.session() as s:
        r = Room(name="A", invite_token="t"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="An", nickname="an", pin="1"); s.add(m); s.flush()
        room_id, member_id = r.id, m.id

    async def _fake_run_turn(user_text, ctx, images=None):
        return TurnResult(final_text="", error="boom")

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)

    msg = await chat.run_bot_turn(db, room_id, member_id, "An", "@bot ai trả tuần này")

    assert msg.kind == "bot"
    assert "boom" in msg.body
    assert "⚠️" in msg.body
