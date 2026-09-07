"""The poker business's own tools: propose a game night (a draft card), void one, list
the history. Everything shared — payments, settlement, statements — is
``packs.ledger_tools``. The tool owns every number: the invariant is checked here even
when the profile's ``chips-conserved`` rule already refused the call (the rule is the
configurable early check, this is the floor).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from kernos.packs import PackTool, err as _err
from ledger_core import roster
from ledger_core.periods import resolve_date, resolve_period
from packs.poker_ledger.models import Game, GameEntry
from packs.poker_ledger.money import PokerError, game_edges, net_positions


def _parse_iso(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _names_for(session, space_id, ids) -> dict[int, str]:
    return {m.id: m.display_name for m in roster.list_members(session, space_id, include_inactive=True) if m.id in set(ids)}


def game_payload(entries: list[dict], house: int, played_on: str | None, note, names: dict[int, str], *, game_id: int = 0) -> dict:
    """The draft payload / result shape: entries, nets, pot, the edges preview."""
    nets = net_positions(entries, house)
    pot = sum(int(e["buy_in"]) for e in entries)
    edges = game_edges(game_id, _parse_iso(played_on) or date.today(), nets, house=int(house))
    return {
        "entries": [{"member": int(e["member"]), "buy_in": int(e["buy_in"]), "cash_out": int(e["cash_out"])} for e in entries],
        "house": int(house), "played_on": played_on, "note": note,
        "pot": pot, "players": len(entries),
        "nets": [{"member": m, "name": names.get(m, "?"), "net": n} for m, n in sorted(nets.items())],
        "edges_preview": [{"from_member_id": e.debtor, "to_member_id": e.creditor, "amount": e.amount} for e in edges],
    }


_ENTRY_SCHEMA = {
    "type": "object",
    "required": ["member", "buy_in", "cash_out"],
    "properties": {
        "member": {"type": "integer", "description": "member id (from find_members)."},
        "buy_in": {"type": "integer", "description": "Total chips bought, integer VND (500k → 500000)."},
        "cash_out": {"type": "integer", "description": "Chips cashed out at the end, integer VND."},
    },
}

_PROPOSE_GAME_SCHEMA = {
    "type": "object",
    "required": ["entries"],
    "properties": {
        "entries": {"type": "array", "items": _ENTRY_SCHEMA,
                    "description": "One entry per player, every player exactly once."},
        "house": {"type": "integer", "description": "Rake / tips the table kept, integer VND. Omit or 0 when none."},
        "day_word": {"type": "string", "description": "The user's own day word ('tối qua', 'thứ 6', '20/7'); the tool resolves it."},
        "played_on": {"type": "string", "description": "ISO date, when the user gave one explicitly."},
        "note": {"type": "string"},
    },
}

_PERIOD_KEYWORD = {"type": "string", "enum": ["since_last", "this_week", "last_week", "this_month", "last_month"],
                   "description": "Which period; default since_last."}


def build(ctx) -> dict[str, PackTool]:
    db = ctx.db

    def propose_game(args, _tool_ctx=None) -> dict:
        args = args or {}
        entries = args.get("entries")
        house = args.get("house") if args.get("house") is not None else 0
        played_on = args.get("played_on")
        if args.get("day_word"):
            try:
                played_on = resolve_date(str(args["day_word"]), today=ctx.today()).isoformat()
            except ValueError as exc:
                return _err(str(exc))
        elif played_on is not None:
            try:
                _parse_iso(played_on)
            except ValueError:
                return _err("Ngày không hợp lệ (cần dạng YYYY-MM-DD).")
        else:
            played_on = ctx.today().isoformat()
        try:
            with db.session() as s:
                ids = [int(e.get("member")) for e in entries or [] if isinstance(e, dict) and e.get("member") is not None]
                names = _names_for(s, ctx.space_id, ids)
            missing = [m for m in ids if m not in names]
            if missing:
                return _err(f"Không tìm thấy thành viên {missing} trong nhóm — dùng find_members để lấy id.")
            payload = game_payload(entries, house, played_on, args.get("note"), names)
        except (PokerError, TypeError, ValueError) as exc:
            return _err(str(exc))
        return {"ok": True, "type": "game_draft", **payload}

    def void_game(args, _tool_ctx=None) -> dict:
        args = args or {}
        game_id = args.get("game_id")
        if not isinstance(game_id, int):
            return _err("Missing game_id.")
        with db.session() as s:
            game = s.get(Game, game_id)
            if game is None or game.room_id != int(ctx.space_id):
                return _err(f"Game #{game_id} not found.")
            if game.voided:
                return _err(f"Game #{game_id} is already voided.")
            game.voided, game.voided_by = True, str(ctx.sender_member_id)
            from ledger_core import clock
            game.voided_at = clock.now()
            s.flush()
        return {"ok": True, "game_id": game_id, "voided": True}

    def game_history(args, _tool_ctx=None) -> dict:
        args = args or {}
        with db.session() as s:
            from ledger_core import ledger
            last = ledger.last_settlement(s, ctx.space_id)
            try:
                period = resolve_period(args.get("keyword"), today=ctx.today(),
                                        last_settlement_to=last.period_to if last else None)
            except ValueError as exc:
                return _err(str(exc))
            conds = [Game.room_id == int(ctx.space_id), Game.voided.is_(False), Game.played_on <= period["to"]]
            if period["from"] is not None:
                conds.append(Game.played_on >= period["from"])
            games = s.scalars(select(Game).where(*conds).order_by(Game.played_on, Game.id)).all()
            rows = []
            for g in games:
                names = _names_for(s, ctx.space_id, [e.member_id for e in g.entries])
                rows.append({"game_id": g.id, "played_on": g.played_on.isoformat(), "house": g.house, "note": g.note,
                             "pot": sum(e.buy_in for e in g.entries), "players": len(g.entries),
                             "nets": [{"member": e.member_id, "name": names.get(e.member_id, "?"), "net": e.cash_out - e.buy_in}
                                      for e in sorted(g.entries, key=lambda e: e.member_id)]})
        return {"ok": True, "type": "game_history",
                "period": {"from": period["from"].isoformat() if period["from"] else None, "to": period["to"].isoformat()},
                "games": rows}

    specs = {
        "propose_game": dict(
            execute=propose_game,
            description=("Propose a poker/card game night (does NOT record it) for the table to confirm: every "
                         "player's buy-in and cash-out, plus `house` for rake/tips. The tool checks that chips are "
                         "conserved (Σ buy_in = Σ cash_out + house) and computes who owes whom — never compute "
                         "or restate those numbers. FINAL TOOL when logging a game."),
            input_schema=_PROPOSE_GAME_SCHEMA,
        ),
        "void_game": dict(
            execute=void_game,
            description="Void a recorded game by game_id to correct a mistake (its debts disappear; payments stay).",
            input_schema={"type": "object", "properties": {"game_id": {"type": "integer"}}, "required": ["game_id"]},
        ),
        "game_history": dict(
            execute=game_history,
            description="List the recorded games of a period with each player's net. Use for 'lịch sử', 'các ván', 'history'.",
            input_schema={"type": "object", "properties": {"keyword": _PERIOD_KEYWORD}},
        ),
    }
    return {name: PackTool(name, spec["description"], spec["input_schema"], spec["execute"]) for name, spec in specs.items()}
