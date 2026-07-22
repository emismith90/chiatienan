import pytest

from app.realtime import hub
from tests.conftest import _make_meal_draft


@pytest.mark.asyncio
async def test_commit_draft_publishes_ledger_changed(api_client_room, monkeypatch):
    client, headers, room_id, m = api_client_room
    seen = []
    orig = hub.publish

    async def spy(rid, ev):
        if rid == room_id and ev.get("type") == "ledger:changed":
            seen.append(ev)
        await orig(rid, ev)

    monkeypatch.setattr(hub, "publish", spy)
    draft_id = _make_meal_draft(client, headers, room_id, m)
    r = client.post(f"/api/rooms/{room_id}/drafts/{draft_id}/commit", headers=headers)
    assert r.status_code == 200
    assert seen, "expected a ledger:changed event after commit"
