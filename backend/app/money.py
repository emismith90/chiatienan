"""Deterministic money math — the arithmetic the LLM is never trusted with.

Two pure functions, no I/O, no SDK imports:

* :func:`split_shares` — turn a meal (total, participants, per-person
  adjustments) into an exact per-person share map whose values sum to ``total``.
* :func:`net_transfers` — net a balance map into the greedy minimal set of
  debtor→creditor transfers.

All amounts are integer VND. Every validation failure raises :class:`MoneyError`
with a human-readable (Vietnamese-friendly) message; the tool layer turns that
into a clarifying-question result rather than writing a bad ledger row.
"""
from __future__ import annotations

from dataclasses import dataclass


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
        raise MoneyError(f"Tổng tiền phải lớn hơn 0 (nhận {total}).")
    if len(participants) < 1:
        raise MoneyError("Cần ít nhất một người tham gia bữa ăn.")

    # participants must be unique
    if len(set(participants)) != len(participants):
        raise MoneyError("Danh sách người tham gia bị trùng.")

    part_set = set(participants)
    stray = [m for m in adjustments if m not in part_set]
    if stray:
        raise MoneyError(
            "Điều chỉnh chỉ áp dụng cho người tham gia; "
            f"những người này không ăn: {sorted(stray)}."
        )

    n = len(participants)
    sum_adj = sum(adjustments.values())
    if sum_adj > total:
        raise MoneyError(
            f"Tổng điều chỉnh ({sum_adj}) không được vượt quá tổng tiền ({total})."
        )

    base = (total - sum_adj) // n
    shares = {m: base + adjustments.get(m, 0) for m in participants}

    negative = [m for m, s in shares.items() if s < 0]
    if negative:
        raise MoneyError(
            f"Điều chỉnh làm phần của {sorted(negative)} bị âm — vui lòng kiểm tra lại."
        )

    remainder = total - sum(shares.values())
    if remainder:
        target = payer_id if payer_id in part_set else participants[0]
        shares[target] += remainder

    assert sum(shares.values()) == total, "share split must sum to total"
    return shares


def net_transfers(balances: dict[int, int]) -> list[Transfer]:
    """Greedy netting: repeatedly settle the biggest debtor against the biggest
    creditor. Fewest transfers in practice (truly-minimal is NP-hard).

    ``balances`` maps member id → signed VND (``paid - consumed``): positive is a
    creditor (owed money), negative is a debtor (owes). Ties break by member id so
    the output is deterministic. Members with a zero balance produce no transfer.
    """
    debtors = {m: -b for m, b in balances.items() if b < 0}
    creditors = {m: b for m, b in balances.items() if b > 0}

    transfers: list[Transfer] = []
    while debtors and creditors:
        # max debtor / max creditor, ties broken by member id (deterministic)
        d = max(debtors, key=lambda m: (debtors[m], -m))
        c = max(creditors, key=lambda m: (creditors[m], -m))
        amount = min(debtors[d], creditors[c])

        transfers.append(Transfer(from_member=d, to_member=c, amount=amount))

        debtors[d] -= amount
        creditors[c] -= amount
        if debtors[d] == 0:
            del debtors[d]
        if creditors[c] == 0:
            del creditors[c]

    return transfers
