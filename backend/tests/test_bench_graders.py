"""The benchmark graders.

Every test here is offline: no judge is constructed, no model is called. A
grader that cannot fail certifies nothing, so these lean on the cases where a
lenient grader would wave a real regression through.
"""
from bench.corpus import Case

MONEY_ARGS = ("total", "payer", "participants", "from", "to", "amount", "items")


def _case(expect=None, message="@bot ghi bữa trưa", **kw):
    return Case(id=kw.pop("id", "C1"), source=kw.pop("source", "meals"),
                day=kw.pop("day", "2026-07-20"), actor=kw.pop("actor", "m1"),
                message=message, expect=expect if expect is not None else {}, **kw)


def _record(tools=(), final_text="Đã ghi.", error=None, **kw):
    """A runner record. `tools` entries are (name, args) or (name, args, result)."""
    calls = []
    for entry in tools:
        name, args, *rest = entry
        calls.append({"name": name, "args": args, "result": rest[0] if rest else {"ok": True}})
    return {"case_id": "C1", "rep": 0, "tools": calls, "final_text": final_text,
            "error": error, "elapsed_s": kw.pop("elapsed_s", 1.0),
            "stats": kw.pop("stats", None), **kw}


# --------------------------------------------------------------------------- #
# tool_selection
# --------------------------------------------------------------------------- #

def test_extra_scaffolding_tools_do_not_fail_a_case():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"]})
    rec = _record(tools=[("find_members", {}), ("propose_meal", {"total": 300000})])
    assert grade_tool_selection(case, rec).passed


def test_a_missing_expected_tool_fails_and_the_reason_names_it():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"]})
    v = grade_tool_selection(case, _record(tools=[("find_members", {})]))
    assert not v.passed and "propose_meal" in v.reason


def test_wrong_money_arg_fails_even_when_the_tool_is_right():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"], "args": {"propose_meal": {"total": 300000}}})
    rec = _record(tools=[("propose_meal", {"total": 30000})])
    v = grade_tool_selection(case, rec)
    assert not v.passed and "total" in v.reason


def test_participants_compare_as_a_set():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"],
                         "args": {"propose_meal": {"participants": [1, 2, 3]}}})
    rec = _record(tools=[("propose_meal", {"participants": [3, 1, 2]})])
    assert grade_tool_selection(case, rec).passed


def test_a_missing_participant_still_fails():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"],
                         "args": {"propose_meal": {"participants": [1, 2, 3]}}})
    v = grade_tool_selection(case, _record(tools=[("propose_meal", {"participants": [1, 2]})]))
    assert not v.passed and "participants" in v.reason


def test_non_money_args_are_ignored():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"], "args": {"propose_meal": {"total": 300000}}})
    rec = _record(tools=[("propose_meal", {"total": 300000, "dish": "bún bò", "note": "x"})])
    assert grade_tool_selection(case, rec).passed


def test_items_compare_ignoring_order_and_label():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"], "args": {"propose_meal": {
        "items": [{"member": 1, "amount": 60000}, {"member": 2, "amount": 40000}]}}})
    rec = _record(tools=[("propose_meal", {"items": [
        {"member": 2, "amount": 40000, "label": "cơm tấm"},
        {"member": 1, "amount": 60000, "label": "bún bò"}]})])
    assert grade_tool_selection(case, rec).passed


def test_an_item_amount_that_differs_fails():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"], "args": {"propose_meal": {
        "items": [{"member": 1, "amount": 60000}]}}})
    v = grade_tool_selection(case, _record(tools=[("propose_meal", {"items": [
        {"member": 1, "amount": 6000}]})]))
    assert not v.passed and "items" in v.reason


def test_a_dropped_guest_fails_because_it_overcharges_every_member():
    from bench.graders import grade_tool_selection
    # Golden G6: a 400k bill with a 300k tracked total because one guest pays
    # cash. Lose the guest and the bill divides by too few heads.
    case = _case(expect={"tools": ["propose_meal"],
                         "args": {"propose_meal": {"total": 400000, "guests": ["Emi"]}}})
    v = grade_tool_selection(case, _record(tools=[("propose_meal", {"total": 400000})]))
    assert not v.passed and "guests" in v.reason


