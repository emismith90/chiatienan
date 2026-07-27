"""End-to-end itemized meal: propose_meal(items=…) → draft card → ledger.

Replays the production conversation that motivated this. Emi paid a 324.200đ
Grab bill whose six line items list at 413.200đ, and said who ate what. The bot
used to charge everyone 54.000đ and file "who ate what" as a decorative note.
"""
import pytest

from app import drafts, ledger
from app.models import Meal
from app.money import MoneyError
from app.tools import ToolContext, build_tools
from tests.test_ledger import _seed_room

PAID = 324_200
# Emi bò, Nhím gà, Giang mỳ cua, Linh cơm tấm, Kun cơm tấm, Tabu bò rim
PRICES = [69_500, 69_500, 94_200, 69_000, 69_000, 42_000]
LABELS = ["bò", "gà", "mỳ cua", "cơm tấm", "cơm tấm", "bò rim"]


def _grab(db):
    room_id, ids = _seed_room(db, 6)
    tools = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=ids[0],
                                    sender_name="Emi"))
    items = [{"member": mid, "amount": amt, "label": lab}
             for mid, amt, lab in zip(ids, PRICES, LABELS)]
    return room_id, ids, tools, items


def test_the_grab_bill_records_per_person_not_evenly(db):
    room_id, ids, tools, items = _grab(db)

    out = tools["propose_meal"].execute({
        "payer": ids[0], "participants": ids, "total": PAID, "items": items,
    })
    assert out["ok"] is True

    with db.session() as s:
        draft, _ = drafts.create_draft(s, room_id, dict(out))
        meal_msg = drafts.commit_draft(s, draft.id, room_id, logged_by=str(ids[0]))
        shares = {row["id"]: row["amount"] for row in meal_msg.attachments["shares"]}

    assert sum(shares.values()) == PAID
    # Nobody pays the flat 54.033đ the old even split would have charged.
    assert all(v != PAID // 6 for v in shares.values())
    # Ordering follows the menu: mỳ cua > bò/gà > cơm tấm > bò rim.
    assert shares[ids[2]] > shares[ids[0]] > shares[ids[3]] > shares[ids[5]]
    # Linh and Kun shared one "2× cơm tấm" line, so they pay the same.
    assert shares[ids[3]] == shares[ids[4]]
    # The 89.000đ promo is spread, not dumped on one person: everyone pays
    # roughly 78% of list price.
    for mid, price in zip(ids, PRICES):
        assert abs(shares[mid] - price * PAID / sum(PRICES)) <= 1


def test_committed_meal_keeps_the_item_breakdown(db):
    room_id, ids, tools, items = _grab(db)
    out = tools["propose_meal"].execute({
        "payer": ids[0], "participants": ids, "total": PAID, "items": items,
    })
    with db.session() as s:
        draft, _ = drafts.create_draft(s, room_id, dict(out))
        assert [i["label"] for i in draft.attachments["items"]] == LABELS
        meal_msg = drafts.commit_draft(s, draft.id, room_id, logged_by=str(ids[0]))
        assert [i["label"] for i in meal_msg.attachments["items"]] == LABELS


def test_editing_the_total_on_the_card_re_prorates_the_discount(db):
    """The card is the human check. Correcting the total there must move every
    share — leaving a stale split behind is exactly the silent-wrong-number
    failure the draft flow exists to prevent."""
    room_id, ids, tools, items = _grab(db)
    out = tools["propose_meal"].execute({
        "payer": ids[0], "participants": ids, "total": PAID, "items": items,
    })
    with db.session() as s:
        draft, _ = drafts.create_draft(s, room_id, dict(out))
        before = {a["member"]: a["amount"] for a in draft.attachments["adjustments"]}
        drafts.update_draft(s, draft.id, room_id, {"bill_total": sum(PRICES)})
        after = {a["member"]: a["amount"] for a in s.get(type(draft), draft.id).attachments["adjustments"]}
        meal_msg = drafts.commit_draft(s, draft.id, room_id, logged_by=str(ids[0]))
        shares = {row["id"]: row["amount"] for row in meal_msg.attachments["shares"]}

    assert before != after
    # No discount now: everyone pays exactly their own list price.
    assert shares == dict(zip(ids, PRICES))


def test_editing_a_price_on_the_card_re_prorates(db):
    room_id, ids, tools, items = _grab(db)
    out = tools["propose_meal"].execute({
        "payer": ids[0], "participants": ids, "total": PAID, "items": items,
    })
    fixed = [dict(i) for i in out["items"]]
    fixed[5]["amount"] = 100_000  # Tabu's bò rim was misread off the bill
    with db.session() as s:
        draft, _ = drafts.create_draft(s, room_id, dict(out))
        drafts.update_draft(s, draft.id, room_id, {"items": fixed})
        meal_msg = drafts.commit_draft(s, draft.id, room_id, logged_by=str(ids[0]))
        shares = {row["id"]: row["amount"] for row in meal_msg.attachments["shares"]}
    assert sum(shares.values()) == PAID
    assert shares[ids[5]] > shares[ids[3]]  # now the priciest-but-one


def test_dropping_a_participant_without_dropping_their_item_is_rejected(db):
    """Rejected loudly rather than billing a stranger or silently reweighting."""
    room_id, ids, tools, items = _grab(db)
    out = tools["propose_meal"].execute({
        "payer": ids[0], "participants": ids, "total": PAID, "items": items,
    })
    with db.session() as s:
        draft, _ = drafts.create_draft(s, room_id, dict(out))
        with pytest.raises(MoneyError):
            drafts.update_draft(s, draft.id, room_id, {"member_participants": ids[:5]})


def test_items_are_optional_equal_split_is_untouched(db):
    room_id, ids, tools, _items = _grab(db)
    out = tools["propose_meal"].execute({
        "payer": ids[0], "participants": ids, "total": 300_000,
    })
    assert out["items"] == []
    assert out["adjustments"] == []
    assert out["per_head_preview"] == 50_000


def test_items_plus_adjustments_is_rejected_as_ambiguous(db):
    room_id, ids, tools, items = _grab(db)
    out = tools["propose_meal"].execute({
        "payer": ids[0], "participants": ids, "total": PAID, "items": items,
        "adjustments": [{"member": ids[0], "amount": 10_000}],
    })
    assert out["ok"] is False
    assert "items" in out["error"]


def test_items_with_guests_is_refused_with_a_way_forward(db):
    """Guest heads and per-item shares don't compose yet — say so instead of
    quietly splitting the guest's food across the members."""
    room_id, ids, tools, items = _grab(db)
    out = tools["propose_meal"].execute({
        "payer": ids[0], "participants": ids, "total": PAID, "items": items,
        "guests": ["Khách"],
    })
    assert out["ok"] is False
    assert "khách" in out["error"].lower()


def test_missing_price_is_a_clarifying_question_not_a_crash(db):
    room_id, ids, tools, items = _grab(db)
    out = tools["propose_meal"].execute({
        "payer": ids[0], "participants": ids, "total": PAID, "items": items[:4],
    })
    assert out["ok"] is False
    assert "thiếu" in out["error"]


def test_items_summing_over_the_total_no_longer_blocks_the_record(db):
    """The old failure mode: the tool rejected item prices that exceeded what
    was paid, and the bot bounced the arithmetic back to the user four times."""
    room_id, ids, tools, items = _grab(db)
    assert sum(PRICES) > PAID
    out = tools["propose_meal"].execute({
        "payer": ids[0], "participants": ids, "total": PAID, "items": items,
    })
    assert out["ok"] is True
    assert sum(a["amount"] for a in out["shares_preview"]) == PAID


def test_each_diner_owes_the_payer_exactly_their_own_dish(db):
    room_id, ids, tools, items = _grab(db)
    out = tools["propose_meal"].execute({
        "payer": ids[0], "participants": ids, "total": PAID, "items": items,
    })
    with db.session() as s:
        draft, _ = drafts.create_draft(s, room_id, dict(out))
        drafts.commit_draft(s, draft.id, room_id, logged_by=str(ids[0]))
        meal = s.query(Meal).one()
        shares = {sh.member_id: sh.share_amount for sh in meal.shares}
        balances = ledger.period_balances(s, room_id, None, meal.occurred_on)

    assert meal.total_amount == PAID          # no guests: whole bill is tracked
    assert sum(shares.values()) == PAID
    # The payer is owed the bill minus their own share; each diner owes theirs.
    assert balances[ids[0]]["balance"] == PAID - shares[ids[0]]
    for mid in ids[1:]:
        assert balances[mid]["balance"] == -shares[mid]
