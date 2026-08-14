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
