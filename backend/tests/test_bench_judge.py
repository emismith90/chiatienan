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
    assert v.passed is None and "OPENROUTER_API_KEY" in v.reason


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
