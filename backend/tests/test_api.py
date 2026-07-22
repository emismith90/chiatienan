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


def test_members_endpoint_includes_status_and_bank_details(client):
    token = _room(client)
    sess, room_id = _join(client, token, "an", bank_code="VCB",
                          account_number="0123", account_holder="NGUYEN AN")
    h = {"Authorization": f"Bearer {sess}"}
    members = client.get(f"/api/rooms/{room_id}/members", headers=h).json()
    assert isinstance(members, list) and members
    m = members[0]
    assert m["claimed"] is True and m["has_bank"] is True
    # Bank details are shared within the room so members can transfer to each other.
    assert (m["bank_code"], m["account_number"], m["account_holder"]) == ("VCB", "0123", "NGUYEN AN")
    assert "pin" not in m  # never expose the PIN


def test_members_endpoint_omits_bank_when_unset(client):
    token = _room(client)
    sess, room_id = _join(client, token, "an")  # no bank fields
    h = {"Authorization": f"Bearer {sess}"}
    m = client.get(f"/api/rooms/{room_id}/members", headers=h).json()[0]
    assert m["has_bank"] is False
    assert m["bank_code"] is None and m["account_number"] is None


def test_invite_returns_room_token_for_member(client):
    token = _room(client)
    sess, room_id = _join(client, token, "an")
    h = {"Authorization": f"Bearer {sess}"}
    r = client.get(f"/api/rooms/{room_id}/invite", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"invite_token": token}


def test_invite_requires_session(client):
    token = _room(client)
    _sess, room_id = _join(client, token, "an")
    assert client.get(f"/api/rooms/{room_id}/invite").status_code == 401


def test_invite_cross_room_is_403(client):
    token_a = _room(client)
    sess_a, _room_a = _join(client, token_a, "an")
    token_b = _room(client)
    _sess_b, room_b = _join(client, token_b, "an")
    ha = {"Authorization": f"Bearer {sess_a}"}
    assert client.get(f"/api/rooms/{room_b}/invite", headers=ha).status_code == 403


def test_room_info_exposes_bot_handle(client):
    token = _room(client)
    r = client.get(f"/api/rooms/{token}")
    assert r.status_code == 200
    assert "bot_handle" in r.json()


def test_room_info_lists_members_with_claim_status(client):
    token = _room(client)
    _sess, _room_id = _join(client, token, "an")  # claimed (joined with a PIN)

    from app import accounts, rooms
    from app.db import get_db
    with get_db().session() as s:  # unclaimed (agent-added, no PIN)
        r = rooms.room_by_invite(s, token)
        accounts.add_unclaimed(s, r, display_name="Bui Trang", nickname="trang")

    info = client.get(f"/api/rooms/{token}").json()
    members = {m["nickname"]: m for m in info["members"]}
    assert members["an"]["claimed"] is True
    assert members["trang"]["claimed"] is False
    # public roster must never leak the pin or banking fields
    assert all(set(m.keys()) == {"display_name", "nickname", "claimed"} for m in info["members"])


def _seed_draft(room_id, a, b, **overrides):
    from app import drafts
    from app.db import get_db

    payload = {
        "payer_member_id": a, "member_participants": [a, b], "guests": [],
        "bill_total": 200_000, "adjustments": [], "dish": None,
        "initiator": None, "note": None, "per_head_preview": 100_000,
        "raw_input": "seed",
    }
    payload.update(overrides)
    with get_db().session() as s:
        d, _extras = drafts.create_draft(s, room_id, payload)
        return d.id


