import json
from pathlib import Path

import pytest

from app import places, seed_places
from app.db import Database
from app.models import Room

SEEDS = Path(__file__).resolve().parents[1] / "seeds"
SEED_FILES = ("places-local.json", "places-nearby.json", "places-online.json")


@pytest.fixture()
def db():
    d = Database("sqlite:///:memory:")
    d.create_all()
    with d.session() as s:
        s.add(Room(id=1, name="test", invite_token="t"))
        s.flush()
    return d


def test_loads_and_is_idempotent(db, tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps([
        {"name": "Phở Vui", "aliases": ["vui"], "tags": ["phở"], "walkable": True},
    ]), encoding="utf-8")
    with db.session() as s:
        assert seed_places.load_file(s, 1, f) == {"created": 1, "updated": 0}
    with db.session() as s:
        assert seed_places.load_file(s, 1, f) == {"created": 0, "updated": 1}
        assert len(places.list_places(s, 1)) == 1


def test_derives_slug_and_keeps_explicit_one(db, tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps([
        {"name": "Phở Vui"},
        {"name": "Quán Bé Bự - Khoai Tây", "slug": "be-bu"},
    ]), encoding="utf-8")
    with db.session() as s:
        seed_places.load_file(s, 1, f)
        assert {p.slug for p in places.list_places(s, 1)} == {"pho-vui", "be-bu"}


def test_real_seed_files_load_clean_with_unique_slugs(db):
    total = 0
    with db.session() as s:
        for name in SEED_FILES:
            total += seed_places.load_file(s, 1, SEEDS / name)["created"]
        rows = places.list_places(s, 1, include_inactive=True)
    assert total == 100, total
    assert len({p.slug for p in rows}) == 100
    assert sum(1 for p in rows if p.walkable) == 76


def test_closed_until_is_parsed_as_a_date(db):
    from datetime import date
    with db.session() as s:
        seed_places.load_file(s, 1, SEEDS / "places-local.json")
        vui = next(p for p in places.list_places(s, 1) if p.slug == "pho-vui")
        assert vui.closed_until == date(2026, 9, 30)


def test_lint_rejects_a_place_alias_that_is_a_bare_member_name():
    # D18: "anh Linh" in Vietnamese means the person, and this room has a Linh.
    bad = [{"name": "Bún Đậu Anh Linh", "aliases": ["anh linh"]}]
    problems = seed_places.lint(bad, member_names=["Giang", "Linh", "Nhím"])
    assert problems and "anh linh" in problems[0]


def test_lint_allows_the_documented_co_trang_exception():
    ok = [{"name": "Bún riêu cô Trang", "aliases": ["cô trang", "co trang"]}]
    assert seed_places.lint(ok, member_names=["Nhím", "DINH HONG TRANG"]) == []


def test_shipped_seeds_pass_the_lint():
    rows = []
    for name in SEED_FILES:
        rows += json.loads((SEEDS / name).read_text(encoding="utf-8"))
    assert seed_places.lint(rows, member_names=[
        "Giang", "Linh", "Nhím",
        "HOANG MINH GIANG", "NGUYEN ANH LINH", "DINH HONG TRANG",
    ]) == []
