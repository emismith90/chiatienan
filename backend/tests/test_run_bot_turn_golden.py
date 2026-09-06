"""Golden fixtures for ``chat.run_bot_turn``'s outcome branches.

Recorded from the code as it stood **before** the kernos pipeline refactor
(plan Task 1.0), so the refactor can be proven byte-identical on the one path the
benchmark never exercises: ``bench.run`` calls ``app.agent.run_turn`` directly
and never enters ``run_bot_turn`` (review finding 6).

Each scenario drives ``run_bot_turn`` with a fake ``run_turn`` — the same
six-argument shape the eleven fakes in ``test_chat.py`` use — records what was
persisted, every event ``emit`` received in order, and what the fake was handed
(memory, history, image count). ``created_at`` is stripped; every other id is
deterministic in a fresh database and is asserted literally.

To re-record after an *intended* behaviour change::

    python -m tests.test_run_bot_turn_golden --record

which rewrites ``tests/golden/turn_fixtures.py``. A diff in that file is a
behaviour change and must be explained in the commit.
"""
from __future__ import annotations

import asyncio
import pprint
import sys
from pathlib import Path

import pytest

import app.agent as agent_mod
from app import chat, drafts
from app.agent import ToolInvocation, TurnResult
from app.db import Database
from app.models import RoomMessage
from tests.test_ledger import _seed_room

FIXTURES_PATH = Path(__file__).parent / "golden" / "turn_fixtures.py"

_EVENTS = [
    {"type": "agent.run.started", "turn_id": "t-golden"},
    {"type": "agent.tool.start", "turn_id": "t-golden", "call_id": "c1", "name": "x", "args": {}},
    {"type": "agent.tool.result", "turn_id": "t-golden", "call_id": "c1", "name": "x",
     "status": "completed", "result": {"ok": True}},
    {"type": "agent.run.finished", "turn_id": "t-golden"},
]


def _strip(d: dict | None) -> dict | None:
    if d is None:
        return None
    out = dict(d)
    out.pop("created_at", None)
    return out


def _meal_payload(a: int, b: int) -> dict:
    return {
        "ok": True, "type": "expense_draft", "payer_member_id": a,
        "member_participants": [a, b], "guests": [], "bill_total": 300000,
        "adjustments": [], "dish": "phở", "initiator": None, "note": None,
        "per_head_preview": 150000, "occurred_on": "2026-08-20",
    }


def _settle_result() -> dict:
    return {
        "ok": True, "period": {"from": "2026-07-01", "to": "2026-07-20"},
        "transfers": [{"from_id": 2, "from_name": "M2", "to_id": 1, "to_name": "M1",
                       "amount": 123456, "note": "x", "qr_url": None}],
        "warnings": [], "committed": False,
    }


#: name → (setup(db, room_id, members) -> extra, fake TurnResult factory(extra), user text)
def _scenarios():
    def no_setup(db, room_id, m):
        return {}

    def pending_meal(db, room_id, m):
        payload = {k: v for k, v in _meal_payload(m[0], m[1]).items() if k not in ("ok", "type")}
        payload["raw_input"] = "@phoenix ghi 300k M1 M2"
        with db.session() as s:
            d, _ = drafts.create_draft(s, room_id, payload)
            return {"draft_id": d.id}

    return {
        "meal_draft": (no_setup, lambda x, m: TurnResult(
            final_text="ghi rồi nhé, mỗi người 1đ thôi", turn_id="t-golden",
            tools=[ToolInvocation("propose_meal", {}, _meal_payload(m[0], m[1]))]),
            "@phoenix ghi 300k M1 M2"),
        "meal_draft_supersedes_pending": (pending_meal, lambda x, m: TurnResult(
            final_text="", turn_id="t-golden",
            tools=[ToolInvocation("propose_meal", {}, _meal_payload(m[0], m[1]))]),
            "@phoenix ghi 300k M1 M2"),
        "payment_draft": (no_setup, lambda x, m: TurnResult(
            final_text="ok", turn_id="t-golden",
            tools=[ToolInvocation("propose_payment", {}, {
                "ok": True, "type": "payment_draft", "from_member_id": m[0],
                "to_member_id": m[1], "amount": 50000, "note": None})]),
            "@phoenix M1 trả M2 50k"),
        "settlement_body": (no_setup, lambda x, m: TurnResult(
            final_text="Đã chốt xong nhé, M2 nợ M1 999đ thôi", turn_id="t-golden",
            tools=[ToolInvocation("settle_period", {}, _settle_result())]),
            "@phoenix chốt kỳ"),
        "free_prose_unbacked_warns": (no_setup, lambda x, m: TurnResult(
            final_text="Bùi Trang −75,000đ · Giang Hoàng +89,000đ", turn_id="t-golden"),
            "@phoenix tóm tắt số dư"),
        "fabricated_commit_blocked": (no_setup, lambda x, m: TurnResult(
            final_text="Đã ghi #14 — Texas Chicken: Bạch Mai trả tổng 793,760đ • M1 132,293đ",
            turn_id="t-golden"),
            "@phoenix log this for all"),
        "engine_error": (no_setup, lambda x, m: TurnResult(
            final_text="", error="boom", turn_id="t-golden"), "@phoenix ai trả tuần này"),
        "capped_empty": (no_setup, lambda x, m: TurnResult(
            final_text="", capped=True, turn_id="t-golden"), "@phoenix ăn gì"),
        "cancelled_draft_republished": (pending_meal, lambda x, m: TurnResult(
            final_text="Huỷ rồi nhé", turn_id="t-golden",
            tools=[ToolInvocation("cancel_draft", {"draft_id": x["draft_id"]},
                                  {"ok": True, "type": "draft_cancelled",
                                   "draft_id": x["draft_id"], "kind": "expense_draft"})]),
            "@phoenix huỷ thẻ"),
    }


