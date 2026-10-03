"""Editing notes: ids, order, etags, retargeting.

These used to guard a hand-edited file (comments and unreadable lines had to survive
every write). The notes now live in the ``notes`` store; what carries over is that an
edit keeps a fact's place, identical facts collapse to one, and nothing is lost
silently — the import reports what it did not take (``test_observations``).
"""
from datetime import date

import pytest

from app import observations as obs
from tests.notes_util import note_lines, notes, seed_notes

#: Three facts, in the order the room wrote them.
THREE = (
    "- always | place:be-bu | busy@12:00 | Đông lúc 12h.\n"
    "- 2026-03-03 | place:com-ga | - | Làm quá chậm.\n"
    "- always | member:nhim | - | Đề xuất rồi lại đổi ý.\n"
)


def _id(db, room, text):
    return next(o for o in notes(db, room) if o.text == text).line_id


# ------------------------------------------------------------------- line ids

def test_line_id_is_stable_and_content_derived():
    o = obs.Observation(when=date(2026, 3, 3), subject="place:com-ga", gate=None, text="Chậm.")
    same = obs.Observation(when=date(2026, 3, 3), subject="place:com-ga", gate=None, text="Chậm.")
    other = obs.Observation(when=date(2026, 3, 3), subject="place:com-ga", gate=None, text="Nhanh.")
    assert o.line_id == same.line_id
    assert o.line_id != other.line_id
    assert len(o.line_id) == 12


def test_load_keeps_the_order_the_room_wrote(db):
    seed_notes(db, 1, THREE)
    assert [o.text for o in notes(db, 1)] == ["Đông lúc 12h.", "Làm quá chậm.", "Đề xuất rồi lại đổi ý."]


# ------------------------------------------------------------------ edit/delete

def test_delete_line_removes_one_fact(db):
    seed_notes(db, 1, THREE)
    with db.session() as s:
        assert obs.delete_line(s, 1, _id(db, 1, "Làm quá chậm.")) is True
    assert [o.text for o in notes(db, 1)] == ["Đông lúc 12h.", "Đề xuất rồi lại đổi ý."]


def test_replace_line_edits_in_place(db):
    seed_notes(db, 1, THREE)
    updated = obs.Observation(when=None, subject="place:be-bu", gate="order-by@11:30",
                              text="Phải gọi trước.")
    with db.session() as s:
        assert obs.replace_line(s, 1, _id(db, 1, "Đông lúc 12h."), updated) is True
    assert note_lines(db, 1) == [
        "- always | place:be-bu | order-by@11:30 | Phải gọi trước.",
        "- 2026-03-03 | place:com-ga | - | Làm quá chậm.",
        "- always | member:nhim | - | Đề xuất rồi lại đổi ý.",
    ]


def test_replacing_a_fact_onto_an_identical_one_keeps_one(db):
    seed_notes(db, 1, THREE)
    copy = obs.Observation(when=date(2026, 3, 3), subject="place:com-ga", gate=None, text="Làm quá chậm.")
    with db.session() as s:
        assert obs.replace_line(s, 1, _id(db, 1, "Đông lúc 12h."), copy) is True
    assert [o.text for o in notes(db, 1)] == ["Làm quá chậm.", "Đề xuất rồi lại đổi ý."]


def test_remove_takes_the_earliest_match(db):
    seed_notes(db, 1, THREE)
    with db.session() as s:
        assert obs.remove(s, 1, subject="member:nhim", text="Đề xuất rồi lại đổi ý.") is True
    assert len(notes(db, 1)) == 2


def test_a_vanished_line_id_is_false_not_an_exception(db):
    seed_notes(db, 1, THREE)
    with db.session() as s:
        assert obs.delete_line(s, 1, "deadbeef1234") is False
        assert obs.replace_line(s, 1, "deadbeef1234", obs.Observation(
            when=None, subject="place:be-bu", gate=None, text="x")) is False
    assert len(notes(db, 1)) == 3


# ---------------------------------------------------------------------- etags

def test_etag_moves_only_when_the_notes_move(db):
    seed_notes(db, 1, THREE)
    with db.session() as s:
        before = obs.etag(s, 1)
        assert obs.etag(s, 1) == before
        obs.append(s, 1, obs.Observation(when=None, subject="place:be-bu", gate=None, text="Mới."))
        assert obs.etag(s, 1) != before


def test_an_edit_moves_the_etag(db):
    seed_notes(db, 1, THREE)
    with db.session() as s:
        before = obs.etag(s, 1)
        obs.replace_line(s, 1, _id(db, 1, "Làm quá chậm."), obs.Observation(
            when=date(2026, 3, 3), subject="place:com-ga", gate=None, text="Nhanh hơn rồi."))
        assert obs.etag(s, 1) != before


def test_etag_of_an_empty_room_is_stable(db):
    with db.session() as s:
        assert obs.etag(s, 7) == obs.etag(s, 7)


# ----------------------------------------------------------------- gate labels

@pytest.mark.parametrize("gate,label,at", [
    ("busy@12:00", "Busy from 12:00", "12:00"),
    ("order-by@11:30", "Order by 11:30", "11:30"),
    ("closes@12:30", "Closes 12:30", "12:30"),
    (None, None, None),
])
def test_gates_render_as_human_words(gate, label, at):
    o = obs.Observation(when=None, subject="place:be-bu", gate=gate, text="x")
    assert obs.gate_label(o) == label
    assert obs.gate_at(o) == at


def test_parse_gate_validates_instead_of_dropping():
    assert obs.parse_gate("order-by", "11:30") == "order-by@11:30"
    assert obs.parse_gate(None, "11:30") is None
    with pytest.raises(ValueError):
        obs.parse_gate("order-by", "25:00")
    with pytest.raises(ValueError):
        obs.parse_gate("nonsense", "11:30")
    with pytest.raises(ValueError):
        obs.parse_gate("busy", None)


# ============================================ retargeting one subject onto another
#
# The write half of a place slug rename. It must never produce two identical facts
# — they would share a `line_id` and neither could be addressed again.

def test_retarget_moves_only_the_matching_subject_and_keeps_order(db):
    seed_notes(db, 1, "- always | place:old | order-by@11:30 | Phải gọi trước.\n"
                      "- 2026-08-10 | place:old | - | Hôm nay hết gà.\n"
                      "- always | member:nhim | - | Thích bún riêu.\n")
    with db.session() as s:
        assert obs.retarget_subject(s, 1, old="place:old", new="place:new") == {"moved": 2, "deduped": 0}
    assert note_lines(db, 1) == [
        "- always | place:new | order-by@11:30 | Phải gọi trước.",
        "- 2026-08-10 | place:new | - | Hôm nay hết gà.",
        "- always | member:nhim | - | Thích bún riêu.",
    ]


def test_retarget_drops_a_fact_that_would_collide_instead_of_writing_it(db):
    seed_notes(db, 1, "- always | place:old | - | Ăn được.\n"
                      "- always | place:new | - | Ăn được.\n")
    with db.session() as s:
        assert obs.retarget_subject(s, 1, old="place:old", new="place:new") == {"moved": 0, "deduped": 1}
    assert note_lines(db, 1) == ["- always | place:new | - | Ăn được."]


def test_retarget_with_nothing_to_move_changes_nothing(db):
    seed_notes(db, 1, "- always | member:nhim | - | Thích bún riêu.\n")
    with db.session() as s:
        before = obs.etag(s, 1)
        assert obs.retarget_subject(s, 1, old="place:old", new="place:new") == {"moved": 0, "deduped": 0}
        assert obs.etag(s, 1) == before
