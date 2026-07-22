import asyncio

import pytest

from app import chat
from tests.test_ledger import _seed_room


class _FakeResult:
    turn_id = "turn-1"
    final_text = "ok"
    error = None

    def __init__(self, payment):
        self._payment = payment

    def last_result(self, name):
        return self._payment if name == "propose_payment" else None


@pytest.fixture
def room(db):
    room_id, (a, b) = _seed_room(db, 2)
    ids = {"room": room_id, "alice": a, "bob": b}
    return db, ids


def test_payment_proposal_creates_payment_draft(room, monkeypatch):
    db, ids = room
    payment = {"type": "payment_draft", "from_member_id": ids["alice"],
               "to_member_id": ids["bob"], "amount": 50000, "note": None}

    async def fake_run_turn(*a, **k):
        return _FakeResult(payment)

    monkeypatch.setattr("app.agent.run_turn", fake_run_turn)
    msg = asyncio.run(chat.run_bot_turn(db, ids["room"], ids["alice"], "Alice", "@bot alice trả bob rồi"))
    assert msg.kind == "payment_draft"
    assert (msg.attachments or {})["amount"] == 50000
