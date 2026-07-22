# Money-Reporting Overhaul — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the bot's money reporting — record real cash payments against gross directional debt (with clarify-on-ambiguity), add sender-scoped statements, a chronological group summary, explicit meal dates, and a read-only ledger API — all with tool-owned numbers.

**Architecture:** One new primitive — *gross debt per direction, broken down by meal* (`money.py` pure helpers + `ledger.py` queries) — feeds three tools (`member_statement`, `get_period_summary`, the fixed `propose_payment`) plus a `GET /ledger` REST endpoint. Chat bodies stay server-assembled from tool dicts (design D3). No schema change.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (SQLite/WAL), pytest. Cursor SDK `CustomTool` for the agent tools.

**Spec:** [`docs/superpowers/specs/2026-07-22-bot-money-reporting-overhaul-design.md`](../specs/2026-07-22-bot-money-reporting-overhaul-design.md)

## Global Constraints

- **Money-safety (D3):** tools own every number. The model passes a user-stated amount in at most once; it never computes or transcribes a tool-produced amount. The ② clarify loop re-calls with a `mode` token, never a transcribed amount.
- **Amounts** are integer VND throughout. **Dates** are `datetime.date` in ICT (`app.clock.today_ict()`); period windows are inclusive, `from=None` means "since the beginning of the ledger".
- **No schema change.** `meals.occurred_on`, `meals.dish`, `meals.created_at`, `payments.*` all already exist.
- **Ledger single-writer:** writes are serialized by `chat._agent_lock` / the draft-commit routes. Read tools open their own short-lived session (`db.session()`); never widen a write's transaction.
- **Tests:** TDD — write the failing test first, watch it fail, implement minimally, watch it pass, commit. Run backend tests from `backend/` with the venv active: `cd backend && source .venv/bin/activate`.

## File Structure

- `backend/app/money.py` — **modify**: add `DebtEdge`, `build_debt_edges`, `apply_payments_fifo` (pure). `per_payer_transfers` unchanged.
- `backend/app/ledger.py` — **modify**: add `debt_breakdown`, `period_timeline`.
- `backend/app/periods.py` — **modify**: add `resolve_date`.
- `backend/app/tools.py` — **modify**: fix `propose_payment`; add `member_statement`, `get_period_summary`, `resolve_date` tools; add `occurred_on` to `propose_meal`.
- `backend/app/chat.py` — **modify**: map new tool results to attachments; add `_statement_body`, `_summary_body`.
- `backend/app/prompt.py` — **modify**: inject today's date + anti-narration line.
- `backend/app/agent_skills/skills/` — **modify/rename**: `settle-period`→`balances`, `record-payment`, `record-meal`.
- `backend/app/main.py` — **modify**: add `GET /api/rooms/{id}/ledger`; publish `ledger:changed` on commits.
- `backend/tests/` — **create**: `test_debt_breakdown.py`, `test_propose_payment_grossdir.py`, `test_member_statement.py`, `test_period_summary.py`, `test_resolve_date.py`, `test_ledger_endpoint.py`; extend existing chat tests.

---

### Task 1: Gross-debt primitive (pure money helpers)

**Files:**
- Modify: `backend/app/money.py`
- Test: `backend/tests/test_debt_breakdown.py` (new; pure-function half)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `DebtEdge(debtor:int, creditor:int, meal_id:int, dish:str|None, occurred_on:date, amount:int, paid:int=0)` with `.outstanding:int` and `.status:str` (`"unpaid"|"partial"|"paid"`).
  - `build_debt_edges(meals: list[dict]) -> list[DebtEdge]` — `meals` items are `{"meal_id", "payer_id", "dish", "occurred_on", "shares": {member_id: amount}}`.
  - `apply_payments_fifo(edges: list[DebtEdge], payments: list[dict]) -> list[DebtEdge]` — `payments` items `{"from","to","amount"}`; sets each edge's `paid` oldest-first per `(debtor,creditor)`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_debt_breakdown.py`:

```python
from datetime import date
from app.money import DebtEdge, build_debt_edges, apply_payments_fifo


def _meal(meal_id, payer, shares, day, dish="x"):
    return {"meal_id": meal_id, "payer_id": payer, "dish": dish,
            "occurred_on": date(2026, 7, day), "shares": shares}


def test_build_edges_skips_payer_and_zero():
    # meal #2: Linh(6) paid, 5 share 61k each
    edges = build_debt_edges([_meal(2, 6, {4: 61000, 6: 61000, 7: 61000, 8: 61000, 9: 61000}, 21)])
    pairs = {(e.debtor, e.creditor): e.amount for e in edges}
    assert (6, 6) not in pairs                 # payer owes nobody
    assert pairs == {(4, 6): 61000, (7, 6): 61000, (8, 6): 61000, (9, 6): 61000}


def test_fifo_marks_paid_oldest_first():
    edges = build_debt_edges([
        _meal(2, 6, {9: 61000}, 21),   # Giang(9) owes Linh(6) 61k (older)
        _meal(3, 6, {9: 40000}, 22),   # Giang owes Linh 40k (newer)
    ])
    out = apply_payments_fifo(edges, [{"from": 9, "to": 6, "amount": 61000}])
    by_meal = {e.meal_id: e for e in out}
    assert by_meal[2].status == "paid" and by_meal[2].outstanding == 0
    assert by_meal[3].status == "unpaid" and by_meal[3].outstanding == 40000


def test_fifo_partial():
    edges = build_debt_edges([_meal(2, 6, {9: 61000}, 21)])
    out = apply_payments_fifo(edges, [{"from": 9, "to": 6, "amount": 20000}])
    assert out[0].status == "partial" and out[0].paid == 20000 and out[0].outstanding == 41000


def test_fifo_overpayment_floors_at_zero():
    edges = build_debt_edges([_meal(2, 6, {9: 61000}, 21)])
    out = apply_payments_fifo(edges, [{"from": 9, "to": 6, "amount": 90000}])
    assert out[0].paid == 61000 and out[0].outstanding == 0   # leftover ignored, never negative
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_debt_breakdown.py -v`
Expected: FAIL — `ImportError: cannot import name 'DebtEdge'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/money.py`, add `from datetime import date` to the imports, and append:

```python
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
    """Attribute each ``(from,to)`` payment to that pair's edges oldest-meal-first.

    Returns new edges with ``paid`` set. Payment beyond a pair's total meal debt
    is ignored (never makes ``outstanding`` negative). Deterministic:
    ``(occurred_on, meal_id)`` order.
    """
    pool: dict[tuple[int, int], int] = {}
    for p in payments or []:
        pool[(p["from"], p["to"])] = pool.get((p["from"], p["to"]), 0) + p["amount"]
    out: list[DebtEdge] = []
    for e in sorted(edges, key=lambda e: (e.occurred_on, e.meal_id)):
        avail = pool.get((e.debtor, e.creditor), 0)
        paid = min(avail, e.amount)
        if paid:
            pool[(e.debtor, e.creditor)] = avail - paid
        out.append(DebtEdge(e.debtor, e.creditor, e.meal_id, e.dish, e.occurred_on, e.amount, paid))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_debt_breakdown.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/money.py backend/tests/test_debt_breakdown.py
git commit -m "feat(be): gross-debt-by-meal primitive (DebtEdge + FIFO payment attribution)"
```

