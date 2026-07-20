"""HTTP surface tests — rooms/accounts/chat/SSE routes.

Uses the file-backed ``db`` fixture (conftest) and points ``app.db._default``
at it so ``get_db()`` returns the same database everywhere (auth, routes, chat).
Admin auth uses the conftest ``ADMIN_PASSWORD=test-admin-pw``.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.auth import AuthCtx

ADMIN = {"X-Admin-Password": "test-admin-pw"}


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr("app.db._default", db, raising=False)
    return TestClient(main.app)


def _room(client) -> str:
    r = client.post("/api/rooms", headers=ADMIN, json={"name": "Lunch"})
    assert r.status_code == 200, r.text
    return r.json()["invite_token"]


def _join(client, token, nickname, pin="1234", **bank):
    r = client.post(
        f"/api/rooms/{token}/accounts",
        json={"display_name": nickname.title(), "nickname": nickname, "pin": pin, **bank},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    return body["token"], body["room_id"]


def test_full_join_and_post_flow(client):
    token = _room(client)
    sess_a, room_id = _join(client, token, "an")
    sess_b, _ = _join(client, token, "binh")
    ha = {"Authorization": f"Bearer {sess_a}"}
    hb = {"Authorization": f"Bearer {sess_b}"}

    assert client.post(f"/api/rooms/{room_id}/messages", headers=ha, json={"body": "hi"}).status_code == 200

    for h in (ha, hb):
        msgs = client.get(f"/api/rooms/{room_id}/messages", headers=h).json()["messages"]
        assert any(m["body"] == "hi" for m in msgs)


def test_no_session_is_401(client):
    token = _room(client)
    _sess, room_id = _join(client, token, "an")
    assert client.get(f"/api/rooms/{room_id}/messages").status_code == 401


def test_cross_room_is_403(client):
    token_a = _room(client)
    sess_a, room_a = _join(client, token_a, "an")
    token_b = _room(client)
    _sess_b, room_b = _join(client, token_b, "an")  # same nick, different room
    assert room_a != room_b
    ha = {"Authorization": f"Bearer {sess_a}"}
    # session for room A may not read room B
    assert client.get(f"/api/rooms/{room_b}/messages", headers=ha).status_code == 403


def test_sse_catchup_since_returns_only_newer(client):
    # starlette's TestClient buffers the whole response, so it can't drive an
    # infinite SSE generator; call the async stream route directly and consume
    # just the catch-up chunk, then close it.
    token = _room(client)
    sess, room_id = _join(client, token, "an")
    h = {"Authorization": f"Bearer {sess}"}
    first_id = client.post(f"/api/rooms/{room_id}/messages", headers=h, json={"body": "first"}).json()["id"]
    client.post(f"/api/rooms/{room_id}/messages", headers=h, json={"body": "second"})
    me = client.get("/api/me", headers=h).json()
    ctx = AuthCtx(member_id=me["id"], room_id=room_id, display_name="An", nickname="an")

    async def consume() -> str:
        resp = await main.stream(room_id, since=first_id, ctx=ctx)
        it = resp.body_iterator
        try:
            chunk = await anext(it)  # first catch-up chunk
        finally:
            await it.aclose()
        return chunk if isinstance(chunk, str) else chunk.decode()

    text = asyncio.run(consume())
    assert "second" in text
    assert "first" not in text


def test_members_endpoint_has_no_banking(client):
    token = _room(client)
    sess, room_id = _join(client, token, "an", bank_code="VCB",
                          account_number="0123", account_holder="NGUYEN AN")
    h = {"Authorization": f"Bearer {sess}"}
    members = client.get(f"/api/rooms/{room_id}/members", headers=h).json()
    assert isinstance(members, list) and members
    m = members[0]
    assert set(m.keys()) == {"id", "display_name", "nickname"}
    assert "account_number" not in m and "bank_code" not in m
