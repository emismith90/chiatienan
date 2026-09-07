"""The room-CMS routes over HTTP (plan Phase 11.1–11.3).

`test_room_cms.py` covers the rules by calling `app.roomcms` directly. That left the
five routes in `app/main.py` with **no test at all**, and they shipped to production
with `NameError: name 'kernel_for' is not defined` — `main.py` imports `kernel_for`
locally inside every other function that needs it, and these five called it as a global.
A unit test on the logic cannot see that; only a request can.

So these go through `TestClient` end to end: real bearer auth, real routing, real JSON.
"""
import pytest


def _agent(client, headers, room_id):
    return client.get(f"/api/rooms/{room_id}/agent", headers=headers)


# ------------------------------------------------------- the routes resolve at all

def test_every_room_cms_route_is_reachable(api_client_room):
    """The regression test for the production 500: each route must at least run its
    handler. A NameError in the handler is a 500 here, exactly as it was in prod."""
    client, headers, room_id, _members = api_client_room
    for method, path in (
        ("GET", f"/api/rooms/{room_id}/agent"),
        ("GET", f"/api/rooms/{room_id}/agent/versions"),
        ("GET", f"/api/rooms/{room_id}/agent/versions/1"),
    ):
        r = client.request(method, path, headers=headers)
        assert r.status_code != 500, f"{method} {path} -> 500: {r.text[:400]}"
        assert r.status_code == 200, f"{method} {path} -> {r.status_code}: {r.text[:200]}"

    # the two writers reach their handler too; an unbound room is refused with 403,
    # which is the rule under test in test_room_cms.py — the point here is that the
    # handler ran and answered rather than raising
    put = client.put(f"/api/rooms/{room_id}/agent/content", headers=headers,
                     json={"base_version_id": 1, "prompt_append": ["be brief"]})
    assert put.status_code == 403 and "bind" in put.text

    post = client.post(f"/api/rooms/{room_id}/agent/versions/1/republish", headers=headers, json={})
    assert post.status_code == 403 and "bind" in post.text


def test_the_read_route_returns_the_shape_the_panel_expects(api_client_room):
    client, headers, room_id, _members = api_client_room
    body = _agent(client, headers, room_id).json()
    assert set(body) == {"agent", "profile", "version", "editable", "readonly",
                         "source_etags", "can_edit", "shared", "scope"}
    assert body["agent"]["slug"] == "phoenix"
    assert body["can_edit"] is False and body["shared"] is True     # no binding yet
    assert body["profile"]["managed_by"] == "boot"
    assert set(body["editable"]) == {"prompt_body", "prompt_append", "skills", "rules"}
    assert body["editable"]["prompt_body"]
    money = next(r for r in body["editable"]["rules"] if "money" in r["tags"])
    assert money["editable"] is False
    assert set(body["readonly"]) == {"models", "caps", "builtin_tools", "tool_packs",
                                     "pipeline_stages"}


def test_the_versions_route_returns_the_revision_log(api_client_room):
    client, headers, room_id, _members = api_client_room
    rows = client.get(f"/api/rooms/{room_id}/agent/versions", headers=headers).json()
    assert rows and rows[0]["version"] == 1 and rows[0]["status"] == "published"
    assert rows[0]["actor"] == "boot"
    assert set(rows[0]) >= {"id", "version", "status", "actor", "note", "created_at",
                            "published_at", "paths"}

    one = client.get(f"/api/rooms/{room_id}/agent/versions/1", headers=headers).json()
    assert one["version"] == 1 and "editable" in one and "diff" in one


def test_a_version_that_does_not_exist_is_a_404_not_a_500(api_client_room):
    client, headers, room_id, _members = api_client_room
    r = client.get(f"/api/rooms/{room_id}/agent/versions/999", headers=headers)
    assert r.status_code == 404, r.text


# ------------------------------------------------------------------------ auth

def test_no_token_is_401_on_every_route(api_client_room):
    client, _headers, room_id, _members = api_client_room
    for method, path in (("GET", f"/api/rooms/{room_id}/agent"),
                         ("GET", f"/api/rooms/{room_id}/agent/versions"),
                         ("GET", f"/api/rooms/{room_id}/agent/versions/1"),
                         ("PUT", f"/api/rooms/{room_id}/agent/content"),
                         ("POST", f"/api/rooms/{room_id}/agent/versions/1/republish")):
        r = client.request(method, path, json={})
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


def test_a_members_token_does_not_reach_another_room(api_client_room):
    client, headers, room_id, _members = api_client_room
    other = room_id + 1000
    r = client.get(f"/api/rooms/{other}/agent", headers=headers)
    assert r.status_code == 403 and "wrong room" in r.text


# --------------------------------------------------- the write path, once bound

def _bind(db, room_id):
    from app.kernel import kernel_for
    k = kernel_for(db)
    agent = k.store.default_agent(k.seed_report["business_id"])
    k.store.bind_space(str(room_id), agent["id"], actor="admin")
    k.invalidate()
    return k


def test_editing_and_republishing_over_http_once_the_room_is_bound(api_client_room, db):
    client, headers, room_id, _members = api_client_room
    k = _bind(db, room_id)

    seen = _agent(client, headers, room_id).json()
    assert seen["can_edit"] is True

    put = client.put(f"/api/rooms/{room_id}/agent/content", headers=headers,
                     json={"base_version_id": seen["version"]["id"],
                           "prompt_append": ["Luôn trả lời thật ngắn."],
                           "note": "shorter replies",
                           "source_etags": seen["source_etags"]})
    assert put.status_code == 200, put.text
    assert put.json()["paths"] == ["prompt.append"]
    assert put.json()["actor"].startswith("member:")

    # the published spec really moved
    assert k.store.published_spec(k.seed_report["profile_id"])["prompt"]["append"] == \
        ["Luôn trả lời thật ngắn."]

    # a stale base_version_id is a 409, not a silent overwrite
    stale = client.put(f"/api/rooms/{room_id}/agent/content", headers=headers,
                       json={"base_version_id": seen["version"]["id"], "prompt_append": ["again"]})
    assert stale.status_code == 409 and "reload" in stale.text

    # and the earlier version comes back through the route
    back = client.post(f"/api/rooms/{room_id}/agent/versions/1/republish",
                       headers=headers, json={"note": "put it back"})
    assert back.status_code == 200, back.text
    assert back.json()["from_version"] == 1
    assert k.store.published_spec(k.seed_report["profile_id"])["prompt"]["append"] == []


def test_a_refused_edit_answers_with_the_reason_over_http(api_client_room, db):
    client, headers, room_id, _members = api_client_room
    k = _bind(db, room_id)
    seen = _agent(client, headers, room_id).json()
    published = k.store.published_spec(k.seed_report["profile_id"])

    # the money rule, by slug — the guard the review gate asked for
    rules = [{"slug": r["slug"], "content": "relaxed" if "money" in r["tags"] else r["content"]}
             for r in published["rules"]]
    r = client.put(f"/api/rooms/{room_id}/agent/content", headers=headers,
                   json={"base_version_id": seen["version"]["id"], "rules": rules})
    assert r.status_code == 400 and "money-safety" in r.text

    # a gate failure comes back as 422 with the gate named
    bad = client.put(f"/api/rooms/{room_id}/agent/content", headers=headers,
                     json={"base_version_id": seen["version"]["id"], "prompt_body": "Hi {{nope}}"})
    assert bad.status_code == 422 and "gates" in bad.text
