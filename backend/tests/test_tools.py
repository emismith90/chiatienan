from app import ledger
from app.db import Database
from app.models import Meal, Room, Member
from app.tools import ToolContext, build_tools


def _ctx():
    d = Database("sqlite://")
    d.create_all()
    with d.session() as s:
        r = Room(name="A", invite_token="t")
        s.add(r)
        s.flush()
        an = Member(room_id=r.id, display_name="An", nickname="an", pin="1")
        bi = Member(room_id=r.id, display_name="Bình", nickname="binh", pin="2")
        s.add_all([an, bi])
        s.flush()
        ids = (r.id, an.id, bi.id)
    return d, ids


def _other_room_member(d):
    with d.session() as s:
        r2 = Room(name="B", invite_token="t2")
        s.add(r2)
        s.flush()
        other = Member(room_id=r2.id, display_name="Khác", nickname="khac", pin="9")
        s.add(other)
        s.flush()
        return r2.id, other.id


def _seed_room(db, n, *, token="tok"):
    """Create a room with ``n`` members directly. Mirrors the helper in
    ``test_ledger.py``. Returns ``(room_id, [member_id, ...])``.
    """
    with db.session() as s:
        room = Room(name="Room", invite_token=token)
        s.add(room)
        s.flush()
        members = [
            Member(room_id=room.id, display_name=f"M{i}", nickname=f"m{i}", pin=str(i))
            for i in range(1, n + 1)
        ]
        s.add_all(members)
        s.flush()
        return room.id, [m.id for m in members]


def test_propose_meal_writes_nothing_and_previews(db):
    from app import ledger
    from app.models import Meal
    from app.tools import ToolContext, build_tools

    room_id, (a, b, c) = _seed_room(db, 3)   # reuse the helper pattern in this file
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    tools = build_tools(ctx)
    out = tools["propose_meal"].execute({
        "payer": a, "participants": [a, b, c], "total": 400_000, "guests": ["Emi"],
        "dish": "phở", "note": "test",
    })
    assert out["ok"] is True
    assert out["type"] == "expense_draft"
    assert out["member_participants"] == [a, b, c]
    assert out["guests"] == ["Emi"]
    assert out["bill_total"] == 400_000
    assert out["per_head_preview"] == 100_000
    assert out["dish"] == "phở"
    # nothing persisted
    with db.session() as s:
        assert s.query(Meal).count() == 0


def test_propose_meal_defaults_payer_to_sender(db):
    from app.tools import ToolContext, build_tools
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    out = build_tools(ctx)["propose_meal"].execute({"participants": [a, b], "total": 200_000})
    assert out["payer_member_id"] == a


def test_propose_meal_explicit_payer_overrides_sender(db):
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    out = build_tools(ctx)["propose_meal"].execute({"payer": b, "participants": [a, b], "total": 200_000})
    assert out["ok"] is True
    assert out["payer_member_id"] == b
    with db.session() as s:
        assert s.query(Meal).count() == 0


def test_propose_meal_no_payer_and_no_sender_errors(db):
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=None)
    out = build_tools(ctx)["propose_meal"].execute({"participants": [a, b], "total": 100_000})
    assert out["ok"] is False
    with db.session() as s:
        assert s.query(Meal).count() == 0


def test_propose_meal_invalid_participant_id_returns_error(db):
    """Fix 4: a non-int participant id must not raise uncaught out of the
    tool — it's a clarifying-question result like every other bad input."""
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    out = build_tools(ctx)["propose_meal"].execute({
        "participants": [a, "not-an-id"], "total": 100_000,
    })
    assert out["ok"] is False
    assert "error" in out
    with db.session() as s:
        assert s.query(Meal).count() == 0


def test_propose_meal_adjustments_round_trip(db):
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    out = build_tools(ctx)["propose_meal"].execute({
        "participants": [a, b], "total": 300_000, "adjustments": [{"member": b, "amount": 50_000}],
    })
    assert out["ok"] is True
    assert out["adjustments"] == [{"member": b, "amount": 50_000}]
    with db.session() as s:
        assert s.query(Meal).count() == 0


def _seed_meal(d, room_id, payer, participants, total, **kwargs):
    """Write a meal straight through ``ledger.record_meal`` (the deterministic
    commit path), bypassing the (now write-nothing) ``propose_meal`` tool, so
    void/balances/settle tests still have a meal to work against.
    """
    with d.session() as s:
        res = ledger.record_meal(
            s,
            room_id=room_id,
            payer_member_id=payer,
            participants=participants,
            total_amount=total,
            source="web",
            logged_by=str(payer),
            **kwargs,
        )
        return res["meal_id"]