---

### Task 2: `debt_breakdown` + `period_timeline` (ledger queries)

**Files:**
- Modify: `backend/app/ledger.py`
- Test: `backend/tests/test_debt_breakdown.py` (extend with DB half)

**Interfaces:**
- Consumes: `money.build_debt_edges`, `money.apply_payments_fifo`; existing `Meal`, `MealShare`, `Payment`.
- Produces:
  - `debt_breakdown(session, room_id:int, from_date:date|None, to_date:date) -> list[DebtEdge]`.
  - `period_timeline(session, room_id:int, from_date:date|None, to_date:date) -> list[dict]` — events `{"kind":"meal","meal_id","payer_id","dish","occurred_on","total","participant_ids","created_at"}` and `{"kind":"payment","payment_id","from_id","to_id","amount","occurred_on","created_at"}`, sorted by `(occurred_on, created_at)`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_debt_breakdown.py`:

```python
import pytest
from app.db import Database
from app import ledger, roster


@pytest.fixture
def db(tmp_path):
    d = Database(f"sqlite:///{tmp_path}/t.db")
    d.create_all()
    return d


def _mk_room_members(s):
    from app.models import Room, Member
    r = Room(name="t", invite_token="tok")
    s.add(r); s.flush()
    ms = {}
    for name in ("Linh", "Giang", "Dung"):
        m = Member(room_id=r.id, display_name=name, nickname=name)
        s.add(m); s.flush(); ms[name] = m.id
    return r.id, ms


def test_debt_breakdown_two_way(db):
    with db.session() as s:
        room, m = _mk_room_members(s)
        # Linh pays 122k split Linh+Giang -> Giang owes Linh 61k
        ledger.record_meal(s, room_id=room, payer_member_id=m["Linh"],
                           participants=[m["Linh"], m["Giang"]], total_amount=122000,
                           dish="bun bo", occurred_on=date(2026, 7, 21))
        # Giang pays 150k split Linh+Giang -> Linh owes Giang 75k
        ledger.record_meal(s, room_id=room, payer_member_id=m["Giang"],
                           participants=[m["Linh"], m["Giang"]], total_amount=150000,
                           dish="nem", occurred_on=date(2026, 7, 22))
        edges = ledger.debt_breakdown(s, room, None, date(2026, 7, 22))
        owe = {(e.debtor, e.creditor): e.outstanding for e in edges}
        assert owe[(m["Giang"], m["Linh"])] == 61000   # gross, NOT netted
        assert owe[(m["Linh"], m["Giang"])] == 75000


def test_period_timeline_orders_by_date_then_created(db):
    with db.session() as s:
        room, m = _mk_room_members(s)
        ledger.record_meal(s, room_id=room, payer_member_id=m["Linh"],
                           participants=[m["Linh"], m["Giang"]], total_amount=122000,
                           dish="bun bo", occurred_on=date(2026, 7, 21))
        ledger.record_payment(s, room_id=room, from_member_id=m["Giang"],
                              to_member_id=m["Linh"], amount=61000, occurred_on=date(2026, 7, 22))
        tl = ledger.period_timeline(s, room, None, date(2026, 7, 22))
        assert [e["kind"] for e in tl] == ["meal", "payment"]
        assert tl[0]["dish"] == "bun bo" and tl[1]["amount"] == 61000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_debt_breakdown.py -v`
Expected: FAIL — `AttributeError: module 'app.ledger' has no attribute 'debt_breakdown'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/ledger.py`, extend the money import:

```python
from app.money import apply_payments_fifo, build_debt_edges, split_with_guests
```

Append (after `period_transfer_inputs`):

```python
def debt_breakdown(
    session: Session, room_id: int, from_date: date | None, to_date: date
) -> list["DebtEdge"]:
    """Gross per-(debtor, creditor, meal) edges with FIFO-attributed payments.

    Same window semantics as :func:`period_balances`; excludes voided meals and
    voided payments. Does NOT net opposing debts — that is the whole point (a
    person's real debt to a creditor, per meal).
    """
    meal_conds = [Meal.room_id == room_id, Meal.voided.is_(False), Meal.occurred_on <= to_date]
    if from_date is not None:
        meal_conds.append(Meal.occurred_on >= from_date)
    meal_rows = session.execute(
        select(Meal.id, Meal.payer_member_id, Meal.dish, Meal.occurred_on).where(*meal_conds)
    ).all()
    by_id = {
        mid: {"meal_id": mid, "payer_id": payer, "dish": dish, "occurred_on": occ, "shares": {}}
        for mid, payer, dish, occ in meal_rows
    }
    if by_id:
        for meal_id, member_id, amt in session.execute(
            select(MealShare.meal_id, MealShare.member_id, MealShare.share_amount)
            .where(MealShare.meal_id.in_(by_id.keys()))
        ).all():
            by_id[meal_id]["shares"][member_id] = amt

    pay_conds = [Payment.room_id == room_id, Payment.voided.is_(False), Payment.occurred_on <= to_date]
    if from_date is not None:
        pay_conds.append(Payment.occurred_on >= from_date)
    payments = [
        {"from": f, "to": t, "amount": a}
        for f, t, a in session.execute(
            select(Payment.from_member_id, Payment.to_member_id, Payment.amount).where(*pay_conds)
        ).all()
    ]
    return apply_payments_fifo(build_debt_edges(list(by_id.values())), payments)


def period_timeline(
    session: Session, room_id: int, from_date: date | None, to_date: date
) -> list[dict]:
    """Meals + payments in the window as one list ordered by (occurred_on, created_at)."""
    meal_conds = [Meal.room_id == room_id, Meal.voided.is_(False), Meal.occurred_on <= to_date]
    pay_conds = [Payment.room_id == room_id, Payment.voided.is_(False), Payment.occurred_on <= to_date]
    if from_date is not None:
        meal_conds.append(Meal.occurred_on >= from_date)
        pay_conds.append(Payment.occurred_on >= from_date)

    events: list[dict] = []
    meal_rows = session.execute(
        select(Meal.id, Meal.payer_member_id, Meal.dish, Meal.occurred_on,
               Meal.total_amount, Meal.created_at).where(*meal_conds)
    ).all()
    meal_ids = [row[0] for row in meal_rows]
    participants: dict[int, list[int]] = {mid: [] for mid in meal_ids}
    if meal_ids:
        for meal_id, member_id in session.execute(
            select(MealShare.meal_id, MealShare.member_id)
            .where(MealShare.meal_id.in_(meal_ids))
        ).all():
            participants[meal_id].append(member_id)
    for mid, payer, dish, occ, total, created in meal_rows:
        events.append({"kind": "meal", "meal_id": mid, "payer_id": payer, "dish": dish,
                       "occurred_on": occ.isoformat(), "total": total,
                       "participant_ids": participants.get(mid, []),
                       "created_at": created.isoformat() if created else ""})
    for pid, f, t, amt, occ, created in session.execute(
        select(Payment.id, Payment.from_member_id, Payment.to_member_id, Payment.amount,
               Payment.occurred_on, Payment.created_at).where(*pay_conds)
    ).all():
        events.append({"kind": "payment", "payment_id": pid, "from_id": f, "to_id": t,
                       "amount": amt, "occurred_on": occ.isoformat(),
                       "created_at": created.isoformat() if created else ""})
    events.sort(key=lambda e: (e["occurred_on"], e["created_at"]))
    return events
