"""World reconstruction and the case runner.

The whole point of `bench.world` is that a case's expectation assumes a world,
and replaying chat text does not build one. These tests assert the world is
*there* before the LLM turn — if they pass and the naive runner had been written
instead, every money grader would have failed on both engines and `--compare`
would have called that equivalence.
"""
from datetime import date

import pytest

from app import drafts, ledger, tools
from bench.corpus import Case, load


def _week(case_id):
    return next(c for c in load("week") if c.id == case_id)


def test_world_reconstruction_puts_s5_in_the_state_its_expectation_assumes(db):
    from bench.world import build_world
    s5 = _week("s5")
    room_id, ids, _ = build_world(db, s5)
    with db.session() as s:
        bal = ledger.period_balances(s, room_id, None, date(2999, 1, 1))
    # s4's expectation, i.e. the state s5 must settle from
    assert bal[ids["a2"]]["balance"] == 300_000
    assert bal[ids["a3"]]["balance"] == -225_000


def test_week_members_are_seeded_with_banks_so_qr_builds(db):
    from bench.world import build_world
    s5 = _week("s5")
    room_id, ids, _ = build_world(db, s5)
    ctx = tools.ToolContext(db=db, room_id=room_id, sender_member_id=ids["a3"])
    res = tools.build_tools(ctx)["settle_period"].execute({"keyword": "since_last"})
    payee_rows = [t for t in res["transfers"] if t["to_id"] == ids["a1"]]
    assert payee_rows and all(t["qr_url"] for t in payee_rows)   # would QRError unbanked


def test_s8_world_has_the_pending_draft_that_blocks_settle(db):
    from bench.world import build_world
    s8 = _week("s8")
    room_id, _, _ = build_world(db, s8)
    with db.session() as s:
        assert len(drafts.list_pending_drafts(s, room_id)) == 1


def test_s12_world_is_a_settled_ledger(db):
    from bench.world import build_world
    s12 = _week("s12")
    room_id, ids, _ = build_world(db, s12)
    with db.session() as s:
        bal = ledger.period_balances(s, room_id, None, date(2999, 1, 1))
    assert all(v["balance"] == 0 for v in bal.values())   # the s11* payments ran


def test_the_member_added_mid_scenario_exists_in_a_later_world(db):
    from bench.world import build_world
    # a5 joins at s6 and is a participant in s7 — a world that skipped the
    # message-less steps could not even build s8.
    room_id, ids, _ = build_world(db, _week("s8"))
    assert "a5" in ids


def test_confirm_pending_commits_the_draft_its_ref_created(db):
    from bench.world import build_world
    # s10b's expectation needs s7 (confirmed by s9a) and s9b (confirmed by s10a).
    room_id, _, draft_by_step = build_world(db, _week("s10b"))
    with db.session() as s:
        assert drafts.list_pending_drafts(s, room_id) == []
    assert {"s7", "s9b"} <= set(draft_by_step)


def test_s10b_settles_to_its_expected_transfers(db):
    from bench.graders import compare_settlement
    from bench.world import build_world, frozen_clock
    case = _week("s10b")
    room_id, ids, _ = build_world(db, case)
    with frozen_clock(case.day):
        ctx = tools.ToolContext(db=db, room_id=room_id, sender_member_id=ids[case.actor])
        res = tools.build_tools(ctx)["settle_period"].execute({"keyword": "since_last"})
    assert compare_settlement(res, case.expect, ids) == []


def test_a_meals_world_is_just_the_four_member_room(db):
    from bench.world import build_world
    room_id, ids, draft_by_step = build_world(db, next(c for c in load("meals") if c.id == "G1"))
    assert set(ids) == {"m1", "m2", "m3", "m4"} and draft_by_step == {}
    with db.session() as s:
        assert ledger.period_balances(s, room_id, None, date(2999, 1, 1)) == {}


