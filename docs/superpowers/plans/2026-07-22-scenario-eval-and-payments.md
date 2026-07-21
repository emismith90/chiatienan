# Scenario Eval + Payments + Revised Draft Lifecycle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable week-long behavioral eval of the lunch bot, and the capabilities it requires: ad-hoc member payments, a persist-and-confirm draft lifecycle (no auto-commit, settle-gate, edit-approved), and the frontend to match.

**Architecture:** Backend money logic stays deterministic and tool-owned (design D3). New `Payment` rows fold into `period_balances`. Drafts no longer auto-commit on supersede; `settle_period` refuses (and reports) while pending proposals exist; committed meals are edited via void+re-record with a settled-period guard. A declarative scenario spec drives a deterministic CI runner and an opt-in LLM runner.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy / SQLite (WAL), pytest; Next.js 16 / React 19 / TypeScript, Vitest + Testing Library.

## Global Constraints

- All money is **integer VND**. Tools own every number; the LLM never computes or re-types a tool-produced amount (design D3).
- The ledger is **append-only**: meals are corrected by void + re-record, never mutated in place.
- Balances are **derived, never stored**.
- Backend tests run from `backend/` with `pytest`; frontend tests from `frontend/` with `npm test` (vitest). No new frontend runtime deps (use `fireEvent`, not `user-event`).
- Dates are **ICT (Asia/Ho_Chi_Minh)**; freeze time in tests by monkeypatching `app.clock.now_ict` (NOT `today_ict`, which is import-bound in `ledger`/`drafts`/`tools`).
- Vietnamese is the bot's response language; server-rendered money bodies come from tool-result dicts.

---

### Task 1: `Payment` model

**Files:**
- Modify: `backend/app/models.py`
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Produces: `app.models.Payment` with columns `id, room_id, from_member_id, to_member_id, amount, occurred_on, note, source, logged_by, voided, voided_by, voided_at, created_at`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_models.py`:

```python
def test_payment_model_persists(db):
    from datetime import date
    from app.models import Payment, Room, Member
    with db.session() as s:
        room = Room(name="R", invite_token="tp")
        s.add(room); s.flush()
        a = Member(room_id=room.id, display_name="A", nickname="a", pin="1")
        b = Member(room_id=room.id, display_name="B", nickname="b", pin="2")
        s.add_all([a, b]); s.flush()
        p = Payment(room_id=room.id, from_member_id=a.id, to_member_id=b.id,
                    amount=125_000, occurred_on=date(2026, 7, 21))
        s.add(p); s.flush()
        assert p.id > 0
        assert p.voided is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_models.py::test_payment_model_persists -v`
Expected: FAIL — `ImportError: cannot import name 'Payment'`.

- [ ] **Step 3: Write minimal implementation**

Add to `backend/app/models.py` after the `Settlement` class:

```python
class Payment(Base):
    """An ad-hoc cash payment between two members (outside meals/settlements).

    Adjusts balances directly (payer's balance += amount, payee's -= amount);
    carries no shares. Append-only; corrections are a void + new payment.
    """
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False, index=True)
    from_member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False, index=True)
    to_member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # VND
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note: Mapped[str | None] = mapped_column(String(400))
    source: Mapped[str] = mapped_column(String(20), default="web", nullable=False)
    logged_by: Mapped[str | None] = mapped_column(String(120))
    voided: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    voided_by: Mapped[str | None] = mapped_column(String(120))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_models.py::test_payment_model_persists -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/tests/test_models.py
git commit -m "feat(be): Payment model for ad-hoc member payments"
```

---

### Task 2: `ledger.record_payment` + fold payments into `period_balances`

**Files:**
- Modify: `backend/app/ledger.py`
- Test: `backend/tests/test_ledger.py`

**Interfaces:**
- Consumes: `app.models.Payment` (Task 1); `_seed_room(db, n)` from `tests/test_ledger.py`.
- Produces:
  - `ledger.record_payment(session, *, room_id, from_member_id, to_member_id, amount, occurred_on=None, note=None, source="web", logged_by=None) -> dict` returning `{payment_id, from_member_id, to_member_id, amount, occurred_on}`.
  - `ledger.period_balances(...)` now folds payments: `balance[from] += amount`, `balance[to] -= amount`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_ledger.py`:

```python
def test_record_payment_shifts_balances(db):
    from datetime import date
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=m[0],
                           participants=[m[0], m[1]], total_amount=200,
                           occurred_on=date(2026, 7, 20))
        # m0 +100, m1 -100
        ledger.record_payment(s, room_id=room_id, from_member_id=m[1],
                              to_member_id=m[0], amount=40, occurred_on=date(2026, 7, 20))
        bal = ledger.period_balances(s, room_id, None, date(2999, 1, 1))
        assert bal[m[0]]["balance"] == 60   # 100 - 40 received
        assert bal[m[1]]["balance"] == -60  # -100 + 40 paid
        assert bal[m[0]]["balance"] + bal[m[1]]["balance"] == 0


def test_record_payment_payment_only_member_appears(db):
    from datetime import date
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        ledger.record_payment(s, room_id=room_id, from_member_id=m[0],
                              to_member_id=m[1], amount=50, occurred_on=date(2026, 7, 20))
        bal = ledger.period_balances(s, room_id, None, date(2999, 1, 1))
        assert bal[m[0]]["balance"] == 50
        assert bal[m[1]]["balance"] == -50


def test_record_payment_validation(db):
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        with pytest.raises(ledger.LedgerError):
            ledger.record_payment(s, room_id=room_id, from_member_id=m[0],
                                  to_member_id=m[0], amount=10)  # from == to
        with pytest.raises(ledger.LedgerError):
            ledger.record_payment(s, room_id=room_id, from_member_id=m[0],
                                  to_member_id=m[1], amount=0)   # amount <= 0
        with pytest.raises(ledger.LedgerError):
            ledger.record_payment(s, room_id=room_id, from_member_id=m[0],
                                  to_member_id=9999, amount=10)  # unknown member


def test_voided_payment_excluded_from_balances(db):
    from datetime import date
    from app.models import Payment
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        res = ledger.record_payment(s, room_id=room_id, from_member_id=m[0],
                                    to_member_id=m[1], amount=50, occurred_on=date(2026, 7, 20))
        s.get(Payment, res["payment_id"]).voided = True
        s.flush()
        bal = ledger.period_balances(s, room_id, None, date(2999, 1, 1))
        assert bal.get(m[0], {"balance": 0})["balance"] == 0
        assert bal.get(m[1], {"balance": 0})["balance"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_ledger.py -k payment -v`
Expected: FAIL — `AttributeError: module 'app.ledger' has no attribute 'record_payment'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/ledger.py`, update the model import and add the function + fold. Change the import line:

```python
from app.models import Meal, MealShare, Member, Payment, Settlement
```

Add after `record_meal`:

