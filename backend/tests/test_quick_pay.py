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


def test_quick_pay_leaves_the_same_audit_trail_as_a_confirmed_draft(api_client_room):
    """It posted a bare line of text while commit_payment_draft attached the
    amounts — so the one path a user taps most left the least behind."""
    client, headers, room_id, members = api_client_room
    linh, giang = members["Linh"], members["Giang"]

    from app import ledger
    from app.db import get_db
    with get_db().session() as s:
        meal = ledger.record_meal(s, room_id=room_id, payer_member_id=linh,
                                  participants=[linh, giang], total_amount=100_000,
                                  adjustments={}, guests=[], dish="pho", logged_by=str(linh))
        meal_id, share = meal["meal_id"], meal["shares"][giang]

    # Giang taps ⑦ on that meal.
    gheaders = _giang_headers(client, headers, room_id)
    r = client.post(f"/api/rooms/{room_id}/payments/quick",
                    json={"to": linh, "meal_id": meal_id}, headers=gheaders)
    assert r.status_code == 200, r.text

    msgs = client.get(f"/api/rooms/{room_id}/messages", headers=headers).json()["messages"]
    card = [m for m in msgs if (m.get("attachments") or {}).get("type") == "payment"][-1]
    att = card["attachments"]
    assert att["transfers"][0]["amount"] == share
    assert att["transfers"][0]["from"]["id"] == giang
    assert att["meal_id"] == meal_id
    assert "balances" not in att, "no card carries a net-balance snapshot any more"
    assert f"{share:,}đ" in card["body"]


def test_creditor_can_mark_an_owed_to_you_row_paid(api_client_room):
    """The "owed to you" ⑦ button: Linh records that Giang handed her the cash.

    Same endpoint, `from` instead of `to`. The debt clears for both of them —
    it is one edge, not two — and the audit card names Giang as the payer even
    though Linh is the one who tapped.
    """
    client, headers, room_id, m = api_client_room     # headers == Linh, the creditor
    from app.db import get_db
    from app import ledger
    with get_db().session() as s:
        meal_id = ledger.record_meal(s, room_id=room_id, payer_member_id=m["Linh"],
                                     participants=[m["Linh"], m["Giang"]], total_amount=122000,
                                     dish="bun bo", occurred_on=date(2026, 7, 21))["meal_id"]

    r = client.post(f"/api/rooms/{room_id}/payments/quick",
                    json={"from": m["Giang"], "meal_id": meal_id}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["amount"] == 61000

    # Gone from Linh's "owed to you"...
    led = client.get(f"/api/rooms/{room_id}/ledger", headers=headers).json()
    assert all(row["meal_id"] != meal_id for row in led["me"]["owed"])
    # ...and from Giang's "you owe" too: one edge, settled once.
    gheaders = _giang_headers(client, headers, room_id)
    gled = client.get(f"/api/rooms/{room_id}/ledger", headers=gheaders).json()
    assert all(row["meal_id"] != meal_id for row in gled["me"]["owe"])

    msgs = client.get(f"/api/rooms/{room_id}/messages", headers=headers).json()["messages"]
    att = [x for x in msgs if (x.get("attachments") or {}).get("type") == "payment"][-1]["attachments"]
    t = att["transfers"][0]
    assert (t["from"]["id"], t["to"]["id"], t["amount"]) == (m["Giang"], m["Linh"], 61000)


def test_marking_an_owed_row_paid_twice_is_refused(api_client_room):
    """Second tap has nothing left outstanding — 409, not a double payment."""
    client, headers, room_id, m = api_client_room
    from app.db import get_db
    from app import ledger
    with get_db().session() as s:
        meal_id = ledger.record_meal(s, room_id=room_id, payer_member_id=m["Linh"],
                                     participants=[m["Linh"], m["Giang"]], total_amount=122000,
                                     dish="bun bo", occurred_on=date(2026, 7, 21))["meal_id"]
    body = {"from": m["Giang"], "meal_id": meal_id}
    assert client.post(f"/api/rooms/{room_id}/payments/quick",
                       json=body, headers=headers).status_code == 200
    assert client.post(f"/api/rooms/{room_id}/payments/quick",
                       json=body, headers=headers).status_code == 409


def test_quick_pay_refuses_a_payment_the_caller_is_not_part_of(api_client_room):
    """`from` names the debtor and `to` the creditor — naming both would let a
    bystander record other people's money. The caller must be one of the two."""
    client, headers, room_id, m = api_client_room
    inv = client.get(f"/api/rooms/{room_id}/invite", headers=headers).json()["invite_token"]
    nhim = client.post(f"/api/rooms/{inv}/accounts", json={
        "display_name": "Nhim", "nickname": "nhim", "pin": "1234"}).json()["member_id"]

    from app.db import get_db
    from app import ledger
    with get_db().session() as s:
        meal_id = ledger.record_meal(s, room_id=room_id, payer_member_id=m["Linh"],
                                     participants=[m["Linh"], nhim], total_amount=122000,
                                     dish="bun bo", occurred_on=date(2026, 7, 21))["meal_id"]

    # Giang is neither end of Nhim → Linh, so this is not hers to record.
    gheaders = _giang_headers(client, headers, room_id)
    r = client.post(f"/api/rooms/{room_id}/payments/quick",
                    json={"from": nhim, "to": m["Linh"], "meal_id": meal_id}, headers=gheaders)
    assert r.status_code == 403
    # And the debt is untouched: Nhim still owes Linh for that meal.
    led = client.get(f"/api/rooms/{room_id}/ledger", headers=headers).json()
    assert any(row["meal_id"] == meal_id for row in led["me"]["owed"])


def test_quick_pay_refuses_a_self_payment(api_client_room):
    client, headers, room_id, m = api_client_room
    r = client.post(f"/api/rooms/{room_id}/payments/quick",
                    json={"from": m["Linh"], "to": m["Linh"], "meal_id": 1}, headers=headers)
    assert r.status_code == 400
