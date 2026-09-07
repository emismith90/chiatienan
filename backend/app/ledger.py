"""Shim: the ledger moved to :mod:`ledger_core.ledger` (plan Task 3.2).

``app.models`` configures ``ledger_core`` with this host's member model and its
ICT clock (read through ``app.clock`` at call time, so tests can freeze it).
``today_ict``/``now_ict`` stay importable here because tests call them by this
name. Every name the core module defines is re-exported."""
from ledger_core.ledger import *  # noqa: F401,F403
import ledger_core.ledger as _core

globals().update({k: v for k, v in vars(_core).items() if not k.startswith("__")})

from app.clock import now_ict, today_ict  # noqa: E402,F401
