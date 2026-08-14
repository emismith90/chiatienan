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


def test_lint_rejects_the_bare_co_trang_alias_too():
    """No exceptions. The operator's rule: "Bún riêu cô Trang" is a full
    restaurant name, and a bare "cô Trang" is a person — two of them in this room
    (DINH HONG TRANG, BUI THU TRANG). An earlier version excepted this alias on
    the argument that it was real speech; that let a note about a colleague be
    filed against a bún riêu shop, so the exception is gone.
    """
    bad = [{"name": "Bún riêu cô Trang", "aliases": ["cô trang", "co trang"]}]
    problems = seed_places.lint(bad, member_names=["Nhím", "DINH HONG TRANG"])
    assert problems, "a bare member-name alias must be rejected"


def test_lint_allows_the_full_restaurant_name_as_an_alias():
    """The multi-word form is unambiguous and must stay usable."""
    ok = [{"name": "Bún riêu cô Trang",
           "aliases": ["bún riêu cô trang", "bun rieu co trang"]}]
    assert seed_places.lint(ok, member_names=["Nhím", "DINH HONG TRANG"]) == []


def test_shipped_seeds_pass_the_lint():
    rows = []
    for name in SEED_FILES:
        rows += json.loads((SEEDS / name).read_text(encoding="utf-8"))
    assert seed_places.lint(rows, member_names=[
        "Giang", "Linh", "Nhím",
        "HOANG MINH GIANG", "NGUYEN ANH LINH", "DINH HONG TRANG",
    ]) == []


def test_main_runs_end_to_end_as_the_cli_does(db, tmp_path, monkeypatch, capsys):
    """Exercise `main()` end to end: rc, output, and the observations half.

    Note this does NOT catch the definition-order bug that hit production on
    2026-08-14 — verified by reintroducing it, and this test still passed. pytest
    imports the module fully before calling `main()`, so a definition below the
    `__main__` guard resolves anyway. The failure exists only under `python -m`,
    where the guard fires *during* module execution.
    `test_every_public_name_main_uses_is_defined_before_the_guard` is what guards
    that; this one covers everything else about the CLI flow.
    """
    from app import db as db_mod
    from app import memory as mem
    from app import observations, seed_places

    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path)
    monkeypatch.setattr(db_mod, "get_db", lambda: db)

    rc = seed_places.main(["app.seed_places", "1",
                           str(SEEDS / "places-local.json")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "backfill:" in out
    # The observations half must actually have run, not merely been reachable.
    assert "observations:" in out
    assert len(observations.load(1)) == 42, out


def test_every_public_name_main_uses_is_defined_before_the_guard():
    """Structural guard: nothing main() calls may be defined after __main__."""
    import inspect

    from app import seed_places

    src = inspect.getsource(seed_places)
    guard = src.index('if __name__ == "__main__":')
    for name in ("lint", "load_file", "install_observations", "main"):
        assert src.index(f"def {name}(") < guard, \
            f"{name} is defined after the __main__ guard — main() cannot see it"


# ================================================ re-seeding after a slug rename
#
# The failure nobody notices for months: the seed files pin `slug` on all 100
# rows and `_FIELDS` excludes it, so a row still pinned to a pre-rename slug used
# to miss its place, create a second one carrying the old slug, and leave
# `backfill_links` to link meals to whichever the matcher preferred. Production's
# room 3 is byte-identical in slug to `seeds/*.json`, so this fires on the first
# reseed after any rename — it is not hypothetical.

@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    from app import memory as mem
    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path / "rooms")
    return tmp_path


def test_reseeding_with_the_old_pinned_slug_updates_instead_of_duplicating(db, tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps([
        {"name": "Bún chả rửa xe Nam Đồng", "slug": "bun-cha-rua-xe", "tags": ["bún"]},
    ]), encoding="utf-8")
    with db.session() as s:
        seed_places.load_file(s, 1, f)

    with db.session() as s:
        pid = places.list_places(s, 1)[0].id
        places.rename_slug(s, 1, pid, "bun-cha-huong-lien")

    # The JSON is unchanged — it still pins the old slug, as a repo file would.
    with db.session() as s:
        assert seed_places.load_file(s, 1, f) == {"created": 0, "updated": 1}

    with db.session() as s:
        rows = places.list_places(s, 1)
        assert len(rows) == 1, "a rename must not manufacture a second place"
        assert rows[0].slug == "bun-cha-huong-lien"      # the DB owns identity
        assert rows[0].tags == ["bún"]                   # the file owns the rest


def test_a_live_slug_beats_another_places_former_one(db, tmp_path):
    """If one place used to hold `x` and another holds it now, the seed row for
    `x` belongs to the live holder."""
    f = tmp_path / "p.json"
    f.write_text(json.dumps([{"name": "First", "slug": "shared"}]), encoding="utf-8")
    with db.session() as s:
        seed_places.load_file(s, 1, f)
        first = places.list_places(s, 1)[0].id
        places.rename_slug(s, 1, first, "first-moved")
        second = places.create_place(s, 1, name="Second")
        second.slug = "shared"
        s.flush()
        second_id = second.id

    with db.session() as s:
        seed_places.load_file(s, 1, f)
    with db.session() as s:
        assert len(places.list_places(s, 1)) == 2
        from app.models import Place
        assert s.get(Place, second_id).name == "First"   # the live holder was updated


def test_install_observations_does_not_re_add_a_note_under_a_renamed_slug(db, tmp_path):
    """A seed line naming a pre-rename slug looks like a fact the room lacks,
    because the live file now says the new slug — so every re-seed would add one
    more orphan."""
    from app import observations as obs

    seed = tmp_path / "observations-local.md"
    seed.write_text("- always | place:bun-cha-rua-xe | - | Ăn được.\n", encoding="utf-8")
    pf = tmp_path / "p.json"
    pf.write_text(json.dumps([{"name": "Bún chả", "slug": "bun-cha-rua-xe"}]),
                  encoding="utf-8")

    with db.session() as s:
        seed_places.load_file(s, 1, pf)
        assert seed_places.install_observations(1, seed, s) == {"added": 1, "skipped": 0}
        pid = places.list_places(s, 1)[0].id
        places.rename_slug(s, 1, pid, "bun-cha-huong-lien")

    with db.session() as s:
        assert seed_places.install_observations(1, seed, s) == {"added": 0, "skipped": 1}

    rows = obs.load(1)
    assert [o.subject for o in rows] == ["place:bun-cha-huong-lien"]


def test_install_observations_without_a_session_still_works(db, tmp_path):
    """The canonicalisation is an improvement, not a requirement — callers that
    have no session (the tests that predate this) keep working."""
    seed = tmp_path / "observations-local.md"
    seed.write_text("- always | place:x | - | Ăn được.\n", encoding="utf-8")
    assert seed_places.install_observations(1, seed) == {"added": 1, "skipped": 0}
