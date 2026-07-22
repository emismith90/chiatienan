import os
import tempfile

import pytest

# Point the module-level Settings at writable temp paths BEFORE any app import
# (config.settings is frozen at import time). Real env, if present, wins.
_TMP = tempfile.mkdtemp(prefix="chiatienan-test-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/default.db")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pw")
os.environ.setdefault("CURSOR_SDK_WORKSPACE", f"{_TMP}/ws")

from app.db import Database  # noqa: E402


@pytest.fixture
def db(tmp_path):
    """A fresh file-backed SQLite database per test (WAL, real single-writer)."""
    database = Database(f"sqlite:///{tmp_path}/test.db")
    database.create_all()
    return database


@pytest.fixture
def api_client_room(db, monkeypatch):
    """An authenticated room with two members for API tests.

    Points ``app.db._default`` at the per-test ``db`` (so ``get_db()`` returns
    the same database in the route, auth, and the test body), creates a room via
    the public ``/api/rooms/create`` flow, joins a second member, and returns
    ``(client, headers, room_id, {display_name: member_id})``. The header uses
    the same ``Authorization: Bearer <token>`` key as the other API tests.
    """
    monkeypatch.setattr("app.db._default", db, raising=False)
    import app.main as main
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    created = client.post("/api/rooms/create", json={
        "room_name": "Lunch", "display_name": "Linh", "nickname": "linh", "pin": "1234",
    })
    assert created.status_code == 200, created.text
    body = created.json()
    room_id = body["room_id"]
    headers = {"Authorization": f"Bearer {body['token']}"}

    joined = client.post(f"/api/rooms/{body['invite_token']}/accounts", json={
        "display_name": "Giang", "nickname": "giang", "pin": "1234",
    })
    assert joined.status_code == 200, joined.text

    members = client.get(f"/api/rooms/{room_id}/members", headers=headers).json()
    by_name = {m["display_name"]: m["id"] for m in members}
    return client, headers, room_id, by_name
