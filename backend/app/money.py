"""Deterministic money math — the arithmetic the LLM is never trusted with.

Pure functions, no I/O, no SDK imports:

* :func:`split_shares` — turn a meal (total, participants, per-person
  adjustments) into an exact per-person share map whose values sum to ``total``.
* :func:`prorate_items` — itemized ("ai ăn nấy trả") mode: scale each person's
  own dish price so the shares sum to what was actually paid, spreading any
  discount or delivery fee proportionally.
* :func:`per_payer_transfers` — settle a set of meals by repaying whoever
  fronted each meal (per-pair netted), never reassigning creditors.

All amounts are integer VND. Every validation failure raises :class:`MoneyError`
with a human-readable (Vietnamese-friendly) message; the tool layer turns that
into a clarifying-question result rather than writing a bad ledger row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


class MoneyError(ValueError):
    """A meal/settlement could not be computed from the given inputs."""


@dataclass(frozen=True)
class Transfer:
    from_member: int
    to_member: int
    amount: int


def split_shares(
    total: int,
    participants: list[int],
    adjustments: dict[int, int] | None = None,
    payer_id: int | None = None,
) -> dict[int, int]:
    """Split ``total`` VND across ``participants`` (equal base + signed overrides).

    Rules (design §5):
      * ``base = (total - Σ adjustments) // |P|``; each share = ``base + adj``.
      * The integer division remainder is assigned to the **payer if the payer is
        a participant, else the first participant**, so ``Σ shares == total``.
      * Adjustments may only name participants; a positive adjustment means "ate
        pricier", negative means "ate less".

    Raises :class:`MoneyError` if ``total <= 0``, ``|P| < 1``, an adjustment names
    a non-participant, ``Σ adjustments > total``, or any resulting share is < 0.
    Never returns a negative or non-summing share.
    """
    adjustments = dict(adjustments or {})

    if total <= 0:
        raise MoneyError(f"Total must be greater than 0 (got {total}).")
    if len(participants) < 1:
        raise MoneyError("At least one meal participant is required.")

    # participants must be unique
    if len(set(participants)) != len(participants):
        raise MoneyError("Participant list has duplicates.")

    part_set = set(participants)
    stray = [m for m in adjustments if m not in part_set]
    if stray:
        raise MoneyError(
            "Adjustments apply only to participants; "
            f"these are not in the meal: {sorted(stray)}."
        )

    n = len(participants)
    sum_adj = sum(adjustments.values())
    if sum_adj > total:
        raise MoneyError(
            f"Total adjustments ({sum_adj}) must not exceed the bill total ({total})."
        )

    base = (total - sum_adj) // n
    shares = {m: base + adjustments.get(m, 0) for m in participants}

    negative = [m for m, s in shares.items() if s < 0]
    if negative:
        raise MoneyError(
            f"Adjustments make the share of {sorted(negative)} negative — please review."
        )

    remainder = total - sum(shares.values())
    if remainder:
        target = payer_id if payer_id in part_set else participants[0]
        shares[target] += remainder

    assert sum(shares.values()) == total, "share split must sum to total"
    return shares


def normalize_items(items, participants: list[int]) -> list[dict]:
    """Validate raw itemized entries against the participant list.

    Returns ``[{"member", "amount", "label"}]`` ordered like ``participants``.
    Every participant must appear exactly once: a missing person is far more
    likely a misread bill than a genuine 0đ, and silently charging them nothing
    would quietly push their food onto everyone else. Say so instead.

    Raises :class:`MoneyError` on a malformed entry, an unknown member, a
    duplicate, or an uncovered participant.
    """
    by_member: dict[int, dict] = {}
    for raw in items or []:
        try:
            member, amount = int(raw["member"]), int(raw["amount"])
        except (KeyError, TypeError, ValueError):
            raise MoneyError("Mỗi dòng món cần {member, amount} là số.") from None
        if member in by_member:
            raise MoneyError(
                f"Thành viên {member} có nhiều hơn một dòng món — gộp lại thành một dòng."
            )
        label = raw.get("label") if isinstance(raw, dict) else None
        by_member[member] = {
            "member": member,
            "amount": amount,
            "label": str(label) if label else None,
        }

    part_set = set(participants)
    stray = sorted(m for m in by_member if m not in part_set)
    if stray:
        raise MoneyError(f"Các dòng món này không thuộc bữa ăn: {stray}.")
    missing = [m for m in participants if m not in by_member]
    if missing:
        raise MoneyError(
            f"Ghi theo món cần đủ giá của MỌI người trong bữa — còn thiếu: {missing}."
        )
    return [by_member[m] for m in participants]


def prorate_items(total: int, items: dict[int, int]) -> dict[int, int]:
    """Itemized split: scale each person's own dish price to sum exactly to ``total``.

    A real bill almost never equals the sum of its line items — a promo makes it
    lower, a delivery/service fee makes it higher. Rather than refusing (or
    asking a human to do the arithmetic), every share is scaled by
    ``total / Σ items`` so the discount or fee lands on each person in
    proportion to what they ordered.

    Rounding uses largest-remainder (Hamilton) so ``Σ shares == total`` exactly
    with no share off by more than 1đ; ties break by member id, so the result is
    deterministic. A 0đ item yields a 0đ share (ate nothing).

    Raises :class:`MoneyError` if ``total <= 0``, ``items`` is empty, any item is
    negative, or the items sum to 0.
    """
    if total <= 0:
        raise MoneyError(f"Total must be greater than 0 (got {total}).")
    if not items:
        raise MoneyError("Itemized split needs at least one item.")
    negative = sorted(m for m, v in items.items() if v < 0)
    if negative:
        raise MoneyError(f"Item prices must not be negative: {negative}.")

    gross = sum(items.values())
    if gross <= 0:
        raise MoneyError("The item prices add up to 0 — nothing to split.")

    shares = {m: (v * total) // gross for m, v in items.items()}
    leftover = total - sum(shares.values())
    # Hand the (< |items|) leftover đồng to the largest fractional remainders.
    by_remainder = sorted(items, key=lambda m: (-(items[m] * total % gross), m))
    for m in by_remainder[:leftover]:
        shares[m] += 1

    assert sum(shares.values()) == total, "itemized split must sum to total"
    return shares


def itemized_adjustments(total: int, shares: dict[int, int]) -> dict[int, int]:
    """Express exact per-person ``shares`` as :func:`split_shares` adjustments.

    Itemized meals ride the same ledger path as every other meal, so the
    per-person amounts have to survive as ``adjustments`` around an equal base.
    With ``base = total // n`` and ``adj = share - base``, ``split_shares``
    reproduces ``shares`` exactly: it recomputes the same base (``Σ adj`` is
    ``total - n·base``, so ``(total - Σ adj) // n == base``) and is left with no
    remainder to hand out. Zero adjustments are omitted — they are a no-op.

    ``shares`` must sum to ``total`` (:func:`prorate_items` guarantees it).
    """
    n = len(shares)
    if n < 1:
        raise MoneyError("At least one meal participant is required.")
    if sum(shares.values()) != total:
        raise MoneyError("Itemized shares must sum to the bill total.")
    base = total // n
    return {m: s - base for m, s in shares.items() if s != base}


def per_payer_transfers(
    meals: list[dict],
    payments: list[dict] | None = None,
) -> list[Transfer]:
    """Attribute each debt to the member who actually fronted the meal.

    Repays whoever paid rather than minimising transfer *count* by reassigning
    creditors: for every meal, each participant other than the payer owes the
    payer their own share. Opposing debts within the same pair are netted into a
    single directed transfer, and ad-hoc ``payments``
    (``{"from", "to", "amount"}``) pay a debt down.

    ``meals`` items are ``{"payer_id": int, "shares": {member_id: amount}}``
    (the payer's own share carries no transfer). Ties break by member id so the
    output is deterministic. Members who come out even produce no transfer.
    """
    # owed[(debtor, creditor)] accumulates gross before per-pair netting.
    owed: dict[tuple[int, int], int] = {}

    for meal in meals:
        payer = meal["payer_id"]
        for member, share in meal["shares"].items():
            if member == payer or share == 0:
                continue
            owed[(member, payer)] = owed.get((member, payer), 0) + share

    for p in payments or []:
        frm, to, amount = p["from"], p["to"], p["amount"]
        # A cash payment frm -> to settles that much of what frm owes to.
        owed[(frm, to)] = owed.get((frm, to), 0) - amount

    transfers: list[Transfer] = []
    seen: set[tuple[int, int]] = set()
    for a, b in sorted(owed):
        if (a, b) in seen:
            continue
        seen.add((a, b))
        seen.add((b, a))
        net = owed.get((a, b), 0) - owed.get((b, a), 0)
        if net > 0:
            transfers.append(Transfer(from_member=a, to_member=b, amount=net))
        elif net < 0:
            transfers.append(Transfer(from_member=b, to_member=a, amount=-net))

    return transfers


def split_with_guests(
    total: int,
    member_ids: list[int],
    guest_count: int,
    adjustments: dict[int, int] | None = None,
    payer_id: int | None = None,
) -> dict:
    """Split ``total`` over ``member_ids`` + ``guest_count`` guest heads.

    Guests shrink the per-head number but are never billed (guest-pays-cash): the
    per-head is computed over members + guests, members are billed their share
    (+ adjustments), and the guest heads' shares are dropped (assumed settled in
    cash). The integer-division remainder is assigned by :func:`split_shares`
    (payer if a participant, else the first participant) — the payer is always a
    member here, so the remainder stays inside the tracked (member) total.

    Returns ``{shares, per_head, tracked_total, guest_total, headcount}`` where
    ``shares`` maps member id → VND and ``tracked_total == sum(shares.values())``.
    Raises :class:`MoneyError` (via :func:`split_shares`) on any invalid split, or
    directly if there are no members.
    """
    if len(member_ids) < 1:
        raise MoneyError("At least one member is required in the meal.")
    if guest_count < 0:
        raise MoneyError("Invalid guest count.")
    if any(m < 0 for m in member_ids):
        raise MoneyError("Member id must not be negative (collides with guest ids).")

    # Guest placeholders use negative ids so they never collide with real (positive)
    # member ids; adjustments only ever name members.
    guest_ids = [-(i + 1) for i in range(guest_count)]
    full_participants = list(member_ids) + guest_ids
    full = split_shares(total, full_participants, adjustments, payer_id=payer_id)

    shares = {m: full[m] for m in member_ids}
    tracked_total = sum(shares.values())
    n = len(full_participants)
    sum_adj = sum((adjustments or {}).values())
    per_head = (total - sum_adj) // n
    return {
        "shares": shares,
        "per_head": per_head,
        "tracked_total": tracked_total,
        "guest_total": total - tracked_total,
        "headcount": n,
    }


@dataclass(frozen=True)
class DebtEdge:
    """One participant's gross debt to a meal's payer, for a single meal.

    ``paid`` is the portion covered by ad-hoc payments (attributed oldest-first
    by :func:`apply_payments_fifo`); ``outstanding`` never goes negative.
    """
    debtor: int
    creditor: int
    meal_id: int
    dish: str | None
    occurred_on: date
    amount: int
    paid: int = 0

    @property
    def outstanding(self) -> int:
        return self.amount - self.paid

    @property
    def status(self) -> str:
        if self.paid <= 0:
            return "unpaid"
        return "paid" if self.paid >= self.amount else "partial"


def build_debt_edges(meals: list[dict]) -> list[DebtEdge]:
    """One :class:`DebtEdge` per (participant≠payer, meal), gross, ``paid=0``."""
    edges: list[DebtEdge] = []
    for m in meals:
        payer = m["payer_id"]
        for member, share in m["shares"].items():
            if member == payer or share == 0:
                continue
            edges.append(DebtEdge(
                debtor=member, creditor=payer, meal_id=m["meal_id"],
                dish=m.get("dish"), occurred_on=m["occurred_on"], amount=share,
            ))
    return edges


def apply_payments_fifo(edges: list[DebtEdge], payments: list[dict] | None) -> list[DebtEdge]:
    """Attribute payments to edges. A payment with ``meal_id`` settles that exact
    edge first (⑦ quick action); the rest apply to the pair oldest-meal-first.

    Returns new edges with ``paid`` set. Payment beyond an edge/pair total is
    ignored (never makes ``outstanding`` negative). Deterministic:
    ``(occurred_on, meal_id)`` order.
    """
    targeted: dict[tuple[int, int, int], int] = {}
    pool: dict[tuple[int, int], int] = {}
    for p in payments or []:
        mid = p.get("meal_id")
        if mid is not None:
            targeted[(p["from"], p["to"], mid)] = targeted.get((p["from"], p["to"], mid), 0) + p["amount"]
        else:
            pool[(p["from"], p["to"])] = pool.get((p["from"], p["to"]), 0) + p["amount"]
    out: list[DebtEdge] = []
    for e in sorted(edges, key=lambda e: (e.occurred_on, e.meal_id)):
        paid = 0
        tk = (e.debtor, e.creditor, e.meal_id)
        if targeted.get(tk):
            take = min(targeted[tk], e.amount)
            targeted[tk] -= take
            paid += take
        remaining = e.amount - paid
        if remaining > 0:
            avail = pool.get((e.debtor, e.creditor), 0)
            take = min(avail, remaining)
            if take:
                pool[(e.debtor, e.creditor)] = avail - take
                paid += take
        out.append(DebtEdge(e.debtor, e.creditor, e.meal_id, e.dish, e.occurred_on, e.amount, paid))
    return out
