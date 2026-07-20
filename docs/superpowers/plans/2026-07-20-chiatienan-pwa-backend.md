# chiatienan PWA — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the existing Teams-oriented backend into the multi-room PWA backend: rooms + invite links, self-service accounts (nickname + PIN identity), room-scoped ledger, shared chat with SSE fan-out, and a `@bot`-invoked agent — reusing the deterministic ledger/agent core.

**Architecture:** FastAPI backend on the existing droplet. All ledger data is scoped by `room_id`. Members become room accounts. Chat messages persist in `room_messages` and fan out to per-room in-process SSE subscribers. A message mentioning `@bot` triggers `agent.run_turn` (reused as-is), whose structured `TurnResult` is rendered into one bot `room_message` and broadcast. No Teams/Azure/Bot Framework.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 (SQLite/WAL), `cursor-sdk`, pytest + httpx. Frontend is a separate plan.

## Global Constraints

- All money is **integer VND**; balances derived, never stored (spec §4).
- All dates/times in **`Asia/Ho_Chi_Minh`**; week = Mon–Sun (spec §4). Use `app.clock`.
- **Numbers never round-trip tool→LLM→tool** — `settle_period` computes balances→net→QR server-side (spec D3/§8).
- **Identity is not authentication** (spec D7/D8): store PIN as plaintext, no hashing/rate-limit/lockout. Invite + session tokens are unguessable (`secrets.token_urlsafe`), HTTPS only.
- **Single instance** (in-proc SSE pub/sub) — no horizontal scaling (spec §11).
- Agent runs **one at a time** (serialize ledger writes) via an `asyncio` lock.
- Every ledger/roster/chat query is **filtered by `room_id`**; a session may only touch its own room.
- Follow existing patterns: each tool opens a short-lived `db.session()`; validation failures return `{"ok": False, "error": ...}`.

**Migration note:** the droplet DB holds no real data yet. This plan changes the schema; **delete the dev DB** (`rm /opt/chiatienan/data/chiatienan.db*`) before first run rather than writing migrations.

---

## Revisions after Fable review (these OVERRIDE the tasks below where they conflict)

- **B2 — tests use the existing fixtures, not in-memory/monkeypatch.** All new tests use the
  `db` fixture in `backend/tests/conftest.py` (file-backed `tmp_path` SQLite) and the
  `ADMIN_PASSWORD=test-admin-pw` it sets. For API tests, override the app's DB by monkeypatching the
  module singleton: `monkeypatch.setattr("app.db._default", db, raising=False)` (so `get_db()` returns
  it) — do **not** monkeypatch the frozen `Settings`, and do **not** use `Database("sqlite://")` with
  `TestClient`. Use `X-Admin-Password: test-admin-pw` in admin calls.
- **B3 — stale tests must be migrated in the task that breaks them.** Task 3 **replaces the entire
  contents** of `tests/test_roster.py`. Task 4 **replaces the entire contents** of `tests/test_tools.py`
  and edits `tests/test_agent.py` (drop `sender_teams_id=`; use `room_id=`, `sender_member_id=`).
  Task 10 **deletes** `tests/test_main.py` (Teams `/api/messages`+`/admin` assertions) and **rewrites**
  `tests/test_config.py` (drop `microsoft_*`, assert `bot_handle == "bot"`), plus deletes
  `test_teams_parse.py`/`test_reply.py`/`test_worker.py`. Every "full suite green" gate assumes these.
- **M4 — agent dispatch (Task 10 `post_message`).** Keep a reference to the task
  (`_BG: set = set(); t = asyncio.create_task(_run()); _BG.add(t); t.add_done_callback(_BG.discard)`).
  Wrap `_run` in `try/except/finally`; the `finally` **always** publishes `bot.done`; on exception
  post an error bot message (`chat.post_message(..., kind="bot", body="⚠️ …")`) and publish it. Never
  leave the typing indicator stuck.
- **M5 — never block the event loop.** In `agent.run_turn`, run the synchronous model resolution off
  the loop: `selection = await asyncio.to_thread(resolve_model_selection, api_key, default_cursor_model(), reasoning="medium")`
  (`cursor_runner._list_models`/`_alias_index` use blocking `urllib`/`Cursor.models.list`).
- **M2 — SSE hardening (Tasks 8 & 10).** `stream()` uses `await asyncio.wait_for(q.get(), timeout=25)`;
  on `TimeoutError` yield `": ping\n\n"` and continue. `RoomHub.publish`: on `asyncio.QueueFull`,
  `unsubscribe` that queue and put a `{"type":"__closed__"}` sentinel so its generator exits and the
  client reconnects with `?since=` (do not silently drop).
- **M6 — add `GET /api/rooms/{room_id}/members`** (Task 10): `require_session` + `_check_room` →
  `[{ "id", "display_name", "nickname" }]` from `roster.list_members(s, room_id)` (no banking).
- **M7 — persist chat images.** `MessageIn.images` (list of `{data, mimeType}`) is validated with
  `app.images.sanitize_images` and stored on the message: `attachments={"images": clean}` (merged with
  bot attachments for bot messages). `post_message`/`message_to_dict` carry `attachments` through; the
  frontend renders them. Reuse `sanitize_images` — do not accept unbounded base64.
- **M8 — deps + build.** In `backend/pyproject.toml` remove `botbuilder-core` and `aiohttp`, fix the
  description; keep `sqlalchemy`, `python-multipart` only if used. Do **not** `--build` the frontend on
  the 512 MB droplet (see frontend plan); the backend image is light and builds fine on-box.
