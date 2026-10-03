"""The pre-deploy backup (plan 2026-10-02, D12 / review R3)."""
import sqlite3
import time

import pytest

from app import backup


def _db(path, rows):
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE IF NOT EXISTS meals (id INTEGER PRIMARY KEY, total INTEGER)")
        c.executemany("INSERT INTO meals (total) VALUES (?)", [(r,) for r in rows])


def test_a_backup_holds_the_database_including_wal_writes_and_the_room_files(tmp_path):
    data = tmp_path / "data"
    (data / "rooms" / "3").mkdir(parents=True)
    (data / "rooms" / "3" / "observations.md").write_text("- always | place:a | - | Ngon\n", encoding="utf-8")
    db = data / "chiatienan.db"
    live = sqlite3.connect(db)                      # held open: recent writes sit in the -wal file
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("CREATE TABLE meals (id INTEGER PRIMARY KEY, total INTEGER)")
    live.executemany("INSERT INTO meals (total) VALUES (?)", [(200000,), (150000,)])
    live.commit()

    dest = backup.backup(f"sqlite:///{db}", data, label="pre-abc123")
    live.close()

    assert dest.name.endswith("-pre-abc123")
    with sqlite3.connect(dest / "chiatienan.db") as c:
        assert c.execute("SELECT sum(total) FROM meals").fetchone()[0] == 350000
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"   # one self-contained file
    assert not (dest / "chiatienan.db-wal").exists()
    assert (dest / "rooms" / "3" / "observations.md").read_text(encoding="utf-8").startswith("- always")


def test_only_the_newest_backups_are_kept(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    _db(data / "chiatienan.db", [1])
    made = []
    for i in range(4):
        made.append(backup.backup(f"sqlite:///{data / 'chiatienan.db'}", data, label=f"n{i}", keep=2).name)
        time.sleep(1.05)                            # distinct second-resolution stamps
    assert sorted(p.name for p in (data / "backups").iterdir()) == made[-2:]


def test_a_missing_database_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="no database"):
        backup.backup(f"sqlite:///{tmp_path / 'nope.db'}", tmp_path, label="x")


def test_a_backup_that_would_fill_the_disk_is_refused(tmp_path, monkeypatch):
    import collections
    data = tmp_path / "data"
    data.mkdir()
    _db(data / "chiatienan.db", [1])
    Usage = collections.namedtuple("Usage", "total used free")
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _p: Usage(100, 99, 1))
    with pytest.raises(SystemExit, match="need"):
        backup.backup(f"sqlite:///{data / 'chiatienan.db'}", data, label="x")
    assert not (data / "backups").exists() or not any((data / "backups").iterdir())