def test_a_guest_named_differently_still_passes():
    from bench.graders import grade_tool_selection
    # The count is the arithmetic; the name is the model reading prose.
    case = _case(expect={"tools": ["propose_meal"],
                         "args": {"propose_meal": {"guests": ["Emi"]}}})
    rec = _record(tools=[("propose_meal", {"guests": ["emi"]})])
    assert grade_tool_selection(case, rec).passed


def test_a_missing_money_arg_fails_rather_than_being_skipped():
    from bench.graders import grade_tool_selection
    # An omitted `total` is not a pass just because there is nothing to compare.
    case = _case(expect={"tools": ["propose_meal"], "args": {"propose_meal": {"total": 300000}}})
    v = grade_tool_selection(case, _record(tools=[("propose_meal", {"participants": [1]})]))
    assert not v.passed and "total" in v.reason


def test_the_last_call_to_a_tool_is_the_one_graded():
    from bench.graders import grade_tool_selection
    # A model that corrects itself is judged on what the user ends up seeing,
    # matching TurnResult.last_result().
    case = _case(expect={"tools": ["propose_meal"], "args": {"propose_meal": {"total": 300000}}})
    rec = _record(tools=[("propose_meal", {"total": 30000}), ("propose_meal", {"total": 300000})])
    assert grade_tool_selection(case, rec).passed


def test_a_case_with_no_tool_expectation_is_ungraded_not_passed():
    from bench.graders import grade_tool_selection
    # Reported as n/a. A vacuous pass would let a corpus of expectation-less
    # cases read as equivalence.
    v = grade_tool_selection(_case(expect={}), _record(tools=[("propose_meal", {})]))
    assert v.passed is None and v.reason


def test_a_turn_that_errored_fails_tool_selection():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"]})
    v = grade_tool_selection(case, _record(tools=[], error="bridge died"))
    assert not v.passed and "bridge died" in v.reason


# --------------------------------------------------------------------------- #
# ledger_state
# --------------------------------------------------------------------------- #
#
# The trap this grader has to avoid: an LLM turn for a meal or a payment step
# produces a *draft* and writes nothing (`propose_meal` "never writes at all";
# `propose_payment` returns a `payment_draft`). So the post-turn ledger is byte
# for byte the pre-turn ledger, and comparing database balances against a step's
# expectation would fail on every meal and payment case — identically on both
# engines, which `--compare` would report as "no change". Instead the draft's own
# numbers are projected onto the world `build_world` built, and the projection is
# what gets compared.

from datetime import date, datetime, time  # noqa: E402

from app import drafts, ledger, tools  # noqa: E402
from app.clock import ICT  # noqa: E402
from app.models import Member, Room  # noqa: E402
from tests.golden.scenario_week import MEMBERS, STEPS  # noqa: E402

_STEP = {s["id"]: s for s in STEPS}


def _freeze(monkeypatch, day_iso):
    frozen = datetime.combine(date.fromisoformat(day_iso), time(12, 0), tzinfo=ICT)
    monkeypatch.setattr("app.clock.now_ict", lambda: frozen)


def _week_room(db):
    """The scenario room, banks and all — seeded the way `build_world` will."""
    ids = {}
    with db.session() as s:
        room = Room(name="Week", invite_token="grader-tok")
        s.add(room); s.flush()
        for spec in MEMBERS:
            m = Member(room_id=room.id, display_name=spec["display_name"],
                       nickname=spec["nickname"], pin="1", **(spec.get("bank") or {}))
            s.add(m); s.flush()
            ids[spec["key"]] = m.id
        return room.id, ids


def _settle(db, room_id, actor_id):
    ctx = tools.ToolContext(db=db, room_id=room_id, sender_member_id=actor_id)
    return tools.build_tools(ctx)["settle_period"].execute({"keyword": "since_last"})


def _meals_room(db):
    """The four-member room the golden meal cases assume (An/Bình/Cường/Dung)."""
    from bench.corpus import MEALS_MEMBERS
    ids = {}
    with db.session() as s:
        room = Room(name="Meals", invite_token="grader-meals")
        s.add(room); s.flush()
        for spec in MEALS_MEMBERS:
            m = Member(room_id=room.id, display_name=spec["display_name"],
                       nickname=spec["nickname"], pin="1")
            s.add(m); s.flush()
            ids[spec["key"]] = m.id
        return room.id, ids


