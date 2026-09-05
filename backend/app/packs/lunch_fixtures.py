"""World-building steps for the lunch ledger — the five prior-step kinds the eval
world builder replays (``bench.world``), re-homed out of ``bench`` so the pack
exposes them as ``fixtures()`` without pulling the benchmark into the app graph
(review F6). Bodies are byte-for-byte what ``bench.world.build_world`` did.
"""
from __future__ import annotations

from app import drafts, ledger
from app.models import Member


def draft_payload(step: dict, ids: dict[str, int]) -> dict:
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


def add_member(db, room_id: int, step: dict, ids: dict, drafts_by_step: dict, actor) -> None:
    with db.session() as s:
        member = Member(room_id=room_id, display_name=step["new_member"].upper(),
                        nickname=step["new_member"], pin="1")
        s.add(member); s.flush()
        ids[step["new_member"]] = member.id


def meal(db, room_id: int, step: dict, ids: dict, drafts_by_step: dict, actor) -> None:
    """`meal_confirmed` and `leave_pending`: create the draft; commit only the former."""
    with db.session() as s:
        draft, _ = drafts.create_draft(s, room_id, draft_payload(step, ids))
        drafts_by_step[step["id"]] = draft.id
        if step["kind"] == "meal_confirmed":
            drafts.commit_draft(s, draft.id, room_id, logged_by=str(actor))


def confirm_pending(db, room_id: int, step: dict, ids: dict, drafts_by_step: dict, actor) -> None:
    with db.session() as s:
        drafts.commit_draft(s, drafts_by_step[step["ref"]], room_id, logged_by=str(actor))


def payment(db, room_id: int, step: dict, ids: dict, drafts_by_step: dict, actor) -> None:
    with db.session() as s:
        ledger.record_payment(s, room_id=room_id, from_member_id=ids[step["from"]],
                              to_member_id=ids[step["to"]], amount=step["amount"], logged_by=str(actor))


def settle(db, room_id: int, step: dict, ids: dict, drafts_by_step: dict, actor) -> None:
    # Read-only: it changes nothing, so a prior settle needs no replay.
    return None


FIXTURES = {
    "add_member": add_member,
    "meal_confirmed": meal,
    "leave_pending": meal,
    "confirm_pending": confirm_pending,
    "payment": payment,
    "settle": settle,
}
