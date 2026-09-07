"""``poker_ledger``: the second money business (design §7; plan Task 6.3). What poker
*adds* to the ledger: game nights with buy-ins and cash-outs, the `game_draft` card,
the debt edges from losers to winners, its content (prompt, skills, rules), its
fixtures, graders and golden cases. Everything shared is ``packs.ledger_tools``.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import select

from kernos.packs import BasePack, DraftKind, PackTool
from ledger_core import clock, roster
from packs.poker_ledger import fixtures, render, tools
from packs.poker_ledger.models import Base, Game, GameEntry, bind
from packs.poker_ledger.money import game_edges, net_positions

POKER_TOOLS = frozenset({"propose_game", "void_game", "game_history"})
_CONTENT = Path(__file__).parent / "content"
EDITABLE = frozenset({"entries", "house", "note", "played_on"})


def _names(session, space_id) -> dict[int, str]:
    return {m.id: m.display_name for m in roster.list_members(session, space_id, include_inactive=True)}


def prepare(att: dict) -> dict:
    """Re-derive nets, pot and the edge preview from the entries on create and on
    every edit; a table that no longer conserves chips raises (the store refuses)."""
    nets = net_positions(att.get("entries") or [], int(att.get("house") or 0))
    played_on = date.fromisoformat(att["played_on"]) if att.get("played_on") else clock.today()
    att["pot"] = sum(int(e["buy_in"]) for e in att["entries"])
    att["players"] = len(att["entries"])
    att["nets"] = [{"member": m, "name": (att_name(att, m)), "net": n} for m, n in sorted(nets.items())]
    att["edges_preview"] = [{"from_member_id": e.debtor, "to_member_id": e.creditor, "amount": e.amount}
                            for e in game_edges(0, played_on, nets, house=int(att.get('house') or 0))]
    return att


def att_name(att: dict, member: int) -> str:
    for n in att.get("nets") or []:
        if n.get("member") == member and n.get("name"):
            return n["name"]
    return "?"


def signature(att: dict) -> tuple:
    return ("game", frozenset(int(e["member"]) for e in att.get("entries") or []), att.get("played_on"))


def commit(session, space_id, att: dict, *, logged_by) -> dict:
    nets = net_positions(att["entries"], int(att.get("house") or 0))
    played_on = date.fromisoformat(att["played_on"]) if att.get("played_on") else clock.today()
    game = Game(room_id=int(space_id), played_on=played_on, house=int(att.get("house") or 0), note=att.get("note"),
                raw_input=att.get("raw_input"), logged_by=logged_by)
    for e in att["entries"]:
        game.entries.append(GameEntry(member_id=int(e["member"]), buy_in=int(e["buy_in"]), cash_out=int(e["cash_out"])))
    session.add(game)
    session.flush()
    return {"game_id": game.id, "played_on": played_on.isoformat(), "house": game.house, "note": game.note,
            "pot": sum(int(e["buy_in"]) for e in att["entries"]), "players": len(att["entries"]),
            "nets": {m: n for m, n in nets.items()}}


def card(session, space_id, att: dict, res: dict) -> tuple[str, dict]:
    names = _names(session, space_id)
    game_att = {"type": "game", "game_id": res["game_id"], "played_on": res["played_on"], "house": res["house"],
                "note": res["note"], "pot": res["pot"], "players": res["players"],
                "nets": [{"member": m, "name": names.get(m, "?"), "net": n} for m, n in sorted(res["nets"].items())]}
    return render._game_body(game_att), game_att


def summary(session, space_id, att: dict) -> dict:
    return {"kind": "game", "label": f"game: {len(att.get('entries') or [])} players, pot {int(att.get('pot') or 0):,}đ"}


def exists(session, space_id, game_id) -> bool:
    game = session.get(Game, int(game_id))
    return game is not None and game.room_id == int(space_id) and not game.voided


def _read(rel: str) -> str:
    return (_CONTENT / rel).read_text(encoding="utf-8")


def _skill(name: str) -> dict:
    body = _read(f"skills/{name}.md")
    description = ""
    if body.startswith("---"):
        for line in body.split("---", 2)[1].splitlines():
            if line.startswith("description:"):
                description = line.split(":", 1)[1].strip()
    return {"name": name, "description": description, "body": body}


class PokerLedgerPack(BasePack):
    id, version, handles_money = "poker_ledger", "1", True
    money_tools = POKER_TOOLS
    metadata = Base.metadata

    def tools(self, ctx) -> dict[str, PackTool]:
        return tools.build(ctx)

    def draft_kinds(self) -> dict[str, DraftKind]:
        return {"game_draft": DraftKind("game_draft", commit, editable=EDITABLE,
                                        stamps=frozenset({"raw_input", "logged_by", "turn_id"}),
                                        card=card, prepare=prepare, signature=signature, summary=summary, exists=exists)}

    def render(self, result):
        return render.decide(result)

    def contributions(self, session, space_id) -> list:
        """Every non-voided game's edges, losers → winners, unwindowed (review F4)."""
        out = []
        for game in session.scalars(select(Game).where(Game.room_id == int(space_id), Game.voided.is_(False))).all():
            nets = {e.member_id: e.cash_out - e.buy_in for e in game.entries}
            out.extend(game_edges(game.id, game.played_on, nets, house=game.house))
        return out

    def timeline(self, session, space_id, from_date, to_date) -> list[dict]:
        conds = [Game.room_id == int(space_id), Game.voided.is_(False), Game.played_on <= to_date]
        if from_date is not None:
            conds.append(Game.played_on >= from_date)
        return [{"kind": "game", "game_id": g.id, "occurred_on": g.played_on.isoformat(),
                 "created_at": g.created_at.isoformat() if g.created_at else "", "pot": sum(e.buy_in for e in g.entries),
                 "players": len(g.entries), "house": g.house}
                for g in session.scalars(select(Game).where(*conds)).all()]

    def fixtures(self):
        return dict(fixtures.FIXTURES)

    def bind(self, engine) -> None:
        bind(engine)

    def graders(self):
        from packs.poker_ledger import eval as poker_eval
        return dict(poker_eval.GRADERS)

    def content(self) -> dict:
        return {"prompt_body": _read("prompt.md"),
                "skills": [_skill("record-game"), _skill("poker-balances")],
                "rules": [{"slug": "poker", "content": _read("rules/poker.mdc"), "tags": ["money"]}],
                "rubric": None}

    def eval_cases(self) -> list[dict]:
        from packs.poker_ledger.golden import CASES
        return [dict(c) for c in CASES]
