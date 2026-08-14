"""Itemized ("ai ăn nấy trả") meals — the split where the bill ≠ Σ line items.

The case that motivated this: a Grab bill whose line items add up to 413.200đ
while the payer was actually charged 324.200đ after a promo. Charging list price
overcharges the group; refusing to record it (or asking a human to work out six
"after discount" numbers) is what the bot used to do. The discount belongs to
whoever ordered the pricier dish, so it is prorated.
"""
import pytest

from app.money import (
    MoneyError,
    itemized_adjustments,
    normalize_items,
    prorate_items,
    split_shares,
)

# The real bill: Emi bò 69.5k, Nhím gà 69.5k, Giang mỳ cua 94.2k,
# Linh + Kun cơm tấm 69k each (one 2× line of 138k), Tabu bò rim 42k.
GRAB_ITEMS = {1: 69_500, 2: 69_500, 3: 94_200, 4: 69_000, 5: 69_000, 6: 42_000}
GRAB_PAID = 324_200


def test_discount_is_prorated_and_shares_sum_to_what_was_paid():
    shares = prorate_items(GRAB_PAID, GRAB_ITEMS)
    assert sum(shares.values()) == GRAB_PAID
    # Everyone pays less than list price, in proportion to what they ordered.
    assert all(shares[m] < GRAB_ITEMS[m] for m in GRAB_ITEMS)
    assert shares[3] > shares[1] > shares[4] > shares[6]


def test_a_fee_scales_the_other_way():
    """Ship/service pushes the bill above the items — same proration, upward."""
    shares = prorate_items(500_000, {1: 100_000, 2: 300_000})
    assert sum(shares.values()) == 500_000
    assert shares == {1: 125_000, 2: 375_000}


def test_no_discount_means_everyone_pays_their_own_dish():
    items = {1: 40_000, 2: 60_000}
    assert prorate_items(100_000, items) == items


def test_leftover_dong_goes_to_the_largest_remainder_not_nowhere():
    # 3 × 1đ items out of 10đ: 3.33đ each → 3,3,3 floors, 1đ leftover.
    shares = prorate_items(10, {5: 1, 7: 1, 9: 1})
    assert sum(shares.values()) == 10
    assert sorted(shares.values()) == [3, 3, 4]
    # Deterministic: an exact three-way tie breaks by member id.
    assert shares[5] == 4


def test_zero_item_pays_nothing():
    shares = prorate_items(90_000, {1: 0, 2: 50_000, 3: 40_000})
    assert shares[1] == 0
    assert sum(shares.values()) == 90_000


@pytest.mark.parametrize("total,items", [
    (0, {1: 10}),
    (-1, {1: 10}),
    (100, {}),
    (100, {1: 0, 2: 0}),
    (100, {1: -5, 2: 105}),
])
def test_invalid_itemized_inputs_raise(total, items):
    with pytest.raises(MoneyError):
        prorate_items(total, items)


def test_adjustments_encoding_reproduces_the_itemized_shares_exactly():
    """`itemized_adjustments` is the bridge to the normal ledger path — round-trip
    it through `split_shares`, which is what `record_meal` actually calls."""
    shares = prorate_items(GRAB_PAID, GRAB_ITEMS)
    adj = itemized_adjustments(GRAB_PAID, shares)
    assert split_shares(GRAB_PAID, list(GRAB_ITEMS), adj, payer_id=1) == shares