```python
def record_payment(
    session: Session,
    *,
    room_id: int,
    from_member_id: int,
    to_member_id: int,
    amount: int,
    occurred_on: date | None = None,
    note: str | None = None,
    source: str = "web",
    logged_by: str | None = None,
) -> dict:
    """Record a cash payment from one member to another (adjusts balances)."""
    if amount <= 0:
        raise LedgerError("Payment amount must be greater than 0.")
    if from_member_id == to_member_id:
        raise LedgerError("A payment must be between two different members.")
    found = {
        m.id
        for m in session.scalars(
            select(Member).where(
                Member.id.in_([from_member_id, to_member_id]), Member.room_id == room_id
            )
        )
    }
    for mid in (from_member_id, to_member_id):
        if mid not in found:
            raise LedgerError(f"Member (id={mid}) does not exist.")

    pay = Payment(
        room_id=room_id,
        from_member_id=from_member_id,
        to_member_id=to_member_id,
        amount=amount,
        occurred_on=occurred_on or today_ict(),
        note=note,
        source=source,
        logged_by=logged_by,
    )
    session.add(pay)
    session.flush()
    return {
        "payment_id": pay.id,
        "from_member_id": from_member_id,
        "to_member_id": to_member_id,
        "amount": amount,
        "occurred_on": pay.occurred_on.isoformat(),
    }
```

In `period_balances`, AFTER the existing `for row in out.values(): row["balance"] = ...` loop and BEFORE `return out`, add:

```python
    # Fold ad-hoc payments: a payment from X to Y increases X's balance (their
    # debt shrinks) and decreases Y's. Done after the paid-consumed loop so it
    # is not overwritten. Voided payments are excluded.
    pay_conds = [Payment.room_id == room_id, Payment.voided.is_(False), Payment.occurred_on <= to_date]
    if from_date is not None:
        pay_conds.append(Payment.occurred_on >= from_date)
    pay_rows = session.execute(
        select(Payment.from_member_id, Payment.to_member_id, Payment.amount).where(*pay_conds)
    ).all()
    for from_id, to_id, amt in pay_rows:
        out.setdefault(from_id, {"paid": 0, "consumed": 0, "balance": 0})
        out.setdefault(to_id, {"paid": 0, "consumed": 0, "balance": 0})
        out[from_id]["balance"] += amt
        out[to_id]["balance"] -= amt
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_ledger.py -v`
Expected: PASS (all, including pre-existing).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ledger.py backend/tests/test_ledger.py
git commit -m "feat(be): ledger.record_payment + fold payments into period_balances"
```

---

### Task 3: `record_payment` tool

**Files:**
- Modify: `backend/app/tools.py`
- Test: `backend/tests/test_tools.py`

**Interfaces:**
- Consumes: `ledger.record_payment` (Task 2); `drafts.current_balances` (existing).
- Produces: tool `"record_payment"` in `build_tools(ctx)` returning `{ok, type:"payment", from:{id,name}, to:{id,name}, amount, balances}` or `{ok:False, error}`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_tools.py` (follow the file's existing ctx/room setup pattern; the sketch below assumes a `_ctx(db)` helper like the other tests use — if the file builds `ToolContext` inline, mirror that):

```python
def test_record_payment_tool_happy(db):
    from app.tools import build_tools, ToolContext
    room_id, m = _seed_room(db, 2)  # import from tests.test_ledger if not local
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=m[1])
    tools = build_tools(ctx)
    res = tools["record_payment"].execute({"from": m[1], "to": m[0], "amount": 125_000})
    assert res["ok"] is True
    assert res["type"] == "payment"
    assert res["amount"] == 125_000
    assert res["from"]["id"] == m[1] and res["to"]["id"] == m[0]
    assert isinstance(res["balances"], list)


def test_record_payment_tool_defaults_from_to_sender(db):
    from app.tools import build_tools, ToolContext
    room_id, m = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=m[0])
    res = build_tools(ctx)["record_payment"].execute({"to": m[1], "amount": 10_000})
    assert res["ok"] is True and res["from"]["id"] == m[0]


def test_record_payment_tool_errors(db):
    from app.tools import build_tools, ToolContext
    room_id, m = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=m[0])
    tools = build_tools(ctx)
    assert tools["record_payment"].execute({"to": m[1]})["ok"] is False       # no amount
    assert tools["record_payment"].execute({"amount": 10})["ok"] is False     # no recipient
    assert tools["record_payment"].execute({"to": m[0], "amount": 10})["ok"] is False  # from==to
```

Ensure `from tests.test_ledger import _seed_room` is available at the top of the test file if not already.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_tools.py -k record_payment -v`
Expected: FAIL — `KeyError: 'record_payment'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/tools.py`, add the schema near the other schemas:

```python
_PAYMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "from": {"type": "integer", "description": "member id who paid; blank = the sender."},
        "to": {"type": "integer", "description": "member id who received the money."},
        "amount": {"type": "integer", "description": "Amount, integer VND (125k → 125000)."},
    },
    "required": ["to", "amount"],
}
```

Inside `build_tools`, define the function (alongside the others):

```python
    def record_payment(args, _tool_ctx=None) -> dict:
        from app import drafts  # lazy: avoid import cycle at module load
        args = args or {}
        amount = args.get("amount")
        if not isinstance(amount, int):
            return _err("Missing amount (integer VND).")
        to = args.get("to")
        frm = args.get("from") or ctx.sender_member_id
        if not frm:
            return _err("Could not determine who paid.")
        if not to:
            return _err("Missing recipient.")
        with db.session() as s:
            try:
                ledger.record_payment(
                    s, room_id=ctx.room_id, from_member_id=int(frm),
                    to_member_id=int(to), amount=amount, logged_by=str(ctx.sender_member_id),
                )
            except ledger.LedgerError as exc:
                return _err(str(exc))
            names = _names_for(s, ctx.room_id, [int(frm), int(to)])
            balances = drafts.current_balances(s, ctx.room_id)
        return {
            "ok": True,
            "type": "payment",
            "from": {"id": int(frm), "name": names.get(int(frm), "?")},
            "to": {"id": int(to), "name": names.get(int(to), "?")},
            "amount": amount,
            "balances": balances,
        }
