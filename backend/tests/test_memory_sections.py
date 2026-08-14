"""``memory.md`` as editable sections.

The round-trip test comes first on purpose: a parser that strips whitespace and a
writer that re-adds its own guess would reformat the whole file the first time
anyone edited one section of it, and the diff would be attributed to the person who
fixed a typo.
"""
import pytest

from app import memory as mem


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path)
    return tmp_path


def _write(room_id, text):
    (mem.room_memory_dir(room_id) / "memory.md").write_text(text, encoding="utf-8")


def _read(room_id):
    return (mem.room_memory_dir(room_id) / "memory.md").read_text(encoding="utf-8")


def _two_sections(room_id):
    """Exactly what two ``append_summary`` calls leave behind."""
    mem.append_summary(room_id, summary_text="- Ăn bún chả\n- Linh trả",
                       through_id=10, through_at="2026-07-01T12:00:00", header="Tóm tắt")
    mem.append_summary(room_id, summary_text="- Ăn cơm gà", through_id=20,
                       through_at="2026-08-01T12:00:00", header="Tóm tắt")


# ------------------------------------------------------------------ round-trip

def test_split_and_join_are_byte_identical(tmp_path):
    _two_sections(1)
    before = _read(1)
    preamble, blocks = mem._split(before)
    assert mem._join(preamble, blocks) == before


def test_round_trip_survives_a_preamble_and_a_dateless_header():
    _write(1, "Ghi chú tay ở đầu file.\n\n## Không có ngày\nnội dung\n\n## Có — 2026-08-01\nx\n")
    before = _read(1)
    preamble, blocks = mem._split(before)
    assert mem._join(preamble, blocks) == before
    secs = mem.parse_sections(1)
    assert [(s["title"], s["date"]) for s in secs] == [
        ("Không có ngày", None), ("Có", "2026-08-01")]


def test_parse_sections_splits_title_from_date():
    _two_sections(1)
    secs = mem.parse_sections(1)
    assert [s["index"] for s in secs] == [0, 1]
    assert [s["date"] for s in secs] == ["2026-07-01", "2026-08-01"]
    assert secs[0]["title"] == "Tóm tắt"
    assert secs[0]["body"] == "- Ăn bún chả\n- Linh trả"


def test_no_sections_no_crash():
    assert mem.parse_sections(3) == []


# ----------------------------------------------------------------------- edits

def test_write_section_leaves_its_neighbours_alone():
    _two_sections(1)
    mem.write_section(1, 0, "- Sửa lại rồi")
    secs = mem.parse_sections(1)
    assert secs[0]["body"] == "- Sửa lại rồi"
    assert secs[1]["body"] == "- Ăn cơm gà"
    # The blank line between sections is structure, not content: losing it would
    # merge the two headings into one paragraph on every markdown renderer.
    assert "\n\n## Tóm tắt — 2026-08-01" in _read(1)


def test_write_section_refuses_to_smuggle_in_a_new_heading():
    _two_sections(1)
    with pytest.raises(mem.MemoryError_):
        mem.write_section(1, 0, "- ok\n## Mục lạ\n- xấu")
    assert len(mem.parse_sections(1)) == 2


def test_write_section_rejects_an_index_that_does_not_exist():
    _two_sections(1)
    with pytest.raises(mem.MemoryError_):
        mem.write_section(1, 5, "x")


def test_delete_section_keeps_the_watermark():
    _two_sections(1)
    before = mem.read_watermark(1)
    mem.delete_section(1, 0)
    secs = mem.parse_sections(1)
    assert [s["date"] for s in secs] == ["2026-08-01"]
    # Rolling the watermark back would make the next turn re-summarize months of
    # chat into a duplicate section — LLM-billed, and with no undo.
    assert mem.read_watermark(1) == before == 20


def test_delete_the_only_section_empties_the_file_without_deleting_it():
    mem.append_summary(1, summary_text="- x", through_id=1,
                       through_at="2026-07-01T12:00:00", header="Tóm tắt")
    mem.delete_section(1, 0)
    assert mem.parse_sections(1) == []
    assert _read(1) == ""


def test_delete_section_preserves_a_hand_written_preamble():
    _write(1, "Ghi chú tay.\n\n## A — 2026-07-01\na\n\n## B — 2026-08-01\nb\n")
    mem.delete_section(1, 1)
    assert _read(1).startswith("Ghi chú tay.")
    assert "## A — 2026-07-01" in _read(1)
    assert "## B" not in _read(1)


# ----------------------------------------------------------------- meta / etag

def test_read_meta_returns_both_watermark_fields():
    mem.set_watermark(1, through_id=42, through_at="2026-08-01T12:00:00")
    assert mem.read_meta(1) == {
        "summarized_through_id": 42, "summarized_through_at": "2026-08-01T12:00:00"}


def test_read_meta_survives_a_corrupt_file():
    mem.room_memory_dir(1)
    (mem.room_memory_dir(1) / "memory.meta.json").write_text("{not json", encoding="utf-8")
    assert mem.read_meta(1) == {}
    assert mem.read_watermark(1) == 0


def test_etag_moves_with_the_bytes():
    _two_sections(1)
    before = mem.file_etag(1)
    mem.write_section(1, 0, "- khác hẳn")
    assert mem.file_etag(1) != before
