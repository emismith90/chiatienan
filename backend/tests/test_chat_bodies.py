from app.chat import render_bot_attachments, _statement_body, _summary_body


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
    assert body == "Tóm tắt đến 2026-07-22: 1 bữa trong 1 ngày — chi tiết ở dưới."
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
    assert body == ("Tóm tắt 2026-07-20 → 2026-07-26: 2 bữa, 2 lượt trả tiền "
                    "trong 3 ngày — chi tiết ở dưới.")


def test_summary_body_when_the_period_is_empty():
    body = _summary_body({"period": {"from": None, "to": "2026-07-26"}, "timeline": []})
    assert body == "Tóm tắt đến 2026-07-26: chưa có giao dịch nào trong kỳ."


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
    assert "Bạn nợ:" in body and "Được nợ:" in body
    assert "61,000đ" in body and "20,000đ" in body


def test_statement_body_when_nothing_is_open_either_way():
    body = _statement_body({"member": {"id": 9, "name": "Giang"}, "owe": [], "owed": []})
    assert body == "Giang — nợ và được nợ:\nBạn không nợ ai, không ai nợ bạn."
    assert "Ròng" not in body


def test_err_statement_result_not_wrapped():
    # An _err (ok:False) from member_statement must NOT render as a balanced card.
    res = _Fake("member_statement", {"ok": False, "error": "x"})
    assert render_bot_attachments(res) is None


def test_err_summary_result_not_wrapped():
    res = _Fake("get_period_summary", {"ok": False, "error": "x"})
    assert render_bot_attachments(res) is None
