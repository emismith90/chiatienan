"""POST /api/rooms/{room_id}/qr-requests — the ledger QR button.

Deterministic read: the server aggregates the caller's outstanding debt to one
creditor (same `debt_breakdown` nets as settlement), builds the VietQR and hands
it back to the caller, who shows it in a dialog — no LLM turn, money never
through the model (design D3). Nothing is posted to the chat and nothing is
written, so a tap leaves no trace in the room.
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
    d = r.json()
    assert d["amount"] == 80_000
    assert d["from"]["id"] == m["Giang"] and d["to"]["id"] == m["Linh"]
    assert d["to"]["name"] == "Linh" and d["to"]["account_number"] == "0123456789"
    assert "amount=80000" in d["qr_url"]
    assert "VCB-0123456789" in d["qr_url"]
    assert d["note"]
    # The breakdown behind the total, oldest meal first.
    assert [x["dish"] for x in d["meals"]] == ["pho", "bun"]
    assert [x["amount"] for x in d["meals"]] == [50_000, 30_000]


def test_qr_request_posts_nothing_to_the_chat(api_client_room):
    """The QR opens in a dialog for the caller alone — the room hears nothing."""
    client, headers, room_id, m = api_client_room
    _give_bank_details(m["Linh"])
    _two_meals_owed_to_linh(room_id, m["Linh"], m["Giang"])
    gheaders = _giang_headers(client, headers, room_id)

    before = client.get(f"/api/rooms/{room_id}/messages", headers=headers).json()["messages"]
    assert client.post(f"/api/rooms/{room_id}/qr-requests",
                       json={"to": m["Linh"]}, headers=gheaders).status_code == 200
    after = client.get(f"/api/rooms/{room_id}/messages", headers=headers).json()["messages"]
    assert len(after) == len(before)


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


def test_qr_request_drops_the_meals_already_paid(api_client_room):
    """Pay one meal and the QR covers only what is still outstanding."""
    client, headers, room_id, m = api_client_room
    _give_bank_details(m["Linh"])
    _two_meals_owed_to_linh(room_id, m["Linh"], m["Giang"])
    gheaders = _giang_headers(client, headers, room_id)

    led = client.get(f"/api/rooms/{room_id}/ledger", headers=gheaders).json()
    first = led["me"]["owe"][0]
    assert client.post(f"/api/rooms/{room_id}/payments/quick",
                       json={"to": m["Linh"], "meal_id": first["meal_id"]},
                       headers=gheaders).status_code == 200

    d = client.post(f"/api/rooms/{room_id}/qr-requests",
                    json={"to": m["Linh"]}, headers=gheaders).json()
    assert d["amount"] == 80_000 - first["amount"]
    assert [x["meal_id"] for x in d["meals"]] != [first["meal_id"]]

    # Pay the rest and there is nothing left to build a QR from.
    led = client.get(f"/api/rooms/{room_id}/ledger", headers=gheaders).json()
    for row in led["me"]["owe"]:
        client.post(f"/api/rooms/{room_id}/payments/quick",
                    json={"to": m["Linh"], "meal_id": row["meal_id"]}, headers=gheaders)
    assert client.post(f"/api/rooms/{room_id}/qr-requests",
                       json={"to": m["Linh"]}, headers=gheaders).status_code == 409
