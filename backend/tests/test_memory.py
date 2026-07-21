from datetime import timedelta

import pytest

from app import memory as mem
from app.clock import now_ict
from tests.test_ledger import _seed_room


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path)
    return tmp_path


def test_load_and_watermark_default_when_absent(workspace):
    assert mem.load_memory(1) == ""
    assert mem.read_watermark(1) == 0


def test_append_summary_writes_section_and_advances_watermark(workspace):
    mem.append_summary(1, summary_text="- An trả 100k", through_id=5,
                       through_at="2026-07-21T10:00:00+07:00", header="Xoá ngữ cảnh")
    body = mem.load_memory(1)
    assert "## Xoá ngữ cảnh" in body
    assert "An trả 100k" in body
    assert mem.read_watermark(1) == 5

    # second append accumulates and moves the watermark forward
    mem.append_summary(1, summary_text="- Bình trả 50k", through_id=9,
                       through_at="2026-07-28T10:00:00+07:00", header="Xoá ngữ cảnh")
    body2 = mem.load_memory(1)
    assert "An trả 100k" in body2 and "Bình trả 50k" in body2
    assert mem.read_watermark(1) == 9


def test_set_watermark_without_append(workspace):
    mem.set_watermark(1, through_id=3, through_at="2026-07-21T10:00:00+07:00")
    assert mem.read_watermark(1) == 3
    assert mem.load_memory(1) == ""


def test_messages_to_summarize_filters(workspace, db):
    from app import chat
    room_id, m = _seed_room(db, 2)
    old = now_ict() - timedelta(weeks=20)
    with db.session() as s:
        m1 = chat.post_message(s, room_id, m[0], "xin chào")
        m2 = chat.post_message(s, room_id, None, "chào bạn", kind="bot")
        m3 = chat.post_message(s, room_id, m[1], "/clear")
        div = chat.post_message(s, room_id, None, "reset", kind="context_reset")
        # backdate the first two so the rollover filter can catch them
        m1.created_at = old
        m2.created_at = old
        s.flush()
        wm0 = mem.read_watermark(room_id)
        # watermark filter + kind filter (context_reset excluded)
        rows = mem.messages_to_summarize(s, room_id, watermark=wm0)
        ids = [r.id for r in rows]
        assert m1.id in ids and m2.id in ids and m3.id in ids
        assert div.id not in ids
        # before_id excludes the /clear line
        rows_b = mem.messages_to_summarize(s, room_id, watermark=wm0, before_id=m3.id)
        assert m3.id not in [r.id for r in rows_b]
        # older_than catches only the backdated pair
        rows_old = mem.messages_to_summarize(s, room_id, watermark=wm0,
                                             older_than=now_ict() - timedelta(weeks=10))
        assert [r.id for r in rows_old] == [m1.id, m2.id]