async def run_scenario(db: Database, name: str, monkeypatch) -> dict:
    setup, make_result, text = _scenarios()[name]
    room_id, m = _seed_room(db, 3)
    with db.session() as s:
        chat.post_message(s, room_id, m[1], "hôm qua ăn bún bò")
        chat.post_message(s, room_id, None, "Ok, chưa ghi gì.", kind="bot")
    extra = setup(db, room_id, m)

    handed: dict = {}
    events: list[dict] = []

    async def _fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        handed.update(user_text=user_text, memory=memory, history=history,
                      images=len(images or []), room_id=ctx.room_id,
                      sender=(ctx.sender_member_id, ctx.sender_name))
        for ev in _EVENTS:
            await emit(ev)
        return make_result(extra, m)

    async def _emit(ev):
        events.append(_strip(ev))

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run_turn)
    msg = await chat.run_bot_turn(db, room_id, m[0], "M1", text, emit=_emit)

    with db.session() as s:
        rows = s.scalars(chat.select(RoomMessage).where(RoomMessage.room_id == room_id)
                         .order_by(RoomMessage.id)).all()
        all_messages = [_strip(chat.message_to_dict(r, None)) for r in rows]
    return {
        "returned": _strip(chat.message_to_dict(msg, None)),
        "messages": all_messages,
        "events": events,
        "handed": handed,
    }


@pytest.mark.parametrize("name", list(_scenarios()))
async def test_run_bot_turn_matches_golden(name, db, monkeypatch):
    from tests.golden.turn_fixtures import FIXTURES
    got = await run_scenario(db, name, monkeypatch)
    assert got == FIXTURES[name], f"{name} drifted from the recorded fixture"


def _record() -> None:
    """Re-record every scenario into ``turn_fixtures.py`` (see module docstring)."""
    import tempfile

    class _MP:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)

    out = {}
    for name in _scenarios():
        tmp = tempfile.mkdtemp(prefix="golden-")
        db = Database(f"sqlite:///{tmp}/g.db")
        db.create_all()
        out[name] = asyncio.run(run_scenario(db, name, _MP()))
    body = pprint.pformat(out, width=96, sort_dicts=False)
    FIXTURES_PATH.write_text(
        '"""GENERATED by `python -m tests.test_run_bot_turn_golden --record` — do not hand-edit.\n\n'
        "What `chat.run_bot_turn` persisted, emitted and handed to the engine for each\n"
        "outcome branch, recorded before the kernos pipeline refactor (plan Task 1.0).\n"
        '"""\n\nFIXTURES = ' + body + "\n",
        encoding="utf-8",
    )
    print(f"recorded {len(out)} scenarios → {FIXTURES_PATH}")


if __name__ == "__main__" and "--record" in sys.argv:
    _record()
