"""The poker business's eval knowledge (plan Task 6.3): its compared arguments, the
"same nets under another encoding" equivalence, which results the table sees as a
card, the `game_state` grader, and the golden cases it ships (``eval_cases``)."""
from __future__ import annotations

from typing import Callable

from kernos.eval import Prose, ToolSelection, Verdict, _ok_results
from ledger_core.moneyguard import unbacked_amounts
from packs.ledger_tools.eval import SHARED_CARD_LABELS, compare_settlement, shared_body_kind
from packs.poker_ledger.money import PokerError, net_positions

TOOL_SELECTION_CONFIG = {
    "compared_args": ["entries", "house", "from", "to", "amount"],
    "unordered": [],
    "member_amount_lists": ["entries"],
    "item_fields": {"entries": ["member", "buy_in", "cash_out"]},
    "count_only": [],
    "sender_defaulted": ["from"],
    "equivalence_keys": ["entries"],
    "member_args": ["from", "to"],
    "member_list_args": [],
}

CARD_LABELS = {"game_draft": "a game draft card", "game_history": "a server-rendered game history", **SHARED_CARD_LABELS}


def nets_map(args: dict) -> dict | None:
    """The table's nets from one `propose_game` argument set — the money, not the encoding."""
    try:
        return net_positions(args.get("entries") or [], int(args.get("house") or 0))
    except (PokerError, TypeError, ValueError):
        return None


def posted_body_kind(record: dict) -> str | None:
    if any(r.get("type") == "game_draft" for r in _ok_results(record, "propose_game")):
        return "game_draft"
    if _ok_results(record, "game_history"):
        return "game_history"
    return shared_body_kind(record)


def _last_result(record: dict, *names: str):
    for call in reversed(record.get("tools") or []):
        if call.get("name") in names:
            return call["name"], call.get("result")
    return None, None


class GameState:
    """Did the turn put the table's money where the golden case says? A game draft's
    `nets` (`expect.nets`, by member key) and pot; a settlement (`transfers` as a set,
    `qr_payees`, `empty`, `blocked_pending`) through the shared comparison."""

    blocking = True

    def grade(self, case, record: dict, world) -> Verdict:
        expect = case.expect or {}
        game_keys = {"nets", "pot"} & expect.keys()
        settle_keys = {"transfers", "qr_payees", "blocked_pending", "empty"} & expect.keys()
        if not game_keys and not settle_keys:
            return Verdict(None, "no ledger expectation for this case")
        if record.get("error"):
            return Verdict(False, f"turn errored: {record['error']}")
        problems: list[str] = []
        ids = world.ids
        if game_keys:
            _, result = _last_result(record, "propose_game")
            if not result or result.get("ok") is False:
                problems.append("propose_game did not return a draft")
            else:
                if "nets" in expect:
                    want = {ids[k]: v for k, v in expect["nets"].items()}
                    got = {n["member"]: n["net"] for n in result.get("nets") or []}
                    if got != want:
                        problems.append(f"nets: expected {want}, got {got}")
                if "pot" in expect and result.get("pot") != expect["pot"]:
                    problems.append(f"pot: expected {expect['pot']:,}, got {result.get('pot')}")
        if settle_keys:
            _, result = _last_result(record, "settle_period")
            if result is None:
                problems.append("settle_period was never called")
            else:
                exp = dict(expect)
                if "transfers" in exp and result.get("transfers") is not None:
                    # order-insensitive: netting order is the core's, not the case's
                    got = sorted(((t["from_id"], t["to_id"], t["amount"]) for t in result["transfers"]))
                    want = sorted(((ids[t["from"]], ids[t["to"]], t["amount"]) for t in exp["transfers"]))
                    if got != want:
                        problems.append(f"transfers: expected {want}, got {got}")
                    exp.pop("transfers")
                problems.extend(compare_settlement(result, exp, ids))
        if problems:
            return Verdict(False, "; ".join(problems))
        return Verdict(True, f"table matches on {sorted(game_keys | settle_keys)}")


def tool_selection(config: dict, *, judge=None) -> ToolSelection:
    return ToolSelection({**TOOL_SELECTION_CONFIG, **config}, equivalence={"propose_game": nets_map})


def game_state(config: dict, *, judge=None) -> GameState:
    return GameState()


def prose(config: dict, *, judge=None) -> Prose:
    from packs.lunch_ledger.eval import PROSE_RUBRIC
    return Prose(unbacked_amounts, posted_body_kind, judge=judge, rubric=config.get("rubric") or PROSE_RUBRIC,
                 card_labels=CARD_LABELS)


GRADERS: dict[str, Callable] = {
    "poker_ledger.eval.tool_selection": tool_selection,
    "poker_ledger.eval.game_state": game_state,
    "poker_ledger.eval.prose": prose,
}