def _propose(db, room_id, ids, spec):
    """Run the real `propose_meal` for a step/case spec. Writes nothing."""
    ctx = tools.ToolContext(db=db, room_id=room_id, sender_member_id=ids[spec["payer"]])
    return tools.build_tools(ctx)["propose_meal"].execute({
        "payer": ids[spec["payer"]],
        "participants": [ids[p] for p in spec["participants"]],
        "total": spec["total"], "guests": list(spec.get("guests") or [])})


def _play(db, room_id, ids, step_ids, monkeypatch):
    """Deterministically commit the named steps, as `bench.world` will."""
    for sid in step_ids:
        step = _STEP[sid]
        _freeze(monkeypatch, step["day"])
        if step["kind"] == "add_member":
            with db.session() as s:
                m = Member(room_id=room_id, display_name=step["new_member"].upper(),
                           nickname=step["new_member"], pin="1")
                s.add(m); s.flush()
                ids[step["new_member"]] = m.id
        elif step["kind"] in ("meal_confirmed", "leave_pending"):
            with db.session() as s:
                d, _ = drafts.create_draft(s, room_id, {
                    "payer_member_id": ids[step["payer"]],
                    "member_participants": [ids[p] for p in step["participants"]],
                    "guests": step.get("guests", []), "bill_total": step["total"],
                    "adjustments": [], "per_head_preview": 0, "raw_input": "seed"})
                if step["kind"] == "meal_confirmed":
                    drafts.commit_draft(s, d.id, room_id, logged_by="1")
        elif step["kind"] == "payment":
            with db.session() as s:
                ledger.record_payment(s, room_id=room_id, from_member_id=ids[step["from"]],
                                      to_member_id=ids[step["to"]], amount=step["amount"],
                                      logged_by="1")


def test_compare_settlement_accepts_the_real_s5_expectation(db, monkeypatch):
    from bench.graders import compare_settlement
    room_id, ids = _week_room(db)
    _play(db, room_id, ids, ["s1", "s2", "s3", "s4"], monkeypatch)
    _freeze(monkeypatch, _STEP["s5"]["day"])
    res = _settle(db, room_id, ids["a3"])
    assert compare_settlement(res, _STEP["s5"]["expect"], ids) == []


def test_compare_settlement_rejects_transfers_in_the_wrong_order(db, monkeypatch):
    from bench.graders import compare_settlement
    room_id, ids = _week_room(db)
    _play(db, room_id, ids, ["s1", "s2", "s3", "s4"], monkeypatch)
    _freeze(monkeypatch, _STEP["s5"]["day"])
    res = _settle(db, room_id, ids["a3"])
    shuffled = dict(_STEP["s5"]["expect"])
    shuffled["transfers"] = list(reversed(shuffled["transfers"]))
    problems = compare_settlement(res, shuffled, ids)
    assert problems and any("transfers" in p for p in problems)


def test_compare_settlement_rejects_a_missing_qr_url(db, monkeypatch):
    from bench.graders import compare_settlement
    room_id, ids = _week_room(db)
    _play(db, room_id, ids, ["s1", "s2", "s3", "s4"], monkeypatch)
    _freeze(monkeypatch, _STEP["s5"]["day"])
    res = _settle(db, room_id, ids["a3"])
    for t in res["transfers"]:
        t["qr_url"] = None
    problems = compare_settlement(res, _STEP["s5"]["expect"], ids)
    assert problems and any("qr" in p for p in problems)


def test_compare_settlement_rejects_an_amount_the_body_does_not_render(db, monkeypatch):
    from bench.graders import compare_settlement
    room_id, ids = _week_room(db)
    _play(db, room_id, ids, ["s1", "s2", "s3", "s4"], monkeypatch)
    _freeze(monkeypatch, _STEP["s5"]["day"])
    res = _settle(db, room_id, ids["a3"])
    # An amount present in the transfer list must also reach the rendered card.
    res["transfers"] = [dict(t, amount=t["amount"]) for t in res["transfers"]]
    expect = dict(_STEP["s5"]["expect"])
    expect["transfers"] = [dict(t) for t in expect["transfers"]]
    expect["transfers"][0]["amount"] = 999_999
    problems = compare_settlement(res, expect, ids)
    assert problems


