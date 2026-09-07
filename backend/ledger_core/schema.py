"""Create the ledger tables next to a host's, additively (same discipline as kernos)."""
from __future__ import annotations

from sqlalchemy.engine import Engine

from kernos.content.schema import sync_additive_columns
from ledger_core.models import Base


def bind(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    sync_additive_columns(engine, Base.metadata)
