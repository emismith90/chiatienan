import asyncio

import pytest

from app import chat
from tests.test_ledger import _seed_room


class _FakeResult:
    turn_id = "turn-1"
    final_text = "ok"
    error = None

    def __init__(self, payments):
        self._payments = payments

    def last_result(self, name):
        return None

    def all_results(self, name):
        return self._payments if name == "propose_payment" else []


@pytest.fixture
def room(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    ids = {"room": room_id, "alice": a, "bob": b, "carol": c}
    return db, ids


def test_payment_proposal_creates_payment_draft(room, monkeypatch):
    db, ids = room
    payments = [{"type": "payment_draft", "from_member_id": ids["alice"],
                 "to_member_id": ids["bob"], "amount": 50000, "note": None}]

    async def fake_run_turn(*a, **k):
        return _FakeResult(payments)

    monkeypatch.setattr("app.agent.run_turn", fake_run_turn)
    msg = asyncio.run(chat.run_bot_turn(db, ids["room"], ids["alice"], "Alice", "@bot alice trả bob rồi"))
    assert msg.kind == "payment_draft"
    transfers = (msg.attachments or {})["transfers"]
    assert len(transfers) == 1
    assert transfers[0]["amount"] == 50000


def test_multi_payer_proposals_create_one_payment_draft(room, monkeypatch):
    db, ids = room
    payments = [
        {"type": "payment_draft", "from_member_id": ids["alice"],
         "to_member_id": ids["carol"], "amount": 30000, "note": None},
        {"type": "payment_draft", "from_member_id": ids["bob"],
         "to_member_id": ids["carol"], "amount": 20000, "note": None},
    ]

    async def fake_run_turn(*a, **k):
        return _FakeResult(payments)

    monkeypatch.setattr("app.agent.run_turn", fake_run_turn)
    msg = asyncio.run(chat.run_bot_turn(db, ids["room"], ids["alice"], "Alice",
                                        "@bot alice và bob trả carol rồi"))
    assert msg.kind == "payment_draft"
    transfers = (msg.attachments or {})["transfers"]
    assert len(transfers) == 2


def test_same_pair_proposals_collapse_to_last(room, monkeypatch):
    db, ids = room
    # Model self-correction: "100k… actually 150k" for the SAME (from,to) pair.
    payments = [
        {"type": "payment_draft", "from_member_id": ids["alice"],
         "to_member_id": ids["bob"], "amount": 100000, "note": None},
        {"type": "payment_draft", "from_member_id": ids["alice"],
         "to_member_id": ids["bob"], "amount": 150000, "note": None},
    ]

    async def fake_run_turn(*a, **k):
        return _FakeResult(payments)

    monkeypatch.setattr("app.agent.run_turn", fake_run_turn)
    msg = asyncio.run(chat.run_bot_turn(db, ids["room"], ids["alice"], "Alice",
                                        "@bot alice trả bob 100k à nhầm 150k"))
    assert msg.kind == "payment_draft"
    transfers = (msg.attachments or {})["transfers"]
    assert len(transfers) == 1
    assert transfers[0]["amount"] == 150000