def test_compare_settlement_handles_the_blocked_pending_branch(db, monkeypatch):
    from bench.graders import compare_settlement
    room_id, ids = _week_room(db)
    _play(db, room_id, ids, ["s1", "s2", "s3", "s4", "s6", "s7"], monkeypatch)
    _freeze(monkeypatch, _STEP["s8"]["day"])
    res = _settle(db, room_id, ids["a1"])
    assert res["type"] == "settle_blocked"
    assert compare_settlement(res, {"blocked_pending": 1}, ids) == []
    assert compare_settlement(res, {"blocked_pending": 2}, ids) != []


def test_a_settlement_that_should_be_blocked_but_is_not_fails(db, monkeypatch):
    from bench.graders import compare_settlement
    room_id, ids = _week_room(db)
    _play(db, room_id, ids, ["s1"], monkeypatch)
    _freeze(monkeypatch, _STEP["s8"]["day"])
    res = _settle(db, room_id, ids["a1"])
    assert res.get("type") != "settle_blocked"
    assert compare_settlement(res, {"blocked_pending": 1}, ids) != []


def test_compare_settlement_accepts_an_empty_ledger(db, monkeypatch):
    from bench.graders import compare_settlement
    room_id, ids = _week_room(db)
    _freeze(monkeypatch, _STEP["s12"]["day"])
    res = _settle(db, room_id, ids["a1"])
    assert compare_settlement(res, {"empty": True}, ids) == []


def test_a_non_empty_ledger_fails_an_empty_expectation(db, monkeypatch):
    from bench.graders import compare_settlement
    room_id, ids = _week_room(db)
    _play(db, room_id, ids, ["s1"], monkeypatch)
    _freeze(monkeypatch, _STEP["s12"]["day"])
    res = _settle(db, room_id, ids["a1"])
    assert compare_settlement(res, {"empty": True}, ids) != []


def test_balances_by_member_reads_the_open_period(db, monkeypatch):
    from bench.graders import balances_by_member
    room_id, ids = _week_room(db)
    _play(db, room_id, ids, ["s1", "s2", "s3"], monkeypatch)
    _freeze(monkeypatch, _STEP["s3"]["day"])
    bal = balances_by_member(db, room_id)
    assert bal[ids["a1"]] == 200_000 and bal[ids["a2"]] == 0


# --- the draft projection ---------------------------------------------------- #

def test_a_meal_draft_projects_onto_the_prior_ledger(db, monkeypatch):
    from bench.graders import grade_ledger_state
    from bench.corpus import load
    # s4: a 500k bill over four members plus one cash guest, so the payer is
    # credited the 400k TRACKED total, not the 500k bill.
    room_id, ids = _week_room(db)
    _play(db, room_id, ids, ["s1", "s2", "s3"], monkeypatch)
    _freeze(monkeypatch, _STEP["s4"]["day"])
    res = _propose(db, room_id, ids, _STEP["s4"])
    case = next(c for c in load("week") if c.id == "s4")
    rec = _record(tools=[("propose_meal", {}, res)], room_id=room_id)
    v = grade_ledger_state(case, rec, db, ids)
    assert v.passed, v.reason


def test_a_meal_draft_with_the_wrong_total_fails_the_projection(db, monkeypatch):
    from bench.graders import grade_ledger_state
    from bench.corpus import load
    room_id, ids = _week_room(db)
    _play(db, room_id, ids, ["s1", "s2", "s3"], monkeypatch)
    _freeze(monkeypatch, _STEP["s4"]["day"])
    step = dict(_STEP["s4"], total=50_000)      # the classic 500k → 50k slip
    res = _propose(db, room_id, ids, step)
    case = next(c for c in load("week") if c.id == "s4")
    rec = _record(tools=[("propose_meal", {}, res)], room_id=room_id)
    v = grade_ledger_state(case, rec, db, ids)
    assert not v.passed and "balances" in v.reason


