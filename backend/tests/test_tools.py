from datetime import date

from app import ledger, roster
from app.tools import ToolContext, build_tools


def _member(s, name, **kw):
    kw.setdefault("bank_code", "VCB")
    kw.setdefault("account_number", "001")
    kw.setdefault("account_holder", name)
    return roster.create_member(s, display_name=name, **kw)


def test_find_members_all_active(db):
    with db.session() as s:
        _member(s, "An")
        _member(s, "Bình")
    tools = build_tools(ToolContext(db=db))
    res = tools["find_members"].execute({"all_active": True}, None)
    assert res["ok"] is True
    assert {m["display_name"] for m in res["matched"]} == {"An", "Bình"}


def test_record_meal_happy_path(db):
    with db.session() as s:
        a = _member(s, "An")
        b = _member(s, "Bình")
        ids = (a.id, b.id)
    tools = build_tools(ToolContext(db=db, sender_teams_id="29:an"))
    res = tools["record_meal"].execute(
        {"payer": ids[0], "participants": list(ids), "total": 200}, None
    )
    assert res["ok"] is True
    assert res["total_amount"] == 200
    assert sum(sh["amount"] for sh in res["shares"]) == 200


def test_record_meal_error_is_returned_not_raised(db):
    with db.session() as s:
        a = _member(s, "An")
        ids = [a.id]
    tools = build_tools(ToolContext(db=db))
    res = tools["record_meal"].execute({"payer": ids[0], "participants": ids, "total": 0}, None)
    assert res["ok"] is False
    assert "error" in res


def test_record_meal_defaults_payer_to_sender(db):
    # sender is captured as a member and used as payer when payer omitted
    with db.session() as s:
        b = _member(s, "Bình")
        bid = b.id
    tools = build_tools(ToolContext(db=db, sender_teams_id="29:an", sender_name="An"))
    res = tools["record_meal"].execute({"participants": [bid], "total": 100}, None)
    assert res["ok"] is True
    assert res["payer"]["name"] == "An"  # captured sender


def test_settle_period_builds_qr_and_transfers(db):
    with db.session() as s:
        a = _member(s, "An")
        b = _member(s, "Bình")
        # An pays 200 for both → Bình owes An 100
        ledger.record_meal(
            s, payer_member_id=a.id, participants=[a.id, b.id], total_amount=200,
            occurred_on=date.today(),
        )
    tools = build_tools(ToolContext(db=db, sender_teams_id="29:an"))
    res = tools["settle_period"].execute({"keyword": "since_last", "commit": False}, None)
    assert res["ok"] is True
    assert len(res["transfers"]) == 1
    t = res["transfers"][0]
    assert t["from_name"] == "Bình" and t["to_name"] == "An"
    assert t["amount"] == 100
    assert t["qr_url"].startswith("https://img.vietqr.io/image/")
    assert res["committed"] is False


def test_settle_period_commit_closes_period(db):
    with db.session() as s:
        a = _member(s, "An")
        b = _member(s, "Bình")
        ledger.record_meal(
            s, payer_member_id=a.id, participants=[a.id, b.id], total_amount=200,
            occurred_on=date.today(),
        )
    tools = build_tools(ToolContext(db=db, sender_teams_id="29:an"))
    res = tools["settle_period"].execute({"keyword": "since_last", "commit": True}, None)
    assert res["committed"] is True
    with db.session() as s:
        assert ledger.last_settlement(s) is not None


def test_settle_period_nothing_to_settle(db):
    with db.session() as s:
        _member(s, "An")
    tools = build_tools(ToolContext(db=db))
    res = tools["settle_period"].execute({"keyword": "since_last"}, None)
    assert res["ok"] is True
    assert res["transfers"] == []


def test_settle_period_warns_when_payee_missing_bank(db):
    with db.session() as s:
        # An (creditor) has no bank details → QR can't be built, surfaces a warning
        a = roster.create_member(s, display_name="An")
        b = _member(s, "Bình")
        ledger.record_meal(
            s, payer_member_id=a.id, participants=[a.id, b.id], total_amount=200,
            occurred_on=date.today(),
        )
    tools = build_tools(ToolContext(db=db))
    res = tools["settle_period"].execute({"keyword": "since_last"}, None)
    assert res["transfers"][0]["qr_url"] is None
    assert res["warnings"]


def test_resolve_period_tool(db):
    tools = build_tools(ToolContext(db=db))
    res = tools["resolve_period"].execute({"keyword": "this_week"}, None)
    assert res["ok"] is True
    assert res["keyword"] == "this_week"
    assert res["from"] and res["to"]
