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
    assert "net" not in res


def test_non_numeric_member_returns_err_not_raise(setup):
    d, room, m = setup
    res = build_tools(ToolContext(db=d, room_id=room, sender_member_id=m["Giang"]))["member_statement"].execute({"member": "abc"})
    assert res["ok"] is False


def test_statement_reports_both_directions_without_netting_them(setup):
    """Production, 2026-07-27: the reply ended "Ròng: -54.500đ". It answered
    neither "tôi nợ ai" nor "ai nợ tôi", and where debts ran both ways it read as
    though they had been offset. Both edges must survive, whole, side by side."""
    d, room, m = setup
    with d.session() as s:
        ledger.record_meal(s, room_id=room, payer_member_id=m["Giang"],
                           participants=[m["Linh"], m["Giang"]], total_amount=40000,
                           dish="ca phe", occurred_on=date(2026, 7, 22))
    res = build_tools(ToolContext(db=d, room_id=room, sender_member_id=m["Giang"]))["member_statement"].execute({})
    assert [r["amount"] for r in res["owe"]] == [61000]
    assert [r["amount"] for r in res["owed"]] == [20000]
    assert "net" not in res            # not 61000 - 20000, and not -41000 either
