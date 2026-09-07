"""World-building steps for the lunch ledger — the prior-step kinds an eval world
builder replays (chiatienan's ``bench.world``), exposed as ``pack.fixtures()``.

A fixture builds through a ``world`` the host provides, so the pack never touches
the host's member or message tables::

    world.space_id                          the room/table being built
    world.session()                         a SQLAlchemy session context manager
    world.add_member(display_name=, nickname=) -> member id
    world.create_card(kind, payload) -> card id     (a pending draft)
    world.commit_card(card_id, actor)               (the human's Confirm)

Signature of every step: ``(world, step, ids, drafts_by_step, actor)`` — ``ids``
maps the corpus's member keys to ids, ``drafts_by_step`` a step id to the card it
created (for ``confirm_pending``'s ``ref``).
"""
from __future__ import annotations


def draft_payload(step: dict, ids: dict[str, int]) -> dict:
    """The draft a prior step creates.

    `items` / `adjustments` / `discount_split` are passed through when the step has
    them — a production meal seeded without its `items` splits evenly instead of
    per dish, so the ledger the next turn reads would be a *different* room's. The
    shares themselves are never copied: the host's draft store recomputes them from
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


def meal(world, step: dict, ids: dict, drafts_by_step: dict, actor) -> None:
    """`meal_confirmed` and `leave_pending`: create the draft; commit only the former."""
    draft_id = world.create_card("expense_draft", draft_payload(step, ids))
    drafts_by_step[step["id"]] = draft_id
    if step["kind"] == "meal_confirmed":
        world.commit_card(draft_id, actor)


def confirm_pending(world, step: dict, ids: dict, drafts_by_step: dict, actor) -> None:
    world.commit_card(drafts_by_step[step["ref"]], actor)


#: `add_member`, `payment` and `settle` are `packs.ledger_tools.fixtures` (shared).
FIXTURES = {
    "meal_confirmed": meal,
    "leave_pending": meal,
    "confirm_pending": confirm_pending,
}
