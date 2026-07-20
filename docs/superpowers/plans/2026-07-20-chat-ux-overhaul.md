# Chat UX Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live agent timeline, an `@bot` composer dropdown, an optimistic human-in-the-loop expense-draft flow (with occasional guest-pays-cash math, auto-save on supersede, and story metadata), and a post-log balance table to the chiatienan room chat.

**Architecture:** The Cursor agent only *proposes* meals (new `propose_meal` tool, writes nothing); all ledger writes go through deterministic server code (`commit_draft`). Drafts are live server-side `RoomMessage` rows (`kind="expense_draft"`) edited via PATCH and committed explicitly or when superseded by a newer draft. The agent turn streams `agent.*` progress events over the existing `RoomHub` SSE while still returning the same money-safe `TurnResult`.

**Tech Stack:** Backend — Python 3.14, FastAPI, SQLAlchemy (SQLite/WAL), cursor-sdk, pytest. Frontend — Next.js (App Router), React, TypeScript, Tailwind, Vitest.

## Global Constraints

- **Money-safety (D3):** authoritative numbers are computed server-side and rendered from structured payloads — never from LLM prose. The LLM proposes; it never writes to the ledger.
- **All amounts are integer VND.** No floats anywhere in money math.
- **Single uvicorn worker:** the `_agent_lock` and the single-pending-draft invariant assume one process (already required by the ledger single-writer design).
- **Language:** all user-facing bot/UI copy is Vietnamese.
- **Guests are never members:** occasional guests never get a ledger row, never appear in settlement or QR.
- **Draft lifecycle:** `pending` → (`committed` | `cancelled`); at most one `pending` draft per room.
- **TDD + frequent commits:** every task is test-first and ends in a commit.
- Run backend tests from `backend/` with `.venv` active: `cd backend && .venv/bin/pytest`.
- Run frontend tests from `frontend/`: `cd frontend && npm test`.

---

## File Structure

**Backend (create):**
- `backend/tests/test_split_with_guests.py` — unit tests for the new pure function.
- `backend/tests/test_drafts.py` — draft lifecycle + commit/supersede/cancel.
- `backend/tests/test_agui.py` — AG-UI translator.
- `backend/tests/golden/meals.py` — the golden scenario dataset (G1–G12).
- `backend/tests/test_golden_meals.py` — runs the golden dataset end-to-end.
- `backend/app/agui.py` — Cursor-message → `agent.*` event translator.
- `backend/app/drafts.py` — draft persistence + `commit_draft` + supersede flush.

**Backend (modify):**
- `backend/app/money.py` — add `split_with_guests`.
- `backend/app/models.py` — `Meal.dish`, `Meal.initiator`, `Meal.guests`; `RoomMessage.kind` doc.
- `backend/app/ledger.py` — `record_meal` gains `guests`.
- `backend/app/tools.py` — replace `record_meal` tool with `propose_meal`.
- `backend/app/agent.py` — `run_turn` gains an `emit` streaming callback.
- `backend/app/chat.py` — meal-body/attachment rendering for new fields + balances.
- `backend/app/main.py` — draft PATCH / commit endpoints, `agent.*` wiring, expose `bot_handle`.

**Frontend (create):**
- `frontend/src/components/chat/agent-timeline.tsx`
- `frontend/src/components/chat/expense-draft-card.tsx`
- `frontend/src/components/chat/balance-table.tsx`
- `frontend/src/components/chat/mention-dropdown.tsx`
- `frontend/src/hooks/__tests__/timeline.test.ts`

**Frontend (modify):**
- `frontend/src/hooks/use-room.ts` — timeline slice in `mergeEvent`, optimistic echo.
- `frontend/src/components/chat/room-view.tsx` — render timeline, pass members/roomId down.
- `frontend/src/components/chat/message-list.tsx` — `expense_draft` branch.
- `frontend/src/components/chat/bot-message.tsx` — meal card new fields + `<BalanceTable>`.
- `frontend/src/components/chat/composer.tsx` — `@` dropdown + optimistic send.
- `frontend/src/lib/api.ts` — draft PATCH/commit, bot-handle.
- `frontend/src/types/chat.ts` — draft/meal/timeline types.

---

## Phase 1 — Money & ledger engine

### Task 1: `money.split_with_guests`

**Files:**
- Modify: `backend/app/money.py`
- Test: `backend/tests/test_split_with_guests.py` (create)

**Interfaces:**
- Consumes: existing `money.split_shares(total, participants, adjustments, payer_id)`.
- Produces: `split_with_guests(total: int, member_ids: list[int], guest_count: int, adjustments: dict[int,int] | None = None, payer_id: int | None = None) -> dict` returning `{"shares": dict[int,int], "per_head": int, "tracked_total": int, "guest_total": int, "headcount": int}`. `shares` maps **member** id → VND (guests excluded); `tracked_total == sum(shares.values())`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_split_with_guests.py
import pytest

from app.money import MoneyError, split_with_guests


def test_no_guests_matches_plain_split():
    r = split_with_guests(400_000, [1, 2, 3, 4], 0, payer_id=1)
    assert r["shares"] == {1: 100_000, 2: 100_000, 3: 100_000, 4: 100_000}
    assert r["tracked_total"] == 400_000
    assert r["guest_total"] == 0
    assert r["per_head"] == 100_000
    assert r["headcount"] == 4


def test_one_guest_pays_cash():
    # 400k over 4 heads (3 members + 1 guest) = 100k/head; members tracked, guest dropped
    r = split_with_guests(400_000, [1, 2, 3], 1, payer_id=1)
    assert r["shares"] == {1: 100_000, 2: 100_000, 3: 100_000}
    assert r["tracked_total"] == 300_000
    assert r["guest_total"] == 100_000
    assert r["per_head"] == 100_000
    assert r["headcount"] == 4


def test_two_guests_pay_cash():
    r = split_with_guests(400_000, [1, 2], 2, payer_id=2)
    assert r["shares"] == {1: 100_000, 2: 100_000}
    assert r["tracked_total"] == 200_000
    assert r["guest_total"] == 200_000


def test_remainder_stays_on_payer_member():
    # 100k over 3 heads (2 members + 1 guest): base 33333, remainder 1 → payer(1)
    r = split_with_guests(100_000, [1, 2], 1, payer_id=1)
    assert r["shares"] == {1: 33_334, 2: 33_333}
    assert r["tracked_total"] == 66_667
    assert r["guest_total"] == 33_333
    assert sum(r["shares"].values()) == r["tracked_total"]


def test_adjustment_on_member_with_guest():
    # 300k, members [1,2] + 1 guest; member 2 +30k
    r = split_with_guests(300_000, [1, 2], 1, {2: 30_000}, payer_id=1)
    # base = (300000 - 30000) // 3 = 90000 ; member2 = 120000, member1 = 90000, guest 90000
    assert r["shares"] == {1: 90_000, 2: 120_000}
    assert r["tracked_total"] == 210_000
    assert r["guest_total"] == 90_000


def test_rejects_no_members():
    with pytest.raises(MoneyError):
        split_with_guests(100_000, [], 2, payer_id=1)


def test_rejects_zero_total():
    with pytest.raises(MoneyError):
        split_with_guests(0, [1, 2], 1, payer_id=1)


