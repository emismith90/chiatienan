"""The admin API end to end (plan Task 2.5): edit a source, draft, publish, bind a
room, and see the bound room run the edit while an unbound room does not."""
import asyncio

import pytest

import app.agent as agent_mod
from app import chat
from app.agent import TurnResult
from app.kernel import kernel_for
from tests.test_ledger import _seed_room

ADMIN = {"X-Admin-Password": "test-admin-pw"}


@pytest.fixture
def admin(api_client_room, db):
    client, _headers, room_id, _m = api_client_room
    k = kernel_for(db)
    return client, room_id, k


def test_registry_and_schema(admin):
    client, _, _ = admin
    rows = client.get("/api/admin/registry", headers=ADMIN).json()
    ids = {r["id"] for r in rows}
    assert {"kernos.context.rollover", "kernos.prompt.template", "kernos.render.packs"} <= ids
    assert client.get("/api/admin/plugins/kernos.context.history/1/schema", headers=ADMIN).json()["required"] == ["max_messages"]
    assert client.get("/api/admin/plugins/nope/1/schema", headers=ADMIN).status_code == 404
    assert client.get("/api/admin/registry").status_code == 401                      # guarded


def test_sources_etag_flow(admin):
    client, _, k = admin
    bid = k.seed_report["business_id"]
    r = client.get(f"/api/admin/businesses/{bid}/sources/rule/money-safety", headers=ADMIN)
    assert r.status_code == 200 and r.headers["ETag"] == r.json()["etag"]
    etag = r.headers["ETag"]
    stale = client.put(f"/api/admin/businesses/{bid}/sources/rule/money-safety", headers={**ADMIN, "If-Match": "nope"},
                       json={"body": "x"})
    assert stale.status_code == 412
    ok = client.put(f"/api/admin/businesses/{bid}/sources/rule/house", headers=ADMIN,
                    json={"title": "House", "body": "No bash.", "frontmatter": {"tags": []}})
    assert ok.status_code == 200 and ok.headers["ETag"]
    assert client.get(f"/api/admin/businesses/{bid}/sources?kind=rule", headers=ADMIN).json()[0]["slug"] in ("house", "money-safety")
    assert client.put(f"/api/admin/businesses/{bid}/sources/bogus/x", headers=ADMIN, json={"body": ""}).status_code == 422
    assert client.delete(f"/api/admin/businesses/{bid}/sources/rule/house", headers={**ADMIN, "If-Match": "stale"}).status_code == 412
    assert client.delete(f"/api/admin/businesses/{bid}/sources/rule/house", headers={**ADMIN, "If-Match": ok.headers["ETag"]}).status_code == 204
    assert etag == client.get(f"/api/admin/businesses/{bid}/sources/rule/money-safety", headers=ADMIN).headers["ETag"]


def test_edit_draft_publish_bind_and_the_bound_room_runs_the_edit(admin, db, monkeypatch):
    client, room_id, k = admin
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    # 1. a new skill source
    assert client.put(f"/api/admin/businesses/{bid}/sources/skill/poker-night", headers=ADMIN,
                      json={"body": "Play fair.", "frontmatter": {"description": "poker"}}).status_code == 200
    # 2. a second profile + agent for the edited behaviour, from the seeded spec
    prof = client.post("/api/admin/profiles", headers=ADMIN, json={"business_id": bid, "name": "poker-ish"}).json()
    draft = client.post(f"/api/admin/profiles/{prof['id']}/versions", headers=ADMIN,
                        json={"base_spec": k.store.published_spec(pid)}).json()
    assert draft["status"] == "draft" and any(s["name"] == "poker-night" for s in draft["spec"]["skills"])
    patched = client.patch(f"/api/admin/profiles/{prof['id']}/versions/1", headers=ADMIN,
                           json={"caps": {"max_tools": 7}, "builtin_tools": []}).json()
    assert patched["spec"]["caps"]["max_tools"] == 7
    # 3. publish: gates pass (bash removed → no override needed; models unchanged vs none published… probe!)
    pub = client.post(f"/api/admin/profiles/{prof['id']}/versions/1/publish", headers=ADMIN, json={"actor": "boot"})
    assert pub.status_code == 422 and "reserved" in pub.text
    pub = client.post(f"/api/admin/profiles/{prof['id']}/versions/1/publish", headers=ADMIN, json={})
    assert pub.status_code == 200, pub.text
    agent = client.post("/api/admin/agents", headers=ADMIN, json={
        "business_id": bid, "slug": "poker", "name": "Poker", "profile_id": prof["id"]}).json()
    # 4. bind THIS room; another room stays unbound
    other_room, _ = _seed_room(db, 2, token="other")
    bad = client.put(f"/api/admin/spaces/{room_id}/binding", headers=ADMIN, json={"agent_id": agent["id"], "overrides": {"bogus": 1}})
    assert bad.status_code == 422
    bnd = client.put(f"/api/admin/spaces/{room_id}/binding", headers=ADMIN,
                     json={"agent_id": agent["id"], "overrides": {"append_sections": ["Room rule: be brief."]}})
    assert bnd.status_code == 200
    # 5. resolved shows the edit for the bound room only
    bound = client.get(f"/api/admin/spaces/{room_id}/resolved", headers=ADMIN).json()
    assert bound["resolution"]["source"] == "binding" and bound["engine_spec"]["max_tools"] == 7
    assert any(s["name"] == "poker-night" for s in bound["engine_spec"]["skills"])
    unbound = client.get(f"/api/admin/spaces/{other_room}/resolved", headers=ADMIN).json()
    assert unbound["resolution"]["source"] == "default" and unbound["engine_spec"]["max_tools"] == 40
    assert client.get(f"/api/admin/rooms/{other_room}/resolved", headers=ADMIN).json()["engine_spec"] == unbound["engine_spec"]
    # 6. and a real turn in the bound room hands the engine the edited spec
    seen = {}

    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        seen["spec"], seen["system"] = ctx.engine_spec, ctx.system_override
        return TurnResult(final_text="ok")

    monkeypatch.setattr(agent_mod, "run_turn", fake)
    asyncio.run(chat.run_bot_turn(db, room_id, 1, "Linh", "@phoenix hi"))
    assert seen["spec"].max_tools == 7 and any(s["name"] == "poker-night" for s in seen["spec"].skills)
    assert seen["system"].endswith("Room rule: be brief.")
    asyncio.run(chat.run_bot_turn(db, other_room, 1, "M1", "@phoenix hi"))
    assert seen["spec"].max_tools == 40 and "Room rule" not in seen["system"]
    # 7. unbind → back to default; audit has the trail
    assert client.delete(f"/api/admin/spaces/{room_id}/binding", headers=ADMIN).status_code == 204
    assert client.get(f"/api/admin/spaces/{room_id}/resolved", headers=ADMIN).json()["resolution"]["source"] == "default"
    actions = {a["action"] for a in client.get("/api/admin/audit", headers=ADMIN).json()}
    assert {"publish", "bind", "unbind", "put"} <= actions


