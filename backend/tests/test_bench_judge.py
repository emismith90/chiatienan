"""The prose judge — offline, with the HTTP POST injected.

Its most important property is what it does when it *fails*: a judge that
silently passed on error would turn an outage into a clean bill of health, and one
that failed on error would blame the engine for the harness's own problem. Both
routes must land on "not graded".
"""
from bench.corpus import Case
from bench.graders import PROSE_RUBRIC, grade_prose
from bench.judge import openrouter_judge


def _case(message="@bot ghi bữa trưa"):
    return Case(id="C1", source="prod", day="2026-07-20", actor="A1", message=message)


def _record(final_text="Đã ghi nhé."):
    return {"case_id": "C1", "rep": 0, "tools": [], "final_text": final_text,
            "error": None, "elapsed_s": 1.0, "stats": None}


def _reply(content):
    return lambda url, payload, headers, timeout: {
        "choices": [{"message": {"content": content}}]}


def test_a_clean_json_verdict_passes_through():
    judge = openrouter_judge("m/1", api_key="k", post=_reply('{"ok": true, "reason": "fine"}'))
    v = grade_prose(_case(), _record(), judge=judge)
    assert v.passed is True and v.reason == "fine"


def test_a_rejecting_verdict_fails_the_case():
    judge = openrouter_judge("m/1", api_key="k",
                             post=_reply('{"ok": false, "reason": "English"}'))
    assert grade_prose(_case(), _record(), judge=judge).passed is False


def test_a_fenced_or_chatty_reply_is_still_parsed():
    judge = openrouter_judge("m/1", api_key="k", post=_reply(
        'Sure!\n```json\n{"ok": true, "reason": "ổn"}\n```\n'))
    assert grade_prose(_case(), _record(), judge=judge).passed is True


def test_a_missing_key_is_ungraded_not_passed():
    judge = openrouter_judge("m/1", api_key="", post=_reply('{"ok": true}'))
    v = grade_prose(_case(), _record(), judge=judge)
    assert v.passed is None and "OPEN_ROUTER_KEY" in v.reason


def test_the_key_is_read_from_the_environment_name_the_env_actually_uses(monkeypatch):
    from bench.judge import KEY_ENV
    assert KEY_ENV == "OPEN_ROUTER_KEY"
    monkeypatch.setenv(KEY_ENV, "from-env")
    seen = {}
    def capture(url, payload, headers, timeout):
        seen["auth"] = headers["Authorization"]
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}
    grade_prose(_case(), _record(), judge=openrouter_judge("m/1", post=capture))
    assert seen["auth"] == "Bearer from-env"


def test_a_transport_failure_is_ungraded_not_passed():
    def boom(*_a, **_k):
        raise OSError("connection reset")
    v = grade_prose(_case(), _record(), judge=openrouter_judge("m/1", api_key="k", post=boom))
    assert v.passed is None and "connection reset" in v.reason


def test_an_unparseable_reply_is_ungraded_not_passed():
    judge = openrouter_judge("m/1", api_key="k", post=_reply("looks fine to me"))
    v = grade_prose(_case(), _record(), judge=judge)
    assert v.passed is None and "looks fine to me" in v.reason


def test_an_unexpected_body_shape_is_ungraded_not_passed():
    judge = openrouter_judge("m/1", api_key="k",
                             post=lambda *_a, **_k: {"error": "rate limited"})
    assert grade_prose(_case(), _record(), judge=judge).passed is None


def test_the_request_pins_the_model_and_zero_temperature():
    seen = {}
    def capture(url, payload, headers, timeout):
        seen.update(payload=payload, headers=headers, url=url)
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}
    grade_prose(_case(), _record(), judge=openrouter_judge("vendor/judge-1", api_key="k",
                                                          post=capture))
    assert seen["payload"]["model"] == "vendor/judge-1"
    assert seen["payload"]["temperature"] == 0        # a drifting judge is not a judge
    assert seen["headers"]["Authorization"] == "Bearer k"


def test_the_prompt_shows_the_message_and_reply_but_not_the_answer_key():
    from bench.judge import build_prompt
    case = _case("@bot 300k cả nhóm")
    prompt = build_prompt(case, _record("Đã ghi bữa trưa."), PROSE_RUBRIC)
    assert "@bot 300k cả nhóm" in prompt and "Đã ghi bữa trưa." in prompt
    assert PROSE_RUBRIC in prompt
    # Showing the expected tools would invite the judge to grade correctness it
    # is not being asked about.
    assert "propose_meal" not in prompt


# --------------------------------------------------------------------------- #
# the agent as judge
# --------------------------------------------------------------------------- #