```

Register it in the returned dict:

```python
        "record_payment": CustomTool(
            execute=record_payment,
            description="Record a cash payment one member made to another (e.g. 'A đưa B 100k', 'tôi nhận 125k từ C'). Adjusts balances; not a meal.",
            input_schema=_PAYMENT_SCHEMA,
        ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/tools.py backend/tests/test_tools.py
git commit -m "feat(be): record_payment tool"
```

---

### Task 4: Remove supersede-autocommit; add `list_pending_drafts`

**Files:**
- Modify: `backend/app/drafts.py`
- Test: `backend/tests/test_golden_meals.py`, `backend/tests/test_drafts.py`

**Interfaces:**
- Consumes: existing `create_draft`, `commit_draft`, `get_pending_draft`.
- Produces: `drafts.create_draft(...)` no longer supersedes (returns `(new_draft, [])`); `drafts.list_pending_drafts(session, room_id) -> list[RoomMessage]` (pending, oldest first).

- [ ] **Step 1: Write the failing tests**

Replace `test_golden_G9_supersede_autocommit` and `test_golden_G11_edit_then_supersede` in `backend/tests/test_golden_meals.py` with:

```python
def test_new_draft_does_not_commit_previous(db):
    from app.models import Meal, RoomMessage
    room_id, ids = _seed_room(db, 4)
    with db.session() as s:
        d1, _ = drafts.create_draft(s, room_id, _payload(CASES[0], ids))
        d2, _ = drafts.create_draft(s, room_id, _payload(CASES[1], ids))
        assert s.get(RoomMessage, d1.id).attachments["status"] == "pending"
        assert s.get(RoomMessage, d2.id).attachments["status"] == "pending"
        assert s.query(Meal).count() == 0  # nothing auto-committed


def test_list_pending_drafts_returns_all_pending_oldest_first(db):
    room_id, ids = _seed_room(db, 4)
    with db.session() as s:
        d1, _ = drafts.create_draft(s, room_id, _payload(CASES[0], ids))
        d2, _ = drafts.create_draft(s, room_id, _payload(CASES[1], ids))
        pending = drafts.list_pending_drafts(s, room_id)
        assert [p.id for p in pending] == [d1.id, d2.id]
        drafts.commit_draft(s, d1.id, room_id, logged_by="1")
        assert [p.id for p in drafts.list_pending_drafts(s, room_id)] == [d2.id]
```

(`test_golden_G10_cancel_writes_nothing` stays unchanged.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_golden_meals.py -k "does_not_commit or list_pending" -v`
Expected: FAIL — `test_new_draft_does_not_commit_previous` fails (a Meal is created by the old supersede) and `list_pending_drafts` is undefined.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/drafts.py`, replace the body of `create_draft` (drop the supersede block):

```python
def create_draft(session: Session, room_id: int, payload: dict) -> tuple[RoomMessage, list[RoomMessage]]:
    """Persist a new pending draft. Never commits or supersedes an existing
    draft — proposals persist as independent cards until each is confirmed,
    edited, or cancelled from its own card. Returns ``(new_draft, [])``; the
    empty list preserves the caller signature (there are no supersede extras)."""
    att = {"type": "expense_draft", "status": "pending", **payload}
    att.pop("logged_by", None)
    new_draft = chat.post_message(session, room_id, None, body="", attachments=att, kind="expense_draft")
    return new_draft, []
```

Add `list_pending_drafts` after `get_pending_draft`:

```python
def list_pending_drafts(session: Session, room_id: int) -> list[RoomMessage]:
    """All pending expense drafts in the room, oldest first."""
    rows = session.scalars(
        select(RoomMessage)
        .where(RoomMessage.room_id == room_id, RoomMessage.kind == "expense_draft")
        .order_by(RoomMessage.id)
    ).all()
    return [m for m in rows if (m.attachments or {}).get("status") == "pending"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_golden_meals.py tests/test_drafts.py -v`
Expected: PASS. (If any pre-existing `test_drafts.py`/`test_chat.py` test asserts old supersede-commit behavior, update it to the persist-and-confirm model in this task.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/drafts.py backend/tests/test_golden_meals.py backend/tests/test_drafts.py
git commit -m "feat(be): drafts persist without auto-commit; list_pending_drafts"
```

---

### Task 5: `settle_period` gate (block & ask on open proposals)

**Files:**
- Modify: `backend/app/tools.py`
- Test: `backend/tests/test_tools.py`

**Interfaces:**
- Consumes: `drafts.list_pending_drafts` (Task 4).
- Produces: `settle_period` returns `{ok:True, type:"settle_blocked", pending:[{draft_id, payer_name, bill_total, participant_count}], message}` when any pending draft exists (for both `commit:false` and `commit:true`); otherwise unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_tools.py`:

```python
def test_settle_period_blocks_when_pending_draft_exists(db):
    from datetime import date
    from app.tools import build_tools, ToolContext
    from app import drafts, ledger
    room_id, m = _seed_room(db, 3)
    with db.session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=m[0],
                           participants=m, total_amount=300, occurred_on=date(2026, 7, 20))
        drafts.create_draft(s, room_id, {
            "payer_member_id": m[0], "member_participants": m, "guests": [],
            "bill_total": 90, "adjustments": [], "per_head_preview": 30, "raw_input": "x"})
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=m[0])
    res = build_tools(ctx)["settle_period"].execute({"keyword": "since_last"})
    assert res["type"] == "settle_blocked"
    assert len(res["pending"]) == 1
    assert "transfers" not in res


def test_settle_period_runs_when_no_pending(db):
    from datetime import date
    from app.tools import build_tools, ToolContext
    from app import ledger
    room_id, m = _seed_room(db, 3)
    with db.session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=m[0],
                           participants=m, total_amount=300, occurred_on=date(2026, 7, 20))
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=m[0])
    res = build_tools(ctx)["settle_period"].execute({"keyword": "since_last"})
    assert res.get("type") != "settle_blocked"
    assert "transfers" in res
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_tools.py -k settle_period -v`
Expected: FAIL — `test_settle_period_blocks_...` gets a normal settlement (no gate yet).

- [ ] **Step 3: Write minimal implementation**

In `backend/app/tools.py`, at the very top of `settle_period` (inside `with db.session() as s:`, before `last = ledger.last_settlement(...)`), add:

```python
        from app import drafts  # lazy: avoid import cycle at module load
        pending = drafts.list_pending_drafts(s, ctx.room_id)
        if pending:
            summaries = []
            for d in pending:
                att = d.attachments or {}
                names = _names_for(s, ctx.room_id, [att.get("payer_member_id")])
                summaries.append({
                    "draft_id": d.id,
                    "payer_name": names.get(att.get("payer_member_id"), "?"),
                    "bill_total": att.get("bill_total", 0),
                    "participant_count": len(att.get("member_participants") or []),
                })
            return {
                "ok": True,
                "type": "settle_blocked",
                "pending": summaries,
                "message": f"Có {len(pending)} đề xuất chưa xác nhận — xác nhận hoặc huỷ trước khi chốt.",
            }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/tools.py backend/tests/test_tools.py
git commit -m "feat(be): settle_period gates on open proposals (block & ask)"
```

---

### Task 6: `recommit_draft` (edit approved meal via void + re-record)

**Files:**
- Modify: `backend/app/drafts.py`
- Test: `backend/tests/test_drafts.py`

**Interfaces:**
- Consumes: `ledger.void_meal`, `ledger.record_meal`, `ledger.last_settlement`, existing `commit_draft` rendering.
- Produces:
  - `drafts._meal_message(session, room_id, att, res) -> RoomMessage` (extracted meal-card builder, reused by `commit_draft`).
  - `drafts.recommit_draft(session, draft_id, room_id, patch, logged_by) -> RoomMessage`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_drafts.py`:

