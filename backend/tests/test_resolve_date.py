from datetime import date
import pytest
from app.periods import resolve_date

WED = date(2026, 7, 22)  # a Wednesday


@pytest.mark.parametrize("word,expected", [
    ("hôm nay", WED), ("today", WED),
    ("hôm qua", date(2026, 7, 21)), ("yesterday", date(2026, 7, 21)),
    ("thứ 2", date(2026, 7, 20)), ("t2", date(2026, 7, 20)), ("monday", date(2026, 7, 20)),
    ("thứ 4", WED),                       # today's weekday resolves to today
    ("thứ 5", date(2026, 7, 16)),         # Thursday is in the past -> last week
    ("20/7", date(2026, 7, 20)), ("20/07/2026", date(2026, 7, 20)),
])
def test_resolve_date(word, expected):
    assert resolve_date(word, today=WED) == expected


def test_resolve_date_bad():
    with pytest.raises(ValueError):
        resolve_date("blah", today=WED)
