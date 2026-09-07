"""The two routes the admin UI needs that the admin API did not have (plan Phase 12.0).

`GET /api/admin/bindings` — an operator's first question is *which* spaces are bound,
because the binding, not room membership, is what lets a space's members edit its agent.
`GET …/versions/{v}/diff` — the revision log needs the same diff the room panel and the
proposal card render, computed once, on the server.
"""
from app.kernel import kernel_for

ADMIN = {"X-Admin-Password": "test-admin-pw"}


def test_bindings_lists_every_bound_space(api_client_room, db):
    client, _headers, room_id, _m = api_client_room
    k = kernel_for(db)
    bid = k.seed_report["business_id"]
    agent_id = client.get("/api/admin/agents", headers=ADMIN).json()[0]["id"]

    assert client.get("/api/admin/bindings", headers=ADMIN).json() == []
    assert client.get("/api/admin/bindings").status_code == 401              # guarded

    client.put(f"/api/admin/spaces/{room_id}/binding", headers=ADMIN, json={"agent_id": agent_id})
    client.put("/api/admin/spaces/99/binding", headers=ADMIN, json={"agent_id": agent_id})
    rows = client.get("/api/admin/bindings", headers=ADMIN).json()
    assert [r["space_id"] for r in rows] == sorted([str(room_id), "99"])
    assert all(r["agent_id"] == agent_id for r in rows)
    assert bid  # the seed is the business these agents belong to

    client.delete("/api/admin/spaces/99/binding", headers=ADMIN)
    assert [r["space_id"] for r in client.get("/api/admin/bindings", headers=ADMIN).json()] == [str(room_id)]


def test_first_version_diffs_against_nothing(api_client_room, db):
    """An origin changed nothing — `changed_paths(None, spec)` would list every field."""
    client, _headers, _room_id, _m = api_client_room
    pid = kernel_for(db).seed_report["profile_id"]
    body = client.get(f"/api/admin/profiles/{pid}/versions/1/diff", headers=ADMIN).json()
    assert body == {"version": 1, "against": None, "paths": [], "diff": ""}


def test_diff_reports_the_changed_paths_and_a_unified_diff(api_client_room, db):
    client, _headers, _room_id, _m = api_client_room
    pid = kernel_for(db).seed_report["profile_id"]
    draft = client.post(f"/api/admin/profiles/{pid}/versions", headers=ADMIN, json={}).json()
    client.patch(f"/api/admin/profiles/{pid}/versions/{draft['version']}", headers=ADMIN,
                 json={"prompt": {"body": "Bạn là Phoenix, khác đi một chút."}})

    body = client.get(f"/api/admin/profiles/{pid}/versions/{draft['version']}/diff", headers=ADMIN).json()
    assert body["against"] == draft["version"] - 1
    assert body["paths"] == ["prompt.body"]
    assert "khác đi một chút" in body["diff"]
    assert f"(v{draft['version']})" in body["diff"] and f"(v{draft['version'] - 1})" in body["diff"]

    # an explicit `against`, and a version that does not exist
    same = client.get(f"/api/admin/profiles/{pid}/versions/{draft['version']}/diff",
                      headers=ADMIN, params={"against": draft["version"]}).json()
    assert same["paths"] == [] and same["diff"] == ""
    assert client.get(f"/api/admin/profiles/{pid}/versions/999/diff", headers=ADMIN).status_code == 404
    assert client.get(f"/api/admin/profiles/{pid}/versions/1/diff", headers=ADMIN,
                      params={"against": 999}).status_code == 404
