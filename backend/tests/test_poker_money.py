"""Poker arithmetic (plan Task 6.3, review F3): chips conserved exactly, edges exact on
both sides, deterministic under reordering, every invariant refused with a usable error."""
import random
from datetime import date

import pytest

from packs.poker_ledger.money import PokerError, allocate, game_edges, net_positions

D = date(2026, 8, 3)


def _e(m, b, c):
    return {"member": m, "buy_in": b, "cash_out": c}


def test_nets_and_the_hand_derived_edges():
    nets = net_positions([_e(1, 400_000, 200_000), _e(2, 400_000, 550_000), _e(3, 400_000, 500_000), _e(4, 400_000, 350_000)])
    assert nets == {1: -200_000, 2: 150_000, 3: 100_000, 4: -50_000}
    edges = [(e.debtor, e.creditor, e.amount) for e in game_edges(7, D, nets)]
    assert edges == [(1, 2, 120_000), (1, 3, 80_000), (4, 2, 30_000), (4, 3, 20_000)]
    e = game_edges(7, D, nets)[0]
    assert e.ref_kind == "game" and e.ref_id == 7 and e.label == "game #7" and e.occurred_on == D
    nets = net_positions([_e(1, 300_000, 500_000), _e(2, 300_000, 50_000)], house=50_000)
    assert nets == {1: 200_000, 2: -250_000}
    assert [(e.debtor, e.creditor, e.amount) for e in game_edges(3, D, nets, house=50_000)] == [(2, 1, 200_000)]
    # two losers share the house in proportion to their losses, exactly
    nets = net_positions([_e(1, 100, 400), _e(2, 100, 0), _e(3, 100, 0), _e(4, 100, 0)], house=0) | {}
    assert [(e.debtor, e.amount) for e in game_edges(4, D, nets)] == [(2, 100), (3, 100), (4, 100)]
    nets = net_positions([_e(1, 100, 370), _e(2, 100, 0), _e(3, 100, 10), _e(4, 100, 0)], house=20)
    assert [(e.debtor, e.amount) for e in game_edges(5, D, nets, house=20)] == [(2, 93), (3, 84), (4, 93)]   # 270 to the winner


@pytest.mark.parametrize("seed", range(40))
def test_edges_are_exact_on_both_sides_and_deterministic(seed):
    rng = random.Random(seed)
    n = rng.randint(2, 7)
    buy = [rng.randrange(0, 2_000_000, 1000) for _ in range(n)]
    total = sum(buy)
    house = rng.choice([0, 0, rng.randrange(0, 100_000, 1000)])
    # random cash-outs that conserve chips exactly (VND, not multiples of 1,000 on purpose)
    cuts = sorted(rng.randint(0, total - house) for _ in range(n - 1))
    cash = [b - a for a, b in zip([0] + cuts, cuts + [total - house])]
    entries = [_e(i + 1, buy[i], cash[i]) for i in range(n)]
    nets = net_positions(entries, house)
    assert sum(nets.values()) == -house
    from packs.poker_ledger.money import house_shares
    edges = game_edges(1, D, nets, house=house)
    to_house = house_shares(nets, house)
    assert sum(to_house.values()) == (house if any(n < 0 for n in nets.values()) else 0)
    for m, net in nets.items():
        if net < 0:
            assert sum(e.amount for e in edges if e.debtor == m) == -net - to_house.get(m, 0), (m, net)
        elif net > 0:
            assert sum(e.amount for e in edges if e.creditor == m) == net, (m, net)
    assert all(e.amount > 0 for e in edges)
    shuffled = dict(rng.sample(list(nets.items()), len(nets)))
    assert game_edges(1, D, shuffled, house=house) == edges


def test_allocate_rounds_by_largest_remainder_with_id_ties():
    assert allocate(100, {1: 1, 2: 1, 3: 1}) == {1: 34, 2: 33, 3: 33}
    assert allocate(1, {1: 5, 2: 5}) == {1: 1}
    assert sum(allocate(999, {1: 3, 2: 7, 3: 11}).values()) == 999


@pytest.mark.parametrize("entries, house, needle", [
    ([_e(1, 500_000, 900_000), _e(2, 500_000, 200_000)], 0, "= -100,000đ"),           # over
    ([_e(1, 500_000, 300_000), _e(2, 500_000, 500_000)], 0, "= +200,000đ"),           # short
    ([_e(1, 500_000, 300_000), _e(2, 500_000, 500_000)], 100_000, "= +100,000đ"),     # house does not cover it
    ([_e(1, 500_000, 500_000)], 0, "at least two players"),
    ([_e(1, 500_000, 400_000), _e(1, 500_000, 600_000)], 0, "appears twice"),
    ([_e(1, -1, 0), _e(2, 1, 0)], 0, "must not be negative"),
    ([_e(1, "500k", 0), _e(2, 1, 0)], 0, "integer amount"),
    ([_e(1, 500_000, 400_000), _e(2, 500_000, 600_000)], -1, "house must not be negative"),
])
def test_invariants_are_refused_with_the_delta_in_the_message(entries, house, needle):
    with pytest.raises(PokerError) as exc:
        net_positions(entries, house)
    assert needle in str(exc.value)
    if "đ" in needle:
        assert "house" in str(exc.value)          # where rake/tips go
