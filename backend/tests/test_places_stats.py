from datetime import date

import pytest

from app import places
from app.db import Database
from app.models import Meal, MealShare, Member, Place, Room

TODAY = date(2026, 8, 14)          # a Friday


@pytest.fixture()
def db():
    d = Database("sqlite:///:memory:")
    d.create_all()
    with d.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.flush()
        for i in (1, 2, 3):
            s.add(Member(id=i, room_id=1, display_name=f"M{i}", nickname=f"m{i}"))
        s.flush()
        s.add(Place(id=10, room_id=1, slug="cheap", name="Rẻ", price_hint=None))
        s.add(Place(id=11, room_id=1, slug="mid", name="Vừa"))
        s.add(Place(id=12, room_id=1, slug="pricey", name="Đắt"))
        s.add(Place(id=13, room_id=1, slug="untried", name="Chưa thử", tags=["chưa-thử"]))
    return d


def _meal(s, place_id, on, total, members, guests=(), voided=False):
    m = Meal(room_id=1, occurred_on=on, payer_member_id=members[0], total_amount=total,
             place_id=place_id, guests=list(guests), voided=voided)
    m.shares = [MealShare(member_id=i, share_amount=total // len(members)) for i in members]
    s.add(m)
    s.flush()
    return m


def test_counts_recency_and_weekday(db):
    with db.session() as s:
        _meal(s, 10, date(2026, 8, 10), 90000, [1, 2, 3])     # Monday
        _meal(s, 10, date(2026, 8, 3), 90000, [1, 2, 3])      # Monday
        _meal(s, 11, date(2026, 8, 13), 150000, [1, 2, 3])    # Thursday
        st = places.stats(s, 1, today=TODAY)
    assert st[10]["times"] == 2
    assert st[10]["last_on"] == date(2026, 8, 10)
    assert st[10]["days_since"] == 4
    assert st[10]["weekday_counts"][0] == 2, "both were Mondays"
    assert st[11]["days_since"] == 1


def test_avg_per_head_divides_by_members_only(db):
    """total_amount is tracked_total — guests are already out of it (money.py:341).

    Dividing by members+guests would understate the per-head cost by exactly the
    guest fraction, making any place where guests join look cheaper than it is.
    """
    with db.session() as s:
        _meal(s, 10, date(2026, 8, 10), 90000, [1, 2, 3], guests=["khách A", "khách B"])
        st = places.stats(s, 1, today=TODAY)
    assert st[10]["avg_per_head"] == 30000


def test_voided_meals_are_excluded(db):
    with db.session() as s:
        _meal(s, 10, date(2026, 8, 10), 90000, [1, 2, 3], voided=True)
        st = places.stats(s, 1, today=TODAY)
    assert st[10]["times"] == 0 and st[10]["days_since"] is None


def test_price_hint_supplies_a_band_with_no_history(db):
    with db.session() as s:
        s.get(Place, 10).price_hint = 30000
        s.get(Place, 11).price_hint = 70000
        s.get(Place, 12).price_hint = 200000
        s.flush()
        st = places.stats(s, 1, today=TODAY)
    assert st[10]["band"] == "rẻ"
    assert st[12]["band"] == "đắt"


def test_ledger_overrides_the_price_hint_once_a_meal_links(db):
    with db.session() as s:
        s.get(Place, 10).price_hint = 500000        # a wrong/stale hint
        s.flush()
        _meal(s, 10, date(2026, 8, 10), 30000, [1, 2, 3])   # real: 10k/head
        st = places.stats(s, 1, today=TODAY)
    assert st[10]["avg_per_head"] == 10000, "the ledger is authoritative once it exists"


def test_place_with_neither_history_nor_hint_has_no_band(db):
    with db.session() as s:
        st = places.stats(s, 1, today=TODAY)
    assert st[13]["band"] is None
    assert st[13]["days_since"] is None
    assert st[13]["times"] == 0


def test_window_excludes_old_meals(db):
    with db.session() as s:
        _meal(s, 10, date(2026, 1, 1), 90000, [1, 2, 3])
        st = places.stats(s, 1, window_days=30, today=TODAY)
    assert st[10]["times"] == 0
