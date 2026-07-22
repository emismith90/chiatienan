# Payments-as-drafts, Cursor skills, grok-4.5 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make cash payments go through a propose-draft → human-confirm flow with a server-computed pay-off amount, move the bot's procedures into Cursor workspace skills, switch the model to grok-4.5, and wipe corrupted data.

**Architecture:** Mirror the existing meal flow (`propose_meal` → `expense_draft` card → `commit_draft`) for payments (`propose_payment` → `payment_draft` card → `commit_payment_draft`). The pay-off amount is computed server-side from the existing `net_transfers` settlement graph — the model never invents amounts. Bot procedures move from the monolithic system prompt into workspace `.cursor/rules` (always-on) + `.cursor/skills` (on-demand), loaded via `LocalAgentOptions(setting_sources=["project"])`.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy / pytest (backend); Next.js / React / vitest / Tailwind (frontend); Cursor SDK 0.1.9 (grok-4.5).

## Global Constraints

- Money-safety invariant: the model NEVER computes/retypes a monetary amount; every ledger write goes through a tool; balance-changing actions are proposals a human confirms. (Copied from spec.)
- All ledger writes go through `app.ledger` (`record_meal`, `record_payment`, `record_settlement`). Tools/drafts never write rows directly.
- Backend tests: `cd backend && .venv/bin/python -m pytest`. Frontend tests: `cd frontend && npx vitest run`. Typecheck: `cd frontend && npx tsc --noEmit`.
- Vietnamese user-facing bot copy; English UI chrome.
- Model config value for grok high+fast is exactly `grok-4.5-fast` (bare `grok-4.5` resolves to fast=false).

---

### Task 1: `propose_payment` tool + server-computed pay-off (replaces `record_payment`)

**Files:**
- Modify: `backend/app/tools.py` (schema `_PAYMENT_SCHEMA` → `_PROPOSE_PAYMENT_SCHEMA`; replace `record_payment` fn + registration with `propose_payment`)
- Test: `backend/tests/test_tools_payment.py` (create)

**Interfaces:**
- Consumes: `resolve_period`, `today_ict`, `ledger.last_settlement`, `ledger.period_balances`, `net_transfers` (already imported in tools.py), `_names_for`, `_err`, `ctx`.
- Produces: tool `propose_payment` returning one of:
  - `{"ok": True, "type": "payment_draft", "from_member_id": int, "to_member_id": int, "amount": int, "note": str|None, "from_name": str, "to_name": str}`
  - `{"ok": True, "type": "payment_settled", "from": {"id","name"}, "to": {"id","name"}}` (nothing owed → no draft)
  - `{"ok": False, "error": str}`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_tools_payment.py`:

```python
import datetime as dt
import pytest
from app.db import Database
from app.tools import ToolContext, build_tools
from app import accounts, ledger, rooms


@pytest.fixture
def room(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'t.db'}")
    db.create_all()
    with db.session() as s:
        r = rooms.create_room(s, name="T", bot_handle="bot", admin_password_hash="x")
        a = accounts.add_unclaimed(s, r, display_name="Alice", nickname="alice")
        b = accounts.add_unclaimed(s, r, display_name="Bob", nickname="bob")
        s.flush()
        ids = {"room": r.id, "alice": a.id, "bob": b.id}
    return db, ids


def _tools(db, ids):
    ctx = ToolContext(db=db, room_id=ids["room"], sender_member_id=ids["alice"],
                      sender_name="Alice", turn_mentions=[])
    return build_tools(ctx)


def test_propose_payment_explicit_amount(room):
    db, ids = room
    t = _tools(db, ids)["propose_payment"]
    out = t.execute({"from": ids["alice"], "to": ids["bob"], "amount": 50000})
    assert out["ok"] and out["type"] == "payment_draft"
    assert (out["from_member_id"], out["to_member_id"], out["amount"]) == (ids["alice"], ids["bob"], 50000)


def test_propose_payment_payoff_uses_settle_transfer(room):
    db, ids = room
    # Alice owes Bob: Bob paid a 100k meal for both → Alice owes Bob 50k.
    with db.session() as s:
        ledger.record_meal(s, room_id=ids["room"], payer_member_id=ids["bob"],
                           participants=[ids["alice"], ids["bob"]], total_amount=100000,
                           adjustments={}, guests=[], logged_by="test")
    t = _tools(db, ids)["propose_payment"]
    out = t.execute({"from": ids["alice"], "to": ids["bob"]})  # no amount → pay off
    assert out["type"] == "payment_draft" and out["amount"] == 50000


def test_propose_payment_nothing_owed_returns_settled(room):
    db, ids = room
    t = _tools(db, ids)["propose_payment"]
    out = t.execute({"from": ids["alice"], "to": ids["bob"]})  # no meals → owes nothing
    assert out["ok"] and out["type"] == "payment_settled"


