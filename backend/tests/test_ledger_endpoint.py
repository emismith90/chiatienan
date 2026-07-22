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
    assert {b["name"] for b in data["balances"]} == {"Linh", "Giang"}
