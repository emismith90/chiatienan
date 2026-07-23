from app.chat import render_bot_attachments, _statement_body, _summary_body


class _Fake:
    def __init__(self, name, res): self._n, self._r = name, res
    def last_result(self, name): return self._r if name == self._n else None


def test_render_statement_attachment():
    res = _Fake("member_statement", {"ok": True, "type": "statement", "member": {"id": 9, "name": "Giang"},
                "period": {"from": None, "to": "2026-07-22"},
                "owe": [{"creditor_id": 6, "name": "Linh", "meal_id": 2, "dish": "bun bo",
                         "occurred_on": "2026-07-21", "amount": 61000, "status": "unpaid"}],
                "owed": [], "net": -61000})
    att = render_bot_attachments(res)
    assert att["type"] == "statement"
    body = _statement_body(att)
    assert "Linh" in body and "61" in body


def test_render_summary_attachment():
    res = _Fake("get_period_summary", {"ok": True, "type": "summary",
                "period": {"from": None, "to": "2026-07-22"},
                "timeline": [{"kind": "meal", "dish": "bun bo", "payer_name": "Linh", "total": 122000,
                              "occurred_on": "2026-07-21"}],
                "balances": [{"id": 6, "name": "Linh", "balance": 61000}]})
    att = render_bot_attachments(res)
    assert att["type"] == "summary"
    assert "bun bo" in _summary_body(att)


def test_err_statement_result_not_wrapped():
    # An _err (ok:False) from member_statement must NOT render as a balanced card.
    res = _Fake("member_statement", {"ok": False, "error": "x"})
    assert render_bot_attachments(res) is None


def test_err_summary_result_not_wrapped():
    res = _Fake("get_period_summary", {"ok": False, "error": "x"})
    assert render_bot_attachments(res) is None
