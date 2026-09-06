"""``GET /api/admin/rooms/{id}/resolved`` says what a room runs (plan Task 1.9)."""
from dataclasses import asdict

from app import agent

ADMIN = {"X-Admin-Password": "test-admin-pw"}


def test_requires_the_admin_password(api_client_room):
    client, _headers, room_id, _m = api_client_room
    assert client.get(f"/api/admin/rooms/{room_id}/resolved").status_code == 401
    assert client.get(f"/api/admin/rooms/{room_id}/resolved",
                      headers={"X-Admin-Password": "nope"}).status_code == 401


def test_resolved_engine_spec_is_todays_command_and_the_pipeline_is_listed(api_client_room):
    client, _headers, room_id, _m = api_client_room
    r = client.get(f"/api/admin/rooms/{room_id}/resolved", headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["room_id"] == room_id
    assert body["engine_spec"] == asdict(agent.default_engine_spec())
    stages = [(e["stage"], e["plugin"]) for e in body["pipeline"]]
    assert stages[:2] == [("context", "kernos.context.rollover"), ("context", "kernos.context.memory")]
    assert ("run", "app.run.legacy") in stages and ("render", "kernos.render.packs") in stages
    assert all(e["version"] for e in body["pipeline"])
    assert body["spec"]["persona"]["name"] == "Phoenix" and body["spec"]["meta"]["handles_money"] is True
