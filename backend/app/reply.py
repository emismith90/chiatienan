"""Render a Teams reply from a :class:`~app.agent.TurnResult`.

Pure, deterministic, and — crucially — money-bearing replies are built from the
**structured tool results**, never from the LLM's free text. A settlement's
amounts + QR URLs come straight out of ``settle_period``; a meal breakdown comes
straight out of ``record_meal``. Only non-money turns (clarifying questions,
"how much did I spend", errors) fall back to the model's text (D3).

Returns a :class:`Reply` = ``text`` (always) + optional ``card`` (an Adaptive
Card dict for a settlement). ``teams.py`` attaches the card; tests assert on the
dict directly.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.agent import TurnResult

_ADAPTIVE_CARD_VERSION = "1.4"
_FALLBACK = "Xin lỗi, mình chưa xử lý được. Bạn thử nói lại rõ hơn nhé."


@dataclass
class Reply:
    text: str
    card: dict | None = None


def fmt_vnd(amount: int) -> str:
    """Compact VND: exact-thousands as ``840k``, else grouped ``116.500đ``."""
    amount = int(amount)
    if amount % 1000 == 0:
        return f"{amount // 1000}k"
    return f"{amount:,.0f}đ".replace(",", ".")


def build_reply(result: TurnResult) -> Reply:
    settle = result.last_result("settle_period")
    if settle is not None:
        return _settlement_reply(settle)

    record = result.last_result("record_meal")
    if record is not None:
        return Reply(text=_meal_breakdown(record))

    void = result.last_result("void_meal")
    if void is not None:
        return Reply(text=f"Đã xoá bữa #{void.get('meal_id')} ✅")

    balances = result.last_result("get_period_balances")
    if balances is not None:
        return Reply(text=_balances_text(balances))

    if result.final_text:
        return Reply(text=result.final_text)

    if result.error:
        return Reply(text="Có lỗi khi xử lý, bạn thử lại sau nhé. 🙏")

    return Reply(text=_FALLBACK)


def _meal_breakdown(record: dict) -> str:
    total = record.get("total_amount", 0)
    payer = record.get("payer", {}).get("name", "?")
    shares = record.get("shares", [])
    parts = [f"{sh['name']} {fmt_vnd(sh['amount'])}" for sh in shares]
    body = " • ".join(parts) if parts else "—"
    return (
        f"Tổng {fmt_vnd(total)} • {body} • "
        f"{payer} trả • đã ghi #{record.get('meal_id')} ✅"
    )


def _balances_text(balances: dict) -> str:
    rows = balances.get("balances", [])
    if not rows:
        return "Chưa có chi tiêu nào trong kỳ này."
    lines = []
    for r in rows:
        bal = r["balance"]
        if bal > 0:
            lines.append(f"• {r['name']}: được nhận {fmt_vnd(bal)}")
        elif bal < 0:
            lines.append(f"• {r['name']}: còn nợ {fmt_vnd(-bal)}")
        else:
            lines.append(f"• {r['name']}: cân bằng")
    return "Số dư trong kỳ:\n" + "\n".join(lines)


def _period_label(period: dict) -> str:
    frm, to = period.get("from"), period.get("to")
    if frm:
        return f"{frm} → {to}"
    return f"đến {to}"


def _settlement_reply(settle: dict) -> Reply:
    transfers = settle.get("transfers") or []
    period = settle.get("period", {})
    committed = settle.get("committed")

    if not transfers:
        msg = settle.get("message") or "Không có gì để chốt trong kỳ này."
        return Reply(text=msg)

    header = "Đã chốt" if committed else "Ai trả ai"
    summary_lines = [
        f"{t['from_name']} → {t['to_name']}: {fmt_vnd(t['amount'])}" for t in transfers
    ]
    text = f"{header} ({_period_label(period)}):\n" + "\n".join(summary_lines)
    if committed:
        text += "\n\n🔒 Kỳ này đã được chốt."

    return Reply(text=text, card=_settlement_card(settle))


def _settlement_card(settle: dict) -> dict:
    transfers = settle.get("transfers") or []
    period = settle.get("period", {})
    committed = settle.get("committed")

    title = "🔒 Đã chốt tiền ăn" if committed else "💸 Ai trả ai"
    body: list[dict] = [
        {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True},
        {"type": "TextBlock", "text": _period_label(period), "isSubtle": True, "size": "Small", "wrap": True},
    ]

    for t in transfers:
        body.append(
            {
                "type": "TextBlock",
                "text": f"**{t['from_name']}** trả **{t['to_name']}**: {fmt_vnd(t['amount'])}",
                "wrap": True,
                "spacing": "Medium",
            }
        )
        if t.get("qr_url"):
            body.append({"type": "Image", "url": t["qr_url"], "size": "Medium", "spacing": "None"})
        else:
            body.append(
                {
                    "type": "TextBlock",
                    "text": "⚠️ Chưa tạo được QR (thiếu thông tin ngân hàng — nhờ admin cập nhật).",
                    "wrap": True,
                    "color": "Attention",
                    "size": "Small",
                }
            )

    for w in settle.get("warnings") or []:
        body.append({"type": "TextBlock", "text": f"⚠️ {w}", "wrap": True, "color": "Warning", "size": "Small"})

    body.append(
        {
            "type": "TextBlock",
            "text": "Quét mã bằng app ngân hàng, hoặc nhập ảnh từ thư viện nếu mã đang ở máy này.",
            "isSubtle": True,
            "wrap": True,
            "size": "Small",
            "spacing": "Medium",
        }
    )

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": _ADAPTIVE_CARD_VERSION,
        "body": body,
    }