```python
def test_recommit_draft_edits_committed_meal(db):
    from app.models import Meal, RoomMessage
    from app import drafts, ledger
    from tests.test_ledger import _seed_room
    room_id, ids = _seed_room(db, 3)
    with db.session() as s:
        d, _ = drafts.create_draft(s, room_id, {
            "payer_member_id": ids[0], "member_participants": ids, "guests": [],
            "bill_total": 300, "adjustments": [], "per_head_preview": 100, "raw_input": "x"})
        drafts.commit_draft(s, d.id, room_id, logged_by="1")
        old_meal_id = s.get(RoomMessage, d.id).attachments["committed_meal_id"]
        drafts.recommit_draft(s, d.id, room_id, {"bill_total": 600}, logged_by="1")
        att = s.get(RoomMessage, d.id).attachments
        assert att["committed_meal_id"] != old_meal_id
        assert s.get(Meal, old_meal_id).voided is True
        assert s.get(Meal, att["committed_meal_id"]).total_amount == 600


def test_recommit_blocked_when_meal_is_settled(db):
    from datetime import date
    from app.models import RoomMessage
    from app import drafts, ledger
    from tests.test_ledger import _seed_room
    room_id, ids = _seed_room(db, 3)
    with db.session() as s:
        d, _ = drafts.create_draft(s, room_id, {
            "payer_member_id": ids[0], "member_participants": ids, "guests": [],
            "bill_total": 300, "adjustments": [], "per_head_preview": 100, "raw_input": "x"})
        meal_msg = drafts.commit_draft(s, d.id, room_id, logged_by="1")
        occurred = date.fromisoformat(meal_msg.attachments["occurred_on"])
        ledger.record_settlement(s, room_id=room_id, period_from=None,
                                 period_to=occurred, requested_by="1", transfers=[])
        with pytest.raises(ledger.LedgerError):
            drafts.recommit_draft(s, d.id, room_id, {"bill_total": 600}, logged_by="1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_drafts.py -k recommit -v`
Expected: FAIL — `AttributeError: module 'app.drafts' has no attribute 'recommit_draft'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/drafts.py`, add `Meal` to the models import:

```python
from app.models import Meal, Member, RoomMessage
```

Extract the meal-card builder from `commit_draft` into a helper and reuse it. Replace the block in `commit_draft` from `names = _all_member_names(...)` through `meal_msg = chat.post_message(...)` with `meal_msg = _meal_message(session, room_id, att, res)`, and add:

```python
def _meal_message(session: Session, room_id: int, att: dict, res: dict) -> RoomMessage:
    """Build + persist the committed-meal bot card from a record_meal result."""
    names = _all_member_names(session, room_id)
    meal_att = {
        "type": "meal",
        "meal_id": res["meal_id"],
        "occurred_on": res["occurred_on"],
        "bill_total": res["bill_total"],
        "tracked_total": res["tracked_total"],
        "guests": res["guests"],
        "dish": att.get("dish"),
        "initiator": att.get("initiator"),
        "note": att.get("note"),
        "payer": {"id": res["payer_member_id"], "name": names.get(res["payer_member_id"], "?")},
        "shares": [{"id": mid, "name": names.get(mid, "?"), "amount": amt}
                   for mid, amt in res["shares"].items()],
        "balances": current_balances(session, room_id),
    }
    body = chat._meal_body(meal_att)
    return chat.post_message(session, room_id, None, body, attachments=meal_att, kind="bot")
```

Add `recommit_draft`:

```python
def recommit_draft(session: Session, draft_id: int, room_id: int, patch: dict,
                   logged_by: str | None) -> RoomMessage:
    """Edit an already-committed draft: void its meal and re-record with the
    edited fields (ledger stays append-only). Rejected if the meal is inside a
    committed settlement — the closed period's numbers must not shift."""
    m = session.get(RoomMessage, draft_id)
    if m is None or m.room_id != room_id or m.kind != "expense_draft":
        raise ledger.LedgerError(f"Draft #{draft_id} not found.")
    att = dict(m.attachments or {})
    if att.get("status") != "committed" or not att.get("committed_meal_id"):
        raise ledger.LedgerError("Only a recorded draft can be edited.")
    meal = session.get(Meal, att["committed_meal_id"])
    if meal is None or meal.voided:
        raise ledger.LedgerError("The recorded meal is missing or already voided.")
    last = ledger.last_settlement(session, room_id)
    if last is not None and meal.occurred_on <= last.period_to:
        raise ledger.LedgerError(
            "Bữa ăn này đã được chốt — hãy ghi một khoản điều chỉnh mới thay vì sửa."
        )
    for k in _EDITABLE:
        if k in patch:
            att[k] = patch[k]
    ledger.void_meal(session, meal.id, room_id=room_id, by=logged_by)
    res = ledger.record_meal(
        session, room_id=room_id, payer_member_id=int(att["payer_member_id"]),
        participants=[int(x) for x in att["member_participants"]],
        total_amount=int(att["bill_total"]), adjustments=_adjustments_map(att),
        guests=[str(g) for g in att.get("guests") or []], dish=att.get("dish"),
        initiator=att.get("initiator"), note=att.get("note"),
        raw_input=att.get("raw_input"), logged_by=logged_by, occurred_on=meal.occurred_on,
    )
    meal_msg = _meal_message(session, room_id, att, res)
    att["committed_meal_id"] = res["meal_id"]
    m.attachments = att
    session.flush()
    return meal_msg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_drafts.py tests/test_golden_meals.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/drafts.py backend/tests/test_drafts.py
git commit -m "feat(be): recommit_draft edits approved meals (void+re-record, settled-guard)"
```

---

### Task 7: Chat rendering — payment, settle_blocked bodies + dispatch

**Files:**
- Modify: `backend/app/chat.py`
- Test: `backend/tests/test_chat.py`

**Interfaces:**
- Consumes: tool results (Tasks 3, 5).
- Produces: `chat._payment_body(att)`, `chat._settle_blocked_body(att)`; `render_bot_attachments` maps `record_payment`→`{type:"payment"}` and `settle_period` blocked→`{type:"settle_blocked"}`; `run_bot_turn` renders both.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_chat.py`:

```python
def test_payment_body_renders_from_dict():
    from app.chat import _payment_body
    body = _payment_body({"from": {"name": "An"}, "to": {"name": "Bình"}, "amount": 125000})
    assert "An" in body and "Bình" in body and "125,000" in body


