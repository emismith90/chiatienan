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


# --- equal-delta itemized split -------------------------------------------- #

def test_equal_discount_split_matches_what_the_room_did_by_hand():
    """Production 07-27: Σ items 414,200đ, paid 324,200đ, and Emi asked for the
    90,000đ gap split six ways (15,000đ each) rather than by dish price."""
    from app.money import prorate_items

    items = {4: 69_500, 8: 69_500, 9: 94_200, 6: 69_500, 7: 69_500, 5: 42_000}
    shares = prorate_items(324_200, items, discount_split="equal")
    assert shares == {4: 54_500, 8: 54_500, 9: 79_200, 6: 54_500, 7: 54_500, 5: 27_000}
    assert sum(shares.values()) == 324_200


def test_equal_and_proportional_disagree_and_both_sum_to_total():
    from app.money import prorate_items

    items = {1: 100_000, 2: 20_000}
    eq = prorate_items(100_000, items, discount_split="equal")
    pr = prorate_items(100_000, items, discount_split="proportional")
    assert eq != pr
    assert sum(eq.values()) == sum(pr.values()) == 100_000
    assert eq == {1: 90_000, 2: 10_000}          # 20,000 gap, 10,000 each
    assert pr == {1: 83_333, 2: 16_667}          # scaled by 100/120


def test_equal_split_of_a_fee_adds_to_every_share():
    from app.money import prorate_items

    # Σ items 90,000 but 100,000 paid (ship): everyone absorbs 5,000 more.
    shares = prorate_items(100_000, {1: 50_000, 2: 40_000}, discount_split="equal")
    assert shares == {1: 55_000, 2: 45_000}


def test_equal_split_remainder_is_deterministic_and_exact():
    from app.money import prorate_items

    # 10,001đ gap over 3 people: 3,334 / 3,334 / 3,333 by member id.
    shares = prorate_items(29_999, {1: 10_000, 2: 10_000, 3: 20_000}, discount_split="equal")
    assert sum(shares.values()) == 29_999
    assert shares == {1: 6_666, 2: 6_666, 3: 16_667}


def test_equal_split_refuses_to_invent_a_negative_share():
    """A gap bigger than someone's dish has no honest equal answer — say so
    instead of charging them a negative amount."""
    import pytest

    from app.money import MoneyError, prorate_items

    with pytest.raises(MoneyError, match="âm"):
        prorate_items(100_000, {1: 195_000, 2: 5_000}, discount_split="equal")


def test_unknown_discount_split_is_rejected():
    import pytest

    from app.money import MoneyError, prorate_items

    with pytest.raises(MoneyError, match="discount_split"):
        prorate_items(100_000, {1: 60_000, 2: 60_000}, discount_split="median")


def test_proportional_stays_the_default():
    from app.money import prorate_items

    items = {1: 100_000, 2: 20_000}
    assert prorate_items(100_000, items) == prorate_items(
        100_000, items, discount_split="proportional")


# --- a windowed settle must not invent debts -------------------------------- #
#
# Production 2026-07-27 20:03. Linh asked "hôm nay ai trả tiền", the day-scoped
# settle answered "Linh Nguyen → Giang Hoàng: 107,000đ", and Linh asked three
# times why he owed Giang anything. He didn't: Giang had PAID him 107,000đ that
# day, for meals on the 23rd and 24th that fall outside a one-day window.

EMI, NHIM, GIANG, LINH, KUN, TABU = 4, 8, 9, 6, 7, 5


def _grab_food_day():
    """The only meal dated 07-27: Emi paid, six shares."""
    return [{"payer_id": EMI, "shares": {
        EMI: 54_500, NHIM: 54_500, GIANG: 79_200, LINH: 54_500, KUN: 54_500, TABU: 27_000}}]


def test_a_payment_for_an_older_meal_does_not_reverse_the_debt():
    from app.money import per_payer_transfers

    payments = [
        {"from": GIANG, "to": LINH, "amount": 107_000},   # for meals on 07-23/07-24
        {"from": TABU, "to": EMI, "amount": 27_000},
        {"from": GIANG, "to": EMI, "amount": 79_200},
        {"from": LINH, "to": EMI, "amount": 54_500},
    ]
    got = {(t.from_member, t.to_member): t.amount
           for t in per_payer_transfers(_grab_food_day(), payments)}
    assert (LINH, GIANG) not in got, "invented a debt from an out-of-window payment"
    assert got == {(NHIM, EMI): 54_500, (KUN, EMI): 54_500}


def test_a_payment_between_two_people_with_no_debt_invents_nothing():
    """A 07-23..07-27 window produced "Giang Hoàng → Tabu: 75,000đ" — Tabu's
    payment for a 07-22 meal, flipped into a debt the other way."""
    from app.money import per_payer_transfers

    transfers = per_payer_transfers(
        _grab_food_day(), [{"from": TABU, "to": GIANG, "amount": 75_000}]
    )
    assert all({t.from_member, t.to_member} != {TABU, GIANG} for t in transfers)


