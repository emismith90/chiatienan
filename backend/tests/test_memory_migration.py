"""The DATA_DIR rename must not orphan production room memory.

Memory lived at `{cursor_workspace}/rooms/{id}/` and prod's workspace was
`/data/cursor-agent`, so `DATA_DIR=/data` points at a *different* directory. Without
this migration the first post-deploy turn would find `load_memory` empty,
`read_watermark` at 0, and `_maybe_rollover` re-summarizing every room's entire
>10-week history into a fresh memory.md — silent, expensive, user-visible data loss
dressed up as a rename.
"""
import json

from app import memory


def test_legacy_room_memory_is_migrated_on_first_access(tmp_path, monkeypatch):
    legacy = tmp_path / "cursor-agent" / "rooms" / "1"
    legacy.mkdir(parents=True)
    (legacy / "memory.md").write_text("## tuần trước\n- An trả 300k\n", encoding="utf-8")
    (legacy / "memory.meta.json").write_text(
        json.dumps({"summarized_through_id": 42}), encoding="utf-8")
    monkeypatch.setattr(memory, "_LEGACY_BASE", tmp_path / "cursor-agent")
    monkeypatch.setattr(memory, "_base_dir", lambda: tmp_path / "new")

    assert "An trả 300k" in memory.load_memory(1)
    assert memory.read_watermark(1) == 42       # not 0 — no re-summarization


def test_migration_is_idempotent_and_never_overwrites(tmp_path, monkeypatch):
    legacy = tmp_path / "cursor-agent" / "rooms" / "1"
    legacy.mkdir(parents=True)
    (legacy / "memory.md").write_text("old", encoding="utf-8")
    monkeypatch.setattr(memory, "_LEGACY_BASE", tmp_path / "cursor-agent")
    monkeypatch.setattr(memory, "_base_dir", lambda: tmp_path / "new")

    memory.load_memory(1)                        # migrates
    target = tmp_path / "new" / "rooms" / "1" / "memory.md"
    target.write_text("newer memory written after the migration", encoding="utf-8")
    memory.load_memory(1)                        # must not clobber
    assert target.read_text(encoding="utf-8").startswith("newer")


def test_a_room_with_no_legacy_memory_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_LEGACY_BASE", tmp_path / "cursor-agent")
    monkeypatch.setattr(memory, "_base_dir", lambda: tmp_path / "new")
    assert memory.load_memory(7) == ""
    assert memory.read_watermark(7) == 0


def test_the_migration_copies_rather_than_moves(tmp_path, monkeypatch):
    # A rollback to the previous engine must still find the originals.
    legacy = tmp_path / "cursor-agent" / "rooms" / "1"
    legacy.mkdir(parents=True)
    (legacy / "memory.md").write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(memory, "_LEGACY_BASE", tmp_path / "cursor-agent")
    monkeypatch.setattr(memory, "_base_dir", lambda: tmp_path / "new")
    memory.load_memory(1)
    assert (legacy / "memory.md").exists()
