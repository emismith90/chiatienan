"""Back up everything the app stores, before a deploy changes it.

    python -m app.backup --label pre-<sha>      # -> {DATA_DIR}/backups/<UTC time>-<label>/

Run by the deploy with the backend stopped (nothing writes while it copies), before
the storage migration. Copies:

* the SQLite database through ``sqlite3``'s online-backup API — WAL-safe: committed
  pages *and* the ``-wal`` contents land in one self-contained file, which a plain
  ``cp`` of the main file would miss;
* the ``rooms/`` directory — each room's ``memory.md`` and, before the notes import,
  its ``observations.md``.

Keeps the newest ``--keep`` backups (default 10). Restore: stop the backend, copy
``chiatienan.db`` and ``rooms/`` back into ``DATA_DIR``, deploy the previous commit
(``deploy/DEBUGGING.md`` §3).
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _sqlite_path(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise SystemExit(f"backup: only sqlite URLs are supported, got {url!r}")
    return Path(url[len("sqlite:///"):])


def backup(database_url: str, data_dir: Path, *, label: str, keep: int = 10) -> Path:
    """Write one backup and prune old ones; returns its directory."""
    src = _sqlite_path(database_url)
    if not src.exists():
        raise SystemExit(f"backup: no database at {src}")
    root = data_dir / "backups"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = root / f"{stamp}-{label}" if label else root / stamp
    dest.mkdir(parents=True, exist_ok=False)

    with sqlite3.connect(src) as live, sqlite3.connect(dest / src.name) as copy:
        live.backup(copy)
    with sqlite3.connect(dest / src.name) as copy:       # self-contained: no -wal sidecar needed
        copy.execute("PRAGMA journal_mode=DELETE")
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit(f"backup: integrity check failed on {dest / src.name}")
    rooms = data_dir / "rooms"
    if rooms.is_dir():
        shutil.copytree(rooms, dest / "rooms")

    for old in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)[:-keep or None]:
        shutil.rmtree(old)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.backup", description=__doc__.split("\n")[0])
    ap.add_argument("--label", default="", help="suffix for the backup directory, e.g. pre-<sha>")
    ap.add_argument("--keep", type=int, default=10, help="how many backups to keep")
    args = ap.parse_args(argv)
    if args.keep < 1:
        ap.error("--keep must be at least 1")

    from app.config import settings

    dest = backup(settings.database_url, Path(settings.data_dir), label=args.label, keep=args.keep)
    print(f"backup: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
