"""'A pays X, the rest split evenly' — production 2026-10-05: "cơm tấm 335k, emi 110k, linh,
gh, nhím". The bot could not do it (no tool option, and it may not compute 225,000 ÷ 3
itself), so Emi typed the amounts by hand. Now the tool takes the said amounts and splits
the rest."""
import pytest

from app.money import MoneyError, itemized_adjustments, split_shares
from ledger_core.money import fixed_then_even


def test_the_production_case_emi_110k_and_the_rest_split_three_ways():
    emi, linh, gh, nhim = 4, 9, 8, 6
    shares = fixed_then_even(335_000, [emi, linh, gh, nhim], {emi: 110_000}, payer_id=emi)
    assert shares == {emi: 110_000, linh: 75_000, gh: 75_000, nhim: 75_000}


def test_the_leftover_dong_goes_to_the_payer_first_then_in_order_and_nothing_is_lost():
    shares = fixed_then_even(100_000, [1, 2, 3, 4], {1: 30_001}, payer_id=3)
    assert shares == {1: 30_001, 2: 23_333, 3: 23_333, 4: 23_333}
    shares = fixed_then_even(100_001, [1, 2, 3, 4], {1: 30_000}, payer_id=3)
    assert shares[3] == 23_334 and sum(shares.values()) == 100_001
    shares = fixed_then_even(100_002, [1, 2, 3, 4], {1: 30_000}, payer_id=1)   # payer is the fixed one
    assert shares == {1: 30_000, 2: 23_334, 3: 23_334, 4: 23_334}


def test_several_fixed_people_and_a_zero_fixed_amount():
    assert fixed_then_even(300_000, [1, 2, 3], {1: 100_000, 2: 0}) == {1: 100_000, 2: 0, 3: 200_000}


@pytest.mark.parametrize("total, participants, fixed, needle", [
    (100_000, [1, 2], {3: 10_000}, "not among the participants"),
    (100_000, [1, 2], {1: 50_000, 2: 50_000}, "nobody is left"),
    (100_000, [1, 2], {1: 120_000}, "more than the bill"),
    (100_000, [1, 2], {1: -1}, "negative"),
    (0, [1, 2], {1: 0}, "greater than 0"),
])
def test_impossible_splits_are_refused_with_a_reason(total, participants, fixed, needle):
    with pytest.raises(MoneyError, match=needle):
        fixed_then_even(total, participants, fixed)


@pytest.mark.parametrize("total", [335_000, 100_001, 99_999, 7])
def test_the_ledger_encoding_reproduces_the_shares_exactly(total):
    participants, fixed = [1, 2, 3, 4], {2: min(total, 5)}
    shares = fixed_then_even(total, participants, fixed, payer_id=1)
    assert split_shares(total, participants, itemized_adjustments(total, shares), payer_id=1) == shares


def _tools(db, n=4):
    from app.tools import ToolContext, build_tools
    from tests.test_ledger import _seed_room

    room_id, ids = _seed_room(db, n)
    return room_id, ids, build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=ids[0],
                                                 sender_name="Emi", turn_mentions=[]))


def test_propose_meal_takes_the_said_amount_and_splits_the_rest(db):
    room_id, ids, tools = _tools(db)
    out = tools["propose_meal"].execute({
        "payer": ids[0], "participants": list(ids), "total": 335_000,
        "fixed": [{"member": ids[0], "amount": 110_000}], "dish": "cơm tấm"})
    assert out["ok"], out
    shares = {r["member"]: r["amount"] for r in out["shares_preview"]}
    assert shares == {ids[0]: 110_000, ids[1]: 75_000, ids[2]: 75_000, ids[3]: 75_000}
    assert out["fixed"] == [{"member": ids[0], "amount": 110_000}]


@pytest.mark.parametrize("extra, needle", [
    ({"items": [{"member": 1, "amount": 1}]}, "alone"),
    ({"adjustments": [{"member": 1, "amount": 1}]}, "alone"),
    ({"guests": ["Khách"]}, "guests"),
])
def test_propose_meal_refuses_fixed_mixed_with_other_modes(db, extra, needle):
    room_id, ids, tools = _tools(db)
    out = tools["propose_meal"].execute({"payer": ids[0], "participants": list(ids), "total": 335_000,
                                         "fixed": [{"member": ids[0], "amount": 110_000}], **extra})
    assert out["ok"] is False and needle in out["error"]


def test_propose_meal_explains_an_impossible_fixed_split(db):
    room_id, ids, tools = _tools(db)
    out = tools["propose_meal"].execute({"payer": ids[0], "participants": list(ids), "total": 100_000,
                                         "fixed": [{"member": ids[0], "amount": 150_000}]})
    assert out["ok"] is False and "more than the bill" in out["error"]


def test_editing_the_card_re_splits_the_rest_and_confirming_records_those_shares(db):
    from app import drafts
    from ledger_core.models import Meal

    room_id, ids, tools = _tools(db)
    out = tools["propose_meal"].execute({"payer": ids[0], "participants": list(ids), "total": 335_000,
                                         "fixed": [{"member": ids[0], "amount": 110_000}]})
    with db.session() as s:
        d, _ = drafts.create_draft(s, room_id, {k: v for k, v in out.items() if k not in ("ok", "type")}
                                   | {"raw_input": "@phoenix test"})
        # the bill was 365k, not 335k: Emi's 110k stays, the others now split 255k
        drafts.update_draft(s, d.id, room_id, {"bill_total": 365_000})
        msg = drafts.commit_draft(s, d.id, room_id, logged_by=str(ids[0]))
        meal = s.get(Meal, msg.attachments["meal_id"])
        shares = {sh.member_id: sh.share_amount for sh in meal.shares}
    assert shares == {ids[0]: 110_000, ids[1]: 85_000, ids[2]: 85_000, ids[3]: 85_000}