```

Add a `TYPE_CHECKING` import so the annotation resolves without a runtime cycle — at the top of `ledger.py`:

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.money import DebtEdge
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_debt_breakdown.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ledger.py backend/tests/test_debt_breakdown.py
git commit -m "feat(be): debt_breakdown + period_timeline ledger queries"
```

---

### Task 3: Fix `propose_payment` — gross-directional pay-off + clarify (②, the live bug)

**Files:**
- Modify: `backend/app/tools.py` (`propose_payment`, `_PROPOSE_PAYMENT_SCHEMA`)
- Test: `backend/tests/test_propose_payment_grossdir.py` (new)

**Interfaces:**
- Consumes: `ledger.debt_breakdown`, existing `ToolContext`, `resolve_period`, `last_settlement`.
- Produces (results the `record-payment` skill/chat rely on): result `type` is one of
  `payment_draft` (`{from_member_id,to_member_id,amount,note,from_name,to_name}`),
  `payment_settled` (`{from,to}`), `nothing_owed` (`{from,to,reverse_amount}`),
  `payment_ambiguous` (`{from,to,gross:{from_member_id,to_member_id,amount}, offset:{from_member_id,to_member_id,amount}}`).
  New optional arg `mode: "gross"|"offset"`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_propose_payment_grossdir.py`:

```python
from datetime import date
import pytest
from app.db import Database
from app import ledger
from app.tools import ToolContext, build_tools


@pytest.fixture
def setup(tmp_path):
    d = Database(f"sqlite:///{tmp_path}/t.db")
    d.create_all()
    from app.models import Room, Member
    with d.session() as s:
        r = Room(name="t", invite_token="tok"); s.add(r); s.flush()
        m = {}
        for name in ("Linh", "Giang", "Dung"):
            x = Member(room_id=r.id, display_name=name, nickname=name)
            s.add(x); s.flush(); m[name] = x.id
        # Giang owes Linh 61k (bun bo, Linh paid); Linh owes Giang 75k (nem, Giang paid)
        ledger.record_meal(s, room_id=r.id, payer_member_id=m["Linh"],
                           participants=[m["Linh"], m["Giang"], m["Dung"]], total_amount=183000,
                           dish="bun bo", occurred_on=date(2026, 7, 21))  # 61k each
        ledger.record_meal(s, room_id=r.id, payer_member_id=m["Giang"],
                           participants=[m["Linh"], m["Giang"]], total_amount=150000,
                           dish="nem", occurred_on=date(2026, 7, 22))     # 75k each
        room_id = r.id
    return d, room_id, m


def _tools(d, room_id, sender):
    return build_tools(ToolContext(db=d, room_id=room_id, sender_member_id=sender))


def test_one_sided_autofills_gross(setup):
    d, room, m = setup
    # Dung owes Linh 61k, nothing the other way -> unambiguous draft
    res = _tools(d, room, m["Dung"])["propose_payment"].execute({"to": m["Linh"]})
    assert res["type"] == "payment_draft" and res["amount"] == 61000


def test_two_way_is_ambiguous(setup):
    d, room, m = setup
    res = _tools(d, room, m["Giang"])["propose_payment"].execute({"to": m["Linh"]})
    assert res["type"] == "payment_ambiguous"
    assert res["gross"]["amount"] == 61000              # Giang -> Linh, full
    assert res["offset"]["amount"] == 14000             # net 75k-61k
    assert res["offset"]["from_member_id"] == m["Linh"] # net direction flips


def test_mode_gross_records_full(setup):
    d, room, m = setup
    res = _tools(d, room, m["Giang"])["propose_payment"].execute({"to": m["Linh"], "mode": "gross"})
    assert res["type"] == "payment_draft"
    assert res["from_member_id"] == m["Giang"] and res["to_member_id"] == m["Linh"] and res["amount"] == 61000


def test_settled_when_truly_zero(setup):
    d, room, m = setup
    with d.session() as s:
        ledger.record_payment(s, room_id=room, from_member_id=m["Dung"],
                              to_member_id=m["Linh"], amount=61000, occurred_on=date(2026, 7, 22))
    res = _tools(d, room, m["Dung"])["propose_payment"].execute({"to": m["Linh"]})
    assert res["type"] == "payment_settled"


def test_explicit_amount_never_netted(setup):
    d, room, m = setup
    res = _tools(d, room, m["Giang"])["propose_payment"].execute({"to": m["Linh"], "amount": 61000})
    assert res["type"] == "payment_draft" and res["amount"] == 61000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_propose_payment_grossdir.py -v`
Expected: FAIL — current pay-off returns `payment_settled` for the two-way case (asserts on `payment_ambiguous` fail).

- [ ] **Step 3: Write minimal implementation**

In `backend/app/tools.py`, add `mode` to `_PROPOSE_PAYMENT_SCHEMA["properties"]`:

```python
        "mode": {
            "type": "string",
            "enum": ["gross", "offset"],
            "description": "For a two-way pair only: 'gross' = pay the full amount `from` owes `to`; 'offset' = settle the net difference. Omit otherwise.",
        },
```

Replace the `amount is None` pay-off block (the branch from `if amount is None:` through `amount = match.amount`) in `propose_payment` with:

```python
            if amount is None:
                # Gross directional pay-off over the open (since_last) period. We
                # do NOT net A<->B: a real cash payment settles what `from` owes
                # `to`, per meal. Netting is only for settle_period's QR.
                last = ledger.last_settlement(s, ctx.room_id)
                period = resolve_period(
                    "since_last", today=today_ict(),
                    last_settlement_to=last.period_to if last else None,
                )
                edges = ledger.debt_breakdown(s, ctx.room_id, period["from"], period["to"])
                gross_ft = sum(e.outstanding for e in edges
                               if e.debtor == frm_id and e.creditor == to_id)
                gross_tf = sum(e.outstanding for e in edges
                               if e.debtor == to_id and e.creditor == frm_id)
                mode = args.get("mode")

                if gross_ft <= 0 and gross_tf <= 0:
                    return {"ok": True, "type": "payment_settled",
                            "from": {"id": frm_id, "name": names.get(frm_id, "?")},
                            "to": {"id": to_id, "name": names.get(to_id, "?")}}
                if gross_ft > 0 and gross_tf <= 0:
                    amount = gross_ft
                elif gross_ft <= 0 and gross_tf > 0:
                    return {"ok": True, "type": "nothing_owed",
                            "from": {"id": frm_id, "name": names.get(frm_id, "?")},
                            "to": {"id": to_id, "name": names.get(to_id, "?")},
                            "reverse_amount": gross_tf}
                elif mode == "gross":
                    amount = gross_ft
                elif mode == "offset":
                    net = gross_ft - gross_tf
                    if net == 0:
                        return {"ok": True, "type": "payment_settled",
                                "from": {"id": frm_id, "name": names.get(frm_id, "?")},
                                "to": {"id": to_id, "name": names.get(to_id, "?")}}
                    if net > 0:
                        amount = net
                    else:  # net direction flips: to -> frm
                        frm_id, to_id = to_id, frm_id
                        amount = -net
                else:
                    return {
                        "ok": True, "type": "payment_ambiguous",
                        "from": {"id": frm_id, "name": names.get(frm_id, "?")},
                        "to": {"id": to_id, "name": names.get(to_id, "?")},
                        "gross": {"from_member_id": frm_id, "to_member_id": to_id, "amount": gross_ft},
                        "offset": (
                            {"from_member_id": frm_id, "to_member_id": to_id, "amount": gross_ft - gross_tf}
                            if gross_ft >= gross_tf else
                            {"from_member_id": to_id, "to_member_id": frm_id, "amount": gross_tf - gross_ft}
                        ),
                    }
