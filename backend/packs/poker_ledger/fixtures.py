"""World-building steps for the poker business (``(world, step, ids, drafts_by_step,
actor)``): `game_recorded` creates and confirms a game card, `game_pending` leaves it
pending, `confirm_pending` confirms a named earlier step. Shared steps (`add_member`,
`payment`, `settle`) are ``packs.ledger_tools.fixtures``."""
from __future__ import annotations


def game_payload(step: dict, ids: dict[str, int]) -> dict:
    return {
        "entries": [{"member": ids[e["member"]], "buy_in": e["buy_in"], "cash_out": e["cash_out"]} for e in step["entries"]],
        "house": step.get("house", 0), "played_on": step["day"], "note": step.get("note"),
        "raw_input": step.get("message") or f'bench:{step["id"]}',
    }


def game(world, step: dict, ids: dict, drafts_by_step: dict, actor) -> None:
    card_id = world.create_card("game_draft", game_payload(step, ids))
    drafts_by_step[step["id"]] = card_id
    if step["kind"] == "game_recorded":
        world.commit_card(card_id, actor)


def confirm_pending(world, step: dict, ids: dict, drafts_by_step: dict, actor) -> None:
    world.commit_card(drafts_by_step[step["ref"]], actor)


FIXTURES = {"game_recorded": game, "game_pending": game, "confirm_pending": confirm_pending}
