"""Deterministic period resolution (ICT date math).

Pure functions over ``date`` objects — no clock, no DB. Callers pass ``today``
(computed in Asia/Ho_Chi_Minh) and, for ``since_last``, the ``period_to`` of the
last committed settlement. "Week" is Monday–Sunday.

A resolved period is a half-open-feeling inclusive date range used to query
``meals.occurred_on``: ``{"from": date | None, "to": date}``. ``from = None``
means "from the beginning of the ledger" (no committed settlement yet).
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

_KEYWORDS = {"since_last", "this_week", "last_week", "today", "yesterday", "this_month", "explicit"}


def _week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday=0


def resolve_period(
    keyword: str | None,
    *,
    today: date,
    last_settlement_to: date | None = None,
    explicit_from: date | None = None,
    explicit_to: date | None = None,
) -> dict:
    """Resolve a period keyword into ``{"from", "to", "keyword"}``.

    Supported keywords (unknown/blank → ``since_last``):
      * ``since_last`` — ``(last_settlement_to, today]``; if never settled, the
        whole ledger up to today (``from = None``).
      * ``this_week`` / ``last_week`` — Mon–Sun of the current / previous week.
      * ``today`` / ``yesterday`` — a single day.
      * ``this_month`` — 1st … last day of the current month.
      * ``explicit`` — the caller-supplied ``explicit_from``/``explicit_to``
        (``explicit_to`` defaults to ``today``).
    """
    kw = (keyword or "").strip().lower()
    if kw not in _KEYWORDS:
        kw = "since_last"

    if kw == "since_last":
        if last_settlement_to is None:
            return {"from": None, "to": today, "keyword": kw}
        return {"from": last_settlement_to + timedelta(days=1), "to": today, "keyword": kw}

    if kw == "this_week":
        monday = _week_monday(today)
        return {"from": monday, "to": monday + timedelta(days=6), "keyword": kw}

    if kw == "last_week":
        monday = _week_monday(today) - timedelta(days=7)
        return {"from": monday, "to": monday + timedelta(days=6), "keyword": kw}

    if kw == "today":
        return {"from": today, "to": today, "keyword": kw}

    if kw == "yesterday":
        y = today - timedelta(days=1)
        return {"from": y, "to": y, "keyword": kw}

    if kw == "this_month":
        first = today.replace(day=1)
        last = today.replace(day=monthrange(today.year, today.month)[1])
        return {"from": first, "to": last, "keyword": kw}

    # explicit
    if explicit_from is None and explicit_to is None:
        raise ValueError("explicit period requires explicit_from and/or explicit_to")
    return {
        "from": explicit_from,
        "to": explicit_to or today,
        "keyword": kw,
    }
