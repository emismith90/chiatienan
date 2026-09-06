"""The tools every ledger business shares (Phase 6, review F2): who is who, what a
period is, the statements and summary, the settlement with its QR codes, the random
draw, proposing a cash payment, cancelling a pending card. Bodies are those of
``packs/lunch_ledger/tools.py`` before Task 6.1, with two things injected that were
lunch's: the QR fallback note and how another pack's pending card is described.

What the pack needs from the host's per-turn context (``ctx``), duck-typed:
``db.session()``, ``space_id``, ``sender_member_id``, ``turn_mentions``,
``unknown_names`` (mutated by ``find_members``), ``cards`` (``kernos.adapters.CardStore``),
``today()``, ``choice(seq)``.
"""
from __future__ import annotations

from datetime import date

from kernos.packs import PackTool, err as _err
from ledger_core import ledger, roster
from ledger_core.money import MoneyError, net_transfers
from ledger_core.notes import build_qr_note
from ledger_core.periods import resolve_date, resolve_period
from ledger_core.qr import QRError


def _parse_iso(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _names_for(session, space_id, ids) -> dict[int, str]:
    # include_inactive: a balance/settlement can reference a since-removed member.
    return {
        m.id: m.display_name
        for m in roster.list_members(session, space_id, include_inactive=True)
        if m.id in set(ids)
    }



# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #

_FIND_SCHEMA = {
    "type": "object",
    "properties": {
        "names": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Names to look up (e.g. ['An', 'Bình']). Pass the name EXACTLY as the user"
                " wrote it — the tool matches display name, nickname, aliases and the bank"
                " account holder, with or without Vietnamese tones, and strips 'anh'/'chị'"
                " itself. Do not de-accent or shorten it yourself."
            ),
        },
        "all_active": {
            "type": "boolean",
            "description": (
                "True to fetch the WHOLE roster — every active member of the room, with"
                " nobody filtered out. Use it for 'cả nhóm' / 'cả team' / 'mọi người' and"
                " for the English 'everyone' / 'all' / 'the whole group' (production said"
                " 'log this for all' and only the Vietnamese triggers were documented)."
            ),
        },
    },
}

_RANDOM_PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {
            "type": "string",
            "description": "What the pick is for, as the user said it ('trả tiền', 'đi mua đồ ăn'). Cosmetic only.",
        },
    },
}

_PERIOD_SCHEMA = {
    "type": "object",
    "properties": {
        "keyword": {
            "type": "string",
            "enum": ["since_last", "this_week", "last_week", "today", "yesterday", "this_month", "explicit"],
        },
        "from": {"type": "string", "description": "ISO date for keyword=explicit."},
        "to": {"type": "string", "description": "ISO date for keyword=explicit."},
    },
}

_PROPOSE_PAYMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "from": {"type": "integer", "description": "member id who paid; blank = the sender."},
        "to": {"type": "integer", "description": "member id who received the money."},
        "amount": {
            "type": "integer",
            "description": "Integer VND (125k → 125000). OMIT to pay off exactly what `from` currently owes `to`.",
        },
        "mode": {
            "type": "string",
            "enum": ["gross", "offset"],
            "description": "For a two-way pair only: 'gross' = pay the full amount `from` owes `to`; 'offset' = settle the net difference. Omit otherwise.",
        },
        "note": {"type": "string"},
    },
    "required": ["to"],
}

_SETTLE_SCHEMA = {
    "type": "object",
    "properties": {
        "keyword": {
            "type": "string",
            "enum": ["since_last", "this_week", "last_week", "today", "yesterday", "this_month", "explicit"],
        },
        "from": {"type": "string", "description": "ISO date. Supplying from/to means an explicit range; omit `keyword` (or pass 'explicit')."},
        "to": {"type": "string", "description": "ISO date, inclusive. See `from`."},
    },
}



