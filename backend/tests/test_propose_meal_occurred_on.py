import pytest
from app.db import Database
from app.tools import ToolContext, build_tools


@pytest.fixture
def setup(tmp_path):
    d = Database(f"sqlite:///{tmp_path}/t.db")
    d.create_all()
    from app.models import Room, Member
    with d.session() as s:
        r = Room(name="t", invite_token="tok"); s.add(r); s.flush()
        m = {}
        for name in ("An", "Binh"):
            x = Member(room_id=r.id, display_name=name, nickname=name)
            s.add(x); s.flush(); m[name] = x.id
        room_id = r.id
    return d, room_id, m


def _tools(d, room_id, sender):
    return build_tools(ToolContext(db=d, room_id=room_id, sender_member_id=sender))


def test_occurred_on_non_iso_is_rejected(setup):
    d, room, m = setup
    res = _tools(d, room, m["An"])["propose_meal"].execute({
        "participants": [m["An"], m["Binh"]],
        "total": 100000,
        "occurred_on": "thứ 2",
    })
    assert res["ok"] is False
    assert "error" in res


def test_occurred_on_valid_iso_is_echoed(setup):
    d, room, m = setup
    res = _tools(d, room, m["An"])["propose_meal"].execute({
        "participants": [m["An"], m["Binh"]],
        "total": 100000,
        "occurred_on": "2026-07-20",
    })
    assert res["ok"] is True
    assert res["type"] == "expense_draft"
    assert res["occurred_on"] == "2026-07-20"
