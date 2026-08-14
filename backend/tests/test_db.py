"""Schema bring-up: ``create_all`` must add missing *columns*, not just tables.

The regression these cover is a real outage. PR #34 added
``members.default_participant``; `create_all()` skipped the already-existing
`members` table, so every member SELECT on the live DB raised
``no such column`` and the room rendered empty — the group's ledger looked
wiped while all 265 messages and 7 members sat untouched on disk.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import text

from app.db import Database, _ddl_default, _sql_literal
from app.models import Member, Room


def _legacy_db(tmp_path, drop: str, table: str = "members") -> str:
    """A DB at the current schema minus ``table.drop`` — i.e. a pre-migration DB."""
    url_path = tmp_path / "legacy.db"
    Database(f"sqlite:///{url_path}").create_all()
    with sqlite3.connect(url_path) as raw:
        raw.execute(f"ALTER TABLE {table} DROP COLUMN {drop}")
    return str(url_path)


def _columns(url_path, table: str = "members") -> set[str]:
    with sqlite3.connect(url_path) as raw:
        return {r[1] for r in raw.execute(f"PRAGMA table_info({table})")}


def test_create_all_adds_a_column_missing_from_an_existing_table(tmp_path):
    path = _legacy_db(tmp_path, "default_participant")
    assert "default_participant" not in _columns(path)

    with sqlite3.connect(path) as raw:
        raw.execute(
            "INSERT INTO rooms (name, invite_token, created_at)"
            " VALUES ('12B', 'tok', '2026-07-22 10:31:23')"
        )
        raw.execute(
            "INSERT INTO members (room_id, display_name, nickname, pin, aliases, active, created_at)"
            " VALUES (1, 'An', 'an', '1234', '[]', 1, '2026-07-22 10:31:23')"
        )

    Database(f"sqlite:///{path}").create_all()
    assert "default_participant" in _columns(path)

    # The pre-existing row survived and picked up the model's default, so the
    # ORM read that used to raise `no such column` now works.
    db = Database(f"sqlite:///{path}")
    with db.session() as s:
        m = s.query(Member).one()
        assert m.display_name == "An"
        assert m.default_participant is True


def test_added_not_null_column_keeps_its_not_null_and_default(tmp_path):
    path = _legacy_db(tmp_path, "default_participant")
    Database(f"sqlite:///{path}").create_all()

    with sqlite3.connect(path) as raw:
        info = {r[1]: r for r in raw.execute("PRAGMA table_info(members)")}
    _, _, coltype, notnull, dflt, _ = info["default_participant"]
    assert (coltype, notnull, dflt) == ("BOOLEAN", 1, "1")


def test_create_all_is_idempotent_and_leaves_a_current_db_alone(tmp_path):
    path = tmp_path / "current.db"
    db = Database(f"sqlite:///{path}")
    db.create_all()
    before = _columns(path)

    db.create_all()  # second pass: nothing missing, so nothing to do
    Database(f"sqlite:///{path}").create_all()
    assert _columns(path) == before


def test_a_nullable_column_is_added_without_a_not_null_clause(tmp_path):
    path = _legacy_db(tmp_path, "account_holder")
    Database(f"sqlite:///{path}").create_all()

    with sqlite3.connect(path) as raw:
        info = {r[1]: r for r in raw.execute("PRAGMA table_info(members)")}
    assert info["account_holder"][3] == 0  # notnull flag stays off
    assert "account_holder" in _columns(path)


def test_not_null_column_with_a_callable_default_is_added_nullable_and_warns(tmp_path, caplog):
    # `created_at` defaults to now_ict() — a Python callable with no DDL literal.
    # Adding it nullable is the deliberate degradation: a NULL in old rows beats
    # a table the app cannot read.
    path = _legacy_db(tmp_path, "created_at")
    with caplog.at_level("WARNING", logger="chiatienan.db"):
        Database(f"sqlite:///{path}").create_all()

    assert "created_at" in _columns(path)
    with sqlite3.connect(path) as raw:
        info = {r[1]: r for r in raw.execute("PRAGMA table_info(members)")}
    assert info["created_at"][3] == 0
    assert "members.created_at" in caplog.text


def test_migration_does_not_drop_a_column_the_models_no_longer_have(tmp_path):
    """Additive only: an extra live column is left in place, never dropped."""
    path = tmp_path / "extra.db"
    Database(f"sqlite:///{path}").create_all()
    with sqlite3.connect(path) as raw:
        raw.execute("ALTER TABLE members ADD COLUMN retired_field TEXT")

    Database(f"sqlite:///{path}").create_all()
    assert "retired_field" in _columns(path)


def test_missing_table_is_still_created_whole(tmp_path):
    path = tmp_path / "notable.db"
    Database(f"sqlite:///{path}").create_all()
    with sqlite3.connect(path) as raw:
        raw.execute("DROP TABLE settlements")

    Database(f"sqlite:///{path}").create_all()
    with sqlite3.connect(path) as raw:
        names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "settlements" in names


def test_a_lost_race_on_the_same_alter_is_treated_as_success(tmp_path, caplog, monkeypatch):
    """Two overlapping containers can both run the ALTER; the loser must not crash."""
    path = _legacy_db(tmp_path, "default_participant")
    db = Database(f"sqlite:///{path}")

    import app.db as db_mod

    # Let the "other container" win the ALTER, then hand the migration a stale
    # view of the schema — that is what puts us on the losing side of the race:
    # we still believe the column is missing and try to add it again.
    live = db_mod.inspect(db.engine)
    pre_alter = {t: live.get_columns(t) for t in live.get_table_names()}
    with sqlite3.connect(path) as raw:
        raw.execute("ALTER TABLE members ADD COLUMN default_participant BOOLEAN DEFAULT 1 NOT NULL")

    class StaleInspector:
        def get_table_names(self):
            return list(pre_alter)

        def get_columns(self, table):
            return pre_alter[table]

    monkeypatch.setattr(db_mod, "inspect", lambda _engine: StaleInspector())

    with caplog.at_level("INFO", logger="chiatienan.db"):
        db_mod._sync_additive_columns(db.engine)  # must not raise "duplicate column name"

    assert "already added concurrently" in caplog.text
    assert "default_participant" in _columns(path)


def test_a_real_alter_failure_is_not_swallowed(tmp_path, monkeypatch):
    path = _legacy_db(tmp_path, "default_participant")
    db = Database(f"sqlite:///{path}")

    from sqlalchemy.exc import OperationalError

    import app.db as db_mod

    def boom(_sql):
        raise OperationalError("ALTER", {}, Exception("database is locked"))

    monkeypatch.setattr(db_mod, "text", boom)
    with pytest.raises(OperationalError, match="database is locked"):
        db_mod._sync_additive_columns(db.engine)


@pytest.mark.parametrize(
    "value,expected",
    [(True, "1"), (False, "0"), (0, "0"), (7, "7"), ("hi", "'hi'"), ("O'x", "'O''x'"),
     (None, None), (1.5, None), ([], None)],
)
def test_sql_literal_renders_scalars_and_refuses_the_rest(value, expected):
    assert _sql_literal(value) == expected


def test_ddl_default_reads_scalar_defaults_and_skips_callables():
    assert _ddl_default(Member.__table__.c.default_participant) == "1"
    assert _ddl_default(Member.__table__.c.aliases) is None       # default=list
    assert _ddl_default(Room.__table__.c.created_at) is None      # default=now_ict
    assert _ddl_default(Member.__table__.c.bank_code) is None     # no default


def test_ddl_default_prefers_an_explicit_server_default():
    from sqlalchemy import Column, Integer, MetaData, Table

    t = Table("t", MetaData(), Column("n", Integer, server_default=text("42"), default=7))
    assert _ddl_default(t.c.n) == "42"


def test_former_slugs_is_added_to_an_existing_places_table(tmp_path):
    """`_sync_additive_columns` must ALTER it in, with `[]` for the rows already
    there — a nullable column would make every reader need `or []`, and the whole
    point of the column is that `seed_places` can trust it."""
    import sqlalchemy as sa
    from app.db import Database

    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text(
            "CREATE TABLE places (id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL,"
            " slug VARCHAR(60) NOT NULL, name VARCHAR(120) NOT NULL)"))
        c.execute(sa.text(
            "INSERT INTO places (room_id, slug, name) VALUES (1, 'be-bu', 'Quán Bé Bự')"))
    eng.dispose()

    db = Database(url)
    db.create_all()

    from app.models import Place
    with db.session() as s:
        p = s.scalars(sa.select(Place)).one()
        assert p.former_slugs == []