def build(ctx, *, qr, fallback_note, describe_pending) -> dict[str, PackTool]:
    """The nine shared tools, closed over one turn's ``ctx``.

    ``qr(payee, amount, note) -> url`` builds the VietQR image link (raising
    :class:`ledger_core.qr.QRError` when the payee has no bank details);
    ``fallback_note(to_date) -> str`` is the bank memo when no record names the debt;
    ``describe_pending(session, space_id, kind, payload) -> dict`` describes another
    pack's pending card for the blocked-settle listing.
    """
    db = ctx.db

    def find_members(args, _tool_ctx=None) -> dict:
        args = args or {}
        names = list(args.get("names") or [])
        all_active = bool(args.get("all_active"))
        with db.session() as s:
            res = roster.resolve(s, ctx.space_id, names=names,
                                 mentions=ctx.turn_mentions, all_active=all_active)
        asked = names + [str(m.get("nickname") or "?") for m in ctx.turn_mentions]
        still_unknown = {str(n) for n in res["unresolved"]}
        still_unknown |= {str(a["name"]) for a in res["ambiguous"]}
        for raw in asked:
            if raw in still_unknown:
                ctx.unknown_names[raw] = "ambiguous" if raw not in res["unresolved"] else "unresolved"
            else:
                # Asked again and pinned down this time — no longer a hole.
                ctx.unknown_names.pop(raw, None)
        return {"ok": True, **res}

    def resolve_date_tool(args, _tool_ctx=None) -> dict:
        args = args or {}
        try:
            d = resolve_date(str(args.get("word") or ""), today=ctx.today())
        except ValueError as exc:
            return _err(str(exc))
        return {"ok": True, "date": d.isoformat()}

    def cancel_draft(args, _tool_ctx=None) -> dict:
        """Cancel a pending draft card by id. Writes nothing to the ledger.

        The one draft action the bot may take on the user's word: confirming
        still requires the button on the card (money-safety D3), but a stale
        proposal blocks every settle and used to need a human to scroll back and
        find the card. Cancelling loses nothing — the proposal can be re-made.
        """
        args = args or {}
        draft_id = args.get("draft_id")
        if not isinstance(draft_id, int):
            return _err("Missing draft_id (the # shown on the card).")
        try:
            m = ctx.cards.cancel(ctx.space_id, draft_id)
        except ValueError as exc:   # the store's "no such pending card" (LedgerError is one)
            return _err(str(exc))
        return {"ok": True, "type": "draft_cancelled", "draft_id": m.id, "kind": m.kind}

    def pick_random(args, _tool_ctx=None) -> dict:
        # The draw itself lives in the tool, never in the model — an LLM cannot
        # be trusted to be uniform (or unmanipulable). The visible body is built
        # server-side from `chosen`, so the winner can't be re-typed either.
        args = args or {}
        with db.session() as s:
            members = {
                m.id: m.display_name
                for m in roster.list_members(s, ctx.space_id, default_only=True)
            }
        # The pool is every default-participant member of the group — no
        # per-request subsetting (the tool takes no participant list), but a
        # member flagged out of default group activities (default_participant
        # = false) is skipped here.
        pool_ids = list(members)
        if not pool_ids:
            return _err("Không có ai trong nhóm để bốc.")
        chosen_id = ctx.choice(pool_ids)
        label = (args.get("label") or "").strip() or None
        return {
            "ok": True,
            "type": "random_pick",
            "chosen": {"id": chosen_id, "name": members[chosen_id]},
            "candidates": [{"id": i, "name": members[i]} for i in pool_ids],
            "label": label,
        }

    def resolve_period_tool(args, _tool_ctx=None) -> dict:
        args = args or {}
        with db.session() as s:
            last = ledger.last_settlement(s, ctx.space_id)
            try:
                period = resolve_period(
                    args.get("keyword"),
                    today=ctx.today(),
                    last_settlement_to=last.period_to if last else None,
                    explicit_from=_parse_iso(args.get("from")),
                    explicit_to=_parse_iso(args.get("to")),
                )
            except ValueError as exc:
                return _err(str(exc))
        return {
            "ok": True,
            "from": period["from"].isoformat() if period["from"] else None,
            "to": period["to"].isoformat(),
            "keyword": period["keyword"],
        }

    def member_statement(args, _tool_ctx=None) -> dict:
        args = args or {}
        member = args.get("member") or ctx.sender_member_id
        if not member:
            return _err("Không xác định được thành viên.")
        try:
            member = int(member)
        except (TypeError, ValueError):
            return _err("Không xác định được thành viên.")
        with db.session() as s:
            last = ledger.last_settlement(s, ctx.space_id)
            period = resolve_period(
                args.get("keyword"), today=ctx.today(),
                last_settlement_to=last.period_to if last else None,
            )
            stmt = ledger.statement_for(s, ctx.space_id, member, period["from"], period["to"])
            ids = {r["other_id"] for r in stmt["owe"]} | {r["other_id"] for r in stmt["owed"]} \
                | {member}
            names = _names_for(s, ctx.space_id, ids)

        def _row(r, key):
            other_id = r["other_id"]
            return {key: other_id, "name": names.get(other_id, "?"),
                    "meal_id": r["meal_id"], "dish": r["dish"],
                    "occurred_on": r["occurred_on"], "amount": r["amount"], "status": r["status"]}

        owe = [_row(r, "creditor_id") for r in stmt["owe"]]
        owed = [_row(r, "debtor_id") for r in stmt["owed"]]
        return {
            "ok": True, "type": "statement",
            "member": {"id": member, "name": names.get(member, "?")},
            "period": {"from": period["from"].isoformat() if period["from"] else None,
                       "to": period["to"].isoformat()},
            "owe": owe, "owed": owed,
        }

    def get_period_summary(args, _tool_ctx=None) -> dict:
        args = args or {}
        with db.session() as s:
            last = ledger.last_settlement(s, ctx.space_id)
            period = resolve_period(
                args.get("keyword"), today=ctx.today(),
                last_settlement_to=last.period_to if last else None,
            )
            timeline = ledger.period_timeline(s, ctx.space_id, period["from"], period["to"])
            outstanding = ledger.outstanding_pairs(s, ctx.space_id, period["from"], period["to"])
            ids = {r["debtor_id"] for r in outstanding} | {r["creditor_id"] for r in outstanding} \
                | {e.get("payer_id") for e in timeline} \
                | {e.get("from_id") for e in timeline} | {e.get("to_id") for e in timeline}
            ids.discard(None)
            names = _names_for(s, ctx.space_id, ids)
        for e in timeline:
            if e["kind"] == "meal":
                e["payer_name"] = names.get(e["payer_id"], "?")
            else:
                e["from_name"] = names.get(e["from_id"], "?")
                e["to_name"] = names.get(e["to_id"], "?")
        return {
            "ok": True, "type": "summary",
            "period": {"from": period["from"].isoformat() if period["from"] else None,
                       "to": period["to"].isoformat()},
            "timeline": timeline,
            "outstanding": [{**r,
                             "debtor_name": names.get(r["debtor_id"], "?"),
                             "creditor_name": names.get(r["creditor_id"], "?")}
                            for r in outstanding],
        }

    def propose_payment(args, _tool_ctx=None) -> dict:
        args = args or {}
        to = args.get("to")
        frm = args.get("from") or ctx.sender_member_id
        if not frm:
            return _err("Không xác định được người trả.")
        if not to:
            return _err("Thiếu người nhận.")
        try:
            frm_id, to_id = int(frm), int(to)
        except (TypeError, ValueError):
            return _err("from/to không hợp lệ.")
        if frm_id == to_id:
            return _err("Người trả và người nhận phải khác nhau.")
        amount = args.get("amount")
        if amount is not None and not isinstance(amount, int):
            return _err("amount phải là số nguyên VND.")
        with db.session() as s:
            names = _names_for(s, ctx.space_id, [frm_id, to_id])
            # _names_for returns only the ids that are real room members, so a
            # hallucinated from/to would be missing here — reject it before the
            # pay-off path can falsely report payment_settled.
            if frm_id not in names or to_id not in names:
                return _err("Không tìm thấy thành viên trong nhóm.")
            if amount is None:
                # Gross directional pay-off over the open (since_last) period. We
                # do NOT net A<->B: a real cash payment settles what `from` owes
                # `to`, per meal. Netting is only for settle_period's QR.
                last = ledger.last_settlement(s, ctx.space_id)
                period = resolve_period(
                    "since_last", today=ctx.today(),
                    last_settlement_to=last.period_to if last else None,
                )
                edges = ledger.debt_breakdown(s, ctx.space_id, period["from"], period["to"])
                gross_ft = sum(e.outstanding for e in edges
                               if e.debtor == frm_id and e.creditor == to_id)
                gross_tf = sum(e.outstanding for e in edges
                               if e.debtor == to_id and e.creditor == frm_id)
                mode = args.get("mode")

                if gross_ft <= 0 and gross_tf <= 0:
                    return {"ok": True, "type": "payment_settled",
                            "from": {"id": frm_id, "name": names.get(frm_id, "?")},
                            "to": {"id": to_id, "name": names.get(to_id, "?")}}
                if gross_ft > 0 and gross_tf <= 0:
                    amount = gross_ft
                elif gross_ft <= 0 and gross_tf > 0:
                    return {"ok": True, "type": "nothing_owed",
                            "from": {"id": frm_id, "name": names.get(frm_id, "?")},
                            "to": {"id": to_id, "name": names.get(to_id, "?")},
                            "reverse_amount": gross_tf}
                elif mode == "gross":
                    amount = gross_ft
                elif mode == "offset":
                    net = gross_ft - gross_tf
                    if net == 0:
                        return {"ok": True, "type": "payment_settled",
                                "from": {"id": frm_id, "name": names.get(frm_id, "?")},
                                "to": {"id": to_id, "name": names.get(to_id, "?")}}
                    if net > 0:
                        amount = net
                    else:  # net direction flips: to -> frm
                        frm_id, to_id = to_id, frm_id
                        amount = -net
                else:
                    return {
                        "ok": True, "type": "payment_ambiguous",
                        "from": {"id": frm_id, "name": names.get(frm_id, "?")},
                        "to": {"id": to_id, "name": names.get(to_id, "?")},
                        "gross": {"from_member_id": frm_id, "to_member_id": to_id, "amount": gross_ft},
                        "offset": (
                            {"from_member_id": frm_id, "to_member_id": to_id, "amount": gross_ft - gross_tf}
                            if gross_ft >= gross_tf else
                            {"from_member_id": to_id, "to_member_id": frm_id, "amount": gross_tf - gross_ft}
                        ),
                    }
            if amount <= 0:
                return _err("Số tiền phải lớn hơn 0.")
        return {
            "ok": True,
            "type": "payment_draft",
            "from_member_id": frm_id,
            "to_member_id": to_id,
            "amount": amount,
            "note": args.get("note"),
            "from_name": names.get(frm_id, "?"),
            "to_name": names.get(to_id, "?"),
        }

    def settle_period(args, _tool_ctx=None) -> dict:
        """Who owes whom right now, with QR codes: edges → net → QR → payload.

        Read-only. It computes a running total and writes NOTHING — there is no
        period-closing feature, so `settlements` stays empty and every window
        keeps the whole ledger behind it. The tool used to take a `commit` flag
        that wrote a Settlement row; nobody ever passed it, and the day someone
        had, `resolve_period("since_last")` would have flipped from "the whole
        ledger" to a bounded window for the ledger panel, quick-pay, and every
        statement at once. Reintroduce it deliberately or not at all (see
        ledger.record_settlement, kept for that purpose).
        """
        args = args or {}
        pending = ctx.cards.pending(ctx.space_id)
        with db.session() as s:
            if pending:
                # Each pending card is described by the pack that owns its kind
                # (`DraftKind.summary` — Phase 6 review F2b); this pack knows none by name.
                summaries = [{"draft_id": d.id, **describe_pending(s, ctx.space_id, d.kind, d.attachments or {})}
                             for d in pending]
                return {
                    "ok": True,
                    "type": "settle_blocked",
                    "pending": summaries,
                    "message": f"Có {len(pending)} đề xuất chưa xác nhận — xác nhận hoặc huỷ trước khi tính.",
                }

            last = ledger.last_settlement(s, ctx.space_id)
            try:
                period = resolve_period(
                    args.get("keyword"),
                    today=ctx.today(),
                    last_settlement_to=last.period_to if last else None,
                    explicit_from=_parse_iso(args.get("from")),
                    explicit_to=_parse_iso(args.get("to")),
                )
            except ValueError as exc:
                return _err(str(exc))

            from_date, to_date = period["from"], period["to"]
            # One computation behind the amounts, the per-meal QR notes and the
            # "đã cân bằng" verdict. These edges carry FIFO-attributed payments,
            # so `outstanding > 0` is exactly "still being repaid" — which is
            # also why the note never names a meal that is already settled.
            open_edges = [e for e in ledger.debt_breakdown(s, ctx.space_id, from_date, to_date)
                          if e.outstanding > 0]
            transfers = net_transfers(open_edges)

            # Gated on the transfers themselves, not on period_balances: that
            # number used to disagree with this one on a bounded window, so the
            # room could be told "mọi người đã cân bằng" with transfers pending,
            # or handed an empty transfer list with no explanation at all.
            if not transfers:
                return {
                    "ok": True,
                    "period": {"from": from_date.isoformat() if from_date else None, "to": to_date.isoformat()},
                    "transfers": [],
                    "message": "Mọi người đã cân bằng — không ai nợ ai trong kỳ này.",
                }

            # include_inactive: a transfer may involve a since-removed member.
            members = {m.id: m for m in roster.list_members(s, ctx.space_id, include_inactive=True)}
            fallback = fallback_note(to_date)

            rows: list[dict] = []
            warnings: list[str] = []
            for t in transfers:
                payee = members.get(t.to_member)
                payer = members.get(t.from_member)
                # Meals the payee (creditor) fronted that this debtor still owes
                # on — the "what you're repaying" list for this transfer.
                pair_meals = [
                    {"date": e.occurred_on, "dish": e.dish}
                    for e in open_edges
                    if e.debtor == t.from_member and e.creditor == t.to_member
                ]
                note = build_qr_note(
                    payer.display_name if payer else "",
                    pair_meals,
                    fallback=fallback,
                )
                row = {
                    "from_id": t.from_member,
                    "from_name": payer.display_name if payer else "?",
                    "to_id": t.to_member,
                    "to_name": payee.display_name if payee else "?",
                    "amount": t.amount,
                    "note": note,
                    "qr_url": None,
                }
                try:
                    row["qr_url"] = qr(payee, t.amount, note)
                except QRError as exc:
                    warnings.append(str(exc))
                rows.append(row)

            # Nothing is written. See the tool description: this is a running
            # total, not a closing entry.

        return {
            "ok": True,
            "period": {"from": from_date.isoformat() if from_date else None, "to": to_date.isoformat()},
            "transfers": rows,
            "warnings": warnings,
        }
    specs = {
        "find_members": dict(
            execute=find_members,
            description=("Look up member ids by name/nickname/real name/bank-account name, or the"
                         " whole group (all_active — every active member, no exceptions). Returns"
                         " `unresolved` (nobody by that name) and `ambiguous` (two people match —"
                         " ask which one); neither may be ignored."),
            input_schema=_FIND_SCHEMA,
        ),
        "cancel_draft": dict(
            execute=cancel_draft,
            description=(
                "Cancel a PENDING draft card by its # (e.g. a stale proposal blocking "
                "settle_period). Records nothing. Confirming a draft is NOT possible from "
                "chat — only the Confirm button on the card can do that."
            ),
            input_schema={
                "type": "object",
                "properties": {"draft_id": {"type": "integer",
                                            "description": "The # shown on the draft card."}},
                "required": ["draft_id"],
            },
        ),
        "pick_random": dict(
            execute=pick_random,
            description="Randomly pick ONE member of the group ('bốc thăm', 'random ai trả', 'chọn đại một người'). Draws from default-participant members only (see update_member's default_participant flag) — no per-request subsetting. The tool does the draw — never pick yourself.",
            input_schema=_RANDOM_PICK_SCHEMA,
        ),
        "resolve_period": dict(
            execute=resolve_period_tool,
            description="Turn a time keyword (since_last/this_week/...) into a concrete date range (ICT).",
            input_schema=_PERIOD_SCHEMA,
        ),
        "resolve_date": dict(
            execute=resolve_date_tool,
            description="Turn a day word ('thứ 2', 'hôm qua', '20/7') into an ISO date (ICT). Use before propose_meal when the user names a day.",
            input_schema={"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]},
        ),
        "member_statement": dict(
            execute=member_statement,
            description="A person's own statement: what they owe + are owed, per meal, with paid/unpaid status. There is no net/ròng figure — report the owe and owed rows as they are. Default member = the sender. Use for first-person questions ('tôi nợ ai', 'how much do I owe').",
            input_schema={"type": "object", "properties": {
                "member": {"type": "integer", "description": "member id; blank = the sender."},
                "keyword": _PERIOD_SCHEMA["properties"]["keyword"],
            }},
        ),
        "get_period_summary": dict(
            execute=get_period_summary,
            description="Group summary: chronological timeline of meals + payments plus every open 'X owes Y' row (display only). Use for 'summary'/'current state'/'tổng kết'.",
            input_schema={"type": "object", "properties": {"keyword": _PERIOD_SCHEMA["properties"]["keyword"]}},
        ),
        "settle_period": dict(
            execute=settle_period,
            description=(
                "Compute who pays whom right now + build VietQR codes for a period. "
                "READ-ONLY: it is a running total ('tạm tính'), it does NOT close or reset "
                "anything. If the user asks to chốt/reset, show this and say plainly that "
                "closing a period is not supported yet."
            ),
            input_schema=_SETTLE_SCHEMA,
        ),
        "propose_payment": dict(
            execute=propose_payment,
            description=(
                "Propose a cash payment one member made to another for the user to confirm "
                "(e.g. 'A trả B 100k', 'A đã trả B'). Does NOT write the ledger. FINAL TOOL for a "
                "payment. Omit `amount` to pay off exactly what `from` owes `to`."
            ),
            input_schema=_PROPOSE_PAYMENT_SCHEMA,
        ),
    }

    return {name: PackTool(name, spec["description"], spec["input_schema"], spec["execute"])
            for name, spec in specs.items()}
