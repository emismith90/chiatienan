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


def test_summary_timeline_and_balances(setup):
    d, room, m = setup
    res = build_tools(ToolContext(db=d, room_id=room, sender_member_id=m["Giang"]))["get_period_summary"].execute({})
    assert res["type"] == "summary"
    kinds = [e["kind"] for e in res["timeline"]]
    assert kinds == ["meal", "payment"]
    assert res["timeline"][0]["payer_name"] == "Linh"
    assert res["timeline"][1]["from_name"] == "Giang" and res["timeline"][1]["to_name"] == "Linh"
    bal = {b["name"]: b["balance"] for b in res["balances"]}
    assert bal["Giang"] == 0 and bal["Linh"] == 0     # 61k meal debt, 61k paid -> even
