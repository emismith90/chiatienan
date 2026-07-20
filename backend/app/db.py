"""SQLite engine + session management (WAL, single writer).

The worker runs at concurrency 1 (design §3), so there is one application writer.
WAL mode plus a ``busy_timeout`` keeps the admin page's occasional reads from
colliding with a write. ``DATABASE_URL`` must be an **absolute** sqlite path on
the persistent volume in production (``sqlite:////data/chiatienan.db``).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base


def _apply_sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


class Database:
    """Owns one engine + sessionmaker for a given ``DATABASE_URL``."""

    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(url, future=True, connect_args=connect_args)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _apply_sqlite_pragmas)
        self._sessionmaker = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Transactional scope: commit on success, rollback on error."""
        s = self._sessionmaker()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


_default: Database | None = None


def get_db() -> Database:
    """Process-wide default database (created + migrated on first use)."""
    global _default
    if _default is None:
        _default = Database(settings.database_url)
        _default.create_all()
    return _default
