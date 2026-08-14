from datetime import date, datetime

import pytest

from app import observations as obs


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    # `memory._base_dir` exists precisely so tests can redirect the room files
    # (test_memory.py:12) — patching `settings` would not work, since memory.py
    # bound its own reference at import.
    from app import memory as mem
    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path)
    return tmp_path


def _write(room_id, text, tmp_path):
    from app.memory import room_memory_dir
    (room_memory_dir(room_id) / "observations.md").write_text(text, encoding="utf-8")


# ------------------------------------------------------------------- parsing

def test_parses_the_four_fields(tmp_path):
    _write(1, "- 2026-03-03 | place:com-ga-thinh-lo | - | Làm quá chậm.\n", tmp_path)
    rows = obs.load(1)
    assert len(rows) == 1
    o = rows[0]
    assert o.when == date(2026, 3, 3)
    assert o.subject == "place:com-ga-thinh-lo"
    assert o.gate is None
    assert o.text == "Làm quá chậm."


def test_always_lines_have_no_date(tmp_path):
    _write(1, "- always | place:be-bu | busy@12:00 | Đông lúc 12h.\n", tmp_path)
    o = obs.load(1)[0]
    assert o.when is None and o.gate == "busy@12:00"
    assert o.is_rule is True


def test_a_malformed_line_is_skipped_not_raised(tmp_path):
    _write(1, (
        "- 2026-03-03 | place:a | - | fine\n"
        "this line is nonsense\n"
        "- nope | place:b | - | bad date\n"
        "- 2026-03-04 | place:c | - | also fine\n"
    ), tmp_path)
    rows = obs.load(1)
    assert [r.subject for r in rows] == ["place:a", "place:c"], \
        "the operator hand-edits this file; one bad line must cost one fact, not lunch"


def test_missing_file_is_empty(tmp_path):
    assert obs.load(99) == []


# ------------------------------------------------------------------- queries

def test_for_subjects_filters_by_recency_but_never_ages_out_a_rule(tmp_path):
    _write(1, (
        "- 2025-01-01 | place:a | - | ancient complaint\n"
        "- 2026-08-01 | place:a | - | recent complaint\n"
        "- always     | place:a | - | standing rule\n"
    ), tmp_path)
    rows = obs.for_subjects(1, ["place:a"], since_days=180, today=date(2026, 8, 14))
    texts = {r.text for r in rows}
    assert "recent complaint" in texts
    assert "standing rule" in texts, "an `always` line must never be aged out (D4)"
    assert "ancient complaint" not in texts


def test_for_subjects_ignores_other_subjects(tmp_path):
    _write(1, "- always | place:a | - | A\n- always | member:nhim | - | N\n", tmp_path)
    assert {r.text for r in obs.for_subjects(1, ["place:a"])} == {"A"}


def test_count_since_gives_the_model_the_number(tmp_path):
    """The brief's third example: 'that's the 3rd time this month'.

    Python counts; the model writes prose around the number it is handed.
    """
    _write(1, (
        "- 2026-08-02 | member:nhim | - | đổi ý\n"
        "- 2026-08-07 | member:nhim | - | đổi ý\n"
        "- 2026-08-13 | member:nhim | - | đổi ý\n"
        "- 2026-07-30 | member:nhim | - | đổi ý (tháng trước)\n"
    ), tmp_path)
    assert obs.count_since(1, "member:nhim", since=date(2026, 8, 1)) == 3


# ------------------------------------------------------------------- writing

def test_append_and_remove_round_trip(tmp_path):
    obs.append(1, obs.Observation(when=None, subject="place:a", gate=None, text="Ngon"))
    assert [r.text for r in obs.load(1)] == ["Ngon"]
    assert obs.remove(1, subject="place:a", text="Ngon") is True
    assert obs.load(1) == []


def test_remove_returns_false_when_nothing_matches(tmp_path):
    obs.append(1, obs.Observation(when=None, subject="place:a", gate=None, text="Ngon"))
    assert obs.remove(1, subject="place:a", text="khác") is False
    assert len(obs.load(1)) == 1


def test_append_is_round_trippable_through_the_line_format(tmp_path):
    o = obs.Observation(when=date(2026, 8, 14), subject="place:be-bu",
                        gate="busy@12:00", text="Đông | có ký tự lạ")
    obs.append(1, o)
    back = obs.load(1)[0]
    assert (back.when, back.subject, back.gate) == (o.when, o.subject, o.gate)
    assert back.text == "Đông | có ký tự lạ", "text may contain the delimiter"


# --------------------------------------------------------------------- gates

from datetime import time as _time


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