def test_overpaying_a_real_debt_settles_it_without_a_refund_transfer():
    from app.money import per_payer_transfers

    meals = [{"payer_id": EMI, "shares": {EMI: 50_000, KUN: 50_000}}]
    transfers = per_payer_transfers(meals, [{"from": KUN, "to": EMI, "amount": 80_000}])
    assert transfers == []


def test_a_payment_still_pays_a_debt_down_within_the_window():
    from app.money import per_payer_transfers

    meals = [{"payer_id": EMI, "shares": {EMI: 50_000, KUN: 50_000}}]
    transfers = per_payer_transfers(meals, [{"from": KUN, "to": EMI, "amount": 20_000}])
    assert [(t.from_member, t.to_member, t.amount) for t in transfers] == [(KUN, EMI, 30_000)]


def test_opposing_debts_from_real_meals_still_net():
    """The floor is per-pair-per-direction, so genuine two-way debt still nets."""
    from app.money import per_payer_transfers

    meals = [
        {"payer_id": LINH, "shares": {LINH: 61_000, GIANG: 61_000}},
        {"payer_id": GIANG, "shares": {GIANG: 75_000, LINH: 75_000}},
    ]
    transfers = per_payer_transfers(meals, [])
    assert [(t.from_member, t.to_member, t.amount) for t in transfers] == [(LINH, GIANG, 14_000)]


# --- targeted payments must not evaporate ----------------------------------- #

def _edge(debtor, creditor, meal_id, amount, day=1):
    from datetime import date

    from app.money import DebtEdge
    return DebtEdge(debtor=debtor, creditor=creditor, meal_id=meal_id, dish=None,
                    occurred_on=date(2026, 7, day), amount=amount)


def test_a_targeted_payment_for_a_missing_meal_falls_back_to_the_pair():
    """⑦ quick-pay stamps meal_id; editing that meal re-records it under a NEW id.
    The payment used to bind to the vanished edge and be discarded, so the payer's
    statement showed their share unpaid while the settlement counted it."""
    from app.money import apply_payments_fifo

    edges = apply_payments_fifo(
        [_edge(1, 2, meal_id=99, amount=55_000)],
        [{"from": 1, "to": 2, "amount": 50_000, "meal_id": 42}],   # 42 was voided
    )
    assert [e.paid for e in edges] == [50_000]
    assert [e.outstanding for e in edges] == [5_000]


def test_a_targeted_overpayment_spills_onto_the_pair_s_other_meals():
    from app.money import apply_payments_fifo

    edges = apply_payments_fifo(
        [_edge(1, 2, meal_id=1, amount=50_000, day=1), _edge(1, 2, meal_id=2, amount=30_000, day=2)],
        [{"from": 1, "to": 2, "amount": 70_000, "meal_id": 1}],
    )
    by_meal = {e.meal_id: e.outstanding for e in edges}
    assert by_meal == {1: 0, 2: 10_000}


def test_a_targeted_payment_still_settles_its_own_meal_first():
    """The point of meal_id: pay off the meal you chose, not the oldest one."""
    from app.money import apply_payments_fifo

    edges = apply_payments_fifo(
        [_edge(1, 2, meal_id=1, amount=50_000, day=1), _edge(1, 2, meal_id=2, amount=50_000, day=2)],
        [{"from": 1, "to": 2, "amount": 50_000, "meal_id": 2}],
    )
    assert {e.meal_id: e.outstanding for e in edges} == {1: 50_000, 2: 0}


def test_net_transfers_and_per_payer_transfers_agree():
    """Two ways to the same answer; they share the netting step so they cannot
    drift. per_payer_transfers is kept for callers holding only meals+payments."""
    from app.money import apply_payments_fifo, net_transfers, per_payer_transfers

    meals = [
        {"meal_id": 1, "payer_id": 2, "occurred_on": __import__("datetime").date(2026, 7, 1),
         "dish": None, "shares": {1: 40_000, 2: 40_000}},
        {"meal_id": 2, "payer_id": 1, "occurred_on": __import__("datetime").date(2026, 7, 2),
         "dish": None, "shares": {1: 30_000, 2: 30_000}},
    ]
    payments = [{"from": 1, "to": 2, "amount": 10_000, "meal_id": None}]
    from app.money import build_debt_edges
    edges = apply_payments_fifo(build_debt_edges(meals), payments)

    a = [(t.from_member, t.to_member, t.amount) for t in net_transfers(edges)]
    b = [(t.from_member, t.to_member, t.amount) for t in per_payer_transfers(meals, payments)]
    assert a == b