def test_publish_gates_report_failures_and_rollback_works(admin):
    client, _, k = admin
    pid = k.seed_report["profile_id"]
    d = client.post(f"/api/admin/profiles/{pid}/versions", headers=ADMIN, json={}).json()
    v = d["version"]
    # gate 2: the seeded profile has bash on and handles money → needs a reason
    r = client.post(f"/api/admin/profiles/{pid}/versions/{v}/publish", headers=ADMIN, json={})
    assert r.status_code == 422 and r.json()["detail"]["gates"][0]["gate"] == "money"
    # gate 3: a new model with no probe
    client.patch(f"/api/admin/profiles/{pid}/versions/{v}", headers=ADMIN, json={"models": {"text": "new/model"}})
    r = client.post(f"/api/admin/profiles/{pid}/versions/{v}/publish", headers=ADMIN, json={"override_reason": "x"})
    assert [g["gate"] for g in r.json()["detail"]["gates"]] == ["probe"]
    # gate 1: unknown template variable
    client.patch(f"/api/admin/profiles/{pid}/versions/{v}", headers=ADMIN,
                 json={"models": {"text": k.default_spec.models.text}, "prompt": {"append": ["{{nope}}"]}})
    r = client.post(f"/api/admin/profiles/{pid}/versions/{v}/publish", headers=ADMIN, json={"override_reason": "x"})
    assert any("unknown variable" in g["message"] for g in r.json()["detail"]["gates"])
    client.patch(f"/api/admin/profiles/{pid}/versions/{v}", headers=ADMIN, json={"prompt": {"append": []}})
    r = client.post(f"/api/admin/profiles/{pid}/versions/{v}/publish", headers=ADMIN, json={"override_reason": "x"})
    assert r.status_code == 200
    assert client.get(f"/api/admin/profiles/{pid}", headers=ADMIN).json()["managed_by"] == "human"
    # state conflicts: publishing again, editing a published version
    assert client.post(f"/api/admin/profiles/{pid}/versions/{v}/publish", headers=ADMIN, json={}).status_code == 409
    assert client.patch(f"/api/admin/profiles/{pid}/versions/{v}", headers=ADMIN, json={"caps": {"max_tools": 1}}).status_code == 409
    # rollback to version 1 (superseded) and retire
    rb = client.post(f"/api/admin/profiles/{pid}/rollback", headers=ADMIN, json={"version": 1, "override_reason": "incident"})
    assert rb.status_code == 200 and rb.json()["version"] == 1
    assert client.post(f"/api/admin/profiles/{pid}/versions/{v}/retire", headers=ADMIN).status_code == 200
    versions = client.get(f"/api/admin/profiles/{pid}/versions", headers=ADMIN).json()
    assert [(x["version"], x["status"]) for x in versions] == [(1, "published"), (2, "retired")]


def test_catalogue_and_probe_without_key_is_501(admin, monkeypatch):
    client, _, _ = admin
    models = client.get("/api/admin/catalogue/models", headers=ADMIN).json()
    assert any(m["probe"].get("ok") for m in models)
    monkeypatch.delenv("OPEN_ROUTER_KEY", raising=False)
    r = client.post("/api/admin/catalogue/models/x%2Fy/probe", headers=ADMIN)
    assert r.status_code == 501
