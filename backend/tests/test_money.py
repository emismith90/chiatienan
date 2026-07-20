import pytest

from app.money import MoneyError, Transfer, net_transfers, split_shares


# --- split_shares ---------------------------------------------------------- #

def test_even_split_exact():
    shares = split_shares(600, [1, 2, 3])
    assert shares == {1: 200, 2: 200, 3: 200}
    assert sum(shares.values()) == 600


def test_remainder_to_payer_when_participant():
    # 100 / 3 = 33 base, remainder 1 → payer (2) absorbs it
    shares = split_shares(100, [1, 2, 3], payer_id=2)
    assert shares == {1: 33, 2: 34, 3: 33}
    assert sum(shares.values()) == 100


def test_remainder_to_first_participant_when_payer_not_in_set():
    # payer 9 did not eat → remainder goes to first participant (1)
    shares = split_shares(100, [1, 2, 3], payer_id=9)
    assert shares == {1: 34, 2: 33, 3: 33}
    assert sum(shares.values()) == 100


def test_positive_adjustment_pricier_dish():
    # total 840k, 7 people, Bình (id 2) +50k. Compare against another non-payer
    # participant so the payer's remainder cent doesn't skew the delta.
    shares = split_shares(840_000, [1, 2, 3, 4, 5, 6, 7], {2: 50_000}, payer_id=1)
    assert shares[2] - shares[3] == 50_000
    assert sum(shares.values()) == 840_000


def test_negative_adjustment_ate_less():
    shares = split_shares(300, [1, 2, 3], {3: -30}, payer_id=1)
    # base = (300 - (-30)) // 3 = 110 ; 3 → 80
    assert shares[3] == 80
    assert sum(shares.values()) == 300


def test_payer_not_participant_gets_no_share():
    shares = split_shares(200, [2, 3], payer_id=1)
    assert 1 not in shares
    assert sum(shares.values()) == 200


def test_reject_zero_total():
    with pytest.raises(MoneyError):
        split_shares(0, [1, 2])


def test_reject_empty_participants():
    with pytest.raises(MoneyError):
        split_shares(100, [])


def test_reject_adjustment_for_non_participant():
    with pytest.raises(MoneyError):
        split_shares(100, [1, 2], {3: 10})


def test_reject_overshoot_adjustments():
    with pytest.raises(MoneyError):
        split_shares(100, [1, 2], {1: 150})


def test_reject_negative_resulting_share():
    # a huge negative adjustment drives participant 2's share below zero
    with pytest.raises(MoneyError):
        split_shares(100, [1, 2], {2: -150})


def test_reject_duplicate_participants():
    with pytest.raises(MoneyError):
        split_shares(100, [1, 1])


# --- net_transfers --------------------------------------------------------- #

def test_net_simple_two_party():
    # 1 paid, 2 owes
    transfers = net_transfers({1: 100, 2: -100})
    assert transfers == [Transfer(from_member=2, to_member=1, amount=100)]


def test_net_zero_balances_no_transfers():
    assert net_transfers({1: 0, 2: 0}) == []


def test_net_greedy_min_transfers():
    # 1 is owed 100, 2 & 3 each owe 50 → two transfers into 1
    transfers = net_transfers({1: 100, 2: -50, 3: -50})
    assert len(transfers) == 2
    assert all(t.to_member == 1 for t in transfers)
    assert sum(t.amount for t in transfers) == 100


def test_net_max_debtor_to_max_creditor():
    balances = {1: 60, 2: 40, 3: -70, 4: -30}
    transfers = net_transfers(balances)
    # biggest debtor (3, owes 70) pays biggest creditor (1, owed 60) first
    assert transfers[0].from_member == 3 and transfers[0].to_member == 1
    assert transfers[0].amount == 60
    # everything nets out
    assert sum(t.amount for t in transfers if t.to_member == 1) == 60
    assert sum(t.amount for t in transfers if t.to_member == 2) == 40


def test_net_conserves_money():
    balances = {1: 150, 2: -70, 3: -80}
    transfers = net_transfers(balances)
    paid_out = sum(t.amount for t in transfers)
    assert paid_out == 150
    # each debtor pays exactly what they owe
    assert sum(t.amount for t in transfers if t.from_member == 2) == 70
    assert sum(t.amount for t in transfers if t.from_member == 3) == 80


def test_net_is_deterministic_on_ties():
    balances = {1: 50, 2: 50, 3: -50, 4: -50}
    a = net_transfers(balances)
    b = net_transfers(balances)
    assert a == b
