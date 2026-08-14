from datetime import date

import pytest

from app import memos, observations as obs
from app.db import Database
from app.models import Member, Place, Room, RoomMessage
from app.tools import ToolContext, build_tools


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    from app import memory as mem
    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def env():
    db = Database("sqlite:///:memory:")
    db.create_all()
    with db.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.flush()
        s.add(Member(id=1, room_id=1, display_name="Giang Hoàng", nickname="Giang"))
        s.add(Place(room_id=1, slug="be-bu", name="Quán Bé Bự", aliases=["bé bự"]))
    return db, build_tools(ToolContext(db=db, room_id=1, sender_member_id=1))


# ------------------------------------------------------------------ lifecycle

def test_commit_writes_the_line(env):
    db, _ = env
    with db.session() as s:
        m = memos.create(s, 1, action="add", subject="place:be-bu",
                         subject_label="Quán Bé Bự", text="Quán mùi.")
        memos.commit(s, m.id, 1)
    rows = obs.load(1)
    assert [(r.subject, r.text) for r in rows] == [("place:be-bu", "Quán mùi.")]


def test_commit_is_idempotent(env):
    db, _ = env
    with db.session() as s:
        m = memos.create(s, 1, action="add", subject="place:be-bu",
                         subject_label="X", text="Ngon")
        memos.commit(s, m.id, 1)
        memos.commit(s, m.id, 1)
    assert len(obs.load(1)) == 1


def test_cancel_writes_nothing(env):
    db, _ = env
    with db.session() as s:
        m = memos.create(s, 1, action="add", subject="place:be-bu",
                         subject_label="X", text="Ngon")
        memos.cancel(s, m.id, 1)
        assert s.get(RoomMessage, m.id).attachments["status"] == "cancelled"
    assert obs.load(1) == []


def test_remove_action_deletes_the_line(env):
    db, _ = env
    obs.append(1, obs.Observation(when=None, subject="place:be-bu", gate=None, text="Chậm"))
    with db.session() as s:
        m = memos.create(s, 1, action="remove", subject="place:be-bu",
                         subject_label="X", text="Chậm")
        memos.commit(s, m.id, 1)
    assert obs.load(1) == []


def test_a_standing_rule_round_trips_its_gate(env):
    db, _ = env
    with db.session() as s:
        m = memos.create(s, 1, action="add", subject="place:be-bu", subject_label="X",
                         text="Đông lúc 12h", gate="busy@12:00", when=None)
        memos.commit(s, m.id, 1)
    o = obs.load(1)[0]
    assert o.gate == "busy@12:00" and o.is_rule


def test_unknown_action_and_bad_subject_are_refused(env):
    db, _ = env
    with db.session() as s:
        with pytest.raises(memos.MemoError):
            memos.create(s, 1, action="nope", subject="place:a", subject_label="A", text="x")
        with pytest.raises(memos.MemoError):
            memos.create(s, 1, action="add", subject="be-bu", subject_label="A", text="x")


# ---------------------------------------------------------------------- tools

def test_remember_proposes_rather_than_writing(env):
    _db, tools = env
    res = tools["remember"].execute({"about": "bé bự", "text": "Hay hết cánh gà"})
    assert res["ok"] and res["subject"] == "place:be-bu"
    assert obs.load(1) == [], "remember must not write until the card is confirmed (D7)"


def test_remember_dates_todays_note_but_not_a_standing_rule(env):
    db, tools = env
    tools["remember"].execute({"about": "bé bự", "text": "Hôm nay chậm"})
    tools["remember"].execute({"about": "bé bự", "text": "Phải đi sớm", "standing": True})
    with db.session() as s:
        for m in s.query(RoomMessage).filter_by(kind="memo_draft").all():
            memos.commit(s, m.id, 1)
    by_text = {o.text: o for o in obs.load(1)}
    assert by_text["Hôm nay chậm"].when is not None
    assert by_text["Phải đi sớm"].is_rule


def test_remember_resolves_a_member_subject(env):
    _db, tools = env
    res = tools["remember"].execute({"about": "Giang", "text": "Thích bún riêu"})
    assert res["ok"] and res["subject"].startswith("member:")


def test_remember_refuses_an_unknown_subject(env):
    _db, tools = env
    res = tools["remember"].execute({"about": "quán nào đó", "text": "abc"})
    assert res["ok"] is False and "find_places" in res["error"]


def test_forget_refuses_text_that_does_not_exist(env):
    _db, tools = env
    res = tools["forget"].execute({"about": "bé bự", "text": "không có câu này"})
    assert res["ok"] is False