def test_patch_and_commit_draft(client):
    token = _room(client)
    sess_a, room_id = _join(client, token, "an")
    sess_b, _ = _join(client, token, "binh")
    ha = {"Authorization": f"Bearer {sess_a}"}
    hb = {"Authorization": f"Bearer {sess_b}"}
    a = client.get("/api/me", headers=ha).json()["id"]
    b = client.get("/api/me", headers=hb).json()["id"]

    draft_id = _seed_draft(room_id, a, b)

    r = client.patch(f"/api/rooms/{room_id}/drafts/{draft_id}", json={"dish": "phở"}, headers=ha)
    assert r.status_code == 200, r.text

    r = client.post(f"/api/rooms/{room_id}/drafts/{draft_id}/commit", headers=ha)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "meal_id" in body

    # committed draft can no longer be edited/committed again.
    r = client.patch(f"/api/rooms/{room_id}/drafts/{draft_id}", json={"dish": "bún"}, headers=ha)
    assert r.status_code == 404
    r = client.post(f"/api/rooms/{room_id}/drafts/{draft_id}/commit", headers=ha)
    assert r.status_code == 409


def test_recommit_route_edits_committed_meal(client):
    token = _room(client)
    sess_a, room_id = _join(client, token, "an")
    sess_b, _ = _join(client, token, "binh")
    ha = {"Authorization": f"Bearer {sess_a}"}
    hb = {"Authorization": f"Bearer {sess_b}"}
    a = client.get("/api/me", headers=ha).json()["id"]
    b = client.get("/api/me", headers=hb).json()["id"]

    draft_id = _seed_draft(room_id, a, b)
    r = client.post(f"/api/rooms/{room_id}/drafts/{draft_id}/commit", headers=ha)
    assert r.status_code == 200, r.text
    old_meal_id = r.json()["meal_id"]

    r = client.post(
        f"/api/rooms/{room_id}/drafts/{draft_id}/recommit",
        json={"payer_member_id": a, "member_participants": [a, b],
              "guests": [], "bill_total": 600_000, "adjustments": []},
        headers=ha,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["meal_id"] > 0
    assert body["meal_id"] != old_meal_id


def test_recommit_route_rejects_settled_meal(client):
    token = _room(client)
    sess_a, room_id = _join(client, token, "an")
    sess_b, _ = _join(client, token, "binh")
    ha = {"Authorization": f"Bearer {sess_a}"}
    hb = {"Authorization": f"Bearer {sess_b}"}
    a = client.get("/api/me", headers=ha).json()["id"]
    b = client.get("/api/me", headers=hb).json()["id"]

    draft_id = _seed_draft(room_id, a, b)
    r = client.post(f"/api/rooms/{room_id}/drafts/{draft_id}/commit", headers=ha)
    assert r.status_code == 200, r.text

    from app import ledger
    from app.clock import today_ict
    from app.db import get_db
    with get_db().session() as s:
        ledger.record_settlement(
            s, room_id=room_id, period_from=None, period_to=today_ict(),
            requested_by=str(a), transfers=[],
        )

    r = client.post(
        f"/api/rooms/{room_id}/drafts/{draft_id}/recommit",
        json={"payer_member_id": a, "member_participants": [a, b],
              "guests": [], "bill_total": 600_000, "adjustments": []},
        headers=ha,
    )
    assert r.status_code == 409


def test_patch_draft_rejects_bad_status(client):
    token = _room(client)
    sess_a, room_id = _join(client, token, "an")
    sess_b, _ = _join(client, token, "binh")
    ha = {"Authorization": f"Bearer {sess_a}"}
    hb = {"Authorization": f"Bearer {sess_b}"}
    a = client.get("/api/me", headers=ha).json()["id"]
    b = client.get("/api/me", headers=hb).json()["id"]

    draft_id = _seed_draft(room_id, a, b)

    r = client.patch(f"/api/rooms/{room_id}/drafts/{draft_id}", json={"status": "committed"}, headers=ha)
    assert r.status_code == 400


def test_patch_and_commit_unknown_draft_is_404(client):
    token = _room(client)
    sess, room_id = _join(client, token, "an")
    h = {"Authorization": f"Bearer {sess}"}

    r = client.patch(f"/api/rooms/{room_id}/drafts/999999", json={"dish": "phở"}, headers=h)
    assert r.status_code == 404
    r = client.post(f"/api/rooms/{room_id}/drafts/999999/commit", headers=h)
    assert r.status_code == 409


def test_clear_command_posts_divider_and_skips_bot(client, monkeypatch):
    token = _room(client)
    sess, room_id = _join(client, token, "an")
    headers = {"Authorization": f"Bearer {sess}"}

    called = {"clear": 0, "bot": 0}

    async def fake_clear(db, rid, *, up_to_id, emit=None):
        called["clear"] += 1
        from app import chat
        with db.session() as s:
            return chat.post_message(s, rid, None, "🧹 reset", kind="context_reset")

    async def fake_bot(*a, **k):
        called["bot"] += 1

    monkeypatch.setattr("app.chat.clear_context", fake_clear, raising=False)
    monkeypatch.setattr("app.chat.run_bot_turn", fake_bot, raising=False)

    r = client.post(f"/api/rooms/{room_id}/messages", json={"body": "/clear"}, headers=headers)
    assert r.status_code == 200

    # allow the spawned background task to run
    import time
    for _ in range(50):
        if called["clear"]:
            break
        time.sleep(0.02)
    assert called["clear"] == 1
    assert called["bot"] == 0


def test_clear_command_with_bot_mention_still_skips_bot(client, monkeypatch):
    # "@bot /clear" matches BOTH is_clear_command (its regex allows an
    # optional leading "@bot"/"@<handle>") AND mentions_bot (it contains an
    # "@bot" token). This is the one case where the early `return` in the
    # is_clear_command branch of post_message is load-bearing: without it,
    # this message would fall through to the mentions_bot branch too and
    # also fire a bot turn.
    token = _room(client)
    sess, room_id = _join(client, token, "an")
    headers = {"Authorization": f"Bearer {sess}"}

    called = {"clear": 0, "bot": 0}

    async def fake_clear(db, rid, *, up_to_id, emit=None):
        called["clear"] += 1
        from app import chat
        with db.session() as s:
            return chat.post_message(s, rid, None, "🧹 reset", kind="context_reset")

    async def fake_bot(*a, **k):
        called["bot"] += 1

    monkeypatch.setattr("app.chat.clear_context", fake_clear, raising=False)
    monkeypatch.setattr("app.chat.run_bot_turn", fake_bot, raising=False)

    r = client.post(f"/api/rooms/{room_id}/messages", json={"body": "@bot /clear"}, headers=headers)
    assert r.status_code == 200

    # allow the spawned background task to run
    import time
    for _ in range(50):
        if called["clear"]:
            break
        time.sleep(0.02)
    assert called["clear"] == 1
    assert called["bot"] == 0


def test_public_create_room_creates_room_member_session(client):
    r = client.post("/api/rooms/create", json={
        "room_name": "Team A", "display_name": "An", "nickname": "an", "pin": "1234",
        "bank_code": "VCB", "account_number": "007", "account_holder": "AN NGUYEN",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["room_name"] == "Team A"
    assert body["invite_token"]
    # The returned token is a working session for the new room.
    h = {"Authorization": f"Bearer {body['token']}"}
    members = client.get(f"/api/rooms/{body['room_id']}/members", headers=h)
    assert members.status_code == 200
    assert members.json()[0]["nickname"] == "an"
    # The invite token admits a second joiner into the same room.
    _sess_b, rid = _join(client, body["invite_token"], "binh")
    assert rid == body["room_id"]


def test_public_create_room_rejects_missing_nickname_or_pin(client):
    r = client.post("/api/rooms/create", json={
        "room_name": "X", "display_name": "A", "nickname": "", "pin": ""})
    assert r.status_code == 422


def test_public_create_room_is_isolated_from_other_rooms(client):
    a = client.post("/api/rooms/create", json={
        "room_name": "A", "display_name": "An", "nickname": "an", "pin": "1"}).json()
    b = client.post("/api/rooms/create", json={
        "room_name": "B", "display_name": "Binh", "nickname": "binh", "pin": "2"}).json()
    ha = {"Authorization": f"Bearer {a['token']}"}
    assert client.get(f"/api/rooms/{b['room_id']}/messages", headers=ha).status_code == 403