- **M9 — text sweep (own task, before deploy).** `prompt.py`: replace "nhóm chat Microsoft Teams"
  wording with room/PWA-neutral text. `qr.py`: change the "/admin" hint to "/profile". `tools.py`:
  `find_members` schema `include_tagged`/@tag text — `turn_mentions` is always `[]` now; drop the
  tagged-people path (names + `all_active` only).
- **Minors.** `tools._names_for(session, room_id, ids)` gains `room_id`. Keep `roster.resolve`'s
  return key **`{"matched": [{id, display_name}]}`** (do not rename to `resolved`/`name`) to avoid
  churn — update Task 3's test to `matched`/`display_name`. `message_to_dict(m, author)` (2 args).
  `PUT /api/me` must allow clearing a bank field (accept explicit empty string; use
  `model_dump(exclude_unset=True)` and set even when `""`). `resolve_bearer` strips `Bearer ` case-
  insensitively. Add a Task-10 test for **SSE catch-up via `since`** and **cross-room 403** on
  `/messages`. If `caddy_domain` is empty, return `invite_token` without a broken `invite_link`.

### Studio-apartment / dual-key model (spec D5/D5a/D7 update — apply across tasks)

- **`Member.pin` is nullable** (Task 1): `pin: Mapped[str | None] = mapped_column(String(20))`.
  `pin IS NULL` = unclaimed (agent-added). Drop `nullable=False` on pin.
- **`accounts.identify` also claims** (Task 6): if the nickname exists with `pin IS NULL`, set the
  provided PIN (claim) and return a session; if `pin` is set, require a match; else 401. Add
  `add_unclaimed(session, room, *, display_name, nickname, bank_code, account_number, account_holder) -> Member`
  (creates a member with `pin=None`; rejects duplicate nickname).
- **New agent tool `add_member`** (Task 4, `tools.py`): input `{display_name, nickname, bank_code?,
  account_number?, account_holder?}`; calls `accounts.add_unclaimed(s, room, ...)` for `ctx.room_id`;
  returns `{ok, member_id, nickname}` or `{ok: False, error}` on duplicate. Any resident can invoke it
  via `@bot add …` — there is **no admin gate** on tools. Add it to `build_tools`'s returned dict and
  document it in `prompt.py`.
- **No per-room admin.** Only `POST /api/rooms` uses `require_admin`; every other route is
  `require_session` (any resident is equal). Do not add admin checks to log/query/add/settle.
- Tests: `add_member` creates an unclaimed member; `identify` claims an unclaimed nickname then
  enforces the PIN on the next call.

## File Structure

- `backend/app/models.py` — **modify**: add `Room`, `Session`, `RoomMessage`; add `room_id` to `Member`/`Meal`/`Settlement`; add `nickname`/`pin` to `Member`; drop `teams_user_id`/`aad_object_id` and `ProcessedActivity`.
- `backend/app/ledger.py` — **modify**: thread `room_id` through every read/write.
- `backend/app/roster.py` — **modify**: room-scoped `resolve`/`list_members`; drop `capture_sender`.
- `backend/app/tools.py` — **modify**: `ToolContext` carries `room_id` + `sender_member_id` (int); pass `room_id` into ledger/roster.
- `backend/app/agent.py` — **modify (minimal)**: `ToolContext` field rename only; `run_turn` reused.
- `backend/app/rooms.py` — **create**: room create (admin) + lookup by invite token.
- `backend/app/accounts.py` — **create**: join/create-account, identify (nick+pin), sessions, profile.
- `backend/app/auth.py` — **create**: `require_session` FastAPI dependency → `(member, room)`.
- `backend/app/realtime.py` — **create**: `RoomHub` in-proc pub/sub + SSE event helpers.
- `backend/app/chat.py` — **create**: persist/list messages, `@bot` detection, agent dispatch + bot-message rendering.
- `backend/app/config.py` — **modify**: drop `MICROSOFT_*`/`bot_handle`; add `bot_handle` default `bot`.
- `backend/app/main.py` — **modify**: mount `rooms`/`accounts`/`chat` routers, SSE route; remove Teams lifespan.
- `backend/app/reply.py`, `teams.py`, `teams_parse.py`, `worker.py`, `admin.py` — **delete**.
- Tests under `backend/tests/` per task.

---

## Task 1: Schema — Room, room-scoped accounts, sessions, messages

**Files:**
- Modify: `backend/app/models.py`
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Produces: `Room(id, name, invite_token, created_at)`; `Member(id, room_id, display_name, nickname, pin, aliases, bank_code, account_number, account_holder, active, created_at)`; `Session(id, member_id, token, created_at)`; `RoomMessage(id, room_id, author_member_id|None, kind, body, attachments, created_at)`; `Meal`/`MealShare`/`Settlement` unchanged except `Meal.room_id`, `Settlement.room_id`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_models.py
from app.db import Database
from app.models import Room, Member, RoomMessage, Session as UserSession


def _db():
    d = Database("sqlite://")  # in-memory
    d.create_all()
    return d


def test_room_member_message_roundtrip():
    d = _db()
    with d.session() as s:
        room = Room(name="Lunch", invite_token="tok123")
        s.add(room); s.flush()
        m = Member(room_id=room.id, display_name="An", nickname="an", pin="1234",
                   bank_code="VCB", account_number="001", account_holder="AN")
        s.add(m); s.flush()
        s.add(UserSession(member_id=m.id, token="sess1"))
        s.add(RoomMessage(room_id=room.id, author_member_id=m.id, kind="text", body="hi"))
        s.add(RoomMessage(room_id=room.id, author_member_id=None, kind="bot", body="pong",
                          attachments={"transfers": []}))
        s.flush()
        assert m.room_id == room.id
        msgs = s.query(RoomMessage).order_by(RoomMessage.id).all()
        assert [x.kind for x in msgs] == ["text", "bot"]
        assert msgs[1].author_member_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_models.py -v`
Expected: FAIL — `ImportError`/`AttributeError` (Room/nickname/pin/RoomMessage don't exist).

- [ ] **Step 3: Write minimal implementation**

Edit `backend/app/models.py`: remove `ProcessedActivity`; add `Room`, `Session`, `RoomMessage`; adjust `Member`, `Meal`, `Settlement`.

```python
from sqlalchemy import UniqueConstraint

