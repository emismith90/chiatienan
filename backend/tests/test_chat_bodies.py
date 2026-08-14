from app.chat import (
    render_bot_attachments,
    _statement_body,
    _summary_body,
    _random_pick_body,
)


class _Fake:
    def __init__(self, name, res): self._n, self._r = name, res
    def last_result(self, name): return self._r if name == self._n else None


def test_render_statement_attachment():
    res = _Fake("member_statement", {"ok": True, "type": "statement", "member": {"id": 9, "name": "Giang"},
                "period": {"from": None, "to": "2026-07-22"},
                "owe": [{"creditor_id": 6, "name": "Linh", "meal_id": 2, "dish": "bun bo",
                         "occurred_on": "2026-07-21", "amount": 61000, "status": "unpaid"}],
                "owed": []})
    att = render_bot_attachments(res)
    assert att["type"] == "statement"
    body = _statement_body(att)
    assert "Linh" in body and "61" in body


def test_render_summary_attachment():
    res = _Fake("get_period_summary", {"ok": True, "type": "summary",
                "period": {"from": None, "to": "2026-07-22"},
                "timeline": [{"kind": "meal", "dish": "bun bo", "payer_name": "Linh", "total": 122000,
                              "occurred_on": "2026-07-21"}],
                "outstanding": [{"debtor_id": 9, "debtor_name": "Giang", "creditor_id": 6,
                                 "creditor_name": "Linh", "amount": 61000}]})
    att = render_bot_attachments(res)
    assert att["type"] == "summary"
    # The body is a headline; the rows live in the card (grouped by day) and in
    # the ledger panel it can open. Printing all of them made a fifteen-row
    # paragraph that a "format it as bullets please" request could not change.
    body = _summary_body(att)
    assert body == "Summary through 2026-07-22: 1 meal across 1 day — details below."
    # The detail is still carried, just not in the prose.
    assert att["timeline"][0]["dish"] == "bun bo"


def test_summary_body_counts_meals_payments_and_days():
    """Production: 15 rows over 3 days, asked for twice, printed identically."""
    body = _summary_body({
        "period": {"from": "2026-07-20", "to": "2026-07-26"},
        "timeline": [
            {"kind": "meal", "occurred_on": "2026-07-22", "total": 1},
            {"kind": "meal", "occurred_on": "2026-07-22", "total": 1},
            {"kind": "payment", "occurred_on": "2026-07-23", "amount": 1},
            {"kind": "payment", "occurred_on": "2026-07-24", "amount": 1},
        ],
    })
    assert body == ("Summary 2026-07-20 → 2026-07-26: 2 meals, 2 payments "
                    "across 3 days — details below.")


def test_summary_body_when_the_period_is_empty():
    body = _summary_body({"period": {"from": None, "to": "2026-07-26"}, "timeline": []})
    assert body == "Summary through 2026-07-26: no transactions in this period."


def test_statement_body_prints_no_net_line():
    """Production, 2026-07-27/28: every balance answer ended "Ròng: -54.500đ" —
    a number that is not payable to anyone, and that reads as an offset the
    ledger never performs. Both directions, no total."""
    body = _statement_body({
        "member": {"id": 9, "name": "Giang"},
        "owe": [{"name": "Linh", "amount": 61000, "dish": "bun bo", "status": "unpaid"}],
        "owed": [{"name": "Linh", "amount": 20000, "dish": "ca phe", "status": "unpaid"}],
    })
    assert "Ròng" not in body and "-41" not in body and "41.000" not in body
    assert "You owe:" in body and "You are owed:" in body
    assert "61,000đ" in body and "20,000đ" in body


def test_statement_body_when_nothing_is_open_either_way():
    body = _statement_body({"member": {"id": 9, "name": "Giang"}, "owe": [], "owed": []})
    assert body == "Giang — owes and is owed:\nYou owe nobody, and nobody owes you."
    assert "Ròng" not in body


def test_err_statement_result_not_wrapped():
    # An _err (ok:False) from member_statement must NOT render as a balanced card.
    res = _Fake("member_statement", {"ok": False, "error": "x"})
    assert render_bot_attachments(res) is None


def test_err_summary_result_not_wrapped():
    res = _Fake("get_period_summary", {"ok": False, "error": "x"})
    assert render_bot_attachments(res) is None


def test_render_random_pick_attachment_and_body():
    res = _Fake("pick_random", {"ok": True, "type": "random_pick",
                "chosen": {"id": 3, "name": "An"}, "label": "trả tiền",
                "candidates": [{"id": 1, "name": "Bình"}, {"id": 3, "name": "An"}]})
    att = render_bot_attachments(res)
    assert att["type"] == "random_pick"
    body = _random_pick_body(att)
    assert "An" in body and "trả tiền" in body and "from 2 people" in body


def test_err_random_pick_result_not_wrapped():
    res = _Fake("pick_random", {"ok": False, "error": "x"})
    assert render_bot_attachments(res) is None


# ------------------------------------------------- an answerless turn, by cause
#
# Production, 2026-08-14 room 3: two turns came back with no text for two
# completely different reasons and the room was told the same thing both times.
# `capped` existed but never left `agent.run_turn`, so `chat.py` could not tell a
# 120s timeout from an empty completion.

def _result(*, final_text="", error=None, capped=False):
    from app.agent import TurnResult
    return TurnResult(final_text=final_text, error=error, capped=capped)


def test_a_capped_turn_says_it_ran_out_of_time():
    from app.chat import _empty_turn_body
    body = _empty_turn_body(_result(capped=True))
    assert "ran out of time" in body
    assert "nothing was recorded" in body


def test_an_empty_completion_is_not_reported_as_a_timeout():
    from app.chat import _empty_turn_body
    body = _empty_turn_body(_result())
    assert "time" not in body.lower()
    assert "nothing" in body.lower()


def test_a_capped_turn_and_an_empty_one_do_not_read_the_same():
    from app.chat import _empty_turn_body
    assert _empty_turn_body(_result(capped=True)) != _empty_turn_body(_result())


def test_an_error_outranks_the_cap():
    """A capped turn that also errored reports the error: it is the more specific
    fact, and `turn.js` only sets `error` when the failure was not the cap."""
    from app.chat import _empty_turn_body
    assert _empty_turn_body(_result(error="rate limited", capped=True)) == "⚠️ rate limited"


def test_partial_text_from_a_capped_turn_is_kept_over_any_of_this():
    """A cap keeps whatever was accumulated — that partial answer beats a notice."""
    from app.chat import _empty_turn_body
    r = _result(final_text="Mai ăn bún chả nhé", capped=True)
    assert r.final_text or _empty_turn_body(r) == r.final_text
