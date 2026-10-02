from datetime import date, datetime

import pytest

from app import observations as obs
from tests.notes_util import notes, seed_notes


# ------------------------------------------------------------------- parsing (the import format)

def test_parses_the_four_fields():
    rows, _ = obs.parse_text("- 2026-03-03 | place:com-ga-thinh-lo | - | Làm quá chậm.\n")
    assert len(rows) == 1
    o = rows[0]
    assert o.when == date(2026, 3, 3)
    assert o.subject == "place:com-ga-thinh-lo"
    assert o.gate is None
    assert o.text == "Làm quá chậm."


def test_always_lines_have_no_date():
    o = obs.parse_text("- always | place:be-bu | busy@12:00 | Đông lúc 12h.\n")[0][0]
    assert o.when is None and o.gate == "busy@12:00"
    assert o.is_rule is True


def test_a_malformed_line_is_skipped_and_reported_not_raised():
    rows, report = obs.parse_text(
        "# viết tay\n"
        "- 2026-03-03 | place:a | - | fine\n"
        "this line is nonsense\n"
        "- nope | place:b | - | bad date\n"
        "\n"
        "- 2026-03-04 | place:c | - | also fine\n")
    assert [r.subject for r in rows] == ["place:a", "place:c"], \
        "the operator hand-edited this file; one bad line must cost one fact, not lunch"
    assert report == {"comments": 1, "blank": 1,
                      "dropped": [(3, "this line is nonsense"), (4, "- nope | place:b | - | bad date")]}


def test_a_room_without_notes_is_empty(db):
    assert notes(db, 99) == []


# ------------------------------------------------------------------- queries

def test_for_subjects_filters_by_recency_but_never_ages_out_a_rule(db):
    seed_notes(db, 1, (
        "- 2025-01-01 | place:a | - | ancient complaint\n"
        "- 2026-08-01 | place:a | - | recent complaint\n"
        "- always     | place:a | - | standing rule\n"
    ))
    with db.session() as s:
        rows = obs.for_subjects(s, 1, ["place:a"], since_days=180, today=date(2026, 8, 14))
    texts = {r.text for r in rows}
    assert "recent complaint" in texts
    assert "standing rule" in texts, "an `always` line must never be aged out (D4)"
    assert "ancient complaint" not in texts


def test_for_subjects_ignores_other_subjects(db):
    seed_notes(db, 1, "- always | place:a | - | A\n- always | member:nhim | - | N\n")
    with db.session() as s:
        assert {r.text for r in obs.for_subjects(s, 1, ["place:a"])} == {"A"}


def test_count_since_gives_the_model_the_number(db):
    """The brief's third example: 'that's the 3rd time this month'.

    Python counts; the model writes prose around the number it is handed.
    """
    seed_notes(db, 1, (
        "- 2026-08-02 | member:nhim | - | đổi ý\n"
        "- 2026-08-07 | member:nhim | - | đổi ý lần 2\n"
        "- 2026-08-13 | member:nhim | - | đổi ý lần 3\n"
        "- 2026-07-30 | member:nhim | - | đổi ý (tháng trước)\n"
    ))
    with db.session() as s:
        assert obs.count_since(s, 1, "member:nhim", since=date(2026, 8, 1)) == 3


def test_notes_are_per_room(db):
    seed_notes(db, 1, "- always | place:a | - | A\n")
    assert notes(db, 2) == []


# ------------------------------------------------------------------- writing

def test_append_and_remove_round_trip(db):
    with db.session() as s:
        assert obs.append(s, 1, obs.Observation(when=None, subject="place:a", gate=None, text="Ngon")) is True
    assert [r.text for r in notes(db, 1)] == ["Ngon"]
    with db.session() as s:
        assert obs.remove(s, 1, subject="place:a", text="Ngon") is True
    assert notes(db, 1) == []


