"""Changing a place's slug moves everything filed under it.

`slug` is a foreign key held by four things that are not the database — the
`place:` subject in `observations.md`, the frozen subject on a pending memo card,
and the two seed files — so a rename is a small migration, not a field edit. The
refusals are pinned as hard as the successes: renaming onto a taken slug must not
quietly merge two restaurants' history.
"""
import pytest

from app import observations as obs, places
from app.db import Database
from app.models import Member, Place, Room


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    from app import memory as mem
    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def db():
    d = Database("sqlite:///:memory:")
    d.create_all()
    with d.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.flush()
        s.add(Member(id=1, room_id=1, display_name="Emi", nickname="emi"))
    return d


def _place(db, name, slug=None, **kw):
    with db.session() as s:
        p = places.create_place(s, 1, name=name, **kw)
        if slug:                      # a curated slug, as the seed files carry
            p.slug = slug
            s.flush()
        return p.id


def _write_obs(text):
    from app.memory import room_memory_dir
    (room_memory_dir(1) / "observations.md").write_text(text, encoding="utf-8")


def _read_obs():
    from app.memory import room_memory_dir
    return (room_memory_dir(1) / "observations.md").read_text(encoding="utf-8")


def _rename(db, place_id, slug):
    with db.session() as s:
        return places.rename_slug(s, 1, place_id, slug)


# --------------------------------------------------------------------- refusals

def test_an_empty_or_punctuation_only_slug_is_refused(db):
    pid = _place(db, "Quán Bé Bự")
    for bad in ("", "   ", "!!!", "---"):
        with pytest.raises(places.PlaceError, match="Cannot build an identifier"):
            _rename(db, pid, bad)
    with db.session() as s:
        assert s.get(Place, pid).slug == "quan-be-bu"


def test_renaming_onto_a_taken_slug_is_refused_naming_the_holder(db):
    """Not a merge. Merging two places also has to move `meals.place_id` and
    reconcile two histories, and doing it by accident would be silent."""
    pid = _place(db, "Bún chả rửa xe")
    _place(db, "Bún chả Hương Liên")

    with pytest.raises(places.PlaceError, match="Bún chả Hương Liên"):
        _rename(db, pid, "bun-cha-huong-lien")

    with db.session() as s:
        assert s.get(Place, pid).slug == "bun-cha-rua-xe"


def test_renaming_onto_another_places_former_slug_is_refused(db):
    """One slug must resolve to one row. Allowing this would make `SubjectIndex`
    and `seed_places.load_file` both ambiguous."""
    other = _place(db, "Cơm gà Thịnh Lơ")
    _rename(db, other, "com-ga-moi")
    pid = _place(db, "Quán Bé Bự")

    with pytest.raises(places.PlaceError, match="Cơm gà Thịnh Lơ"):
        _rename(db, pid, "com-ga-thinh-lo")


def test_a_place_from_another_room_is_not_found(db):
    with db.session() as s:
        s.add(Room(id=2, name="other", invite_token="o"))
        s.flush()
        p = Place(room_id=2, slug="x", name="X")
        s.add(p)
        s.flush()
        other_id = p.id
    with pytest.raises(places.PlaceError, match="No such place"):
        _rename(db, other_id, "y")


# ------------------------------------------------------------------- no-op path

def test_a_rename_to_the_same_slug_changes_nothing_at_all(db):
    """"Quán Bé Bự" → "quán bé bự" is the same identity. A rewrite would churn
    every line's content-derived `line_id` for no reason."""
    pid = _place(db, "Quán Bé Bự")
    _write_obs("- always | place:quan-be-bu | - | Ăn được.\n")
    before = _read_obs()

    out = _rename(db, pid, "  Quán Bé Bự  ")

    assert out["changed"] is False
    assert _read_obs() == before
    with db.session() as s:
        assert s.get(Place, pid).former_slugs == []


# --------------------------------------------------------------------- the move