@pytest.mark.parametrize("total", [1, 5, 6, 7, 100, 999_999, 1_000_000])
@pytest.mark.parametrize("gross", [3, 7, 413_200])
def test_round_trip_holds_across_awkward_totals(total, gross):
    per = max(1, gross // 6)
    items = {i: per * i for i in range(1, 7)}
    shares = prorate_items(total, items)
    assert sum(shares.values()) == total
    adj = itemized_adjustments(total, shares)
    assert split_shares(total, list(items), adj, payer_id=3) == shares


def test_normalize_items_orders_by_participant_and_keeps_labels():
    items = normalize_items(
        [{"member": 2, "amount": 5, "label": "gà"}, {"member": 1, "amount": 7}], [1, 2]
    )
    assert items == [
        {"member": 1, "amount": 7, "label": None},
        {"member": 2, "amount": 5, "label": "gà"},
    ]


def test_a_participant_with_no_item_is_rejected_not_billed_zero():
    """A misread bill line must surface, not silently move someone's food onto
    everyone else."""
    with pytest.raises(MoneyError, match="thiếu"):
        normalize_items([{"member": 1, "amount": 10}], [1, 2])


def test_item_for_a_non_participant_is_rejected():
    with pytest.raises(MoneyError, match="not part of the meal"):
        normalize_items([{"member": 1, "amount": 10}, {"member": 9, "amount": 10}], [1])


def test_duplicate_item_rows_are_rejected():
    with pytest.raises(MoneyError, match="nhiều hơn một"):
        normalize_items([{"member": 1, "amount": 10}, {"member": 1, "amount": 20}], [1])


def test_malformed_item_row_is_rejected():
    with pytest.raises(MoneyError):
        normalize_items([{"member": 1}], [1])


def test_propose_meal_accepts_the_equal_discount_split(db):
    """One tool call for what the room did by hand: Σ items 414,200đ, paid
    324,200đ, gap split six ways."""
    from app.tools import ToolContext, build_tools
    from tests.test_ledger import _seed_room

    room_id, ids = _seed_room(db, 6)
    prices = [69_500, 69_500, 94_200, 69_500, 69_500, 42_000]
    tools = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=ids[0],
                                   sender_name="Emi", turn_mentions=[]))
    out = tools["propose_meal"].execute({
        "payer": ids[0], "participants": list(ids), "total": 324_200,
        "items": [{"member": m, "amount": p} for m, p in zip(ids, prices)],
        "discount_split": "equal",
    })

    assert out["ok"], out
    assert out["discount_split"] == "equal"
    shares = {row["member"]: row["amount"] for row in out["shares_preview"]}
    assert sum(shares.values()) == 324_200
    assert shares[ids[5]] == 27_000        # 42,000 dish − 15,000 gap share
    assert shares[ids[2]] == 79_200        # 94,200 dish − 15,000


def test_propose_meal_defaults_to_proportional(db):
    from app.tools import ToolContext, build_tools
    from tests.test_ledger import _seed_room

    room_id, ids = _seed_room(db, 2)
    tools = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=ids[0],
                                   sender_name="Emi", turn_mentions=[]))
    out = tools["propose_meal"].execute({
        "payer": ids[0], "participants": list(ids), "total": 100_000,
        "items": [{"member": ids[0], "amount": 100_000}, {"member": ids[1], "amount": 20_000}],
    })
    assert out["discount_split"] == "proportional"
    shares = {row["member"]: row["amount"] for row in out["shares_preview"]}
    assert shares[ids[1]] == 16_667       # scaled, not 10,000


def test_editing_an_equal_split_draft_keeps_it_equal(db):
    """The card patches a fixed field list without discount_split, so _sync_items
    reads it back off the draft — otherwise a price edit silently re-prorates."""
    from app import drafts
    from tests.test_ledger import _seed_room

    room_id, ids = _seed_room(db, 2)
    with db.session() as s:
        d, _ = drafts.create_draft(s, room_id, {
            "payer_member_id": ids[0], "member_participants": list(ids), "guests": [],
            "bill_total": 100_000, "adjustments": [], "discount_split": "equal",
            "items": [{"member": ids[0], "amount": 100_000}, {"member": ids[1], "amount": 20_000}],
            "dish": None, "initiator": None, "note": None, "per_head_preview": 0,
            "raw_input": "@phoenix test",
        })
        updated = drafts.update_draft(s, d.id, room_id, {"bill_total": 100_000})
        by_member = {a["member"]: a["amount"] for a in updated.attachments["adjustments"]}
        # Shares are stored as offsets from the even split. Equal keeps the
        # 20,000 gap at 10,000 each (shares 90,000 / 10,000 -> ±40,000);
        # proportional would have re-derived ±33,333.
        assert by_member == {ids[0]: 40_000, ids[1]: -40_000}
        assert updated.attachments["discount_split"] == "equal"
