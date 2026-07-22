import pytest

from app.money import MoneyError, per_payer_transfers, split_shares


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


# --- per_payer_transfers --------------------------------------------------- #

def _tset(transfers):
    return {(t.from_member, t.to_member, t.amount) for t in transfers}


def test_per_payer_each_participant_repays_the_meal_payer():
    # One meal: payer 1 fronts, 2 and 3 each owe their share back to 1.
    meals = [{"payer_id": 1, "shares": {1: 40, 2: 30, 3: 30}}]
    assert _tset(per_payer_transfers(meals)) == {(2, 1, 30), (3, 1, 30)}


def test_per_payer_payer_owes_nothing_to_self():
    meals = [{"payer_id": 1, "shares": {1: 100}}]
    assert per_payer_transfers(meals) == []


def test_per_payer_prod_room3_scenario():
    # The reported bug: two meals with different, partially-overlapping rosters.
    # ids: Emi=4 Trang=5 Linh=6 Dung=7 TrangDinh=8 Giang=9
    meals = [
        # meal #2 — Linh(6) paid 305k, split 5 ways (no Trang)
        {"payer_id": 6, "shares": {4: 61000, 9: 61000, 6: 61000, 7: 61000, 8: 61000}},
        # meal #3 — Giang(9) paid 375k, split 5 ways (no Dung)
        {"payer_id": 9, "shares": {5: 75000, 4: 75000, 9: 75000, 6: 75000, 8: 75000}},
    ]
    assert _tset(per_payer_transfers(meals)) == {
        (4, 6, 61000),   # Emi -> Linh
        (4, 9, 75000),   # Emi -> Giang
        (8, 6, 61000),   # TrangDinh -> Linh
        (8, 9, 75000),   # TrangDinh -> Giang
        (5, 9, 75000),   # Trang -> Giang
        (7, 6, 61000),   # Dung -> Linh (all of it, not split)
        (6, 9, 14000),   # Linh owes Giang 75k, Giang owes Linh 61k -> net 14k
    }


def test_per_payer_conserves_net_balances():
    meals = [
        {"payer_id": 6, "shares": {4: 61000, 9: 61000, 6: 61000, 7: 61000, 8: 61000}},
        {"payer_id": 9, "shares": {5: 75000, 4: 75000, 9: 75000, 6: 75000, 8: 75000}},
    ]
    transfers = per_payer_transfers(meals)
    net = {}
    for t in transfers:
        net[t.from_member] = net.get(t.from_member, 0) - t.amount
        net[t.to_member] = net.get(t.to_member, 0) + t.amount
    assert net == {4: -136000, 5: -75000, 6: 169000, 7: -61000, 8: -136000, 9: 239000}


def test_per_payer_nets_opposing_debts_per_pair_into_one_direction():
    # 1 pays a meal 2 joins; 2 pays a meal 1 joins. Only the net remains.
    meals = [
        {"payer_id": 1, "shares": {1: 50, 2: 50}},   # 2 owes 1: 50
        {"payer_id": 2, "shares": {1: 30, 2: 30}},   # 1 owes 2: 30
    ]
    assert _tset(per_payer_transfers(meals)) == {(2, 1, 20)}


def test_per_payer_folds_adhoc_payments_reducing_debt():
    meals = [{"payer_id": 1, "shares": {1: 40, 2: 60}}]  # 2 owes 1: 60
    payments = [{"from": 2, "to": 1, "amount": 60}]      # 2 already paid 1
    assert per_payer_transfers(meals, payments) == []


def test_per_payer_is_deterministic():
    meals = [{"payer_id": 1, "shares": {1: 10, 2: 10, 3: 10}}]
    assert per_payer_transfers(meals) == per_payer_transfers(meals)
