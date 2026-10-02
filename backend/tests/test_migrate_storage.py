"""The one-time import of places and notes (plan 2026-10-02, release A, S5)."""
from datetime import date, datetime

import pytest

from app import migrate_storage, observations, places
from app.models import LegacyPlace, Room
from tests.notes_util import notes

FILE = ("# viết tay\n"
        "- always | place:be-bu | busy@12:00 | Đông lúc 12h.\n"
        "this line does not parse\n"
        "\n"
        "- 2026-08-10 | place:be-bu | - | Hết gà.\n"
        "- always | place:be-bu | busy@12:00 | Đông lúc 12h.\n"          # an exact duplicate
        "- always | member:nhim | - | Đề xuất rồi đổi ý.\n")


@pytest.fixture(autouse=True)
def room_files(tmp_path, monkeypatch):
    from app import memory as mem
    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def legacy(db):
    """A production-shaped database: two rooms, legacy place rows, one notes file."""
    with db.session() as s:
        s.add_all([Room(id=1, name="a", invite_token="a"), Room(id=3, name="b", invite_token="b")])
        s.flush()
        s.add_all([
            LegacyPlace(id=57, room_id=3, slug="be-bu", name="Quán Bé Bự - Khoai Tây", aliases=["bé bự"],
                        tags=["cơm"], delivery=["grab"], former_slugs=["quan-be-bu"], walkable=True,
                        walk_minutes=7, phone="0912345678", price_hint=45000, closed_until=date(2026, 11, 1),
                        active=True, created_at=datetime(2026, 7, 22, 10, 31, 23)),
            LegacyPlace(id=101, room_id=3, slug="pho-ha", name="Phở Hà", walkable=False, active=False,
                        created_at=datetime(2026, 8, 1, 9, 0, 0)),
            LegacyPlace(id=12, room_id=1, slug="x", name="X", created_at=datetime(2026, 7, 22, 10, 0, 0)),
        ])
    observations.legacy_path(3).write_text(FILE, encoding="utf-8")
    return db


def test_apply_imports_every_place_with_its_id_and_every_note_in_order(legacy):
    status, report = migrate_storage.run(legacy, apply=True)
    assert status == 0 and report["status"] == "applied", report
    with legacy.session() as s:
        bebu = places.get_place(s, 3, 57)
        assert bebu.slug == "be-bu" and bebu.former_slugs == ["quan-be-bu"] and bebu.walk_minutes == 7
        assert bebu.closed_until == date(2026, 11, 1) and bebu.created_at == datetime(2026, 7, 22, 10, 31, 23)
        assert places.get_place(s, 3, 101).active is False
        assert places.get_place(s, 1, 12).name == "X"
        assert places.create_place(s, 3, name="Mới").id == 102      # above every legacy id
    assert [o.text for o in notes(legacy, 3)] == ["Đông lúc 12h.", "Hết gà.", "Đề xuất rồi đổi ý."]
    room3 = report["notes"]["3"]
    assert room3 == {"imported": 3, "duplicates": 1, "comments": 1, "blank": 1,
                     "dropped": ["line 3: this line does not parse"]}
    # the file is retired, not deleted; the legacy table is untouched
    assert not observations.legacy_path(3).exists()
    assert [p.name for p in observations.legacy_path(3).parent.iterdir()] == [
        f"observations.md.imported-{date.today().isoformat()}"]
    with legacy.session() as s:
        assert s.query(LegacyPlace).count() == 3


def test_a_second_apply_is_a_no_op(legacy):
    assert migrate_storage.run(legacy, apply=True)[0] == 0
    status, report = migrate_storage.run(legacy, apply=True)
    assert (status, report) == (0, {"status": "already applied"})
    assert len(notes(legacy, 3)) == 3


def test_check_imports_verifies_and_rolls_back(legacy):
    status, report = migrate_storage.run(legacy, apply=False)
    assert status == 0 and report["status"].startswith("check passed"), report
    with legacy.session() as s:
        assert places.list_places(s, 3, include_inactive=True) == []
    assert notes(legacy, 3) == []
    assert observations.legacy_path(3).exists()                     # nothing touched


def test_any_difference_refuses_and_writes_nothing(legacy, monkeypatch):
    """A self-check failure must leave production exactly as it was."""
    real = places._save

    def off_by_one(session, p, *, create=False):
        from dataclasses import replace
        return real(session, replace(p, price_hint=(p.price_hint or 0) + 1), create=create)

    with monkeypatch.context() as m:
        m.setattr(places, "_save", off_by_one)
        status, report = migrate_storage.run(legacy, apply=True)
    assert status == 1 and report["status"].startswith("refused"), report
    assert any("place 57" in p for p in report["problems"])
    with legacy.session() as s:
        assert places.list_places(s, 3, include_inactive=True) == []
        assert migrate_storage._done(s) is None
    assert observations.legacy_path(3).exists()


def test_a_room_without_a_file_imports_no_notes(db):
    with db.session() as s:
        s.add(Room(id=1, name="a", invite_token="a"))
    status, report = migrate_storage.run(db, apply=True)
    assert status == 0 and report["notes"] == {} and report["places"] == {}
