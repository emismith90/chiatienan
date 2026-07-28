from datetime import date

from fastapi.testclient import TestClient  # noqa: F401


def test_ledger_endpoint_since_last(api_client_room):
    # api_client_room: fixture giving (client, headers, room_id, members-by-name)
    client, headers, room_id, m = api_client_room
    from app.db import get_db
    from app import ledger
    with get_db().session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=m["Linh"],
                           participants=[m["Linh"], m["Giang"]], total_amount=122000,
                           dish="bun bo", occurred_on=date(2026, 7, 21))
    r = client.get(f"/api/rooms/{room_id}/ledger", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["period"]["keyword"] == "since_last"
    assert any(e["kind"] == "meal" and e["payer_name"] == "Linh" for e in data["timeline"])
    # Who owes whom, by name and direction — not a per-person net column.
    assert data["outstanding"] == [
        {"debtor_id": m["Giang"], "debtor_name": "Giang",
         "creditor_id": m["Linh"], "creditor_name": "Linh", "amount": 61000},
    ]
    assert "balances" not in data
    assert "net" not in data["me"]


def test_ledger_endpoint_keeps_both_directions_of_a_two_way_debt(api_client_room):
    """A and B each fronted a meal the other ate. There is no netting here — that
    is settle_period's job, and only so it can print one QR. The panel shows both
    rows, because both are real and each is settled separately."""
    client, headers, room_id, m = api_client_room
    from app.db import get_db
    from app import ledger
    with get_db().session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=m["Linh"],
                           participants=[m["Linh"], m["Giang"]], total_amount=122000,
                           dish="bun bo", occurred_on=date(2026, 7, 21))
        ledger.record_meal(s, room_id=room_id, payer_member_id=m["Giang"],
                           participants=[m["Linh"], m["Giang"]], total_amount=40000,
                           dish="ca phe", occurred_on=date(2026, 7, 22))
    out = client.get(f"/api/rooms/{room_id}/ledger", headers=headers).json()["outstanding"]
    assert [(r["debtor_name"], r["creditor_name"], r["amount"]) for r in out] == [
        ("Giang", "Linh", 61000),
        ("Linh", "Giang", 20000),
    ]


# --- settled QRs must stop being payable ------------------------------------ #

def test_old_settlement_qrs_are_marked_settled_once_paid(db):
    """Production history holds 34 live QR links for debts already paid — two of
    them for a transfer that was never owed at all. Scroll back, tap one, pay
    twice. Annotated at read time so the whole backlog is corrected at once."""
    from datetime import date

    from app import chat, ledger
    from tests.test_ledger import _seed_room

    room_id, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        meal = ledger.record_meal(
            s, room_id=room_id, payer_member_id=b, participants=[a, b],
            total_amount=80_000, adjustments={}, guests=[], dish="bún chả",
            occurred_on=date(2026, 7, 23), logged_by=str(b),
        )
        card = chat.post_message(s, room_id, None, "Tạm tính…", kind="bot", attachments={
            "type": "settlement",
            "period": {"from": None, "to": "2026-07-23"},
            "transfers": [{"from_id": a, "to_id": b, "from_name": "A", "to_name": "B",
                           "amount": meal["shares"][a], "note": "x",
                           "qr_url": "https://img.vietqr.io/image/X.png?amount=40000"}],
        })
        # Still owed -> the QR stays live.
        out = chat.annotate_settled_transfers(s, room_id, [chat.message_to_dict(card, None)])
        assert out[0]["attachments"]["transfers"][0]["settled"] is False

        ledger.record_payment(s, room_id=room_id, from_member_id=a, to_member_id=b,
                              amount=meal["shares"][a], logged_by=str(a))
        out = chat.annotate_settled_transfers(s, room_id, [chat.message_to_dict(card, None)])
        assert out[0]["attachments"]["transfers"][0]["settled"] is True
        # The stored card itself is untouched: history stays what was said.
        assert "settled" not in (card.attachments["transfers"][0])


def test_a_transfer_that_was_never_owed_is_marked_settled(db):
    """msg#153/#161 in production: "Linh → Giang 107,000đ", a direction that never
    existed. There is no payment to match, so it must read as not-payable."""
    from app import chat
    from tests.test_ledger import _seed_room

    room_id, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        card = chat.post_message(s, room_id, None, "Tạm tính…", kind="bot", attachments={
            "type": "settlement", "period": {"from": None, "to": "2026-07-27"},
            "transfers": [{"from_id": a, "to_id": b, "from_name": "A", "to_name": "B",
                           "amount": 107_000, "qr_url": "https://img.vietqr.io/x.png"}],
        })
        out = chat.annotate_settled_transfers(s, room_id, [chat.message_to_dict(card, None)])
        assert out[0]["attachments"]["transfers"][0]["settled"] is True


def test_annotation_leaves_other_message_kinds_alone(db):
    from app import chat
    from tests.test_ledger import _seed_room

    room_id, (a, _b) = _seed_room(db, 2)
    with db.session() as s:
        plain = chat.post_message(s, room_id, a, "trưa nay ăn gì")
        payloads = [chat.message_to_dict(plain, None)]
        assert chat.annotate_settled_transfers(s, room_id, payloads) == payloads


def test_ledger_endpoint_accepts_an_explicit_range(api_client_room):
    client, headers, room_id, _members = api_client_room
    r = client.get(f"/api/rooms/{room_id}/ledger?from=2026-07-20&to=2026-07-26", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["period"] == {"from": "2026-07-20", "to": "2026-07-26", "keyword": "explicit"}


def test_ledger_endpoint_rejects_a_bad_date(api_client_room):
    client, headers, room_id, _members = api_client_room
    r = client.get(f"/api/rooms/{room_id}/ledger?from=last-tuesday", headers=headers)
    assert r.status_code == 400