def test_settle_blocked_body_lists_pending():
    from app.chat import _settle_blocked_body
    body = _settle_blocked_body({
        "message": "Có 1 đề xuất chưa xác nhận — xác nhận hoặc huỷ trước khi chốt.",
        "pending": [{"draft_id": 7, "payer_name": "An", "bill_total": 400000, "participant_count": 4}],
    })
    assert "#7" in body and "An" in body and "400,000" in body


def test_render_bot_attachments_dispatch():
    from app.chat import render_bot_attachments

    class R:
        def __init__(self, mapping):
            self._m = mapping
        def last_result(self, name):
            return self._m.get(name)

    assert render_bot_attachments(R({"record_payment": {"type": "payment", "amount": 1}}))["type"] == "payment"
    assert render_bot_attachments(R({"settle_period": {"type": "settle_blocked", "pending": []}}))["type"] == "settle_blocked"
    assert render_bot_attachments(R({"settle_period": {"transfers": []}}))["type"] == "settlement"
```

(Use the file's existing fake-result helper if present — `test_chat.py` already defines a minimal `TurnResult` stand-in near the top.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_chat.py -k "payment_body or settle_blocked or dispatch" -v`
Expected: FAIL — `_payment_body`/`_settle_blocked_body` undefined.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/chat.py`, update `render_bot_attachments`:

```python
def render_bot_attachments(result) -> dict | None:
    settle = result.last_result("settle_period")
    if settle:
        if settle.get("type") == "settle_blocked":
            return dict(settle)
        return {"type": "settlement", **settle}
    payment = result.last_result("record_payment")
    if payment:
        return dict(payment)  # already {type:"payment", ...}
    return None
```

Add the two body builders next to `_settlement_body`:

```python
def _payment_body(attachments: dict) -> str:
    frm = attachments.get("from") or {}
    to = attachments.get("to") or {}
    return (
        f"💸 Đã ghi: {frm.get('name', '?')} trả {to.get('name', '?')} "
        f"{attachments.get('amount', 0):,}đ"
    )


def _settle_blocked_body(attachments: dict) -> str:
    lines = [attachments.get("message") or "Có đề xuất chưa xác nhận."]
    for p in attachments.get("pending") or []:
        lines.append(
            f"• #{p['draft_id']}: {p.get('payer_name', '?')} trả "
            f"{p.get('bill_total', 0):,}đ ({p.get('participant_count', 0)} người)"
        )
    return "\n".join(lines)
```

In `run_bot_turn`, replace the body-selection `if/else` (the `if attachments and attachments.get("type") == "settlement":` block) with:

```python
            if attachments and attachments.get("type") == "settlement":
                body = _settlement_body(attachments)
            elif attachments and attachments.get("type") == "settle_blocked":
                body = _settle_blocked_body(attachments)
            elif attachments and attachments.get("type") == "payment":
                body = _payment_body(attachments)
            else:
                body = result.final_text or (result.error and f"⚠️ {result.error}") or "(không có phản hồi)"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_chat.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/chat.py backend/tests/test_chat.py
git commit -m "feat(be): render payment + settle_blocked bodies server-side"
```

---

### Task 8: `recommit` API route

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `drafts.recommit_draft` (Task 6).
- Produces: `POST /api/rooms/{room_id}/drafts/{draft_id}/recommit` → `{ok:True, meal_id}`; 409 on `LedgerError`/`MoneyError`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_api.py`, following the file's existing auth/room/client fixtures (reuse whatever helper other tests use to create a room, join, and get an authorized client + a committed draft). Skeleton:

```python
def test_recommit_route_edits_committed_meal(client, committed_draft):
    room_id, draft_id, headers = committed_draft
    r = client.post(f"/api/rooms/{room_id}/drafts/{draft_id}/recommit",
                    json={"payer_member_id": ..., "member_participants": [...],
                          "guests": [], "bill_total": 600, "adjustments": []},
                    headers=headers)
    assert r.status_code == 200
    assert r.json()["meal_id"] > 0
```

If `test_api.py` has no committed-draft fixture, build one inline in the test using the existing message/commit routes. Also add a 409 case where the meal is settled (record a settlement covering it, then expect 409).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_api.py -k recommit -v`
Expected: FAIL — 404 (route not found).

- [ ] **Step 3: Write minimal implementation**

In `backend/app/main.py`, add a request model near `DraftPatchIn`:

```python
class DraftEditIn(BaseModel):
    payer_member_id: int
    member_participants: list[int]
    guests: list[str] = []
    bill_total: int
    adjustments: list[dict] = []
    dish: str | None = None
    initiator: str | None = None
    note: str | None = None
