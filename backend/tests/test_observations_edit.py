"""Editing ``observations.md`` must not eat the lines the parser skips.

``load()`` deliberately drops comments and malformed lines ("one stray line must
cost one fact, not lunch"). Any writer that rebuilds the file from ``load()``'s
output therefore *deletes* them — which is what ``remove()`` used to do, and what
every editor built on that path would have done to a hand-maintained file.
"""
from datetime import date

import pytest

from app import observations as obs


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    from app import memory as mem
    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path)
    return tmp_path


def _write(room_id, text):
    from app.memory import room_memory_dir
    (room_memory_dir(room_id) / "observations.md").write_text(text, encoding="utf-8")


def _read(room_id):
    from app.memory import room_memory_dir
    return (room_memory_dir(room_id) / "observations.md").read_text(encoding="utf-8")


def _path_bytes(room_id):
    from app.memory import room_memory_dir
    return (room_memory_dir(room_id) / "observations.md").read_bytes()


@pytest.fixture
def room():
    """A room id, so the retarget tests read as prose rather than as `1`."""
    return 1


#: A comment, a good line, a line the parser cannot read, and two more good ones.
MIXED = (
    "# Ghi chú tay — đừng xoá\n"
    "- always | place:be-bu | busy@12:00 | Đông lúc 12h.\n"
    "- rubbish line that does not parse\n"
    "- 2026-03-03 | place:com-ga | - | Làm quá chậm.\n"
    "- always | member:nhim | - | Đề xuất rồi lại đổi ý.\n"
)


# ------------------------------------------------------------------- line ids

def test_line_id_is_stable_and_content_derived():
    o = obs.Observation(when=date(2026, 3, 3), subject="place:com-ga", gate=None, text="Chậm.")
    same = obs.Observation(when=date(2026, 3, 3), subject="place:com-ga", gate=None, text="Chậm.")
    other = obs.Observation(when=date(2026, 3, 3), subject="place:com-ga", gate=None, text="Nhanh.")
    assert o.line_id == same.line_id
    assert o.line_id != other.line_id
    assert len(o.line_id) == 12


def test_indexed_reports_raw_line_numbers_not_parsed_positions():
    _write(1, MIXED)
    rows = obs.indexed(1)
    # The good lines sit at raw indexes 1, 3 and 4 — the comment and the junk
    # occupy 0 and 2, and an editor that forgot them would write to the wrong line.
    assert [i for i, _o in rows] == [1, 3, 4]


# ------------------------------------------------------------------ edit/delete

def test_delete_line_keeps_comments_and_malformed_lines():
    _write(1, MIXED)
    target = next(o for o in obs.load(1) if o.text == "Làm quá chậm.")
    assert obs.delete_line(1, target.line_id) is True
    after = _read(1)
    assert "# Ghi chú tay — đừng xoá" in after
    assert "- rubbish line that does not parse" in after
    assert "Làm quá chậm." not in after
    assert len(obs.load(1)) == 2


def test_replace_line_edits_in_place_and_leaves_the_rest_byte_identical():
    _write(1, MIXED)
    target = next(o for o in obs.load(1) if o.text == "Đông lúc 12h.")
    updated = obs.Observation(when=None, subject="place:be-bu", gate="order-by@11:30",
                              text="Phải gọi trước.")
    assert obs.replace_line(1, target.line_id, updated) is True
    lines = _read(1).splitlines()
    assert lines[0] == "# Ghi chú tay — đừng xoá"
    assert lines[1] == "- always | place:be-bu | order-by@11:30 | Phải gọi trước."
    assert lines[2] == "- rubbish line that does not parse"
    assert lines[3] == "- 2026-03-03 | place:com-ga | - | Làm quá chậm."


def test_remove_no_longer_destroys_unparsed_lines():
    """The regression this suite exists for."""
    _write(1, MIXED)
    assert obs.remove(1, subject="member:nhim", text="Đề xuất rồi lại đổi ý.") is True
    after = _read(1)
    assert "# Ghi chú tay — đừng xoá" in after
    assert "- rubbish line that does not parse" in after
    assert "Đề xuất rồi lại đổi ý." not in after


def test_a_vanished_line_id_is_false_not_an_exception():
    _write(1, MIXED)
    assert obs.delete_line(1, "deadbeef1234") is False
    assert obs.replace_line(1, "deadbeef1234", obs.Observation(
        when=None, subject="place:be-bu", gate=None, text="x")) is False
    assert _read(1) == MIXED


