# Two-Tier Bot Memory + `/clear` Command — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the lunch bot a per-room recent-conversation window plus a durable `memory.md` summary, and a `/clear` command that compresses the live window into that summary without deleting chat.

**Architecture:** Two tiers injected into each `@bot` turn's prompt — a per-room `memory.md` (LLM-written summaries) and a recent-message window bounded below by a `summarized_through_id` watermark and above by "now". `/clear` and a 10-week rollover both summarize aged/cleared messages into `memory.md` and advance the watermark. State is file-based (workspace) + a cosmetic `context_reset` message row — no DB migration.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy (SQLite, WAL, single writer), Cursor SDK (`cursor_sdk`), pytest + `pytest-asyncio`; Next.js / React / TypeScript frontend, vitest + React Testing Library.

## Global Constraints

- **Money-safety (D3):** `memory.md` and recent-history text are **advisory context only**. The bot never computes or re-types a tool-produced number; the summarizer prompt must forbid inventing money figures. Copy this rule verbatim into the summary prompt.
- **Single writer:** all ledger/memory writes happen under `chat._agent_lock`. Any new memory write path must hold that lock.
- **Migration-free:** no new columns on any model. `db.py` runs only `create_all()`.
- **Backward compatibility:** when both memory and recent-history are empty, the turn prompt must be **byte-identical** to today's.
- **Language:** all user-visible bot/system copy is Vietnamese.
- **All money is integer VND.** (Unchanged; no money math added here.)
- **Config values:** recent window = **10 weeks** (`MEMORY_WINDOW_WEEKS`, default `10`); recent-history safety cap = **200** messages (`HISTORY_MAX_MESSAGES`, default `200`).

---

## File Structure

- Create `backend/app/memory.py` — per-room memory file persistence (no LLM): paths, load/read/append/set-watermark, `messages_to_summarize` query.
- Create `backend/app/summarize.py` — `summarize_messages()`: one minimal Cursor call producing a plain-text Vietnamese summary.
- Create `backend/tests/test_memory.py`, `backend/tests/test_summarize.py`.
- Modify `backend/app/config.py` — add `memory_window_weeks`, `history_max_messages`.
- Modify `backend/app/agent.py` — add `_render_prompt(...)`, extend `run_turn(..., memory=None, history=None)`.
- Modify `backend/app/chat.py` — add `is_clear_command`, `_render_messages`, `build_history`, `clear_context`, `_maybe_rollover`; wire memory into `run_bot_turn` (new `before_id` kwarg).
- Modify `backend/app/main.py` — intercept `/clear` in the POST `/messages` route; pass `before_id` into `run_bot_turn`.
- Modify `backend/tests/test_chat.py`, `backend/tests/test_agent.py`, `backend/tests/test_api.py`, `backend/tests/test_config.py`.
- Modify `frontend/src/components/chat/message-list.tsx` — render `kind="context_reset"` divider.
- Create `frontend/src/components/chat/__tests__/message-list.test.tsx`.

Run backend tests from `backend/` with the project venv: `cd backend && .venv/bin/pytest`.
Run frontend tests from `frontend/` with `npm test`.

---

### Task 1: Config settings

**Files:**
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `settings.memory_window_weeks: int` (default 10), `settings.history_max_messages: int` (default 200).

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_config.py`:

```python
def test_memory_settings_defaults(monkeypatch):
    monkeypatch.delenv("MEMORY_WINDOW_WEEKS", raising=False)
    monkeypatch.delenv("HISTORY_MAX_MESSAGES", raising=False)
    from app.config import Settings
    s = Settings.from_env()
    assert s.memory_window_weeks == 10
    assert s.history_max_messages == 200


def test_memory_settings_from_env(monkeypatch):
    monkeypatch.setenv("MEMORY_WINDOW_WEEKS", "6")
    monkeypatch.setenv("HISTORY_MAX_MESSAGES", "50")
    from app.config import Settings
    s = Settings.from_env()
    assert s.memory_window_weeks == 6
    assert s.history_max_messages == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_config.py -k memory_settings -v`
Expected: FAIL (`TypeError`/`AttributeError` — field does not exist).

- [ ] **Step 3: Implement**

In `backend/app/config.py`, add two fields to the `Settings` dataclass (after `max_seconds`, in the "Cursor SDK" block):

```python
    max_seconds: int
    memory_window_weeks: int
    history_max_messages: int
```

And in `from_env(...)` (after the `max_seconds=...` line):

```python
            max_seconds=_int_env("CURSOR_AGENT_MAX_SECONDS", 120),
            memory_window_weeks=_int_env("MEMORY_WINDOW_WEEKS", 10),
            history_max_messages=_int_env("HISTORY_MAX_MESSAGES", 200),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/test_config.py
