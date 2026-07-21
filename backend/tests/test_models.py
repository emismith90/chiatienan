from app.db import Database
from app.models import Room, Member, RoomMessage, Session as UserSession


def _db():
    d = Database("sqlite://")  # in-memory
    d.create_all()
    return d


def test_room_member_message_roundtrip():
    d = _db()
    with d.session() as s:
        room = Room(name="Lunch", invite_token="tok123")
        s.add(room); s.flush()
        m = Member(room_id=room.id, display_name="An", nickname="an", pin="1234",
                   bank_code="VCB", account_number="001", account_holder="AN")
        s.add(m); s.flush()
        s.add(UserSession(member_id=m.id, token="sess1"))
        s.add(RoomMessage(room_id=room.id, author_member_id=m.id, kind="text", body="hi"))
        s.add(RoomMessage(room_id=room.id, author_member_id=None, kind="bot", body="pong",
                          attachments={"transfers": []}))
        s.flush()
        assert m.room_id == room.id
        msgs = s.query(RoomMessage).order_by(RoomMessage.id).all()
        assert [x.kind for x in msgs] == ["text", "bot"]
        assert msgs[1].author_member_id is None


def test_payment_model_persists():
    from datetime import date
    from app.models import Payment
    d = _db()
    with d.session() as s:
        room = Room(name="R", invite_token="tp")
        s.add(room); s.flush()
        a = Member(room_id=room.id, display_name="A", nickname="a", pin="1")
        b = Member(room_id=room.id, display_name="B", nickname="b", pin="2")
        s.add_all([a, b]); s.flush()
        p = Payment(room_id=room.id, from_member_id=a.id, to_member_id=b.id,
                    amount=125_000, occurred_on=date(2026, 7, 21))
        s.add(p); s.flush()
        assert p.id > 0
        assert p.voided is False