def test_rejects_adjustment_for_non_member():
    with pytest.raises(MoneyError):
        split_with_guests(100_000, [1, 2], 1, {9: 10_000}, payer_id=1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_split_with_guests.py -v`
Expected: FAIL with `ImportError: cannot import name 'split_with_guests'`.

- [ ] **Step 3: Implement `split_with_guests`**

Append to `backend/app/money.py`:

```python
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
        raise MoneyError("Cần ít nhất một thành viên trong bữa ăn.")
    if guest_count < 0:
        raise MoneyError("Số khách không hợp lệ.")

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_split_with_guests.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/money.py backend/tests/test_split_with_guests.py
git commit -m "feat(money): split_with_guests (guest-pays-cash split)"
```

---

### Task 2: `Meal` metadata columns + `record_meal` guest support

**Files:**
- Modify: `backend/app/models.py:77-96` (Meal), `backend/app/ledger.py:23-83` (record_meal)
- Test: `backend/tests/test_ledger.py` (extend)

**Interfaces:**
- Consumes: `money.split_with_guests` (Task 1).
- Produces: `ledger.record_meal(..., guests: list[str] | None = None, dish: str | None = None, initiator: str | None = None)`. The persisted `Meal.total_amount` equals the **tracked** (member) total; the return dict gains `"bill_total"`, `"tracked_total"`, `"guests"`. `Meal` gains nullable `dish: str|None`, `initiator: str|None`, `guests: list` (JSON, default `[]`).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_ledger.py`:

```python
def test_record_meal_with_guest_tracks_members_only(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        res = ledger.record_meal(
            s, room_id=room_id, payer_member_id=a, participants=[a, b, c],
            total_amount=400_000, guests=["Emi"], occurred_on=date(2026, 7, 15),
        )
        assert res["bill_total"] == 400_000
        assert res["tracked_total"] == 300_000
        assert res["total_amount"] == 300_000       # persisted total = tracked
        assert res["guests"] == ["Emi"]
        assert sum(res["shares"].values()) == 300_000
        bal = ledger.period_balances(s, room_id, date(2026, 7, 1), date(2026, 7, 31))
        assert bal[a]["balance"] == 200_000          # paid 300k, consumed 100k
        assert bal[b]["balance"] == -100_000
        assert bal[c]["balance"] == -100_000


def test_record_meal_stores_metadata(db):
    room_id, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        res = ledger.record_meal(
            s, room_id=room_id, payer_member_id=a, participants=[a, b],
            total_amount=200_000, dish="phở", initiator="Emi",
            note="An đổi ý", raw_input="@bot 200k phở",
        )
        meal = s.get(__import__("app.models", fromlist=["Meal"]).Meal, res["meal_id"])
        assert meal.dish == "phở" and meal.initiator == "Emi"
        assert meal.note == "An đổi ý" and meal.raw_input == "@bot 200k phở"
        assert meal.guests == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_ledger.py::test_record_meal_with_guest_tracks_members_only -v`
Expected: FAIL (`record_meal() got an unexpected keyword argument 'guests'`).

- [ ] **Step 3: Add the Meal columns**

In `backend/app/models.py`, inside `class Meal`, add after `raw_input` (line 86):

```python
    dish: Mapped[str | None] = mapped_column(String(120))
    initiator: Mapped[str | None] = mapped_column(String(120))
    guests: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
```

Update the `RoomMessage.kind` comment (line 71) to:

```python
    kind: Mapped[str] = mapped_column(String(20), default="text", nullable=False)  # text|bot|expense_draft
```

- [ ] **Step 4: Update `record_meal`**

In `backend/app/ledger.py`, replace the signature and split/return of `record_meal` (lines 23-83). New signature adds `guests`, `dish`, `initiator`; the split routes through `split_with_guests`:

```python
from app.money import split_with_guests   # replace the split_shares import


def record_meal(
    session: Session,
    *,
    room_id: int,
    payer_member_id: int,
    participants: list[int],
    total_amount: int,
    adjustments: dict[int, int] | None = None,
    guests: list[str] | None = None,
    dish: str | None = None,
    initiator: str | None = None,
    occurred_on: date | None = None,
    note: str | None = None,
    raw_input: str | None = None,
    source: str = "web",
    logged_by: str | None = None,
) -> dict:
    """Validate, split, and write ``meals`` + ``meal_shares`` in one transaction.

    ``total_amount`` is the bill the group saw; ``guests`` are occasional
    non-members who pay their share in cash (they shrink the per-head but are
    never billed). The persisted ``Meal.total_amount`` is the **tracked** member
    total (bill − guest total), so balances/settlement stay correct.
    """
    guests = list(guests or [])
    payer = session.get(Member, payer_member_id)
    if payer is None or payer.room_id != room_id:
        raise LedgerError(f"Người trả tiền (id={payer_member_id}) không tồn tại.")

    known = {
        m.id
        for m in session.scalars(
            select(Member).where(Member.id.in_(participants), Member.room_id == room_id)
        )
    }
    missing = [p for p in participants if p not in known]
    if missing:
        raise LedgerError(f"Người tham gia không tồn tại: {missing}.")

    split = split_with_guests(
        total_amount, participants, len(guests), adjustments, payer_id=payer_member_id
    )
    shares = split["shares"]
    tracked_total = split["tracked_total"]

    meal = Meal(
        room_id=room_id,
        occurred_on=occurred_on or today_ict(),
        payer_member_id=payer_member_id,
        total_amount=tracked_total,
        note=note,
        raw_input=raw_input,
        dish=dish,
        initiator=initiator,
        guests=guests,
        source=source,
        logged_by=logged_by,
    )
    meal.shares = [MealShare(member_id=mid, share_amount=amt) for mid, amt in shares.items()]
    session.add(meal)
    session.flush()

    return {
        "meal_id": meal.id,
        "occurred_on": meal.occurred_on.isoformat(),
        "payer_member_id": payer_member_id,
        "bill_total": total_amount,
        "tracked_total": tracked_total,
        "total_amount": tracked_total,
        "guests": guests,
        "shares": dict(shares),
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_ledger.py -v`
Expected: PASS (existing ledger tests still green — `guests=None` → `guest_count=0` reproduces the old split; new tests green).

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/ledger.py backend/tests/test_ledger.py
git commit -m "feat(ledger): meal metadata + occasional guest support"
```

---

## Phase 2 — Draft engine (propose / commit / supersede)

### Task 3: `propose_meal` tool (writes nothing)

**Files:**
- Modify: `backend/app/tools.py` (replace `record_meal` tool with `propose_meal`), `backend/app/prompt.py` (tool guidance)
- Test: `backend/tests/test_tools.py` (extend)

**Interfaces:**
- Consumes: `roster.resolve`, `money.split_with_guests`, `ToolContext`.
- Produces: a `propose_meal` CustomTool whose result dict is the **draft payload** (no DB write): `{"ok": True, "type": "expense_draft", "payer_member_id", "member_participants": [int], "guests": [str], "bill_total": int, "adjustments": [{"member","amount"}], "dish", "initiator", "note", "per_head_preview": int}`. `TurnResult.last_result("propose_meal")` is how `chat` picks it up.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_tools.py` (follow the file's existing `ToolContext`/`build_tools` setup pattern; seed a room like `test_ledger._seed_room`):

```python
def test_propose_meal_writes_nothing_and_previews(db):
    from app import ledger
    from app.models import Meal
    from app.tools import ToolContext, build_tools

    room_id, (a, b, c) = _seed_room(db, 3)   # reuse the helper pattern in this file
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    tools = build_tools(ctx)
    out = tools["propose_meal"].execute({
        "payer": a, "participants": [a, b, c], "total": 400_000, "guests": ["Emi"],
        "dish": "phở", "note": "test",
    })
    assert out["ok"] is True
    assert out["type"] == "expense_draft"
    assert out["member_participants"] == [a, b, c]
    assert out["guests"] == ["Emi"]
    assert out["bill_total"] == 400_000
    assert out["per_head_preview"] == 100_000
    assert out["dish"] == "phở"
    # nothing persisted
    with db.session() as s:
        assert s.query(Meal).count() == 0


def test_propose_meal_defaults_payer_to_sender(db):
    from app.tools import ToolContext, build_tools
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    out = build_tools(ctx)["propose_meal"].execute({"participants": [a, b], "total": 200_000})
    assert out["payer_member_id"] == a
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_tools.py -k propose_meal -v`
Expected: FAIL (`KeyError: 'propose_meal'`).

- [ ] **Step 3: Add the `propose_meal` implementation**

In `backend/app/tools.py`, add a schema next to `_RECORD_SCHEMA`:

```python
_PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "payer": {"type": "integer", "description": "member id người trả; bỏ trống = người đang nhắn."},
        "participants": {"type": "array", "items": {"type": "integer"},
                         "description": "member id những người ăn (chia phần)."},
        "total": {"type": "integer", "description": "Tổng hoá đơn, VND nguyên (840k → 840000)."},
        "guests": {"type": "array", "items": {"type": "string"},
                   "description": "Tên khách vãng lai (không phải thành viên, trả tiền mặt)."},
        "adjustments": {"type": "array", "items": {
            "type": "object",
            "properties": {"member": {"type": "integer"}, "amount": {"type": "integer"}},
            "required": ["member", "amount"]}},
        "dish": {"type": "string", "description": "Món ăn (nếu người dùng có nói)."},
        "initiator": {"type": "string", "description": "Ai rủ ăn (nếu có)."},
        "note": {"type": "string", "description": "Ghi chú tự do (vd 'An đổi ý')."},
    },
    "required": ["participants", "total"],
}
```

Inside `build_tools`, add the executor (before the `return {...}`):

```python
    def propose_meal(args, _tool_ctx=None) -> dict:
        args = args or {}
        participants = [int(p) for p in (args.get("participants") or [])]
        total = args.get("total")
        if not isinstance(total, int):
            return _err("Thiếu tổng tiền (total) dạng số nguyên VND.")
        if not participants:
            return _err("Chưa có người tham gia (participants).")
        guests = [str(g) for g in (args.get("guests") or [])]
        adjustments = {}
        for adj in args.get("adjustments") or []:
            try:
                adjustments[int(adj["member"])] = int(adj["amount"])
            except (KeyError, TypeError, ValueError):
                return _err("Điều chỉnh (adjustments) phải có {member, amount} là số.")
        payer = args.get("payer") or ctx.sender_member_id
        if not payer:
            return _err("Không xác định được người trả tiền (payer).")
        try:
            preview = split_with_guests(total, participants, len(guests), adjustments, payer_id=int(payer))
        except MoneyError as exc:
            return _err(str(exc))
        return {
            "ok": True,
            "type": "expense_draft",
            "payer_member_id": int(payer),
            "member_participants": participants,
            "guests": guests,
            "bill_total": total,
            "adjustments": [{"member": m, "amount": a} for m, a in adjustments.items()],
            "dish": args.get("dish"),
            "initiator": args.get("initiator"),
            "note": args.get("note"),
            "per_head_preview": preview["per_head"],
        }
```

Add `split_with_guests` to the `from app.money import ...` line. In the returned dict, **remove** the `record_meal` entry and add:

```python
        "propose_meal": CustomTool(
            execute=propose_meal,
            description="Đề xuất một bữa ăn (KHÔNG ghi sổ) để người dùng xác nhận. CÔNG CỤ CUỐI khi ghi bữa ăn.",
            input_schema=_PROPOSE_SCHEMA,
        ),
```

- [ ] **Step 4: Update the system prompt**

In `backend/app/prompt.py`, replace the "Ghi một bữa ăn" block (lines 25-33) so the model calls `propose_meal` (never writes) and knows guests exist:

```python
        "## Ghi một bữa ăn (vd: '840k cả nhóm trừ An, Bình +50k, có 1 khách')\n"
        "1. `find_members` để xác định người trả + người tham gia (dùng `all_active:true` cho 'cả nhóm').\n"
        "2. `propose_meal` với payer, participants (id), total (tổng hoá đơn), adjustments nếu có,\n"
        "   guests (tên khách vãng lai — không phải thành viên, họ trả tiền mặt), và dish/initiator/note nếu người dùng nói.\n"
        "   - 'trừ An' = An không nằm trong participants.\n"
        "   - 'An trả nhưng không ăn' = An là payer nhưng không nằm trong participants.\n"
        "   - 'Bình +50k' = adjustment {member: <id Bình>, amount: 50000}.\n"
        "   - `propose_meal` CHỈ ĐỀ XUẤT — không ghi sổ. Người dùng xác nhận trên thẻ nháp.\n"
        "3. Nếu công cụ trả về `error`, hãy hỏi lại cho rõ thay vì đoán.\n"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_tools.py -v`
Expected: PASS. (If existing tests referenced the removed `record_meal` tool, update them to `propose_meal` + assert no write.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/tools.py backend/app/prompt.py backend/tests/test_tools.py
git commit -m "feat(tools): propose_meal replaces record_meal (agent proposes, never writes)"
```

---

### Task 4: `drafts.py` — persist, commit, supersede, cancel

**Files:**
- Create: `backend/app/drafts.py`
- Test: `backend/tests/test_drafts.py` (create)

**Interfaces:**
- Consumes: `chat.post_message`, `chat.message_to_dict`, `ledger.record_meal`, `ledger.period_balances`, `ledger.last_settlement`, `roster.list_members`, `clock.today_ict`.
- Produces:
  - `create_draft(session, room_id, payload: dict) -> RoomMessage` — flushes any pending draft (commit) first, then writes a `kind="expense_draft"` message with `attachments={"type":"expense_draft","status":"pending", ...payload}`.
  - `get_pending_draft(session, room_id) -> RoomMessage | None`
  - `update_draft(session, draft_id, room_id, patch: dict) -> RoomMessage` — merges editable fields / sets `status="cancelled"`.
  - `commit_draft(session, draft_id, room_id, logged_by: str | None) -> RoomMessage` — records the meal, appends the bot meal message (with balances), flips draft `status="committed"`, returns the **meal message**.
  - `current_balances(session, room_id) -> list[dict]` — since-last-settlement per-person `{id,name,paid,consumed,balance}`, sorted by balance desc.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_drafts.py
from datetime import date

from app import drafts
from app.models import Meal, RoomMessage
from tests.test_ledger import _seed_room


def _payload(a, b, c, total=400_000, guests=None):
    return {
        "payer_member_id": a, "member_participants": [a, b, c], "guests": guests or [],
        "bill_total": total, "adjustments": [], "dish": None, "initiator": None,
        "note": None, "per_head_preview": total // (3 + len(guests or [])),
        "raw_input": "@bot test",
    }


def test_create_draft_is_pending(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d = drafts.create_draft(s, room_id, _payload(a, b, c))
        assert d.kind == "expense_draft"
        assert d.attachments["status"] == "pending"


def test_commit_draft_writes_meal_and_balances(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d = drafts.create_draft(s, room_id, _payload(a, b, c))
        meal_msg = drafts.commit_draft(s, d.id, room_id, logged_by=str(a))
        assert meal_msg.kind == "bot"
        att = meal_msg.attachments
        assert att["type"] == "meal"
        assert att["bill_total"] == 400_000
        assert any(row["balance"] == 300_000 for row in att["balances"])  # payer owed
        assert s.query(Meal).count() == 1
        assert s.get(RoomMessage, d.id).attachments["status"] == "committed"


def test_second_draft_supersedes_first(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d1 = drafts.create_draft(s, room_id, _payload(a, b, c))
        d2 = drafts.create_draft(s, room_id, _payload(b, a, c))
        assert s.get(RoomMessage, d1.id).attachments["status"] == "committed"
        assert d2.attachments["status"] == "pending"
        assert drafts.get_pending_draft(s, room_id).id == d2.id
        assert s.query(Meal).count() == 1   # only d1 committed so far


def test_cancel_writes_nothing(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d = drafts.create_draft(s, room_id, _payload(a, b, c))
        drafts.update_draft(s, d.id, room_id, {"status": "cancelled"})
        assert s.get(RoomMessage, d.id).attachments["status"] == "cancelled"
        assert s.query(Meal).count() == 0
        assert drafts.get_pending_draft(s, room_id) is None


def test_edit_then_supersede_saves_edits(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d = drafts.create_draft(s, room_id, _payload(a, b, c))
        drafts.update_draft(s, d.id, room_id, {"member_participants": [a, b]})  # drop c
        drafts.create_draft(s, room_id, _payload(b, a, c))                       # supersede
        meal = s.query(Meal).one()
        member_ids = {sh.member_id for sh in meal.shares}
        assert member_ids == {a, b}   # committed the edited set, not the original
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_drafts.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.drafts'`).

- [ ] **Step 3: Implement `drafts.py`**

```python
# backend/app/drafts.py
"""Expense-draft lifecycle: persist, edit, commit, supersede, cancel.

A draft is a ``RoomMessage`` (``kind="expense_draft"``) whose ``attachments``
carry the proposed meal plus a ``status`` (pending|committed|cancelled). At most
one draft is ``pending`` per room: creating a new draft first commits the
existing pending one ("only when superseded"). All ledger writes go through
:func:`app.ledger.record_meal` — the LLM never writes.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import chat, ledger, roster
from app.clock import today_ict
from app.models import RoomMessage

_EDITABLE = {
    "payer_member_id", "member_participants", "guests", "bill_total",
    "adjustments", "dish", "initiator", "note",
}


def get_pending_draft(session: Session, room_id: int) -> RoomMessage | None:
    for m in session.scalars(
        select(RoomMessage)
        .where(RoomMessage.room_id == room_id, RoomMessage.kind == "expense_draft")
        .order_by(RoomMessage.id.desc())
    ):
        if (m.attachments or {}).get("status") == "pending":
            return m
    return None


def create_draft(session: Session, room_id: int, payload: dict) -> RoomMessage:
    """Commit any pending draft (supersede), then persist a new pending draft."""
    prev = get_pending_draft(session, room_id)
    if prev is not None:
        commit_draft(session, prev.id, room_id, logged_by=payload.get("logged_by"))
    att = {"type": "expense_draft", "status": "pending", **payload}
    att.pop("logged_by", None)
    return chat.post_message(session, room_id, None, body="", attachments=att, kind="expense_draft")


def update_draft(session: Session, draft_id: int, room_id: int, patch: dict) -> RoomMessage:
    m = session.get(RoomMessage, draft_id)
    if m is None or m.room_id != room_id or m.kind != "expense_draft":
        raise ledger.LedgerError(f"Không tìm thấy thẻ nháp #{draft_id}.")
    att = dict(m.attachments or {})
    if att.get("status") != "pending":
        raise ledger.LedgerError("Thẻ nháp đã được xử lý.")
    if patch.get("status") == "cancelled":
        att["status"] = "cancelled"
    else:
        for k in _EDITABLE:
            if k in patch:
                att[k] = patch[k]
    m.attachments = att   # reassign so SQLAlchemy marks the JSON dirty
    session.flush()
    return m


def _adjustments_map(att: dict) -> dict[int, int]:
    return {int(a["member"]): int(a["amount"]) for a in att.get("adjustments") or []}


def commit_draft(session: Session, draft_id: int, room_id: int, logged_by: str | None) -> RoomMessage:
    m = session.get(RoomMessage, draft_id)
    if m is None or m.room_id != room_id or m.kind != "expense_draft":
        raise ledger.LedgerError(f"Không tìm thấy thẻ nháp #{draft_id}.")
    att = dict(m.attachments or {})
    if att.get("status") != "pending":
        raise ledger.LedgerError("Thẻ nháp đã được xử lý.")

    res = ledger.record_meal(
        session,
        room_id=room_id,
        payer_member_id=int(att["payer_member_id"]),
        participants=[int(x) for x in att["member_participants"]],
        total_amount=int(att["bill_total"]),
        adjustments=_adjustments_map(att),
        guests=[str(g) for g in att.get("guests") or []],
        dish=att.get("dish"),
        initiator=att.get("initiator"),
        note=att.get("note"),
        raw_input=att.get("raw_input"),
        logged_by=logged_by,
    )
    names = {mem.id: mem.display_name for mem in roster.list_members(session, room_id)}
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
    meal_msg = chat.post_message(session, room_id, None, body, attachments=meal_att, kind="bot")

    att["status"] = "committed"
    att["committed_meal_id"] = res["meal_id"]
    m.attachments = att
    session.flush()
    return meal_msg


def current_balances(session: Session, room_id: int) -> list[dict]:
    last = ledger.last_settlement(session, room_id)
    from_date = last.period_to if last else None
    balances = ledger.period_balances(session, room_id, from_date, today_ict())
    names = {mem.id: mem.display_name for mem in roster.list_members(session, room_id)}
    rows = [{"id": mid, "name": names.get(mid, "?"), **vals} for mid, vals in balances.items()]
    return sorted(rows, key=lambda r: r["balance"], reverse=True)
```

- [ ] **Step 4: Extend `chat._meal_body` for the new fields**

In `backend/app/chat.py`, replace `_meal_body` (lines 100-109) so it reads the committed-meal attachment shape and mentions guests/dish:

```python
def _meal_body(attachments: dict) -> str:
    """Deterministic Vietnamese summary of a committed meal, straight from the
    tool-result dict — never from LLM prose (design D3, money-safety)."""
    payer = attachments.get("payer") or {}
    shares = attachments.get("shares") or []
    shares_str = ", ".join(f"{s['name']} {s['amount']:,}đ" for s in shares)
    bill = attachments.get("bill_total", attachments.get("tracked_total", 0))
    guests = attachments.get("guests") or []
    guest_str = f" (gồm {len(guests)} khách trả tiền mặt)" if guests else ""
    dish = attachments.get("dish")
    dish_str = f" — {dish}" if dish else ""
    return (
        f"Đã ghi #{attachments.get('meal_id')}{dish_str}: {payer.get('name', '?')} trả "
        f"tổng {bill:,}đ{guest_str} • {shares_str}"
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_drafts.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/drafts.py backend/app/chat.py backend/tests/test_drafts.py
git commit -m "feat(drafts): draft persist/commit/supersede/cancel + balances"
```

---

### Task 5: Draft/commit endpoints + `run_bot_turn` proposes drafts + expose `bot_handle`

**Files:**
- Modify: `backend/app/main.py`, `backend/app/chat.py` (`run_bot_turn` posts a draft), `backend/app/config.py` (confirm `bot_handle` exists)
- Test: `backend/tests/test_api.py` (extend)

**Interfaces:**
- Consumes: `drafts.*` (Task 4), `chat.run_bot_turn` (modified), `AuthCtx`.
- Produces HTTP:
  - `PATCH /api/rooms/{room_id}/drafts/{draft_id}` body `{payer_member_id?, member_participants?, guests?, bill_total?, adjustments?, dish?, initiator?, note?, status?}` → `{ok:true}`; publishes an updated `message` for the draft.
  - `POST /api/rooms/{room_id}/drafts/{draft_id}/commit` → `{ok:true, meal_id}`; publishes the meal `message` + draft-updated `message`.
  - `GET /api/rooms/{room_id}` (or members payload) now returns `bot_handle`.
- Produces behavior: `run_bot_turn`, when the turn yields a `propose_meal` result, calls `drafts.create_draft` and returns that draft message instead of a `kind="bot"` text message.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_api.py` (reuse its existing FastAPI `TestClient` + auth-token helpers):

```python
def test_patch_and_commit_draft(client, room_token, session_headers):
    # Arrange: create a pending draft row directly via drafts.create_draft
    from app import drafts
    from app.db import get_db
    room_id = ...  # from room_token fixture
    with get_db().session() as s:
        d = drafts.create_draft(s, room_id, {
            "payer_member_id": 1, "member_participants": [1, 2], "guests": [],
            "bill_total": 200_000, "adjustments": [], "dish": None,
            "initiator": None, "note": None, "per_head_preview": 100_000,
            "raw_input": "seed",
        })
        draft_id = d.id
    # Edit
    r = client.patch(f"/api/rooms/{room_id}/drafts/{draft_id}",
                     json={"dish": "phở"}, headers=session_headers)
    assert r.status_code == 200
    # Commit
    r = client.post(f"/api/rooms/{room_id}/drafts/{draft_id}/commit", headers=session_headers)
    assert r.status_code == 200 and r.json()["ok"] is True


def test_room_info_exposes_bot_handle(client, room_token):
    r = client.get(f"/api/rooms/{room_token}")
    assert "bot_handle" in r.json()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_api.py -k "draft or bot_handle" -v`
Expected: FAIL (404 / missing key).

- [ ] **Step 3: Add the endpoints**

In `backend/app/main.py`, add models + routes:

```python
from app import drafts
from app.models import RoomMessage

class DraftPatchIn(BaseModel):
    payer_member_id: int | None = None
    member_participants: list[int] | None = None
    guests: list[str] | None = None
    bill_total: int | None = None
    adjustments: list[dict] | None = None
    dish: str | None = None
    initiator: str | None = None
    note: str | None = None
    status: str | None = None   # only "cancelled" is accepted


@app.patch("/api/rooms/{room_id}/drafts/{draft_id}")
async def patch_draft(room_id: int, draft_id: int, body: DraftPatchIn,
                      ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    patch = body.model_dump(exclude_unset=True)
    if patch.get("status") not in (None, "cancelled"):
        raise HTTPException(400, "status chỉ nhận 'cancelled'")
    db = get_db()
    with db.session() as s:
        try:
            m = drafts.update_draft(s, draft_id, room_id, patch)
        except Exception as e:  # LedgerError → 404/409
            raise HTTPException(404, str(e))
        payload = chat.message_to_dict(m, None)
    await hub.publish(room_id, {"type": "message", **payload})
    return {"ok": True}


@app.post("/api/rooms/{room_id}/drafts/{draft_id}/commit")
async def commit_draft_route(room_id: int, draft_id: int,
                             ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    db = get_db()
    async with chat._agent_lock:
        with db.session() as s:
            try:
                meal_msg = drafts.commit_draft(s, draft_id, room_id, logged_by=str(ctx.member_id))
            except Exception as e:
                raise HTTPException(409, str(e))
            meal_payload = chat.message_to_dict(meal_msg, None)
            draft_payload = chat.message_to_dict(s.get(RoomMessage, draft_id), None)
            meal_id = meal_msg.attachments["meal_id"]
    await hub.publish(room_id, {"type": "message", **draft_payload})
    await hub.publish(room_id, {"type": "message", **meal_payload})
    return {"ok": True, "meal_id": meal_id}
```

Expose the handle in `room_info` (line 82-88):

```python
        return {"room_id": r.id, "name": r.name, "bot_handle": settings.bot_handle}
```

- [ ] **Step 4: Make `run_bot_turn` post a draft**

In `backend/app/chat.py`, update `run_bot_turn` (lines 112-139): after computing `result`, if the turn produced a `propose_meal` result, create a draft; else keep the existing settlement/text rendering. Replace the body after `result = await run_turn(...)`:

```python
    from app import drafts

    proposal = result.last_result("propose_meal")
    if proposal:
        payload = {k: proposal[k] for k in (
            "payer_member_id", "member_participants", "guests", "bill_total",
            "adjustments", "dish", "initiator", "note", "per_head_preview")}
        payload["raw_input"] = text
        payload["logged_by"] = str(member_id)
        with db.session() as s:
            return drafts.create_draft(s, room_id, payload)

    attachments = render_bot_attachments(result)
    if attachments and attachments.get("type") == "settlement":
        body = _settlement_body(attachments)
    else:
        body = result.final_text or (result.error and f"⚠️ {result.error}") or "(không có phản hồi)"
    with db.session() as s:
        return post_message(s, room_id, None, body, attachments=attachments, kind="bot")
```

Remove the `meal` branch from `render_bot_attachments` (meals now flow only through drafts): delete the `record_meal` lookup lines (75-76) leaving only the `settle_period` branch.

- [ ] **Step 5: Run the tests**

Run: `cd backend && .venv/bin/pytest -v`
Expected: PASS (fix any fixtures in `test_api.py`/`test_chat.py` that assumed the old immediate-`record_meal` path — meals are now drafts).

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/app/chat.py backend/tests/test_api.py
git commit -m "feat(api): draft patch/commit endpoints; bot proposes drafts; expose bot_handle"
```

---

### Task 6: Golden scenario dataset (G1–G12)

**Files:**
- Create: `backend/tests/golden/__init__.py`, `backend/tests/golden/meals.py`, `backend/tests/test_golden_meals.py`

**Interfaces:**
- Consumes: `drafts.create_draft`, `drafts.commit_draft`, `ledger.period_balances`, the `_seed_room` helper.
- Produces: `golden.meals.CASES` — a list of scenario dicts asserted end-to-end.

- [ ] **Step 1: Write the dataset + runner (this IS the test)**

```python
# backend/tests/golden/__init__.py
```

```python
# backend/tests/golden/meals.py
"""Golden scenarios: member indices are 1-based into the seeded room
(An=1, Bình=2, Cường=3, Dung=4). Amounts are integer VND.

Each case: draft payload (member indices) + expected member shares (by index),
expected balances (by index), and expected persisted tracked total.
"""

CASES = [
    {"id": "G1", "desc": "Even split all members",
     "payer": 1, "participants": [1, 2, 3, 4], "total": 400_000, "guests": [],
     "shares": {1: 100_000, 2: 100_000, 3: 100_000, 4: 100_000},
     "balances": {1: 300_000, 2: -100_000, 3: -100_000, 4: -100_000}, "tracked": 400_000},
    {"id": "G2", "desc": "Exclude a member",
     "payer": 2, "participants": [2, 3, 4], "total": 300_000, "guests": [],
     "shares": {2: 100_000, 3: 100_000, 4: 100_000},
     "balances": {2: 200_000, 3: -100_000, 4: -100_000}, "tracked": 300_000},
    {"id": "G3", "desc": "Payer not a participant",
     "payer": 1, "participants": [2, 3], "total": 200_000, "guests": [],
     "shares": {2: 100_000, 3: 100_000},
     "balances": {1: 200_000, 2: -100_000, 3: -100_000}, "tracked": 200_000},
    {"id": "G4", "desc": "Adjustment +50k",
     "payer": 1, "participants": [1, 2], "total": 250_000, "guests": [],
     "adjustments": [{"member": 2, "amount": 50_000}],
     "shares": {1: 100_000, 2: 150_000},
     "balances": {1: 150_000, 2: -150_000}, "tracked": 250_000},
    {"id": "G5", "desc": "Remainder to payer",
     "payer": 1, "participants": [1, 2, 3], "total": 100_000, "guests": [],
     "shares": {1: 33_334, 2: 33_333, 3: 33_333},
     "balances": {1: 66_666, 2: -33_333, 3: -33_333}, "tracked": 100_000},
    {"id": "G6", "desc": "One guest pays cash",
     "payer": 1, "participants": [1, 2, 3], "total": 400_000, "guests": ["Emi"],
     "shares": {1: 100_000, 2: 100_000, 3: 100_000},
     "balances": {1: 200_000, 2: -100_000, 3: -100_000}, "tracked": 300_000},
    {"id": "G7", "desc": "Two guests pay cash",
     "payer": 2, "participants": [2, 3], "total": 400_000, "guests": ["X", "Y"],
     "shares": {2: 100_000, 3: 100_000},
     "balances": {2: 100_000, 3: -100_000}, "tracked": 200_000},
    {"id": "G8", "desc": "Guest + remainder stays on payer",
     "payer": 1, "participants": [1, 2], "total": 100_000, "guests": ["Z"],
     "shares": {1: 33_334, 2: 33_333},
     "balances": {1: 33_333, 2: -33_333}, "tracked": 66_667},
    {"id": "G12", "desc": "Metadata round-trip",
     "payer": 1, "participants": [1, 2], "total": 200_000, "guests": [],
     "dish": "phở", "initiator": "Emi", "note": "An đổi ý",
     "shares": {1: 100_000, 2: 100_000},
     "balances": {1: 100_000, 2: -100_000}, "tracked": 200_000,
     "expect_meta": {"dish": "phở", "initiator": "Emi", "note": "An đổi ý"}},
]
```

```python
# backend/tests/test_golden_meals.py
from datetime import date

import pytest

from app import drafts, ledger
from app.models import Meal, RoomMessage
from tests.golden.meals import CASES
from tests.test_ledger import _seed_room


def _payload(case, ids):
    idx = {i + 1: ids[i] for i in range(len(ids))}
    return {
        "payer_member_id": idx[case["payer"]],
        "member_participants": [idx[p] for p in case["participants"]],
        "guests": case.get("guests", []),
        "bill_total": case["total"],
        "adjustments": [{"member": idx[a["member"]], "amount": a["amount"]}
                        for a in case.get("adjustments", [])],
        "dish": case.get("dish"), "initiator": case.get("initiator"),
        "note": case.get("note"), "per_head_preview": 0, "raw_input": "golden",
    }


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_meal(db, case):
    room_id, ids = _seed_room(db, 4)
    idx = {i + 1: ids[i] for i in range(4)}
    with db.session() as s:
        d = drafts.create_draft(s, room_id, _payload(case, ids))
        meal_msg = drafts.commit_draft(s, d.id, room_id, logged_by=str(idx[1]))
        meal = s.get(Meal, meal_msg.attachments["meal_id"])
        # shares
        got_shares = {sh.member_id: sh.share_amount for sh in meal.shares}
        want_shares = {idx[k]: v for k, v in case["shares"].items()}
        assert got_shares == want_shares, case["id"]
        # tracked total persisted
        assert meal.total_amount == case["tracked"], case["id"]
        assert sum(got_shares.values()) == case["tracked"], case["id"]
        # guests never in the ledger
        assert set(got_shares) == set(want_shares)
        # balances
        bal = ledger.period_balances(s, room_id, None, date(2999, 1, 1))
        for member_idx, want in case["balances"].items():
            assert bal[idx[member_idx]]["balance"] == want, f'{case["id"]} m{member_idx}'
        # metadata
        for k, v in case.get("expect_meta", {}).items():
            assert getattr(meal, k) == v, f'{case["id"]} {k}'


def test_golden_G9_supersede_autocommit(db):
    room_id, ids = _seed_room(db, 4)
    idx = {i + 1: ids[i] for i in range(4)}
    with db.session() as s:
        d1 = drafts.create_draft(s, room_id, _payload(CASES[0], ids))  # G1
        d2 = drafts.create_draft(s, room_id, _payload(CASES[1], ids))  # G2 supersedes
        assert s.get(RoomMessage, d1.id).attachments["status"] == "committed"
        assert s.get(RoomMessage, d2.id).attachments["status"] == "pending"
        assert s.query(Meal).count() == 1


def test_golden_G10_cancel_writes_nothing(db):
    room_id, ids = _seed_room(db, 4)
    with db.session() as s:
        d = drafts.create_draft(s, room_id, _payload(CASES[0], ids))
        drafts.update_draft(s, d.id, room_id, {"status": "cancelled"})
        assert s.query(Meal).count() == 0


def test_golden_G11_edit_then_supersede(db):
    room_id, ids = _seed_room(db, 4)
    idx = {i + 1: ids[i] for i in range(4)}
    with db.session() as s:
        d = drafts.create_draft(s, room_id, _payload(CASES[0], ids))         # [1,2,3,4]
        drafts.update_draft(s, d.id, room_id, {"member_participants": [idx[1], idx[2]]})
        drafts.create_draft(s, room_id, _payload(CASES[1], ids))              # supersede
        meal = s.query(Meal).one()
        assert {sh.member_id for sh in meal.shares} == {idx[1], idx[2]}
```

- [ ] **Step 2: Run to verify (fails only if earlier tasks regressed)**

Run: `cd backend && .venv/bin/pytest tests/test_golden_meals.py -v`
Expected: PASS (11 parametrized + 3 lifecycle). If a G-case fails, the bug is in Task 1/2/4 math — fix there, not in the dataset.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/golden backend/tests/test_golden_meals.py
git commit -m "test: golden meal scenario dataset (G1-G12) end-to-end"
```

---

## Phase 3 — Live agent timeline (streaming)

### Task 7: `agui.py` translator

**Files:**
- Create: `backend/app/agui.py`, `backend/tests/test_agui.py`

**Interfaces:**
- Consumes: Cursor `run.messages()` message objects (duck-typed: `.type`, `.message.content`, `.name`, `.args`, `.status`, `.result`, `.call_id`).
- Produces: `translate(msg, turn_id) -> list[dict]` returning zero or more `agent.*` events; `start(turn_id)` / `finish(turn_id, error=None)` helpers. Event dicts match §4.1 of the spec.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_agui.py
from types import SimpleNamespace

from app import agui


def _assistant(text):
    return SimpleNamespace(type="assistant",
                           message=SimpleNamespace(content=[SimpleNamespace(type="text", text=text)]))


def _tool(call_id, name, status, args=None, result=None):
    return SimpleNamespace(type="tool_call", call_id=call_id, name=name,
                           status=status, args=args, result=result)


def test_start_and_finish():
    assert agui.start("t1")[0]["type"] == "agent.run.started"
    assert agui.finish("t1")[0]["type"] == "agent.run.finished"
    assert agui.finish("t1", error="boom")[0]["type"] == "agent.run.error"


def test_assistant_text_delta():
    evs = agui.translate(_assistant("xin chào"), "t1")
    assert evs == [{"type": "agent.text.delta", "turn_id": "t1", "delta": "xin chào"}]


def test_tool_start_then_result():
    start = agui.translate(_tool("c1", "propose_meal", "running", args={"total": 100}), "t1")
    assert start[0]["type"] == "agent.tool.start"
    assert start[0]["name"] == "propose_meal"
    done = agui.translate(_tool("c1", "propose_meal", "completed", result={"ok": True}), "t1")
    assert done[0]["type"] == "agent.tool.result"
    assert done[0]["call_id"] == "c1"


def test_mcp_unwrap_names_the_real_tool():
    ev = agui.translate(_tool("c2", "mcp", "running",
                              args={"toolName": "find_members", "args": {"names": ["An"]}}), "t1")
    assert ev[0]["name"] == "find_members"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_agui.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.agui'`).

- [ ] **Step 3: Implement `agui.py`**

```python
# backend/app/agui.py
"""Cursor run-message → chiatienan ``agent.*`` SSE event translator.

Live-only progress: these events are published to the RoomHub during a turn and
never persisted. The authoritative bot message is still posted separately from
the money-safe TurnResult. Adapted from the Atlas reference cursor_agui.py,
reduced to the plain dict events this PWA's SSE stream carries.
"""
from __future__ import annotations

import json


def _unwrap_name(name, args) -> str:
    if name == "mcp" and isinstance(args, dict) and args.get("toolName"):
        return str(args["toolName"])
    return name or "tool"


def _unwrap_args(args):
    if isinstance(args, dict) and "args" in args and args.get("toolName"):
        return args["args"]
    return args


def _assistant_text(msg) -> str:
    message = getattr(msg, "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    out = []
    for block in content or []:
        if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str):
            out.append(block.text)
    return "".join(out)


def start(turn_id: str) -> list[dict]:
    return [{"type": "agent.run.started", "turn_id": turn_id}]


def finish(turn_id: str, error: str | None = None) -> list[dict]:
    if error:
        return [{"type": "agent.run.error", "turn_id": turn_id, "message": error}]
    return [{"type": "agent.run.finished", "turn_id": turn_id}]


def translate(msg, turn_id: str) -> list[dict]:
    mtype = getattr(msg, "type", None)
    if mtype == "assistant":
        text = _assistant_text(msg)
        return [{"type": "agent.text.delta", "turn_id": turn_id, "delta": text}] if text else []
    if mtype == "tool_call":
        status = (getattr(msg, "status", "") or "").lower()
        call_id = getattr(msg, "call_id", None) or ""
        raw_args = getattr(msg, "args", None)
        name = _unwrap_name(getattr(msg, "name", None), raw_args)
        if status in ("completed", "error"):
            result = getattr(msg, "result", None)
            return [{"type": "agent.tool.result", "turn_id": turn_id, "call_id": call_id,
                     "name": name, "status": status,
                     "result": json.loads(json.dumps(result, default=str)) if result is not None else None}]
        return [{"type": "agent.tool.start", "turn_id": turn_id, "call_id": call_id,
                 "name": name, "args": json.loads(json.dumps(_unwrap_args(raw_args), default=str))}]
    return []
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_agui.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/agui.py backend/tests/test_agui.py
git commit -m "feat(agui): Cursor-message to agent.* SSE event translator"
```

---

### Task 8: Stream `agent.*` during the turn

**Files:**
- Modify: `backend/app/agent.py` (`run_turn` gains `emit`), `backend/app/chat.py` (`run_bot_turn` gains `emit`), `backend/app/main.py` (wire hub publish + `turn_id`)

**Interfaces:**
- Consumes: `agui.start/translate/finish` (Task 7).
- Produces: `run_turn(user_text, ctx, images=None, emit=None)` where `emit: Callable[[dict], Awaitable[None]] | None`; when set, `run_turn` awaits `emit(event)` for each `agui.translate(msg, turn_id)` event. `run_bot_turn(..., emit=None)` forwards it. No behavior change when `emit is None`.

- [ ] **Step 1: Write the failing test**

```python
# add to backend/tests/test_agent.py (or a new test_stream.py)
import asyncio
from types import SimpleNamespace

import pytest

from app import agui


@pytest.mark.asyncio
async def test_emit_receives_events_for_messages():
    # Exercise the same loop shape run_turn uses: translate + await emit.
    seen = []
    async def emit(ev): seen.append(ev)
    msgs = [
        SimpleNamespace(type="assistant",
                        message=SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])),
        SimpleNamespace(type="tool_call", call_id="c1", name="propose_meal",
                        status="completed", args={"total": 1}, result={"ok": True}),
    ]
    turn_id = "t1"
    for ev in agui.start(turn_id):
        await emit(ev)
    for m in msgs:
        for ev in agui.translate(m, turn_id):
            await emit(ev)
    for ev in agui.finish(turn_id):
        await emit(ev)
    kinds = [e["type"] for e in seen]
    assert kinds[0] == "agent.run.started" and kinds[-1] == "agent.run.finished"
    assert "agent.text.delta" in kinds and "agent.tool.result" in kinds
```

> This locks the contract the wiring must honor. (`pytest-asyncio` is already used in the repo — confirm via `test_realtime.py`; if not, mark with `asyncio.run`.)

- [ ] **Step 2: Run to verify it passes trivially, then wire the real emit**

Run: `cd backend && .venv/bin/pytest tests/test_agent.py -k emit -v`
Expected: PASS (it validates the event contract). Now add the wiring:

- [ ] **Step 3: Thread `emit` through `run_turn`**

In `backend/app/agent.py`, add `emit=None` to `run_turn`'s signature, import agui, generate a `turn_id`, and emit around the loop. Inside `run_turn`, before the `try:`:

```python
    import uuid
    from app import agui
    turn_id = uuid.uuid4().hex
    if emit:
        for ev in agui.start(turn_id):
            await emit(ev)
```

Inside the `async for msg in run.messages():` loop, at the top of the loop body:

```python
                    if emit:
                        for ev in agui.translate(msg, turn_id):
                            await emit(ev)
```

In the `finally`-equivalent tail (after the `except`, before `return result`):

```python
    if emit:
        for ev in agui.finish(turn_id, error=result.error):
            await emit(ev)
    result.turn_id = turn_id
```

Add `turn_id: str | None = None` to the `TurnResult` dataclass.

- [ ] **Step 4: Forward `emit` and publish from the route**

In `backend/app/chat.py`, `run_bot_turn` gains `emit=None` and passes it: `result = await run_turn(text, ctx, images=images, emit=emit)`.

In `backend/app/main.py` `_run()`, build an emit that publishes to the hub and pass it:

```python
        async def _run():
            async def emit(ev):
                await hub.publish(room_id, ev)
            try:
                bot_msg = await chat.run_bot_turn(
                    db, room_id, ctx.member_id, ctx.display_name, body.body,
                    images=clean, emit=emit,
                )
                await hub.publish(room_id, {"type": "message", **chat.message_to_dict(bot_msg, None)})
            ...
```

Keep the existing `bot.typing`/`bot.done` publishes (belt-and-braces for the collapsed indicator).

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && .venv/bin/pytest -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agent.py backend/app/chat.py backend/app/main.py backend/tests/test_agent.py
git commit -m "feat(stream): publish agent.* timeline events during a bot turn"
```

---

## Phase 4 — Frontend

### Task 9: `mergeEvent` timeline slice + `<AgentTimeline>`

**Files:**
- Modify: `frontend/src/hooks/use-room.ts`, `frontend/src/components/chat/room-view.tsx`, `frontend/src/types/chat.ts`
- Create: `frontend/src/components/chat/agent-timeline.tsx`, `frontend/src/hooks/__tests__/timeline.test.ts`

**Interfaces:**
- Consumes: `agent.*` SSE events (Task 8).
- Produces: `RoomState` gains `timelines: Record<string, TimelineStep[]>`; `mergeEvent` handles `agent.run.started|text.delta|tool.start|tool.result|run.finished|run.error`. `TimelineStep = { kind: "text"|"tool", name?: string, status?: string, text?: string }`.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/hooks/__tests__/timeline.test.ts
import { describe, expect, it } from "vitest";
import { mergeEvent, type RoomState } from "../use-room";

const empty: RoomState = { messages: [], typing: false, timelines: {} };

describe("mergeEvent agent timeline", () => {
  it("opens a timeline on run.started", () => {
    const s = mergeEvent(empty, { type: "agent.run.started", turn_id: "t1" });
    expect(s.timelines["t1"]).toEqual([]);
  });
  it("appends tool steps", () => {
    let s = mergeEvent(empty, { type: "agent.run.started", turn_id: "t1" });
    s = mergeEvent(s, { type: "agent.tool.start", turn_id: "t1", call_id: "c1", name: "propose_meal", args: {} });
    s = mergeEvent(s, { type: "agent.tool.result", turn_id: "t1", call_id: "c1", name: "propose_meal", status: "completed", result: {} });
    expect(s.timelines["t1"].filter((x) => x.kind === "tool").length).toBe(1);
    expect(s.timelines["t1"][0].status).toBe("completed");
  });
  it("marks finished", () => {
    let s = mergeEvent(empty, { type: "agent.run.started", turn_id: "t1" });
    s = mergeEvent(s, { type: "agent.run.finished", turn_id: "t1" });
    expect(s.timelines["t1"]).toBeDefined();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- timeline`
Expected: FAIL (`timelines` undefined / property missing on `RoomState`).

- [ ] **Step 3: Extend `RoomState` + `mergeEvent`**

In `frontend/src/hooks/use-room.ts`, change the type and reducer:

```typescript
export type TimelineStep = { kind: "text" | "tool"; name?: string; status?: string; text?: string };
export type RoomState = { messages: any[]; typing: boolean; timelines: Record<string, TimelineStep[]> };

export function mergeEvent(s: RoomState, e: any): RoomState {
  if (e.type === "bot.typing") return { ...s, typing: true };
  if (e.type === "bot.done") return { ...s, typing: false };
  if (e.type === "agent.run.started") {
    return { ...s, timelines: { ...s.timelines, [e.turn_id]: [] } };
  }
  if (e.type === "agent.text.delta") {
    const prev = s.timelines[e.turn_id] ?? [];
    const last = prev[prev.length - 1];
    const steps = last?.kind === "text"
      ? [...prev.slice(0, -1), { ...last, text: (last.text ?? "") + e.delta }]
      : [...prev, { kind: "text" as const, text: e.delta }];
    return { ...s, timelines: { ...s.timelines, [e.turn_id]: steps } };
  }
  if (e.type === "agent.tool.start") {
    const prev = s.timelines[e.turn_id] ?? [];
    return { ...s, timelines: { ...s.timelines, [e.turn_id]: [...prev, { kind: "tool", name: e.name, status: "running" }] } };
  }
  if (e.type === "agent.tool.result") {
    const prev = s.timelines[e.turn_id] ?? [];
    const i = [...prev].reverse().findIndex((x) => x.kind === "tool" && x.name === e.name && x.status === "running");
    if (i === -1) return { ...s, timelines: { ...s.timelines, [e.turn_id]: [...prev, { kind: "tool", name: e.name, status: e.status }] } };
    const idx = prev.length - 1 - i;
    const steps = prev.map((x, j) => (j === idx ? { ...x, status: e.status } : x));
    return { ...s, timelines: { ...s.timelines, [e.turn_id]: steps } };
  }
  if (e.type === "agent.run.finished" || e.type === "agent.run.error") return s; // timeline stays; collapses in UI
  if (e.type === "message") {
    if (s.messages.some((m) => m.id === e.id)) return s;
    const { type, ...msg } = e;
    return { ...s, messages: [...s.messages, msg] };
  }
  return s;
}
```

Update the two `setState({ messages, typing })` initializers in `useRoom` to include `timelines: {}`, and return `timelines` from the hook: `return { messages: state.messages, typing: state.typing, timelines: state.timelines, send };`.

- [ ] **Step 4: Create `<AgentTimeline>` and render it**

```tsx
// frontend/src/components/chat/agent-timeline.tsx
"use client";
import { useState } from "react";
import type { TimelineStep } from "@/hooks/use-room";

const LABELS: Record<string, string> = {
  find_members: "Đang tra thành viên…",
  propose_meal: "Đang soạn bữa ăn…",
  settle_period: "Đang tính chuyển khoản…",
  get_period_balances: "Đang tính số dư…",
  resolve_period: "Đang xác định kỳ…",
};

export function AgentTimeline({ steps, live }: { steps: TimelineStep[]; live: boolean }) {
  const [open, setOpen] = useState(true);
  if (steps.length === 0 && !live) return null;
  const collapsed = !live && !open;
  return (
    <div className="mt-2 rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-xs text-[var(--text-secondary)]">
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 text-left">
        <span className="font-medium text-[var(--accent-primary)]">
          {live ? "Bot đang xử lý…" : `▸ ${steps.length} bước`}
        </span>
      </button>
      {!collapsed && (
        <ul className="mt-1 space-y-1">
          {steps.map((s, i) => (
            <li key={i} className="flex items-center gap-2">
              {s.kind === "tool" ? (
                <>
                  <span aria-hidden>{s.status === "running" ? "⏳" : s.status === "error" ? "⚠️" : "✓"}</span>
                  <span>{s.name ? LABELS[s.name] ?? s.name : "công cụ"}</span>
                </>
              ) : (
                <span className="italic">{s.text}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

In `frontend/src/components/chat/room-view.tsx`: pull `timelines` from `useRoom`, and render active timelines (those whose `turn_id` has no matching committed message yet) above the typing indicator:

```tsx
  const { messages, typing, timelines, send } = useRoom(roomId);
  ...
  {Object.entries(timelines).map(([tid, steps]) => (
    <AgentTimeline key={tid} steps={steps} live={typing} />
  ))}
```

(Import `AgentTimeline`.)

- [ ] **Step 5: Run tests + typecheck**

Run: `cd frontend && npm test -- timeline && npx tsc --noEmit`
Expected: PASS, no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/hooks/use-room.ts frontend/src/components/chat/agent-timeline.tsx frontend/src/components/chat/room-view.tsx frontend/src/hooks/__tests__/timeline.test.ts
git commit -m "feat(fe): live agent timeline from agent.* events"
```

---

### Task 10: Optimistic echo of the user's own message

**Files:**
- Modify: `frontend/src/hooks/use-room.ts`
- Test: `frontend/src/hooks/__tests__/merge.test.ts` (extend)

**Interfaces:**
- Produces: `useRoom().send` appends a pending bubble `{ id: <temp neg>, kind: "text", body, author: {...}, pending: true }` immediately; `mergeEvent` drops a pending bubble when the matching real `message` (same body, author id) arrives.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/hooks/__tests__/merge.test.ts`:

```typescript
it("reconciles an optimistic pending bubble with the real message", () => {
  const s0: RoomState = {
    messages: [{ id: -1, kind: "text", body: "hi", author: { id: 7 }, pending: true }],
    typing: false, timelines: {},
  };
  const s1 = mergeEvent(s0, { type: "message", id: 42, kind: "text", body: "hi", author: { id: 7 } });
  expect(s1.messages.filter((m) => m.pending).length).toBe(0);
  expect(s1.messages.some((m) => m.id === 42)).toBe(true);
  expect(s1.messages.length).toBe(1);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- merge`
Expected: FAIL (duplicate message; pending not dropped).

- [ ] **Step 3: Reconcile in `mergeEvent` + optimistic append in `send`**

In `mergeEvent`, change the `message` branch to drop a matching pending bubble first:

```typescript
  if (e.type === "message") {
    if (s.messages.some((m) => m.id === e.id)) return s;
    const { type, ...msg } = e;
    const withoutPending = s.messages.filter(
      (m) => !(m.pending && m.body === e.body && m.author?.id === e.author?.id),
    );
    return { ...s, messages: [...withoutPending, msg] };
  }
```

Replace `send` in `useRoom`:

```typescript
  const send = (text: string, images?: ChatImage[]) => {
    const tempId = -Date.now();
    setState((prev) => ({
      ...prev,
      messages: [...prev.messages, {
        id: tempId, kind: "text", body: text,
        author: { id: memberId }, pending: true,
      }],
    }));
    return api.postMessage(roomId, text, images).catch((err) => {
      setState((prev) => ({
        ...prev,
        messages: prev.messages.map((m) => (m.id === tempId ? { ...m, error: true } : m)),
      }));
      throw err;
    });
  };
```

Add `const { memberId } = useSession();` — extend `session.tsx` to expose the logged-in member id if not already present (read from `/api/me` on load). If `memberId` isn't available, match pending only by `body` in `mergeEvent` and author-render as "you".

`message-list.tsx` `HumanMessage`: dim pending, mark errored:

```tsx
      <div className={`... ${message.pending ? "opacity-60" : ""} ${message.error ? "border-red-400" : ""}`}>
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && npm test -- merge && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/use-room.ts frontend/src/hooks/__tests__/merge.test.ts frontend/src/components/chat/message-list.tsx frontend/src/lib/session.tsx
git commit -m "feat(fe): optimistic echo of the user's own message"
```

---

### Task 11: `@bot` composer dropdown

**Files:**
- Create: `frontend/src/components/chat/mention-dropdown.tsx`
- Modify: `frontend/src/components/chat/composer.tsx`, `frontend/src/lib/api.ts`
- Test: covered by a small `mention-dropdown` unit test + manual `/run`.

**Interfaces:**
- Consumes: `roomInfo().bot_handle` (Task 5).
- Produces: composer detects a trailing `@<partial>` at the caret and shows `<MentionDropdown items={["bot", ...handles]}>`; ↑/↓/Enter/Tab/Esc; accept replaces the partial with `@bot `.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/chat/__tests__/mention.test.tsx
import { describe, expect, it } from "vitest";
import { mentionQuery } from "../mention-dropdown";

describe("mentionQuery", () => {
  it("detects an @ at the caret", () => {
    expect(mentionQuery("hello @bo", 9)).toBe("bo");
    expect(mentionQuery("hello @", 7)).toBe("");
    expect(mentionQuery("hello world", 11)).toBeNull();
    expect(mentionQuery("a@b.com", 7)).toBeNull(); // email, not a mention
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- mention`
Expected: FAIL (`mentionQuery` not exported).

- [ ] **Step 3: Implement dropdown + helper**

```tsx
// frontend/src/components/chat/mention-dropdown.tsx
"use client";

/** Returns the partial handle being typed at `caret` (text after a boundary "@"),
 * or null if the caret is not in an @-mention. */
export function mentionQuery(text: string, caret: number): string | null {
  const upto = text.slice(0, caret);
  const m = upto.match(/(?:^|[\s(])@([\w-]*)$/);
  return m ? m[1] : null;
}

export function MentionDropdown({
  items, active, onPick,
}: { items: string[]; active: number; onPick: (h: string) => void }) {
  if (items.length === 0) return null;
  return (
    <ul className="absolute bottom-full left-0 mb-1 w-48 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] shadow-lg">
      {items.map((h, i) => (
        <li key={h}>
          <button
            type="button"
            onMouseDown={(e) => { e.preventDefault(); onPick(h); }}
            className={`block w-full px-3 py-2 text-left text-sm ${i === active ? "bg-[var(--bg-base)] text-[var(--accent-primary)]" : "text-[var(--text-primary)]"}`}
          >
            @{h}
          </button>
        </li>
      ))}
    </ul>
  );
}
```

In `composer.tsx`: track caret, compute `mentionQuery`, filter `["bot", ...extraHandles]`, render `<MentionDropdown>` inside a `relative` wrapper around the textarea; handle ↑/↓/Enter/Tab/Esc in `onKeyDown` (when the dropdown is open, Enter accepts the mention instead of sending). Accept replaces the `@<partial>` with `@bot ` at the caret. Fetch the handle once via `api.roomInfo` (or a new `api.botHandle(roomId)`); default to `["bot"]`.

Add to `api.ts` (if room-info isn't already reachable post-auth): reuse `roomInfo` — but it takes an invite token. Add a session-scoped fetch:

```typescript
export const botHandle = async (): Promise<string> => {
  // bot_handle is stable; hardcode fallback avoids an extra round-trip.
  return "bot";
};
```

> The `@bot`-only scope means the list is effectively `["bot"]`; the dropdown still gives the tap-to-insert affordance the user asked for. Skip the network call unless multiple handles are configured.

- [ ] **Step 4: Run tests + manual check**

Run: `cd frontend && npm test -- mention && npx tsc --noEmit`
Expected: PASS. Then `/run` the app, type `@`, confirm the dropdown appears and inserts `@bot `.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/chat/mention-dropdown.tsx frontend/src/components/chat/composer.tsx frontend/src/lib/api.ts frontend/src/components/chat/__tests__/mention.test.tsx
git commit -m "feat(fe): @bot mention dropdown in the composer"
```

---

### Task 12: `<ExpenseDraftCard>` + draft API

**Files:**
- Create: `frontend/src/components/chat/expense-draft-card.tsx`
- Modify: `frontend/src/components/chat/message-list.tsx`, `frontend/src/lib/api.ts`, `frontend/src/components/chat/room-view.tsx`, `frontend/src/types/chat.ts`

**Interfaces:**
- Consumes: draft `message` (`kind:"expense_draft"`, `attachments.status`), room `members`.
- Produces: `api.patchDraft(roomId, draftId, patch)`, `api.commitDraft(roomId, draftId)`, `api.cancelDraft(roomId, draftId)`. `<ExpenseDraftCard message members roomId />` renders payer selector, member chips, guest add, amount, adjustments (advanced), story fields, provisional per-head, and Ghi ngay / Huỷ. Edits debounce-PATCH; `status !== "pending"` renders read-only.

- [ ] **Step 1: Add the API calls**

```typescript
// frontend/src/lib/api.ts
export const patchDraft = (roomId: number, draftId: number, patch: any) =>
  req(`/api/rooms/${roomId}/drafts/${draftId}`, { method: "PATCH", body: JSON.stringify(patch) });

export const commitDraft = (roomId: number, draftId: number) =>
  req(`/api/rooms/${roomId}/drafts/${draftId}/commit`, { method: "POST" });

export const cancelDraft = (roomId: number, draftId: number) =>
  req(`/api/rooms/${roomId}/drafts/${draftId}`, { method: "PATCH", body: JSON.stringify({ status: "cancelled" }) });
```

- [ ] **Step 2: Add types**

```typescript
// frontend/src/types/chat.ts (append)
export interface ExpenseDraft {
  type: "expense_draft";
  status: "pending" | "committed" | "cancelled";
  payer_member_id: number;
  member_participants: number[];
  guests: string[];
  bill_total: number;
  adjustments: { member: number; amount: number }[];
  dish: string | null;
  initiator: string | null;
  note: string | null;
  per_head_preview: number;
}
```

- [ ] **Step 3: Build `<ExpenseDraftCard>`**

```tsx
// frontend/src/components/chat/expense-draft-card.tsx
"use client";
import { useEffect, useRef, useState } from "react";
import * as api from "@/lib/api";

interface Member { id: number; display_name: string; nickname?: string | null }
const fmt = (n: number) => new Intl.NumberFormat("vi-VN").format(n);

/** Provisional per-head over (billed members + guests). Display only; the
 * server recomputes authoritatively on commit. */
function perHead(total: number, memberCount: number, guestCount: number): number {
  const heads = memberCount + guestCount;
  return heads > 0 ? Math.floor(total / heads) : 0;
}

export function ExpenseDraftCard({
  message, members, roomId,
}: { message: any; members: Member[]; roomId: number }) {
  const att = message.attachments as any;
  const readonly = att.status !== "pending";
  const [payer, setPayer] = useState<number>(att.payer_member_id);
  const [billed, setBilled] = useState<number[]>(att.member_participants ?? []);
  const [guests, setGuests] = useState<string[]>(att.guests ?? []);
  const [total, setTotal] = useState<number>(att.bill_total ?? 0);
  const [dish, setDish] = useState<string>(att.dish ?? "");
  const [initiator, setInitiator] = useState<string>(att.initiator ?? "");
  const [note, setNote] = useState<string>(att.note ?? "");
  const [guestName, setGuestName] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef<any>(null);

  // Debounced PATCH of the editable state so auto-save-on-supersede uses the latest.
  useEffect(() => {
    if (readonly) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      api.patchDraft(roomId, message.id, {
        payer_member_id: payer, member_participants: billed, guests,
        bill_total: total, dish: dish || null, initiator: initiator || null, note: note || null,
      }).catch(() => {});
    }, 600);
    return () => timer.current && clearTimeout(timer.current);
  }, [payer, billed, guests, total, dish, initiator, note, readonly, roomId, message.id]);

  const toggle = (id: number) =>
    setBilled((b) => (b.includes(id) ? b.filter((x) => x !== id) : [...b, id]));
  const addGuest = () => { if (guestName.trim()) { setGuests((g) => [...g, guestName.trim()]); setGuestName(""); } };

  const ph = perHead(total, billed.length, guests.length);

  const statusLabel =
    att.status === "committed" ? "Đã ghi sổ" : att.status === "cancelled" ? "Đã huỷ" : null;

  return (
    <div className="mt-1 w-full max-w-[95%] rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-semibold text-[var(--text-primary)]">Nháp bữa ăn</span>
        {statusLabel && <span className="text-xs text-[var(--text-secondary)]">{statusLabel}</span>}
      </div>

      <label className="block text-xs text-[var(--text-secondary)]">Người trả</label>
      <select disabled={readonly} value={payer} onChange={(e) => setPayer(Number(e.target.value))}
        className="mb-2 w-full rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm">
        {members.map((m) => <option key={m.id} value={m.id}>{m.display_name}</option>)}
      </select>

      <div className="mb-2 flex flex-wrap gap-1.5">
        {members.map((m) => (
          <button key={m.id} type="button" disabled={readonly} onClick={() => toggle(m.id)}
            className={`rounded-full border px-2.5 py-1 text-xs ${billed.includes(m.id)
              ? "border-[var(--accent-primary)] bg-[var(--accent-primary)] text-white"
              : "border-[var(--border)] text-[var(--text-secondary)]"}`}>
            {m.display_name}
          </button>
        ))}
        {guests.map((g, i) => (
          <span key={`g${i}`} className="inline-flex items-center gap-1 rounded-full border border-dashed border-[var(--border)] px-2.5 py-1 text-xs text-[var(--text-secondary)]">
            {g} (khách)
            {!readonly && <button type="button" onClick={() => setGuests((x) => x.filter((_, j) => j !== i))}>×</button>}
          </span>
        ))}
      </div>

      {!readonly && (
        <div className="mb-2 flex gap-2">
          <input value={guestName} onChange={(e) => setGuestName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addGuest())}
            placeholder="Thêm khách…" className="flex-1 rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />
          <button type="button" onClick={addGuest} className="rounded-md border border-[var(--border)] px-2 text-sm">+</button>
        </div>
      )}

      <label className="block text-xs text-[var(--text-secondary)]">Tổng hoá đơn (đ)</label>
      <input type="number" disabled={readonly} value={total} onChange={(e) => setTotal(Number(e.target.value))}
        className="mb-2 w-full rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />

      <div className="mb-2 grid grid-cols-2 gap-2">
        <input disabled={readonly} value={dish} onChange={(e) => setDish(e.target.value)} placeholder="Món ăn"
          className="rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />
        <input disabled={readonly} value={initiator} onChange={(e) => setInitiator(e.target.value)} placeholder="Ai rủ"
          className="rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />
      </div>
      <input disabled={readonly} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Ghi chú (vd 'An đổi ý')"
        className="mb-2 w-full rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />

      <p className="mb-2 text-xs text-[var(--text-secondary)]">
        Tạm tính: <span className="font-medium text-[var(--text-primary)]">{fmt(ph)} đ/người</span>
        {guests.length > 0 && ` • ${guests.length} khách trả tiền mặt`}
      </p>

      {!readonly && (
        <div className="flex gap-2">
          <button type="button" disabled={busy}
            onClick={() => { setBusy(true); api.commitDraft(roomId, message.id).catch(() => setBusy(false)); }}
            className="flex-1 rounded-lg bg-[var(--accent-primary)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
            Ghi ngay
          </button>
          <button type="button" disabled={busy}
            onClick={() => { setBusy(true); api.cancelDraft(roomId, message.id).catch(() => setBusy(false)); }}
            className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text-secondary)]">
            Huỷ
          </button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Wire it into the message list**

`message-list.tsx`: accept `members` + `roomId` props, add an `expense_draft` branch:

```tsx
export function MessageList({ messages, members, roomId }:
  { messages: Message[]; members: any[]; roomId: number }) {
  return (
    <div className="flex flex-col gap-4">
      {messages.map((m) =>
        m.kind === "expense_draft" ? (
          <div key={m.id} className="flex flex-col items-start">
            <span className="mb-1 px-1 text-xs font-medium text-[var(--accent-primary)]">Bot</span>
            <ExpenseDraftCard message={m} members={members} roomId={roomId} />
          </div>
        ) : m.kind === "bot" ? (
          /* ...existing bot branch... */
        ) : (
          <HumanMessage key={m.id} message={m} />
        ),
      )}
    </div>
  );
}
```

(Import `ExpenseDraftCard`.) In `room-view.tsx`, pass `<MessageList messages={messages} members={members} roomId={roomId} />`.

- [ ] **Step 5: Typecheck + manual `/run`**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors. `/run`: send `@bot 400k trưa cả nhóm, 1 khách Emi` → a draft card appears; toggle a chip; **Ghi ngay** → a committed meal message follows.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/chat/expense-draft-card.tsx frontend/src/components/chat/message-list.tsx frontend/src/components/chat/room-view.tsx frontend/src/lib/api.ts frontend/src/types/chat.ts
git commit -m "feat(fe): interactive expense draft card (HITL + guests + metadata)"
```

---

### Task 13: `<BalanceTable>` on the committed meal card

**Files:**
- Create: `frontend/src/components/chat/balance-table.tsx`
- Modify: `frontend/src/components/chat/bot-message.tsx`

**Interfaces:**
- Consumes: committed-meal attachment `balances: [{id,name,paid,consumed,balance}]` + new `bill_total`, `guests`, `dish` fields (Task 4/5).
- Produces: `<BalanceTable rows={...} />`; the meal card renders bill + guests + shares + `<BalanceTable>`.

- [ ] **Step 1: Build `<BalanceTable>`**

```tsx
// frontend/src/components/chat/balance-table.tsx
"use client";
const fmt = (n: number) => new Intl.NumberFormat("vi-VN").format(n);

interface Row { id: number; name: string; paid: number; consumed: number; balance: number }

export function BalanceTable({ rows }: { rows: Row[] }) {
  if (!rows || rows.length === 0) return null;
  return (
    <div className="mt-3">
      <p className="mb-1 text-xs font-medium uppercase tracking-wide text-[var(--text-secondary)]">
        Số dư hiện tại
      </p>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="text-left text-xs text-[var(--text-secondary)]">
            <th className="py-1">Người</th>
            <th className="py-1 text-right">Đã trả</th>
            <th className="py-1 text-right">Đã ăn</th>
            <th className="py-1 text-right">Cân đối</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-[var(--border)]">
              <td className="py-1 text-[var(--text-primary)]">{r.name}</td>
              <td className="py-1 text-right text-[var(--text-secondary)]">{fmt(r.paid)}</td>
              <td className="py-1 text-right text-[var(--text-secondary)]">{fmt(r.consumed)}</td>
              <td className={`py-1 text-right font-medium ${r.balance >= 0 ? "text-[var(--accent-primary)]" : "text-red-500"}`}>
                {r.balance >= 0 ? "+" : ""}{fmt(r.balance)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Extend the meal card**

In `bot-message.tsx`, update `MealCard` to read the new fields and append the table:

```tsx
import { BalanceTable } from "./balance-table";

function MealCard({ attachments }: { attachments: any }) {
  const payer = attachments.payer ?? {};
  const shares: Share[] = attachments.shares ?? [];
  const bill: number = attachments.bill_total ?? attachments.tracked_total ?? 0;
  const guests: string[] = attachments.guests ?? [];
  return (
    <div className="mt-3 space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-2 text-sm text-[var(--text-primary)]">
        <span className="text-[var(--text-secondary)]">Người trả:</span>
        <span className="font-medium">{payer.name ?? "?"}</span>
        <span className="ml-auto font-semibold text-[var(--accent-primary)]">{fmt(bill)} đ</span>
      </div>
      {guests.length > 0 && (
        <p className="text-xs text-[var(--text-secondary)]">gồm {guests.length} khách trả tiền mặt: {guests.join(", ")}</p>
      )}
      {attachments.dish && <p className="text-xs text-[var(--text-secondary)]">Món: {attachments.dish}</p>}
      {shares.length > 0 && (
        <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)] bg-[var(--bg-base)]">
          {shares.map((s, i) => (
            <li key={i} className="flex items-center justify-between px-3 py-2 text-sm">
              <span className="text-[var(--text-primary)]">{s.name}</span>
              <span className="font-medium text-[var(--text-secondary)]">{fmt(s.amount)} đ</span>
            </li>
          ))}
        </ul>
      )}
      <BalanceTable rows={attachments.balances ?? []} />
    </div>
  );
}
```

- [ ] **Step 3: Typecheck + manual `/run`**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors. `/run`: commit a meal → the card shows the bill, guest note, shares, and the balance table.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/chat/balance-table.tsx frontend/src/components/chat/bot-message.tsx
git commit -m "feat(fe): balance table on the committed meal card"
```

---

## Self-Review

**Spec coverage:**
- §4.1 live timeline → Tasks 7, 8, 9. ✅
- §4.2 @bot dropdown + optimistic echo → Tasks 11, 10. ✅
- §4.3 optimistic HITL drafts (propose/edit/commit/supersede/cancel) → Tasks 3, 4, 5, 12. ✅
- §4.4 guest-pays-cash math → Tasks 1, 2 (+ golden G6–G8). ✅
- §4.5 balance table → Tasks 4 (`current_balances`), 13. ✅
- §4.6 data model / endpoints → Tasks 2 (columns), 5 (routes, bot_handle). ✅
- §6.1 unit coverage → every task is test-first. ✅
- §6.2 golden dataset → Task 6. ✅
- Removed `record_meal` from agent tools → Task 3. ✅ Settlement unchanged → untouched. ✅

**Placeholder scan:** The `_run()` snippet in Task 8 Step 4 uses `...` to denote the existing error branch that stays unchanged — the engineer keeps the current `except`/`finally`; not a placeholder for new code. The `message-list.tsx` bot branch comment in Task 12 likewise means "keep the existing branch". All new code is spelled out.

**Type consistency:** Draft attachment keys (`payer_member_id`, `member_participants`, `guests`, `bill_total`, `adjustments`, `dish`, `initiator`, `note`, `per_head_preview`, `status`) are identical across `propose_meal` (Task 3), `drafts.py` (Task 4), the PATCH model (Task 5), and `<ExpenseDraftCard>` (Task 12). Meal attachment keys (`bill_total`, `tracked_total`, `guests`, `payer`, `shares`, `balances`, `dish`) match between `commit_draft` (Task 4) and `<MealCard>`/`<BalanceTable>` (Task 13). `RoomState.timelines` + `TimelineStep` match between `use-room.ts` and `<AgentTimeline>` (Task 9).

**Note for the implementer:** `test_api.py` / `test_chat.py` currently assume the old immediate-`record_meal` path; Task 5 Step 5 calls out updating them to the draft flow (a `@bot` meal now yields a `kind="expense_draft"` message, not an immediate `kind="bot"` meal).
