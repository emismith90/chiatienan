from app import chat
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
