"""Deterministic end-to-end eval: plays scenario_week.STEPS through the real
money engine with the clock frozen per day, asserting balances/transfers/QR and
rendered bodies at each step. No LLM involved — the correct tool calls are
encoded by `kind`.

The settlement comparison itself lives in `bench.graders` and is imported back
here, so the deterministic eval and the LLM benchmark judge money with exactly
one implementation. This test passing is what proves the extraction was faithful.
"""
from datetime import date, datetime, time

from app import drafts, ledger, tools
from app.clock import ICT
from app.models import Member, Room
from bench.graders import balances_by_member, compare_settlement
from tests.golden.scenario_week import MEMBERS, STEPS


def _seed(db):
    ids = {}
    with db.session() as s:
        room = Room(name="Week", invite_token="week-tok")
        s.add(room); s.flush()
        for spec in MEMBERS:
            m = Member(room_id=room.id, display_name=spec["display_name"],
                       nickname=spec["nickname"], pin="1", **(spec.get("bank") or {}))
            s.add(m); s.flush()
            ids[spec["key"]] = m.id
        return room.id, ids


def _freeze(monkeypatch, day_iso):
    d = date.fromisoformat(day_iso)
    frozen = datetime.combine(d, time(12, 0), tzinfo=ICT)
    monkeypatch.setattr("app.clock.now_ict", lambda: frozen)


def test_scenario_week(db, monkeypatch):
    room_id, ids = _seed(db)
    draft_by_step = {}  # step id -> draft_id (for confirm_pending refs)

    for step in STEPS:
        _freeze(monkeypatch, step["day"])
        kind = step["kind"]
        actor = ids.get(step["actor"])

        if kind == "add_member":
            with db.session() as s:
                m = Member(room_id=room_id, display_name=step["new_member"].upper(),
                           nickname=step["new_member"], pin="1")
                s.add(m); s.flush()
                ids[step["new_member"]] = m.id

        elif kind in ("meal_confirmed", "leave_pending"):
            payload = {
                "payer_member_id": ids[step["payer"]],
                "member_participants": [ids[p] for p in step["participants"]],
                "guests": step.get("guests", []),
                "bill_total": step["total"], "adjustments": [],
                "per_head_preview": 0, "raw_input": step["message"],
            }
            with db.session() as s:
                d, _ = drafts.create_draft(s, room_id, payload)
                draft_by_step[step["id"]] = d.id
                if kind == "meal_confirmed":
                    drafts.commit_draft(s, d.id, room_id, logged_by=str(actor))

        elif kind == "confirm_pending":
            with db.session() as s:
                drafts.commit_draft(s, draft_by_step[step["ref"]], room_id, logged_by=str(actor))

        elif kind == "payment":
            with db.session() as s:
                ledger.record_payment(s, room_id=room_id, from_member_id=ids[step["from"]],
                                      to_member_id=ids[step["to"]], amount=step["amount"],
                                      logged_by=str(actor))

        elif kind == "settle":
            ctx = tools.ToolContext(db=db, room_id=room_id, sender_member_id=actor)
            res = tools.build_tools(ctx)["settle_period"].execute({"keyword": "since_last"})
            exp = step.get("expect", {})
            problems = compare_settlement(res, exp, ids)
            assert problems == [], f'{step["id"]}: {problems}'
            if exp.get("blocked_pending") is not None:
                continue

        # Balance assertion (when the step declares expected balances).
        exp = step.get("expect", {})
        if "balances" in exp:
            bal = balances_by_member(db, room_id)
            for key, want in exp["balances"].items():
                assert bal.get(ids[key], 0) == want, f'{step["id"]} {key}: {bal.get(ids[key])} != {want}'