def test_find_members_tool_matches_names_and_all_active():
    d, (room_id, an, bi) = _ctx()
    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an)
    tools = build_tools(ctx)

    out = tools["find_members"].execute({"names": ["An"]})
    assert out["ok"] is True
    assert {m["id"] for m in out["matched"]} == {an}
    assert out["unresolved"] == []

    out_all = tools["find_members"].execute({"all_active": True})
    assert {m["id"] for m in out_all["matched"]} == {an, bi}


def test_find_members_tool_does_not_see_other_rooms_members():
    d, (room_id, an, bi) = _ctx()
    _other_room_member(d)

    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an)
    tools = build_tools(ctx)
    out = tools["find_members"].execute({"all_active": True})
    assert {m["id"] for m in out["matched"]} == {an, bi}


def test_find_members_tool_schema_has_no_include_tagged():
    d, (room_id, an, bi) = _ctx()
    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an)
    tools = build_tools(ctx)
    assert "include_tagged" not in tools["find_members"].input_schema["properties"]


def test_void_meal_tool_marks_voided_with_sender_as_by():
    d, (room_id, an, bi) = _ctx()
    meal_id = _seed_meal(d, room_id, an, [an, bi], 100000)

    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)
    out = tools["void_meal"].execute({"meal_id": meal_id})
    assert out["ok"] is True and out["voided"] is True
    with d.session() as s:
        meal = s.get(Meal, meal_id)
        assert meal.voided is True
        assert meal.voided_by == str(an)


def test_void_meal_tool_cannot_void_another_rooms_meal():
    d, (room_id, an, bi) = _ctx()
    other_room_id, other_id = _other_room_member(d)
    meal_id = _seed_meal(d, room_id, an, [an, bi], 100000)

    other_ctx = ToolContext(db=d, room_id=other_room_id, sender_member_id=other_id)
    other_tools = build_tools(other_ctx)
    out = other_tools["void_meal"].execute({"meal_id": meal_id})
    assert out["ok"] is False and "error" in out


def test_no_tool_hands_the_agent_a_net_balance():
    """`get_period_balances` returned paid/consumed/balance per person. While it
    existed the agent could fetch a net figure and narrate it, whatever the
    prompt said — so it is gone, not merely unused."""
    d, (room_id, an, _bi) = _ctx()
    tools = build_tools(ToolContext(db=d, room_id=room_id, sender_member_id=an))
    assert "get_period_balances" not in tools


def test_group_summary_outstanding_is_scoped_to_room():
    d, (room_id, an, bi) = _ctx()
    other_room_id, other_id = _other_room_member(d)
    _seed_meal(d, room_id, an, [an, bi], 100000)

    other_ctx = ToolContext(db=d, room_id=other_room_id, sender_member_id=other_id)
    other_tools = build_tools(other_ctx)
    out = other_tools["get_period_summary"].execute({})
    assert out["ok"] is True
    assert out["outstanding"] == []


def test_add_member_tool_creates_unclaimed_member_and_rejects_duplicate_nickname():
    d, (room_id, an, bi) = _ctx()
    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)

    out = tools["add_member"].execute({"display_name": "Chi", "nickname": "chi"})
    assert out["ok"] is True
    assert out["nickname"] == "chi"
    with d.session() as s:
        m = s.get(Member, out["member_id"])
        assert m.room_id == room_id
        assert m.pin is None
        assert m.display_name == "Chi"

    dup = tools["add_member"].execute({"display_name": "Chi2", "nickname": "chi"})
    assert dup["ok"] is False and "error" in dup


def test_update_member_tool_edits_renames_and_handles_errors(db):
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    tools = build_tools(ctx)

    ok = tools["update_member"].execute({"target": "m2", "display_name": "Bob", "nickname": "bob"})
    assert ok["ok"] is True and ok["nickname"] == "bob" and ok["display_name"] == "Bob"
    with db.session() as s:
        assert s.get(Member, b).display_name == "Bob"

    # rename onto an existing nickname is rejected
    clash = tools["update_member"].execute({"target": "bob", "nickname": "m1"})
    assert clash["ok"] is False and "error" in clash
    # unknown target
    missing = tools["update_member"].execute({"target": "nobody"})
    assert missing["ok"] is False and "error" in missing