def test_a_payment_draft_projects_onto_the_prior_ledger(db, monkeypatch):
    from bench.graders import grade_ledger_state
    from bench.corpus import load
    room_id, ids = _week_room(db)
    _play(db, room_id, ids, ["s1", "s2"], monkeypatch)
    _freeze(monkeypatch, _STEP["s3"]["day"])
    ctx = tools.ToolContext(db=db, room_id=room_id, sender_member_id=ids["a1"])
    res = tools.build_tools(ctx)["propose_payment"].execute(
        {"from": ids["a2"], "to": ids["a1"], "amount": 125_000})
    assert res["type"] == "payment_draft"       # writes nothing
    case = next(c for c in load("week") if c.id == "s3")
    rec = _record(tools=[("propose_payment", {}, res)], room_id=room_id)
    assert grade_ledger_state(case, rec, db, ids).passed


def test_a_golden_meal_draft_is_graded_on_shares_and_tracked(db, monkeypatch):
    from bench.graders import grade_ledger_state
    from bench.corpus import load
    room_id, ids = _meals_room(db)
    _freeze(monkeypatch, "2026-07-20")
    g6 = next(c for c in load("meals") if c.id == "G6")   # 400k bill, one guest
    res = _propose(db, room_id, ids, {"payer": "m1", "participants": ["m1", "m2", "m3"],
                                      "total": 400_000, "guests": ["Emi"]})
    rec = _record(tools=[("propose_meal", {}, res)], room_id=room_id)
    v = grade_ledger_state(g6, rec, db, ids)
    assert v.passed, v.reason


def test_a_golden_meal_draft_that_loses_the_guest_fails(db, monkeypatch):
    from bench.graders import grade_ledger_state
    from bench.corpus import load
    room_id, ids = _meals_room(db)
    _freeze(monkeypatch, "2026-07-20")
    g6 = next(c for c in load("meals") if c.id == "G6")
    res = _propose(db, room_id, ids, {"payer": "m1", "participants": ["m1", "m2", "m3"],
                                      "total": 400_000, "guests": []})
    rec = _record(tools=[("propose_meal", {}, res)], room_id=room_id)
    v = grade_ledger_state(g6, rec, db, ids)
    assert not v.passed


def test_a_case_with_no_ledger_expectation_is_ungraded(db):
    from bench.graders import grade_ledger_state
    v = grade_ledger_state(_case(expect={"tools": ["random_pick"]}),
                           _record(tools=[("random_pick", {})], room_id=1), db, {})
    assert v.passed is None


def test_a_settle_expectation_with_no_settle_call_fails(db, monkeypatch):
    from bench.graders import grade_ledger_state
    from bench.corpus import load
    room_id, ids = _week_room(db)
    _freeze(monkeypatch, _STEP["s12"]["day"])
    case = next(c for c in load("week") if c.id == "s12")
    rec = _record(tools=[("find_members", {})], room_id=room_id)
    v = grade_ledger_state(case, rec, db, ids)
    assert not v.passed and "settle_period" in v.reason


def test_an_errored_tool_result_fails_rather_than_being_ignored(db, monkeypatch):
    from bench.graders import grade_ledger_state
    from bench.corpus import load
    room_id, ids = _week_room(db)
    _freeze(monkeypatch, _STEP["s12"]["day"])
    case = next(c for c in load("week") if c.id == "s12")
    rec = _record(tools=[("settle_period", {}, {"ok": False, "error": "Ngày nào?"})],
                  room_id=room_id)
    v = grade_ledger_state(case, rec, db, ids)
    assert not v.passed and "Ngày nào?" in v.reason


# --------------------------------------------------------------------------- #
# prose_quality
# --------------------------------------------------------------------------- #

def test_unbacked_amount_in_the_reply_fails_without_calling_the_judge():
    from bench.graders import grade_prose
    called = []
    case = _case(message="@bot 300k cả nhóm", expect={"tools": ["random_pick"]})
    rec = _record(final_text="Đã ghi, mỗi người 75.000đ",
                  tools=[("random_pick", {}, {"ok": True})])
    v = grade_prose(case, rec, judge=lambda *_: called.append(1))
    assert not v.passed
    assert "75000" in v.reason.replace(",", "").replace(".", "")
    assert called == []          # stage 1 short-circuits; no judge spend