def test_forget_proposes_removal_of_a_real_line(env):
    db, tools = env
    obs.append(1, obs.Observation(when=None, subject="place:be-bu", gate=None, text="Chậm"))
    res = tools["forget"].execute({"about": "bé bự", "text": "Chậm"})
    assert res["ok"]
    assert len(obs.load(1)) == 1, "still there until confirmed"
    with db.session() as s:
        memos.commit(s, res["memo_id"], 1)
    assert obs.load(1) == []


# ------------------- place-vs-person, operator's explicit rule (D18) --------

@pytest.fixture()
def two_trangs():
    """The real room: a restaurant whose full name contains a person's name."""
    db = Database("sqlite:///:memory:")
    db.create_all()
    with db.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.flush()
        s.add(Member(id=8, room_id=1, display_name="Nhím", nickname="Nhím",
                     account_holder="DINH HONG TRANG"))
        s.add(Member(id=5, room_id=1, display_name="Tabu", nickname="Tabu",
                     account_holder="BUI THU TRANG", aliases=["Bùi Trang"]))
        s.add(Member(id=9, room_id=1, display_name="Giang Hoàng", nickname="Giang"))
        s.add(Place(room_id=1, slug="bun-rieu-co-trang", name="Bún riêu cô Trang",
                    aliases=["bún riêu cô trang", "bun rieu co trang"]))
    return db, build_tools(ToolContext(db=db, room_id=1, sender_member_id=9))


def test_the_full_restaurant_name_files_against_the_place(two_trangs):
    _db, tools = two_trangs
    res = tools["remember"].execute({"about": "Bún riêu cô Trang", "text": "Gọi giá tính tiền"})
    assert res["ok"] and res["subject"] == "place:bun-rieu-co-trang"


def test_a_bare_person_name_never_files_against_the_restaurant(two_trangs):
    """The operator's rule: 'cô Trang' is a person, not the bún riêu shop.

    resolve_best would tie-break this onto the restaurant, filing a note about a
    colleague against a shop. _memo_subject requires a confident place tier and
    refuses when the text could also be a person.
    """
    _db, tools = two_trangs
    res = tools["remember"].execute({"about": "cô Trang", "text": "Hay đổi ý"})
    assert res["ok"] is False, "two people answer to Trang — must ask, not guess"
    assert "find_places" in res["error"] or "find_members" in res["error"]


def test_an_unambiguous_person_still_works(two_trangs):
    _db, tools = two_trangs
    res = tools["remember"].execute({"about": "Giang", "text": "Thích bún riêu"})
    assert res["ok"] and res["subject"].startswith("member:")


# ------------------------------------------ re-filing a card when a place is renamed
#
# `create` freezes the subject into the attachment and `commit` reads it back, so
# a card left pending across a slug rename would commit onto a slug the place no
# longer has. Production had exactly one such card pending when this was written.

def _memos(session, room_id=1):
    from sqlalchemy import select
    return list(session.scalars(
        select(RoomMessage).where(RoomMessage.room_id == room_id,
                                  RoomMessage.kind == "memo_draft")))


def test_retarget_moves_pending_cards_and_their_label(env):
    db, _ = env
    with db.session() as s:
        memos.create(s, 1, action="add", subject="place:be-bu",
                     subject_label="Quán Bé Bự", text="Gọi giá tính tiền",
                     when=date(2026, 8, 14))
    with db.session() as s:
        assert memos.retarget_subject(s, 1, old="place:be-bu", new="place:be-bu-moi",
                                      new_label="Bé Bự (mới)") == 1
    with db.session() as s:
        att = _memos(s)[0].attachments
    assert att["subject"] == "place:be-bu-moi"
    assert att["subject_label"] == "Bé Bự (mới)"
    assert att["status"] == "pending"


def test_retarget_leaves_committed_and_cancelled_cards_alone(env):
    """A committed card records what already happened under the old slug — the
    same rename moves that observation line, and rewriting the card as well would
    make the record disagree with the file."""
    db, _ = env
    with db.session() as s:
        done = memos.create(s, 1, action="add", subject="place:be-bu",
                            subject_label="Quán Bé Bự", text="đã ghi")
        gone = memos.create(s, 1, action="add", subject="place:be-bu",
                            subject_label="Quán Bé Bự", text="đã huỷ")
        memos.commit(s, done.id, 1)
        memos.cancel(s, gone.id, 1)

    with db.session() as s:
        assert memos.retarget_subject(s, 1, old="place:be-bu", new="place:x") == 0
    with db.session() as s:
        assert {m.attachments["subject"] for m in _memos(s)} == {"place:be-bu"}


def test_retarget_ignores_cards_about_someone_else(env):
    db, _ = env
    with db.session() as s:
        memos.create(s, 1, action="add", subject="member:giang",
                     subject_label="Giang Hoàng", text="hay đổi ý")
    with db.session() as s:
        assert memos.retarget_subject(s, 1, old="place:be-bu", new="place:x") == 0
