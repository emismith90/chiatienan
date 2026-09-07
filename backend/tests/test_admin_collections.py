"""The collections routes (plan Task 5.1)."""
from app.kernel import kernel_for

ADMIN = {"X-Admin-Password": "test-admin-pw"}
ROTA = {"type": "object", "required": ["week", "who"],
        "properties": {"week": {"type": "string"}, "who": {"type": "string"}}}


def test_collection_and_document_routes(api_client_room):
    client, _headers, room_id, _m = api_client_room
    from app.db import get_db
    k = kernel_for(get_db())
    bid = k.seed_report["business_id"]
    assert client.get(f"/api/admin/businesses/{bid}/collections").status_code == 401
    body = {"name": "Rota", "schema": ROTA, "key": "week", "indexed": ["who"]}
    r = client.put(f"/api/admin/businesses/{bid}/collections/rota", json=body, headers=ADMIN)
    assert r.status_code == 200 and r.json()["indexed"] == ["who"]
    bad = client.put(f"/api/admin/businesses/{bid}/collections/rota", headers=ADMIN,
                     json={**body, "schema": {**ROTA, "additionalProperties": False}})
    assert bad.status_code == 422 and "additionalProperties" in bad.text
    assert client.put(f"/api/admin/businesses/{bid}/collections/propose_meal", json=body, headers=ADMIN).status_code == 200   # no clash: propose_meal_find
    assert client.put(f"/api/admin/businesses/{bid}/collections/rota", json=body,
                      headers={**ADMIN, "X-Actor": "agent:phoenix"}).status_code == 422
    assert [c["slug"] for c in client.get(f"/api/admin/businesses/{bid}/collections", headers=ADMIN).json()] == ["propose_meal", "rota"]

    base = f"/api/admin/spaces/{room_id}/collections/rota/documents"
    r = client.put(f"{base}/2026-W36", json={"data": {"week": "2026-W36", "who": "An"}}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["created_by"] == "admin"
    assert client.put(f"{base}/2026-W37", json={"data": {"week": "2026-W36", "who": "An"}}, headers=ADMIN).status_code == 422
    assert client.put(f"{base}/2026-W38", json={"data": {"week": "2026-W38"}}, headers=ADMIN).status_code == 422
    listing = client.get(base, headers=ADMIN).json()
    assert [d["doc_id"] for d in listing["documents"]] == ["2026-W36"] and listing["more"] is False
    assert client.get(f"{base}/2026-W36", headers=ADMIN).json()["data"]["who"] == "An"
    assert client.get(f"{base}/nope", headers=ADMIN).status_code == 404
    assert client.get(f"/api/admin/spaces/{room_id + 1}/collections/rota/documents", headers=ADMIN).json()["documents"] == []
    assert client.delete(f"/api/admin/businesses/{bid}/collections/rota", headers=ADMIN).status_code == 409
    r = client.delete(f"{base}/2026-W36", headers=ADMIN)
    assert r.status_code == 200 and r.json()["data"]["who"] == "An"
    assert client.delete(f"/api/admin/businesses/{bid}/collections/rota", headers=ADMIN).status_code == 204
    assert client.get(f"/api/admin/businesses/{bid}/collections/rota", headers=ADMIN).status_code == 404