```

The final `return {... "type": "payment_draft" ...}` already uses `frm_id`/`to_id`/`amount`/`names`, so the `offset`-flip is picked up automatically. (`_names_for` was called for both ids above, so a flipped `to_id` still resolves.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_propose_payment_grossdir.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/tools.py backend/tests/test_propose_payment_grossdir.py
git commit -m "fix(be): record payments against gross directional debt; clarify two-way pairs (②)"
```

---

### Task 4: `member_statement` tool (①③)

**Files:**
- Modify: `backend/app/tools.py` (new tool + register)
- Test: `backend/tests/test_member_statement.py` (new)

**Interfaces:**
- Consumes: `ledger.debt_breakdown`, `ledger.period_balances`, `_names_for`, `resolve_period`.
- Produces: result `{ok, type:"statement", member:{id,name}, period:{from,to}, owe:[{creditor_id,name,meal_id,dish,occurred_on,amount,status}], owed:[…debtor_id,name…], net:int}`. Registered tool name `member_statement`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_member_statement.py`:

```python
from datetime import date
import pytest
from app.db import Database
from app import ledger
from app.tools import ToolContext, build_tools


@pytest.fixture
def setup(tmp_path):
    d = Database(f"sqlite:///{tmp_path}/t.db"); d.create_all()
    from app.models import Room, Member
    with d.session() as s:
        r = Room(name="t", invite_token="tok"); s.add(r); s.flush()
        m = {}
        for name in ("Linh", "Giang"):
            x = Member(room_id=r.id, display_name=name, nickname=name); s.add(x); s.flush()
            m[name] = x.id
        ledger.record_meal(s, room_id=r.id, payer_member_id=m["Linh"],
                           participants=[m["Linh"], m["Giang"]], total_amount=122000,
                           dish="bun bo", occurred_on=date(2026, 7, 21))
        room = r.id
    return d, room, m


def test_statement_defaults_to_sender_and_splits_directions(setup):
    d, room, m = setup
    res = build_tools(ToolContext(db=d, room_id=room, sender_member_id=m["Giang"]))["member_statement"].execute({})
    assert res["member"]["id"] == m["Giang"]
    assert len(res["owe"]) == 1 and res["owe"][0]["name"] == "Linh"
    assert res["owe"][0]["amount"] == 61000 and res["owe"][0]["status"] == "unpaid"
    assert res["owe"][0]["dish"] == "bun bo"
    assert res["owed"] == []
    assert res["net"] == -61000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_member_statement.py -v`
Expected: FAIL — `KeyError: 'member_statement'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/tools.py`, define `member_statement` inside `build_tools` (near `get_period_balances`):

```python
    def member_statement(args, _tool_ctx=None) -> dict:
        args = args or {}
        member = args.get("member") or ctx.sender_member_id
        if not member:
            return _err("Không xác định được thành viên.")
        member = int(member)
        with db.session() as s:
            last = ledger.last_settlement(s, ctx.room_id)
            period = resolve_period(
                args.get("keyword"), today=today_ict(),
                last_settlement_to=last.period_to if last else None,
            )
            edges = ledger.debt_breakdown(s, ctx.room_id, period["from"], period["to"])
            ids = {e.debtor for e in edges} | {e.creditor for e in edges} | {member}
            names = _names_for(s, ctx.room_id, ids)

        def _row(e, other_id):
            return {"creditor_id" if other_id == e.creditor else "debtor_id": other_id,
                    "name": names.get(other_id, "?"), "meal_id": e.meal_id, "dish": e.dish,
                    "occurred_on": e.occurred_on.isoformat(), "amount": e.outstanding,
                    "status": e.status}

        owe = [_row(e, e.creditor) for e in edges if e.debtor == member and e.outstanding > 0]
        owed = [_row(e, e.debtor) for e in edges if e.creditor == member and e.outstanding > 0]
        net = sum(r["amount"] for r in owed) - sum(r["amount"] for r in owe)
        return {
            "ok": True, "type": "statement",
            "member": {"id": member, "name": names.get(member, "?")},
            "period": {"from": period["from"].isoformat() if period["from"] else None,
                       "to": period["to"].isoformat()},
            "owe": owe, "owed": owed, "net": net,
        }
