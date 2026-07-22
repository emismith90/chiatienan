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
        room = r.id
    return d, room, m


def test_statement_defaults_to_sender_and_splits_directions(setup):
    d, room, m = setup
    res = build_tools(ToolContext(db=d, room_id=room, sender_member_id=m["Giang"]))["member_statement"].execute({})
    assert res["member"]["id"] == m["Giang"]
    assert len(res["owe"]) == 1 and res["owe"][0]["name"] == "Linh"
    assert res["owe"][0]["amount"] == 61000 and res["owe"][0]["status"] == "unpaid"
    assert res["owe"][0]["dish"] == "bun bo"
    assert res["owed"] == []
    assert res["net"] == -61000