def test_tool_backed_amounts_reach_the_judge():
    from bench.graders import grade_prose
    rec = _record(final_text="Đã ghi bữa trưa nhé", tools=[("random_pick", {}, {"ok": True})])
    v = grade_prose(_case(), rec, judge=lambda *_: {"ok": True, "reason": "fine"})
    assert v.passed


def test_an_amount_a_tool_result_produced_is_backed():
    from bench.graders import grade_prose
    # moneyguard reads `.args`/`.result` off its argument with getattr, so the
    # runner's plain dicts have to be adapted or nothing counts as backed.
    rec = _record(final_text="Mỗi người 75.000đ nhé",
                  tools=[("random_pick", {}, {"ok": True, "per_head": 75000})])
    v = grade_prose(_case(message="@bot chia đi"), rec, judge=lambda *_: {"ok": True, "reason": "ok"})
    assert v.passed, v.reason


def test_a_missing_judge_is_an_error_not_a_pass():
    from bench.graders import grade_prose
    # An unjudged run must not silently count as passing — that would make the
    # Cursor baseline and the Pi run incomparable (design §11.5).
    v = grade_prose(_case(), _record(final_text="ok"), judge=None)
    assert v.reason and "judge" in v.reason.lower()
    assert v.passed is None      # tri-state: not graded


def test_a_judge_that_rejects_the_reply_fails_the_case():
    from bench.graders import grade_prose
    v = grade_prose(_case(), _record(final_text="I logged your lunch."),
                    judge=lambda *_: {"ok": False, "reason": "replied in English"})
    assert not v.passed and "English" in v.reason


def test_a_malformed_judge_reply_is_ungraded_not_passed():
    from bench.graders import grade_prose
    v = grade_prose(_case(), _record(final_text="ok"), judge=lambda *_: "yes please")
    assert v.passed is None and "judge" in v.reason.lower()


def test_an_errored_turn_fails_prose():
    from bench.graders import grade_prose
    v = grade_prose(_case(), _record(final_text="", error="bridge died"),
                    judge=lambda *_: {"ok": True, "reason": "-"})
    assert not v.passed and "bridge died" in v.reason


# `chat.py` only posts `final_text` on its fallback path. A proposal turn posts a
# draft card and a settle turn posts a server-rendered body, so their prose never
# reaches the room — grading it would judge text nobody reads, and would flag
# "unbacked" amounts in a reply that was discarded.

def test_a_proposal_turn_is_not_graded_because_its_prose_is_never_posted():
    from bench.graders import grade_prose
    called = []
    rec = _record(final_text="Mỗi người 75.000đ",
                  tools=[("propose_meal", {}, {"ok": True, "type": "expense_draft",
                                               "payer_member_id": 1,
                                               "shares_preview": []})])
    v = grade_prose(_case(), rec, judge=lambda *_: called.append(1))
    assert v.passed is None and "card" in v.reason.lower()
    assert called == []


def test_a_settlement_turn_is_not_graded_either():
    from bench.graders import grade_prose
    # A real settle_period result carries NO "type": "settlement" — chat's
    # render_bot_attachments stamps that on. Matching result types alone would
    # miss every settlement and fail its discarded prose as an empty reply.
    rec = _record(final_text="An chuyển cho Bình 125.000đ",
                  tools=[("settle_period", {}, {"ok": True, "transfers": [],
                                                "period": {"from": None, "to": "2026-07-27"}})])
    assert grade_prose(_case(), rec, judge=lambda *_: {"ok": True, "reason": "-"}).passed is None


def test_a_payment_draft_turn_is_not_graded_either():
    from bench.graders import grade_prose
    rec = _record(final_text="Ghi nhận 125.000đ",
                  tools=[("propose_payment", {}, {"ok": True, "type": "payment_draft",
                                                  "from_member_id": 1, "to_member_id": 2,
                                                  "amount": 125000})])
    assert grade_prose(_case(), rec, judge=lambda *_: {"ok": True, "reason": "-"}).passed is None