def test_editing_an_absent_file_is_a_no_op():
    assert obs.delete_line(9, "deadbeef1234") is False
    assert obs.load(9) == []


# ---------------------------------------------------------------------- etags

def test_etag_moves_only_when_the_bytes_move():
    _write(1, MIXED)
    before = obs.file_etag(1)
    assert obs.file_etag(1) == before
    obs.append(1, obs.Observation(when=None, subject="place:be-bu", gate=None, text="Mới."))
    assert obs.file_etag(1) != before


def test_etag_of_a_missing_file_is_stable():
    assert obs.file_etag(7) == obs.file_etag(7)


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
# The write half of a place slug rename. Line-preserving like every other write
# here, and additionally responsible for never producing two byte-identical lines
# — they would share a `line_id` and neither could be addressed again.

def test_retarget_moves_only_the_matching_subject(room):
    _write(room, "# tay viết\n"
                 "- always | place:old | order-by@11:30 | Phải gọi trước.\n"
                 "- 2026-08-10 | place:old | - | Hôm nay hết gà.\n"
                 "- always | member:nhim | - | Thích bún riêu.\n"
                 "rác không đọc được\n")

    assert obs.retarget_subject(room, old="place:old", new="place:new") == {
        "moved": 2, "deduped": 0}

    lines = _read(room).splitlines()
    assert lines == [
        "# tay viết",
        "- always | place:new | order-by@11:30 | Phải gọi trước.",
        "- 2026-08-10 | place:new | - | Hôm nay hết gà.",
        "- always | member:nhim | - | Thích bún riêu.",
        "rác không đọc được",
    ]


def test_retarget_keeps_comments_and_unreadable_lines_byte_for_byte(room):
    """The K6 guarantee, applied to the widest write in the module."""
    original = ("# quán ăn\n"
                "\n"
                "- always | place:old | - | Ăn được.\n"
                "- 2026-13-99 | place:old | - | ngày sai\n"
                "  lơ lửng không có gạch đầu dòng\n")
    _write(room, original)

    obs.retarget_subject(room, old="place:old", new="place:new")

    after = _read(room).splitlines()
    assert after[0] == "# quán ăn"
    assert after[1] == ""
    assert after[3] == "- 2026-13-99 | place:old | - | ngày sai"   # unparsed, untouched
    assert after[4] == "  lơ lửng không có gạch đầu dòng"


def test_retarget_drops_a_line_that_would_collide_instead_of_writing_it(room):
    """A moved line landing exactly on an existing one would make both
    unaddressable — `line_id` is a hash of the rendered line."""
    _write(room, "- always | place:old | - | Ăn được.\n"
                 "- always | place:new | - | Ăn được.\n")

    assert obs.retarget_subject(room, old="place:old", new="place:new") == {
        "moved": 0, "deduped": 1}
    assert _read(room) == "- always | place:new | - | Ăn được.\n"


def test_retarget_collapses_a_pre_existing_identical_pair(room):
    """Two identical lines under the old subject were already unaddressable;
    coming out the other side as one is a repair, not a loss."""
    _write(room, "- always | place:old | - | Ăn được.\n"
                 "- always | place:old | - | Ăn được.\n")

    assert obs.retarget_subject(room, old="place:old", new="place:new") == {
        "moved": 1, "deduped": 1}
    assert _read(room) == "- always | place:new | - | Ăn được.\n"

    ids = [o.line_id for o in obs.load(room)]
    assert len(ids) == len(set(ids))


def test_retarget_with_nothing_to_move_does_not_touch_the_file(room):
    _write(room, "- always | member:nhim | - | Thích bún riêu.\n")
    before = _path_bytes(room)

    assert obs.retarget_subject(room, old="place:old", new="place:new") == {
        "moved": 0, "deduped": 0}
    assert _path_bytes(room) == before


def test_retarget_leaves_no_temp_file_behind(room):
    """The write goes through a sibling temp file and `os.replace`."""
    _write(room, "- always | place:old | - | Ăn được.\n")
    obs.retarget_subject(room, old="place:old", new="place:new")

    from app.memory import room_memory_dir
    assert [p.name for p in room_memory_dir(room).iterdir()] == ["observations.md"]
