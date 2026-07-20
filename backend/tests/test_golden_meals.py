from datetime import date

import pytest

from app import drafts, ledger
from app.models import Meal, RoomMessage
from tests.golden.meals import CASES
from tests.test_ledger import _seed_room


def _payload(case, ids):
    idx = {i + 1: ids[i] for i in range(len(ids))}
    return {
        "payer_member_id": idx[case["payer"]],
        "member_participants": [idx[p] for p in case["participants"]],
        "guests": case.get("guests", []),
        "bill_total": case["total"],
        "adjustments": [{"member": idx[a["member"]], "amount": a["amount"]}
                        for a in case.get("adjustments", [])],
        "dish": case.get("dish"), "initiator": case.get("initiator"),
        "note": case.get("note"), "per_head_preview": 0, "raw_input": "golden",
    }


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_meal(db, case):
    room_id, ids = _seed_room(db, 4)
    idx = {i + 1: ids[i] for i in range(4)}
    with db.session() as s:
        d, _extras = drafts.create_draft(s, room_id, _payload(case, ids))
        meal_msg = drafts.commit_draft(s, d.id, room_id, logged_by=str(idx[1]))
        meal = s.get(Meal, meal_msg.attachments["meal_id"])
        # shares
        got_shares = {sh.member_id: sh.share_amount for sh in meal.shares}
        want_shares = {idx[k]: v for k, v in case["shares"].items()}
        assert got_shares == want_shares, case["id"]
        # tracked total persisted
        assert meal.total_amount == case["tracked"], case["id"]
        assert sum(got_shares.values()) == case["tracked"], case["id"]
        # balances
        bal = ledger.period_balances(s, room_id, None, date(2999, 1, 1))
        for member_idx, want in case["balances"].items():
            assert bal[idx[member_idx]]["balance"] == want, f'{case["id"]} m{member_idx}'
        # metadata
        for k, v in case.get("expect_meta", {}).items():
            assert getattr(meal, k) == v, f'{case["id"]} {k}'


def test_golden_G9_supersede_autocommit(db):
    room_id, ids = _seed_room(db, 4)
    idx = {i + 1: ids[i] for i in range(4)}
    with db.session() as s:
        d1, _ = drafts.create_draft(s, room_id, _payload(CASES[0], ids))  # G1
        d2, _ = drafts.create_draft(s, room_id, _payload(CASES[1], ids))  # G2 supersedes
        assert s.get(RoomMessage, d1.id).attachments["status"] == "committed"
        assert s.get(RoomMessage, d2.id).attachments["status"] == "pending"
        assert s.query(Meal).count() == 1


def test_golden_G10_cancel_writes_nothing(db):
    room_id, ids = _seed_room(db, 4)
    with db.session() as s:
        d, _ = drafts.create_draft(s, room_id, _payload(CASES[0], ids))
        drafts.update_draft(s, d.id, room_id, {"status": "cancelled"})
        assert s.query(Meal).count() == 0


def test_golden_G11_edit_then_supersede(db):
    room_id, ids = _seed_room(db, 4)
    idx = {i + 1: ids[i] for i in range(4)}
    with db.session() as s:
        d, _ = drafts.create_draft(s, room_id, _payload(CASES[0], ids))         # [1,2,3,4]
        drafts.update_draft(s, d.id, room_id, {"member_participants": [idx[1], idx[2]]})
        drafts.create_draft(s, room_id, _payload(CASES[1], ids))              # supersede
        meal = s.query(Meal).one()
        assert {sh.member_id for sh in meal.shares} == {idx[1], idx[2]}