def test_an_add_member_turn_is_graded_because_it_posts_prose():
    from bench.graders import grade_prose
    rec = _record(final_text="Đã thêm A5 vào nhóm.",
                  tools=[("add_member", {"display_name": "A5"}, {"ok": True})])
    v = grade_prose(_case(), rec, judge=lambda *_: {"ok": True, "reason": "fine"})
    assert v.passed is True, v.reason


def test_the_rubric_reaches_the_judge():
    from bench.graders import PROSE_RUBRIC, grade_prose
    seen = {}
    def judge(case, record, rubric):
        seen["rubric"] = rubric
        return {"ok": True, "reason": "-"}
    grade_prose(_case(), _record(final_text="Xong nhé"), judge=judge)
    assert seen["rubric"] is PROSE_RUBRIC


# --------------------------------------------------------------------------- #
# cost_latency — reported, never pass/fail
# --------------------------------------------------------------------------- #

def test_percentiles_use_nearest_rank_on_a_known_list():
    from bench.graders import summarize_cost_latency
    recs = [_record(elapsed_s=float(i)) for i in range(1, 11)]
    s = summarize_cost_latency(recs)
    assert s["n"] == 10
    assert s["p50_s"] == 5.0          # ceil(0.5*10) - 1 -> index 4
    assert s["p95_s"] == 10.0         # ceil(0.95*10) - 1 -> index 9


def test_percentiles_on_an_even_short_list():
    from bench.graders import summarize_cost_latency
    s = summarize_cost_latency([_record(elapsed_s=x) for x in (4.0, 1.0, 3.0, 2.0)])
    assert s["p50_s"] == 2.0 and s["p95_s"] == 4.0


def test_missing_stats_yields_none_not_zero():
    from bench.graders import summarize_cost_latency
    # Cursor exposes no cost. Reporting 0 would say "free" where the truth is
    # "unknown", and a Pi cost compared against that would look like a rise from
    # nothing.
    s = summarize_cost_latency([_record(elapsed_s=1.0), _record(elapsed_s=2.0)])
    assert s["total_cost_usd"] is None
    assert s["total_tokens"] is None
    assert s["stats_n"] == 0


def test_stats_are_summed_and_the_denominator_is_reported():
    from bench.graders import summarize_cost_latency
    recs = [_record(elapsed_s=1.0, stats={"tokens": 100, "cost": 0.001}),
            _record(elapsed_s=2.0, stats={"tokens": 250, "cost": 0.002}),
            _record(elapsed_s=3.0)]          # no stats at all
    s = summarize_cost_latency(recs)
    assert s["total_tokens"] == 350
    assert abs(s["total_cost_usd"] - 0.003) < 1e-9
    assert s["stats_n"] == 2 and s["n"] == 3   # 2 of 3 contributed


def test_mean_tool_calls_counts_every_invocation():
    from bench.graders import summarize_cost_latency
    recs = [_record(tools=[("a", {}), ("b", {})]), _record(tools=[("a", {})])]
    assert summarize_cost_latency(recs)["mean_tool_calls"] == 1.5


def test_errored_turns_count_in_latency_and_are_reported_separately():
    from bench.graders import summarize_cost_latency
    # A turn that failed after 60s is a latency fact, not a gap in the data.
    s = summarize_cost_latency([_record(elapsed_s=60.0, error="boom"), _record(elapsed_s=2.0)])
    assert s["n"] == 2 and s["error_n"] == 1 and s["p95_s"] == 60.0


def test_an_empty_record_list_is_reported_not_crashed():
    from bench.graders import summarize_cost_latency
    s = summarize_cost_latency([])
    assert s["n"] == 0 and s["p50_s"] is None and s["mean_tool_calls"] is None


def test_a_dropped_adjustment_fails_because_it_splits_one_persons_extra():
    from bench.graders import grade_tool_selection
    # Golden G4: 250k over two people is 100k/150k with the adjustment and
    # 125k/125k without. A grader blind to `adjustments` calls the wrong one
    # correct.
    case = _case(expect={"tools": ["propose_meal"], "args": {"propose_meal": {
        "total": 250000, "adjustments": [{"member": 2, "amount": 50000}]}}})
    v = grade_tool_selection(case, _record(tools=[("propose_meal", {"total": 250000})]))
    assert not v.passed and "adjustments" in v.reason