def test_an_identical_fact_is_not_added_twice(db):
    o = obs.Observation(when=None, subject="place:a", gate=None, text="Ngon")
    with db.session() as s:
        assert obs.append(s, 1, o) is True
        assert obs.append(s, 1, o) is False
    assert len(notes(db, 1)) == 1


def test_remove_returns_false_when_nothing_matches(db):
    with db.session() as s:
        obs.append(s, 1, obs.Observation(when=None, subject="place:a", gate=None, text="Ngon"))
        assert obs.remove(s, 1, subject="place:a", text="khác") is False
    assert len(notes(db, 1)) == 1


def test_append_round_trips_every_field(db):
    o = obs.Observation(when=date(2026, 8, 14), subject="place:be-bu",
                        gate="busy@12:00", text="Đông | có ký tự lạ")
    with db.session() as s:
        obs.append(s, 1, o)
    back = notes(db, 1)[0]
    assert back == o, "text may contain the old file's delimiter"


def test_a_note_rolls_back_with_its_transaction(db):
    """The memo card's status flip and the note it writes commit together."""
    with pytest.raises(RuntimeError):
        with db.session() as s:
            obs.append(s, 1, obs.Observation(when=None, subject="place:a", gate=None, text="Ngon"))
            raise RuntimeError("the card failed")
    assert notes(db, 1) == []


# --------------------------------------------------------------------- gates

def _at(h, m):
    return datetime(2026, 8, 14, h, m)


@pytest.mark.parametrize("now,expected", [
    (_at(11, 20), "ok"),        # 40 min + 5 walk -> comfortable
    (_at(11, 50), "act_now"),   # 10 min + 5 walk -> inside the warning window
    (_at(12, 10), "too_late"),  # already past
])
def test_busy_gate_accounts_for_the_walk(now, expected):
    o = obs.Observation(when=None, subject="place:be-bu", gate="busy@12:00", text="Đông")
    status, _left = obs.gate_status(o, now=now, walk_minutes=5)
    assert status == expected


def test_busy_gate_without_a_walk_override_uses_the_room_default():
    o = obs.Observation(when=None, subject="place:be-bu", gate="busy@12:00", text="Đông")
    # 11:53 + the 5-minute room default lands at 11:58 — inside the window.
    assert obs.gate_status(o, now=_at(11, 53))[0] == "act_now"


@pytest.mark.parametrize("now,expected", [
    (_at(11, 0), "ok"),
    (_at(11, 25), "act_now"),
    (_at(11, 40), "too_late"),
])
def test_order_by_gate_ignores_the_walk(now, expected):
    """You phone ahead — travel time is irrelevant to a call deadline."""
    o = obs.Observation(when=None, subject="place:x", gate="order-by@11:30", text="Đặt trước")
    status, _left = obs.gate_status(o, now=now, walk_minutes=30)
    assert status == expected


def test_closes_is_a_distinct_status_from_busy_at_the_same_instant():
    """D9: 'sẽ đông' is advice, 'đóng cửa rồi' is a refusal — different answers."""
    busy = obs.Observation(when=None, subject="place:a", gate="busy@12:30", text="Đông")
    closes = obs.Observation(when=None, subject="place:b", gate="closes@12:30", text="Đóng cửa")
    now = _at(12, 40)
    assert obs.gate_status(busy, now=now, walk_minutes=5)[0] == "too_late"
    assert obs.gate_status(closes, now=now, walk_minutes=5)[0] == "too_late"
    # ...and the kind is reported so the prose can differ.
    assert obs.gate_kind(busy) == "busy"
    assert obs.gate_kind(closes) == "closes"


def test_minutes_left_is_reported_for_act_now():
    o = obs.Observation(when=None, subject="place:x", gate="order-by@11:30", text="Đặt trước")
    status, left = obs.gate_status(o, now=_at(11, 15))
    assert status == "act_now" and left == 15


def test_an_ungated_observation_is_always_ok():
    o = obs.Observation(when=None, subject="place:x", gate=None, text="Ngon")
    assert obs.gate_status(o, now=_at(23, 0)) == ("ok", None)