def test_the_frozen_clock_reaches_the_tool(db):
    from bench.world import build_world, frozen_clock
    # A stub tool observes case.day, not today. `today_ict` is import-bound in
    # ledger/drafts/tools, so only patching `now_ict` works.
    case = _week("s1")
    room_id, ids, _ = build_world(db, case)
    with frozen_clock(case.day):
        ctx = tools.ToolContext(db=db, room_id=room_id, sender_member_id=ids["a1"])
        res = tools.build_tools(ctx)["resolve_date"].execute({"word": "hôm nay"})
    assert res["date"] == case.day


def test_the_clock_is_restored_after_the_context_exits():
    from app import clock
    from bench.world import frozen_clock
    real = clock.now_ict
    with frozen_clock("2026-07-20"):
        assert clock.now_ict().date().isoformat() == "2026-07-20"
    assert clock.now_ict is real


# --------------------------------------------------------------------------- #
# the runner
# --------------------------------------------------------------------------- #

class _StubResult:
    def __init__(self, tools_called=(), final_text="Đã ghi.", error=None):
        from app.agent import ToolInvocation
        self.final_text = final_text
        self.error = error
        self.turn_id = "t1"
        self.tools = [ToolInvocation(name=n, args=a, result=r) for n, a, r in tools_called]


def _stub(*_args, **_kwargs):
    async def _run():
        return _StubResult()
    return _run()


def test_repeat_produces_n_records_per_case():
    from bench.run import run_corpus
    recs = run_corpus("meals", repeat=3, run_turn=_stub)
    assert len([r for r in recs if r["case_id"] == "G1"]) == 3
    assert {r["rep"] for r in recs if r["case_id"] == "G1"} == {0, 1, 2}


def test_a_case_that_raises_is_recorded_not_fatal():
    from bench.run import run_corpus

    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        async def _run():
            if calls["n"] == 1:
                raise RuntimeError("model exploded")
            return _StubResult()
        return _run()

    recs = run_corpus("meals", repeat=1, run_turn=boom)
    # one bad case must never kill the run
    assert len(recs) == 9
    assert sum(1 for r in recs if r["error"]) == 1
    assert any("model exploded" in (r["error"] or "") for r in recs)


def test_a_record_carries_what_the_graders_need():
    from bench.run import run_corpus
    recs = run_corpus("meals", repeat=1, run_turn=_stub)
    r = recs[0]
    for key in ("case_id", "rep", "source", "room_id", "tools", "final_text",
                "error", "elapsed_s", "stats", "grades"):
        assert key in r, key
    assert set(r["grades"]) == {"tool_selection", "ledger_state", "prose_quality"}


def test_the_runner_grades_each_record_while_its_world_is_alive():
    from bench.run import run_corpus

    def good_meal(user_text, ctx, **_k):
        # what a correct G1 turn looks like: 400k over all four members
        ids = sorted(m.id for m in _members(ctx))
        result = tools.build_tools(ctx)["propose_meal"].execute(
            {"payer": ids[0], "participants": ids, "total": 400_000})
        async def _run():
            return _StubResult(tools_called=[("propose_meal",
                                             {"payer": ids[0], "participants": ids,
                                              "total": 400_000}, result)])
        return _run()

    recs = run_corpus("meals", repeat=1, run_turn=good_meal)
    g1 = next(r for r in recs if r["case_id"] == "G1")
    assert g1["grades"]["tool_selection"]["passed"] is True, g1["grades"]
    assert g1["grades"]["ledger_state"]["passed"] is True, g1["grades"]


def _members(ctx):
    from app.models import Member
    with ctx.db.session() as s:
        return list(s.query(Member).filter(Member.room_id == ctx.room_id).all())


def test_expected_args_are_resolved_from_corpus_keys_to_member_ids():
    from bench.corpus import resolve_args
    case = Case(id="x", source="week", day="2026-07-20", actor="a1",
                expect={"tools": ["propose_meal"], "args": {"propose_meal": {
                    "payer": "a1", "participants": ["a1", "a2"], "total": 300_000}}})
    out = resolve_args(case, {"a1": 11, "a2": 22})
    args = out.expect["args"]["propose_meal"]
    assert args["payer"] == 11 and args["participants"] == [11, 22]
    assert args["total"] == 300_000            # an amount is not a member key
    # the original is untouched, so a repeat run re-resolves cleanly
    assert case.expect["args"]["propose_meal"]["payer"] == "a1"


