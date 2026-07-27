"""Tests for the read-only debug/export API (app/debug_api.py).

The behaviour worth pinning down here is mostly negative: that the API is off
until configured, that a wrong key gets nothing, and above all that no export
path — CSV, table dump, or SQLite snapshot — can be used to lift a live session
token, an invite token, a PIN or a bank account number out of production.
"""
from __future__ import annotations

import csv
import dataclasses
import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import debug_api
from app.models import Member, Room, RoomMessage, Session

KEY = "test-debug-key-long-enough-to-be-accepted"


def use_settings(monkeypatch, **overrides):
    """Swap ``debug_api.settings`` for a copy with ``overrides`` applied.

    ``Settings`` is a frozen dataclass, so individual attributes cannot be
    monkeypatched — replace the whole object instead.
    """
    monkeypatch.setattr(
        debug_api, "settings",
        dataclasses.replace(debug_api.settings, **overrides), raising=False)


@pytest.fixture
def client(db, monkeypatch):
    """App + a room with two members, a chatlog, and a live session token."""
    monkeypatch.setattr("app.db._default", db, raising=False)
    use_settings(monkeypatch, debug_api_key=KEY, database_url=str(db.engine.url))
    import app.main as main

    with db.session() as s:
        room = Room(name="Lunch", invite_token="super-secret-invite")
        s.add(room)
        s.flush()
        linh = Member(room_id=room.id, display_name="Linh", nickname="linh", pin="1234",
                      bank_code="VCB", account_number="0123456789", account_holder="LINH")
        an = Member(room_id=room.id, display_name="An", nickname="an", pin="9999")
        s.add_all([linh, an])
        s.flush()
        s.add(Session(member_id=linh.id, token="live-bearer-token-do-not-leak"))
        s.add(RoomMessage(room_id=room.id, author_member_id=linh.id, kind="text",
                          body="cơm tấm 120k"))
        s.add(RoomMessage(room_id=room.id, author_member_id=None, kind="bot",
                          body="đã ghi nhé"))
        s.add(RoomMessage(
            room_id=room.id, author_member_id=an.id, kind="text", body="bill đây",
            attachments={"images": [{"data": "A" * 5000, "mime": "image/png"}]}))
        s.add(RoomMessage(
            room_id=room.id, author_member_id=None, kind="expense_draft", body="",
            attachments={"type": "expense_draft", "bill_total": 200000,
                         "items": [{"member": linh.id, "amount": 120000}]}))
        s.commit()
        room_id = room.id

    return TestClient(main.app), room_id


def _csv_rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


# --- the gate ---------------------------------------------------------------

def test_disabled_when_key_unset_looks_like_it_does_not_exist(client, monkeypatch):
    c, _ = client
    use_settings(monkeypatch, debug_api_key="")
    # 404, not 401: an unconfigured deploy should not advertise the surface.
    assert c.get("/internal/debug/ping", headers={"X-Debug-Key": KEY}).status_code == 404


def test_short_key_refuses_to_enable(client, monkeypatch):
    """A placeholder like DEBUG_API_KEY=test must not expose the ledger."""
    c, _ = client
    use_settings(monkeypatch, debug_api_key="test")
    assert c.get("/internal/debug/ping", headers={"X-Debug-Key": "test"}).status_code == 404


@pytest.mark.parametrize("headers", [{}, {"X-Debug-Key": "wrong"}, {"X-Debug-Key": ""}])
def test_missing_or_wrong_key_is_rejected(client, headers):
    c, _ = client
    assert c.get("/internal/debug/ping", headers=headers).status_code == 401


def test_every_route_is_gated(client):
    """No route may be reachable without the key."""
    c, room_id = client
    for path in ("/ping", "/rooms", "/conversation.csv", "/conversation.txt",
                 "/tables", "/tables/meals.csv", "/db", "/logs"):
        r = c.get(f"/internal/debug{path}")
        assert r.status_code == 401, f"{path} was not gated (got {r.status_code})"


# --- secrets never leave ----------------------------------------------------

def test_sessions_table_is_not_exportable(client):
    c, _ = client
    h = {"X-Debug-Key": KEY}
    assert "sessions" not in c.get("/internal/debug/tables", headers=h).json()["tables"]
    assert c.get("/internal/debug/tables/sessions.csv", headers=h).status_code == 404


