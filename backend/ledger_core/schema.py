"""Create the ledger tables next to a host's, additively (same discipline as kernos),
and declare the ledger journal those tables are mirrored into (plan 2026-10-02, B)."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from kernos.content.schema import bind as bind_content
from kernos.content.schema import sync_additive_columns
from ledger_core.models import Base


def session_factory(engine: Engine):
    """A ``with factory() as s:`` scope that commits on success — what the data plane's
    ``ensure_internal`` expects."""
    make = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def scope():
        s = make()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()
    return scope


def bind(engine: Engine) -> None:
    """Tables, then the journal (which lives on the content plane, bound first), then the
    mirror that keeps the journal in step with the tables. Idempotent."""
    from kernos.data import DataStore, ensure_internal
    from ledger_core import journal

    bind_content(engine)
    Base.metadata.create_all(engine)
    sync_additive_columns(engine, Base.metadata)
    factory = session_factory(engine)
    ensure_internal(DataStore(factory), factory, [journal.LEDGER])
    journal.register_ledger()
