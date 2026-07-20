"""The LLM-facing ``CustomTool`` set — where every number lives.

The model decides *when* to call these; the tools own all arithmetic and all
QR-building (design D3). Each tool opens its own short-lived DB session, so a
turn that fails before ``record_meal`` never half-writes. Validation failures are
returned as ``{"ok": False, "error": ...}`` dicts (a clarifying-question result)
rather than raised, so the model can ask the user instead of guessing.

Numbers that end up in a QR are computed and rendered entirely inside
``settle_period`` — they never round-trip tool → LLM → tool.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from cursor_sdk import CustomTool

from app import ledger, roster
from app.clock import today_ict
from app.db import Database
from app.money import MoneyError, net_transfers
from app.periods import resolve_period
from app.qr import QRError, make_qr_url

logger = logging.getLogger("chiatienan")


@dataclass
class ToolContext:
    """Per-turn context the tools close over (never seen by the model)."""

    db: Database
    sender_teams_id: str | None = None
    sender_name: str | None = None
    sender_aad: str | None = None
    # People @mentioned in this message (bot mention already stripped):
    turn_mentions: list[dict] = field(default_factory=list)

    def sender_member_id(self, session) -> int | None:
        if not self.sender_teams_id:
            return None
        member = roster.capture_sender(
            session,
            teams_user_id=self.sender_teams_id,
            aad_object_id=self.sender_aad,
            name=self.sender_name or "Người dùng mới",
        )
        return member.id


def _err(message: str) -> dict:
    return {"ok": False, "error": message}


def _parse_iso(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _names_for(session, ids) -> dict[int, str]:
    return {m.id: m.display_name for m in roster.list_members(session) if m.id in set(ids)}


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #

_FIND_SCHEMA = {
    "type": "object",
    "properties": {
        "names": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tên hoặc biệt danh cần tra (vd ['An', 'Bình']).",
        },
        "all_active": {
            "type": "boolean",
            "description": "True để lấy toàn bộ thành viên đang hoạt động ('cả nhóm').",
        },
        "include_tagged": {
            "type": "boolean",
            "description": "Gộp những người được @tag trong tin nhắn này (mặc định true).",
        },
    },
}

_RECORD_SCHEMA = {
    "type": "object",
    "properties": {
        "payer": {"type": "integer", "description": "member id người trả tiền; bỏ trống = người đang nhắn."},
        "participants": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "member id những người ăn (chia phần).",
        },
        "total": {"type": "integer", "description": "Tổng tiền, VND nguyên (vd 840k → 840000)."},
        "adjustments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "member": {"type": "integer"},
                    "amount": {"type": "integer", "description": "VND có dấu (+ đắt hơn, - rẻ hơn)."},
                },
                "required": ["member", "amount"],
            },
        },
        "occurred_on": {"type": "string", "description": "Ngày ISO (YYYY-MM-DD); mặc định hôm nay (ICT)."},
        "note": {"type": "string"},
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
        "from": {"type": "string", "description": "Ngày ISO cho keyword=explicit."},
        "to": {"type": "string", "description": "Ngày ISO cho keyword=explicit."},
    },
}

_BALANCES_SCHEMA = {
    "type": "object",
    "properties": {
        "from": {"type": "string", "description": "Ngày ISO (bỏ trống = từ đầu sổ)."},
        "to": {"type": "string", "description": "Ngày ISO."},
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
        "from": {"type": "string"},
        "to": {"type": "string"},
        "commit": {"type": "boolean", "description": "True để CHỐT kỳ (chỉ khi người dùng nói 'chốt')."},
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
        include_tagged = args.get("include_tagged", True)
        mentions = ctx.turn_mentions if include_tagged else []
        with db.session() as s:
            return {"ok": True, **roster.resolve(s, names=names, mentions=mentions, all_active=all_active)}

    def record_meal(args, _tool_ctx=None) -> dict:
        args = args or {}
        participants = list(args.get("participants") or [])
        total = args.get("total")
        if not isinstance(total, int):
            return _err("Thiếu tổng tiền (total) dạng số nguyên VND.")
        adjustments = {}
        for adj in args.get("adjustments") or []:
            try:
                adjustments[int(adj["member"])] = int(adj["amount"])
            except (KeyError, TypeError, ValueError):
                return _err("Điều chỉnh (adjustments) phải có {member, amount} là số.")
        try:
            occurred_on = _parse_iso(args.get("occurred_on"))
        except ValueError:
            return _err("Ngày (occurred_on) không hợp lệ, cần dạng YYYY-MM-DD.")

        with db.session() as s:
            payer = args.get("payer") or ctx.sender_member_id(s)
            if not payer:
                return _err("Không xác định được người trả tiền (payer).")
            if not participants:
                return _err("Chưa có người tham gia (participants).")
            try:
                res = ledger.record_meal(
                    s,
                    payer_member_id=int(payer),
                    participants=[int(p) for p in participants],
                    total_amount=total,
                    adjustments=adjustments,
                    occurred_on=occurred_on,
                    note=args.get("note"),
                    raw_input=None,
                    source="teams",
                    logged_by=ctx.sender_teams_id,
                )
            except (MoneyError, ledger.LedgerError) as exc:
                return _err(str(exc))

            names = _names_for(s, [res["payer_member_id"], *res["shares"].keys()])
            return {
                "ok": True,
                "meal_id": res["meal_id"],
                "occurred_on": res["occurred_on"],
                "total_amount": res["total_amount"],
                "payer": {"id": res["payer_member_id"], "name": names.get(res["payer_member_id"], "?")},
                "shares": [
                    {"id": mid, "name": names.get(mid, "?"), "amount": amt}
                    for mid, amt in res["shares"].items()
                ],
            }

    def void_meal(args, _tool_ctx=None) -> dict:
        args = args or {}
        meal_id = args.get("meal_id")
        if not isinstance(meal_id, int):
            return _err("Thiếu meal_id.")
        with db.session() as s:
            try:
                return {"ok": True, **ledger.void_meal(s, meal_id, by=ctx.sender_teams_id)}
            except ledger.LedgerError as exc:
                return _err(str(exc))

    def resolve_period_tool(args, _tool_ctx=None) -> dict:
        args = args or {}
        with db.session() as s:
            last = ledger.last_settlement(s)
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
            return _err("Ngày không hợp lệ, cần dạng YYYY-MM-DD.")
        if to_date is None:
            return _err("Thiếu ngày kết thúc (to).")
        with db.session() as s:
            balances = ledger.period_balances(s, from_date, to_date)
            names = _names_for(s, balances.keys())
        return {
            "ok": True,
            "from": from_date.isoformat() if from_date else None,
            "to": to_date.isoformat(),
            "balances": [
                {"id": mid, "name": names.get(mid, "?"), **vals}
                for mid, vals in sorted(balances.items(), key=lambda kv: kv[1]["balance"])
            ],
        }

    def settle_period(args, _tool_ctx=None) -> dict:
        """Composite, server-side end-to-end: balances → net → QR → payload."""
        args = args or {}
        commit = bool(args.get("commit"))
        with db.session() as s:
            last = ledger.last_settlement(s)
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
            balances = ledger.period_balances(s, from_date, to_date)
            if not any(v["balance"] for v in balances.values()):
                return {
                    "ok": True,
                    "period": {"from": from_date.isoformat() if from_date else None, "to": to_date.isoformat()},
                    "transfers": [],
                    "committed": False,
                    "message": "Không có gì để chốt trong kỳ này (mọi người đã cân bằng).",
                }

            transfers = net_transfers({mid: v["balance"] for mid, v in balances.items()})
            members = {m.id: m for m in roster.list_members(s)}
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
                    period_from=from_date,
                    period_to=to_date,
                    requested_by=ctx.sender_teams_id,
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
            description="Tra cứu member id từ tên/biệt danh, @tag, hoặc toàn nhóm (all_active).",
            input_schema=_FIND_SCHEMA,
        ),
        "record_meal": CustomTool(
            execute=record_meal,
            description="Ghi một bữa ăn: chia phần đều + điều chỉnh, ghi sổ. CÔNG CỤ CUỐI trong lượt ghi.",
            input_schema=_RECORD_SCHEMA,
        ),
        "void_meal": CustomTool(
            execute=void_meal,
            description="Xoá (void) một bữa ăn theo meal_id để sửa sai.",
            input_schema=_VOID_SCHEMA,
        ),
        "resolve_period": CustomTool(
            execute=resolve_period_tool,
            description="Đổi keyword thời gian (since_last/this_week/...) thành khoảng ngày cụ thể (ICT).",
            input_schema=_PERIOD_SCHEMA,
        ),
        "get_period_balances": CustomTool(
            execute=get_period_balances,
            description="Số dư paid/consumed/balance mỗi người trong khoảng (chỉ để hiển thị).",
            input_schema=_BALANCES_SCHEMA,
        ),
        "settle_period": CustomTool(
            execute=settle_period,
            description="Tính ai trả ai + tạo mã QR VietQR cho cả kỳ. commit:true để CHỐT.",
            input_schema=_SETTLE_SCHEMA,
        ),
    }
