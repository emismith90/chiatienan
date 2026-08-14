from datetime import date

import pytest

from app import places
from app.db import Database
from app.models import Meal, Member, Place, Room


@pytest.fixture()
def db():
    d = Database("sqlite:///:memory:")
    d.create_all()
    with d.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.flush()
        s.add(Member(id=1, room_id=1, display_name="An", nickname="an"))
        s.add(Place(room_id=1, slug="bun-cha-rua-xe", name="Bún chả rửa xe Nam Đồng",
                    aliases=["bún chả rửa xe", "rửa xe"]))
        s.add(Place(room_id=1, slug="banh-cuon-ba-hoanh", name="Bánh cuốn Bà Hoành"))
        s.add(Place(room_id=1, slug="banh-cuon-ba-xuan", name="Bánh cuốn Bà Xuân"))
    return d


def _meal(s, dish, **kw):
    m = Meal(room_id=1, occurred_on=date(2026, 8, 1), payer_member_id=1,
             total_amount=100000, dish=dish, guests=[], **kw)
    s.add(m)
    s.flush()
    return m


def test_links_confident_matches_only(db):
    with db.session() as s:
        confident = _meal(s, "bún chả rửa xe")
        ambiguous = _meal(s, "bánh cuốn")
        unknown = _meal(s, "sushi bar")
        res = places.backfill_links(s, 1)
        assert res == {"linked": 1, "skipped": 1, "ambiguous": 1}
        assert s.get(Meal, confident.id).place_id is not None
        assert s.get(Meal, ambiguous.id).place_id is None
        assert s.get(Meal, unknown.id).place_id is None


def test_refuses_the_token_tier(db):
    # Only a token-subset match. No card, no human review — a silent wrong link
    # moves money history onto the wrong restaurant.
    with db.session() as s:
        m = _meal(s, "rửa xe bún chả nam đồng hôm qua")
        places.backfill_links(s, 1)
        assert s.get(Meal, m.id).place_id is None


def test_is_idempotent_and_never_relinks(db):
    with db.session() as s:
        _meal(s, "bún chả rửa xe")
        first = places.backfill_links(s, 1)
        second = places.backfill_links(s, 1)
        assert first["linked"] == 1
        assert second["linked"] == 0, "an already-linked meal must be left alone"


def test_skips_voided_and_dishless_meals(db):
    with db.session() as s:
        voided = _meal(s, "bún chả rửa xe", voided=True)
        _meal(s, None)
        res = places.backfill_links(s, 1)
        assert res["linked"] == 0
        assert s.get(Meal, voided.id).place_id is None
