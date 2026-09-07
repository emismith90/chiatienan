"""The shared ledger's deterministic reply bodies and outcome decision (design D3).

A money reply is built server-side from the tool-result dict, never from the
model's prose, so the visible text can never disagree with the QR/attachment
numbers. Bodies are those of ``packs/lunch_ledger/render.py`` before Task 6.1; the
summary now counts events by ``kind`` and the blocked-settle listing describes cards
of any kind (Phase 6 review F2).
"""
from __future__ import annotations

from kernos.kernel import Body, Draft


def render_bot_attachments(result) -> dict | None:
    settle = result.last_result("settle_period")
    if settle:
        if settle.get("type") == "settle_blocked":
            return dict(settle)
        return {"type": "settlement", **settle}
    statement = result.last_result("member_statement")
    if statement and statement.get("type") == "statement":
        return {"type": "statement", **statement}
    summary = result.last_result("get_period_summary")
    if summary and summary.get("type") == "summary":
        return {"type": "summary", **summary}
    pick = result.last_result("pick_random")
    if pick and pick.get("type") == "random_pick":
        return {"type": "random_pick", **pick}
    return None


def _settlement_body(attachments: dict) -> str:
    """Deterministic summary of a settlement, straight from the tool-result dict —
    never from LLM prose (design D3, money-safety)."""
    period = attachments.get("period") or {}
    p_from, p_to = period.get("from"), period.get("to")
    # "Provisional", not "Settled": nothing is recorded and no period closes, so a
    # header that reads like a closing entry was telling the room the books had
    # been ruled off when `settlements` had been empty since the ledger began.
    header = (f"Provisional {p_from} → {p_to}:" if p_from
              else f"Provisional through {p_to}:")

    transfers = attachments.get("transfers") or []
    lines = [header]
    if transfers:
        # The memo rides along the QR as the bank's addInfo, and it is the part
        # people dispute ("sai nội dung chuyển khoản r"). It was only ever in the
        # attachment, so nobody could see what it said without opening the card.
        lines.extend(
            f"{t['from_name']} → {t['to_name']}: {t['amount']:,}đ"
            + (f" · ref: {t['note']}" if t.get("note") else "")
            for t in transfers
        )
    else:
        lines.append(attachments.get("message") or "Nobody owes anybody.")

    for w in attachments.get("warnings") or []:
        lines.append(f"⚠️ {w}")
    return "\n".join(lines)


def _settle_blocked_body(attachments: dict) -> str:
    """Deterministic summary of a blocked settle (pending drafts must be
    confirmed/cancelled first), straight from the tool-result dict — never from
    LLM prose (design D3, money-safety)."""
    lines = [attachments.get("message") or "There are drafts still unconfirmed."]
    for p in attachments.get("pending") or []:
        if p.get("kind") == "payment":
            parts = ", ".join(
                f"{t['from_name']}→{t['to_name']} {t['amount']:,}đ" for t in (p.get("transfers") or [])
            )
            lines.append(f"• #{p['draft_id']}: {parts}")
        elif p.get("kind") in (None, "meal"):          # a meal draft (the shape before kinds carried a name)
            lines.append(
                f"• #{p['draft_id']}: {p.get('payer_name', '?')} paid "
                f"{p.get('bill_total', 0):,}đ ({p.get('participant_count', 0)} people)"
            )
        else:
            lines.append(f"• #{p['draft_id']}: {p.get('label') or p.get('kind') or 'a pending card'}")
    # Production: this listed the blocking draft and stopped there, so people
    # asked the bot to close it four different ways instead of scrolling up to
    # the card. Say where the buttons are, and that chat can cancel it.
    if attachments.get("pending"):
        # The buttons are named as the card renders them, in English — an
        # instruction that names a control the room cannot find is worse than none.
        lines.append(
            "Open the draft card above (by its # number) and press **Confirm** or "
            '**Cancel** — or say "huỷ đề xuất #<số>" and I will cancel it for you.'
        )
    return "\n".join(lines)


