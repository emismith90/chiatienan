"""The domain's clock, injected by the host (design §4.6: ``Clock``).

Every date the ledger writes — ``meals.occurred_on``, ``payments.occurred_on``,
settlement windows, "today" for period resolution — comes from here. A host sets
the provider once (chiatienan: Asia/Ho_Chi_Minh, read through ``app.clock`` at
call time so tests can freeze it); the default is UTC.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable

_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)  # noqa: E731


def set_provider(now: Callable[[], datetime]) -> None:
    global _now
    _now = now


def now() -> datetime:
    return _now()


def today() -> date:
    return _now().date()
