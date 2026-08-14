"""The bill someone pasted a message ago has to reach the vision turn.

Nobody types "@phoenix" in the same message as the screenshot. In production the
bill went up, then "@phoenix log đi" went up, and the bot — which only ever received
the images attached to the mentioning message — asked for a total that was on
screen. Everything downstream (asking for per-dish prices, four rounds of
"gửi số tiền từng người") followed from that.
"""
from datetime import timedelta

import app.agent as agent_mod
from app import chat, memory
from app.clock import now_ict
from app.models import Member, Room, RoomMessage

IMG = {"data": "aGVsbG8=", "mimeType": "image/png"}


def _room(db, token):
    with db.session() as s:
        r = Room(name="A", invite_token=token); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="Emi", nickname=f"emi-{token}", pin="1")
        s.add(m); s.flush()
        return r.id, m.id


def _post(db, room_id, member_id, body, images=None, minutes_ago=0):
    with db.session() as s:
        m = chat.post_message(s, room_id, member_id, body,
                              attachments={"images": images} if images else None)
        if minutes_ago:
            m.created_at = now_ict() - timedelta(minutes=minutes_ago)
        s.flush()
        return m.id


def test_image_from_the_previous_message_is_found(db):
    room_id, member_id = _room(db, "t-prev")
    _post(db, room_id, member_id, "emi ăn bò, nhím gà", images=[IMG])
    ask = _post(db, room_id, member_id, "@phoenix log đi")
    with db.session() as s:
        assert chat.recent_images(s, room_id, before_id=ask) == [IMG]


def test_only_the_newest_bill_is_carried_forward(db):
    room_id, member_id = _room(db, "t-newest")
    _post(db, room_id, member_id, "bill hôm qua", images=[IMG])
    newer = {"data": "d29ybGQ=", "mimeType": "image/jpeg"}
    _post(db, room_id, member_id, "bill hôm nay", images=[newer])
    ask = _post(db, room_id, member_id, "@phoenix log đi")
    with db.session() as s:
        assert chat.recent_images(s, room_id, before_id=ask) == [newer]


def test_a_stale_image_is_not_dragged_into_an_unrelated_turn(db):
    room_id, member_id = _room(db, "t-stale")
    _post(db, room_id, member_id, "bill tuần trước", images=[IMG], minutes_ago=60 * 24)
    ask = _post(db, room_id, member_id, "@phoenix số dư")
    with db.session() as s:
        assert chat.recent_images(s, room_id, before_id=ask) == []


def test_an_image_far_up_the_scrollback_is_not_carried_forward(db):
    room_id, member_id = _room(db, "t-far")
    _post(db, room_id, member_id, "bill", images=[IMG])
    for i in range(15):
        _post(db, room_id, member_id, f"chat {i}")
    ask = _post(db, room_id, member_id, "@phoenix số dư")
    with db.session() as s:
        assert chat.recent_images(s, room_id, before_id=ask) == []


def test_no_images_in_the_room_is_just_empty(db):
    room_id, member_id = _room(db, "t-none")
    ask = _post(db, room_id, member_id, "@phoenix số dư")
    with db.session() as s:
        assert chat.recent_images(s, room_id, before_id=ask) == []


async def test_run_bot_turn_feeds_the_previous_message_s_bill_to_the_agent(monkeypatch, db):
    room_id, member_id = _room(db, "t-turn")
    _post(db, room_id, member_id, "emi ăn bò, nhím gà", images=[IMG])
    ask = _post(db, room_id, member_id, "@phoenix log đi")
    seen = {}

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        seen["images"], seen["history"] = images, history
        from app.agent import TurnResult
        return TurnResult(final_text="ok")

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)
    await chat.run_bot_turn(db, room_id, member_id, "Emi", "@phoenix log đi", before_id=ask)

    assert seen["images"] == [IMG]
    # …and the text history says an image was there, so the model knows to look.
    assert "[ảnh: 1]" in seen["history"]


async def test_images_on_the_mentioning_message_still_win(monkeypatch, db):
    room_id, member_id = _room(db, "t-own")
    _post(db, room_id, member_id, "bill cũ", images=[IMG])
    own = {"data": "bmV3", "mimeType": "image/png"}
    ask = _post(db, room_id, member_id, "@phoenix log đi", images=[own])
    seen = {}

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        seen["images"] = images
        from app.agent import TurnResult
        return TurnResult(final_text="ok")

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)
    await chat.run_bot_turn(db, room_id, member_id, "Emi", "@phoenix log đi",
                            images=[own], before_id=ask)
    assert seen["images"] == [own]


def test_history_marks_images_without_dumping_base64(db):
    room_id, member_id = _room(db, "t-hist")
    _post(db, room_id, member_id, "hoá đơn đây", images=[IMG, IMG])
    with db.session() as s:
        history = chat.build_history(s, room_id, watermark=memory.read_watermark(room_id))
    assert "[ảnh: 2]" in history
    assert IMG["data"] not in history


def test_lookback_can_be_switched_off(db, monkeypatch):
    room_id, member_id = _room(db, "t-off")
    _post(db, room_id, member_id, "bill", images=[IMG])
    ask = _post(db, room_id, member_id, "@phoenix log đi")
    with db.session() as s:
        assert chat.recent_images(s, room_id, before_id=ask, max_messages=0) == []