def test_a_rename_moves_the_notes_and_remembers_the_old_slug(db):
    pid = _place(db, "Bún chả rửa xe Nam Đồng")
    _write_obs("# tay viết\n"
               "- always | place:bun-cha-rua-xe-nam-dong | busy@12:00 | Đông lúc 12h.\n"
               "- 2026-08-10 | place:bun-cha-rua-xe-nam-dong | - | Hết chả.\n"
               "- always | member:emi | - | Thích bún chả.\n")

    out = _rename(db, pid, "bun-cha-huong-lien")

    assert out["changed"] is True
    assert (out["slug"], out["former_slug"]) == ("bun-cha-huong-lien",
                                                 "bun-cha-rua-xe-nam-dong")
    assert out["notes_moved"] == 2
    with db.session() as s:
        p = s.get(Place, pid)
        assert p.slug == "bun-cha-huong-lien"
        assert p.former_slugs == ["bun-cha-rua-xe-nam-dong"]

    lines = _read_obs().splitlines()
    assert lines[0] == "# tay viết"                      # comment survives
    assert lines[3] == "- always | member:emi | - | Thích bún chả."
    assert [o.subject for o in obs.load(1)] == [
        "place:bun-cha-huong-lien", "place:bun-cha-huong-lien", "member:emi"]


def test_the_typed_slug_goes_through_slugify(db):
    """Never a hand-rolled normaliser: `roster._fold` maps đ→d by hand because
    NFD leaves it whole."""
    pid = _place(db, "Quán Bé Bự")
    out = _rename(db, pid, "  Bún Đậu Cô Tư  ")
    assert out["slug"] == "bun-dau-co-tu"


def test_renaming_back_retires_the_slug_from_the_former_list(db):
    """One row must not answer to one slug twice — that is the same ambiguity the
    cross-place check refuses, wearing a different hat."""
    pid = _place(db, "Quán Bé Bự")
    _rename(db, pid, "be-bu")
    _rename(db, pid, "quan-be-bu")

    with db.session() as s:
        p = s.get(Place, pid)
        assert p.slug == "quan-be-bu"
        assert p.former_slugs == ["be-bu"]


def test_a_rename_with_no_notes_still_succeeds(db):
    pid = _place(db, "Quán Bé Bự")
    out = _rename(db, pid, "be-bu")
    assert (out["changed"], out["notes_moved"], out["memos_moved"]) == (True, 0, 0)


def test_a_pending_memo_follows_the_rename_and_commits_onto_the_new_slug(db):
    """The trap this exists for. `memos.create` freezes the subject and
    `memos.commit` reads it back, so a card left pending across a rename would
    write its line under a slug the place no longer has. Production had one such
    card pending (`place:bun-rieu-co-trang`) when this was written."""
    from datetime import date

    from app import memos

    pid = _place(db, "Bún riêu cô Trang")
    with db.session() as s:
        m = memos.create(s, 1, action="add", subject="place:bun-rieu-co-trang",
                         subject_label="Bún riêu cô Trang",
                         text="Gọi giá tính tiền", when=date(2026, 8, 14))
        memo_id = m.id

    out = _rename(db, pid, "bun-rieu-truong-sa")
    assert out["memos_moved"] == 1

    with db.session() as s:
        memos.commit(s, memo_id, 1)

    assert [o.subject for o in obs.load(1)] == ["place:bun-rieu-truong-sa"]


def test_a_moved_note_that_would_collide_is_deduped_not_duplicated(db):
    """Two byte-identical lines share a `line_id` and neither can be addressed
    again — the same rule the knowledge API enforces on POST and PATCH."""
    pid = _place(db, "Quán Bé Bự")
    _write_obs("- always | place:quan-be-bu | - | Ăn được.\n"
               "- always | place:be-bu | - | Ăn được.\n")

    out = _rename(db, pid, "be-bu")

    assert (out["notes_moved"], out["notes_deduped"]) == (0, 1)
    ids = [o.line_id for o in obs.load(1)]
    assert len(ids) == len(set(ids)) == 1
