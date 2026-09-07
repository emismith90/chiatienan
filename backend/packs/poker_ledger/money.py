"""Poker's arithmetic — pure, deterministic, every number the model must never touch.

A game night: every player buys in and cashes out; ``net = cash_out − buy_in``; the
table conserves chips — ``Σ buy_in = Σ cash_out + house`` **exactly** (tolerance 0;
rake and tips are the explicit ``house`` line, design §11.4). The debt edges of a game
run from each loser to the winners. The house's cut is borne by the losers in
proportion to their losses (exactly, largest remainder) and is a debt to nobody —
the table kept that cash; the rest of each loss is split across the winners in
proportion to each winner's **remaining** unreceived win, losers taken in member-id
order with largest-remainder rounding, so that every loser's edges sum exactly to
their loss minus their house share **and** every winner's receipts sum exactly to
their win (Phase 6 review F3 — the last loser's shares are the winners' remainders,
because Σ losses − house = Σ wins).
"""
from __future__ import annotations

from datetime import date

from ledger_core.money import DebtEdge


class PokerError(ValueError):
    """A table that cannot be recorded; the message says what to ask."""


def net_positions(entries: list[dict], house: int = 0) -> dict[int, int]:
    """``{member: cash_out − buy_in}`` after checking every invariant.

    Raises :class:`PokerError` for: fewer than two players, a member twice, a negative
    or non-integer amount, a negative house, or chips not conserved — that message
    carries the signed delta (``Σ buy_in − Σ cash_out − house``) and says where rake
    goes, so the model's clarifying question is specific.
    """
    if not isinstance(house, int) or isinstance(house, bool):
        raise PokerError("house must be an integer amount (VND).")
    if house < 0:
        raise PokerError("house must not be negative.")
    if not isinstance(entries, list) or len(entries) < 2:
        raise PokerError("A game needs at least two players (entries).")
    nets: dict[int, int] = {}
    for e in entries:
        if not isinstance(e, dict):
            raise PokerError("Each entry is {member, buy_in, cash_out}.")
        try:
            member = int(e["member"])
        except (KeyError, TypeError, ValueError):
            raise PokerError("Each entry needs a member id.") from None
        for key in ("buy_in", "cash_out"):
            v = e.get(key)
            if not isinstance(v, int) or isinstance(v, bool):
                raise PokerError(f"{key} of member {member} must be an integer amount (VND).")
            if v < 0:
                raise PokerError(f"{key} of member {member} must not be negative.")
        if member in nets:
            raise PokerError(f"Member {member} appears twice — one entry per player.")
        nets[member] = int(e["cash_out"]) - int(e["buy_in"])
    total_in = sum(int(e["buy_in"]) for e in entries)
    total_out = sum(int(e["cash_out"]) for e in entries)
    delta = total_in - total_out - house
    if delta != 0:
        raise PokerError(
            f"Chips are not conserved: Σ buy_in {total_in:,} − Σ cash_out {total_out:,} − house {house:,} = {delta:+,}đ. "
            "Ask who is short or over — or whether the difference is the house's (rake/tips go in `house`).")
    return nets


def allocate(amount: int, weights: dict[int, int]) -> dict[int, int]:
    """Split ``amount`` in proportion to ``weights``, exactly (largest-remainder rounding;
    ties broken by member id). Σ result = amount; zero shares are dropped."""
    loss, remaining = amount, weights
    total = sum(remaining.values())
    if total <= 0:
        raise PokerError("Nothing left to allocate to.")
    exact = {w: loss * r / total for w, r in remaining.items() if r > 0}
    floors = {w: int(v) for w, v in exact.items()}
    short = loss - sum(floors.values())
    order = sorted(exact, key=lambda w: (-(exact[w] - floors[w]), w))
    for w in order[:short]:
        floors[w] += 1
    return {w: v for w, v in floors.items() if v}


def house_shares(nets: dict[int, int], house: int) -> dict[int, int]:
    """How much of the house's cut each loser bore — in proportion to their loss,
    exact (largest remainder). The house is money the table kept, paid at the table,
    so it is a debt to nobody in the ledger; what remains of a loss is owed to the
    winners."""
    losses = {m: -n for m, n in nets.items() if n < 0}
    if not house or not losses:
        return {}
    return allocate(house, losses)


def game_edges(game_id: int, played_on: date, nets: dict[int, int], *, house: int = 0,
               label: str | None = None) -> list[DebtEdge]:
    """The game's debt edges, losers → winners, exact on both sides: Σ per loser = their
    loss minus their share of the house; Σ per winner = their win."""
    winners = {m: n for m, n in nets.items() if n > 0}
    to_house = house_shares(nets, house)
    losers = sorted(((m, -n - to_house.get(m, 0)) for m, n in nets.items() if n < 0), key=lambda x: x[0])
    losers = [(m, loss) for m, loss in losers if loss > 0]
    remaining = dict(winners)
    edges: list[DebtEdge] = []
    for loser, loss in losers:
        for winner, amount in sorted(allocate(loss, remaining).items()):
            remaining[winner] -= amount
            edges.append(DebtEdge(debtor=loser, creditor=winner, meal_id=game_id, dish=label or f"game #{game_id}",
                                  occurred_on=played_on, amount=amount, ref_kind="game"))
    assert all(v == 0 for v in remaining.values()), remaining     # Σ (loss − house share) = Σ wins, by conservation
    return edges
