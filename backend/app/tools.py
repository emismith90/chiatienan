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
from dataclasses import dataclass, field
from datetime import date

from cursor_sdk import CustomTool

from app import accounts, ledger, roster, rooms
from app.clock import today_ict
from app.db import Database
from app.money import MoneyError, net_transfers, split_with_guests
from app.periods import resolve_period
from app.qr import QRError, make_qr_url

logger = logging.getLogger("chiatienan")


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
    return {m.id: m.display_name for m in roster.list_members(session, room_id) if m.id in set(ids)}


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
                         "description": "member ids of those who ate (split the bill)."},
        "total": {"type": "integer", "description": "Bill total, integer VND (840k → 840000)."},
        "guests": {"type": "array", "items": {"type": "string"},
                   "description": "Guest names (non-members who pay cash)."},
        "adjustments": {"type": "array", "items": {
            "type": "object",
            "properties": {"member": {"type": "integer"}, "amount": {"type": "integer"}},
            "required": ["member", "amount"]}},
        "dish": {"type": "string", "description": "Dish (if the user mentioned it)."},
        "initiator": {"type": "string", "description": "Who initiated the meal (if any)."},
        "note": {"type": "string", "description": "Free-form note (e.g. 'An đổi ý')."},
    },
    "required": ["participants", "total"],
}

_VOID_SCHEMA = {
    "type": "object",
    "properties": {"meal_id": {"type": "integer"}},
    "required": ["meal_id"],
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

_BALANCES_SCHEMA = {
    "type": "object",
    "properties": {
        "from": {"type": "string", "description": "ISO date (blank = from the start of the ledger)."},
        "to": {"type": "string", "description": "ISO date."},
    },
    "required": ["to"],
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

_SETTLE_SCHEMA = {
    "type": "object",
    "properties": {
        "keyword": {
            "type": "string",
            "enum": ["since_last", "this_week", "last_week", "today", "yesterday", "this_month", "explicit"],
        },
        "from": {"type": "string"},
        "to": {"type": "string"},
        "commit": {"type": "boolean", "description": "True to CLOSE the period (only when the user says 'chốt')."},
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
            "dish": args.get("dish"),
            "initiator": args.get("initiator"),
            "note": args.get("note"),
            "per_head_preview": preview["per_head"],
        }

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

    def get_period_balances(args, _tool_ctx=None) -> dict:
        args = args or {}
        try:
            from_date = _parse_iso(args.get("from"))
            to_date = _parse_iso(args.get("to"))
        except ValueError:
            return _err("Invalid date; expected YYYY-MM-DD.")
        if to_date is None:
            return _err("Missing end date (to).")
        with db.session() as s:
            balances = ledger.period_balances(s, ctx.room_id, from_date, to_date)
            names = _names_for(s, ctx.room_id, balances.keys())
        return {
            "ok": True,
            "from": from_date.isoformat() if from_date else None,
            "to": to_date.isoformat(),
            "balances": [
                {"id": mid, "name": names.get(mid, "?"), **vals}
                for mid, vals in sorted(balances.items(), key=lambda kv: kv[1]["balance"])
            ],
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

    def settle_period(args, _tool_ctx=None) -> dict:
        """Composite, server-side end-to-end: balances → net → QR → payload."""
        args = args or {}
        commit = bool(args.get("commit"))
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

            from_date, to_date = period["from"], period["to"]
            balances = ledger.period_balances(s, ctx.room_id, from_date, to_date)
            if not any(v["balance"] for v in balances.values()):
                return {
                    "ok": True,
                    "period": {"from": from_date.isoformat() if from_date else None, "to": to_date.isoformat()},
                    "transfers": [],
                    "committed": False,
                    "message": "Không có gì để chốt trong kỳ này (mọi người đã cân bằng).",
                }

            transfers = net_transfers({mid: v["balance"] for mid, v in balances.items()})
            members = {m.id: m for m in roster.list_members(s, ctx.room_id)}
            note = f"Chia tien an {to_date.isoformat()}"

            rows: list[dict] = []
            warnings: list[str] = []
            for t in transfers:
                payee = members.get(t.to_member)
                payer = members.get(t.from_member)
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

            committed = False
            if commit:
                ledger.record_settlement(
                    s,
                    room_id=ctx.room_id,
                    period_from=from_date,
                    period_to=to_date,
                    requested_by=str(ctx.sender_member_id),
                    transfers=rows,
                )
                committed = True

        return {
            "ok": True,
            "period": {"from": from_date.isoformat() if from_date else None, "to": to_date.isoformat()},
            "transfers": rows,
            "warnings": warnings,
            "committed": committed,
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
        "resolve_period": CustomTool(
            execute=resolve_period_tool,
            description="Turn a time keyword (since_last/this_week/...) into a concrete date range (ICT).",
            input_schema=_PERIOD_SCHEMA,
        ),
        "get_period_balances": CustomTool(
            execute=get_period_balances,
            description="Per-person paid/consumed/balance over a range (display only).",
            input_schema=_BALANCES_SCHEMA,
        ),
        "settle_period": CustomTool(
            execute=settle_period,
            description="Compute who pays whom + build VietQR codes for the period. commit:true to CLOSE it.",
            input_schema=_SETTLE_SCHEMA,
        ),
        "add_member": CustomTool(
            execute=add_member,
            description="Add a new member to the room (no PIN yet); they set their PIN on first sign-in.",
            input_schema=_ADD_MEMBER_SCHEMA,
        ),
    }