def test_propose_payment_rejects_same_member(room):
    db, ids = room
    t = _tools(db, ids)["propose_payment"]
    out = t.execute({"from": ids["alice"], "to": ids["alice"], "amount": 1000})
    assert out["ok"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tools_payment.py -v`
Expected: FAIL — `KeyError: 'propose_payment'` (tool not registered yet). (If `rooms.create_room` / `accounts.add_unclaimed` signatures differ, adjust the fixture to match the existing test helpers in `tests/conftest.py` / `tests/test_api.py`.)

- [ ] **Step 3: Replace the schema**

In `backend/app/tools.py`, replace `_PAYMENT_SCHEMA` (lines ~172–180) with:

```python
_PROPOSE_PAYMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "from": {"type": "integer", "description": "member id who paid; blank = the sender."},
        "to": {"type": "integer", "description": "member id who received the money."},
        "amount": {
            "type": "integer",
            "description": "Integer VND (125k → 125000). OMIT to pay off exactly what `from` currently owes `to`.",
        },
        "note": {"type": "string"},
    },
    "required": ["to"],
}
```

- [ ] **Step 4: Replace the `record_payment` execute fn with `propose_payment`**

In `build_tools`, replace the whole `def record_payment(...)` block (lines ~373–406) with:

```python
    def propose_payment(args, _tool_ctx=None) -> dict:
        args = args or {}
        to = args.get("to")
        frm = args.get("from") or ctx.sender_member_id
        if not frm:
            return _err("Không xác định được người trả.")
        if not to:
            return _err("Thiếu người nhận.")
        try:
            frm_id, to_id = int(frm), int(to)
        except (TypeError, ValueError):
            return _err("from/to không hợp lệ.")
        if frm_id == to_id:
            return _err("Người trả và người nhận phải khác nhau.")
        amount = args.get("amount")
        if amount is not None and not isinstance(amount, int):
            return _err("amount phải là số nguyên VND.")
        with db.session() as s:
            names = _names_for(s, ctx.room_id, [frm_id, to_id])
            if amount is None:
                # Pay-off: amount = the current settle transfer frm -> to over the
                # open (since_last) period. No such transfer => nothing owed.
                last = ledger.last_settlement(s, ctx.room_id)
                period = resolve_period(
                    "since_last", today=today_ict(),
                    last_settlement_to=last.period_to if last else None,
                )
                balances = ledger.period_balances(s, ctx.room_id, period["from"], period["to"])
                transfers = net_transfers({mid: v["balance"] for mid, v in balances.items()})
                match = next(
                    (t for t in transfers if t.from_member == frm_id and t.to_member == to_id),
                    None,
                )
                if match is None:
                    return {
                        "ok": True,
                        "type": "payment_settled",
                        "from": {"id": frm_id, "name": names.get(frm_id, "?")},
                        "to": {"id": to_id, "name": names.get(to_id, "?")},
                    }
                amount = match.amount
            if amount <= 0:
                return _err("Số tiền phải lớn hơn 0.")
        return {
            "ok": True,
            "type": "payment_draft",
            "from_member_id": frm_id,
            "to_member_id": to_id,
            "amount": amount,
            "note": args.get("note"),
            "from_name": names.get(frm_id, "?"),
            "to_name": names.get(to_id, "?"),
        }
```

Then in the returned tools dict (lines ~547–551), replace the `"record_payment"` entry with:

```python
        "propose_payment": CustomTool(
            execute=propose_payment,
            description=(
                "Propose a cash payment one member made to another for the user to confirm "
                "(e.g. 'A trả B 100k', 'A đã trả B'). Does NOT write the ledger. FINAL TOOL for a "
                "payment. Omit `amount` to pay off exactly what `from` owes `to`."
            ),
            input_schema=_PROPOSE_PAYMENT_SCHEMA,
        ),
```

Note: `net_transfers`, `resolve_period`, `today_ict`, `ledger` are already imported at the top of tools.py.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tools_payment.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/tools.py backend/tests/test_tools_payment.py
git commit -m "feat(be): propose_payment tool with server-computed pay-off (replaces record_payment)"
```

---

### Task 2: payment-draft lifecycle in `drafts.py`

**Files:**
- Modify: `backend/app/drafts.py` (add kinds constant; generalize `list_pending_drafts` + `update_draft` guard; add `create_payment_draft`, `commit_payment_draft`, `commit_any`)
- Test: `backend/tests/test_payment_drafts.py` (create)

**Interfaces:**
- Consumes: `chat.post_message`, `chat._payment_body`, `ledger.record_payment`, `current_balances`, `_all_member_names`.
- Produces:
  - `create_payment_draft(session, room_id, payload: dict) -> RoomMessage`
  - `commit_payment_draft(session, draft_id, room_id, logged_by) -> RoomMessage`
  - `commit_any(session, draft_id, room_id, logged_by) -> RoomMessage` (dispatch by kind)
  - `list_pending_drafts` now returns both `expense_draft` and `payment_draft` pending rows.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_payment_drafts.py`:

```python
import pytest
from app.db import Database
from app import accounts, drafts, ledger, rooms
from app.models import Payment


@pytest.fixture
def room(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'t.db'}")
    db.create_all()
    with db.session() as s:
        r = rooms.create_room(s, name="T", bot_handle="bot", admin_password_hash="x")
        a = accounts.add_unclaimed(s, r, display_name="Alice", nickname="alice")
        b = accounts.add_unclaimed(s, r, display_name="Bob", nickname="bob")
        s.flush()
        ids = {"room": r.id, "alice": a.id, "bob": b.id}
    return db, ids


def test_create_and_commit_payment_draft(room):
    db, ids = room
    with db.session() as s:
        d = drafts.create_payment_draft(s, ids["room"], {
            "from_member_id": ids["alice"], "to_member_id": ids["bob"],
            "amount": 50000, "note": None})
        draft_id = d.id
        assert d.kind == "payment_draft"
        assert (d.attachments or {})["status"] == "pending"
    with db.session() as s:
        card = drafts.commit_any(s, draft_id, ids["room"], logged_by="test")
        assert card.kind == "bot"
    with db.session() as s:
        assert s.query(Payment).count() == 1


def test_commit_twice_is_rejected(room):
    db, ids = room
    with db.session() as s:
        d = drafts.create_payment_draft(s, ids["room"], {
            "from_member_id": ids["alice"], "to_member_id": ids["bob"], "amount": 1000, "note": None})
        draft_id = d.id
    with db.session() as s:
        drafts.commit_any(s, draft_id, ids["room"], logged_by="t")
    with db.session() as s:
        with pytest.raises(ledger.LedgerError):
            drafts.commit_any(s, draft_id, ids["room"], logged_by="t")