def test_delete_member_tool_soft_deletes_out_of_roster_but_reversible(db):
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    tools = build_tools(ctx)

    out = tools["delete_member"].execute({"target": b})
    assert out["ok"] is True and out["member_id"] == b
    with db.session() as s:
        assert s.get(Member, b).active is False
    # removed from selection/roster
    assert {m["id"] for m in tools["find_members"].execute({"all_active": True})["matched"]} == {a}
    # restore via update_member(active=True)
    tools["update_member"].execute({"target": b, "active": True})
    assert {m["id"] for m in tools["find_members"].execute({"all_active": True})["matched"]} == {a, b}


def test_settle_still_names_a_deleted_member(db):
    room_id, (a, b) = _seed_room(db, 2)
    _seed_meal(db, room_id, a, [a, b], 100_000)  # b owes a 50k
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    tools = build_tools(ctx)

    tools["delete_member"].execute({"target": b})  # b removed after incurring a debt
    out = tools["settle_period"].execute({"keyword": "since_last"})
    assert out["ok"] is True
    names = {row["from_name"] for row in out["transfers"]} | {row["to_name"] for row in out["transfers"]}
    assert "?" not in names            # deleted member's name still resolves
    assert "M2" in names               # b's display name is present



def test_settle_period_is_read_only():
    """A running total, not a closing entry. The tool used to accept commit:true
    and write a Settlement; nobody ever passed it, and the first time anyone had,
    `since_last` would have flipped from "the whole ledger" to a bounded window
    for the ledger panel, quick-pay and every card's balances at once.

    `ledger.record_settlement` is deliberately kept (and still tested directly)
    for whenever closing a period becomes a real feature.
    """
    d, (room_id, an, bi) = _ctx()
    _seed_meal(d, room_id, an, [an, bi], 100000)

    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)
    out = tools["settle_period"].execute({"keyword": "since_last"})
    assert out["ok"] is True
    assert "committed" not in out
    # Even asked to close, it cannot: the flag is gone from the schema.
    tools["settle_period"].execute({"keyword": "since_last", "commit": True})

    with d.session() as s:
        assert ledger.last_settlement(s, room_id) is None


def test_settle_period_blocks_when_pending_draft_exists(db):
    from datetime import date
    from app.tools import build_tools, ToolContext
    from app import drafts, ledger
    room_id, m = _seed_room(db, 3)
    with db.session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=m[0],
                           participants=m, total_amount=300, occurred_on=date(2026, 7, 20))
        drafts.create_draft(s, room_id, {
            "payer_member_id": m[0], "member_participants": m, "guests": [],
            "bill_total": 90, "adjustments": [], "per_head_preview": 30, "raw_input": "x"})
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=m[0])
    res = build_tools(ctx)["settle_period"].execute({"keyword": "since_last"})
    assert res["type"] == "settle_blocked"
    assert len(res["pending"]) == 1
    assert "transfers" not in res


def test_settle_period_runs_when_no_pending(db):
    from datetime import date
    from app.tools import build_tools, ToolContext
    from app import ledger
    room_id, m = _seed_room(db, 3)
    with db.session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=m[0],
                           participants=m, total_amount=300, occurred_on=date(2026, 7, 20))
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=m[0])
    res = build_tools(ctx)["settle_period"].execute({"keyword": "since_last"})
    assert res.get("type") != "settle_blocked"
    assert "transfers" in res


def test_settle_period_attributes_debt_to_the_actual_payer(db):
    # Regression for the prod bug: two meals with overlapping-but-different
    # rosters. Each debtor must repay the member who fronted the meal they ate,
    # NOT a globally-minimised creditor.
    #   m0=Emi m1=Trang m2=Linh m3=Dung m4=TrangDinh m5=Giang
    from datetime import date
    from app.tools import build_tools, ToolContext
    from app import ledger
    room_id, m = _seed_room(db, 6)
    with db.session() as s:
        # meal #2 — Linh paid 305k, no Trang
        ledger.record_meal(s, room_id=room_id, payer_member_id=m[2],
                           participants=[m[0], m[2], m[3], m[4], m[5]],
                           total_amount=305000, occurred_on=date(2026, 7, 22))
        # meal #3 — Giang paid 375k, no Dung
        ledger.record_meal(s, room_id=room_id, payer_member_id=m[5],
                           participants=[m[0], m[1], m[2], m[4], m[5]],
                           total_amount=375000, occurred_on=date(2026, 7, 22))
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=m[0])
    res = build_tools(ctx)["settle_period"].execute({"keyword": "since_last"})
    got = {(t["from_id"], t["to_id"], t["amount"]) for t in res["transfers"]}
    assert got == {
        (m[0], m[2], 61000),   # Emi -> Linh
        (m[0], m[5], 75000),   # Emi -> Giang
        (m[4], m[2], 61000),   # TrangDinh -> Linh
        (m[4], m[5], 75000),   # TrangDinh -> Giang
        (m[1], m[5], 75000),   # Trang -> Giang
        (m[3], m[2], 61000),   # Dung -> Linh (whole 61k)
        (m[2], m[5], 14000),   # Linh -> Giang (net of the two meals)
    }


