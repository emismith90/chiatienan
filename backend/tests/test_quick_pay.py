from datetime import date


def _giang_headers(client, headers, room_id):
    """Sign in as Giang (the debtor) so ctx.member_id == Giang.

    The shared ``api_client_room`` fixture authenticates as the room creator
    (Linh). Quick-pay must run as the person who *owes*, so we re-identify as
    Giang here and use those headers for the POST.
    """
    inv = client.get(f"/api/rooms/{room_id}/invite", headers=headers).json()["invite_token"]
    tok = client.post(f"/api/rooms/{inv}/identify",
                      json={"nickname": "giang", "pin": "1234"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_quick_pay_records_meal_outstanding(api_client_room):
    client, headers, room_id, m = api_client_room     # m keyed by display name
    headers = _giang_headers(client, headers, room_id)
    from app.db import get_db
    from app import ledger
    with get_db().session() as s:
        meal_id = ledger.record_meal(s, room_id=room_id, payer_member_id=m["Linh"],
                                     participants=[m["Linh"], m["Giang"]], total_amount=122000,
                                     dish="bun bo", occurred_on=date(2026, 7, 21))["meal_id"]
    # caller (session member) is Giang -> owes Linh 61k for this meal
    r = client.post(f"/api/rooms/{room_id}/payments/quick",
                    json={"to": m["Linh"], "meal_id": meal_id}, headers=headers)
    assert r.status_code == 200 and r.json()["amount"] == 61000
    # ledger now shows the paid meal is gone from the caller's owe list
    led = client.get(f"/api/rooms/{room_id}/ledger", headers=headers).json()
    assert all(row["meal_id"] != meal_id for row in led["me"]["owe"])
    assert led["me"]["owe"] == []  # the fixture's single debt is fully cleared