def test_pending_list_includes_payment_drafts(room):
    db, ids = room
    with db.session() as s:
        drafts.create_payment_draft(s, ids["room"], {
            "from_member_id": ids["alice"], "to_member_id": ids["bob"], "amount": 1000, "note": None})
    with db.session() as s:
        pending = drafts.list_pending_drafts(s, ids["room"])
        assert len(pending) == 1 and pending[0].kind == "payment_draft"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_payment_drafts.py -v`
Expected: FAIL — `AttributeError: module 'app.drafts' has no attribute 'create_payment_draft'`.

- [ ] **Step 3: Add the kinds constant and generalize the guards**

In `backend/app/drafts.py`, after the imports add:

```python
DRAFT_KINDS = ("expense_draft", "payment_draft")
```

Change `list_pending_drafts` (line ~41) `.where(...)` clause from
`RoomMessage.kind == "expense_draft"` to `RoomMessage.kind.in_(DRAFT_KINDS)`.

Change `update_draft` guard (line ~49) from
`or m.kind != "expense_draft"` to `or m.kind not in DRAFT_KINDS`.

- [ ] **Step 4: Add the payment-draft functions**

Append to `backend/app/drafts.py`:

```python
def create_payment_draft(session: Session, room_id: int, payload: dict) -> RoomMessage:
    """Persist a new pending payment draft (mirror of create_draft)."""
    att = {"type": "payment_draft", "status": "pending", **payload}
    att.pop("logged_by", None)
    return chat.post_message(session, room_id, None, body="", attachments=att, kind="payment_draft")


def commit_payment_draft(session: Session, draft_id: int, room_id: int,
                         logged_by: str | None) -> RoomMessage:
    m = session.get(RoomMessage, draft_id)
    if m is None or m.room_id != room_id or m.kind != "payment_draft":
        raise ledger.LedgerError(f"Draft #{draft_id} not found.")
    att = dict(m.attachments or {})
    if att.get("status") != "pending":
        raise ledger.LedgerError("This draft has already been processed.")
    if att.get("from_member_id") is None or att.get("to_member_id") is None or not att.get("amount"):
        raise ledger.LedgerError("The draft is missing required fields to record.")

    ledger.record_payment(
        session, room_id=room_id,
        from_member_id=int(att["from_member_id"]),
        to_member_id=int(att["to_member_id"]),
        amount=int(att["amount"]), note=att.get("note"), logged_by=logged_by,
    )
    names = _all_member_names(session, room_id)
    pay_att = {
        "type": "payment",
        "from": {"id": att["from_member_id"], "name": names.get(att["from_member_id"], "?")},
        "to": {"id": att["to_member_id"], "name": names.get(att["to_member_id"], "?")},
        "amount": att["amount"],
        "balances": current_balances(session, room_id),
    }
    card = chat.post_message(session, room_id, None, chat._payment_body(pay_att),
                            attachments=pay_att, kind="bot")
    att["status"] = "committed"
    m.attachments = att
    session.flush()
    return card


def commit_any(session: Session, draft_id: int, room_id: int,
               logged_by: str | None) -> RoomMessage:
    """Commit a draft, dispatching by kind (meal vs payment)."""
    m = session.get(RoomMessage, draft_id)
    if m is None or m.room_id != room_id:
        raise ledger.LedgerError(f"Draft #{draft_id} not found.")
    if m.kind == "payment_draft":
        return commit_payment_draft(session, draft_id, room_id, logged_by)
    return commit_draft(session, draft_id, room_id, logged_by)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_payment_drafts.py tests/test_drafts.py -v`
Expected: PASS (new tests + existing meal-draft tests still green).

- [ ] **Step 6: Commit**

```bash
git add backend/app/drafts.py backend/tests/test_payment_drafts.py
git commit -m "feat(be): payment-draft lifecycle (create/commit) + generalized pending drafts"
```

---

### Task 3: wire `propose_payment` → draft in the turn; update settle-blocked rendering

**Files:**
- Modify: `backend/app/chat.py` (`run_bot_turn` branch; `render_bot_attachments`; `_settle_blocked_body`)
- Modify: `backend/app/tools.py` (`settle_period` pending-summary builder handles both kinds)
- Test: `backend/tests/test_chat_payment_turn.py` (create)

**Interfaces:**
- Consumes: `result.last_result("propose_payment")`, `drafts.create_payment_draft`.
- Produces: an `@bot` payment turn ends with a persisted `payment_draft` message (kind `payment_draft`, status pending).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_chat_payment_turn.py`:

```python
import pytest
from app.db import Database
from app import accounts, chat, rooms
from app.models import RoomMessage


class _FakeResult:
    turn_id = "turn-1"
    final_text = "ok"
    error = None
    def __init__(self, payment): self._payment = payment
    def last_result(self, name):
        return self._payment if name == "propose_payment" else None


@pytest.fixture
def room(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'t.db'}")
    db.create_all()
    with db.session() as s:
        r = rooms.create_room(s, name="T", bot_handle="bot", admin_password_hash="x")
        a = accounts.add_unclaimed(s, r, display_name="Alice", nickname="alice")
        b = accounts.add_unclaimed(s, r, display_name="Bob", nickname="bob")
        s.flush()
        ids = {"room": r.id, "alice": a.id, "bob": b.id}
    return db, ids


def test_payment_proposal_creates_payment_draft(room, monkeypatch):
    db, ids = room
    payment = {"type": "payment_draft", "from_member_id": ids["alice"],
               "to_member_id": ids["bob"], "amount": 50000, "note": None}

    async def fake_run_turn(*a, **k):
        return _FakeResult(payment)

    monkeypatch.setattr("app.agent.run_turn", fake_run_turn)
    import asyncio
    msg = asyncio.run(chat.run_bot_turn(db, ids["room"], ids["alice"], "Alice", "@bot alice trả bob rồi"))
    assert msg.kind == "payment_draft"
    assert (msg.attachments or {})["amount"] == 50000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_chat_payment_turn.py -v`
