"""Rebuilding a case's world on this host (plan Task 4.3, review F5).

Moved from ``bench.world`` so an eval run needs no ``bench`` in the image: the
frozen clock, the room seeding from a case's member specs, the ``World`` the pack's
fixtures build through (members and cards are this host's tables), and
``build_world``. ``bench.world`` imports these back.

**Job-only.** ``frozen_clock`` patches the process-wide ``app.clock.now_ict`` that
every production turn reads through; run evals in their own process
(``python -m app.evalhost``), never in the serving one (review F3).
"""
from __future__ import annotations

import contextlib
from datetime import date, datetime, time

from app import clock
from app.clock import ICT
from app.hostadapters import RoomCards
from app.models import Member, Room
from app.packs import lunch_ledger_pack


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


def seed_room(db, case) -> tuple[int, dict[str, int]]:
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


class World:
    """The host services a pack's fixtures build a world through (see
    ``packs.lunch_ledger.fixtures``): members and cards are this host's tables."""

    def __init__(self, db, room_id: int) -> None:
        self.db, self.space_id = db, room_id
        self._cards = RoomCards(db)

    def session(self):
        return self.db.session()

    def add_member(self, *, display_name: str, nickname: str) -> int:
        with self.db.session() as s:
            member = Member(room_id=self.space_id, display_name=display_name, nickname=nickname, pin="1")
            s.add(member); s.flush()
            return member.id

    def create_card(self, kind: str, payload: dict) -> int:
        card, _superseded = self._cards.create(self.space_id, kind, payload)
        return card.id

    def commit_card(self, card_id: int, actor) -> None:
        from app import drafts
        with self.db.session() as s:
            drafts.commit_any(s, card_id, self.space_id, logged_by=str(actor))


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
    room_id, ids = seed_room(db, case)
    draft_by_step: dict[str, int] = {}

    # The step kinds are the lunch pack's fixtures (`packs.lunch_ledger.fixtures`):
    # the bench sequences them and freezes the clock, the pack knows how to put a
    # room into "meal confirmed" or "payment recorded" state — through `_World`.
    fixtures = lunch_ledger_pack().fixtures()
    world = World(db, room_id)
    for step in case.prior_steps:
        kind = step["kind"]
        with frozen_clock(step["day"]):
            actor = ids.get(step.get("actor"))
            run = fixtures.get(kind)
            if run is None:
                raise ValueError(f'{case.id}: unknown prior step kind {kind!r} in {step["id"]}')
            run(world, step, ids, draft_by_step, actor)

    return room_id, ids, draft_by_step
