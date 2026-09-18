"""``POST /payments/{id}/void`` (and the ``ledger.void_payment`` it calls):
undo for a mistaken payment, which used to have none — see the ledger.py
docstring for the production incident this closes."""
from datetime import date

from app import ledger


def _giang_headers(client, headers, room_id):
    """Sign in as Giang so ``ctx.member_id`` is hers — same helper as
    ``test_quick_pay.py``."""
    inv = client.get(f"/api/rooms/{room_id}/invite", headers=headers).json()["invite_token"]
    tok = client.post(f"/api/rooms/{inv}/identify",
                      json={"nickname": "giang", "pin": "1234"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_a_party_can_void_a_payment_and_the_ledger_recomputes(api_client_room):
    """Giang quick-pays her share, then undoes it herself: the debt reopens and
    the payment drops out of the timeline, exactly as if it had never landed."""
    client, headers, room_id, m = api_client_room
    gheaders = _giang_headers(client, headers, room_id)
    from app.db import get_db
    with get_db().session() as s:
        meal_id = ledger.record_meal(s, room_id=room_id, payer_member_id=m["Linh"],
                                     participants=[m["Linh"], m["Giang"]], total_amount=122000,
                                     dish="bun bo", occurred_on=date(2026, 7, 21))["meal_id"]
    pay = client.post(f"/api/rooms/{room_id}/payments/quick",
                      json={"to": m["Linh"], "meal_id": meal_id}, headers=gheaders)
    assert pay.status_code == 200
    payment_id = pay.json()["payment_id"]

    # Gone from Giang's "you owe" while the payment stands.
    led = client.get(f"/api/rooms/{room_id}/ledger", headers=gheaders).json()
    assert all(row["meal_id"] != meal_id for row in led["me"]["owe"])

    r = client.post(f"/api/rooms/{room_id}/payments/{payment_id}/void", headers=gheaders)
    assert r.status_code == 200, r.text

    led = client.get(f"/api/rooms/{room_id}/ledger", headers=gheaders).json()
    # The debt is back, at its full amount.
    row = [row for row in led["me"]["owe"] if row["meal_id"] == meal_id]
    assert row and row[0]["amount"] == 61000
    # The undone payment no longer appears as a transaction.
    assert all(e.get("payment_id") != payment_id for e in led["timeline"])
    # The room was told, in the same voice/formatting as other money bots.
    msgs = client.get(f"/api/rooms/{room_id}/messages", headers=headers).json()["messages"]
    assert any("61,000" in (msg.get("body") or "") for msg in msgs)


def test_voiding_an_untargeted_payment_reopens_the_later_meal_it_had_silently_paid(api_client_room):
    """Regression for the production incident (see ``ledger.void_payment``):
    Giang hands Linh 61,000đ with no meal attached (say, cash across the table,
    logged as a bare payment). It sits in the pair pool and — because FIFO
    attribution does not care what a payment says it is for — silently marks a
    LATER, unrelated meal's debt as paid. Voiding it must put that meal's debt
    back at its full amount, not partially or off by the wrong edge.
    """
    client, headers, room_id, m = api_client_room
    linh, giang = m["Linh"], m["Giang"]
    from app.db import get_db
    with get_db().session() as s:
        pay = ledger.record_payment(s, room_id=room_id, from_member_id=giang, to_member_id=linh,
                                    amount=61000, occurred_on=date(2026, 7, 20))
        # A later meal Linh pays for, that Giang also eats at.
        meal_id = ledger.record_meal(s, room_id=room_id, payer_member_id=linh,
                                     participants=[linh, giang], total_amount=122000,
                                     dish="bun bo", occurred_on=date(2026, 7, 27))["meal_id"]
        statement = ledger.statement_for(s, room_id, giang, None, date(2999, 1, 1))
        # Before the fix existed this was the bug: the untargeted payment paid
        # off the later meal instead of whatever it was actually meant to cover.
        assert all(row["meal_id"] != meal_id for row in statement["owe"])

    r = client.post(f"/api/rooms/{room_id}/payments/{pay['payment_id']}/void", headers=headers)
    assert r.status_code == 200, r.text

    with get_db().session() as s:
        statement = ledger.statement_for(s, room_id, giang, None, date(2999, 1, 1))
    row = [row for row in statement["owe"] if row["meal_id"] == meal_id]
    assert row and row[0]["amount"] == 61000 and row[0]["status"] == "unpaid"


def test_voiding_a_payment_you_are_not_part_of_is_refused(api_client_room):
    client, headers, room_id, m = api_client_room
    linh, giang = m["Linh"], m["Giang"]
    inv = client.get(f"/api/rooms/{room_id}/invite", headers=headers).json()["invite_token"]
    nhim_tok = client.post(f"/api/rooms/{inv}/accounts", json={
        "display_name": "Nhim", "nickname": "nhim", "pin": "1234"}).json()["token"]
    nhim_headers = {"Authorization": f"Bearer {nhim_tok}"}

    from app.db import get_db
    with get_db().session() as s:
        pay = ledger.record_payment(s, room_id=room_id, from_member_id=giang, to_member_id=linh,
                                    amount=40000, occurred_on=date(2026, 7, 20))

    r = client.post(f"/api/rooms/{room_id}/payments/{pay['payment_id']}/void", headers=nhim_headers)
    assert r.status_code == 403

    # Untouched: still live.
    with get_db().session() as s:
        from app.models import Payment
        assert s.get(Payment, pay["payment_id"]).voided is False


def test_voiding_an_already_voided_payment_is_a_noop(db):
    from tests.test_ledger import _seed_room
    room_id, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        pay = ledger.record_payment(s, room_id=room_id, from_member_id=a, to_member_id=b,
                                    amount=10000, occurred_on=date(2026, 7, 20))
        first = ledger.void_payment(s, pay["payment_id"], room_id=room_id, by=str(a))
        assert first == {"payment_id": pay["payment_id"], "voided": True}
        second = ledger.void_payment(s, pay["payment_id"], room_id=room_id, by=str(a))
        assert second == {"payment_id": pay["payment_id"], "already_voided": True}
