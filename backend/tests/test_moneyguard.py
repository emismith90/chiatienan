"""Unbacked-money detection (app.moneyguard).

The production examples both come from 2026-07-27: a balance table the model
assembled from conversation history, and six post-discount shares it worked out
with bash before recording them.
"""
from dataclasses import dataclass

from app.moneyguard import money_tokens, unbacked_amounts


@dataclass
class _Inv:
    """Stand-in for app.agent.ToolInvocation."""
    name: str
    args: object = None
    result: object = None


def test_money_tokens_reads_every_vietnamese_spelling():
    text = "Emi 54.500đ, Giang 79,200đ, tổng 324200, cọc 840k, thưởng 1tr"
    assert money_tokens(text) == {54_500, 79_200, 324_200, 840_000, 1_000_000}


def test_money_tokens_ignores_counts_ids_and_dates():
    text = "Chốt kỳ đến 2026-07-27: đề xuất #101 cho 6 người, thẻ T5"
    assert money_tokens(text) == set()


def test_a_hand_typed_balance_table_is_flagged():
    """16:43 — six balances, no money tool in the turn."""
    body = (
        "Tóm tắt kỳ đến 2026-07-22: | Bùi Trang | −75,000đ | | Quách Trí Dũng | −61,000đ | "
        "| Linh Nguyen | +47,000đ | | Giang Hoàng | +89,000đ |"
    )
    stray = unbacked_amounts(body, "@bot show summary not detail", [])
    assert stray == [47_000, 61_000, 75_000, 89_000]


def test_numbers_a_tool_returned_are_not_flagged():
    body = "Chốt kỳ đến 2026-07-27: Giang Hoàng → Linh Nguyen: 107,000đ"
    tools = [_Inv("settle_period", args={}, result={
        "ok": True, "transfers": [{"from_id": 9, "to_id": 6, "amount": 107_000}],
    })]
    assert unbacked_amounts(body, "@bot xin qr", tools) == []


def test_echoing_the_users_own_amount_is_not_flagged():
    """The prompt allows passing a user-stated number through once."""
    assert unbacked_amounts("Đã nhận: 324k nhé.", "@bot i paid, 324k", []) == []


def test_an_amount_passed_into_a_tool_counts_as_backed():
    tools = [_Inv("propose_meal", args={"total": 305_000, "participants": [4, 6]}, result={"ok": True})]
    assert unbacked_amounts("Ghi 305,000đ rồi nhé.", "@bot bún bò 305k", tools) == []


def test_the_bash_computed_shares_are_flagged():
    """13:42 — the model ran the arithmetic itself, then recorded the result."""
    body = (
        "Kết quả: | Emi | 54.500đ | | Nhím | 54.500đ | | Giang | 79.200đ | "
        "| Tabu | 27.000đ | Tổng = 324.200đ"
    )
    stray = unbacked_amounts(body, "@bot I allow it explicitly, use bash", [])
    assert 54_500 in stray and 79_200 in stray and 27_000 in stray


def test_nothing_to_flag_in_a_reply_with_no_money():
    body = "Mình không xác nhận qua chat được — bấm Xác nhận trên thẻ nháp nhé."
    assert unbacked_amounts(body, "@bot xác nhận", []) == []
