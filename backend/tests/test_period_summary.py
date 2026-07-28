from datetime import date
import pytest
from app.db import Database
from app import ledger
from app.tools import ToolContext, build_tools


@pytest.fixture
def setup(tmp_path):
    d = Database(f"sqlite:///{tmp_path}/t.db"); d.create_all()
    from app.models import Room, Member
    with d.session() as s:
        r = Room(name="t", invite_token="tok"); s.add(r); s.flush()
        m = {}
        for name in ("Linh", "Giang"):
            x = Member(room_id=r.id, display_name=name, nickname=name); s.add(x); s.flush()
            m[name] = x.id
        ledger.record_meal(s, room_id=r.id, payer_member_id=m["Linh"],
                           participants=[m["Linh"], m["Giang"]], total_amount=122000,
                           dish="bun bo", occurred_on=date(2026, 7, 21))
        ledger.record_payment(s, room_id=r.id, from_member_id=m["Giang"],
                              to_member_id=m["Linh"], amount=61000, occurred_on=date(2026, 7, 22))
        room = r.id
    return d, room, m


def test_summary_timeline_and_outstanding(setup):
    d, room, m = setup
    res = build_tools(ToolContext(db=d, room_id=room, sender_member_id=m["Giang"]))["get_period_summary"].execute({})
    assert res["type"] == "summary"
    kinds = [e["kind"] for e in res["timeline"]]
    assert kinds == ["meal", "payment"]
    assert res["timeline"][0]["payer_name"] == "Linh"
    assert res["timeline"][1]["from_name"] == "Giang" and res["timeline"][1]["to_name"] == "Linh"
    # 61k meal debt, 61k paid -> nothing left open, and no net column to read.
    assert res["outstanding"] == []
    assert "balances" not in res


def test_summary_lists_who_owes_whom_by_name(setup):
    d, room, m = setup
    with d.session() as s:
        ledger.record_meal(s, room_id=room, payer_member_id=m["Giang"],
                           participants=[m["Linh"], m["Giang"]], total_amount=80000,
                           dish="ca phe", occurred_on=date(2026, 7, 23))
    res = build_tools(ToolContext(db=d, room_id=room, sender_member_id=m["Giang"]))["get_period_summary"].execute({})
    assert res["outstanding"] == [
        {"debtor_id": m["Linh"], "debtor_name": "Linh",
         "creditor_id": m["Giang"], "creditor_name": "Giang", "amount": 40000},
    ]
