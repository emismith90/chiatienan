"""``packs/lunch_ledger`` runs against a stub host (plan Task 3.3).

The pack imports ``kernos`` and ``ledger_core`` only (``test_layering.py``); this
proves the injection points are the whole contract — a host with an in-memory card
store, a stub QR builder, no place resolver and its own clock/draw gets exactly the
tool behaviour chiatienan gets, because nothing else was ever reached for.
"""
from dataclasses import dataclass, field
from datetime import date

from kernos.adapters.memory import InMemoryCards, InMemoryHistory
from ledger_core import ledger
from packs.lunch_ledger import MONEY_TOOLS, LunchLedgerPack
from tests.test_ledger import _seed_room


@dataclass
class StubCtx:
    """What ``packs.lunch_ledger.tools`` documents it needs — and nothing else."""
    db: object
    space_id: int
    cards: object
    sender_member_id: int | None = None
    turn_mentions: list = field(default_factory=list)
    unknown_names: dict = field(default_factory=dict)
    today: object = lambda: date(2026, 7, 25)
    choice: object = lambda pool: pool[-1]


def _pack_and_ctx(db, n=3):
    room_id, m = _seed_room(db, n)
    cards = InMemoryCards(InMemoryHistory())
    pack = LunchLedgerPack(qr=lambda payee, amount, note: f"stub://{payee.id}/{amount}?{note}")
    return pack, StubCtx(db=db, space_id=room_id, cards=cards, sender_member_id=m[0]), m


def test_the_pack_is_the_eleven_money_tools_and_two_draft_kinds(db):
    pack, ctx, _ = _pack_and_ctx(db)
    tools = pack.tools(ctx)
    assert set(tools) == MONEY_TOOLS and len(tools) == 11
    assert set(pack.draft_kinds()) == {"expense_draft", "payment_draft"} and pack.handles_money
    assert set(pack.fixtures()) == {"add_member", "meal_confirmed", "leave_pending", "confirm_pending", "payment", "settle"}


def test_clock_draw_and_place_resolver_come_from_the_host(db):
    pack, ctx, m = _pack_and_ctx(db)
    tools = pack.tools(ctx)
    assert tools["resolve_date"].execute({"word": "hôm qua"}) == {"ok": True, "date": "2026-07-24"}
    pick = tools["pick_random"].execute({})
    assert pick["chosen"]["id"] == m[-1]                       # the host's draw, not random.choice
    meal = tools["propose_meal"].execute({"participants": m, "total": 90_000, "dish": "bún cá", "day_word": "hôm nay"})
    assert meal["ok"] and meal["occurred_on"] == "2026-07-25"
    assert meal["place_id"] is None and meal["place_guess"] is None   # no resolver → no guess, bill unaffected


def test_cards_and_qr_go_through_the_injected_store_and_builder(db):
    pack, ctx, m = _pack_and_ctx(db)
    tools = pack.tools(ctx)
    card, _ = ctx.cards.create(ctx.space_id, "expense_draft", {
        "payer_member_id": m[0], "member_participants": m, "bill_total": 90_000})
    blocked = tools["settle_period"].execute({})
    assert blocked["type"] == "settle_blocked" and [p["draft_id"] for p in blocked["pending"]] == [card.id]
    assert blocked["pending"][0]["payer_name"] == "M1" and blocked["pending"][0]["participant_count"] == 3
    cancelled = tools["cancel_draft"].execute({"draft_id": card.id})
    assert cancelled == {"ok": True, "type": "draft_cancelled", "draft_id": card.id, "kind": "expense_draft"}
    assert tools["cancel_draft"].execute({"draft_id": card.id})["ok"] is False   # in-memory store raises ValueError
    with db.session() as s:
        ledger.record_meal(s, room_id=ctx.space_id, payer_member_id=m[0], participants=m,
                           total_amount=90_000, occurred_on=date(2026, 7, 25), logged_by="t")
    settled = tools["settle_period"].execute({})
    assert [t["amount"] for t in settled["transfers"]] == [30_000, 30_000]
    assert all(t["qr_url"].startswith(f"stub://{m[0]}/30000?") for t in settled["transfers"])
