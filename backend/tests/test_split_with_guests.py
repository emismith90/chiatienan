import pytest

from app.money import MoneyError, split_with_guests


def test_no_guests_matches_plain_split():
    r = split_with_guests(400_000, [1, 2, 3, 4], 0, payer_id=1)
    assert r["shares"] == {1: 100_000, 2: 100_000, 3: 100_000, 4: 100_000}
    assert r["tracked_total"] == 400_000
    assert r["guest_total"] == 0
    assert r["per_head"] == 100_000
    assert r["headcount"] == 4


def test_one_guest_pays_cash():
    # 400k over 4 heads (3 members + 1 guest) = 100k/head; members tracked, guest dropped
    r = split_with_guests(400_000, [1, 2, 3], 1, payer_id=1)
    assert r["shares"] == {1: 100_000, 2: 100_000, 3: 100_000}
    assert r["tracked_total"] == 300_000
    assert r["guest_total"] == 100_000
    assert r["per_head"] == 100_000
    assert r["headcount"] == 4


def test_two_guests_pay_cash():
    r = split_with_guests(400_000, [1, 2], 2, payer_id=2)
    assert r["shares"] == {1: 100_000, 2: 100_000}
    assert r["tracked_total"] == 200_000
    assert r["guest_total"] == 200_000


def test_remainder_stays_on_payer_member():
    # 100k over 3 heads (2 members + 1 guest): base 33333, remainder 1 → payer(1)
    r = split_with_guests(100_000, [1, 2], 1, payer_id=1)
    assert r["shares"] == {1: 33_334, 2: 33_333}
    assert r["tracked_total"] == 66_667
    assert r["guest_total"] == 33_333
    assert sum(r["shares"].values()) == r["tracked_total"]


def test_adjustment_on_member_with_guest():
    # 300k, members [1,2] + 1 guest; member 2 +30k
    r = split_with_guests(300_000, [1, 2], 1, {2: 30_000}, payer_id=1)
    # base = (300000 - 30000) // 3 = 90000 ; member2 = 120000, member1 = 90000, guest 90000
    assert r["shares"] == {1: 90_000, 2: 120_000}
    assert r["tracked_total"] == 210_000
    assert r["guest_total"] == 90_000


def test_rejects_no_members():
    with pytest.raises(MoneyError):
        split_with_guests(100_000, [], 2, payer_id=1)


def test_rejects_zero_total():
    with pytest.raises(MoneyError):
        split_with_guests(0, [1, 2], 1, payer_id=1)


def test_rejects_adjustment_for_non_member():
    with pytest.raises(MoneyError):
        split_with_guests(100_000, [1, 2], 1, {9: 10_000}, payer_id=1)
