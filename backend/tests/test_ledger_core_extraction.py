"""The money domain lives in ``ledger_core`` (plan Task 3.2) and nothing the host
or the tests relied on moved out of reach."""
import pytest
from sqlalchemy import create_engine, inspect, text

import ledger_core
from app import debug_api, drafts, ledger, models, money, notes, periods, qr, roster
from ledger_core import members as core_members


def test_app_shims_re_export_everything_including_private_names():
    assert ledger.record_meal is ledger_core.ledger.record_meal and ledger.today_ict
    assert roster._fold is ledger_core.roster._fold and roster.resolve is ledger_core.roster.resolve
    assert money.DebtEdge is ledger_core.money.DebtEdge and notes._weekday_label
    assert periods.resolve_period is ledger_core.periods.resolve_period
    assert drafts._EDITABLE is ledger_core.drafts.EDITABLE and drafts.DRAFT_KINDS == ("expense_draft", "payment_draft")
    assert models.Meal is ledger_core.models.Meal and models.Payment.__table__.metadata is ledger_core.models.Base.metadata
    assert qr.QRError is ledger_core.qr.QRError


def test_ledger_tables_stay_in_the_debug_export_api():
    assert {"meals", "meal_shares", "payments", "settlements"} <= set(debug_api.dumpable_tables())
    assert "kn_profile_versions" in debug_api.dumpable_tables()


def test_payment_ref_kind_is_additive_with_a_literal_default(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/old.db", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE payments (id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL, "
                          "from_member_id INTEGER NOT NULL, to_member_id INTEGER NOT NULL, amount INTEGER NOT NULL, "
                          "occurred_on DATE NOT NULL, meal_id INTEGER, note VARCHAR(400), source VARCHAR(20) NOT NULL, "
                          "logged_by VARCHAR(120), voided BOOLEAN NOT NULL, voided_by VARCHAR(120), voided_at DATETIME, created_at DATETIME)"))
        conn.execute(text("INSERT INTO payments (room_id, from_member_id, to_member_id, amount, occurred_on, source, voided) "
                          "VALUES (1, 1, 2, 5, '2026-09-01', 'web', 0)"))
    ledger_core.bind(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("payments")}
    assert "ref_kind" in cols
    with engine.begin() as conn:
        assert conn.execute(text("SELECT ref_kind FROM payments")).scalar() == "meal"


def test_ledger_core_needs_a_configured_directory(monkeypatch):
    # The host configures the directory when `app.models` is imported…
    assert isinstance(core_members.directory(), core_members.SqlMemberDirectory)
    # …and an unconfigured core says so instead of failing obscurely. monkeypatch
    # restores the directory; the clock provider is deliberately left alone — it
    # reads `app.clock.now_ict` at call time, which is what `frozen_clock` patches.
    monkeypatch.setattr(core_members, "_directory", core_members._Unconfigured())
    with pytest.raises(RuntimeError, match="configure"):
        core_members.directory().names(None, 1)


def test_directory_reads_the_host_member_model(db):
    from tests.test_ledger import _seed_room
    room_id, m = _seed_room(db, 2)
    d = core_members.directory()
    with db.session() as s:
        assert d.ids_in_space(s, room_id, m + [999]) == set(m)
        assert d.get(s, room_id, m[0]).id == m[0] and d.get(s, room_id + 1, m[0]) is None
        assert set(d.names(s, room_id).values()) == {"M1", "M2"}
        assert [x.id for x in d.list(s, room_id)] == m


def test_fresh_install_meals_reference_rooms_without_a_db_constraint(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/fresh.db", future=True)
    ledger_core.bind(engine)
    fks = inspect(engine).get_foreign_keys("meals")
    assert fks == []                                  # decision 2: plain indexed integers
    assert inspect(engine).get_foreign_keys("meal_shares")[0]["referred_table"] == "meals"
