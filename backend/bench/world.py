"""Rebuild the world a case's expectation assumes, deterministically.

**This module is why the benchmark means anything.** The naive runner — seed a
generic room, replay the conversation as chat text — produces a world in which
the money graders cannot pass on *either* engine, so `--compare` reports "no
change" and the harness certifies equivalence it never tested. Two concrete
reasons, both verified against the datasets:

1. **Chat text creates no ledger rows.** `s5`'s expected transfers require meals
   `s1`/`s2`/`s4` and payment `s3` *committed*; `s8` requires a pending draft;
   `s12`'s `empty: True` requires the eight `s11a`–`s11h` payments, which carry
   no `message` at all and so were never LLM turns to replay.
2. **A generically seeded room has no bank details.**
   `tests/test_ledger._seed_room` creates `M1..Mn` with `display_name` /
   `nickname` / `pin` only, while `scenario_week.MEMBERS` gives `a1`/`a2`/`a4`
   banks precisely "so QR builds succeed". Seeded the wrong way `make_qr_url`
   raises `QRError` for every payee and `qr_payees` can never pass.

So the dispatch here is **factored out of** `tests/test_scenario_week.py:49-115`
rather than re-derived: same `kind` values, same `drafts.create_draft` /
`commit_draft` / `ledger.record_payment` calls, same per-step clock freeze. That
test is the executable specification of this file.
"""
from __future__ import annotations

from packs.lunch_ledger import fixtures as lunch_fixtures


from app.evalworld import World as _World  # noqa: F401
from app.evalworld import build_world, frozen_clock  # noqa: F401
from app.evalworld import seed_room as _seed_room  # noqa: F401


def _draft_payload(step: dict, ids: dict[str, int]) -> dict:
    """See :func:`packs.lunch_ledger.fixtures.draft_payload` — the fixtures moved into
    the lunch pack (plan Tasks 3.1, 3.3); this name stays for the tests that import it."""
    return lunch_fixtures.draft_payload(step, ids)
