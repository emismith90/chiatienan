"""chiatienan's kernos adapters are delegation; these tests pin that they delegate
to the right thing with the right arguments."""
import pytest
from datetime import timedelta

from app import chat
from app import memory as mem
from app.clock import now_ict
from app.hostadapters import build_adapters
from kernos.adapters import HostAdapters
from tests.test_ledger import _seed_room


def test_build_adapters_returns_a_full_set(db):
    a = build_adapters(db)
    assert isinstance(a, HostAdapters) and a.cards and a.completion and a.knowledge


def test_history_render_matches_build_history_and_images_carry(db):
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        chat.post_message(s, room_id, m[0], "bill", attachments={"images": [{"data": "x"}]})
        chat.post_message(s, room_id, None, "ok", kind="bot")
        ask = chat.post_message(s, room_id, m[1], "@phoenix log").id
    a = build_adapters(db)
    with db.session() as s:
        expected = chat.build_history(s, room_id, watermark=0, before_id=ask, limit=200)
    assert a.history.render(str(room_id), bot_label="ignored", since_id=0, limit=200, before_id=ask) == expected
    assert "phoenix: ok" in expected
    assert a.history.recent_images(str(room_id), before_id=ask) == [{"data": "x"}]


def test_aged_renders_only_messages_older_than_the_cutoff(db, monkeypatch, tmp_path):
    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path)
    room_id, m = _seed_room(db, 1)
    with db.session() as s:
        old = chat.post_message(s, room_id, m[0], "cũ")
        chat.post_message(s, room_id, m[0], "mới")
        old.created_at = now_ict() - timedelta(weeks=20)
        s.flush()
        old_id = old.id
    a = build_adapters(db)
    rendered, through = a.history.aged(str(room_id), watermark=0, older_than=now_ict() - timedelta(weeks=10))
    assert "cũ" in rendered and "mới" not in rendered and through == old_id
    assert a.history.aged(str(room_id), watermark=old_id, older_than=now_ict() - timedelta(weeks=10)) == ("", None)


def test_memory_and_messages_and_cards_delegate(db, monkeypatch, tmp_path):
    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path)
    room_id, m = _seed_room(db, 2)
    a = build_adapters(db)
    sid = str(room_id)
    a.memory.append_summary(sid, summary_text="- x", through_id=3, through_at="2026-09-05T00:00", header="H")
    assert a.memory.watermark(sid) == 3 and "- x" in a.memory.load(sid) == mem.load_memory(room_id)
    reply = a.messages.post(sid, author=None, kind="bot", body="hi", attachments=None)
    assert a.messages.to_payload(reply)["body"] == "hi" and reply.kind == "bot"
    payload = {"payer_member_id": m[0], "member_participants": m, "guests": [], "bill_total": 100,
               "adjustments": [], "dish": None, "initiator": None, "note": None,
               "per_head_preview": 50, "raw_input": "x"}
    card, superseded = a.cards.create(sid, "expense_draft", payload)
    assert card.kind == "expense_draft" and superseded == []
    assert a.cards.get(sid, card.id).id == card.id and a.cards.get("999", card.id) is None
    pay, _ = a.cards.create(sid, "payment_draft", {"transfers": [{"from_member_id": m[0], "to_member_id": m[1], "amount": 5, "note": None}]})
    assert pay.kind == "payment_draft"


async def test_completion_is_looked_up_on_chat_at_call_time(monkeypatch, db):
    async def fake(rendered, *, kind="clear"):
        return f"{kind}:{rendered}"
    monkeypatch.setattr("app.chat.summarize_messages", fake, raising=False)
    assert await build_adapters(db).completion.complete("txt", kind="rollover") == "rollover:txt"
    assert build_adapters(db).clock.today() == now_ict().date()


def test_cards_pending_and_cancel_are_the_draft_store(db):
    from app import drafts, ledger
    room_id, m = _seed_room(db, 2)
    cards = build_adapters(db).cards
    card, _ = cards.create(str(room_id), "expense_draft", {
        "payer_member_id": m[0], "member_participants": m, "guests": [], "bill_total": 100_000,
        "adjustments": [], "per_head_preview": 50_000, "raw_input": "x"})
    assert [c.id for c in cards.pending(str(room_id))] == [card.id]
    assert cards.cancel(str(room_id), card.id).attachments["status"] == "cancelled"
    assert cards.pending(str(room_id)) == []
    with db.session() as s:
        assert drafts.list_pending_drafts(s, room_id) == []
    with pytest.raises(ledger.LedgerError):
        cards.cancel(str(room_id), card.id)