def test_adjustments_compare_as_a_multiset():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"], "args": {"propose_meal": {
        "adjustments": [{"member": 1, "amount": 20000}, {"member": 2, "amount": 50000}]}}})
    rec = _record(tools=[("propose_meal", {"adjustments": [
        {"member": 2, "amount": 50000}, {"member": 1, "amount": 20000}]})])
    assert grade_tool_selection(case, rec).passed


def test_an_omitted_payer_that_is_the_sender_is_correct_not_missing():
    from bench.graders import grade_tool_selection
    # tools.py's schema: "payer: member id of the payer; blank = the sender". G1's
    # first real Pi run omitted it, the tool filled in the same id, ledger_state
    # passed — and tool_selection failed a correct turn.
    case = _case(expect={"tools": ["propose_meal"],
                         "args": {"propose_meal": {"total": 400000, "payer": 1}}})
    rec = _record(tools=[("propose_meal", {"total": 400000})], sender_member_id=1)
    assert grade_tool_selection(case, rec).passed


def test_an_omitted_payer_that_is_NOT_the_sender_still_fails():
    from bench.graders import grade_tool_selection
    # Someone else fronted the bill, so omitting it charges the wrong person.
    case = _case(expect={"tools": ["propose_meal"],
                         "args": {"propose_meal": {"total": 400000, "payer": 2}}})
    rec = _record(tools=[("propose_meal", {"total": 400000})], sender_member_id=1)
    v = grade_tool_selection(case, rec)
    assert not v.passed and "payer" in v.reason


def test_an_omitted_from_on_a_payment_follows_the_same_rule():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_payment"],
                         "args": {"propose_payment": {"from": 5, "to": 9, "amount": 1000}}})
    rec = _record(tools=[("propose_payment", {"to": 9, "amount": 1000})], sender_member_id=5)
    assert grade_tool_selection(case, rec).passed


def test_an_omitted_total_is_still_a_failure():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"], "args": {"propose_meal": {"total": 400000}}})
    v = grade_tool_selection(case, _record(tools=[("propose_meal", {})], sender_member_id=1))
    assert not v.passed and "total" in v.reason


def test_an_omitted_amount_passes_when_the_tool_computed_the_expected_one():
    """Leaving `amount` out is the *preferred* behavior, not a missing argument.

    `record-payment` says so: with no amount in the message, omit it and the tool
    works out what is owed from the ledger, so the model never transcribes a number
    (design D3). `p129` ("tôi đã trả tiền A1", expecting 27,000đ) called
    `propose_payment(to=A1)` and was failed for exactly that.
    """
    from bench.corpus import Case
    from bench.graders import grade_tool_selection
    case = Case(id="x", source="prod", day="2026-07-27", actor="A2",
                expect={"tools": ["propose_payment"],
                        "args": {"propose_payment": {"from": 2, "to": 1, "amount": 27000}}})
    record = {"sender_member_id": 2, "tools": [
        {"name": "propose_payment", "args": {"to": 1},
         "result": {"ok": True, "type": "payment_draft", "amount": 27000}}]}
    verdict = grade_tool_selection(case, record)
    assert verdict.passed is True, verdict.reason


def test_an_omitted_amount_still_fails_when_the_tool_computed_a_different_one():
    from bench.corpus import Case
    from bench.graders import grade_tool_selection
    case = Case(id="x", source="prod", day="2026-07-27", actor="A2",
                expect={"tools": ["propose_payment"],
                        "args": {"propose_payment": {"from": 2, "to": 1, "amount": 27000}}})
    record = {"sender_member_id": 2, "tools": [
        {"name": "propose_payment", "args": {"to": 1},
         "result": {"ok": True, "type": "payment_draft", "amount": 54000}}]}
    verdict = grade_tool_selection(case, record)
    assert verdict.passed is False
    assert "amount" in verdict.reason