def test_invite_token_pin_and_bank_account_are_redacted(client):
    c, _ = client
    h = {"X-Debug-Key": KEY}
    rooms = c.get("/internal/debug/tables/rooms.csv", headers=h).text
    assert "super-secret-invite" not in rooms
    assert "[redacted]" in rooms

    members = c.get("/internal/debug/tables/members.csv", headers=h).text
    assert "1234" not in members and "9999" not in members
    assert "0123456789" not in members
    # Non-secret columns still come through — the dump must stay useful.
    assert "Linh" in members and "VCB" in members


def test_no_export_path_leaks_the_session_token(client):
    """Belt-and-braces sweep: the live token must not appear in ANY response."""
    c, room_id = client
    h = {"X-Debug-Key": KEY}
    for path in ("/ping", "/rooms", "/conversation.csv", "/conversation.txt",
                 "/tables", "/tables/members.csv", "/tables/rooms.csv",
                 "/tables/room_messages.csv"):
        r = c.get(f"/internal/debug{path}", headers=h)
        assert r.status_code == 200, path
        assert "live-bearer-token-do-not-leak" not in r.text, f"token leaked via {path}"


def test_snapshot_is_sanitised(client, tmp_path):
    c, _ = client
    r = c.get("/internal/debug/db", headers={"X-Debug-Key": KEY})
    assert r.status_code == 200
    out = tmp_path / "snap.db"
    out.write_bytes(r.content)

    con = sqlite3.connect(out)
    assert con.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    # Secrets overwritten, not nulled (invite_token is NOT NULL + UNIQUE).
    assert [t for (t,) in con.execute("SELECT invite_token FROM rooms")] == ["[redacted]-1"]
    assert con.execute(
        "SELECT count(*) FROM rooms WHERE invite_token = 'super-secret-invite'").fetchone()[0] == 0
    assert con.execute(
        "SELECT count(*) FROM members WHERE pin IN ('1234','9999')").fetchone()[0] == 0
    assert con.execute(
        "SELECT count(*) FROM members WHERE account_number = '0123456789'").fetchone()[0] == 0
    # ...but the data you came for survives.
    assert con.execute("SELECT count(*) FROM room_messages").fetchone()[0] == 4
    assert con.execute("SELECT count(*) FROM members").fetchone()[0] == 2
    assert b"live-bearer-token-do-not-leak" not in out.read_bytes()
    assert b"super-secret-invite" not in out.read_bytes()
    con.close()


def test_snapshot_keeps_unclaimed_accounts_distinguishable(client, db, tmp_path):
    """A NULL pin means "unclaimed" — redaction must not invent a value."""
    c, room_id = client
    with db.session() as s:
        s.add(Member(room_id=room_id, display_name="Ghost", nickname="ghost", pin=None))
        s.commit()
    out = tmp_path / "snap2.db"
    out.write_bytes(c.get("/internal/debug/db", headers={"X-Debug-Key": KEY}).content)
    con = sqlite3.connect(out)
    assert con.execute(
        "SELECT count(*) FROM members WHERE pin IS NULL").fetchone()[0] == 1
    con.close()


# --- the data you actually want ---------------------------------------------

def test_conversation_csv_has_authors_and_bot_rows(client):
    c, room_id = client
    rows = _csv_rows(c.get(f"/internal/debug/conversation.csv?room_id={room_id}",
                           headers={"X-Debug-Key": KEY}).text)
    assert [r["author"] for r in rows] == ["Linh", "BOT", "An", "BOT"]
    assert rows[0]["body"] == "cơm tấm 120k"
    assert rows[0]["kind"] == "text"


def test_image_payloads_are_stripped_but_counted(client):
    c, room_id = client
    r = c.get(f"/internal/debug/conversation.csv?room_id={room_id}",
              headers={"X-Debug-Key": KEY})
    assert "A" * 5000 not in r.text          # the base64 blob is gone
    att = json.loads([x for x in _csv_rows(r.text) if x["body"] == "bill đây"][0]["attachments"])
    assert att["images"][0] == {"mime": "image/png", "omitted": True, "bytes": 5000}


def test_keep_images_restores_the_payload(client):
    c, room_id = client
    r = c.get(f"/internal/debug/conversation.csv?room_id={room_id}&keep_images=true",
              headers={"X-Debug-Key": KEY})
    assert "A" * 5000 in r.text


