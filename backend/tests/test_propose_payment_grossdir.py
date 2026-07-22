from datetime import date
import pytest
from app.db import Database
from app import ledger
from app.tools import ToolContext, build_tools


@pytest.fixture
def setup(tmp_path):
    d = Database(f"sqlite:///{tmp_path}/t.db")
    d.create_all()
    from app.models import Room, Member
    with d.session() as s:
        r = Room(name="t", invite_token="tok"); s.add(r); s.flush()
        m = {}
        for name in ("Linh", "Giang", "Dung"):
            x = Member(room_id=r.id, display_name=name, nickname=name)
            s.add(x); s.flush(); m[name] = x.id
        # Giang owes Linh 61k (bun bo, Linh paid); Linh owes Giang 75k (nem, Giang paid)
        ledger.record_meal(s, room_id=r.id, payer_member_id=m["Linh"],
                           participants=[m["Linh"], m["Giang"], m["Dung"]], total_amount=183000,
                           dish="bun bo", occurred_on=date(2026, 7, 21))  # 61k each
        ledger.record_meal(s, room_id=r.id, payer_member_id=m["Giang"],
                           participants=[m["Linh"], m["Giang"]], total_amount=150000,
                           dish="nem", occurred_on=date(2026, 7, 22))     # 75k each
        room_id = r.id
    return d, room_id, m


def _tools(d, room_id, sender):
    return build_tools(ToolContext(db=d, room_id=room_id, sender_member_id=sender))


def test_one_sided_autofills_gross(setup):
    d, room, m = setup
    # Dung owes Linh 61k, nothing the other way -> unambiguous draft
    res = _tools(d, room, m["Dung"])["propose_payment"].execute({"to": m["Linh"]})
    assert res["type"] == "payment_draft" and res["amount"] == 61000


def test_two_way_is_ambiguous(setup):
    d, room, m = setup
    res = _tools(d, room, m["Giang"])["propose_payment"].execute({"to": m["Linh"]})
    assert res["type"] == "payment_ambiguous"
    assert res["gross"]["amount"] == 61000              # Giang -> Linh, full
    assert res["offset"]["amount"] == 14000             # net 75k-61k
    assert res["offset"]["from_member_id"] == m["Linh"] # net direction flips


def test_mode_gross_records_full(setup):
    d, room, m = setup
    res = _tools(d, room, m["Giang"])["propose_payment"].execute({"to": m["Linh"], "mode": "gross"})
    assert res["type"] == "payment_draft"
    assert res["from_member_id"] == m["Giang"] and res["to_member_id"] == m["Linh"] and res["amount"] == 61000


def test_settled_when_truly_zero(setup):
    d, room, m = setup
    with d.session() as s:
        ledger.record_payment(s, room_id=room, from_member_id=m["Dung"],
                              to_member_id=m["Linh"], amount=61000, occurred_on=date(2026, 7, 22))
    res = _tools(d, room, m["Dung"])["propose_payment"].execute({"to": m["Linh"]})
    assert res["type"] == "payment_settled"


def test_explicit_amount_never_netted(setup):
    d, room, m = setup
    res = _tools(d, room, m["Giang"])["propose_payment"].execute({"to": m["Linh"], "amount": 61000})
    assert res["type"] == "payment_draft" and res["amount"] == 61000


def test_mode_offset_flips_direction(setup):
    d, room, m = setup
    # Giang owes Linh 61k; Linh owes Giang 75k. Net: Linh pays Giang 14k.
    res = _tools(d, room, m["Giang"])["propose_payment"].execute({"to": m["Linh"], "mode": "offset"})
    assert res["type"] == "payment_draft"
    assert res["from_member_id"] == m["Linh"]
    assert res["to_member_id"] == m["Giang"]
    assert res["amount"] == 14000
