"""Every turn leaves a trace row (plan Task 4.1): the seeded pipeline, a failed turn,
the admin turns API, retention."""
import pytest

import app.agent as agent_mod
from app import chat
from app.agent import ToolInvocation, TurnResult
from app.kernel import kernel_for
from tests.test_ledger import _seed_room

ADMIN = {"X-Admin-Password": "test-admin-pw"}


async def test_a_turn_writes_a_trace_with_tools_args_results_and_outcome(db, monkeypatch):
    room_id, m = _seed_room(db, 2)
    k = kernel_for(db)

    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(final_text="", turn_id="t-1", stats={"tokens": 10, "cost": 0.001}, tools=[
            ToolInvocation("propose_meal", {"participants": m, "total": 90000},
                           {"ok": True, "payer_member_id": m[0], "member_participants": m, "bill_total": 90000})])

    monkeypatch.setattr(agent_mod, "run_turn", fake)
    reply = await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix tôi trả 90k")
    assert reply.kind == "expense_draft"
    rows = k.store.list_traces(str(room_id))
    assert len(rows) == 1 and rows[0]["turn_id"] == "t-1" and "tools" not in rows[0]
    full = k.store.get_trace(str(room_id), "t-1")
    assert full["summary"]["tools"] == ["propose_meal"] and full["summary"]["outcome"]["draft_kind"] == "expense_draft"
    assert full["tools"][0]["args"] == {"participants": m, "total": 90000} and full["tools"][0]["result"]["bill_total"] == 90000
    assert full["summary"]["tokens"] == 10 and full["summary"]["error"] is None
    stages = [t["stage"] for t in full["trace"]]
    assert stages[0] == "context" and "run" in stages and "persist" in stages and "after" not in stages
    assert k.store.get_trace(str(room_id), full["id"]) == full          # by row id too


async def test_a_turn_that_raises_before_the_engine_is_traced_with_its_error(db, monkeypatch):
    room_id, m = _seed_room(db, 2)
    k = kernel_for(db)

    async def boom(user_text, ctx, images=None, emit=None, memory=None, history=None):
        raise RuntimeError("sidecar down")

    monkeypatch.setattr(agent_mod, "run_turn", boom)
    with pytest.raises(RuntimeError):
        await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix x")
    rows = k.store.list_traces(str(room_id))
    assert len(rows) == 1 and rows[0]["turn_id"] is None
    assert rows[0]["summary"]["error"] == "RuntimeError: sidecar down" and rows[0]["summary"]["tools"] == []
    assert k.store.get_trace(str(room_id), rows[0]["id"])["trace"][-1]["outcome"] == "error"


def test_admin_turns_routes(api_client_room, monkeypatch):
    client, headers, room_id, m = api_client_room
    from app.db import get_db
    k = kernel_for(get_db())
    k.store.write_trace(str(room_id), "t-a", started="2026-09-06T08:00:00+00:00", finished="2026-09-06T08:00:01+00:00",
                        summary={"tools": ["settle_period"]}, tools=[{"name": "settle_period"}], trace=[])
    assert client.get(f"/api/admin/spaces/{room_id}/turns").status_code == 401
    r = client.get(f"/api/admin/spaces/{room_id}/turns", headers=ADMIN)
    assert r.status_code == 200 and [x["turn_id"] for x in r.json()] == ["t-a"] and "tools" not in r.json()[0]
    r = client.get(f"/api/admin/spaces/{room_id}/turns/t-a", headers=ADMIN)
    assert r.status_code == 200 and r.json()["tools"] == [{"name": "settle_period"}]
    assert client.get(f"/api/admin/spaces/{room_id}/turns/{r.json()['id']}", headers=ADMIN).json() == r.json()
    assert client.get(f"/api/admin/spaces/{room_id}/turns/nope", headers=ADMIN).status_code == 404
    assert client.get(f"/api/admin/spaces/{room_id + 1}/turns/t-a", headers=ADMIN).status_code == 404


def test_keep_days_prunes_old_rows_on_write(db):
    k = kernel_for(db)
    k.store.write_trace("1", "old", started="2026-07-01T00:00:00+00:00", finished="2026-07-01T00:00:01+00:00",
                        summary={}, tools=[], trace=[])
    k.store.write_trace("1", "new", started="2026-09-06T00:00:00+00:00", finished="2026-09-06T00:00:01+00:00",
                        summary={}, tools=[], trace=[], keep_days=30)
    assert [r["turn_id"] for r in k.store.list_traces("1")] == ["new"]
