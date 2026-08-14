"""Unbacked-money detection (app.moneyguard).

The production examples both come from 2026-07-27: a balance table the model
assembled from conversation history, and six post-discount shares it worked out
with bash before recording them.
"""
from dataclasses import dataclass

from app.moneyguard import fabricated_commit, money_tokens, unbacked_amounts


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


def test_the_history_backs_an_amount_the_room_already_stated():
    """A number from the conversation is not invented money.

    The benchmark's `p102` / `p104` replies quote "tổng 324k" — which the room said
    a message earlier and the model was handed as history — and were reported as
    unbacked. `chat.py` passes the history to this function for the same reason, so
    the alerts that survive are the ones worth acting on.
    """
    from app.moneyguard import unbacked_amounts
    history = "«A1»: hết 324k anh A3 trả"
    assert unbacked_amounts("Tổng 324k nhé", f"@bot log\n{history}", []) == []
    # and an amount from nowhere is still unbacked
    assert unbacked_amounts("Tổng 500k nhé", f"@bot log\n{history}", []) == [500000]


# --- fabricated commits (the enforcing half) -------------------------------- #

#: Verbatim from room 3, 2026-08-14 13:14:07. `tools=0`, `images=1`, 6.1s — a
#: word-perfect forgery of `chat._meal_body` for a meal that was never written.
_PROD_FORGERY = (
    "Đã ghi #14 — Texas Chicken: Bạch Mai trả tổng 793,760đ • Emi 132,293đ, "
    "Nhím 132,293đ, Giang Hoàng 132,293đ, Linh Nguyen 132,293đ, Kun 132,293đ, "
    "Tabu 132,293đ"
)


def test_the_production_forgery_is_caught():
    stray = fabricated_commit(_PROD_FORGERY, "@phoenix log this for all", [])
    assert stray == [132_293, 793_760]


def test_a_forgery_survives_the_tools_that_did_not_write():
    """The turn read the room and then lied about the outcome. Lookups and
    read-only queries are not a licence to claim a write."""
    tools = [_Inv("find_members", {"all_active": True}, {"ok": True, "matched": []}),
             _Inv("resolve_date", {"day_word": "hôm nay"}, {"ok": True})]
    assert fabricated_commit(_PROD_FORGERY, "@phoenix log this for all", tools)


def test_a_failed_propose_meal_does_not_license_the_claim():
    """The worst shape: the tool ran, refused, and the model announced success
    anyway. `ok` is false, so nothing wrote."""
    tools = [_Inv("propose_meal", {"total": 793_760}, {"ok": False, "error": "no participants"})]
    assert fabricated_commit(_PROD_FORGERY, "@phoenix log this for all", tools)


def test_a_real_proposal_is_not_a_forgery():
    tools = [_Inv("propose_meal", {"total": 793_760},
                  {"ok": True, "type": "expense_draft", "per_head": 132_293})]
    assert fabricated_commit(_PROD_FORGERY, "@phoenix log this for all", tools) is None


def test_recalling_a_meal_the_history_already_records_is_not_a_forgery():
    """"Đã ghi" about the past is honest. The amounts trace to the conversation
    the model was handed, which is exactly what separates it from an invention."""
    history = "«A1»: BOT: Đã ghi #13 — bún cá: Giang Hoàng trả tổng 175,000đ"
    body = "Bữa bún cá hôm qua mình đã ghi rồi nhé — tổng 175,000đ."
    assert fabricated_commit(body, f"@phoenix ghi chưa\n{history}", []) is None


def test_admitting_failure_is_never_a_forgery():
    """The honest reply must survive: 'chưa ghi' is not 'đã ghi', and the guard
    would otherwise suppress the very message it substitutes."""
    body = "Mình chưa ghi được bữa này — hoá đơn 793,760đ nhưng chưa rõ ai trả."
    assert fabricated_commit(body, "@phoenix log this for all", []) is None


def test_talking_without_money_is_not_a_forgery():
    """No amounts, nothing to be wrong about — 'đã ghi' alone must not block a
    reply that moves no numbers."""
    assert fabricated_commit("Đã ghi chú lại rồi nhé!", "@phoenix nhớ hộ mình", []) is None


# --- the laundering case: a forgery must not survive being repeated ---------- #

def _ledger(*live_ids: int):
    """`meal_exists` for a room whose ledger holds exactly `live_ids`."""
    return lambda mid: mid in live_ids


def test_a_repeat_is_caught_even_though_the_history_now_backs_its_numbers():
    """Room 3, 13:17 / 13:57 / 13:58 — the same forgery, three more times.

    Each repeat came back clean under the amount test alone: the first telling
    was posted, so 793,760 and 132,293 were in the room's own history, and the
    history is part of the allow-set by design. The ledger is not so
    accommodating — meal #14 was never written.
    """
    history = "\n".join([f"«A1»: BOT: {_PROD_FORGERY}"] * 3)
    user_text = f"@phoenix nay ăn Texas chicken hết 793.760, chia đều cả 7 người\n{history}"

    # Amounts alone: laundered clean.
    assert unbacked_amounts(_PROD_FORGERY, user_text, []) == []
    assert fabricated_commit(_PROD_FORGERY, user_text, []) is None

    # Against the ledger: condemned, however often it is retold.
    assert fabricated_commit(_PROD_FORGERY, user_text, [], meal_exists=_ledger(13)) is not None


def test_the_empty_amount_list_still_means_blocked():
    """The meal-id verdict returns [] when nothing is unbacked. A caller testing
    truthiness instead of `is not None` would post the forgery — pin the shape."""
    history = "\n".join([f"«A1»: BOT: {_PROD_FORGERY}"] * 3)
    verdict = fabricated_commit(_PROD_FORGERY, f"@phoenix log lại\n{history}", [],
                                meal_exists=_ledger(13))
    assert verdict == [] and verdict is not None


def test_a_claim_about_a_meal_that_really_exists_is_left_alone():
    body = "Bữa bún cá hôm qua mình đã ghi rồi nhé — Đã ghi #13, tổng 175,000đ."
    history = "«A1»: BOT: Đã ghi #13 — bún cá: Giang Hoàng trả tổng 175,000đ"
    assert fabricated_commit(body, f"@phoenix ghi chưa\n{history}", [],
                             meal_exists=_ledger(13)) is None


def test_a_voided_meal_is_not_a_recorded_one():
    """`meal_exists` is void-aware: a meal the room cancelled did not happen."""
    body = "Đã ghi #13 — bún cá: Giang Hoàng trả tổng 175,000đ"
    assert fabricated_commit(body, "@phoenix ghi lại đi", [],
                             meal_exists=_ledger()) is not None
