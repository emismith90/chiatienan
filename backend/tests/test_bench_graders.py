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