def test_draft_numbers_survive_scrubbing(client):
    """Draft attachments are the point of the export — items must not be lost."""
    c, room_id = client
    rows = _csv_rows(c.get(f"/internal/debug/conversation.csv?room_id={room_id}",
                           headers={"X-Debug-Key": KEY}).text)
    draft = json.loads([r for r in rows if r["kind"] == "expense_draft"][0]["attachments"])
    assert draft["bill_total"] == 200000
    assert draft["items"][0]["amount"] == 120000


def test_conversation_txt_is_a_readable_transcript(client):
    c, room_id = client
    body = c.get(f"/internal/debug/conversation.txt?room_id={room_id}",
                 headers={"X-Debug-Key": KEY}).text
    lines = body.strip().split("\n")
    assert "[text]  Linh:  cơm tấm 120k" in lines[0]
    assert "[bot]  BOT:  đã ghi nhé" in lines[1]
    assert "[ảnh: 1]" in lines[2]        # a pasted bill is visible, not silently absent


def test_since_id_makes_polling_incremental(client):
    c, room_id = client
    h = {"X-Debug-Key": KEY}
    rows = _csv_rows(c.get(f"/internal/debug/conversation.csv?room_id={room_id}", headers=h).text)
    first = int(rows[0]["id"])
    after = _csv_rows(c.get(
        f"/internal/debug/conversation.csv?room_id={room_id}&since_id={first}", headers=h).text)
    assert len(after) == len(rows) - 1
    assert all(int(r["id"]) > first for r in after)


def test_limit_keeps_the_recent_tail_in_chronological_order(client):
    c, room_id = client
    rows = _csv_rows(c.get(f"/internal/debug/conversation.csv?room_id={room_id}&limit=2",
                           headers={"X-Debug-Key": KEY}).text)
    ids = [int(r["id"]) for r in rows]
    assert len(ids) == 2
    assert ids == sorted(ids)             # chronological...
    assert ids[-1] == 4                   # ...but it is the newest two


def test_room_filter_scopes_the_dump(client, db):
    c, room_id = client
    with db.session() as s:
        other = Room(name="Other", invite_token="tok2")
        s.add(other)
        s.flush()
        s.add(RoomMessage(room_id=other.id, author_member_id=None, kind="text", body="elsewhere"))
        s.commit()
    body = c.get(f"/internal/debug/conversation.csv?room_id={room_id}",
                 headers={"X-Debug-Key": KEY}).text
    assert "elsewhere" not in body


def test_unknown_table_is_404_not_a_sql_error(client):
    c, _ = client
    r = c.get("/internal/debug/tables/does_not_exist.csv", headers={"X-Debug-Key": KEY})
    assert r.status_code == 404


def test_table_name_is_not_interpolated(client):
    """The whitelist blocks injection through the path parameter."""
    c, _ = client
    r = c.get("/internal/debug/tables/members%20--.csv", headers={"X-Debug-Key": KEY})
    assert r.status_code == 404


def test_rooms_listing_reports_counts(client):
    c, room_id = client
    rooms = c.get("/internal/debug/rooms", headers={"X-Debug-Key": KEY}).json()["rooms"]
    row = [r for r in rooms if r["id"] == room_id][0]
    assert row["messages"] == 4 and row["members"] == 2
    assert "invite_token" not in row


def test_logs_404s_with_a_useful_message_when_absent(client, monkeypatch, tmp_path):
    c, _ = client
    use_settings(monkeypatch, debug_api_key=KEY, log_file=str(tmp_path / "nope.log"))
    r = c.get("/internal/debug/logs", headers={"X-Debug-Key": KEY})
    assert r.status_code == 404
    assert "docker compose logs" in r.json()["detail"]


def test_logs_tails_the_file(client, monkeypatch, tmp_path):
    c, _ = client
    p = tmp_path / "app.log"
    p.write_text("\n".join(f"line {i}" for i in range(1, 1001)) + "\n", encoding="utf-8")
    use_settings(monkeypatch, debug_api_key=KEY, log_file=str(p))
    body = c.get("/internal/debug/logs?lines=5", headers={"X-Debug-Key": KEY}).text
    assert body.strip().split("\n") == ["line 996", "line 997", "line 998", "line 999", "line 1000"]