class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    invite_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (UniqueConstraint("room_id", "nickname", name="uq_room_nickname"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    nickname: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    pin: Mapped[str] = mapped_column(String(20), nullable=False)  # identity handle, not a secret (D8)
    aliases: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    bank_code: Mapped[str | None] = mapped_column(String(40))
    account_number: Mapped[str | None] = mapped_column(String(40))
    account_holder: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)

    def has_bank_details(self) -> bool:
        return bool(self.bank_code and self.account_number and self.account_holder)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)


class RoomMessage(Base):
    __tablename__ = "room_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False, index=True)
    author_member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"))  # None = bot
    kind: Mapped[str] = mapped_column(String(20), default="text", nullable=False)  # text|bot
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    attachments: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)
```

In `Meal`: add `room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False, index=True)`; change `source` default to `"web"`. In `Settlement`: add the same `room_id`. Delete the `ProcessedActivity` class.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/tests/test_models.py
git commit -m "feat(models): rooms, room-scoped accounts, sessions, messages"
```

---

## Task 2: Room-scope the ledger

**Files:**
- Modify: `backend/app/ledger.py`
- Test: `backend/tests/test_ledger.py` (extend existing)

**Interfaces:**
- Produces (all gain a required `room_id: int`): `record_meal(session, *, room_id, payer_member_id, participants, total_amount, adjustments=None, occurred_on=None, note=None, raw_input=None, source="web", logged_by=None)`; `void_meal(session, meal_id, *, room_id, by=None)`; `period_balances(session, room_id, from_date, to_date)`; `last_settlement(session, room_id)`; `record_settlement(session, *, room_id, period_from, period_to, requested_by, transfers)`; `meals_in_window(session, room_id, from_date, to_date)`. Removes `already_processed`/`mark_processed`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ledger.py  (add)
from app import ledger
from app.db import Database
from app.models import Room, Member


def _room_with_two():
    d = Database("sqlite://"); d.create_all()
    with d.session() as s:
        r = Room(name="A", invite_token="t"); s.add(r); s.flush()
        an = Member(room_id=r.id, display_name="An", nickname="an", pin="1")
        bi = Member(room_id=r.id, display_name="Bình", nickname="binh", pin="2")
        s.add_all([an, bi]); s.flush()
        return d, r.id, an.id, bi.id


