from datetime import date

import pytest

from app.periods import resolve_period


# A known Wednesday: 2026-07-15 (Mon 2026-07-13 .. Sun 2026-07-19)
WED = date(2026, 7, 15)


def test_since_last_with_prior_settlement_starts_day_after():
    p = resolve_period("since_last", today=WED, last_settlement_to=date(2026, 7, 13))
    assert p["from"] == date(2026, 7, 14)
    assert p["to"] == WED


def test_since_last_without_settlement_is_open_start():
    p = resolve_period("since_last", today=WED, last_settlement_to=None)
    assert p["from"] is None
    assert p["to"] == WED


def test_blank_keyword_defaults_to_since_last():
    p = resolve_period("", today=WED)
    assert p["keyword"] == "since_last"


def test_unknown_keyword_defaults_to_since_last():
    p = resolve_period("nonsense", today=WED)
    assert p["keyword"] == "since_last"


def test_this_week_is_monday_to_sunday():
    p = resolve_period("this_week", today=WED)
    assert p["from"] == date(2026, 7, 13)  # Monday
    assert p["to"] == date(2026, 7, 19)    # Sunday


def test_last_week_is_previous_monday_to_sunday():
    p = resolve_period("last_week", today=WED)
    assert p["from"] == date(2026, 7, 6)
    assert p["to"] == date(2026, 7, 12)


def test_week_math_on_a_monday():
    monday = date(2026, 7, 13)
    p = resolve_period("this_week", today=monday)
    assert p["from"] == monday and p["to"] == date(2026, 7, 19)


def test_week_math_on_a_sunday():
    sunday = date(2026, 7, 19)
    p = resolve_period("this_week", today=sunday)
    assert p["from"] == date(2026, 7, 13) and p["to"] == sunday


def test_today_and_yesterday():
    assert resolve_period("today", today=WED) == {"from": WED, "to": WED, "keyword": "today"}
    y = resolve_period("yesterday", today=WED)
    assert y["from"] == date(2026, 7, 14) and y["to"] == date(2026, 7, 14)


def test_this_month_spans_full_month():
    p = resolve_period("this_month", today=WED)
    assert p["from"] == date(2026, 7, 1)
    assert p["to"] == date(2026, 7, 31)


def test_explicit_requires_bounds():
    with pytest.raises(ValueError):
        resolve_period("explicit", today=WED)


def test_explicit_with_bounds():
    p = resolve_period(
        "explicit", today=WED, explicit_from=date(2026, 7, 1), explicit_to=date(2026, 7, 10)
    )
    assert p["from"] == date(2026, 7, 1) and p["to"] == date(2026, 7, 10)
