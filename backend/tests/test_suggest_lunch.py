from datetime import date

import pytest

from app import places
from app.db import Database
from app.models import Meal, MealShare, Member, Place, Room
from app.tools import ToolContext, build_tools

TODAY = date(2026, 8, 14)


def _mk(db_rows):
    db = Database("sqlite:///:memory:")
    db.create_all()
    with db.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.flush()
        for i in (1, 2, 3):
            s.add(Member(id=i, room_id=1, display_name=f"M{i}", nickname=f"m{i}"))
        s.flush()
        for row in db_rows:
            s.add(Place(room_id=1, **row))
    return db


def _meal(s, place_id, on, total=90000, members=(1, 2, 3)):
    m = Meal(room_id=1, occurred_on=on, payer_member_id=members[0],
             total_amount=total, place_id=place_id, guests=[])
    m.shares = [MealShare(member_id=i, share_amount=total // len(members)) for i in members]
    s.add(m)
    s.flush()
    return m


# ---------------------------------------------------------------- resolve_best

def test_resolve_best_prefers_a_known_place_over_untried_ones():
    """D15: the directory import must not make the room's own haunt un-sayable."""
    db = _mk([
        # No bare "nem nướng" alias here: if one place claimed it the exact tier
        # would settle the lookup outright and never reach the tie-break. This is
        # the harder case where all three only match via the token tier.
        dict(id=10, slug="nem-nuong-nha-trang", name="Nem nướng Nha Trang"),
        dict(id=11, slug="co-le-nem-nuong", name="Cô Lê - Nem Nướng Nha Trang",
             tags=["chưa-thử"]),
        dict(id=12, slug="ps-nem-nuong", name="PS+ Nem Nướng Nha Trang",
             tags=["chưa-thử"]),
    ])
    with db.session() as s:
        # The full name folds to exactly one place, so it was never ambiguous.
        assert places.resolve_one(s, 1, "nem nướng nha trang")[0].slug == "nem-nuong-nha-trang"
        # The short form people actually type hits all three via the token tier...
        assert places.resolve_one(s, 1, "nem nướng")[1] == "ambiguous"
        # ...but only one is a place anybody has been to.
        p, tier = places.resolve_best(s, 1, "nem nướng")
        assert p.slug == "nem-nuong-nha-trang" and tier == "best"


def test_resolve_best_breaks_a_known_tie_by_meal_count():
    db = _mk([
        dict(id=10, slug="banh-cuon-a", name="Bánh cuốn A"),
        dict(id=11, slug="banh-cuon-b", name="Bánh cuốn B"),
    ])
    with db.session() as s:
        _meal(s, 10, date(2026, 8, 1))
        _meal(s, 10, date(2026, 8, 2))
        _meal(s, 11, date(2026, 8, 3))
        p, tier = places.resolve_best(s, 1, "bánh cuốn", today=TODAY)
        assert p.slug == "banh-cuon-a" and tier == "best"


def test_resolve_best_still_asks_on_a_genuine_tie():
    db = _mk([
        dict(id=10, slug="banh-cuon-a", name="Bánh cuốn A"),
        dict(id=11, slug="banh-cuon-b", name="Bánh cuốn B"),
    ])
    with db.session() as s:
        assert places.resolve_best(s, 1, "bánh cuốn", today=TODAY) == (None, "ambiguous")


# --------------------------------------------------------------- suggest_lunch

def _tools(db):
    return build_tools(ToolContext(db=db, room_id=1, sender_member_id=1))


def test_returns_a_ranked_list_when_nothing_has_history():
    """The day-one case: every seeded place has times=0 and must still rank."""
    db = _mk([dict(id=10, slug="a", name="A"), dict(id=11, slug="b", name="B")])
    res = _tools(db)["suggest_lunch"].execute({})
    assert res["ok"] and len(res["candidates"]) == 2


def test_recent_places_are_penalised():
    db = _mk([dict(id=10, slug="a", name="A"), dict(id=11, slug="b", name="B")])
    with db.session() as s:
        _meal(s, 10, date(2026, 8, 13))       # yesterday
        _meal(s, 11, date(2026, 6, 1))        # months ago
    res = _tools(db)["suggest_lunch"].execute({"today": TODAY.isoformat()})
    assert res["candidates"][0]["name"] == "B", "yesterday's place should not lead"


def test_budget_filters_by_band_and_keeps_unknown_bands():
    db = _mk([
        dict(id=10, slug="a", name="A", price_hint=30000),
        dict(id=11, slug="b", name="B", price_hint=70000),
        dict(id=12, slug="c", name="C", price_hint=200000),
        dict(id=13, slug="d", name="D"),                       # no band at all
    ])
    res = _tools(db)["suggest_lunch"].execute({"budget": "rẻ", "today": TODAY.isoformat()})
    got = {c["name"] for c in res["candidates"]}
    assert "A" in got and "C" not in got
    assert "D" in got, "an unknown band cannot be ruled out"


def test_delivery_only_places_are_hidden_from_a_walk_out_question():
    db = _mk([
        dict(id=10, slug="walk", name="Đi bộ", walkable=True),
        dict(id=11, slug="ship", name="Giao hàng", walkable=False,
             delivery=["shopeefood"]),
    ])
    res = _tools(db)["suggest_lunch"].execute({"today": TODAY.isoformat()})
    assert [c["name"] for c in res["candidates"]] == ["Đi bộ"]


def test_delivery_mode_shows_the_delivery_places():
    db = _mk([
        dict(id=10, slug="walk", name="Đi bộ", walkable=True),
        dict(id=11, slug="ship", name="Giao hàng", walkable=False,
             delivery=["shopeefood"]),
    ])
    res = _tools(db)["suggest_lunch"].execute({"delivery": True, "today": TODAY.isoformat()})
    assert res["mode"] == "delivery"
    assert "Giao hàng" in {c["name"] for c in res["candidates"]}


def test_closed_and_inactive_places_are_excluded():
    db = _mk([
        dict(id=10, slug="open", name="Mở"),
        dict(id=11, slug="reno", name="Đang sửa", closed_until=date(2026, 9, 30)),
        dict(id=12, slug="gone", name="Đóng hẳn", active=False),
        dict(id=13, slug="reopened", name="Sửa xong", closed_until=date(2026, 7, 1)),
    ])
    res = _tools(db)["suggest_lunch"].execute({"today": TODAY.isoformat()})
    got = {c["name"] for c in res["candidates"]}
    assert got == {"Mở", "Sửa xong"}, "a past closed_until must expire on its own"


def test_exclude_is_honoured_and_untried_is_flagged():
    db = _mk([
        dict(id=10, slug="a", name="A"),
        dict(id=11, slug="b", name="B", tags=["chưa-thử"]),
    ])
    res = _tools(db)["suggest_lunch"].execute({"exclude": ["A"], "today": TODAY.isoformat()})
    assert [c["name"] for c in res["candidates"]] == ["B"]
    assert res["candidates"][0]["untried"] is True


def test_no_raw_vnd_reaches_the_model(db=None):
    """D5: a suggestion may never carry an amount that could read as a ledger figure."""
    db = _mk([dict(id=10, slug="a", name="A", price_hint=45000)])
    res = _tools(db)["suggest_lunch"].execute({"today": TODAY.isoformat()})
    c = res["candidates"][0]
    assert c["band"] is not None or c["band"] is None      # band is the only price signal
    assert "avg_per_head" not in c and "price_hint" not in c
    assert 45000 not in c.values()


def test_phone_is_passed_through_verbatim():
    db = _mk([dict(id=10, slug="a", name="A", phone="0906279398")])
    res = _tools(db)["suggest_lunch"].execute({"today": TODAY.isoformat()})
    assert res["candidates"][0]["phone"] == "0906279398"


def test_the_suggest_lunch_skill_ships():
    from app.agent import _read_skills
    assert "suggest-lunch" in {s["name"] for s in _read_skills()}