git commit -m "feat(be): add memory_window_weeks + history_max_messages settings"
```

---

### Task 2: `memory.py` — per-room memory persistence

**Files:**
- Create: `backend/app/memory.py`
- Test: `backend/tests/test_memory.py`

**Interfaces:**
- Consumes: `app.config.settings`, `app.clock.now_ict`, `app.models.RoomMessage`.
- Produces:
  - `_base_dir() -> pathlib.Path` — returns `Path(settings.cursor_workspace)` (indirection point tests monkeypatch for isolation).
  - `room_memory_dir(room_id: int) -> Path` — ensures `{_base_dir()}/rooms/{room_id}/` exists, returns it.
  - `load_memory(room_id: int) -> str` — contents of `memory.md`, or `""`.
  - `read_watermark(room_id: int) -> int` — `summarized_through_id` from `memory.meta.json`, or `0`.
  - `set_watermark(room_id: int, *, through_id: int, through_at: str) -> None`.
  - `append_summary(room_id: int, *, summary_text: str, through_id: int, through_at: str, header: str) -> None` — appends a dated section to `memory.md`, then `set_watermark`.
  - `messages_to_summarize(session, room_id: int, *, watermark: int, older_than=None, before_id=None) -> list[RoomMessage]`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_memory.py`:

```python
from datetime import timedelta

import pytest

from app import memory as mem
from app.clock import now_ict
from tests.test_ledger import _seed_room


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path)
    return tmp_path


def test_load_and_watermark_default_when_absent(workspace):
    assert mem.load_memory(1) == ""
    assert mem.read_watermark(1) == 0


def test_append_summary_writes_section_and_advances_watermark(workspace):
    mem.append_summary(1, summary_text="- An trả 100k", through_id=5,
                       through_at="2026-07-21T10:00:00+07:00", header="Xoá ngữ cảnh")
    body = mem.load_memory(1)
    assert "## Xoá ngữ cảnh" in body
    assert "An trả 100k" in body
    assert mem.read_watermark(1) == 5

    # second append accumulates and moves the watermark forward
    mem.append_summary(1, summary_text="- Bình trả 50k", through_id=9,
                       through_at="2026-07-28T10:00:00+07:00", header="Xoá ngữ cảnh")
    body2 = mem.load_memory(1)
    assert "An trả 100k" in body2 and "Bình trả 50k" in body2
    assert mem.read_watermark(1) == 9


def test_set_watermark_without_append(workspace):
    mem.set_watermark(1, through_id=3, through_at="2026-07-21T10:00:00+07:00")
    assert mem.read_watermark(1) == 3
    assert mem.load_memory(1) == ""


def test_messages_to_summarize_filters(workspace, db):
    from app import chat
    room_id, m = _seed_room(db, 2)
    old = now_ict() - timedelta(weeks=20)
    with db.session() as s:
        m1 = chat.post_message(s, room_id, m[0], "xin chào")
        m2 = chat.post_message(s, room_id, None, "chào bạn", kind="bot")
        m3 = chat.post_message(s, room_id, m[1], "/clear")
        div = chat.post_message(s, room_id, None, "reset", kind="context_reset")
        # backdate the first two so the rollover filter can catch them
        m1.created_at = old
        m2.created_at = old
        s.flush()
        wm0 = mem.read_watermark(room_id)
        # watermark filter + kind filter (context_reset excluded)
        rows = mem.messages_to_summarize(s, room_id, watermark=wm0)
        ids = [r.id for r in rows]
        assert m1.id in ids and m2.id in ids and m3.id in ids
        assert div.id not in ids
        # before_id excludes the /clear line
        rows_b = mem.messages_to_summarize(s, room_id, watermark=wm0, before_id=m3.id)
        assert m3.id not in [r.id for r in rows_b]
        # older_than catches only the backdated pair
        rows_old = mem.messages_to_summarize(s, room_id, watermark=wm0,
                                             older_than=now_ict() - timedelta(weeks=10))
        assert [r.id for r in rows_old] == [m1.id, m2.id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_memory.py -v`
Expected: FAIL (`ModuleNotFoundError: app.memory`).

- [ ] **Step 3: Implement `backend/app/memory.py`**

```python
"""Per-room long-term memory files (no LLM here).

Two files live under ``{cursor_workspace}/rooms/{room_id}/``:

- ``memory.md``       — human-readable summary sections, appended over time.
- ``memory.meta.json``— ``{"summarized_through_id": int, "summarized_through_at": str}``.

The ``summarized_through_id`` watermark is the lower bound of the recent-message
window fed to the agent (:mod:`app.chat`). Both ``/clear`` and the 10-week
rollover advance it. All writes happen under ``chat._agent_lock`` (single writer).
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.models import RoomMessage

_META_NAME = "memory.meta.json"
_MD_NAME = "memory.md"


def _base_dir() -> Path:
    """Workspace root; indirection so tests can redirect memory files."""
    return Path(settings.cursor_workspace)


def room_memory_dir(room_id: int) -> Path:
    d = _base_dir() / "rooms" / str(room_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_memory(room_id: int) -> str:
    path = room_memory_dir(room_id) / _MD_NAME
    return path.read_text(encoding="utf-8") if path.exists() else ""


def read_watermark(room_id: int) -> int:
    path = room_memory_dir(room_id) / _META_NAME
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("summarized_through_id", 0))
    except (ValueError, TypeError, json.JSONDecodeError):
        return 0


def set_watermark(room_id: int, *, through_id: int, through_at: str) -> None:
    path = room_memory_dir(room_id) / _META_NAME
    path.write_text(
        json.dumps({"summarized_through_id": through_id, "summarized_through_at": through_at}),
        encoding="utf-8",
    )


def append_summary(room_id: int, *, summary_text: str, through_id: int,
                   through_at: str, header: str) -> None:
    date_str = through_at[:10]
    section = f"\n## {header} — {date_str}\n{summary_text.strip()}\n"
    path = room_memory_dir(room_id) / _MD_NAME
    with path.open("a", encoding="utf-8") as f:
        f.write(section)
    set_watermark(room_id, through_id=through_id, through_at=through_at)


def messages_to_summarize(session, room_id: int, *, watermark: int,
                          older_than=None, before_id=None) -> list[RoomMessage]:
    """Chat rows eligible for summarization: ``id > watermark``, text/bot only,
    ordered by id. ``older_than`` (datetime) keeps only ``created_at <
    older_than`` (rollover); ``before_id`` keeps only ``id < before_id``
    (exclude the triggering ``/clear`` line)."""
    q = (
        select(RoomMessage)
        .where(
            RoomMessage.room_id == room_id,
            RoomMessage.id > watermark,
            RoomMessage.kind.in_(("text", "bot")),
        )
        .order_by(RoomMessage.id)
    )
    if older_than is not None:
        q = q.where(RoomMessage.created_at < older_than)
    if before_id is not None:
        q = q.where(RoomMessage.id < before_id)
    return list(session.scalars(q).all())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_memory.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/memory.py backend/tests/test_memory.py
git commit -m "feat(be): per-room memory.md persistence + summarize query"
```