Expected: FAIL — the turn posts a plain bot message (kind `bot`), not `payment_draft`.

- [ ] **Step 3: Add the payment-proposal branch in `run_bot_turn`**

In `backend/app/chat.py`, change the meal-proposal block (lines ~234–261) so the `if proposal:` becomes an `if/elif/else`:

```python
        proposal = result.last_result("propose_meal")
        payment_proposal = result.last_result("propose_payment")
        if proposal:
            payload = {k: proposal[k] for k in (
                "payer_member_id", "member_participants", "guests", "bill_total",
                "adjustments", "dish", "initiator", "note", "per_head_preview")}
            payload["raw_input"] = text
            payload["logged_by"] = str(member_id)
            payload["turn_id"] = result.turn_id
            with db.session() as s:
                new_msg, _ = drafts.create_draft(s, room_id, payload)
        elif payment_proposal and payment_proposal.get("type") == "payment_draft":
            payload = {k: payment_proposal[k] for k in
                       ("from_member_id", "to_member_id", "amount", "note")}
            payload["turn_id"] = result.turn_id
            with db.session() as s:
                new_msg = drafts.create_payment_draft(s, room_id, payload)
        else:
            attachments = render_bot_attachments(result)
            if attachments and attachments.get("type") == "settlement":
                body = _settlement_body(attachments)
            elif attachments and attachments.get("type") == "settle_blocked":
                body = _settle_blocked_body(attachments)
            else:
                body = result.final_text or (result.error and f"⚠️ {result.error}") or "(không có phản hồi)"
            with db.session() as s:
                new_msg = post_message(s, room_id, None, body, attachments=attachments, kind="bot")
```

(This removes the old `elif ... == "payment"` body branch — payments are no longer posted directly.)

- [ ] **Step 4: Drop `record_payment` from `render_bot_attachments`**

In `backend/app/chat.py`, change `render_bot_attachments` (lines ~122–131) to remove the first two lines referencing `record_payment`:

```python
def render_bot_attachments(result) -> dict | None:
    settle = result.last_result("settle_period")
    if settle:
        if settle.get("type") == "settle_blocked":
            return dict(settle)
        return {"type": "settlement", **settle}
    return None
```

Keep `_payment_body` (now used by `commit_payment_draft`).

- [ ] **Step 5: Generalize the settle-blocked summary (both draft kinds)**

In `backend/app/tools.py` `settle_period`, replace the pending-summary loop (lines ~416–425) with:

```python
                summaries = []
                for d in pending:
                    att = d.attachments or {}
                    if att.get("type") == "payment_draft":
                        names = _names_for(s, ctx.room_id,
                                           [att.get("from_member_id"), att.get("to_member_id")])
                        summaries.append({
                            "draft_id": d.id, "kind": "payment",
                            "from_name": names.get(att.get("from_member_id"), "?"),
                            "to_name": names.get(att.get("to_member_id"), "?"),
                            "amount": att.get("amount", 0),
                        })
                    else:
                        names = _names_for(s, ctx.room_id, [att.get("payer_member_id")])
                        summaries.append({
                            "draft_id": d.id, "kind": "meal",
                            "payer_name": names.get(att.get("payer_member_id"), "?"),
                            "bill_total": att.get("bill_total", 0),
                            "participant_count": len(att.get("member_participants") or []),
                        })
```

In `backend/app/chat.py`, replace `_settle_blocked_body` (lines ~166–176) with:

```python
def _settle_blocked_body(attachments: dict) -> str:
    lines = [attachments.get("message") or "Có đề xuất chưa xác nhận."]
    for p in attachments.get("pending") or []:
        if p.get("kind") == "payment":
            lines.append(
                f"• #{p['draft_id']}: {p.get('from_name', '?')} → {p.get('to_name', '?')} "
                f"{p.get('amount', 0):,}đ"
            )
        else:
            lines.append(
                f"• #{p['draft_id']}: {p.get('payer_name', '?')} trả "
                f"{p.get('bill_total', 0):,}đ ({p.get('participant_count', 0)} người)"
            )
    return "\n".join(lines)
```

- [ ] **Step 6: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_chat_payment_turn.py tests/test_chat.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/chat.py backend/app/tools.py backend/tests/test_chat_payment_turn.py
git commit -m "feat(be): payment turn ends in a payment_draft; settle-blocked shows both draft kinds"
```

---

### Task 4: commit endpoint dispatches by draft kind

**Files:**
- Modify: `backend/app/main.py` (`commit_draft_route` calls `drafts.commit_any`)
- Test: `backend/tests/test_api.py` (add one test)

**Interfaces:**
- Consumes: `drafts.commit_any`.
- Produces: `POST /api/rooms/{room_id}/drafts/{draft_id}/commit` works for both draft kinds.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_api.py` (reuse existing `client`, `_room`, `_join` helpers; a payment draft is created directly via `drafts`):