def test_settle_note_names_the_meals_per_transfer(db):
    # Each QR note lists the debtor + the meals the payee fronted that the debtor
    # shared in, in date order, ASCII-folded to Vietnamese weekday labels.
    from datetime import date
    from app.tools import build_tools, ToolContext
    from app import ledger
    room_id, m = _seed_room(db, 2, token="notes")
    with db.session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=m[1],
                           participants=[m[0], m[1]], total_amount=200000,
                           occurred_on=date(2024, 1, 1), dish="bún chả")  # Monday
        ledger.record_meal(s, room_id=room_id, payer_member_id=m[1],
                           participants=[m[0], m[1]], total_amount=100000,
                           occurred_on=date(2024, 1, 2), dish="nem")       # Tuesday
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=m[0])
    res = build_tools(ctx)["settle_period"].execute({"keyword": "since_last"})
    notes = {(t["from_id"], t["to_id"]): t["note"] for t in res["transfers"]}
    assert notes[(m[0], m[1])] == "M1: T2 bun cha, T3 nem"


def test_propose_meal_resolves_day_word_server_side(db, monkeypatch):
    # The tool — not the model — computes the date, so it can never land a day off.
    from datetime import date
    monkeypatch.setattr("app.tools.today_ict", lambda: date(2026, 7, 25))  # Saturday
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    out = build_tools(ctx)["propose_meal"].execute({
        "participants": [a, b], "total": 200_000, "day_word": "thursday",
    })
    assert out["ok"] is True
    assert out["occurred_on"] == "2026-07-23"  # the Thursday before Sat 07-25


def test_propose_meal_day_word_overrides_hand_supplied_occurred_on(db, monkeypatch):
    # A model-computed occurred_on must never win over the resolved day word.
    from datetime import date
    monkeypatch.setattr("app.tools.today_ict", lambda: date(2026, 7, 25))
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a)
    out = build_tools(ctx)["propose_meal"].execute({
        "participants": [a, b], "total": 200_000,
        "day_word": "friday", "occurred_on": "2026-07-22",  # bogus (Wednesday)
    })
    assert out["occurred_on"] == "2026-07-24"  # Friday wins, not the stale Wed


def test_propose_meal_invalid_day_word_errors(db):
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a)
    out = build_tools(ctx)["propose_meal"].execute({
        "participants": [a, b], "total": 200_000, "day_word": "someday",
    })
    assert "error" in out


def test_settle_note_weekdays_match_the_day_words(db, monkeypatch):
    # Regression: a Thursday + Friday meal logged by day word must produce a QR
    # note reading T5/T6 — not the off-by-one T4/T5 from LLM-computed dates.
    from datetime import date
    monkeypatch.setattr("app.tools.today_ict", lambda: date(2026, 7, 25))  # Saturday
    room_id, (a, b) = _seed_room(db, 2, token="daywords")
    tools = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1"))
    for day, dish, total in [("thursday", "pho", 200_000), ("friday", "bun", 100_000)]:
        d = tools["propose_meal"].execute({
            "payer": b, "participants": [a, b], "total": total,
            "dish": dish, "day_word": day,
        })
        with db.session() as s:
            ledger.record_meal(
                s, room_id=room_id, payer_member_id=b, participants=[a, b],
                total_amount=total, dish=dish,
                occurred_on=date.fromisoformat(d["occurred_on"]),
            )
    res = tools["settle_period"].execute({"keyword": "since_last"})
    note = {(t["from_id"], t["to_id"]): t["note"] for t in res["transfers"]}[(a, b)]
    assert "T5 pho" in note and "T6 bun" in note
    assert "T4" not in note


# --- cancel_draft ----------------------------------------------------------- #

