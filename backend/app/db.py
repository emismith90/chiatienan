"""SQLite engine + session management (WAL, single writer).

The worker runs at concurrency 1 (design §3), so there is one application writer.
WAL mode plus a ``busy_timeout`` keeps the admin page's occasional reads from
colliding with a write. ``DATABASE_URL`` must be an **absolute** sqlite path on
the persistent volume in production (``sqlite:////data/chiatienan.db``).

Schema changes: ``create_all()`` builds *missing tables*, and
:func:`_sync_additive_columns` then adds *missing columns* to tables that
already exist (see that function for why the second half is not optional).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base

log = logging.getLogger("chiatienan.db")


def _apply_sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def _sql_literal(value) -> str | None:
    """``value`` as a SQL literal for a DDL ``DEFAULT``, or None if unrenderable."""
    if isinstance(value, bool):  # before int: bool is an int subclass
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _ddl_default(column) -> str | None:
    """DDL ``DEFAULT`` text for ``column``, or None if it has no literal form.

    A ``mapped_column(default=...)`` is applied by the ORM on *insert*, so it
    never reaches rows that already exist — to backfill those, the default has
    to be restated in DDL here. Callable defaults (``now_ict``, ``list``) have
    no literal form and yield None.
    """
    if column.server_default is not None:
        return str(column.server_default.arg)
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    return _sql_literal(default.arg)


def _sync_additive_columns(engine: Engine) -> None:
    """Add columns present in the models but missing from an existing table.

    ``create_all()`` only ever *creates* tables — it will not touch one that
    already exists, so a new ``mapped_column`` on an existing model leaves the
    live DB a column short. Every SELECT that model builds then dies with
    ``no such column``, which reads from the app as "all the data is gone" even
    though the rows are untouched (this happened in prod: PR #34 added
    ``members.default_participant`` and the whole room went blank). The manual
    ``ALTER TABLE`` the runbook prescribed was one forgettable step between a
    merge and an outage, so the process is done here instead.

    Strictly additive and idempotent: it never drops, renames, retypes, or
    reorders anything, and it re-derives its work from the live schema on every
    call, so running it on an already-current DB is a no-op. Anything beyond
    adding a column (a dropped column, a changed type, a new constraint) is
    still a hand-written migration.
    """
    inspector = inspect(engine)
    live_tables = set(inspector.get_table_names())
    quote = engine.dialect.identifier_preparer.quote

    for table in Base.metadata.sorted_tables:
        if table.name not in live_tables:
            continue  # create_all() just built it, with every column
        live_columns = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in live_columns:
                continue
            ddl = (
                f"ALTER TABLE {quote(table.name)} ADD COLUMN "
                f"{quote(column.name)} {column.type.compile(dialect=engine.dialect)}"
            )
            default = _ddl_default(column)
            if default is not None:
                ddl += f" DEFAULT {default}"
            if not column.nullable:
                # SQLite rejects a NOT NULL column with no default: there would
                # be nothing to put in the existing rows. Without a literal
                # default, add it nullable rather than abort — a column the ORM
                # fills on insert beats a table the app cannot read at all.
                if default is None:
                    log.warning(
                        "%s.%s is NOT NULL with no literal default; adding it nullable. "
                        "Existing rows will hold NULL — backfill them by hand.",
                        table.name, column.name,
                    )
                else:
                    ddl += " NOT NULL"
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
            except OperationalError as exc:
                # A deploy can briefly overlap the outgoing and incoming
                # container, so both may reach the same ALTER. Losing that race
                # is success — the column is there. Anything else is a real
                # failure and must not be swallowed.
                if "duplicate column" not in str(exc).lower():
                    raise
                log.info("schema: %s.%s already added concurrently", table.name, column.name)
            else:
                log.info("schema: added %s.%s", table.name, column.name)


class Database:
    """Owns one engine + sessionmaker for a given ``DATABASE_URL``."""

    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(url, future=True, connect_args=connect_args)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _apply_sqlite_pragmas)
        self._sessionmaker = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        """Bring the schema up to the models: missing tables, then missing columns."""
        Base.metadata.create_all(self.engine)
        _sync_additive_columns(self.engine)

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
