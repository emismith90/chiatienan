"""This host's registration of the framework's ``lunch_ledger`` pack (plan Task 3.3).

The pack lives in ``packs/lunch_ledger`` and knows no host; what it needs from
chiatienan — the deployment's VietQR builder and the restaurant guess for a dish
text — is handed to it here.
"""
from __future__ import annotations

from app import places, qr
from packs.ledger_tools import LedgerToolsPack
from packs.lunch_ledger import LunchLedgerPack


def resolve_place(session, space_id, text: str) -> tuple[dict | None, bool]:
    """``propose_meal``'s place guess: ``(place, confident)``.

    Place resolution is metadata and must NEVER block the bill (design D2). Only
    confident tiers link the meal; a weaker guess rides the card instead, where one
    tap fixes it (D3). ``places`` is looked up at call time so tests that patch
    ``app.places.resolve_one`` keep intercepting it.
    """
    hit, tier = places.resolve_one(session, space_id, text)
    if hit is None:
        return None, False
    return {"id": hit.id, "name": hit.name}, tier in places.CONFIDENT_TIERS


def lunch_ledger_pack() -> LunchLedgerPack:
    return LunchLedgerPack(place_resolver=resolve_place)


def _fallback_note(to_date) -> str:
    """The bank memo when no record names the debt — this host's lunch wording."""
    return f"Chia tien an {to_date.day}/{to_date.month}"


def _draft_kinds():
    from app import drafts
    return drafts.draft_kinds()


def ledger_tools_pack() -> LedgerToolsPack:
    return LedgerToolsPack(qr=qr.make_qr_url, fallback_note=_fallback_note, draft_kinds=_draft_kinds)