def test_record_and_balances_are_room_scoped():
    d, room_id, an, bi = _room_with_two()
    with d.session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=an,
                           participants=[an, bi], total_amount=100000)
    with d.session() as s:
        bal = ledger.period_balances(s, room_id, None, __import__("datetime").date(2999, 1, 1))
        assert bal[an]["balance"] == 50000 and bal[bi]["balance"] == -50000
        # other room sees nothing
        assert ledger.period_balances(s, 999, None, __import__("datetime").date(2999, 1, 1)) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_ledger.py::test_record_and_balances_are_room_scoped -v`
Expected: FAIL — `record_meal()` got unexpected/missing `room_id`.

- [ ] **Step 3: Write minimal implementation**

In `ledger.py`: add `room_id` to `Meal(...)` construction in `record_meal`; validate payer/participants belong to `room_id` (`select(Member).where(Member.id.in_(...), Member.room_id == room_id)`). In `void_meal`, load `Meal` and assert `meal.room_id == room_id` else `LedgerError`. In `period_balances`/`meals_in_window`, add `Meal.room_id == room_id` to the `_in_window` conds. In `last_settlement`/`record_settlement`, filter/set `Settlement.room_id == room_id`. Delete `already_processed`/`mark_processed` and the `ProcessedActivity` import.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_ledger.py -v`
Expected: PASS (update any pre-existing ledger tests to pass `room_id=`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ledger.py backend/tests/test_ledger.py
git commit -m "feat(ledger): scope all reads/writes by room_id"
```

---

## Task 3: Room-scope the roster; drop Teams capture

**Files:**
- Modify: `backend/app/roster.py`
- Test: `backend/tests/test_roster.py`

**Interfaces:**
- Produces: `list_members(session, room_id) -> list[Member]`; `resolve(session, room_id, *, names, mentions, all_active) -> {"resolved": [...], "unresolved": [...]}`. Removes `capture_sender`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_roster.py
from app import roster
from app.db import Database
from app.models import Room, Member


def test_resolve_is_room_scoped_and_all_active():
    d = Database("sqlite://"); d.create_all()
    with d.session() as s:
        r1 = Room(name="A", invite_token="a"); r2 = Room(name="B", invite_token="b")
        s.add_all([r1, r2]); s.flush()
        s.add(Member(room_id=r1.id, display_name="An", nickname="an", pin="1", aliases=["cu An"]))
        s.add(Member(room_id=r2.id, display_name="Zed", nickname="zed", pin="9"))
        s.flush()
        got = roster.resolve(s, r1.id, names=["cu An"], mentions=[], all_active=False)
        assert len(got["resolved"]) == 1 and got["resolved"][0]["name"] == "An"
        assert [m.display_name for m in roster.list_members(s, r1.id)] == ["An"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_roster.py -v`
Expected: FAIL — `list_members()`/`resolve()` signature mismatch (no `room_id`).

- [ ] **Step 3: Write minimal implementation**

Rewrite `roster.py` around `room_id`: `list_members(session, room_id)` = `select(Member).where(Member.room_id == room_id, Member.active.is_(True))`. `resolve(session, room_id, *, names, mentions, all_active)`: match `names` against `display_name`/`nickname`/`aliases` (case-insensitive) within the room; `all_active` returns all room members; `mentions` are `{"nickname": ...}` dicts resolved within the room. Return `{"resolved": [{"id","name"}...], "unresolved": [name...]}`. Delete `capture_sender`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_roster.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/roster.py backend/tests/test_roster.py
git commit -m "feat(roster): room-scoped resolve/list; drop teams capture"
```

---

## Task 4: Tool context — room + logged-in sender

**Files:**
- Modify: `backend/app/tools.py`, `backend/app/agent.py`
- Test: `backend/tests/test_tools.py`

**Interfaces:**
- Produces: `ToolContext(db, room_id: int, sender_member_id: int | None, sender_name: str | None, turn_mentions: list[dict])`. `build_tools(ctx)` unchanged in shape; every ledger/roster call inside passes `ctx.room_id`. `record_meal` uses `payer = args.get("payer") or ctx.sender_member_id` (no more Teams capture).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_tools.py
from app.db import Database
from app.models import Room, Member
from app.tools import ToolContext, build_tools


def _ctx():
    d = Database("sqlite://"); d.create_all()
    with d.session() as s:
        r = Room(name="A", invite_token="t"); s.add(r); s.flush()
        an = Member(room_id=r.id, display_name="An", nickname="an", pin="1")
        bi = Member(room_id=r.id, display_name="Bình", nickname="binh", pin="2")
        s.add_all([an, bi]); s.flush()
        ids = (r.id, an.id, bi.id)
    return d, ids


def test_record_meal_tool_scopes_to_room_and_sender():
    d, (room_id, an, bi) = _ctx()
    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)
    out = tools["record_meal"].execute({"participants": [an, bi], "total": 100000})
    assert out["ok"] and out["payer"]["id"] == an
    assert {sh["id"]: sh["amount"] for sh in out["shares"]} == {an: 50000, bi: 50000}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_tools.py::test_record_meal_tool_scopes_to_room_and_sender -v`
Expected: FAIL — `ToolContext` has no `room_id`/`sender_member_id` fields.

- [ ] **Step 3: Write minimal implementation**

In `tools.py`: replace `ToolContext` fields with `db`, `room_id`, `sender_member_id`, `sender_name`, `turn_mentions`; delete `sender_member_id(self, session)` method and `sender_teams_id`/`sender_aad`. Update each tool to pass `ctx.room_id` into `roster.resolve(..., ctx.room_id, ...)`, `roster.list_members(s, ctx.room_id)`, `ledger.*(s, room_id=ctx.room_id, ...)`, `ledger.last_settlement(s, ctx.room_id)`. In `record_meal`: `payer = args.get("payer") or ctx.sender_member_id`; pass `source="web"`, `logged_by=str(ctx.sender_member_id)`. In `agent.py`: no logic change; it constructs no `ToolContext` itself (chat.py does), so only confirm imports still resolve.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/tools.py backend/app/agent.py backend/tests/test_tools.py
git commit -m "feat(tools): room-scoped ToolContext with logged-in sender"
```

---

## Task 5: Rooms — admin create + invite lookup

**Files:**
- Create: `backend/app/rooms.py`
- Test: `backend/tests/test_rooms.py`

**Interfaces:**
- Produces: `create_room(session, name) -> Room` (generates `invite_token = secrets.token_urlsafe(16)`); `room_by_invite(session, invite_token) -> Room | None`; `room_by_id(session, room_id) -> Room | None`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rooms.py
from app import rooms
from app.db import Database


def test_create_room_has_unguessable_token():
    d = Database("sqlite://"); d.create_all()
    with d.session() as s:
        r = rooms.create_room(s, "Lunch crew")
        assert r.name == "Lunch crew" and len(r.invite_token) >= 16
    with d.session() as s:
        assert rooms.room_by_invite(s, r.invite_token).id == r.id
        assert rooms.room_by_invite(s, "nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_rooms.py -v`
Expected: FAIL — `ModuleNotFoundError: app.rooms`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/rooms.py
from __future__ import annotations
import secrets
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Room


def create_room(session: Session, name: str) -> Room:
    room = Room(name=name.strip() or "Lunch", invite_token=secrets.token_urlsafe(16))
    session.add(room); session.flush()
    return room


def room_by_invite(session: Session, invite_token: str) -> Room | None:
    return session.scalars(select(Room).where(Room.invite_token == invite_token)).first()


def room_by_id(session: Session, room_id: int) -> Room | None:
    return session.get(Room, room_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_rooms.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/rooms.py backend/tests/test_rooms.py
git commit -m "feat(rooms): admin create + invite lookup"
```

---

## Task 6: Accounts + sessions (join / identify / profile)

**Files:**
- Create: `backend/app/accounts.py`
- Test: `backend/tests/test_accounts.py`

**Interfaces:**
- Produces: `create_account(session, room, *, display_name, nickname, pin, bank_code, account_number, account_holder) -> tuple[Member, str]` (returns member + new session token; raises `AccountError` on duplicate nickname); `identify(session, room, *, nickname, pin) -> str | None` (session token or None); `member_for_token(session, token) -> Member | None`; `update_profile(session, member, **fields) -> Member`. `AccountError(ValueError)`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_accounts.py
import pytest
from app import accounts, rooms
from app.db import Database


def _room():
    d = Database("sqlite://"); d.create_all()
    with d.session() as s:
        r = rooms.create_room(s, "A"); rid = r.id
    return d, rid


def test_join_then_identify_and_token_maps_back():
    d, rid = _room()
    with d.session() as s:
        room = rooms.room_by_id(s, rid)
        m, tok = accounts.create_account(s, room, display_name="An", nickname="an", pin="1234",
                                         bank_code="VCB", account_number="1", account_holder="AN")
        mid = m.id
    with d.session() as s:
        assert accounts.member_for_token(s, tok).id == mid
        room = rooms.room_by_id(s, rid)
        assert accounts.identify(s, room, nickname="an", pin="1234")
        assert accounts.identify(s, room, nickname="an", pin="0000") is None


def test_duplicate_nickname_rejected():
    d, rid = _room()
    with d.session() as s:
        room = rooms.room_by_id(s, rid)
        accounts.create_account(s, room, display_name="An", nickname="an", pin="1",
                                bank_code=None, account_number=None, account_holder=None)
        with pytest.raises(accounts.AccountError):
            accounts.create_account(s, room, display_name="An2", nickname="an", pin="2",
                                    bank_code=None, account_number=None, account_holder=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_accounts.py -v`
Expected: FAIL — `ModuleNotFoundError: app.accounts`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/accounts.py
from __future__ import annotations
import secrets
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Member, Room, Session as UserSession


class AccountError(ValueError):
    pass


def _new_session(session: Session, member: Member) -> str:
    tok = secrets.token_urlsafe(24)
    session.add(UserSession(member_id=member.id, token=tok)); session.flush()
    return tok


def create_account(session, room: Room, *, display_name, nickname, pin,
                   bank_code, account_number, account_holder) -> tuple[Member, str]:
    nickname = (nickname or "").strip()
    if not nickname or not (pin or "").strip():
        raise AccountError("Cần biệt danh và PIN.")
    exists = session.scalars(
        select(Member).where(Member.room_id == room.id, Member.nickname == nickname)
    ).first()
    if exists:
        raise AccountError(f"Biệt danh '{nickname}' đã có người dùng trong phòng này.")
    m = Member(room_id=room.id, display_name=(display_name or nickname).strip(),
               nickname=nickname, pin=str(pin).strip(), bank_code=bank_code,
               account_number=account_number, account_holder=account_holder)
    session.add(m); session.flush()
    return m, _new_session(session, m)


def identify(session, room: Room, *, nickname, pin) -> str | None:
    m = session.scalars(
        select(Member).where(Member.room_id == room.id, Member.nickname == (nickname or "").strip())
    ).first()
    if m is None or m.pin != str(pin).strip():
        return None
    return _new_session(session, m)


def member_for_token(session, token: str) -> Member | None:
    us = session.scalars(select(UserSession).where(UserSession.token == token)).first()
    return session.get(Member, us.member_id) if us else None


def update_profile(session, member: Member, **fields) -> Member:
    for k in ("display_name", "bank_code", "account_number", "account_holder"):
        if k in fields and fields[k] is not None:
            setattr(member, k, fields[k])
    session.flush()
    return member
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_accounts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/accounts.py backend/tests/test_accounts.py
git commit -m "feat(accounts): join/identify/profile + device sessions"
```

---

## Task 7: Auth dependency

**Files:**
- Create: `backend/app/auth.py`
- Test: `backend/tests/test_auth.py`

**Interfaces:**
- Produces: `require_session(request) -> AuthCtx` FastAPI dependency reading `Authorization: Bearer <token>`, returning `AuthCtx(member_id, room_id, display_name, nickname)`; raises `HTTPException(401)` if missing/invalid. `require_admin(x_admin_password: str | None)` dependency comparing to `settings.admin_password` (401 if unset/mismatch).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_auth.py
import pytest
from fastapi import HTTPException
from app import accounts, rooms, auth
from app.db import Database


def test_require_session_resolves_or_401(monkeypatch):
    d = Database("sqlite://"); d.create_all()
    monkeypatch.setattr(auth, "get_db", lambda: d)
    with d.session() as s:
        room = rooms.create_room(s, "A")
        _, tok = accounts.create_account(s, room, display_name="An", nickname="an", pin="1",
                                         bank_code=None, account_number=None, account_holder=None)
    ctx = auth.resolve_bearer(f"Bearer {tok}")
    assert ctx.nickname == "an"
    with pytest.raises(HTTPException):
        auth.resolve_bearer("Bearer nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: app.auth`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/auth.py
from __future__ import annotations
from dataclasses import dataclass
from fastapi import Header, HTTPException, Request
from app import accounts
from app.config import settings
from app.db import get_db


@dataclass
class AuthCtx:
    member_id: int
    room_id: int
    display_name: str
    nickname: str


def resolve_bearer(authorization: str | None) -> AuthCtx:
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing token")
    with get_db().session() as s:
        m = accounts.member_for_token(s, token)
        if m is None:
            raise HTTPException(status_code=401, detail="invalid token")
        return AuthCtx(member_id=m.id, room_id=m.room_id, display_name=m.display_name, nickname=m.nickname)


async def require_session(request: Request) -> AuthCtx:
    return resolve_bearer(request.headers.get("Authorization"))


async def require_admin(x_admin_password: str | None = Header(default=None)) -> None:
    if not settings.admin_password or x_admin_password != settings.admin_password:
        raise HTTPException(status_code=401, detail="admin only")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/auth.py backend/tests/test_auth.py
git commit -m "feat(auth): bearer session + admin-password dependencies"
```

---

## Task 8: Realtime hub (in-proc pub/sub)

**Files:**
- Create: `backend/app/realtime.py`
- Test: `backend/tests/test_realtime.py`

**Interfaces:**
- Produces: `RoomHub` with `subscribe(room_id) -> asyncio.Queue`, `unsubscribe(room_id, q)`, `async publish(room_id, event: dict)`; module singleton `hub`. Event dicts are `{"type": "message"|"bot.typing"|"bot.done", ...}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_realtime.py
import asyncio
import pytest
from app.realtime import RoomHub


@pytest.mark.asyncio
async def test_publish_fans_out_to_room_only():
    h = RoomHub()
    a = h.subscribe(1); b = h.subscribe(1); other = h.subscribe(2)
    await h.publish(1, {"type": "message", "id": 5})
    assert (await asyncio.wait_for(a.get(), 1))["id"] == 5
    assert (await asyncio.wait_for(b.get(), 1))["id"] == 5
    assert other.empty()
    h.unsubscribe(1, a)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_realtime.py -v`
Expected: FAIL — `ModuleNotFoundError: app.realtime`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/realtime.py
from __future__ import annotations
import asyncio
from collections import defaultdict


class RoomHub:
    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, room_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs[room_id].add(q)
        return q

    def unsubscribe(self, room_id: int, q: asyncio.Queue) -> None:
        self._subs[room_id].discard(q)

    async def publish(self, room_id: int, event: dict) -> None:
        for q in list(self._subs.get(room_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow client drops events; it catches up via ?since=


hub = RoomHub()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_realtime.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/realtime.py backend/tests/test_realtime.py
git commit -m "feat(realtime): per-room in-proc pub/sub hub"
```

---

## Task 9: Chat — persist, list, `@bot` detection, agent dispatch

**Files:**
- Create: `backend/app/chat.py`
- Test: `backend/tests/test_chat.py`

**Interfaces:**
- Produces: `mentions_bot(text) -> bool` (matches `@bot`/`@<bot_handle>`, case-insensitive); `post_message(session, room_id, author_member_id, body, attachments=None) -> RoomMessage`; `list_messages(session, room_id, since_id=0, limit=200) -> list[dict]`; `message_to_dict(m) -> dict`; `async run_bot_turn(db, room_id, member_id, member_name, text, images=None) -> RoomMessage` (calls `agent.run_turn` under a module `asyncio.Lock`, renders reply, persists a `kind="bot"` message). `render_bot_attachments(result: TurnResult) -> dict | None` (pulls `settle_period`/`record_meal` structured results).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_chat.py
from app import chat
from app.db import Database
from app.models import Room, Member


def test_mentions_bot():
    assert chat.mentions_bot("@bot ai trả tuần này")
    assert chat.mentions_bot("hey @Bot log 100k")
    assert not chat.mentions_bot("just chatting")


def test_post_and_list_since():
    d = Database("sqlite://"); d.create_all()
    with d.session() as s:
        r = Room(name="A", invite_token="t"); s.add(r); s.flush()
        m = Member(room_id=r.id, display_name="An", nickname="an", pin="1"); s.add(m); s.flush()
        a = chat.post_message(s, r.id, m.id, "hi")
        b = chat.post_message(s, r.id, m.id, "again")
        rows = chat.list_messages(s, r.id, since_id=a.id)
        assert [x["id"] for x in rows] == [b.id]
        assert rows[0]["author"]["nickname"] == "an"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_chat.py -v`
Expected: FAIL — `ModuleNotFoundError: app.chat`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/chat.py
from __future__ import annotations
import asyncio
import re
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import settings
from app.db import Database
from app.models import Member, RoomMessage

_agent_lock = asyncio.Lock()  # serialize agent runs (ledger single-writer)


def mentions_bot(text: str) -> bool:
    handle = re.escape(settings.bot_handle)
    return re.search(rf"@(bot|{handle})\b", text or "", re.IGNORECASE) is not None


def message_to_dict(m: RoomMessage, author: Member | None) -> dict:
    return {
        "id": m.id, "kind": m.kind, "body": m.body, "attachments": m.attachments,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "author": None if author is None else {"id": author.id, "name": author.display_name, "nickname": author.nickname},
    }


def post_message(session: Session, room_id: int, author_member_id: int | None,
                 body: str, attachments: dict | None = None, kind: str = "text") -> RoomMessage:
    m = RoomMessage(room_id=room_id, author_member_id=author_member_id, kind=kind,
                    body=body, attachments=attachments)
    session.add(m); session.flush()
    return m


def list_messages(session: Session, room_id: int, since_id: int = 0, limit: int = 200) -> list[dict]:
    rows = session.scalars(
        select(RoomMessage).where(RoomMessage.room_id == room_id, RoomMessage.id > since_id)
        .order_by(RoomMessage.id).limit(limit)
    ).all()
    authors = {m.id: m for m in session.scalars(select(Member).where(Member.room_id == room_id))}
    return [message_to_dict(r, authors.get(r.author_member_id)) for r in rows]


def render_bot_attachments(result) -> dict | None:
    settle = result.last_result("settle_period")
    if settle:
        return {"type": "settlement", **settle}
    meal = result.last_result("record_meal")
    if meal:
        return {"type": "meal", **meal}
    return None


async def run_bot_turn(db: Database, room_id: int, member_id: int, member_name: str,
                       text: str, images=None) -> RoomMessage:
    from app.agent import run_turn
    from app.tools import ToolContext
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=member_id,
                      sender_name=member_name, turn_mentions=[])
    async with _agent_lock:
        result = await run_turn(text, ctx, images=images)
    body = result.final_text or (result.error and f"⚠️ {result.error}") or "(không có phản hồi)"
    attachments = render_bot_attachments(result)
    with db.session() as s:
        return post_message(s, room_id, None, body, attachments=attachments, kind="bot")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_chat.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/chat.py backend/tests/test_chat.py
git commit -m "feat(chat): messages, @bot detection, agent dispatch"
```

---

## Task 10: HTTP surface — routers, SSE, config cleanup, delete Teams

**Files:**
- Modify: `backend/app/main.py`, `backend/app/config.py`
- Delete: `backend/app/teams.py`, `teams_parse.py`, `reply.py`, `worker.py`, `admin.py`
- Delete tests: `backend/tests/test_teams_parse.py`, `test_reply.py`, `test_worker.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: everything above. Produces HTTP routes (spec §5): `POST /api/rooms` (admin), `GET /api/rooms/{invite_token}`, `POST /api/rooms/{invite_token}/accounts`, `POST /api/rooms/{invite_token}/identify`, `GET/PUT /api/me`, `GET /api/rooms/{room_id}/messages`, `POST /api/rooms/{room_id}/messages`, `GET /api/rooms/{room_id}/stream`, plus existing `/health`, `/internal/bridge-smoke`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_api.py
from fastapi.testclient import TestClient
import app.main as main
from app import auth
from app.db import Database


def _client(monkeypatch):
    d = Database("sqlite://"); d.create_all()
    monkeypatch.setattr("app.db.get_db", lambda: d)
    monkeypatch.setattr(auth, "get_db", lambda: d)
    monkeypatch.setattr(main, "get_db", lambda: d, raising=False)
    monkeypatch.setattr(main.settings, "admin_password", "secret", raising=False)
    return TestClient(main.app), d


def test_full_join_and_post_flow(monkeypatch):
    c, d = _client(monkeypatch)
    r = c.post("/api/rooms", headers={"X-Admin-Password": "secret"}, json={"name": "Lunch"})
    assert r.status_code == 200
    token = r.json()["invite_token"]
    acc = c.post(f"/api/rooms/{token}/accounts",
                 json={"display_name": "An", "nickname": "an", "pin": "1234"})
    sess = acc.json()["token"]; room_id = acc.json()["room_id"]
    h = {"Authorization": f"Bearer {sess}"}
    assert c.post(f"/api/rooms/{room_id}/messages", headers=h, json={"body": "hi"}).status_code == 200
    msgs = c.get(f"/api/rooms/{room_id}/messages", headers=h).json()
    assert any(m["body"] == "hi" for m in msgs["messages"])
    # room isolation: no session → 401
    assert c.get(f"/api/rooms/{room_id}/messages").status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_api.py -v`
Expected: FAIL — routes don't exist / import errors from deleted Teams modules.

- [ ] **Step 3: Write minimal implementation**

Delete the five Teams modules + their tests. In `config.py`, drop `microsoft_*` and set `bot_handle` default `"bot"`. Rewrite `main.py`:

```python
# backend/app/main.py
from __future__ import annotations
import asyncio, json, logging
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app import accounts, chat, rooms
from app.auth import AuthCtx, require_admin, require_session
from app.bridge_smoke import run_bridge_smoke
from app.config import settings
from app.db import get_db
from app.realtime import hub

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="chiatienan")


class RoomIn(BaseModel):
    name: str = "Lunch"

class AccountIn(BaseModel):
    display_name: str; nickname: str; pin: str
    bank_code: str | None = None; account_number: str | None = None; account_holder: str | None = None

class IdentifyIn(BaseModel):
    nickname: str; pin: str

class MessageIn(BaseModel):
    body: str
    images: list[dict] | None = None


@app.get("/health")
async def health(): return {"status": "ok"}


@app.post("/api/rooms")
async def create_room(body: RoomIn, _=Depends(require_admin)):
    with get_db().session() as s:
        r = rooms.create_room(s, body.name)
        return {"room_id": r.id, "name": r.name, "invite_token": r.invite_token,
                "invite_link": f"https://{settings.caddy_domain}/join/{r.invite_token}"}


@app.get("/api/rooms/{invite_token}")
async def room_info(invite_token: str):
    with get_db().session() as s:
        r = rooms.room_by_invite(s, invite_token)
        if not r: raise HTTPException(404, "room not found")
        return {"room_id": r.id, "name": r.name}


@app.post("/api/rooms/{invite_token}/accounts")
async def join(invite_token: str, body: AccountIn):
    with get_db().session() as s:
        r = rooms.room_by_invite(s, invite_token)
        if not r: raise HTTPException(404, "room not found")
        try:
            m, tok = accounts.create_account(s, r, display_name=body.display_name, nickname=body.nickname,
                pin=body.pin, bank_code=body.bank_code, account_number=body.account_number,
                account_holder=body.account_holder)
        except accounts.AccountError as e:
            raise HTTPException(409, str(e))
        return {"token": tok, "room_id": r.id, "member_id": m.id}


@app.post("/api/rooms/{invite_token}/identify")
async def identify(invite_token: str, body: IdentifyIn):
    with get_db().session() as s:
        r = rooms.room_by_invite(s, invite_token)
        if not r: raise HTTPException(404, "room not found")
        tok = accounts.identify(s, r, nickname=body.nickname, pin=body.pin)
        if not tok: raise HTTPException(401, "sai biệt danh hoặc PIN")
        return {"token": tok, "room_id": r.id}


@app.get("/api/me")
async def me(ctx: AuthCtx = Depends(require_session)):
    with get_db().session() as s:
        from app.models import Member
        m = s.get(Member, ctx.member_id)
        return {"id": m.id, "display_name": m.display_name, "nickname": m.nickname,
                "bank_code": m.bank_code, "account_number": m.account_number, "account_holder": m.account_holder}


class ProfileIn(BaseModel):
    display_name: str | None = None; bank_code: str | None = None
    account_number: str | None = None; account_holder: str | None = None

@app.put("/api/me")
async def update_me(body: ProfileIn, ctx: AuthCtx = Depends(require_session)):
    with get_db().session() as s:
        from app.models import Member
        accounts.update_profile(s, s.get(Member, ctx.member_id), **body.model_dump(exclude_none=True))
    return {"ok": True}


def _check_room(ctx: AuthCtx, room_id: int):
    if ctx.room_id != room_id:
        raise HTTPException(403, "wrong room")


@app.get("/api/rooms/{room_id}/messages")
async def get_messages(room_id: int, since: int = 0, ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    with get_db().session() as s:
        return {"messages": chat.list_messages(s, room_id, since_id=since)}


@app.post("/api/rooms/{room_id}/messages")
async def post_message(room_id: int, body: MessageIn, ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    db = get_db()
    with db.session() as s:
        m = chat.post_message(s, room_id, ctx.member_id, body.body)
        from app.models import Member
        payload = chat.message_to_dict(m, s.get(Member, ctx.member_id))
    await hub.publish(room_id, {"type": "message", **payload})
    if chat.mentions_bot(body.body):
        await hub.publish(room_id, {"type": "bot.typing"})
        async def _run():
            bot_msg = await chat.run_bot_turn(db, room_id, ctx.member_id, ctx.display_name, body.body, images=body.images)
            with db.session() as s:
                out = chat.message_to_dict(bot_msg, None)
            await hub.publish(room_id, {"type": "message", **out})
            await hub.publish(room_id, {"type": "bot.done"})
        asyncio.create_task(_run())
    return {"ok": True, "id": payload["id"]}


@app.get("/api/rooms/{room_id}/stream")
async def stream(room_id: int, since: int = 0, ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    q = hub.subscribe(room_id)

    async def gen():
        try:
            with get_db().session() as s:  # catch-up
                for msg in chat.list_messages(s, room_id, since_id=since):
                    yield f"data: {json.dumps({'type': 'message', **msg})}\n\n"
            while True:
                event = await q.get()
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            hub.unsubscribe(room_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/internal/bridge-smoke")
async def bridge_smoke(request: Request):
    if not settings.admin_password or request.headers.get("X-Admin-Password") != settings.admin_password:
        raise HTTPException(401, "unauthorized")
    return await run_bridge_smoke()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest -v`
Expected: PASS (full suite green; deleted Teams tests gone).

- [ ] **Step 5: Commit**

```bash
git add -A backend
git commit -m "feat(api): rooms/accounts/chat/SSE routes; remove Teams gateway"
```

---

## Task 11: Deploy config — drop Teams env, redeploy, smoke the flow

**Files:**
- Modify: `.env.example` (remove `MICROSOFT_*`), `docker-compose.yml` (unchanged for backend; frontend added in the frontend plan)

- [ ] **Step 1:** Remove the `MICROSOFT_*`/`BOT_HANDLE` lines from `.env.example`; add `BOT_HANDLE=bot`. Commit: `git commit -am "chore(env): drop Teams vars"`.
- [ ] **Step 2:** On the droplet (from a non-office network — office blocks SSH): `ssh -i ~/.ssh/digitalocean-openclaw root@165.22.246.208 'cd /opt/chiatienan && rm -f data/chiatienan.db* && git pull && docker compose up -d --build'`.
- [ ] **Step 3:** Smoke the API end-to-end:

```bash
D=https://chiatienan.duckdns.org
TOK=$(curl -s -XPOST $D/api/rooms -H "X-Admin-Password: $ADMIN" -H 'content-type: application/json' -d '{"name":"Lunch"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["invite_token"])')
SESS=$(curl -s -XPOST $D/api/rooms/$TOK/accounts -H 'content-type: application/json' -d '{"display_name":"An","nickname":"an","pin":"1234"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s -XPOST $D/api/rooms/1/messages -H "Authorization: Bearer $SESS" -H 'content-type: application/json' -d '{"body":"@bot ghi 100k, an và binh"}'
```

Expected: room+account created; message accepted; a `kind:"bot"` reply appears in `GET /api/rooms/1/messages`.

- [ ] **Step 4: Commit** any config fixes surfaced by the smoke.

---

## Self-Review

**Spec coverage:** D1 backend (all tasks); D3 numbers-in-tools (Task 4/9 reuse `settle_period`); D4 room scoping (Tasks 1–4); D5 admin room create (Task 5/7/10); D6 SSE fan-out (Tasks 8/10); D7/D8 identity-not-auth, plaintext PIN, no rate-limit (Tasks 6/7); D9 `@bot`-only (Task 9/10); D10 deploy (Task 11). §5 API — all routes in Task 10. §8 agent room-scoped + run-to-completion — Task 4/9 (bot.delta streaming intentionally simplified to typing→complete message; noted in plan intro). §10 tests — every task.

**Placeholder scan:** none — every step has runnable code/commands.

**Type consistency:** `ToolContext(db, room_id, sender_member_id, sender_name, turn_mentions)` used identically in Tasks 4 & 9. `create_account(...) -> (Member, token)` used in Tasks 6, 7, 10. `list_messages(..., since_id=)` used in Tasks 9 & 10. Ledger signatures gain `room_id` consistently (Tasks 2, 4).
