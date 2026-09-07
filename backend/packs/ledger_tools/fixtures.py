"""World-building steps every ledger business shares (Phase 6 review F9): a member
joins, a cash payment is recorded, a settle (read-only, nothing to replay). Same
``(world, step, ids, drafts_by_step, actor)`` contract as ``packs.lunch_ledger.fixtures``.
"""
from __future__ import annotations

from ledger_core import ledger


def add_member(world, step: dict, ids: dict, drafts_by_step: dict, actor) -> None:
    ids[step["new_member"]] = world.add_member(display_name=step["new_member"].upper(),
                                               nickname=step["new_member"])


def payment(world, step: dict, ids: dict, drafts_by_step: dict, actor) -> None:
    with world.session() as s:
        ledger.record_payment(s, room_id=world.space_id, from_member_id=ids[step["from"]],
                              to_member_id=ids[step["to"]], amount=step["amount"], logged_by=str(actor))


def settle(world, step: dict, ids: dict, drafts_by_step: dict, actor) -> None:
    # Read-only: it changes nothing, so a prior settle needs no replay.
    return None


FIXTURES = {
    "add_member": add_member,
    "payment": payment,
    "settle": settle,
}