```python
def test_commit_payment_draft_via_endpoint(client):
    from app import accounts, drafts, rooms
    from app.db import get_db
    token = _room(client)
    sess, room_id = _join(client, token, "alice")
    h = {"Authorization": f"Bearer {sess}"}
    with get_db().session() as s:
        r = rooms.room_by_id(s, room_id)
        bob = accounts.add_unclaimed(s, r, display_name="Bob", nickname="bob")
        s.flush()
        me = client.get("/api/me", headers=h).json()
        d = drafts.create_payment_draft(s, room_id, {
            "from_member_id": me["id"], "to_member_id": bob.id, "amount": 25000, "note": None})
        draft_id = d.id
    r = client.post(f"/api/rooms/{room_id}/drafts/{draft_id}/commit", headers=h)
    assert r.status_code == 200, r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py::test_commit_payment_draft_via_endpoint -v`
Expected: FAIL — route calls `drafts.commit_draft`, which rejects a `payment_draft` kind ("Draft not found").

- [ ] **Step 3: Point the route at `commit_any`**

In `backend/app/main.py` `commit_draft_route` (line ~355), change `drafts.commit_draft(s, draft_id, room_id, logged_by=str(ctx.member_id))` to `drafts.commit_any(s, draft_id, room_id, logged_by=str(ctx.member_id))`. (The result is a bot message published via the existing hub call — unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py::test_commit_payment_draft_via_endpoint -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_api.py
git commit -m "feat(be): /drafts/{id}/commit dispatches meal vs payment by kind"
```

---

### Task 5: frontend payment confirm card

**Files:**
- Modify: `frontend/src/types/chat.ts` (add `PaymentDraft`)
- Create: `frontend/src/components/chat/payment-draft-card.tsx`
- Modify: `frontend/src/components/chat/message-list.tsx` (route `kind==="payment_draft"`)
- Test: `frontend/src/components/chat/__tests__/payment-draft-card.test.tsx` (create)

**Interfaces:**
- Consumes: `api.commitDraft`, `api.cancelDraft` (both already generic by draftId), `fmt`.
- Produces: `PaymentDraftCard`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/chat/__tests__/payment-draft-card.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { PaymentDraftCard } from "../payment-draft-card";

const commitDraft = vi.fn(() => Promise.resolve({}));
const cancelDraft = vi.fn(() => Promise.resolve({}));
vi.mock("@/lib/api", () => ({
  commitDraft: (...a: any[]) => commitDraft(...a),
  cancelDraft: (...a: any[]) => cancelDraft(...a),
  ApiError: class extends Error {},
}));

const members = [{ id: 1, display_name: "Alice" }, { id: 2, display_name: "Bob" }];
const msg = (status: string) => ({
  id: 9, kind: "payment_draft",
  attachments: { type: "payment_draft", status, from_member_id: 1, to_member_id: 2, amount: 50000, note: null },
});

describe("PaymentDraftCard", () => {
  it("shows from→to + amount and confirms", () => {
    render(<PaymentDraftCard message={msg("pending")} members={members} roomId={3} />);
    expect(screen.getByText(/Alice/)).toBeInTheDocument();
    expect(screen.getByText(/Bob/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));
    expect(commitDraft).toHaveBeenCalledWith(3, 9);
  });

  it("cancels", () => {
    render(<PaymentDraftCard message={msg("pending")} members={members} roomId={3} />);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(cancelDraft).toHaveBeenCalledWith(3, 9);
  });

  it("hides actions once committed", () => {
    render(<PaymentDraftCard message={msg("committed")} members={members} roomId={3} />);
    expect(screen.queryByRole("button", { name: /confirm/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Recorded/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/chat/__tests__/payment-draft-card.test.tsx`
Expected: FAIL — cannot import `../payment-draft-card`.

- [ ] **Step 3: Add the type**

In `frontend/src/types/chat.ts` append:

```ts
/** The interactive `payment_draft` message attachment. draft_id is the carrying
 * message's `id`. Amount is authoritative (server-computed for pay-off). */
export interface PaymentDraft {
  type: "payment_draft";
  status: "pending" | "committed" | "cancelled";
  from_member_id: number;
  to_member_id: number;
  amount: number;
  note: string | null;
  turn_id?: string;
}
```

- [ ] **Step 4: Create the card**

Create `frontend/src/components/chat/payment-draft-card.tsx`:

```tsx
"use client";
import { useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { fmt } from "@/lib/format";
import type { PaymentDraft } from "@/types/chat";

interface Member { id: number; display_name: string }

export function PaymentDraftCard({
  message, members, roomId,
}: { message: any; members: Member[]; roomId: number }) {
  const att = message.attachments as PaymentDraft;
  const name = (id: number) => members.find((m) => m.id === id)?.display_name ?? "?";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const statusLabel =
    att.status === "committed" ? "Recorded" : att.status === "cancelled" ? "Cancelled" : null;

  const run = (fn: Promise<unknown>, fail: string) => {
    setBusy(true); setError(null);
    fn.catch((e) => setError(e instanceof ApiError ? e.message : fail)).finally(() => setBusy(false));
  };

  return (
    <div className="mt-1 w-full max-w-[95%] rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-semibold text-[var(--text-primary)]">Payment</span>
        {statusLabel && <span className="text-xs text-[var(--text-secondary)]">{statusLabel}</span>}
      </div>
      <p className="text-sm text-[var(--text-primary)]">
        {name(att.from_member_id)} → {name(att.to_member_id)}{" "}
        <span className="font-semibold text-[var(--accent-text)]">{fmt(att.amount)} đ</span>
      </p>
      {error && <p className="mt-2 text-xs text-[var(--danger)]">{error}</p>}
      {att.status === "pending" && (
        <div className="mt-2 flex gap-2">
          <button type="button" disabled={busy}
            onClick={() => run(api.commitDraft(roomId, message.id), "Couldn't record, please try again.")}
            className="flex-1 rounded-lg bg-[var(--accent-primary)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
            Confirm
          </button>
          <button type="button" disabled={busy}
            onClick={() => run(api.cancelDraft(roomId, message.id), "Couldn't cancel, please try again.")}
            className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text-secondary)]">
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Route the kind in message-list**

In `frontend/src/components/chat/message-list.tsx`:
- add import: `import { PaymentDraftCard } from "./payment-draft-card";`
- change the `turnId` line (line ~102) to also cover payments:
  `const turnId = (m.kind === "expense_draft" || m.kind === "payment_draft") ? m.attachments?.turn_id : undefined;`
- add a branch after the `expense_draft` branch (after line ~117):

```tsx
        ) : m.kind === "payment_draft" ? (
          <div key={m.id} className="flex flex-col items-start">
            <span className="mb-1 px-1 text-xs font-medium text-[var(--accent-text)]">Bot</span>
            {turnSteps && <AgentTimeline steps={turnSteps} live={false} />}
            <PaymentDraftCard message={m} members={members} roomId={roomId} />
          </div>
```

- [ ] **Step 6: Run tests + typecheck**

Run: `cd frontend && npx vitest run src/components/chat/__tests__/payment-draft-card.test.tsx && npx tsc --noEmit`
Expected: PASS, no type errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/chat.ts frontend/src/components/chat/payment-draft-card.tsx frontend/src/components/chat/message-list.tsx frontend/src/components/chat/__tests__/payment-draft-card.test.tsx
git commit -m "feat(fe): payment confirm card + message-list routing"
```

---

### Task 6: Cursor skills infrastructure + move procedures out of the prompt

**Files:**
- Create: `backend/app/agent_skills/rules/money-safety.mdc`
- Create: `backend/app/agent_skills/skills/record-payment/SKILL.md`
- Create: `backend/app/agent_skills/skills/record-meal/SKILL.md`
- Create: `backend/app/agent_skills/skills/settle-period/SKILL.md`
- Create: `backend/app/skills.py` (materializer)
- Modify: `backend/app/agent.py` (call `materialize`; `setting_sources=["project"]`)
- Modify: `backend/app/prompt.py` (slim to identity + money invariant + pointer)
- Test: `backend/tests/test_skills_materializer.py` (create)

**Interfaces:**
- Produces: `app.skills.materialize(workspace: str) -> None` — idempotently writes
  `<workspace>/.cursor/rules/*.mdc` (forced `alwaysApply: true`) and
  `<workspace>/.cursor/skills/<name>/SKILL.md` from `backend/app/agent_skills/`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_skills_materializer.py`:

```python
from pathlib import Path
from app import skills


def test_materialize_writes_rules_and_skills(tmp_path):
    skills.materialize(str(tmp_path))
    rules = list((tmp_path / ".cursor" / "rules").glob("*.mdc"))
    assert rules, "expected at least one .mdc rule"
    text = rules[0].read_text("utf-8")
    assert "alwaysApply: true" in text.splitlines()[1:4].__str__() or "alwaysApply: true" in text
    assert (tmp_path / ".cursor" / "skills" / "record-payment" / "SKILL.md").exists()


def test_materialize_is_idempotent(tmp_path):
    skills.materialize(str(tmp_path))
    rule = next((tmp_path / ".cursor" / "rules").glob("*.mdc"))
    before = rule.stat().st_mtime_ns
    skills.materialize(str(tmp_path))  # unchanged → no rewrite
    assert rule.stat().st_mtime_ns == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_skills_materializer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.skills'`.

- [ ] **Step 3: Create the skill source files**

`backend/app/agent_skills/rules/money-safety.mdc`:

```markdown
---
alwaysApply: true
---
# Quy tắc TIỀN BẠC (bắt buộc)
- KHÔNG BAO GIỜ tự tính toán hay tự gõ lại một con số tiền do công cụ trả về.
- Số tiền người dùng nói (vd '840k' → 840000) được truyền vào công cụ MỘT LẦN duy nhất.
- Mọi phép chia/cộng/trừ và mã QR đều do công cụ thực hiện — bạn chỉ chọn công cụ.
- '840k' = 840000, '1tr'/'1 triệu' = 1000000, '50k' = 50000 (VND, số nguyên).
- Mọi thay đổi số dư (bữa ăn, trả tiền, chốt) là ĐỀ XUẤT — người dùng xác nhận trên thẻ.
- Nếu công cụ trả về `error`, hỏi lại cho rõ thay vì đoán.
```

`backend/app/agent_skills/skills/record-payment/SKILL.md`:

```markdown
---
name: record-payment
description: Ghi khi một người trả tiền mặt cho người khác — "A trả B", "A đã trả", "A gửi B 100k", "trả đủ rồi".
---
# Ghi trả tiền mặt

Dùng `propose_payment` (KHÔNG dùng `propose_meal`). Nó chỉ ĐỀ XUẤT — người dùng xác nhận trên thẻ.

- `from` = người trả (bỏ trống = người đang nhắn), `to` = người nhận.
- Có số tiền cụ thể ('A trả B 100k') → truyền `amount` (VND).
- KHÔNG có số tiền ('A đã trả B', 'trả đủ rồi') → BỎ TRỐNG `amount`; công cụ tự tính đúng số A đang nợ B. ĐỪNG tự đoán số.
- Nhiều người trả trong một câu ('Dũng và Giang đã trả Linh') → gọi `propose_payment` MỘT LẦN CHO MỖI người trả.
- Nếu công cụ trả về `payment_settled` nghĩa là người đó không còn nợ — báo lại, không tạo thẻ.
```

`backend/app/agent_skills/skills/record-meal/SKILL.md`:

```markdown
---
name: record-meal
description: Ghi một bữa ăn nhóm — "840k cả nhóm trừ An", "bún bò 300k 5 người", có khách, có điều chỉnh.
---
# Ghi một bữa ăn

1. `find_members` để xác định người trả + người tham gia (`all_active:true` cho 'cả nhóm').
2. `propose_meal` với payer, participants (id), total (tổng hoá đơn), adjustments, guests, và dish/initiator/note nếu có.
   - 'trừ An' = An KHÔNG nằm trong participants.
   - 'An trả nhưng không ăn' = An là payer nhưng không nằm trong participants.
   - 'Bình +50k' = adjustment {member: <id Bình>, amount: 50000}.
   - `propose_meal` CHỈ ĐỀ XUẤT — người dùng xác nhận trên thẻ nháp.
- Sửa/xoá: `void_meal` để xoá; sửa thì void rồi `propose_meal` lại.
- Có ảnh hoá đơn: đọc tổng tiền từ ảnh, dùng làm `total`. Chỉ nhận ảnh dán trực tiếp.
```

`backend/app/agent_skills/skills/settle-period/SKILL.md`:

```markdown
---
name: settle-period
description: Xem ai nợ ai và chốt tiền — "ai trả tuần này", "chốt tuần này", "số dư", "reset".
---
# Chốt / xem số dư

- `settle_period` làm tất cả: tính số dư → gộp chuyển khoản → tạo mã QR.
- Xem trước: `commit:false`. Chốt (đóng kỳ): `commit:true` — CHỈ khi người dùng nói rõ 'chốt'/'reset'.
- Không có mốc thời gian rõ → keyword mặc định 'since_last'.
- Chỉ hiển thị chi tiêu ('tháng này tiêu bao nhiêu') → `resolve_period` rồi `get_period_balances`.
- Nếu còn đề xuất chưa xác nhận, công cụ báo `settle_blocked` — nhắc người dùng xác nhận/huỷ trước.
```

- [ ] **Step 4: Create the materializer**

`backend/app/skills.py`:

```python
"""Materialize the bot's Cursor skills/rules into the agent workspace.

Cursor's headless bridge loads workspace guidance from ``.cursor/`` when
``LocalAgentOptions.setting_sources`` includes ``"project"``:
  - ``.cursor/rules/<name>.mdc`` with ``alwaysApply: true`` → loaded every turn.
  - ``.cursor/skills/<name>/SKILL.md`` → on-demand, description-triggered.
Source files live in ``app/agent_skills/`` and are copied idempotently before a turn.
"""
from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).parent / "agent_skills"
_FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def _force_always_apply(text: str) -> str:
    m = _FM_RE.match(text)
    if not m:
        return f"---\nalwaysApply: true\n---\n{text}"
    lines = [ln for ln in m.group(1).splitlines()
             if not ln.strip().lower().startswith("alwaysapply:")]
    lines.append("alwaysApply: true")
    return "---\n" + "\n".join(lines) + "\n---\n" + text[m.end():]


def _write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == data:
        return  # idempotent: unchanged → no rewrite
    path.write_text(data, encoding="utf-8")


def materialize(workspace: str) -> None:
    cursor = Path(workspace) / ".cursor"
    rules_src, skills_src = _SRC / "rules", _SRC / "skills"
    if rules_src.is_dir():
        for src in rules_src.glob("*.mdc"):
            _write(cursor / "rules" / src.name, _force_always_apply(src.read_text(encoding="utf-8")))
    if skills_src.is_dir():
        for skill_dir in skills_src.iterdir():
            if not skill_dir.is_dir():
                continue
            for f in skill_dir.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(skills_src)
                    _write(cursor / "skills" / rel, f.read_text(encoding="utf-8"))
```

- [ ] **Step 5: Run materializer tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_skills_materializer.py -v`
Expected: PASS.

- [ ] **Step 6: Wire into the agent run**

In `backend/app/agent.py`:
- add import near the top: `from app.skills import materialize`
- in `run_turn`, right after `workspace = _ensure_workspace()` (line ~215) add: `materialize(workspace)`
- in the `LocalAgentOptions(...)` call (lines ~224–228) add `setting_sources=["project"],`:

```python
        local = LocalAgentOptions(
            cwd=workspace,
            custom_tools=build_tools(ctx),
            setting_sources=["project"],
            store={"type": "sqlite", "root_dir": os.path.join(workspace, ".cursor-store")},
        )
```

- [ ] **Step 7: Slim the system prompt**

Replace `backend/app/prompt.py` `build_system_prompt` body with the identity + money invariant + a pointer to skills (procedures now live in `.cursor/skills`):

```python
def build_system_prompt(*, sender_name: str | None = None) -> str:
    who = f' The person messaging you now is "{sender_name}".' if sender_name else ""
    return (
        "Bạn là **chiatienan**, một trợ lý chia tiền ăn trưa trong một nhóm chat.\n"
        "Nhóm gồm ~6–7 đồng nghiệp; mỗi ngày ai cũng có thể là người trả tiền.\n"
        f"Trả lời ngắn gọn, thân thiện, bằng tiếng Việt.{who}\n"
        "\n"
        "# Quy tắc TIỀN BẠC (bắt buộc)\n"
        "- KHÔNG BAO GIỜ tự tính toán hay tự gõ lại một con số tiền do công cụ trả về.\n"
        "- Số tiền người dùng nói (vd '840k' → 840000) được truyền vào công cụ MỘT LẦN duy nhất.\n"
        "- Mọi thay đổi số dư (bữa ăn, trả tiền, chốt) là ĐỀ XUẤT — người dùng xác nhận trên thẻ.\n"
        "\n"
        "# Công cụ & quy trình\n"
        "- Quy trình chi tiết cho ghi bữa ăn, ghi trả tiền, và chốt kỳ nằm trong các *skill* của workspace"
        " (record-meal, record-payment, settle-period) — làm theo skill phù hợp với tin nhắn.\n"
        "- Quản lý thành viên: `add_member`, `update_member` (target=nickname|id; `active:true` để khôi phục),"
        " `delete_member` (xoá mềm, giữ lịch sử).\n"
    )
```

- [ ] **Step 8: Run the full backend suite**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS (all green). Fix any prompt-dependent test assertions that referenced removed prompt lines (e.g. a test asserting the old `record_payment` guidance string) — update them to the new prompt/skill content.

- [ ] **Step 9: Commit**

```bash
git add backend/app/agent_skills backend/app/skills.py backend/app/agent.py backend/app/prompt.py backend/tests/test_skills_materializer.py
git commit -m "feat(be): Cursor skills infra (.cursor rules+skills, setting_sources) + slim prompt"
```

---

### Task 7: switch model to grok-4.5 (high, fast)

**Files:**
- Modify: `backend/app/config.py` (default `cursor_model`)
- Modify: `backend/.env.example` (document `CURSOR_SDK_MODEL`)
- Test: `backend/tests/test_config.py` (adjust default assertion if present)

**Interfaces:** none (config only).

- [ ] **Step 1: Update the default**

In `backend/app/config.py` line ~51 change:
`cursor_model=(os.environ.get("CURSOR_SDK_MODEL") or "").strip() or "composer-2.5",`
to:
`cursor_model=(os.environ.get("CURSOR_SDK_MODEL") or "").strip() or "grok-4.5-fast",`

- [ ] **Step 2: Document in `.env.example`**

In `backend/.env.example`, set/add:
```
# grok-4.5-fast = grok-4.5 with effort=high, fast=true (bare grok-4.5 = fast=false)
CURSOR_SDK_MODEL=grok-4.5-fast
```

- [ ] **Step 3: Update/inspect config test**

Run: `cd backend && .venv/bin/python -m pytest tests/test_config.py -v`
If a test asserts the default is `composer-2.5`, change it to `grok-4.5-fast`. Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/config.py backend/.env.example backend/tests/test_config.py
git commit -m "feat(be): default model grok-4.5-fast (effort=high, fast=true)"
```

---

### Task 8: full verification, merge, deploy + wipe

**Files:** none (ops).

- [ ] **Step 1: Full local test suites + typecheck**

Run:
```bash
cd backend && .venv/bin/python -m pytest -q
cd ../frontend && npx vitest run && npx tsc --noEmit
```
Expected: all green.

- [ ] **Step 2: Merge branch → main and push**

```bash
git checkout main && git merge --ff-only feat/agent-payments-skills-grok && git push origin main
```
(If GitHub has diverged, review before merging — see the session's earlier PR-merge experience.)

- [ ] **Step 3: Deploy on the droplet, set model env, wipe data**

```bash
ssh -i ~/.ssh/digitalocean-openclaw root@165.22.246.208 'set -e; cd /opt/chiatienan;
  git fetch origin main -q && git reset --hard origin/main -q;
  grep -q "^CURSOR_SDK_MODEL=grok-4.5-fast" .env || sed -i "s/^CURSOR_SDK_MODEL=.*/CURSOR_SDK_MODEL=grok-4.5-fast/" .env;
  docker compose up -d --build'
```
Then wipe:
```bash
ssh -i ~/.ssh/digitalocean-openclaw root@165.22.246.208 'cd /opt/chiatienan;
  docker compose stop backend; rm -f data/chiatienan.db data/chiatienan.db-shm data/chiatienan.db-wal;
  docker compose up -d backend'
```

- [ ] **Step 4: Verify live**

- `curl -s -o /dev/null -w "%{http_code}" https://chiatienan.duckdns.org/` → 200
- Backend logs show `Resolved model "grok-4.5-fast" -> id=grok-4.5 fast=true,effort=high` (or `effort=high,fast=true`).
- Drive one payment turn: create a room, log a meal, then "@bot <A> đã trả <B>" → a **Payment** confirm card appears (server-computed amount); Confirm records it; balances update. A duplicate "@bot <A> cũng trả <B>" → `payment_settled` (no double count).

- [ ] **Step 5: Update deployment memory** with the grok model + `CURSOR_SDK_MODEL=grok-4.5-fast` note.

---

## Self-Review

**Spec coverage:**
- WS1 payments propose→confirm + pay-off → Tasks 1–5. ✓
- WS2 Cursor skills infra + procedures → Task 6. ✓
- WS3 grok-4.5 (high, fast) → Task 7. ✓
- WS4 wipe data → Task 8 step 3. ✓
- Testing (TDD) → each task leads with a failing test. ✓

**Type/name consistency:** `propose_payment` result keys (`from_member_id`, `to_member_id`, `amount`, `note`, `type:"payment_draft"`) match the payload consumed in Task 3 (`create_payment_draft`) and the attachment read by the frontend `PaymentDraft` (Task 5). `commit_any` (Task 2) is the single entry the route calls (Task 4). `materialize(workspace)` signature matches Task 6 wiring.

**Known limitation (documented):** pay-off amount uses the global `net_transfers` graph, so "X trả Y" with no amount only finds a value when the minimized graph routes X→Y. With multiple creditors it may route X to a different creditor and report `payment_settled`; the user can then state an explicit amount. Acceptable for v1.