```

Add the route after `commit_draft_route`:

```python
@app.post("/api/rooms/{room_id}/drafts/{draft_id}/recommit")
async def recommit_draft_route(room_id: int, draft_id: int, body: DraftEditIn,
                               ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    db = get_db()
    patch = body.model_dump(exclude_unset=True)
    async with chat._agent_lock:
        with db.session() as s:
            try:
                meal_msg = drafts.recommit_draft(s, draft_id, room_id, patch,
                                                 logged_by=str(ctx.member_id))
            except (ledger.LedgerError, MoneyError) as e:
                raise HTTPException(409, str(e))
            meal_payload = chat.message_to_dict(meal_msg, None)
            draft_payload = chat.message_to_dict(s.get(RoomMessage, draft_id), None)
            meal_id = meal_msg.attachments["meal_id"]
    await hub.publish(room_id, {"type": "message", **draft_payload})
    await hub.publish(room_id, {"type": "message", **meal_payload})
    return {"ok": True, "meal_id": meal_id}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_api.py
git commit -m "feat(be): POST drafts/{id}/recommit route for editing approved meals"
```

---

### Task 9: Prompt guidance — payments + reset routing

**Files:**
- Modify: `backend/app/prompt.py`
- Test: `backend/tests/test_agent.py` (or a small `test_prompt.py` if agent tests don't cover the prompt string)

**Interfaces:**
- Produces: `build_system_prompt(...)` mentions `record_payment` and routes "trả đủ rồi / reset" to `settle_period commit:true`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_agent.py`:

```python
def test_system_prompt_mentions_payment_and_reset():
    from app.prompt import build_system_prompt
    p = build_system_prompt()
    assert "record_payment" in p
    assert "reset" in p.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_agent.py -k prompt -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/prompt.py`, insert before the `# Ảnh hoá đơn` section:

```python
        "## Ghi trả tiền mặt (không phải bữa ăn)\n"
        "- 'A đưa/trả B <số tiền>' hoặc 'tôi nhận <số tiền> từ B' → `record_payment`\n"
        "  với from = người trả, to = người nhận, amount = số tiền (VND).\n"
        "- Đây KHÔNG phải bữa ăn — không dùng `propose_meal`.\n"
        "\n"
        "## Chốt/reset số dư\n"
        "- 'trả đủ rồi', 'reset', 'reset số dư' → `settle_period` với `commit:true`.\n"
        "- ĐỪNG ghi việc này bằng `record_payment` (sẽ tạo lệch mới trên kỳ đã đóng).\n"
        "- Nếu còn đề xuất chưa xác nhận, công cụ sẽ báo — nhắc người dùng xác nhận/huỷ trước.\n"
        "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_agent.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/prompt.py backend/tests/test_agent.py
git commit -m "feat(be): prompt guidance for record_payment + reset routing"
```

---

### Task 10: Frontend — recommit API + edit-when-committed card

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/components/chat/expense-draft-card.tsx`
- Test: `frontend/src/components/chat/__tests__/expense-draft-card.test.tsx`

**Interfaces:**
- Consumes: `POST .../drafts/{id}/recommit` (Task 8).
- Produces: `api.recommitDraft(roomId, draftId, fields)`; committed card shows **Edit** → editable fields → **Save changes**/**Cancel edit**.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/components/chat/__tests__/expense-draft-card.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ExpenseDraftCard } from "../expense-draft-card";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  patchDraft: vi.fn(() => Promise.resolve()),
  commitDraft: vi.fn(() => Promise.resolve()),
  cancelDraft: vi.fn(() => Promise.resolve()),
  recommitDraft: vi.fn(() => Promise.resolve({ ok: true, meal_id: 9 })),
}));
import * as api from "@/lib/api";

const members = [{ id: 1, display_name: "A" }, { id: 2, display_name: "B" }];
const committed = {
  id: 50,
  attachments: {
    type: "expense_draft", status: "committed", committed_meal_id: 9,
    payer_member_id: 1, member_participants: [1, 2], guests: [],
    bill_total: 300, adjustments: [],
  },
};

describe("ExpenseDraftCard edit-when-committed", () => {
  it("shows an Edit button on a committed card", () => {
    render(<ExpenseDraftCard message={committed} members={members} roomId={1} />);
    expect(screen.getByRole("button", { name: /edit/i })).toBeInTheDocument();
  });

  it("editing re-enables fields and Save calls recommitDraft", () => {
    render(<ExpenseDraftCard message={committed} members={members} roomId={1} />);
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    const total = screen.getByLabelText(/bill total/i) as HTMLInputElement;
    expect(total.disabled).toBe(false);
    fireEvent.change(total, { target: { value: "600" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    expect(api.recommitDraft).toHaveBeenCalledWith(1, 50, expect.objectContaining({ bill_total: 600 }));
  });
});
```

Note: this requires the Bill-total input to be associated with its label — add `id`/`htmlFor` or `aria-label="Bill total"` to that input while implementing.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/chat/__tests__/expense-draft-card.test.tsx`
Expected: FAIL — no Edit button; `recommitDraft` not exported.

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/lib/api.ts`, add (mirroring `commitDraft`'s shape):

```ts
export function recommitDraft(
  roomId: number,
  draftId: number,
  fields: {
    payer_member_id: number; member_participants: number[]; guests: string[];
    bill_total: number; adjustments: { member: number; amount: number }[];
    dish?: string | null; initiator?: string | null; note?: string | null;
  },
) {
  return apiFetch(`/api/rooms/${roomId}/drafts/${draftId}/recommit`, {
    method: "POST",
    body: JSON.stringify(fields),
  });
}
```

(Match the file's existing `apiFetch`/request helper name and signature.)

In `frontend/src/components/chat/expense-draft-card.tsx`:

1. Add editing state: `const [editing, setEditing] = useState(false);`
2. Change `const readonly = att.status !== "pending";` to
   `const readonly = att.status !== "pending" && !editing;`
3. Add `aria-label="Bill total"` to the bill-total `<input>` (line ~123).
4. After the existing pending-only action block, add a committed-only block:

```tsx
      {att.status === "committed" && !editing && (
        <button type="button" onClick={() => setEditing(true)}
          className="mt-1 text-xs font-medium text-[var(--accent-text)]">
          Edit
        </button>
      )}
      {att.status === "committed" && editing && (
        <div className="mt-1 flex gap-2">
          <button type="button" disabled={busy}
            onClick={() => {
              setBusy(true); setError(null);
              api.recommitDraft(roomId, message.id, {
                payer_member_id: payer, member_participants: billed, guests,
                bill_total: total, adjustments,
                dish: dish || null, initiator: initiator || null, note: note || null,
              })
                .then(() => setEditing(false))
                .catch((err) => setError(err instanceof ApiError ? err.message : "Couldn't save."))
                .finally(() => setBusy(false));
            }}
            className="flex-1 rounded-lg bg-[var(--accent-primary)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
            Save changes
          </button>
          <button type="button" disabled={busy}
            onClick={() => { setEditing(false); setError(null); }}
            className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text-secondary)]">
            Cancel edit
          </button>
        </div>
      )}
```

Note: the debounced auto-PATCH effect (lines ~47-62) returns early when `readonly`. Since `readonly` is now false while editing a committed card, guard that effect to pending-only: change its `if (readonly) return;` to `if (att.status !== "pending") return;` so editing a committed draft does NOT PATCH (recommit is explicit via Save).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test`
Expected: PASS (all frontend suites).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/components/chat/expense-draft-card.tsx frontend/src/components/chat/__tests__/expense-draft-card.test.tsx
git commit -m "feat(fe): edit an approved meal draft (recommit)"
```

---

### Task 11: Scenario spec (declarative data)

**Files:**
- Create: `backend/tests/golden/scenario_week.py`

**Interfaces:**
- Produces: `MEMBERS` (list of `{key, display_name, nickname, bank?}`), `STEPS` (ordered list of step dicts). Consumed by Tasks 12 & 13.

- [ ] **Step 1: Write the spec module**

Create `backend/tests/golden/scenario_week.py`:

```python
"""Declarative week-long scenario for the behavioral eval (deterministic +
opt-in LLM runners). Member keys a1..a5 are resolved to ids by the runner.
Amounts are integer VND. `day` is an ISO date; the runner freezes the clock to
noon ICT on that day. Payees (a1,a2,a4) get bank details so QR builds succeed.

`kind` values:
  meal_confirmed   — create draft + commit (a normal logged, confirmed meal)
  payment          — ledger.record_payment(from,to,amount)
  add_member       — add a new member (key)
  leave_pending    — create draft but DO NOT commit (stays an open proposal)
  confirm_pending  — commit the draft created by a named earlier step (`ref`)
  settle           — settle_period commit:false; expect transfers OR blocked
  settle_commit    — settle_period commit:true (reset/close the period)

`expect` keys: balances {key: vnd}, transfers [{from,to,amount}],
qr_payees [keys], blocked_pending (int count), empty (bool).
"""

MON = "2026-07-20"; TUE = "2026-07-21"; WED = "2026-07-22"
THU = "2026-07-23"; FRI = "2026-07-24"; NEXT_MON = "2026-07-27"

MEMBERS = [
    {"key": "a1", "display_name": "A1", "nickname": "a1",
     "bank": {"bank_code": "VCB", "account_number": "111", "account_holder": "A1"}},
    {"key": "a2", "display_name": "A2", "nickname": "a2",
     "bank": {"bank_code": "VCB", "account_number": "222", "account_holder": "A2"}},
    {"key": "a3", "display_name": "A3", "nickname": "a3"},
    {"key": "a4", "display_name": "A4", "nickname": "a4",
     "bank": {"bank_code": "VCB", "account_number": "444", "account_holder": "A4"}},
    # a5 is added mid-scenario (step 6), not seeded up front.
]

STEPS = [
    {"id": "s1", "day": MON, "actor": "a1", "kind": "meal_confirmed",
     "message": "@bot tôi trả 300k cả nhóm",
     "payer": "a1", "participants": ["a1", "a2", "a3", "a4"], "total": 300_000,
     "expect": {"balances": {"a1": 225_000, "a2": -75_000, "a3": -75_000, "a4": -75_000}}},

    {"id": "s2", "day": TUE, "actor": "a1", "kind": "meal_confirmed",
     "message": "@bot tôi trả 150k, a4 không ăn",
     "payer": "a1", "participants": ["a1", "a2", "a3"], "total": 150_000,
     "expect": {"balances": {"a1": 325_000, "a2": -125_000, "a3": -125_000, "a4": -75_000}}},

    {"id": "s3", "day": TUE, "actor": "a1", "kind": "payment",
     "message": "@bot tôi nhận 125k từ a2",
     "from": "a2", "to": "a1", "amount": 125_000,
     "expect": {"balances": {"a1": 200_000, "a2": 0, "a3": -125_000, "a4": -75_000}}},

    {"id": "s4", "day": WED, "actor": "a2", "kind": "meal_confirmed",
     "message": "@bot tôi trả 500k, cả nhóm 4 người + 1 khách",
     "payer": "a2", "participants": ["a1", "a2", "a3", "a4"], "total": 500_000,
     "guests": ["guest1"],
     "expect": {"balances": {"a1": 100_000, "a2": 300_000, "a3": -225_000, "a4": -175_000}}},

    {"id": "s5", "day": WED, "actor": "a3", "kind": "settle",
     "message": "@bot tôi phải trả bao nhiêu",
     "expect": {"transfers": [{"from": "a3", "to": "a2", "amount": 225_000},
                              {"from": "a4", "to": "a1", "amount": 100_000},
                              {"from": "a4", "to": "a2", "amount": 75_000}],
                "qr_payees": ["a1", "a2"]}},

    {"id": "s6", "day": THU, "actor": "a4", "kind": "add_member",
     "message": "@bot thêm thành viên a5", "new_member": "a5"},

    {"id": "s7", "day": THU, "actor": "a4", "kind": "leave_pending",
     "message": "@bot tôi trả 400k, a2 không ăn",
     "payer": "a4", "participants": ["a1", "a3", "a4", "a5"], "total": 400_000},

    {"id": "s8", "day": FRI, "actor": "a5", "kind": "settle",
     "message": "@bot tính tiền",
     "expect": {"blocked_pending": 1}},

    {"id": "s9a", "day": FRI, "actor": "a1", "kind": "confirm_pending", "ref": "s7"},
    {"id": "s9b", "day": FRI, "actor": "a1", "kind": "leave_pending",
     "message": "@bot tôi trả 300k cho cả nhóm",
     "payer": "a1", "participants": ["a1", "a2", "a3", "a4", "a5"], "total": 300_000},

    {"id": "s10a", "day": FRI, "actor": "a5", "kind": "confirm_pending", "ref": "s9b"},
    {"id": "s10b", "day": FRI, "actor": "a5", "kind": "settle",
     "message": "@bot tính tiền",
     "expect": {"transfers": [{"from": "a3", "to": "a1", "amount": 240_000},
                              {"from": "a5", "to": "a2", "amount": 160_000},
                              {"from": "a3", "to": "a2", "amount": 80_000},
                              {"from": "a3", "to": "a4", "amount": 65_000}],
                "qr_payees": ["a1", "a2", "a4"]}},

    {"id": "s11", "day": FRI, "actor": "a1", "kind": "settle_commit",
     "message": "@bot trả đủ rồi, reset balance"},

    {"id": "s12", "day": NEXT_MON, "actor": "a1", "kind": "settle",
     "message": "@bot còn ai nợ ai gì không",
     "expect": {"empty": True}},
]
```

- [ ] **Step 2: Verify it imports**

Run: `cd backend && python -c "from tests.golden.scenario_week import STEPS, MEMBERS; print(len(STEPS), len(MEMBERS))"`
Expected: prints `14 4`.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/golden/scenario_week.py
git commit -m "test(be): declarative week-long scenario spec"
```

---

### Task 12: Deterministic scenario runner (CI)

**Files:**
- Create: `backend/tests/test_scenario_week.py`

**Interfaces:**
- Consumes: `scenario_week.{MEMBERS,STEPS}`; `ledger`, `drafts`, `tools`, `chat`, `app.clock`.

- [ ] **Step 1: Write the runner test**

Create `backend/tests/test_scenario_week.py`:

```python
"""Deterministic end-to-end eval: plays scenario_week.STEPS through the real
money engine with the clock frozen per day, asserting balances/transfers/QR and
rendered bodies at each step. No LLM involved — the correct tool calls are
encoded by `kind`."""
from datetime import date, datetime, time

import pytest

from app import chat, drafts, ledger, tools
from app.clock import ICT
from app.models import Member, Room
from tests.golden.scenario_week import MEMBERS, STEPS


def _seed(db):
    ids = {}
    with db.session() as s:
        room = Room(name="Week", invite_token="week-tok")
        s.add(room); s.flush()
        for spec in MEMBERS:
            m = Member(room_id=room.id, display_name=spec["display_name"],
                       nickname=spec["nickname"], pin="1", **(spec.get("bank") or {}))
            s.add(m); s.flush()
            ids[spec["key"]] = m.id
        return room.id, ids


def _freeze(monkeypatch, day_iso):
    d = date.fromisoformat(day_iso)
    frozen = datetime.combine(d, time(12, 0), tzinfo=ICT)
    monkeypatch.setattr("app.clock.now_ict", lambda: frozen)


def _balances(db, room_id):
    with db.session() as s:
        last = ledger.last_settlement(s, room_id)
        from app.periods import resolve_period
        from app.clock import today_ict
        period = resolve_period("since_last", today=today_ict(),
                                last_settlement_to=last.period_to if last else None)
        return {mid: v["balance"] for mid, v in
                ledger.period_balances(s, room_id, period["from"], period["to"]).items()}


def test_scenario_week(db, monkeypatch):
    room_id, ids = _seed(db)
    draft_by_step = {}  # step id -> draft_id (for confirm_pending refs)

    for step in STEPS:
        _freeze(monkeypatch, step["day"])
        kind = step["kind"]
        actor = ids.get(step["actor"])

        if kind == "add_member":
            with db.session() as s:
                m = Member(room_id=room_id, display_name=step["new_member"].upper(),
                           nickname=step["new_member"], pin="1")
                s.add(m); s.flush()
                ids[step["new_member"]] = m.id

        elif kind in ("meal_confirmed", "leave_pending"):
            payload = {
                "payer_member_id": ids[step["payer"]],
                "member_participants": [ids[p] for p in step["participants"]],
                "guests": step.get("guests", []),
                "bill_total": step["total"], "adjustments": [],
                "per_head_preview": 0, "raw_input": step["message"],
            }
            with db.session() as s:
                d, _ = drafts.create_draft(s, room_id, payload)
                draft_by_step[step["id"]] = d.id
                if kind == "meal_confirmed":
                    drafts.commit_draft(s, d.id, room_id, logged_by=str(actor))

        elif kind == "confirm_pending":
            with db.session() as s:
                drafts.commit_draft(s, draft_by_step[step["ref"]], room_id, logged_by=str(actor))

        elif kind == "payment":
            with db.session() as s:
                ledger.record_payment(s, room_id=room_id, from_member_id=ids[step["from"]],
                                      to_member_id=ids[step["to"]], amount=step["amount"],
                                      logged_by=str(actor))

        elif kind in ("settle", "settle_commit"):
            ctx = tools.ToolContext(db=db, room_id=room_id, sender_member_id=actor)
            res = tools.build_tools(ctx)["settle_period"].execute(
                {"keyword": "since_last", "commit": kind == "settle_commit"})
            exp = step.get("expect", {})
            if exp.get("blocked_pending") is not None:
                assert res["type"] == "settle_blocked", step["id"]
                assert len(res["pending"]) == exp["blocked_pending"], step["id"]
                continue
            assert res.get("type") != "settle_blocked", step["id"]
            if exp.get("empty"):
                assert res["transfers"] == [], step["id"]
            if "transfers" in exp:
                got = [{"from": t["from_id"], "to": t["to_id"], "amount": t["amount"]}
                       for t in res["transfers"]]
                want = [{"from": ids[t["from"]], "to": ids[t["to"]], "amount": t["amount"]}
                        for t in exp["transfers"]]
                assert got == want, f'{step["id"]}: {got} != {want}'
                body = chat._settlement_body({"type": "settlement", **res})
                for t in exp["transfers"]:
                    assert f'{t["amount"]:,}' in body, step["id"]
            for payee_key in exp.get("qr_payees", []):
                payee_id = ids[payee_key]
                rows = [t for t in res["transfers"] if t["to_id"] == payee_id]
                assert rows and all(t["qr_url"] for t in rows), f'{step["id"]} qr {payee_key}'

        # Balance assertion (when the step declares expected balances).
        exp = step.get("expect", {})
        if "balances" in exp:
            bal = _balances(db, room_id)
            for key, want in exp["balances"].items():
                assert bal.get(ids[key], 0) == want, f'{step["id"]} {key}: {bal.get(ids[key])} != {want}'
```

- [ ] **Step 2: Run it to verify it passes (implementation already exists via Tasks 1-7)**

Run: `cd backend && python -m pytest tests/test_scenario_week.py -v`
Expected: PASS. If a `transfers` mismatch appears, re-derive against `money.net_transfers` (the greedy algorithm re-selects the max debtor each iteration) and fix the spec's expected edges — the runner is the source of truth for the algorithm.

- [ ] **Step 3: Run the full backend suite**

Run: `cd backend && python -m pytest -q`
Expected: PASS (all green).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_scenario_week.py
git commit -m "test(be): deterministic week-long scenario eval"
```

---

### Task 13: Opt-in LLM scenario runner

**Files:**
- Create: `backend/tests/test_scenario_week_llm.py`

**Interfaces:**
- Consumes: `scenario_week.{MEMBERS,STEPS}`; `agent.run_turn`; `tools.ToolContext`.

- [ ] **Step 1: Write the opt-in runner**

Create `backend/tests/test_scenario_week_llm.py`:

```python
"""Opt-in LLM eval: replays each scenario message through the real Cursor agent
and asserts the bot selected the expected tool(s). Skipped in CI — set
RUN_LLM_EVAL=1 (and valid Cursor creds) to run. Non-deterministic; tolerant of
extra tool calls (e.g. find_members) and VN/EN prose."""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_LLM_EVAL"),
    reason="LLM eval is opt-in; set RUN_LLM_EVAL=1 to run.",
)

# message kind -> tool we expect the bot to call
EXPECTED_TOOL = {
    "meal_confirmed": "propose_meal",
    "leave_pending": "propose_meal",
    "payment": "record_payment",
    "add_member": "add_member",
    "settle": "settle_period",
    "settle_commit": "settle_period",
}


@pytest.mark.asyncio
async def test_scenario_week_llm(db):
    from app import drafts, ledger, tools
    from app.agent import run_turn
    from app.models import Member, Room
    from tests.golden.scenario_week import MEMBERS, STEPS

    ids = {}
    with db.session() as s:
        room = Room(name="WeekLLM", invite_token="week-llm")
        s.add(room); s.flush()
        for spec in MEMBERS:
            m = Member(room_id=room.id, display_name=spec["display_name"],
                       nickname=spec["nickname"], pin="1", **(spec.get("bank") or {}))
            s.add(m); s.flush()
            ids[spec["key"]] = m.id
        room_id = room.id

    for step in STEPS:
        if step["kind"] == "confirm_pending":
            with db.session() as s:  # confirmation is a UI action, not an LLM turn
                pass
            continue
        expected = EXPECTED_TOOL.get(step["kind"])
        if not expected or "message" not in step:
            continue
        ctx = tools.ToolContext(db=db, room_id=room_id, sender_member_id=ids[step["actor"]])
        result = await run_turn(step["message"], ctx)
        called = {inv.name for inv in result.tools}
        assert expected in called, f'{step["id"]}: expected {expected}, got {sorted(called)}'
```

- [ ] **Step 2: Verify it is collected but skipped without the env flag**

Run: `cd backend && python -m pytest tests/test_scenario_week_llm.py -v`
Expected: SKIPPED (1 skipped) — confirms the gate works and CI stays clean.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_scenario_week_llm.py
git commit -m "test(be): opt-in LLM scenario eval (skipped unless RUN_LLM_EVAL=1)"
```

---

## Final verification

- [ ] `cd backend && python -m pytest -q` — all green (LLM eval skipped).
- [ ] `cd frontend && npm test` — all green.
- [ ] `cd frontend && npx tsc --noEmit` — clean.
- [ ] Review the diff; open a PR from `feat/scenario-eval-and-payments` to `main`.
