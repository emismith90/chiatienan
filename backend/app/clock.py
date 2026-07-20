"""ICT (Asia/Ho_Chi_Minh) clock helpers.

All dates in the ledger — ``meals.occurred_on``, settlement windows, "today" for
period resolution — are computed in Asia/Ho_Chi_Minh (UTC+7), so a meal logged
late in the evening lands on the correct local day. Centralised here so every
caller shares one definition and tests can freeze it by monkeypatching.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

ICT = ZoneInfo("Asia/Ho_Chi_Minh")


def now_ict() -> datetime:
    """Timezone-aware current time in ICT."""
    return datetime.now(ICT)


def today_ict() -> date:
    """Current local date in ICT."""
    return now_ict().date()
