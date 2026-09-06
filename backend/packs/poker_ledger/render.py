"""The poker business's outcome decision and bodies (design D3): the game proposal
becomes a `game_draft` card; the committed game gets a `game_result` body; a history
result a typed body. Everything shared is ``packs.ledger_tools.render``."""
from __future__ import annotations

from kernos.kernel import Body, Draft

_DRAFT_FIELDS = ("entries", "house", "played_on", "note", "pot", "players", "nets", "edges_preview")


def _signed(n: int) -> str:
    return f"{n:+,}đ"


def _game_body(att: dict) -> str:
    """"#12 — 5 players, pot 2,500,000đ • winners … / losers …" — from the result dict."""
    nets = att.get("nets") or []
    winners = ", ".join(f"{n['name']} {_signed(n['net'])}" for n in nets if n["net"] > 0) or "—"
    losers = ", ".join(f"{n['name']} {_signed(n['net'])}" for n in nets if n["net"] < 0) or "—"
    house = f" • house {att['house']:,}đ" if att.get("house") else ""
    note = f" — {att['note']}" if att.get("note") else ""
    return (f"Recorded game #{att.get('game_id')}{note}: {att.get('players', len(nets))} players, "
            f"pot {att.get('pot', 0):,}đ{house} • winners: {winners} / losers: {losers}")


def _history_body(att: dict) -> str:
    games = att.get("games") or []
    period = att.get("period") or {}
    window = f"{period.get('from')} → {period.get('to')}" if period.get("from") else f"through {period.get('to')}"
    if not games:
        return f"Games {window}: none recorded."
    lines = [f"Games {window}: {len(games)} game{'' if len(games) == 1 else 's'}."]
    for g in games:
        nets = ", ".join(f"{n['name']} {_signed(n['net'])}" for n in g.get("nets") or [])
        lines.append(f"• #{g['game_id']} {g['played_on']}: pot {g['pot']:,}đ — {nets}")
    return "\n".join(lines)


def decide(result) -> Draft | Body | None:
    proposal = result.last_result("propose_game")
    if proposal and proposal.get("type") == "game_draft":
        return Draft("game_draft", {k: proposal.get(k) for k in _DRAFT_FIELDS})
    history = result.last_result("game_history")
    if history and history.get("type") == "game_history":
        return Body(_history_body(history), {"type": "game_history", **history}, claimed_by_pack=True)
    return None