def test_resolving_a_payment_maps_from_and_to():
    from bench.corpus import resolve_args
    case = Case(id="x", source="week", day="2026-07-20", actor="a1",
                expect={"args": {"propose_payment": {"from": "a2", "to": "a1",
                                                     "amount": 125_000}}})
    args = resolve_args(case, {"a1": 11, "a2": 22}).expect["args"]["propose_payment"]
    assert args["from"] == 22 and args["to"] == 11 and args["amount"] == 125_000


def test_an_unknown_corpus_key_in_an_expectation_raises_rather_than_grading_wrong():
    from bench.corpus import resolve_args
    case = Case(id="x", source="week", day="2026-07-20", actor="a1",
                expect={"args": {"propose_meal": {"payer": "ghost"}}})
    with pytest.raises(KeyError, match="ghost"):
        resolve_args(case, {"a1": 11})


# --------------------------------------------------------------------------- #
# The other half of the vacuity guard
# --------------------------------------------------------------------------- #
#
# A grader nobody can satisfy produces the same false equivalence as one that
# passes everything: both engines fail identically and `--compare` reports "no
# change". The no-op stub above proves the graders can fail; this oracle proves
# they can pass — on every case in the golden corpora, through the real tools.

def _oracle(case):
    """An engine that makes exactly the call the corpus expects."""
    def run_turn(user_text, ctx, **_kwargs):
        from app.models import Member
        # `build_world` inserts the seed members in `case.members` order and any
        # mid-scenario `add_member` after them, so ascending id order is corpus
        # key order. That is how the oracle recovers the key -> id map without
        # the runner having to hand it one.
        keys = [m["key"] for m in case.members]
        keys += [s["new_member"] for s in case.prior_steps if s["kind"] == "add_member"]
        with ctx.db.session() as s:
            rows = s.query(Member).filter(Member.room_id == ctx.room_id).order_by(Member.id).all()
        by_key = dict(zip(keys, [m.id for m in rows], strict=True))

        name = case.expect["tools"][0]
        args = dict((case.expect.get("args") or {}).get(name) or {})
        for key in ("payer", "from", "to"):
            if key in args:
                args[key] = by_key[args[key]]
        if "participants" in args:
            args["participants"] = [by_key[k] for k in args["participants"]]
        for list_arg in ("items", "adjustments"):
            if list_arg in args:
                args[list_arg] = [dict(i, member=by_key[i["member"]]) for i in args[list_arg]]
        if name == "settle_period":
            args = {"keyword": "since_last"}
        if name == "add_member":
            args = {"display_name": case.expect["new_member"].upper(),
                    "nickname": case.expect["new_member"]}

        result = tools.build_tools(ctx)[name].execute(args)

        async def _run():
            r = _StubResult(tools_called=[(name, args, result)])
            # A card turn's prose is never posted, so leave it empty; the
            # add_member turn is the one that posts prose.
            r.final_text = "Đã xong nhé." if name == "add_member" else ""
            return r
        return _run()
    return run_turn


@pytest.mark.parametrize("corpus_name", ["meals", "week"])
def test_an_oracle_engine_passes_every_gradable_case(corpus_name):
    from bench.run import _run_one
    failures = []
    for case in load(corpus_name):
        rec = _run_one(case, 0, _oracle(case),
                       judge=lambda *_: {"ok": True, "reason": "oracle"})
        for grader, verdict in rec["grades"].items():
            if verdict["passed"] is False:
                failures.append(f'{case.id}/{grader}: {verdict["reason"]}')
    assert failures == [], "\n".join(failures)


def test_the_oracle_is_actually_being_graded_not_skipped():
    # Guards the test above: if every verdict were None it would pass vacuously.
    from bench.run import _run_one
    passed = 0
    for case in load("week"):
        rec = _run_one(case, 0, _oracle(case), judge=lambda *_: {"ok": True, "reason": "-"})
        passed += sum(1 for v in rec["grades"].values() if v["passed"] is True)
    assert passed >= 20, passed
