from app.agent import ToolInvocation, TurnResult
from app.reply import build_reply, fmt_vnd


def _tr(name, result, final_text=""):
    tr = TurnResult(final_text=final_text)
    tr.tools = [ToolInvocation(name, {}, result)]
    return tr


def test_fmt_vnd_compact_thousands():
    assert fmt_vnd(840_000) == "840k"
    assert fmt_vnd(116_000) == "116k"
    assert fmt_vnd(116_500) == "116.500đ"


def test_meal_breakdown_is_deterministic_from_tool_result():
    result = {
        "ok": True,
        "meal_id": 42,
        "total_amount": 600_000,
        "payer": {"id": 1, "name": "An"},
        "shares": [
            {"id": 1, "name": "An", "amount": 200_000},
            {"id": 2, "name": "Bình", "amount": 200_000},
            {"id": 3, "name": "Cường", "amount": 200_000},
        ],
    }
    reply = build_reply(_tr("record_meal", result, final_text="ignored LLM text"))
    assert reply.card is None
    assert "Tổng 600k" in reply.text
    assert "An 200k" in reply.text
    assert "#42" in reply.text


def test_settlement_reply_builds_card_with_qr():
    settle = {
        "ok": True,
        "period": {"from": "2026-07-14", "to": "2026-07-20"},
        "committed": False,
        "warnings": [],
        "transfers": [
            {
                "from_id": 2, "from_name": "Bình", "to_id": 1, "to_name": "An",
                "amount": 100_000, "note": "Chia tien an 2026-07-20",
                "qr_url": "https://img.vietqr.io/image/VCB-001-compact2.png?amount=100000",
            }
        ],
    }
    reply = build_reply(_tr("settle_period", settle))
    assert reply.card is not None
    assert reply.card["type"] == "AdaptiveCard"
    # a QR image is present in the card
    images = [b for b in reply.card["body"] if b.get("type") == "Image"]
    assert images and images[0]["url"].startswith("https://img.vietqr.io/image/")
    assert "Bình → An: 100k" in reply.text


def test_settlement_committed_note():
    settle = {
        "ok": True, "period": {"from": None, "to": "2026-07-20"}, "committed": True, "warnings": [],
        "transfers": [{"from_name": "B", "to_name": "A", "amount": 50_000, "qr_url": None, "note": "x"}],
    }
    reply = build_reply(_tr("settle_period", settle))
    assert "đã được chốt" in reply.text
    # missing qr surfaces a warning block, not a broken image
    assert any("Chưa tạo được QR" in b.get("text", "") for b in reply.card["body"])


def test_nothing_to_settle_no_card():
    settle = {"ok": True, "period": {"from": None, "to": "2026-07-20"}, "transfers": [], "message": "Không có gì để chốt."}
    reply = build_reply(_tr("settle_period", settle))
    assert reply.card is None
    assert "Không có gì để chốt" in reply.text


def test_void_reply():
    reply = build_reply(_tr("void_meal", {"ok": True, "meal_id": 7, "voided": True}))
    assert "#7" in reply.text


def test_falls_back_to_llm_text_for_non_money_turns():
    tr = TurnResult(final_text="Bạn muốn chia cho những ai?")
    reply = build_reply(tr)
    assert reply.text == "Bạn muốn chia cho những ai?"
    assert reply.card is None


def test_error_fallback():
    tr = TurnResult(error="boom")
    reply = build_reply(tr)
    assert "lỗi" in reply.text.lower()
