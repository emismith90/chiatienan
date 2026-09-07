"""The eval content routes and the kernel's grader registry (plan Task 4.2)."""
from app.kernel import kernel_for

ADMIN = {"X-Admin-Password": "test-admin-pw"}


def test_kernel_registers_the_packs_graders(db):
    k = kernel_for(db)
    assert k.graders.ids() == ["lunch_ledger.eval.ledger_state", "lunch_ledger.eval.prose", "lunch_ledger.eval.tool_selection",
                               "poker_ledger.eval.game_state", "poker_ledger.eval.prose", "poker_ledger.eval.tool_selection"]
    name, g = k.graders.build({"plugin": "lunch_ledger.eval.prose", "name": "prose_quality"})
    assert name == "prose_quality" and g.blocking is False and "Vietnamese" in g.rubric


def test_eval_routes_round_trip(api_client_room):
    client, _headers, room_id, _m = api_client_room
    from app.db import get_db
    bid = kernel_for(get_db()).seed_report["business_id"]
    assert client.get(f"/api/admin/businesses/{bid}/eval/cases").status_code == 401
    case = {"id": "G1", "source": "meals", "day": "2026-07-20", "actor": "m1", "message": "@bot x", "expect": {"tools": ["propose_meal"]}}
    r = client.put(f"/api/admin/businesses/{bid}/eval/cases/G1", json={"case": case, "source": "imported"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["slug"] == "G1"
    assert [c["slug"] for c in client.get(f"/api/admin/businesses/{bid}/eval/cases", headers=ADMIN).json()] == ["G1"]
    assert client.get(f"/api/admin/businesses/{bid}/eval/cases/G1", headers=ADMIN).json()["case"] == case
    r = client.put(f"/api/admin/businesses/{bid}/eval/suites/lunch", headers=ADMIN,
                   json={"case_slugs": ["G1"], "graders": [{"plugin": "lunch_ledger.eval.tool_selection"}], "judge": {"rubric": "prose"}})
    assert r.status_code == 200 and r.json()["repeat"] == 1
    assert client.put(f"/api/admin/businesses/{bid}/eval/suites/bad", headers=ADMIN,
                      json={"graders": [{"config": {}}]}).status_code == 422
    assert client.put(f"/api/admin/businesses/{bid}/eval/rubrics/prose", headers=ADMIN, json={"body": "Be terse."}).status_code == 200
    assert client.get(f"/api/admin/businesses/{bid}/eval/rubrics", headers=ADMIN).json()[0]["body"] == "Be terse."
    assert client.get("/api/admin/eval/graders", headers=ADMIN).json()[0].startswith("lunch_ledger.eval.")
    assert client.get("/api/admin/eval/runs", headers=ADMIN).json() == []
    assert client.get("/api/admin/eval/runs/1", headers=ADMIN).status_code == 404
    assert client.delete(f"/api/admin/businesses/{bid}/eval/suites/lunch", headers=ADMIN).status_code == 204
    assert client.delete(f"/api/admin/businesses/{bid}/eval/cases/G1", headers=ADMIN).status_code == 204
    assert client.get(f"/api/admin/businesses/{bid}/eval/cases/G1", headers=ADMIN).status_code == 404
