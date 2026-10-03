"""Places on the `places` store (plan 2026-10-02, S3; review R4, R11, R13)."""
from dataclasses import FrozenInstanceError
from datetime import date

import pytest
from sqlalchemy import text

from app import places
from tests.places_util import add_place


def test_a_new_place_gets_an_id_above_every_legacy_id(db):
    """`meals.place_id` holds the old table's ids; a new place must never reuse one."""
    from app.models import Room
    with db.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.flush()
        s.execute(text("INSERT INTO places (id, room_id, slug, name, former_slugs, aliases, tags, delivery, "
                       "walkable, active, created_at) VALUES (101, 1, 'old', 'Old', '[]', '[]', '[]', '[]', 1, 1, '2026-07-22 10:00:00')"))
    with db.session() as s:
        assert places.create_place(s, 1, name="Mới").id == 102
        assert places.create_place(s, 1, name="Mới nữa").id == 103


def test_two_places_of_one_room_cannot_share_a_slug_but_two_rooms_can(db):
    with db.session() as s:
        places.create_place(s, 1, name="Phở Hà")
        with pytest.raises(places.PlaceError, match="already on the list"):
            places.create_place(s, 1, name="Phở Hà")
        with pytest.raises(places.PlaceError, match="already taken"):     # the store itself refuses
            add_place(s, room_id=1, slug="pho-ha", name="Another")
        assert places.create_place(s, 2, name="Phở Hà").slug == "pho-ha"


def test_a_place_is_a_frozen_record(db):
    with db.session() as s:
        p = places.create_place(s, 1, name="Bún")
    with pytest.raises(FrozenInstanceError):
        p.active = False          # a missed save fails loudly instead of doing nothing


def test_edit_place_saves_only_a_real_change(db):
    with db.session() as s:
        p = places.create_place(s, 1, name="Bún", tags=["bún"])
    with db.session() as s:
        same, changed = places.edit_place(s, p, {"tags": [" bún "]})
        assert changed is False and same == p
        moved, changed = places.edit_place(s, p, {"closed_until": "2026-10-10", "phone": " 0912 "})
        assert changed is True
    with db.session() as s:
        back = places.get_place(s, 1, p.id)
    assert back.closed_until == date(2026, 10, 10) and back.phone == "0912" and back == moved


def test_list_places_orders_by_name_then_id_and_hides_inactive(db):
    with db.session() as s:
        b = places.create_place(s, 1, name="Bánh cuốn")
        a = places.create_place(s, 1, name="Bánh canh")
        z = places.create_place(s, 1, name="Ăn vặt")          # 'Ă' sorts after 'B' by code point, as in SQL
        places.edit_place(s, b, {"active": False})
    with db.session() as s:
        assert [p.name for p in places.list_places(s, 1)] == ["Bánh canh", "Ăn vặt"]
        assert [p.id for p in places.list_places(s, 1, include_inactive=True)] == [a.id, b.id, z.id]


def test_a_place_round_trips_every_field(db):
    with db.session() as s:
        p = places.create_place(s, 1, name="Quán Bé Bự", aliases=["bé bự"], tags=["cơm"], delivery=["grab"],
                                address="12 Hàng Bông", walkable=False, walk_minutes=8, phone="0912345678",
                                price_hint=45000, closed_until=date(2026, 12, 1))
    with db.session() as s:
        assert places.get_place(s, 1, p.id) == p
        assert places.get_place(s, 2, p.id) is None         # rooms do not see each other's places
