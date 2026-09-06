"""What the shared ledger tools contribute to eval (Phase 6 review F2d): which tool
results the room sees as a server-rendered body instead of the model's prose, and
the labels the prose grader uses for "not graded: the room saw …". A business's eval
module composes these with its own kinds.
"""
from __future__ import annotations

from kernos.eval import _ok_results

SHARED_CARD_LABELS = {
    "payment_draft": "a payment draft card",
    "settlement": "a server-rendered settlement body",
    "settle_blocked": "a server-rendered blocked-settle body",
    "statement": "a server-rendered statement body",
    "summary": "a server-rendered summary body",
    "random_pick": "a server-rendered random-pick body",
}




def shared_body_kind(record: dict) -> str | None:
    """Which shared body the room would see for this turn, in the render decision's own
    precedence: a `propose_payment` draft, then settle, statement, summary, random pick.
    A successful `settle_period` result does **not** carry `type: "settlement"` —
    `render_bot_attachments` stamps that on, so it is matched by tool here."""
    if any(r.get("type") == "payment_draft" for r in _ok_results(record, "propose_payment")):
        return "payment_draft"
    settle = _ok_results(record, "settle_period")
    if settle:
        return "settle_blocked" if settle[-1].get("type") == "settle_blocked" else "settlement"
    for tool_name, kind in (("member_statement", "statement"),
                            ("get_period_summary", "summary"),
                            ("pick_random", "random_pick")):
        results = _ok_results(record, tool_name)
        if results and results[-1].get("type") == kind:
            return kind
    return None
