"""Create the content plane's tables next to a host's, and keep them current.

``bind(engine)`` is what a host calls from its own schema setup. It creates
missing tables and then adds missing columns to existing ones — the same
strictly-additive, idempotent discipline as chiatienan's ``app.db``, parameterised
by metadata so it can serve any host.
"""
from __future__ import annotations

import logging

from sqlalchemy import MetaData, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from kernos.content.models import Base

log = logging.getLogger("kernos.content")


def _sql_literal(value) -> str | None:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _ddl_default(column) -> str | None:
    if column.server_default is not None:
        return str(column.server_default.arg)
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    return _sql_literal(default.arg)


def sync_additive_columns(engine: Engine, metadata: MetaData) -> list[str]:
    """Add columns present in ``metadata`` but missing from existing tables.

    Never drops, renames, retypes or reorders; re-derives its work from the live
    schema on every call. Returns ``["table.column", …]`` it added.
    """
    inspector = inspect(engine)
    live_tables = set(inspector.get_table_names())
    quote = engine.dialect.identifier_preparer.quote
    added: list[str] = []
    for table in metadata.sorted_tables:
        if table.name not in live_tables:
            continue
        live_columns = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in live_columns:
                continue
            ddl = (f"ALTER TABLE {quote(table.name)} ADD COLUMN "
                   f"{quote(column.name)} {column.type.compile(dialect=engine.dialect)}")
            default = _ddl_default(column)
            if default is not None:
                ddl += f" DEFAULT {default}"
                if not column.nullable:
                    ddl += " NOT NULL"
            elif not column.nullable:
                log.warning("%s.%s is NOT NULL with no literal default; adding it nullable",
                            table.name, column.name)
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
            except OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
            else:
                added.append(f"{table.name}.{column.name}")
                log.info("schema: added %s.%s", table.name, column.name)
    return added


def bind(engine: Engine) -> None:
    """Bring the ``kn_`` tables up to the models: missing tables, then missing columns."""
    Base.metadata.create_all(engine)
    sync_additive_columns(engine, Base.metadata)