---

### Task 3: `chat.is_clear_command`, `_render_messages`, `build_history`

**Files:**
- Modify: `backend/app/chat.py`
- Test: `backend/tests/test_chat.py`

**Interfaces:**
- Consumes: `app.config.settings.bot_handle`, `app.models.Member`/`RoomMessage`, `sqlalchemy.select`.
- Produces:
  - `is_clear_command(text: str) -> bool`.
  - `_render_messages(session, room_id: int, rows: list[RoomMessage], *, clamp: int = 500) -> str`.
  - `build_history(session, room_id: int, *, watermark: int = 0, before_id: int | None = None, limit: int = 200) -> str`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_chat.py` (keep existing imports; ensure `from app import chat` and `from tests.test_ledger import _seed_room` are present):

```python
import pytest


@pytest.mark.parametrize("text,expected", [
    ("/clear", True),
    ("  /clear  ", True),
    ("/CLEAR", True),
    ("@bot /clear", True),
    ("@bot   /clear", True),
    ("/cleared", False),
    ("/clear now", False),
    ("clear", False),
    ("", False),
    ("please /clear", False),
])
def test_is_clear_command(text, expected):
    assert chat.is_clear_command(text) is expected


def test_build_history_renders_window(db):
    room_id, m = _seed_room(db, 2)  # M1, M2
    with db.session() as s:
        a = chat.post_message(s, room_id, m[0], "840k cả nhóm")
        b = chat.post_message(s, room_id, None, "Đã ghi #1", kind="bot")
        chat.post_message(s, room_id, None, "reset", kind="context_reset")  # skipped
        cur = chat.post_message(s, room_id, m[1], "@bot ai trả")            # excluded (before_id)
        out = chat.build_history(s, room_id, watermark=0, before_id=cur.id, limit=200)
    assert out == "«M1»: 840k cả nhóm\nchiatienan: Đã ghi #1"


def test_build_history_respects_watermark_and_limit(db):
    room_id, m = _seed_room(db, 1)
    with db.session() as s:
        first = chat.post_message(s, room_id, m[0], "một")
        chat.post_message(s, room_id, m[0], "hai")
        chat.post_message(s, room_id, m[0], "ba")
        # watermark drops "một"; limit keeps the most recent 1 -> "ba"
        out = chat.build_history(s, room_id, watermark=first.id, before_id=None, limit=1)
    assert out == "«M1»: ba"


def test_build_history_empty_returns_blank(db):
    room_id, _ = _seed_room(db, 1)
    with db.session() as s:
        assert chat.build_history(s, room_id, watermark=0, before_id=None, limit=200) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_chat.py -k "clear_command or build_history" -v`
Expected: FAIL (`AttributeError: module 'app.chat' has no attribute 'is_clear_command'`).

- [ ] **Step 3: Implement in `backend/app/chat.py`**

Add near `mentions_bot` (the `re` and `select`/`Member`/`RoomMessage` imports already exist at the top of the file):

```python
_CLEAR_RE = re.compile(
    rf"^\s*(?:@(?:bot|{re.escape(settings.bot_handle)})\s+)?/clear\s*$",
    re.IGNORECASE,
)


def is_clear_command(text: str) -> bool:
    """True iff the whole message is the ``/clear`` command (optionally preceded
    by an ``@bot``/``@<handle>`` mention). Exact — ``/cleared``/``/clear now``
    do not match."""
    return _CLEAR_RE.match(text or "") is not None
```

Add these two functions (near `list_messages`):

```python
def _render_messages(session: Session, room_id: int, rows, *, clamp: int = 500) -> str:
    """Render chat rows as ``«Name»: body`` / ``chiatienan: body`` lines,
    oldest→newest, each body clamped. Empty rows → ``""``."""
    if not rows:
        return ""
    authors = {a.id: a for a in session.scalars(select(Member).where(Member.room_id == room_id))}
    lines = []
    for r in rows:
        body = (r.body or "").strip()
        if len(body) > clamp:
            body = body[:clamp] + "…"
        if r.author_member_id is None:
            lines.append(f"chiatienan: {body}")
        else:
            author = authors.get(r.author_member_id)
            lines.append(f"«{author.display_name if author else '?'}»: {body}")
    return "\n".join(lines)


