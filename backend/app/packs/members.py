"""``room_members``: the roster-administration tools as a host pack (plan Task 3.3).

Adding, updating and removing members is the host's business — a member here is a
sign-in account with a PIN, aliases and bank details (``app.accounts``), which is
why these three tools did not move to ``packs/lunch_ledger`` with the money tools.
Any ledger business on this host (lunch, poker) enables this pack alongside its
own. The tool bodies are those of ``app/tools.py`` before Task 3.3, unchanged.
"""
from __future__ import annotations

from app import accounts, rooms, roster
from kernos.packs import BasePack, PackTool, err as _err

MEMBER_TOOLS = frozenset({"add_member", "update_member", "delete_member"})

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
                "Whether this member is in the pool for a rut tham / random draw "
                "(`pick_random`). Set false for someone who should never be drawn. It does "
                "NOT affect splitting: 'cả nhóm' / 'everyone' always means the whole "
                "roster, and leaving someone out of a meal is done per meal, by omitting "
                "them from `participants`. Defaults true."
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


def build(ctx) -> dict[str, PackTool]:
    db = ctx.db

    def add_member(args, _tool_ctx=None) -> dict:
        args = args or {}
        display_name = args.get("display_name")
        nickname = args.get("nickname")
        with db.session() as s:
            room = rooms.room_by_id(s, ctx.space_id)
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
            # The name that failed to resolve a moment ago now exists, so it is
            # no longer a hole propose_meal has to complain about.
            fresh = roster.name_tokens(m.display_name) | roster.name_tokens(m.nickname)
            for raw in [k for k in ctx.unknown_names if roster.name_tokens(k) & fresh]:
                ctx.unknown_names.pop(raw, None)
            return {"ok": True, "member_id": m.id, "nickname": m.nickname}

    def update_member(args, _tool_ctx=None) -> dict:
        args = args or {}
        target = args.get("target")
        if target in (None, ""):
            return _err("Missing target (nickname or id).")
        with db.session() as s:
            m = accounts.find_member(s, ctx.space_id, target)
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
            m = accounts.find_member(s, ctx.space_id, target)
            if m is None:
                return _err(f"No member found for '{target}'.")
            accounts.soft_delete_member(s, m)
            return {
                "ok": True, "member_id": m.id, "nickname": m.nickname,
                "display_name": m.display_name,
            }
    specs = {
        "add_member": dict(
            execute=add_member,
            description="Add a new member to the room (no PIN yet); they set their PIN on first sign-in.",
            input_schema=_ADD_MEMBER_SCHEMA,
        ),
        "update_member": dict(
            execute=update_member,
            description="Update a member's details (display_name, nickname, bank, aliases), restore a removed one (active:true), or exclude/include them from random draws (default_participant:false/true — draws only, never splits).",
            input_schema=_UPDATE_MEMBER_SCHEMA,
        ),
        "delete_member": dict(
            execute=delete_member,
            description="Remove a member from the group (soft-delete): they leave the roster and can't sign in, but their past meals/settlements are kept.",
            input_schema=_DELETE_MEMBER_SCHEMA,
        ),
    }

    return {name: PackTool(name, spec["description"], spec["input_schema"], spec["execute"])
            for name, spec in specs.items()}


class RoomMembersPack(BasePack):
    id, version, handles_money = "room_members", "1", False

    def tools(self, ctx) -> dict[str, PackTool]:
        return build(ctx)