def _statement_body(att: dict) -> str:
    """Deterministic text for a personal statement — numbers from the tool dict.

    Two sections and no total. The old closing line, "Ròng: -54.500đ", was the
    one number in the reply nobody could act on: it is not what you send anyone,
    it is not what anyone sends you, and when the two sections disagreed with it
    (a debt in each direction) it read as though they had been cancelled out.
    What you owe and what you are owed, per person per meal, is the whole answer.
    """
    name = (att.get("member") or {}).get("name", "?")
    lines = [f"{name} — owes and is owed:"]
    owe = att.get("owe") or []
    owed = att.get("owed") or []
    if owe:
        lines.append("You owe:")
        lines += [f"• {r['name']} {r['amount']:,}đ ({r.get('dish') or 'meal'}"
                  f"{' – paid' if r['status'] == 'paid' else ''})" for r in owe]
    if owed:
        lines.append("You are owed:")
        lines += [f"• {r['name']} {r['amount']:,}đ ({r.get('dish') or 'meal'})" for r in owed]
    if not owe and not owed:
        lines.append("You owe nobody, and nobody owes you.")
    return "\n".join(lines)


def _summary_body(att: dict) -> str:
    """One-line headline for a group summary — numbers from the tool dict.

    This used to print every row. Fifteen transactions arrived as one unbroken
    paragraph, and when someone asked for it "day by day, as bullet points" they
    got the identical blob back, because the body is rendered server-side and
    cannot honour a formatting request. The detail belongs in the card below it
    (grouped by day) and in the ledger panel the card can open — a chat bubble is
    the wrong surface for a table.
    """
    period = att.get("period") or {}
    timeline = att.get("timeline") or []
    kinds: dict[str, int] = {}
    for e in timeline:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    meals, payments = kinds.pop("meal", 0), kinds.pop("payment", 0)
    window = (f"{period.get('from')} → {period.get('to')}" if period.get("from")
              else f"through {period.get('to')}")
    if not timeline:
        return f"Summary {window}: no transactions in this period."
    parts = []
    if meals:
        parts.append(f"{meals} meal{'' if meals == 1 else 's'}")
    if payments:
        parts.append(f"{payments} payment{'' if payments == 1 else 's'}")
    for kind in sorted(kinds):                       # other businesses' events, by kind
        parts.append(f"{kinds[kind]} {kind}{'' if kinds[kind] == 1 else 's'}")
    days = len({e.get("occurred_on") for e in timeline})
    return (f"Summary {window}: {', '.join(parts)} across {days} "
            f"day{'' if days == 1 else 's'} — details below.")


def _random_pick_body(att: dict) -> str:
    """Deterministic text for a random draw — the winner comes from the tool dict,
    never the LLM, so it can't be re-typed into a different name."""
    chosen = att.get("chosen") or {}
    n = len(att.get("candidates") or [])
    label = att.get("label")
    tail = f" ({label})" if label else ""
    return f"🎲 Picked{tail}: **{chosen.get('name', '?')}** — from {n} people."


#: Fields the render stage copies from a `propose_meal` result into the card.
def _payment_body(attachments: dict) -> str:
    """Deterministic summary of recorded payment(s), from the tool/commit dict —
    never LLM prose (money-safety)."""
    transfers = attachments.get("transfers") or []
    if not transfers:
        return "💸 Payment recorded."
    lines = [f"{t['from']['name']} paid {t['to']['name']} {t['amount']:,}đ" for t in transfers]
    return "💸 " + lines[0] if len(lines) == 1 else "💸 Recorded:\n" + "\n".join(lines)



def decide(result) -> Draft | Body | None:
    """Payment proposals become a draft card; settle/statement/summary/random-pick
    results become a typed body. Anything else is not this pack's call."""
    # Collapse multiple proposals for the SAME (from,to) pair to the LAST one (a
    # model self-correction "100k… actually 150k"), preserving order.
    by_pair: dict[tuple[int, int], dict] = {}
    for p in result.all_results("propose_payment"):
        if p.get("type") == "payment_draft":
            by_pair[(p["from_member_id"], p["to_member_id"])] = {
                "from_member_id": p["from_member_id"], "to_member_id": p["to_member_id"],
                "amount": p["amount"], "note": p.get("note")}
    if by_pair:
        return Draft("payment_draft", {"transfers": list(by_pair.values())})

    attachments = render_bot_attachments(result)
    kind = attachments.get("type") if attachments else None
    body = {
        "settlement": _settlement_body, "settle_blocked": _settle_blocked_body,
        "statement": _statement_body, "summary": _summary_body,
        "random_pick": _random_pick_body,
    }.get(kind)
    if body is None:
        return None
    return Body(body(attachments), attachments, claimed_by_pack=True)