def build_history(session: Session, room_id: int, *, watermark: int = 0,
                  before_id: int | None = None, limit: int = 200) -> str:
    """Recent conversation fed to the agent: ``watermark < id [< before_id]``,
    text/bot kinds only, most-recent ``limit`` rows rendered oldest→newest."""
    q = select(RoomMessage).where(
        RoomMessage.room_id == room_id,
        RoomMessage.id > watermark,
        RoomMessage.kind.in_(("text", "bot")),
    )
    if before_id is not None:
        q = q.where(RoomMessage.id < before_id)
    rows = session.scalars(q.order_by(RoomMessage.id.desc()).limit(limit)).all()
    return _render_messages(session, room_id, list(reversed(rows)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_chat.py -v`
Expected: PASS (all, including new tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/chat.py backend/tests/test_chat.py
git commit -m "feat(be): /clear matcher + recent-history renderer"
```

---

### Task 4: `agent._render_prompt` + `run_turn` memory/history params

**Files:**
- Modify: `backend/app/agent.py`
- Test: `backend/tests/test_agent.py`

**Interfaces:**
- Produces: `_render_prompt(user_text: str, *, sender_name: str | None = None, memory: str | None = None, history: str | None = None) -> str`.
- Changes: `run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None)` — same return type `TurnResult`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_agent.py`:

```python
from app.agent import _render_prompt
from app.prompt import build_system_prompt


def test_render_prompt_baseline_unchanged():
    # No memory/history → identical to the pre-memory assembly.
    expected = f"{build_system_prompt(sender_name='An')}\n\n# Tin nhắn người dùng\nxin chào"
    assert _render_prompt("  xin chào  ", sender_name="An") == expected


def test_render_prompt_includes_sections_in_order():
    out = _render_prompt("ai trả", sender_name="An",
                         memory="- An hay trả", history="«An»: hôm qua 100k")
    assert "# Bộ nhớ dài hạn\n- An hay trả" in out
    assert "# Lịch sử hội thoại (gần đây)\n«An»: hôm qua 100k" in out
    # order: memory before history before the user message
    assert out.index("Bộ nhớ dài hạn") < out.index("Lịch sử hội thoại") < out.index("Tin nhắn người dùng")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_agent.py -k render_prompt -v`
Expected: FAIL (`ImportError: cannot import name '_render_prompt'`).

- [ ] **Step 3: Implement in `backend/app/agent.py`**

Add near the top-level helpers (after `_assistant_text`):

```python
def _render_prompt(user_text: str, *, sender_name: str | None = None,
                   memory: str | None = None, history: str | None = None) -> str:
    """Assemble the turn preamble. With no memory/history this is byte-identical
    to the pre-memory assembly (system prompt + user message)."""
    sections = [build_system_prompt(sender_name=sender_name)]
    if memory:
        sections.append(f"# Bộ nhớ dài hạn\n{memory.strip()}")
    if history:
        sections.append(f"# Lịch sử hội thoại (gần đây)\n{history.strip()}")
    sections.append(f"# Tin nhắn người dùng\n{user_text.strip()}")
    return "\n\n".join(sections)
```

In `run_turn`, change the signature:

```python
async def run_turn(user_text: str, ctx: ToolContext, images=None, emit=None,
                    memory=None, history=None) -> TurnResult:
```

And replace the two prompt-building lines:

```python
        prompt = build_system_prompt(sender_name=ctx.sender_name)
        message_text = f"{prompt}\n\n# Tin nhắn người dùng\n{user_text.strip()}"
```

with:

```python
        message_text = _render_prompt(user_text, sender_name=ctx.sender_name,
                                       memory=memory, history=history)
```

(The `from app.prompt import build_system_prompt` import at the top of the module stays — `_render_prompt` uses it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_agent.py -v`
Expected: PASS (existing + 2 new). The existing `test_run_turn_collects_text_and_tool_results` still passes (baseline prompt unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent.py backend/tests/test_agent.py
git commit -m "feat(be): run_turn accepts memory + recent-history sections"
```

---

### Task 5: `summarize.py` — minimal Cursor summarization call

**Files:**
- Create: `backend/app/summarize.py`
- Test: `backend/tests/test_summarize.py`

**Interfaces:**
- Consumes: `app.agent._assistant_text`, `app.agent._ensure_workspace`, `app.agent._launch_bridge_resilient`; `app.cursor_runner.{resolve_cursor_api_key, resolve_model_selection, default_cursor_model}`.
- Produces: `async summarize_messages(rendered_history: str, *, kind: str = "clear") -> str` — plain-text summary, or `""` on empty input / any failure.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_summarize.py` (reuses the fake-client shapes from `test_agent.py`):

```python
import types

import pytest

import app.summarize as summarize_mod
from app.summarize import summarize_messages
from tests.test_agent import _FakeClient, _FakeRun, _text_msg


@pytest.mark.asyncio
async def test_summarize_empty_input_skips_llm():
    assert await summarize_messages("   ", kind="clear") == ""


@pytest.mark.asyncio
async def test_summarize_returns_assistant_text(monkeypatch):
    fake_run = _FakeRun([_text_msg("- An trả 100k\n- Còn nợ Bình 50k")])

    monkeypatch.setattr(summarize_mod, "_ensure_workspace", lambda: "/tmp/chiatienan-test")
    monkeypatch.setattr("app.cursor_runner.resolve_cursor_api_key", lambda *a, **k: "k", raising=False)
    monkeypatch.setattr(
        "app.cursor_runner.resolve_model_selection",
        lambda *a, **k: types.SimpleNamespace(id="composer-2.5", params=None), raising=False,
    )

    async def _fake_launch(AsyncClient, workspace, local):
        return _FakeClient(fake_run)

    monkeypatch.setattr(summarize_mod, "_launch_bridge_resilient", _fake_launch)

    out = await summarize_messages("«An»: 100k cả nhóm", kind="clear")
    assert "An trả 100k" in out


@pytest.mark.asyncio
async def test_summarize_returns_blank_on_failure(monkeypatch):
    monkeypatch.setattr(summarize_mod, "_ensure_workspace", lambda: "/tmp/chiatienan-test")
    monkeypatch.setattr("app.cursor_runner.resolve_cursor_api_key", lambda *a, **k: "k", raising=False)
    monkeypatch.setattr(
        "app.cursor_runner.resolve_model_selection",
        lambda *a, **k: types.SimpleNamespace(id="composer-2.5", params=None), raising=False,
    )

    async def _boom(*a, **k):
        raise RuntimeError("bridge dead")

    monkeypatch.setattr(summarize_mod, "_launch_bridge_resilient", _boom)
    assert await summarize_messages("«An»: 100k", kind="rollover") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_summarize.py -v`
Expected: FAIL (`ModuleNotFoundError: app.summarize`).

- [ ] **Step 3: Implement `backend/app/summarize.py`**

```python
"""Summarize a chunk of room conversation into durable ``memory.md`` text.

One minimal Cursor call (no custom tools). Advisory only: the summary is context
for future turns, NEVER a source of money numbers (design D3) — the prompt says
so explicitly. Reuses :mod:`app.agent`'s workspace + resilient-launch helpers so
tests mock it exactly like ``run_turn``.
"""
from __future__ import annotations

import asyncio
import logging
import os

from app.agent import _assistant_text, _ensure_workspace, _launch_bridge_resilient
from app.cursor_runner import (
    default_cursor_model,
    resolve_cursor_api_key,
    resolve_model_selection,
)

logger = logging.getLogger("chiatienan")

_SUMMARY_PROMPT = (
    "Bạn đang tóm tắt lịch sử một nhóm chat chia tiền ăn trưa để làm bộ nhớ dài hạn.\n"
    "Tóm tắt NGẮN GỌN bằng tiếng Việt, 5–10 gạch đầu dòng: các bữa ăn đã ghi, ai trả, "
    "ai nợ ai, các quyết định và ngữ cảnh đáng nhớ.\n"
    "TUYỆT ĐỐI KHÔNG bịa hay tự tính số tiền — chỉ ghi lại con số đã xuất hiện rõ trong "
    "hội thoại. Đây chỉ là bộ nhớ tham khảo, không phải sổ cái.\n\n"
    "# Hội thoại cần tóm tắt\n"
)


async def summarize_messages(rendered_history: str, *, kind: str = "clear") -> str:
    if not rendered_history.strip():
        return ""
    from cursor_sdk import (
        AgentOptions,
        AsyncClient,
        LocalAgentOptions,
        LocalSendOptions,
        SendOptions,
    )

    try:
        workspace = _ensure_workspace()
        api_key = resolve_cursor_api_key()
        selection = await asyncio.to_thread(
            resolve_model_selection, api_key, default_cursor_model(), "medium"
        )
        message_text = _SUMMARY_PROMPT + rendered_history
        local = LocalAgentOptions(
            cwd=workspace,
            custom_tools=[],
            store={"type": "sqlite", "root_dir": os.path.join(workspace, ".cursor-store")},
        )
        options = AgentOptions(model=selection, api_key=api_key, local=local, mcp_servers={})
        parts: list[str] = []
        client = await _launch_bridge_resilient(AsyncClient, workspace, local)
        async with client:
            async with await client.agents.create(options) as agent:
                run = await agent.send(
                    message_text, SendOptions(model=selection, local=LocalSendOptions(force=True))
                )
                async for msg in run.messages():
                    if getattr(msg, "type", None) == "assistant":
                        parts.append(_assistant_text(msg))
        return "".join(parts).strip()
    except Exception:  # noqa: BLE001 — a failed summary must degrade, never crash a turn
        logger.exception("[summarize] kind=%s failed", kind)
        return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_summarize.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/summarize.py backend/tests/test_summarize.py
git commit -m "feat(be): summarize_messages via minimal Cursor call"
```

---

### Task 6: `chat.clear_context` + rollover wiring in `run_bot_turn`

**Files:**
- Modify: `backend/app/chat.py`
- Test: `backend/tests/test_chat.py`

**Interfaces:**
- Consumes: `app.memory`, `app.summarize.summarize_messages`, `app.clock.now_ict`, `settings.{memory_window_weeks, history_max_messages}`, `build_history`, `_render_messages`, `_agent_lock`, `post_message`, `message_to_dict`.
- Produces:
  - `async _maybe_rollover(db: Database, room_id: int) -> None`.
  - `async clear_context(db: Database, room_id: int, *, up_to_id: int, emit=None) -> RoomMessage`.
- Changes: `run_bot_turn(..., before_id: int | None = None)` — builds/injects memory + history and runs rollover first.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_chat.py`:

```python
from datetime import timedelta

from app import memory as mem
from app.clock import now_ict
from app.db import Database


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_clear_context_summarizes_and_resets(db, ws, monkeypatch):
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        chat.post_message(s, room_id, m[0], "840k cả nhóm")
        chat.post_message(s, room_id, None, "Đã ghi #1", kind="bot")
        clear_line = chat.post_message(s, room_id, m[1], "/clear")
        clear_id = clear_line.id

    seen = {}

    async def fake_summarize(rendered, *, kind="clear"):
        seen["rendered"] = rendered
        seen["kind"] = kind
        return "- An trả 840k cho cả nhóm"

    monkeypatch.setattr("app.chat.summarize_messages", fake_summarize, raising=False)

    div = await chat.clear_context(db, room_id, up_to_id=clear_id)

    assert div.kind == "context_reset"
    assert seen["kind"] == "clear"
    # the /clear line itself is excluded from the summarized text
    assert "840k cả nhóm" in seen["rendered"] and "/clear" not in seen["rendered"]
    assert "An trả 840k" in mem.load_memory(room_id)
    assert mem.read_watermark(room_id) == clear_id


@pytest.mark.asyncio
async def test_clear_context_posts_divider_even_when_summary_blank(db, ws, monkeypatch):
    room_id, m = _seed_room(db, 1)
    with db.session() as s:
        chat.post_message(s, room_id, m[0], "một")
        clear_line = chat.post_message(s, room_id, m[0], "/clear")
        clear_id = clear_line.id

    async def blank_summarize(rendered, *, kind="clear"):
        return ""

    monkeypatch.setattr("app.chat.summarize_messages", blank_summarize, raising=False)

    div = await chat.clear_context(db, room_id, up_to_id=clear_id)
    assert div.kind == "context_reset"
    assert mem.load_memory(room_id) == ""          # nothing appended
    assert mem.read_watermark(room_id) == clear_id  # but window still reset


@pytest.mark.asyncio
async def test_maybe_rollover_folds_aged_messages(db, ws, monkeypatch):
    room_id, m = _seed_room(db, 1)
    with db.session() as s:
        old1 = chat.post_message(s, room_id, m[0], "cũ 1")
        old2 = chat.post_message(s, room_id, m[0], "cũ 2")
        recent = chat.post_message(s, room_id, m[0], "mới")
        old1.created_at = now_ict() - timedelta(weeks=20)
        old2.created_at = now_ict() - timedelta(weeks=20)
        s.flush()
        aged_id = old2.id
        recent_id = recent.id

    calls = {}

    async def fake_summarize(rendered, *, kind="clear"):
        calls["kind"] = kind
        calls["rendered"] = rendered
        return "- tóm tắt cũ"

    monkeypatch.setattr("app.chat.summarize_messages", fake_summarize, raising=False)

    await chat._maybe_rollover(db, room_id)

    assert calls["kind"] == "rollover"
    assert "cũ 1" in calls["rendered"] and "mới" not in calls["rendered"]
    assert mem.read_watermark(room_id) == aged_id
    assert "tóm tắt cũ" in mem.load_memory(room_id)
    # the recent message survives in the window
    with db.session() as s:
        hist = chat.build_history(s, room_id, watermark=mem.read_watermark(room_id),
                                  before_id=None, limit=200)
    assert "mới" in hist and "cũ 1" not in hist
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_chat.py -k "clear_context or rollover" -v`
Expected: FAIL (`AttributeError: ... 'clear_context'`).

- [ ] **Step 3: Implement in `backend/app/chat.py`**

Add the module import near the top (with the other `from app...` imports):

```python
from datetime import timedelta

from app import memory
from app.summarize import summarize_messages
from app.clock import now_ict
```

Add the two functions (place after `run_bot_turn`, or before it — order doesn't matter):

```python
async def _maybe_rollover(db: Database, room_id: int) -> None:
    """Fold messages older than the recent window into ``memory.md`` and advance
    the watermark. No-op when nothing has aged out. Caller holds ``_agent_lock``."""
    cutoff = now_ict() - timedelta(weeks=settings.memory_window_weeks)
    with db.session() as s:
        wm = memory.read_watermark(room_id)
        aged = memory.messages_to_summarize(s, room_id, watermark=wm, older_than=cutoff)
        if not aged:
            return
        through_id = aged[-1].id
        rendered = _render_messages(s, room_id, aged)
    summary = await summarize_messages(rendered, kind="rollover")
    if summary:
        memory.append_summary(room_id, summary_text=summary, through_id=through_id,
                              through_at=now_ict().isoformat(), header="Tự động lưu (cũ hơn 10 tuần)")
    # On a blank/failed summary we leave the watermark untouched so the aged
    # messages are retried next turn — never silently dropped.


async def clear_context(db: Database, room_id: int, *, up_to_id: int, emit=None) -> RoomMessage:
    """Handle ``/clear``: summarize the live window into ``memory.md``, advance
    the watermark to ``up_to_id`` (the ``/clear`` line), and post a visible
    ``context_reset`` divider. Serialized by ``_agent_lock``."""
    async with _agent_lock:
        with db.session() as s:
            wm = memory.read_watermark(room_id)
            rows = memory.messages_to_summarize(s, room_id, watermark=wm, before_id=up_to_id)
            rendered = _render_messages(s, room_id, rows)
        summary = await summarize_messages(rendered, kind="clear") if rendered else ""
        now_iso = now_ict().isoformat()
        if summary:
            memory.append_summary(room_id, summary_text=summary, through_id=up_to_id,
                                  through_at=now_iso, header="Xoá ngữ cảnh")
        else:
            # No summary (empty window or summarizer failure) — still reset the
            # window; the user explicitly asked to clear.
            memory.set_watermark(room_id, through_id=up_to_id, through_at=now_iso)
        with db.session() as s:
            div = post_message(s, room_id, None,
                               "🧹 Đã lưu tóm tắt vào bộ nhớ; ngữ cảnh đã xoá.",
                               kind="context_reset")
    return div
```

Now wire `run_bot_turn`. Change its signature to add `before_id`:

```python
async def run_bot_turn(db: Database, room_id: int, member_id: int, member_name: str,
                        text: str, images=None, emit=None, before_id: int | None = None) -> RoomMessage:
```

Inside `run_bot_turn`, **within** `async with _agent_lock:` and **before** `result = await run_turn(...)`, insert the rollover + context build, then pass memory/history into `run_turn`:

```python
    async with _agent_lock:
        await _maybe_rollover(db, room_id)
        mem_text = memory.load_memory(room_id)
        with db.session() as s:
            history = build_history(
                s, room_id, watermark=memory.read_watermark(room_id),
                before_id=before_id, limit=settings.history_max_messages,
            )
        result = await run_turn(text, ctx, images=images, emit=emit,
                                memory=mem_text or None, history=history or None)
        # ... existing proposal / attachments handling unchanged ...
```

(Leave everything after `result = await run_turn(...)` exactly as it is.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_chat.py -v`
Expected: PASS (all).

- [ ] **Step 5: Run the full backend suite (no regressions)**

Run: `cd backend && .venv/bin/pytest -q`
Expected: PASS (whole suite green).

- [ ] **Step 6: Commit**

```bash
git add backend/app/chat.py backend/tests/test_chat.py
git commit -m "feat(be): clear_context + 10-week rollover wired into bot turns"
```

---

### Task 7: Route intercept for `/clear` + `before_id` threading

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `chat.is_clear_command`, `chat.clear_context`, `chat.run_bot_turn(before_id=...)`, `hub.publish`, the `_BG` task set.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_api.py` (reuse the existing `client` fixture + `_room`/`_join` helpers; `main` is already imported in that file):

```python
def test_clear_command_posts_divider_and_skips_bot(client, monkeypatch):
    token = _room(client)
    sess, room_id = _join(client, token, "an")
    headers = {"Authorization": f"Bearer {sess}"}

    called = {"clear": 0, "bot": 0}

    async def fake_clear(db, rid, *, up_to_id, emit=None):
        called["clear"] += 1
        from app import chat
        with db.session() as s:
            return chat.post_message(s, rid, None, "🧹 reset", kind="context_reset")

    async def fake_bot(*a, **k):
        called["bot"] += 1

    monkeypatch.setattr("app.chat.clear_context", fake_clear, raising=False)
    monkeypatch.setattr("app.chat.run_bot_turn", fake_bot, raising=False)

    r = client.post(f"/api/rooms/{room_id}/messages", json={"body": "/clear"}, headers=headers)
    assert r.status_code == 200

    # allow the spawned background task to run
    import time
    for _ in range(50):
        if called["clear"]:
            break
        time.sleep(0.02)
    assert called["clear"] == 1
    assert called["bot"] == 0
```

The `client` fixture points `main` at the test `db` (see the top of `test_api.py`), so the route's `get_db()` and the patched `chat.clear_context` share it. Keep the assertion focused: `/clear` triggers `clear_context`, not `run_bot_turn`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_api.py -k clear_command -v`
Expected: FAIL (route still routes `/clear` through the normal path; `clear_context` not called).

- [ ] **Step 3: Implement in `backend/app/main.py`**

In `post_message` (the POST `/api/rooms/{room_id}/messages` handler), replace the `if chat.mentions_bot(body.body):` block. Insert the `/clear` branch first, and add `before_id=payload["id"]` to the existing `run_bot_turn` call:

```python
    if chat.is_clear_command(body.body):
        await hub.publish(room_id, {"type": "bot.typing"})

        async def _run_clear():
            async def emit(ev):
                await hub.publish(room_id, ev)
            try:
                div = await chat.clear_context(db, room_id, up_to_id=payload["id"], emit=emit)
                await hub.publish(room_id, {"type": "message", **chat.message_to_dict(div, None)})
            except Exception:  # noqa: BLE001
                log.exception("clear_context failed in room %s", room_id)
            finally:
                await hub.publish(room_id, {"type": "bot.done"})

        t = asyncio.create_task(_run_clear())
        _BG.add(t)
        t.add_done_callback(_BG.discard)
        return {"ok": True, "id": payload["id"]}

    if chat.mentions_bot(body.body):
        await hub.publish(room_id, {"type": "bot.typing"})

        async def _run():
            async def emit(ev):
                await hub.publish(room_id, ev)

            try:
                bot_msg = await chat.run_bot_turn(
                    db, room_id, ctx.member_id, ctx.display_name, body.body,
                    images=clean, emit=emit, before_id=payload["id"],
                )
                await hub.publish(room_id, {"type": "message", **chat.message_to_dict(bot_msg, None)})
            except Exception:  # noqa: BLE001 — never leave the room stuck
                log.exception("bot turn failed in room %s", room_id)
                try:
                    with db.session() as s:
                        err = chat.post_message(
                            s, room_id, None, "⚠️ The bot hit an error, please try again later.", kind="bot",
                        )
                        out = chat.message_to_dict(err, None)
                    await hub.publish(room_id, {"type": "message", **out})
                except Exception:  # noqa: BLE001
                    log.exception("failed to post bot error message in room %s", room_id)
            finally:
                await hub.publish(room_id, {"type": "bot.done"})

        t = asyncio.create_task(_run())
        _BG.add(t)
        t.add_done_callback(_BG.discard)
```

(Only the `before_id=payload["id"]` addition and the new `_run_clear` branch are changes; the rest of `_run` is copied verbatim from the existing handler.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_api.py -k clear_command -v`
Expected: PASS.

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && .venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_api.py
git commit -m "feat(be): route /clear to clear_context; feed history into bot turns"
```

---

### Task 8: Frontend — `context_reset` divider

**Files:**
- Modify: `frontend/src/components/chat/message-list.tsx`
- Test: `frontend/src/components/chat/__tests__/message-list.test.tsx`

**Interfaces:**
- Consumes: the existing `Message` type in `message-list.tsx` (has `kind`, `body`).

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/chat/__tests__/message-list.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageList } from "../message-list";

describe("MessageList context_reset divider", () => {
  it("renders a context_reset message as a centered divider showing its body", () => {
    const messages = [
      { id: 1, kind: "text", body: "hello", author: { id: 5, name: "An" } },
      { id: 2, kind: "context_reset", body: "🧹 Đã xoá ngữ cảnh" },
    ];
    render(<MessageList messages={messages as any} members={[]} roomId={1} />);
    expect(screen.getByText("🧹 Đã xoá ngữ cảnh")).toBeInTheDocument();
    // the human message still renders
    expect(screen.getByText("hello")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- message-list`
Expected: FAIL (the divider text isn't rendered — `context_reset` falls through to `HumanMessage`, which would label it "Anonymous" and not center it; the specific centered element assertion fails).

- [ ] **Step 3: Implement in `frontend/src/components/chat/message-list.tsx`**

Inside the `.map((m) => { ... })` return, add a `context_reset` branch as the **first** condition:

```tsx
        return m.kind === "context_reset" ? (
          <div key={m.id} className="flex justify-center py-1">
            <span className="rounded-full bg-[var(--surface-2,transparent)] px-3 py-1 text-center text-xs text-[var(--text-secondary)]">
              {m.body}
            </span>
          </div>
        ) : m.kind === "expense_draft" ? (
```

(i.e. prepend the new ternary branch ahead of the existing `m.kind === "expense_draft"` check; leave the rest of the chain unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- message-list`
Expected: PASS.

- [ ] **Step 5: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/chat/message-list.tsx frontend/src/components/chat/__tests__/message-list.test.tsx
git commit -m "feat(fe): render context_reset divider in the chat thread"
```

---

## Final Verification

- [ ] **Backend:** `cd backend && .venv/bin/pytest -q` → all green.
- [ ] **Frontend:** `cd frontend && npm test` → all green.
- [ ] **Manual smoke (optional):** run the app (see `.claude/skills/run-chiatienan`), then in a room:
  1. Send a few messages + an `@bot` turn, confirm the bot replies as before.
  2. Send `@bot` again and confirm (via backend logs or behavior) recent history is now in context.
  3. Send `/clear` → a `🧹` divider appears; chat above stays visible; `memory.md` for the room now has a "Xoá ngữ cảnh" section.
  4. Send `@bot` after `/clear` → the bot no longer references pre-clear specifics except via the memory summary.
- [ ] Update `README.md` if it documents bot commands (add `/clear`). (Only if such a section exists — otherwise skip.)

## Notes for the implementer

- **`Database` import in `chat.py`:** already imported (`from app.db import Database`, line 21) — no change needed for the type hints on `_maybe_rollover`/`clear_context`.
- **Circular imports:** `summarize.py` imports from `agent.py` at module load; `chat.py` imports `summarize` and `memory`. `agent.py` imports none of these, so there is no cycle. Do **not** add an `import chat` to `agent.py` or `summarize.py`.
- **Test workspace isolation:** memory files live under `settings.cursor_workspace`. Every test that touches memory must monkeypatch `app.memory._base_dir` to a `tmp_path` (see the `ws`/`workspace` fixtures) so rooms don't collide across tests.
- **`monkeypatch.setattr("app.chat.summarize_messages", ...)`:** works because `chat.py` does `from app.summarize import summarize_messages` (binds the name into `app.chat`). Patch it there, not in `app.summarize`.
