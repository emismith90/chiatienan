"""POST /api/rooms/{room_id}/qr-requests — the ledger QR button.

Deterministic bot post: the server aggregates the caller's outstanding debt to
one creditor (same `debt_breakdown` nets as settlement), builds the VietQR, and
posts a `settlement`-shaped bot card — no LLM turn, money never through the
model (design D3). Reusing the settlement shape means
`annotate_settled_transfers` retires the QR automatically once paid.
"""
from datetime import date

from app.models import Member


def _giang_headers(client, headers, room_id):
    """Re-identify as Giang (the debtor); the fixture authenticates as Linh."""
    inv = client.get(f"/api/rooms/{room_id}/invite", headers=headers).json()["invite_token"]
    tok = client.post(f"/api/rooms/{inv}/identify",
                      json={"nickname": "giang", "pin": "1234"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def _give_bank_details(member_id):
    from app.db import get_db
    with get_db().session() as s:
        m = s.get(Member, member_id)
        m.bank_code = "VCB"
        m.account_number = "0123456789"
        m.account_holder = "LINH NGUYEN"


def _two_meals_owed_to_linh(room_id, linh, giang):
    """Giang owes Linh 50k (pho) + 30k (bun) → 80k aggregate."""
    from app import ledger
    from app.db import get_db
    with get_db().session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=linh,
                           participants=[linh, giang], total_amount=100_000,
                           dish="pho", occurred_on=date(2026, 7, 21))
        ledger.record_meal(s, room_id=room_id, payer_member_id=linh,
                           participants=[linh, giang], total_amount=60_000,
                           dish="bun", occurred_on=date(2026, 7, 22))


def test_qr_request_aggregates_all_outstanding_to_that_creditor(api_client_room):
    client, headers, room_id, m = api_client_room
    _give_bank_details(m["Linh"])
    _two_meals_owed_to_linh(room_id, m["Linh"], m["Giang"])
    gheaders = _giang_headers(client, headers, room_id)

    r = client.post(f"/api/rooms/{room_id}/qr-requests",
                    json={"to": m["Linh"]}, headers=gheaders)
    assert r.status_code == 200, r.text
    assert r.json()["amount"] == 80_000

    msgs = client.get(f"/api/rooms/{room_id}/messages", headers=headers).json()["messages"]
    card = [x for x in msgs if (x.get("attachments") or {}).get("type") == "settlement"][-1]
    t = card["attachments"]["transfers"][0]
    assert t["from_id"] == m["Giang"] and t["to_id"] == m["Linh"]
    assert t["amount"] == 80_000
    assert "amount=80000" in t["qr_url"]
    assert "VCB-0123456789" in t["qr_url"]
    assert "80,000" in card["body"]
    # fresh card: the read-time annotation must not mark it settled
    assert t.get("settled") is not True


def test_qr_request_409_when_nothing_outstanding(api_client_room):
    client, headers, room_id, m = api_client_room
    _give_bank_details(m["Linh"])
    gheaders = _giang_headers(client, headers, room_id)
    r = client.post(f"/api/rooms/{room_id}/qr-requests",
                    json={"to": m["Linh"]}, headers=gheaders)
    assert r.status_code == 409


def test_qr_request_409_when_payee_has_no_bank_details(api_client_room):
    client, headers, room_id, m = api_client_room
    _two_meals_owed_to_linh(room_id, m["Linh"], m["Giang"])
    gheaders = _giang_headers(client, headers, room_id)
    r = client.post(f"/api/rooms/{room_id}/qr-requests",
                    json={"to": m["Linh"]}, headers=gheaders)
    assert r.status_code == 409
    assert "bank" in r.json()["detail"].lower() or "Linh" in r.json()["detail"]
    # and nothing was posted to the chat
    msgs = client.get(f"/api/rooms/{room_id}/messages", headers=headers).json()["messages"]
    assert not [x for x in msgs if (x.get("attachments") or {}).get("type") == "settlement"]


def test_qr_request_card_retires_once_paid(api_client_room):
    client, headers, room_id, m = api_client_room
    _give_bank_details(m["Linh"])
    _two_meals_owed_to_linh(room_id, m["Linh"], m["Giang"])
    gheaders = _giang_headers(client, headers, room_id)

    assert client.post(f"/api/rooms/{room_id}/qr-requests",
                       json={"to": m["Linh"]}, headers=gheaders).status_code == 200

    # Pay both meals via quick-pay, then the card's QR must be annotated settled.
    led = client.get(f"/api/rooms/{room_id}/ledger", headers=gheaders).json()
    for row in led["me"]["owe"]:
        pr = client.post(f"/api/rooms/{room_id}/payments/quick",
                         json={"to": m["Linh"], "meal_id": row["meal_id"]}, headers=gheaders)
        assert pr.status_code == 200, pr.text

    msgs = client.get(f"/api/rooms/{room_id}/messages", headers=headers).json()["messages"]
    card = [x for x in msgs if (x.get("attachments") or {}).get("type") == "settlement"][-1]
    assert card["attachments"]["transfers"][0]["settled"] is True