def test_cancel_draft_closes_a_pending_card_without_writing(db):
    """Production: four people asked the bot to close a stale proposal and it had
    no tool to do it, so it repeated the same blocked message instead."""
    from app import drafts
    from app.models import Meal, RoomMessage

    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d, _ = drafts.create_draft(s, room_id, {
            "payer_member_id": a, "member_participants": [a, b, c], "guests": [],
            "bill_total": 324_000, "adjustments": [], "dish": None, "initiator": None,
            "note": None, "per_head_preview": 108_000, "raw_input": "@bot test",
        })
        draft_id = d.id

    tools = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=a,
                                   sender_name="Emi", turn_mentions=[]))
    out = tools["cancel_draft"].execute({"draft_id": draft_id})

    assert out["ok"] and out["draft_id"] == draft_id
    with db.session() as s:
        assert s.get(RoomMessage, draft_id).attachments["status"] == "cancelled"
        assert drafts.list_pending_drafts(s, room_id) == []
        assert s.query(Meal).count() == 0


def test_cancel_draft_rejects_an_already_processed_card(db):
    from app import drafts

    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d, _ = drafts.create_draft(s, room_id, {
            "payer_member_id": a, "member_participants": [a, b, c], "guests": [],
            "bill_total": 300_000, "adjustments": [], "dish": None, "initiator": None,
            "note": None, "per_head_preview": 100_000, "raw_input": "@bot test",
        })
        drafts.commit_draft(s, d.id, room_id, logged_by=str(a))
        draft_id = d.id

    tools = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=a,
                                   sender_name="Emi", turn_mentions=[]))
    out = tools["cancel_draft"].execute({"draft_id": draft_id})
    assert not out.get("ok")


def test_cancel_draft_cannot_reach_another_room(db):
    from app import drafts

    room_a, (a1, a2, a3) = _seed_room(db, 3)
    room_b, (b1, _b2, _b3) = _seed_room(db, 3, token="tok-b")
    with db.session() as s:
        d, _ = drafts.create_draft(s, room_a, {
            "payer_member_id": a1, "member_participants": [a1, a2, a3], "guests": [],
            "bill_total": 300_000, "adjustments": [], "dish": None, "initiator": None,
            "note": None, "per_head_preview": 100_000, "raw_input": "@bot test",
        })
        draft_id = d.id

    tools = build_tools(ToolContext(db=db, room_id=room_b, sender_member_id=b1,
                                   sender_name="Kun", turn_mentions=[]))
    out = tools["cancel_draft"].execute({"draft_id": draft_id})
    assert not out.get("ok")
    with db.session() as s:
        from app.models import RoomMessage
        assert s.get(RoomMessage, draft_id).attachments["status"] == "pending"


def test_settle_says_balanced_based_on_transfers_not_stale_balances(db):
    """The gate read period_balances, which could disagree with the transfers on a
    bounded window — so the room got an empty transfer list with no explanation,
    or "đã cân bằng" while transfers were pending."""
    from datetime import date

    from app import ledger
    from app.models import Meal
    from tests.test_ledger import _seed_room

    room_id, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        meal = ledger.record_meal(
            s, room_id=room_id, payer_member_id=b, participants=[a, b],
            total_amount=80_000, adjustments={}, guests=[], dish="bún chả",
            occurred_on=date(2026, 7, 23), logged_by=str(b),
        )
        ledger.record_payment(s, room_id=room_id, from_member_id=a, to_member_id=b,
                              amount=meal["shares"][a], occurred_on=date(2026, 7, 27),
                              logged_by=str(a))
        assert s.get(Meal, meal["meal_id"]) is not None

    tools = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=a,
                                   sender_name="An", turn_mentions=[]))
    out = tools["settle_period"].execute({"keyword": "explicit",
                                          "from": "2026-07-20", "to": "2026-07-26"})
    assert out["ok"]
    assert out["transfers"] == []
    assert "cân bằng" in out["message"]


def test_settle_honours_from_to_without_the_explicit_keyword(db):
    from datetime import date

    from app import ledger
    from tests.test_ledger import _seed_room

    room_id, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        ledger.record_meal(
            s, room_id=room_id, payer_member_id=b, participants=[a, b],
            total_amount=80_000, adjustments={}, guests=[], dish="bún chả",
            occurred_on=date(2026, 7, 23), logged_by=str(b),
        )

    tools = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=a,
                                   sender_name="An", turn_mentions=[]))
    out = tools["settle_period"].execute({"from": "2026-07-01", "to": "2026-07-02"})
    assert out["period"] == {"from": "2026-07-01", "to": "2026-07-02"}
    assert out["transfers"] == []   # the meal is outside the asked-for range