```

Register it in the returned dict (alongside `get_period_balances`):

```python
        "member_statement": CustomTool(
            execute=member_statement,
            description="A person's own statement: what they owe + are owed, per meal, with paid/unpaid status. Default member = the sender. Use for first-person balance questions ('tôi nợ ai', 'how much do I owe').",
            input_schema={"type": "object", "properties": {
                "member": {"type": "integer", "description": "member id; blank = the sender."},
                "keyword": _PERIOD_SCHEMA["properties"]["keyword"],
            }},
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_member_statement.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/tools.py backend/tests/test_member_statement.py
git commit -m "feat(be): member_statement tool (sender-scoped owe/owed by meal)"
```

---

### Task 5: `get_period_summary` tool (④)

**Files:**
- Modify: `backend/app/tools.py`
- Test: `backend/tests/test_period_summary.py` (new)

**Interfaces:**
- Consumes: `ledger.period_timeline`, `ledger.period_balances`, `_names_for`, `resolve_period`.
- Produces: result `{ok, type:"summary", period:{from,to}, timeline:[…events + resolved names…], balances:[{id,name,balance}]}`. Tool name `get_period_summary`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_period_summary.py`:

```python
from datetime import date
import pytest
from app.db import Database
from app import ledger
from app.tools import ToolContext, build_tools


@pytest.fixture
def setup(tmp_path):
    d = Database(f"sqlite:///{tmp_path}/t.db"); d.create_all()
    from app.models import Room, Member
    with d.session() as s:
        r = Room(name="t", invite_token="tok"); s.add(r); s.flush()
        m = {}
        for name in ("Linh", "Giang"):
            x = Member(room_id=r.id, display_name=name, nickname=name); s.add(x); s.flush()
            m[name] = x.id
        ledger.record_meal(s, room_id=r.id, payer_member_id=m["Linh"],
                           participants=[m["Linh"], m["Giang"]], total_amount=122000,
                           dish="bun bo", occurred_on=date(2026, 7, 21))
        ledger.record_payment(s, room_id=r.id, from_member_id=m["Giang"],
                              to_member_id=m["Linh"], amount=61000, occurred_on=date(2026, 7, 22))
        room = r.id
    return d, room, m


def test_summary_timeline_and_balances(setup):
    d, room, m = setup
    res = build_tools(ToolContext(db=d, room_id=room, sender_member_id=m["Giang"]))["get_period_summary"].execute({})
    assert res["type"] == "summary"
    kinds = [e["kind"] for e in res["timeline"]]
    assert kinds == ["meal", "payment"]
    assert res["timeline"][0]["payer_name"] == "Linh"
    assert res["timeline"][1]["from_name"] == "Giang" and res["timeline"][1]["to_name"] == "Linh"
    bal = {b["name"]: b["balance"] for b in res["balances"]}
    assert bal["Giang"] == 0 and bal["Linh"] == 0     # 61k meal debt, 61k paid -> even
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_period_summary.py -v`
Expected: FAIL — `KeyError: 'get_period_summary'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/tools.py`, define inside `build_tools`:

```python
    def get_period_summary(args, _tool_ctx=None) -> dict:
        args = args or {}
        with db.session() as s:
            last = ledger.last_settlement(s, ctx.room_id)
            period = resolve_period(
                args.get("keyword"), today=today_ict(),
                last_settlement_to=last.period_to if last else None,
            )
            timeline = ledger.period_timeline(s, ctx.room_id, period["from"], period["to"])
            balances = ledger.period_balances(s, ctx.room_id, period["from"], period["to"])
            ids = set(balances) | {e.get("payer_id") for e in timeline} \
                | {e.get("from_id") for e in timeline} | {e.get("to_id") for e in timeline}
            ids.discard(None)
            names = _names_for(s, ctx.room_id, ids)
        for e in timeline:
            if e["kind"] == "meal":
                e["payer_name"] = names.get(e["payer_id"], "?")
            else:
                e["from_name"] = names.get(e["from_id"], "?")
                e["to_name"] = names.get(e["to_id"], "?")
        return {
            "ok": True, "type": "summary",
            "period": {"from": period["from"].isoformat() if period["from"] else None,
                       "to": period["to"].isoformat()},
            "timeline": timeline,
            "balances": [{"id": mid, "name": names.get(mid, "?"), "balance": v["balance"]}
                         for mid, v in sorted(balances.items(), key=lambda kv: kv[1]["balance"])],
        }
```

Register it:

```python
        "get_period_summary": CustomTool(
            execute=get_period_summary,
            description="Group summary: chronological timeline of meals + payments and per-person net balances (display only). Use for 'summary'/'current state'/'tổng kết'.",
            input_schema={"type": "object", "properties": {"keyword": _PERIOD_SCHEMA["properties"]["keyword"]}},
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_period_summary.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/tools.py backend/tests/test_period_summary.py
git commit -m "feat(be): get_period_summary tool (chronological timeline + balances)"
```

---

### Task 6: `resolve_date` tool + `occurred_on` on `propose_meal` (⑤)

**Files:**
- Modify: `backend/app/periods.py` (`resolve_date`), `backend/app/tools.py` (tool + `propose_meal`), `backend/app/chat.py` (pass `occurred_on` through to the draft payload)
- Test: `backend/tests/test_resolve_date.py` (new)

**Interfaces:**
- Consumes: `app.clock.today_ict`.
- Produces:
  - `periods.resolve_date(word:str, *, today:date) -> date` — `hôm nay`/`today`→today; `hôm qua`/`yesterday`→‑1d; weekday words (`thứ 2..7`/`t2..t7`/`monday..sunday`, case-insensitive) → the most recent past (or today) matching weekday; `dd/mm` or `dd/mm/yyyy` → that date (year defaults to `today.year`). Raises `ValueError` on unparseable input.
  - Tool `resolve_date` → `{ok, date: "YYYY-MM-DD"}`.
  - `propose_meal` accepts `occurred_on` (ISO string) and echoes it in its result; the draft payload carries it into `record_meal`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_resolve_date.py`:

```python
from datetime import date
import pytest
from app.periods import resolve_date

WED = date(2026, 7, 22)  # a Wednesday


@pytest.mark.parametrize("word,expected", [
    ("hôm nay", WED), ("today", WED),
    ("hôm qua", date(2026, 7, 21)), ("yesterday", date(2026, 7, 21)),
    ("thứ 2", date(2026, 7, 20)), ("t2", date(2026, 7, 20)), ("monday", date(2026, 7, 20)),
    ("thứ 4", WED),                       # today's weekday resolves to today
    ("thứ 5", date(2026, 7, 16)),         # Thursday is in the past -> last week
    ("20/7", date(2026, 7, 20)), ("20/07/2026", date(2026, 7, 20)),
])
def test_resolve_date(word, expected):
    assert resolve_date(word, today=WED) == expected


def test_resolve_date_bad():
    with pytest.raises(ValueError):
        resolve_date("blah", today=WED)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_resolve_date.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_date'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/periods.py`, append:

```python
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
    "thu 2": 0, "thu 3": 1, "thu 4": 2, "thu 5": 3, "thu 6": 4, "thu 7": 5, "chu nhat": 6,
    "t2": 0, "t3": 1, "t4": 2, "t5": 3, "t6": 4, "t7": 5, "cn": 6,
}


def _strip_accents(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def resolve_date(word: str, *, today: date) -> date:
    """A single day from a weekday/relative word or dd/mm[/yyyy] (ICT).

    Weekday words resolve to the most recent matching day at or before ``today``
    (a group logs *past* lunches). Raises ``ValueError`` if unparseable.
    """
    raw = (word or "").strip().lower()
    w = _strip_accents(raw).replace("thu ", "thu ").strip()
    if w in ("hom nay", "today", "nay"):
        return today
    if w in ("hom qua", "yesterday", "qua"):
        return today - timedelta(days=1)
    if w in _WEEKDAYS:
        target = _WEEKDAYS[w]
        delta = (today.weekday() - target) % 7
        return today - timedelta(days=delta)
    import re
    md = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", w)
    if md:
        day, month, year = int(md[1]), int(md[2]), md[3]
        y = today.year if year is None else (2000 + int(year) if len(year) == 2 else int(year))
        return date(y, month, day)
    raise ValueError(f"Không hiểu ngày: {word!r}")
```

In `backend/app/tools.py`, add `occurred_on` to `_PROPOSE_SCHEMA["properties"]`:

```python
        "occurred_on": {"type": "string", "description": "Meal date, ISO YYYY-MM-DD (from resolve_date when the user names a day). Omit = today."},
```

In `propose_meal`, add `occurred_on` to the returned dict (after `per_head_preview`):

```python
            "occurred_on": args.get("occurred_on"),
```

Add a `resolve_date` tool inside `build_tools` and register it:

```python
    def resolve_date_tool(args, _tool_ctx=None) -> dict:
        args = args or {}
        try:
            d = resolve_date(str(args.get("word") or ""), today=today_ict())
        except ValueError as exc:
            return _err(str(exc))
        return {"ok": True, "date": d.isoformat()}
```

```python
        "resolve_date": CustomTool(
            execute=resolve_date_tool,
            description="Turn a day word ('thứ 2', 'hôm qua', '20/7') into an ISO date (ICT). Use before propose_meal when the user names a day.",
            input_schema={"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]},
        ),
```

Update the import in `tools.py`: `from app.periods import resolve_date, resolve_period`.

In `backend/app/chat.py`, `run_bot_turn`, extend the meal-draft payload keys to include the date:

```python
            payload = {k: proposal[k] for k in (
                "payer_member_id", "member_participants", "guests", "bill_total",
                "adjustments", "dish", "initiator", "note", "per_head_preview", "occurred_on")}
```

- [ ] **Step 4: Verify `record_meal`/draft commit already forward `occurred_on`**

`drafts.create_draft` persists the payload verbatim; confirm `drafts.commit_any` passes `occurred_on` to `ledger.record_meal` (which already accepts it). Run: `cd backend && source .venv/bin/activate && pytest tests/test_resolve_date.py -v` and the existing draft tests: `pytest tests/ -k draft -v`.
Expected: resolve_date PASS; draft tests still PASS. If a draft test shows `occurred_on` dropped on commit, add `occurred_on=att.get("occurred_on")` where `drafts.commit_any` calls `record_meal`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/periods.py backend/app/tools.py backend/app/chat.py backend/tests/test_resolve_date.py
git commit -m "feat(be): resolve_date + explicit meal occurred_on (⑤)"
```

---

### Task 7: Chat rendering — statement/summary attachments + deterministic bodies

**Files:**
- Modify: `backend/app/chat.py` (`render_bot_attachments`, new `_statement_body`, `_summary_body`, body dispatch in `run_bot_turn`)
- Test: `backend/tests/test_chat_bodies.py` (new)

**Interfaces:**
- Consumes: `TurnResult.last_result` for `member_statement` / `get_period_summary`.
- Produces: `render_bot_attachments` returns `{type:"statement", …}` / `{type:"summary", …}`; bodies assembled server-side (numbers never from `final_text`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_chat_bodies.py`:

```python
from app.chat import render_bot_attachments, _statement_body, _summary_body


class _Fake:
    def __init__(self, name, res): self._n, self._r = name, res
    def last_result(self, name): return self._r if name == self._n else None


def test_render_statement_attachment():
    res = _Fake("member_statement", {"ok": True, "type": "statement", "member": {"id": 9, "name": "Giang"},
                "period": {"from": None, "to": "2026-07-22"},
                "owe": [{"creditor_id": 6, "name": "Linh", "meal_id": 2, "dish": "bun bo",
                         "occurred_on": "2026-07-21", "amount": 61000, "status": "unpaid"}],
                "owed": [], "net": -61000})
    att = render_bot_attachments(res)
    assert att["type"] == "statement"
    body = _statement_body(att)
    assert "Linh" in body and "61" in body


def test_render_summary_attachment():
    res = _Fake("get_period_summary", {"ok": True, "type": "summary",
                "period": {"from": None, "to": "2026-07-22"},
                "timeline": [{"kind": "meal", "dish": "bun bo", "payer_name": "Linh", "total": 122000,
                              "occurred_on": "2026-07-21"}],
                "balances": [{"id": 6, "name": "Linh", "balance": 61000}]})
    att = render_bot_attachments(res)
    assert att["type"] == "summary"
    assert "bun bo" in _summary_body(att)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_chat_bodies.py -v`
Expected: FAIL — `render_bot_attachments` returns `None` for statement (only settle handled); `_statement_body` undefined.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/chat.py`, extend `render_bot_attachments`:

```python
def render_bot_attachments(result) -> dict | None:
    settle = result.last_result("settle_period")
    if settle:
        if settle.get("type") == "settle_blocked":
            return dict(settle)
        return {"type": "settlement", **settle}
    statement = result.last_result("member_statement")
    if statement:
        return {"type": "statement", **statement}
    summary = result.last_result("get_period_summary")
    if summary:
        return {"type": "summary", **summary}
    return None
```

Add body builders (near `_settlement_body`):

```python
def _statement_body(att: dict) -> str:
    """Deterministic VN text for a personal statement — numbers from the tool dict."""
    name = (att.get("member") or {}).get("name", "?")
    lines = [f"Số dư của {name}:"]
    owe = att.get("owe") or []
    owed = att.get("owed") or []
    if owe:
        lines.append("Bạn nợ:")
        lines += [f"• {r['name']} {r['amount']:,}đ ({r.get('dish') or 'bữa ăn'}"
                  f"{' – đã trả' if r['status'] == 'paid' else ''})" for r in owe]
    if owed:
        lines.append("Được nợ:")
        lines += [f"• {r['name']} {r['amount']:,}đ ({r.get('dish') or 'bữa ăn'})" for r in owed]
    if not owe and not owed:
        lines.append("Bạn đã cân bằng — không nợ ai, không ai nợ bạn.")
    else:
        lines.append(f"Ròng: {att.get('net', 0):,}đ")
    return "\n".join(lines)


def _summary_body(att: dict) -> str:
    """Deterministic VN text for the group summary — numbers from the tool dict."""
    period = att.get("period") or {}
    lines = [f"Tóm tắt đến {period.get('to')}:"]
    for e in att.get("timeline") or []:
        if e["kind"] == "meal":
            lines.append(f"• {e.get('occurred_on')} 🍜 {e.get('dish') or 'bữa ăn'} — "
                         f"{e.get('payer_name', '?')} trả {e.get('total', 0):,}đ")
        else:
            lines.append(f"• {e.get('occurred_on')} 💸 {e.get('from_name', '?')} → "
                         f"{e.get('to_name', '?')} {e.get('amount', 0):,}đ")
    if len(lines) == 1:
        lines.append("Chưa có giao dịch nào trong kỳ.")
    return "\n".join(lines)
```

Extend the body dispatch in `run_bot_turn` (the `if attachments and attachments.get("type") == "settlement":` chain):

```python
            if attachments and attachments.get("type") == "settlement":
                body = _settlement_body(attachments)
            elif attachments and attachments.get("type") == "settle_blocked":
                body = _settle_blocked_body(attachments)
            elif attachments and attachments.get("type") == "statement":
                body = _statement_body(attachments)
            elif attachments and attachments.get("type") == "summary":
                body = _summary_body(attachments)
            else:
                body = result.final_text or (result.error and f"⚠️ {result.error}") or "(không có phản hồi)"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_chat_bodies.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/chat.py backend/tests/test_chat_bodies.py
git commit -m "feat(be): render statement/summary cards with server-assembled bodies"
```

---

### Task 8: Skills + prompt (routing, clarify, dates, no narration)

**Files:**
- Rename: `backend/app/agent_skills/skills/settle-period/SKILL.md` → `backend/app/agent_skills/skills/balances/SKILL.md`
- Modify: `record-payment/SKILL.md`, `record-meal/SKILL.md`, `backend/app/prompt.py`
- Test: `backend/tests/test_prompt.py` (new, light)

**Interfaces:**
- Consumes: nothing (text/prompt only). `build_system_prompt` gains today's date.
- Produces: `build_system_prompt(*, sender_name=None, today=None)` includes `hôm nay là <date>`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_prompt.py`:

```python
from datetime import date
from app.prompt import build_system_prompt


def test_prompt_includes_today():
    p = build_system_prompt(sender_name="Giang", today=date(2026, 7, 22))
    assert "2026-07-22" in p
    assert "Giang" in p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_prompt.py -v`
Expected: FAIL — `build_system_prompt() got an unexpected keyword argument 'today'`.

- [ ] **Step 3: Write minimal implementation**

Edit `backend/app/prompt.py` — signature and body:

```python
def build_system_prompt(*, sender_name: str | None = None, today=None) -> str:
    from app.clock import today_ict
    who = f' The person messaging you now is "{sender_name}".' if sender_name else ""
    today = today or today_ict()
    day = today.isoformat()
    return (
        "Bạn là **chiatienan**, một trợ lý chia tiền ăn trưa trong một nhóm chat.\n"
        "Nhóm gồm ~6–7 đồng nghiệp; mỗi ngày ai cũng có thể là người trả tiền.\n"
        f"Trả lời ngắn gọn, thân thiện, bằng tiếng Việt.{who}\n"
        f"Hôm nay là {day} (giờ Việt Nam).\n"
        "Trả lời thẳng vào việc — KHÔNG thuật lại việc bạn đang chọn skill/công cụ nào.\n"
        "\n"
        "# Quy tắc TIỀN BẠC (bắt buộc)\n"
        "- KHÔNG BAO GIỜ tự tính toán hay tự gõ lại một con số tiền do công cụ trả về.\n"
        "- Số tiền người dùng nói (vd '840k' → 840000) được truyền vào công cụ MỘT LẦN duy nhất.\n"
        "- Mọi thay đổi số dư (bữa ăn, trả tiền, chốt) là ĐỀ XUẤT — người dùng xác nhận trên thẻ.\n"
        "\n"
        "# Công cụ & quy trình\n"
        "- Quy trình chi tiết cho ghi bữa ăn, ghi trả tiền, xem số dư, và chốt kỳ nằm trong các *skill*"
        " của workspace (record-meal, record-payment, balances) — làm theo skill phù hợp.\n"
        "- Câu hỏi ngôi thứ nhất ('tôi nợ ai', 'how much do I owe') → xem số dư CỦA NGƯỜI HỎI"
        " (member_statement, mặc định là người nhắn). Chỉ xem cả nhóm khi họ nói rõ.\n"
        "- Ngày cụ thể ('thứ 2', 'hôm qua', '20/7') → gọi `resolve_date` rồi truyền `occurred_on`.\n"
        "- Quản lý thành viên: `add_member`, `update_member`, `delete_member`.\n"
    )
```

- [ ] **Step 4: Rename & rewrite the skills**

Rename the directory:

```bash
git mv backend/app/agent_skills/skills/settle-period backend/app/agent_skills/skills/balances
```

Overwrite `backend/app/agent_skills/skills/balances/SKILL.md`:

```markdown
---
name: balances
description: Xem số dư và chốt tiền — "tôi nợ ai", "how much do I owe", "summary", "current state", "ai trả tuần này", "chốt", "reset".
---
# Xem số dư / tóm tắt / chốt

Chọn đúng công cụ theo câu hỏi:
- Ngôi thứ nhất, hỏi về mình ('tôi nợ bao nhiêu', 'nợ ai', 'nợ buổi nào', 'how much do I owe', 'my part') → `member_statement` (mặc định = người nhắn). KHÔNG hiện cả nhóm.
- Tóm tắt / trạng thái nhóm ('summary', 'current state', 'tổng kết', 'cả nhóm thế nào') → `get_period_summary`.
- Chốt / tạo QR ('ai trả tuần này', 'tạo QR', 'chốt', 'reset') → `settle_period`. `commit:false` để xem trước; `commit:true` CHỈ khi người dùng nói rõ 'chốt'/'reset'.
- Không có mốc thời gian rõ → mặc định 'since_last'.
- Nếu còn đề xuất chưa xác nhận, `settle_period` báo `settle_blocked` — nhắc xác nhận/huỷ trước.
```

Overwrite `backend/app/agent_skills/skills/record-payment/SKILL.md`:

```markdown
---
name: record-payment
description: Ghi khi một người trả tiền mặt cho người khác — "A trả B", "A đã trả", "trả hết rồi".
---
# Ghi trả tiền mặt

Dùng `propose_payment` (KHÔNG dùng `propose_meal`). Nó chỉ ĐỀ XUẤT — người dùng xác nhận trên thẻ.

- `from` = người trả (bỏ trống = người đang nhắn), `to` = người nhận.
- Có số tiền cụ thể ('A trả B 100k') → truyền `amount` (VND).
- KHÔNG có số tiền ('A đã trả B', 'trả hết rồi') → BỎ TRỐNG `amount`; công cụ tính đúng số A đang nợ B (gộp theo từng bữa). ĐỪNG tự đoán số.
- Nếu công cụ trả về `payment_ambiguous` (hai người nợ nhau CẢ HAI CHIỀU): HỎI lại người dùng — trả trọn số `gross` hay chỉ cấn trừ phần chênh `offset` — rồi gọi lại `propose_payment` với `mode:"gross"` hoặc `mode:"offset"` (đừng tự gõ số).
- `payment_settled` = thật sự không còn nợ → báo lại, không tạo thẻ.
- `nothing_owed` = người trả không nợ người kia (mà ngược lại) → giải thích, không tạo thẻ.
- Nhiều người trả trong một câu → gọi `propose_payment` MỘT LẦN CHO MỖI người.
```

Append to `backend/app/agent_skills/skills/record-meal/SKILL.md` (after the numbered list):

```markdown
- Ngày: nếu người dùng nói rõ một ngày ('thứ 2', 'hôm qua', '20/7'), gọi `resolve_date` rồi truyền kết quả vào `occurred_on`. Không nói ngày → bỏ trống (mặc định hôm nay).
```

- [ ] **Step 5: Verify prompt test + agent still loads skills**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_prompt.py -v && pytest tests/ -k "skill or materialize" -v`
Expected: PASS. If a test hard-codes the `settle-period` directory name, update it to `balances`.

Wire the date through: in `backend/app/agent.py`, `_render_prompt` calls `build_system_prompt(sender_name=sender_name)` — leave as is (defaults to `today_ict()`), or pass `today=today_ict()` explicitly for testability. No change required.

- [ ] **Step 6: Commit**

```bash
git add backend/app/prompt.py backend/app/agent_skills backend/tests/test_prompt.py
git commit -m "feat(be): balances skill + sender-default routing, clarify, dates, no-narration prompt"
```

---

### Task 9: `GET /api/rooms/{id}/ledger` endpoint (⑥ data)

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_ledger_endpoint.py` (new)

**Interfaces:**
- Consumes: `ledger.last_settlement`, `resolve_period`, `ledger.period_balances`, `ledger.period_timeline`, `roster.list_members`, `require_session`.
- Produces: `GET /api/rooms/{room_id}/ledger?period=since_last` → `{period:{from,to,keyword}, balances:[{id,name,balance}], timeline:[…named events…]}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_ledger_endpoint.py` (follow the auth/setup pattern of the existing API tests — e.g. `tests/test_messages_api.py`; if the project has a shared client fixture, reuse it):

```python
from datetime import date
from fastapi.testclient import TestClient


def test_ledger_endpoint_since_last(api_client_room):
    # api_client_room: fixture giving (client, headers, room_id, members-by-name)
    client, headers, room_id, m = api_client_room
    from app.db import get_db
    from app import ledger
    with get_db().session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=m["Linh"],
                           participants=[m["Linh"], m["Giang"]], total_amount=122000,
                           dish="bun bo", occurred_on=date(2026, 7, 21))
    r = client.get(f"/api/rooms/{room_id}/ledger", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["period"]["keyword"] == "since_last"
    assert any(e["kind"] == "meal" and e["payer_name"] == "Linh" for e in data["timeline"])
    assert {b["name"] for b in data["balances"]} == {"Linh", "Giang"}
```

> If no `api_client_room` fixture exists, add one to `backend/tests/conftest.py` mirroring the setup in the existing message-API test (create room via `/api/rooms/create`, capture the session token header and member ids). Reuse the exact header key the other API tests use.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_ledger_endpoint.py -v`
Expected: FAIL — 404 (route not defined).

- [ ] **Step 3: Write minimal implementation**

In `backend/app/main.py`, add imports at top: `from app.clock import today_ict` and `from app.periods import resolve_period` (add to existing `from app import ...` line: ensure `ledger`, `roster` present — they are). Add the route (near `get_messages`):

```python
@app.get("/api/rooms/{room_id}/ledger")
async def get_ledger(room_id: int, period: str = "since_last",
                     ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    with get_db().session() as s:
        last = ledger.last_settlement(s, room_id)
        p = resolve_period(period, today=today_ict(),
                           last_settlement_to=last.period_to if last else None)
        balances = ledger.period_balances(s, room_id, p["from"], p["to"])
        timeline = ledger.period_timeline(s, room_id, p["from"], p["to"])
        names = {mm.id: mm.display_name
                 for mm in roster.list_members(s, room_id, include_inactive=True)}
    for e in timeline:
        if e["kind"] == "meal":
            e["payer_name"] = names.get(e["payer_id"], "?")
        else:
            e["from_name"] = names.get(e["from_id"], "?")
            e["to_name"] = names.get(e["to_id"], "?")
    return {
        "period": {"from": p["from"].isoformat() if p["from"] else None,
                   "to": p["to"].isoformat(), "keyword": p["keyword"]},
        "balances": [{"id": mid, "name": names.get(mid, "?"), "balance": v["balance"]}
                     for mid, v in sorted(balances.items(), key=lambda kv: kv[1]["balance"])],
        "timeline": timeline,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_ledger_endpoint.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_ledger_endpoint.py backend/tests/conftest.py
git commit -m "feat(be): GET /rooms/{id}/ledger (since_last balances + timeline)"
```

---

### Task 10: Publish `ledger:changed` on every write (⑥ live refresh)

**Files:**
- Modify: `backend/app/main.py` (commit routes), `backend/app/chat.py` (committed settle)
- Test: `backend/tests/test_ledger_changed_event.py` (new)

**Interfaces:**
- Consumes: `hub.publish`.
- Produces: a `{"type":"ledger:changed"}` SSE event after each committed meal/payment/settlement.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_ledger_changed_event.py`:

```python
import asyncio
import pytest
from app.realtime import hub


@pytest.mark.asyncio
async def test_commit_draft_publishes_ledger_changed(api_client_room, monkeypatch):
    client, headers, room_id, m = api_client_room
    seen = []
    orig = hub.publish
    async def spy(rid, ev):
        if rid == room_id and ev.get("type") == "ledger:changed":
            seen.append(ev)
        await orig(rid, ev)
    monkeypatch.setattr(hub, "publish", spy)
    # create a meal draft via the bot-less path: post an expense draft then commit
    # (use the same helper the existing draft-commit test uses to create a draft)
    draft_id = _make_meal_draft(client, headers, room_id, m)   # from shared test helper
    r = client.post(f"/api/rooms/{room_id}/drafts/{draft_id}/commit", headers=headers)
    assert r.status_code == 200
    assert seen, "expected a ledger:changed event after commit"
```

> Reuse the draft-creation helper from the existing `tests/test_drafts*.py`; if none is exported, create `_make_meal_draft` in `conftest.py` that inserts a pending `expense_draft` via `drafts.create_draft`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_ledger_changed_event.py -v`
Expected: FAIL — no `ledger:changed` published.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/main.py`, in **both** `commit_draft_route` and `recommit_draft_route`, after the two `await hub.publish(... meal_payload)` lines and before `return`, add:

```python
    await hub.publish(room_id, {"type": "ledger:changed"})
```

In `backend/app/chat.py`, `run_bot_turn`: after the turn completes, if a settlement committed, emit the event. Locate where `attachments` is computed for the settlement path and add, inside the `async with _agent_lock:` block after `new_msg = post_message(...)`:

```python
        settle = result.last_result("settle_period")
        if emit and settle and settle.get("committed"):
            await emit({"type": "ledger:changed"})
```

(`emit` publishes to the hub — see `main.py` `_run`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_ledger_changed_event.py -v`
Expected: PASS.

- [ ] **Step 5: Full backend suite + commit**

Run: `cd backend && source .venv/bin/activate && pytest -q`
Expected: all green (existing 103 + new tests).

```bash
git add backend/app/main.py backend/app/chat.py backend/tests/test_ledger_changed_event.py
git commit -m "feat(be): publish ledger:changed on meal/payment/settlement commits"
```

---

## Self-Review

**Spec coverage:**
- ① personal statement → Task 4 (`member_statement`) + Task 7 (render) + Task 8 (routing). ✓
- ② gross pay-off + clarify → Task 3. ✓
- ③ sender-default routing → Task 8 (prompt + balances skill). ✓
- ④ chronological summary → Task 5 + Task 7 + Task 2 (`period_timeline`). ✓
- ⑤ meal dates → Task 6. ✓
- ⑥ panel data (endpoint + SSE) → Task 9 + Task 10. (Panel UI is the frontend plan.) ✓
- Shared primitive → Task 1 + Task 2. ✓
- Money-safety (server bodies, mode token) → Task 3 (mode), Task 7 (bodies). ✓
- Same period reset as chat → Task 9 (`resolve_period("since_last", …)`) + Task 10 (settle fires event). ✓

**Placeholder scan:** none — every code step has complete code; the two fixture-reuse notes (Tasks 9–10) point to concrete existing patterns with a fallback.

**Type consistency:** `DebtEdge` fields/`.outstanding`/`.status` consistent across Tasks 1–5. Tool result `type` strings (`payment_ambiguous`, `nothing_owed`, `statement`, `summary`) match between Tasks 3–5, Task 7 rendering, and Task 8 skills. `member_statement` row keys (`amount`, `status`, `dish`, `name`) match the body builder in Task 7. Timeline event keys (`kind`, `payer_name`, `from_name`, `to_name`, `amount`, `total`, `occurred_on`) match across Tasks 2, 5, 7, 9.

**Frontend interface handed off (plan 2):** attachment shapes `{type:"statement"|"summary", …}` (Task 7) and `GET /ledger` response (Task 9) — the frontend plan consumes exactly these.
