"""``packs/lunch_ledger`` + ``packs/ledger_tools`` run against a stub host (plan Tasks 3.3, 6.1).

Both packs import ``kernos`` and ``ledger_core`` only (``test_layering.py``); this proves
the injection points are the whole contract — a host with an in-memory card store, a
stub QR builder, its own bank-memo wording, no place resolver and its own clock/draw
gets exactly the tool behaviour chiatienan gets, because nothing else was ever reached
for.
"""
from dataclasses import dataclass, field
from datetime import date

from kernos.adapters.memory import InMemoryCards, InMemoryHistory
from ledger_core import ledger
from packs.ledger_tools import LEDGER_TOOLS, LedgerToolsPack
from packs.lunch_ledger import LUNCH_TOOLS, LunchLedgerPack
from tests.test_ledger import _seed_room


@dataclass
class StubCtx:
    """What the packs' tool modules document they need — and nothing else."""
    db: object
    space_id: int
    cards: object
    sender_member_id: int | None = None
    turn_mentions: list = field(default_factory=list)
    unknown_names: dict = field(default_factory=dict)
    today: object = lambda: date(2026, 7, 25)
    choice: object = lambda pool: pool[-1]


def _packs_and_ctx(db, n=3):
    room_id, m = _seed_room(db, n)
    cards = InMemoryCards(InMemoryHistory())
    lunch = LunchLedgerPack()
    kinds = {}
    shared = LedgerToolsPack(qr=lambda payee, amount, note: f"stub://{payee.id}/{amount}?{note}",
                             fallback_note=lambda d: f"Poker night {d.day}/{d.month}", draft_kinds=lambda: kinds)
    kinds.update(lunch.draft_kinds()); kinds.update(shared.draft_kinds())
    ctx = StubCtx(db=db, space_id=room_id, cards=cards, sender_member_id=m[0])
    return lunch, shared, ctx, m


def test_the_two_packs_partition_the_eleven_money_tools_and_three_kinds(db):
    lunch, shared, ctx, _ = _packs_and_ctx(db)
    lt, st = lunch.tools(ctx), shared.tools(ctx)
    assert set(lt) == LUNCH_TOOLS == {"propose_meal", "void_meal"} and set(st) == LEDGER_TOOLS and len(st) == 9
    assert set(lunch.draft_kinds()) == {"expense_draft"} and set(shared.draft_kinds()) == {"payment_draft"}
    assert lunch.handles_money and shared.handles_money
    assert set(lunch.fixtures()) == {"meal_confirmed", "leave_pending", "confirm_pending"}
    assert set(shared.fixtures()) == {"add_member", "payment", "settle"}
    assert "settle_period" in shared.money_tools and "find_members" not in shared.money_tools


def test_clock_draw_and_place_resolver_come_from_the_host(db):
    lunch, shared, ctx, m = _packs_and_ctx(db)
    tools = {**lunch.tools(ctx), **shared.tools(ctx)}
    assert tools["resolve_date"].execute({"word": "hôm qua"}) == {"ok": True, "date": "2026-07-24"}
    pick = tools["pick_random"].execute({})
    assert pick["chosen"]["id"] == m[-1]                       # the host's draw, not random.choice
    meal = tools["propose_meal"].execute({"participants": m, "total": 90_000, "dish": "bún cá", "day_word": "hôm nay"})
    assert meal["ok"] and meal["occurred_on"] == "2026-07-25"
    assert meal["place_id"] is None and meal["place_guess"] is None   # no resolver → no guess, bill unaffected


def test_cards_qr_memo_and_pending_descriptions_go_through_the_injected_hooks(db):
    lunch, shared, ctx, m = _packs_and_ctx(db)
    tools = shared.tools(ctx)
    card, _ = ctx.cards.create(ctx.space_id, "expense_draft", {
        "payer_member_id": m[0], "member_participants": m, "bill_total": 90_000})
    blocked = tools["settle_period"].execute({})
    assert blocked["type"] == "settle_blocked" and [p["draft_id"] for p in blocked["pending"]] == [card.id]
    # the meal draft is described by the lunch pack's kind, not by this pack knowing meals
    assert blocked["pending"][0] == {"draft_id": card.id, "kind": "meal", "payer_name": "M1", "bill_total": 90_000, "participant_count": 3}
    other, _ = ctx.cards.create(ctx.space_id, "mystery_draft", {"x": 1})
    assert tools["settle_period"].execute({})["pending"][1] == {"draft_id": other.id, "kind": "mystery_draft", "label": "mystery_draft"}
    cancelled = tools["cancel_draft"].execute({"draft_id": card.id})
    assert cancelled == {"ok": True, "type": "draft_cancelled", "draft_id": card.id, "kind": "expense_draft"}
    tools["cancel_draft"].execute({"draft_id": other.id})
    assert tools["cancel_draft"].execute({"draft_id": card.id})["ok"] is False   # in-memory store raises ValueError
    with db.session() as s:
        ledger.record_meal(s, room_id=ctx.space_id, payer_member_id=m[0], participants=m,
                           total_amount=90_000, occurred_on=date(2026, 7, 25), logged_by="t")
    settled = tools["settle_period"].execute({})
    rows = {(t["from_id"], t["to_id"]): t for t in settled["transfers"]}
    assert rows[(m[1], m[0])]["amount"] == 30_000 and rows[(m[1], m[0])]["qr_url"].startswith(f"stub://{m[0]}/30000?")
    assert rows[(m[1], m[0])]["note"].startswith("M2: ")                  # the memo from ledger_core.notes
