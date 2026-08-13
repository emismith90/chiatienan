"""The LLM-facing ``CustomTool`` set — where every number lives.

The model decides *when* to call these; the tools own all arithmetic and all
QR-building (design D3). Each tool opens its own short-lived DB session, so a
turn that fails before ``settle_period`` commits never half-writes.
``propose_meal`` never writes at all — it only returns a draft payload for the
user to confirm; the deterministic commit happens elsewhere via
``ledger.record_meal``. Validation failures are returned as
``{"ok": False, "error": ...}`` dicts (a clarifying-question result) rather
than raised, so the model can ask the user instead of guessing.

Numbers that end up in a QR are computed and rendered entirely inside
``settle_period`` — they never round-trip tool → LLM → tool.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import date


from app import accounts, ledger, roster, rooms
from app.clock import today_ict
from app.db import Database
from app.money import (
    MoneyError,
    itemized_adjustments,
    net_transfers,
    normalize_items,
    prorate_items,
    split_with_guests,
)
from app.notes import build_qr_note
from app.periods import resolve_date, resolve_period
from app.qr import QRError, make_qr_url

logger = logging.getLogger("chiatienan")


@dataclass
class CustomTool:
    """The LLM-facing tool shape, owned here now that the vendor SDK is gone.

    Same three fields the SDK's class carried, so all 14 registrations below and
    every executor body are byte-identical to what they were. Nothing about
    arithmetic changed with the engine.
    """

    execute: object
    description: str
    input_schema: dict


@dataclass
class ToolContext:
    """Per-turn context the tools close over (never seen by the model).

    Room-scoped: every tool call is confined to ``room_id``, and the sender is
    whoever is logged in for this PWA session (``sender_member_id``) — a plain
    room member id, not any external chat-platform identity.
    """

    db: Database
    room_id: int
    sender_member_id: int | None = None
    sender_name: str | None = None
    # People @mentioned in this message (bot mention already stripped):
    turn_mentions: list[dict] = field(default_factory=list)


def _err(message: str) -> dict:
    return {"ok": False, "error": message}


def _parse_iso(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _names_for(session, room_id, ids) -> dict[int, str]:
    # include_inactive: a balance/settlement can reference a since-removed member.
    return {
        m.id: m.display_name
        for m in roster.list_members(session, room_id, include_inactive=True)
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
            "description": "Names or nicknames to look up (e.g. ['An', 'Bình']).",
        },
        "all_active": {
            "type": "boolean",
            "description": "True to fetch all active members ('cả nhóm').",
        },
    },
}

_PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "payer": {"type": "integer", "description": "member id of the payer; blank = the sender."},
        "participants": {"type": "array", "items": {"type": "integer"},
                         # "tôi với Bình ăn" listed only Bình on one run, which
                         # charges a two-person bill to one person. The sender is
                         # a participant like anyone else — being the payer does
                         # not put them in, and saying "tôi" does not leave them out.
                         "description": "member ids of EVERYONE who ate, the sender included"
                                        " when they ate ('tôi với Bình ăn' = both ids)."},
        "total": {"type": "integer", "description": "Bill total, integer VND (840k → 840000)."},
        "guests": {"type": "array", "items": {"type": "string"},
                   "description": "Guest names (non-members who pay cash)."},
        "adjustments": {"type": "array", "items": {
            "type": "object",
            "properties": {"member": {"type": "integer"}, "amount": {"type": "integer"}},
            "required": ["member", "amount"]}},
        "items": {
            "type": "array",
            "description": (
                "Per-person mode ('ai ăn nấy trả'): the LIST price of what each person ate,"
                " copied straight off the bill — do NOT pre-apply the discount or split the"
                " difference yourself. One entry per participant, every participant exactly"
                " once. The tool prorates the gap between Σ items and `total` (promo, ship,"
                " service fee) across the items. Omit this to split the bill evenly."
                " **Only when you KNOW who ate what** — the user said so, or the bill writes a"
                " name next to each line. A bill that merely lists dishes does not say who"
                " ordered them: guessing changes what each person owes and looks correct while"
                " being invented. Split evenly instead."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "member": {"type": "integer", "description": "member id who ate this."},
                    "amount": {"type": "integer", "description": "Its price on the bill, integer VND."},
                    "label": {"type": "string", "description": "Dish name, e.g. 'cơm tấm'."},
                },
                "required": ["member", "amount"],
            },
        },
        "discount_split": {
            "type": "string",
            "enum": ["proportional", "equal"],
            "description": (
                "Only with `items`. How to share the gap between Σ items and `total`:"
                " 'proportional' (default) scales each dish price; 'equal' takes the same"
                " amount off everyone — use it when the user says so ('chia đều phần giảm',"
                " 'mỗi người trừ như nhau'). Either way the TOOL does the arithmetic."
            ),
        },
        "dish": {"type": "string", "description": "Dish (if the user mentioned it)."},
        "initiator": {"type": "string", "description": "Who initiated the meal (if any)."},
        "note": {"type": "string", "description": "Free-form note (e.g. 'An đổi ý')."},
        "day_word": {"type": "string", "description": "The day EXACTLY as the user said it ('thứ 5', 'hôm qua', '20/7'). The tool resolves it to a date (ICT) — never compute the date yourself. Omit = today."},
        "occurred_on": {"type": "string", "description": "Deprecated: pre-resolved meal date, ISO YYYY-MM-DD. Prefer `day_word` so the tool does the date math. Ignored when `day_word` is given."},
    },
    "required": ["participants", "total"],
}

_VOID_SCHEMA = {
    "type": "object",
    "properties": {"meal_id": {"type": "integer"}},
    "required": ["meal_id"],
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

_ADD_MEMBER_SCHEMA = {
    "type": "object",
    "properties": {
        "display_name": {"type": "string", "description": "Display name."},
        "nickname": {"type": "string", "description": "Nickname used to sign in, unique within the room."},
        "bank_code": {"type": "string"},
        "account_number": {"type": "string"},
        "account_holder": {"type": "string"},
    },
    "required": ["display_name", "nickname"],
}

_UPDATE_MEMBER_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": ["string", "integer"], "description": "Member to update: nickname or numeric id."},
        "display_name": {"type": "string"},
        "nickname": {"type": "string", "description": "New nickname; must be unique in the room."},
        "bank_code": {"type": "string"},
        "account_number": {"type": "string"},
        "account_holder": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "active": {"type": "boolean", "description": "Set true to restore a previously removed member."},
        "default_participant": {
            "type": "boolean",
            "description": (
                "Whether this member is swept into a 'whole group' chia tien split or "
                "rot tra draw by default. Set false to exclude an irregular/casual member "
                "— they can still be added to a specific activity by name. Defaults true."
            ),
        },
    },
    "required": ["target"],
}

_DELETE_MEMBER_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": ["string", "integer"], "description": "Member to remove: nickname or numeric id."},
    },
    "required": ["target"],
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


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #

def build_tools(ctx: ToolContext) -> dict[str, CustomTool]:
    db = ctx.db

    def find_members(args, _tool_ctx=None) -> dict:
        args = args or {}
        names = list(args.get("names") or [])
        all_active = bool(args.get("all_active"))
        with db.session() as s:
            return {
                "ok": True,
                **roster.resolve(s, ctx.room_id, names=names, mentions=ctx.turn_mentions, all_active=all_active),
            }

    def propose_meal(args, _tool_ctx=None) -> dict:
        args = args or {}
        try:
            participants = [int(p) for p in (args.get("participants") or [])]
        except (TypeError, ValueError):
            return _err("Invalid participant list.")
        total = args.get("total")
        if not isinstance(total, int):
            return _err("Missing total (integer VND).")
        if not participants:
            return _err("No participants provided.")
        guests = [str(g) for g in (args.get("guests") or [])]
        adjustments = {}
        for adj in args.get("adjustments") or []:
            try:
                adjustments[int(adj["member"])] = int(adj["amount"])
            except (KeyError, TypeError, ValueError):
                return _err("Each adjustment must have numeric {member, amount}.")
        payer = args.get("payer") or ctx.sender_member_id
        if not payer:
            return _err("Could not determine the payer.")
        # Date resolution is authoritative here (like money-safety for amounts):
        # the model passes the user's day *word* and the tool computes the date,
        # so an LLM-computed occurred_on can never land a day off.
        day_word = args.get("day_word")
        occurred_on = args.get("occurred_on")
        if day_word:
            try:
                occurred_on = resolve_date(str(day_word), today=today_ict()).isoformat()
            except ValueError as exc:
                return _err(str(exc))
        elif occurred_on is not None:
            try:
                _parse_iso(occurred_on)
            except ValueError:
                return _err("Ngày không hợp lệ (cần dạng YYYY-MM-DD).")

        items = args.get("items") or []
        discount_split = (args.get("discount_split") or "proportional").strip().lower()
        if items:
            if adjustments:
                return _err(
                    "Dùng `items` HOẶC `adjustments`, không dùng cả hai — "
                    "`items` đã là số tiền của từng người rồi."
                )
            if guests:
                return _err(
                    "Ghi theo món chưa hỗ trợ khách lẻ. Bỏ khách ra (chia đều), "
                    "hoặc ghi khách như một dòng món của người trả hộ."
                )
            try:
                items = normalize_items(items, participants)
                shares = prorate_items(
                    total, {i["member"]: i["amount"] for i in items},
                    discount_split=discount_split,
                )
                adjustments = itemized_adjustments(total, shares)
            except MoneyError as exc:
                return _err(str(exc))

        try:
            preview = split_with_guests(total, participants, len(guests), adjustments, payer_id=int(payer))
        except MoneyError as exc:
            return _err(str(exc))
        return {
            "ok": True,
            "type": "expense_draft",
            "payer_member_id": int(payer),
            "member_participants": participants,
            "guests": guests,
            "bill_total": total,
            "adjustments": [{"member": m, "amount": a} for m, a in adjustments.items()],
            "items": items,
            "discount_split": discount_split if items else None,
            "dish": args.get("dish"),
            "initiator": args.get("initiator"),
            "note": args.get("note"),
            "per_head_preview": preview["per_head"],
            "shares_preview": [{"member": m, "amount": a} for m, a in preview["shares"].items()],
            "occurred_on": occurred_on,
        }

    def resolve_date_tool(args, _tool_ctx=None) -> dict:
        args = args or {}
        try:
            d = resolve_date(str(args.get("word") or ""), today=today_ict())
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
        from app import drafts

        args = args or {}
        draft_id = args.get("draft_id")
        if not isinstance(draft_id, int):
            return _err("Missing draft_id (the # shown on the card).")
        with db.session() as s:
            try:
                m = drafts.update_draft(s, draft_id, ctx.room_id, {"status": "cancelled"})
            except (ledger.LedgerError, MoneyError) as exc:
                return _err(str(exc))
            return {"ok": True, "type": "draft_cancelled", "draft_id": m.id, "kind": m.kind}

    def void_meal(args, _tool_ctx=None) -> dict:
        args = args or {}
        meal_id = args.get("meal_id")
        if not isinstance(meal_id, int):
            return _err("Missing meal_id.")
        with db.session() as s:
            try:
                return {
                    "ok": True,
                    **ledger.void_meal(s, meal_id, room_id=ctx.room_id, by=str(ctx.sender_member_id)),
                }
            except ledger.LedgerError as exc:
                return _err(str(exc))

    def pick_random(args, _tool_ctx=None) -> dict:
        # The draw itself lives in the tool, never in the model — an LLM cannot
        # be trusted to be uniform (or unmanipulable). The visible body is built
        # server-side from `chosen`, so the winner can't be re-typed either.
        args = args or {}
        with db.session() as s:
            members = {
                m.id: m.display_name
                for m in roster.list_members(s, ctx.room_id, default_only=True)
            }
        # The pool is every default-participant member of the group — no
        # per-request subsetting (the tool takes no participant list), but a
        # member flagged out of default group activities (default_participant
        # = false) is skipped here.
        pool_ids = list(members)
        if not pool_ids:
            return _err("Không có ai trong nhóm để bốc.")
        chosen_id = random.choice(pool_ids)
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
            last = ledger.last_settlement(s, ctx.room_id)
            try:
                period = resolve_period(
                    args.get("keyword"),
                    today=today_ict(),
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
            last = ledger.last_settlement(s, ctx.room_id)
            period = resolve_period(
                args.get("keyword"), today=today_ict(),
                last_settlement_to=last.period_to if last else None,
            )
            stmt = ledger.statement_for(s, ctx.room_id, member, period["from"], period["to"])
            ids = {r["other_id"] for r in stmt["owe"]} | {r["other_id"] for r in stmt["owed"]} \
                | {member}
            names = _names_for(s, ctx.room_id, ids)

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
            last = ledger.last_settlement(s, ctx.room_id)
            period = resolve_period(
                args.get("keyword"), today=today_ict(),
                last_settlement_to=last.period_to if last else None,
            )
            timeline = ledger.period_timeline(s, ctx.room_id, period["from"], period["to"])
            outstanding = ledger.outstanding_pairs(s, ctx.room_id, period["from"], period["to"])
            ids = {r["debtor_id"] for r in outstanding} | {r["creditor_id"] for r in outstanding} \
                | {e.get("payer_id") for e in timeline} \
                | {e.get("from_id") for e in timeline} | {e.get("to_id") for e in timeline}
            ids.discard(None)
            names = _names_for(s, ctx.room_id, ids)
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

    def add_member(args, _tool_ctx=None) -> dict:
        args = args or {}
        display_name = args.get("display_name")
        nickname = args.get("nickname")
        with db.session() as s:
            room = rooms.room_by_id(s, ctx.room_id)
            if room is None:
                return _err("Room not found.")
            try:
                m = accounts.add_unclaimed(
                    s,
                    room,
                    display_name=display_name,
                    nickname=nickname,
                    bank_code=args.get("bank_code"),
                    account_number=args.get("account_number"),
                    account_holder=args.get("account_holder"),
                )
            except accounts.AccountError as exc:
                return _err(str(exc))
            return {"ok": True, "member_id": m.id, "nickname": m.nickname}

    def update_member(args, _tool_ctx=None) -> dict:
        args = args or {}
        target = args.get("target")
        if target in (None, ""):
            return _err("Missing target (nickname or id).")
        with db.session() as s:
            m = accounts.find_member(s, ctx.room_id, target)
            if m is None:
                return _err(f"No member found for '{target}'.")
            try:
                accounts.update_member(
                    s, m,
                    display_name=args.get("display_name"),
                    nickname=args.get("nickname"),
                    bank_code=args.get("bank_code"),
                    account_number=args.get("account_number"),
                    account_holder=args.get("account_holder"),
                    aliases=args.get("aliases"),
                    active=args.get("active"),
                    default_participant=args.get("default_participant"),
                )
            except accounts.AccountError as exc:
                return _err(str(exc))
            return {
                "ok": True, "member_id": m.id, "nickname": m.nickname,
                "display_name": m.display_name, "active": m.active,
                "default_participant": m.default_participant,
            }

    def delete_member(args, _tool_ctx=None) -> dict:
        args = args or {}
        target = args.get("target")
        if target in (None, ""):
            return _err("Missing target (nickname or id).")
        with db.session() as s:
            m = accounts.find_member(s, ctx.room_id, target)
            if m is None:
                return _err(f"No member found for '{target}'.")
            accounts.soft_delete_member(s, m)
            return {
                "ok": True, "member_id": m.id, "nickname": m.nickname,
                "display_name": m.display_name,
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
            names = _names_for(s, ctx.room_id, [frm_id, to_id])
            # _names_for returns only the ids that are real room members, so a
            # hallucinated from/to would be missing here — reject it before the
            # pay-off path can falsely report payment_settled.
            if frm_id not in names or to_id not in names:
                return _err("Không tìm thấy thành viên trong nhóm.")
            if amount is None:
                # Gross directional pay-off over the open (since_last) period. We
                # do NOT net A<->B: a real cash payment settles what `from` owes
                # `to`, per meal. Netting is only for settle_period's QR.
                last = ledger.last_settlement(s, ctx.room_id)
                period = resolve_period(
                    "since_last", today=today_ict(),
                    last_settlement_to=last.period_to if last else None,
                )
                edges = ledger.debt_breakdown(s, ctx.room_id, period["from"], period["to"])
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
        with db.session() as s:
            from app import drafts  # lazy: avoid import cycle at module load
            pending = drafts.list_pending_drafts(s, ctx.room_id)
            if pending:
                summaries = []
                for d in pending:
                    att = d.attachments or {}
                    if att.get("type") == "payment_draft":
                        tf = att.get("transfers") or []
                        ids = [x for t in tf for x in (t.get("from_member_id"), t.get("to_member_id"))]
                        names = _names_for(s, ctx.room_id, ids)
                        summaries.append({
                            "draft_id": d.id, "kind": "payment",
                            "transfers": [
                                {"from_name": names.get(t.get("from_member_id"), "?"),
                                 "to_name": names.get(t.get("to_member_id"), "?"),
                                 "amount": t.get("amount", 0)} for t in tf],
                        })
                    else:
                        names = _names_for(s, ctx.room_id, [att.get("payer_member_id")])
                        summaries.append({
                            "draft_id": d.id, "kind": "meal",
                            "payer_name": names.get(att.get("payer_member_id"), "?"),
                            "bill_total": att.get("bill_total", 0),
                            "participant_count": len(att.get("member_participants") or []),
                        })
                return {
                    "ok": True,
                    "type": "settle_blocked",
                    "pending": summaries,
                    "message": f"Có {len(pending)} đề xuất chưa xác nhận — xác nhận hoặc huỷ trước khi tính.",
                }

            last = ledger.last_settlement(s, ctx.room_id)
            try:
                period = resolve_period(
                    args.get("keyword"),
                    today=today_ict(),
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
            open_edges = [e for e in ledger.debt_breakdown(s, ctx.room_id, from_date, to_date)
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
            members = {m.id: m for m in roster.list_members(s, ctx.room_id, include_inactive=True)}
            fallback_note = f"Chia tien an {to_date.day}/{to_date.month}"

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
                    fallback=fallback_note,
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
                    row["qr_url"] = make_qr_url(payee, t.amount, note)
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

    return {
        "find_members": CustomTool(
            execute=find_members,
            description="Look up member ids by name/nickname, or the whole group (all_active).",
            input_schema=_FIND_SCHEMA,
        ),
        "propose_meal": CustomTool(
            execute=propose_meal,
            description="Propose a meal (does NOT record it) for the user to confirm. FINAL TOOL when logging a meal.",
            input_schema=_PROPOSE_SCHEMA,
        ),
        "void_meal": CustomTool(
            execute=void_meal,
            description="Void a meal by meal_id to correct a mistake.",
            input_schema=_VOID_SCHEMA,
        ),
        "cancel_draft": CustomTool(
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
        "pick_random": CustomTool(
            execute=pick_random,
            description="Randomly pick ONE member of the group ('bốc thăm', 'random ai trả', 'chọn đại một người'). Draws from default-participant members only (see update_member's default_participant flag) — no per-request subsetting. The tool does the draw — never pick yourself.",
            input_schema=_RANDOM_PICK_SCHEMA,
        ),
        "resolve_period": CustomTool(
            execute=resolve_period_tool,
            description="Turn a time keyword (since_last/this_week/...) into a concrete date range (ICT).",
            input_schema=_PERIOD_SCHEMA,
        ),
        "resolve_date": CustomTool(
            execute=resolve_date_tool,
            description="Turn a day word ('thứ 2', 'hôm qua', '20/7') into an ISO date (ICT). Use before propose_meal when the user names a day.",
            input_schema={"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]},
        ),
        "member_statement": CustomTool(
            execute=member_statement,
            description="A person's own statement: what they owe + are owed, per meal, with paid/unpaid status. There is no net/ròng figure — report the owe and owed rows as they are. Default member = the sender. Use for first-person questions ('tôi nợ ai', 'how much do I owe').",
            input_schema={"type": "object", "properties": {
                "member": {"type": "integer", "description": "member id; blank = the sender."},
                "keyword": _PERIOD_SCHEMA["properties"]["keyword"],
            }},
        ),
        "get_period_summary": CustomTool(
            execute=get_period_summary,
            description="Group summary: chronological timeline of meals + payments plus every open 'X owes Y' row (display only). Use for 'summary'/'current state'/'tổng kết'.",
            input_schema={"type": "object", "properties": {"keyword": _PERIOD_SCHEMA["properties"]["keyword"]}},
        ),
        "settle_period": CustomTool(
            execute=settle_period,
            description=(
                "Compute who pays whom right now + build VietQR codes for a period. "
                "READ-ONLY: it is a running total ('tạm tính'), it does NOT close or reset "
                "anything. If the user asks to chốt/reset, show this and say plainly that "
                "closing a period is not supported yet."
            ),
            input_schema=_SETTLE_SCHEMA,
        ),
        "add_member": CustomTool(
            execute=add_member,
            description="Add a new member to the room (no PIN yet); they set their PIN on first sign-in.",
            input_schema=_ADD_MEMBER_SCHEMA,
        ),
        "update_member": CustomTool(
            execute=update_member,
            description="Update a member's details (display_name, nickname, bank, aliases), restore a removed one (active:true), or exclude/include them from default group activities (default_participant:false/true).",
            input_schema=_UPDATE_MEMBER_SCHEMA,
        ),
        "delete_member": CustomTool(
            execute=delete_member,
            description="Remove a member from the group (soft-delete): they leave the roster and can't sign in, but their past meals/settlements are kept.",
            input_schema=_DELETE_MEMBER_SCHEMA,
        ),
        "propose_payment": CustomTool(
            execute=propose_payment,
            description=(
                "Propose a cash payment one member made to another for the user to confirm "
                "(e.g. 'A trả B 100k', 'A đã trả B'). Does NOT write the ledger. FINAL TOOL for a "
                "payment. Omit `amount` to pay off exactly what `from` owes `to`."
            ),
            input_schema=_PROPOSE_PAYMENT_SCHEMA,
        ),
    }


def tool_manifest() -> list[dict]:
    """`[{name, description, schema}]` for the sidecar's `run` command.

    Built from `build_tools` against a throwaway context so the manifest can never
    drift from the tools that actually execute — the model must be told about
    exactly the schema the tool will validate against.
    """
    from app.db import Database

    ctx = ToolContext(db=Database("sqlite:///:memory:"), room_id=0)
    return [
        {"name": name, "description": tool.description, "schema": tool.input_schema}
        for name, tool in build_tools(ctx).items()
    ]
