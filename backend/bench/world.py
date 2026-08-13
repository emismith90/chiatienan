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

import contextlib
from datetime import date, datetime, time

from app import clock, drafts, ledger
from app.clock import ICT
from app.models import Member, Room


@contextlib.contextmanager
def frozen_clock(day_iso: str):
    """Freeze `app.clock.now_ict` to noon ICT on `day_iso`, then restore it.

    Patches **`now_ict` only**. `today_ict` is import-bound in `ledger`,
    `drafts`, and `tools`, so patching it would leave those modules on the real
    clock while everything else moved — and every date the tools resolve would be
    wrong by however long ago the corpus was written.
    """
    frozen = datetime.combine(date.fromisoformat(day_iso), time(12, 0), tzinfo=ICT)
    real = clock.now_ict
    clock.now_ict = lambda: frozen
    try:
        yield frozen
    finally:
        clock.now_ict = real


def _seed_room(db, case) -> tuple[int, dict[str, int]]:
    """Create the room and its seed members, bank details included."""
    ids: dict[str, int] = {}
    with db.session() as s:
        room = Room(name=f"bench-{case.source}", invite_token=f"bench-{case.source}-{case.id}")
        s.add(room); s.flush()
        for spec in case.members:
            member = Member(room_id=room.id, display_name=spec["display_name"],
                            nickname=spec["nickname"], pin=spec.get("pin", "1"),
                            **(spec.get("bank") or {}))
            s.add(member); s.flush()
            ids[spec["key"]] = member.id
        return room.id, ids


def _draft_payload(step: dict, ids: dict[str, int]) -> dict:
    """The draft a prior step creates.

    `items` / `adjustments` / `discount_split` are passed through when the step has
    them — a production meal seeded without its `items` splits evenly instead of
    per dish, so the ledger the next turn reads would be a *different* room's. The
    shares themselves are never copied: `drafts.create_draft` recomputes them from
    these inputs, which is the same code that produced the numbers being replayed.
    """
    payload = {
        "payer_member_id": ids[step["payer"]],
        "member_participants": [ids[p] for p in step["participants"]],
        "guests": step.get("guests", []),
        "bill_total": step["total"],
        "adjustments": [{**entry, "member": ids[entry["member"]]}
                        for entry in step.get("adjustments") or []],
        "per_head_preview": 0,
        "raw_input": step.get("message") or f'bench:{step["id"]}',
    }
    if step.get("items"):
        payload["items"] = [{**entry, "member": ids[entry["member"]]}
                            for entry in step["items"]]
    if step.get("discount_split"):
        payload["discount_split"] = step["discount_split"]
    return payload


def build_world(db, case) -> tuple[int, dict[str, int], dict[str, int]]:
    """Seed the room and replay every prior step. Returns `(room_id, ids, drafts)`.

    `drafts` maps a step id to the draft it created, for `confirm_pending`'s
    `ref` — and it is returned so a caller can assert which drafts a world
    contains.

    Every prior step runs, message-less ones included: the `confirm_pending`
    button presses and the `s11*` payments are exactly the steps that make the
    later expectations true. For the `meals` corpus there are no prior steps and
    the world is just the seeded room.
    """
    room_id, ids = _seed_room(db, case)
    draft_by_step: dict[str, int] = {}

    for step in case.prior_steps:
        kind = step["kind"]
        with frozen_clock(step["day"]):
            actor = ids.get(step.get("actor"))

            if kind == "add_member":
                with db.session() as s:
                    member = Member(room_id=room_id,
                                    display_name=step["new_member"].upper(),
                                    nickname=step["new_member"], pin="1")
                    s.add(member); s.flush()
                    ids[step["new_member"]] = member.id

            elif kind in ("meal_confirmed", "leave_pending"):
                with db.session() as s:
                    draft, _ = drafts.create_draft(s, room_id, _draft_payload(step, ids))
                    draft_by_step[step["id"]] = draft.id
                    if kind == "meal_confirmed":
                        drafts.commit_draft(s, draft.id, room_id, logged_by=str(actor))

            elif kind == "confirm_pending":
                with db.session() as s:
                    drafts.commit_draft(s, draft_by_step[step["ref"]], room_id,
                                        logged_by=str(actor))

            elif kind == "payment":
                with db.session() as s:
                    ledger.record_payment(s, room_id=room_id,
                                          from_member_id=ids[step["from"]],
                                          to_member_id=ids[step["to"]],
                                          amount=step["amount"], logged_by=str(actor))

            elif kind == "settle":
                # Read-only: it changes nothing, so a prior settle needs no replay.
                pass

            else:
                raise ValueError(f'{case.id}: unknown prior step kind {kind!r} in {step["id"]}')

    return room_id, ids, draft_by_step