def _results(*records, judge=None):
    return {"version": 1, "engine": "cursor", "corpus": "prod", "repeat": 1,
            "judge_model": judge, "records": list(records)}


def _record_with(case_id, passed, reason):
    return {"case_id": case_id, "rep": 0, "final_text": "x",
            "grades": {"prose_quality": {"passed": passed, "reason": reason}}}


def test_prepare_batch_offers_only_what_a_judge_could_decide():
    from bench.judge import prepare_batch
    results = _results(
        _record_with("p1", None, "not graded: no judge configured"),
        _record_with("p2", None, "not graded: the room saw an expense draft card, not the model's prose"),
        _record_with("p3", False, "unbacked amounts in the reply: [40000]"),
        _record_with("p4", True, "fine"),
    )
    # A card turn, a stage-1 failure and an already-decided case are all settled;
    # re-judging them would let an opinion overrule moneyguard.
    assert [c["case_id"] for c in prepare_batch(results)] == ["p1"]


def test_prepare_batch_carries_the_bodies_from_the_corpus():
    from bench.judge import prepare_batch
    # The corpus fills in what the *record* lacks — which is the baseline's case,
    # since its bodies are stripped before it is committed. A record that has its
    # own reply keeps it (see the test below).
    record = _record_with("p1", None, "not graded: no judge configured")
    record["final_text"] = ""
    results = _results(record)
    corpus = {"cases": [{"id": "p1", "message": "@bot ai nợ ai", "reply": "Không ai nợ ai."}]}
    case = prepare_batch(results, corpus)[0]
    assert case["message"] == "@bot ai nợ ai" and case["reply"] == "Không ai nợ ai."


def test_apply_verdicts_records_who_judged():
    from bench.judge import apply_verdicts
    results = _results(_record_with("p1", None, "not graded: no judge configured"))
    applied, unmatched = apply_verdicts(
        results, {"p1": {"ok": False, "reason": "narrates its machinery"}},
        "claude-opus-5 (agent)")
    assert applied == 1 and unmatched == 0
    assert results["judge_model"] == "claude-opus-5 (agent)"
    assert results["records"][0]["grades"]["prose_quality"] == {
        "passed": False, "reason": "narrates its machinery"}


def test_apply_verdicts_never_overrules_a_decided_verdict():
    from bench.judge import apply_verdicts
    results = _results(_record_with("p3", False, "unbacked amounts in the reply: [40000]"))
    applied, _ = apply_verdicts(results, {"p3": {"ok": True, "reason": "looks fine"}}, "agent")
    # moneyguard is deterministic and owns amount provenance; an opinion must not
    # be able to wave a D3 violation through.
    assert applied == 0
    assert results["records"][0]["grades"]["prose_quality"]["passed"] is False


def test_a_verdict_for_an_unknown_case_is_reported():
    from bench.judge import apply_verdicts
    results = _results(_record_with("p1", None, "not graded: no judge configured"))
    _, unmatched = apply_verdicts(results, {"p1": {"ok": True}, "p99": {"ok": True}}, "agent")
    assert unmatched == 1


def test_the_batch_judges_the_run_s_own_reply_not_the_corpus_one():
    """`--corpus` is for the recorded baseline, whose bodies were stripped.

    Its `reply` field is *production's* text, so preferring it handed a Pi run prod's
    replies to grade — twenty of them, with the narration prod was being replaced
    for.
    """
    from bench.judge import prepare_batch
    results = {"records": [{
        "case_id": "p98", "rep": 0, "message": "@bot log đi",
        "final_text": "Đã ghi bữa trưa nhé.",
        "grades": {"prose_quality": {"passed": None,
                                     "reason": "not graded: no judge configured"}}}]}
    corpus = {"cases": [{"id": "p98", "message": "@bot log đi",
                         "reply": "mình đọc skill phù hợp rồi xử lý…"}]}
    batch = prepare_batch(results, corpus)
    assert batch[0]["reply"] == "Đã ghi bữa trưa nhé."


def test_the_corpus_reply_is_the_fallback_when_the_record_has_none():
    from bench.judge import prepare_batch
    results = {"records": [{
        "case_id": "p98", "rep": 0, "final_text": "",
        "grades": {"prose_quality": {"passed": None,
                                     "reason": "not graded: no judge configured"}}}]}
    corpus = {"cases": [{"id": "p98", "message": "@bot log đi", "reply": "Đã ghi."}]}
    batch = prepare_batch(results, corpus)
    assert batch[0] == {"case_id": "p98", "rep": 0, "message": "@bot log đi",
                        "reply": "Đã ghi."}
