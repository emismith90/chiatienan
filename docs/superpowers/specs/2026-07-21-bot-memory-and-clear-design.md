# Bot memory (two-tier) + `/clear` command — design

**Date:** 2026-07-21
**Status:** Approved (brainstorming); pending implementation plan.

## Problem

Today the lunch bot is **stateless per turn**. Each `@bot` message spins up a fresh
Cursor agent (`client.agents.create()` in [`agent.py`](../../../backend/app/agent.py))
and sends it only `system prompt + the current message`. No thread/conversation id
is stored anywhere; the Cursor `store` is never used to resume a prior thread. The bot
"knows" only what it reads live via tools (ledger, roster, pending drafts).

We want:

1. The bot to see **recent room conversation** so it has context across turns.
2. A **`/clear`** command that resets that live context — but *compresses* it into a
   durable summary rather than losing it, and never deletes visible chat.

## Solution overview — two-tier memory

| Tier | What | Injected into each turn? |
|------|------|--------------------------|
| **Long-term** | Per-room `memory.md` — LLM-written summaries of aged-out / cleared periods | Yes — `# Bộ nhớ dài hạn` section |
| **Recent window** | Room messages within the last **10 weeks**, above the summarized watermark | Yes — `# Lịch sử hội thoại (gần đây)` section |

The single source of truth for the recent window's lower bound is a per-room
**`summarized_through_id`** watermark. Both `/clear` and the 10-week rollover advance
this watermark and append to `memory.md`. When both tiers are empty, the prompt is
**byte-identical to today's** (no behavior change for brand-new rooms).

### Money-safety (design D3) is preserved

`memory.md` and the recent-history section are **advisory context only**. All money
still flows exclusively through tools and the ledger. The bot must never treat a number
in a summary or history line as a source of truth for a computation — the existing D3
rule ("never compute or re-type a tool-produced number") stands unchanged, and the
summary prompt explicitly instructs the summarizer not to fabricate money figures.

## Persistence — migration-free

`db.py` only runs `Base.metadata.create_all()` (no `ALTER`, no Alembic), so we add **no
new columns**. State lives in two places, neither requiring a migration:

1. **Reset divider** — a `RoomMessage` row with `kind="context_reset"`. Purely a
   *visible* chat divider; it carries no functional meaning for the window bound.
2. **Per-room memory files** in the Cursor workspace:
   - `{cursor_workspace}/rooms/{room_id}/memory.md` — appended summary sections.
   - `{cursor_workspace}/rooms/{room_id}/memory.meta.json` —
     `{"summarized_through_id": int, "summarized_through_at": "<iso>"}`.
     Missing file ⇒ watermark `0`.

These files are read by **our** code and injected into the prompt. The agent does not
read or write them itself (summaries come from a dedicated LLM call, not an agent tool).

## Components

### New: `app/memory.py`

Pure-ish file/persistence helpers (no LLM, no Cursor SDK). Easy to unit-test.

- `room_memory_dir(room_id) -> Path` — ensures `{workspace}/rooms/{room_id}/` exists.
- `load_memory(room_id) -> str` — contents of `memory.md`, or `""`.
- `read_watermark(room_id) -> int` — `summarized_through_id` from meta, or `0`.
- `append_summary(room_id, *, summary_text, through_id, through_at, header)` —
  append a dated section to `memory.md` and atomically rewrite the meta with the new
  watermark. Section format:
  ```
  ## <header> — <through_at date>
  <summary_text>
  ```
- `messages_to_summarize(session, room_id, *, watermark, older_than=None) -> list[RoomMessage]` —
  rows with `id > watermark`, `kind in ("text","bot")`, ordered by id; when
  `older_than` (a datetime) is given, additionally `created_at < older_than`
  (the rollover case). Returns `[]` when nothing qualifies.

### New: `app/summarize.py`

One function that turns a list of messages into a plain-text Vietnamese summary via a
**minimal Cursor call** (no custom tools). Reuses `cursor_runner` model resolution and
mirrors `agent.run_turn`'s bridge-launch shape so it is mockable the same way in tests
(monkeypatch `_launch_bridge_resilient` + `resolve_model_selection`).

- `async summarize_messages(rendered_history: str, *, kind: str) -> str`
  - Builds a summarization prompt: "Tóm tắt ngắn gọn (5–10 gạch đầu dòng) các sự kiện,
    quyết định, và ai-nợ-ai đáng nhớ trong đoạn hội thoại sau. KHÔNG bịa số tiền; chỉ
    ghi lại điều đã nêu." + the rendered history.
  - Runs the agent to completion, returns the assistant text (stripped).
  - On failure returns `""` (caller then skips the append and does **not** advance the
    watermark, so the messages remain live and get retried next trigger — a failed
    summary must never silently drop context).

### Changed: `app/chat.py`

- `is_clear_command(text) -> bool` — `True` iff the trimmed text equals `/clear`
  (case-insensitive), tolerating an optional leading `@bot` / `@<handle>` + whitespace.
  Exact match — `/cleared`, `/clear now`, `clear` do **not** match.
- `build_history(session, room_id, *, watermark, before_id, limit) -> str` — rows with
  `watermark < id < before_id`, `kind in ("text","bot")`, most-recent `limit` (default
  `settings.history_max_messages`), rendered oldest→newest as:
  - human: `«<display_name>»: <body>` (each body clamped to ~500 chars)
  - bot: `chiatienan: <body>`
  Non-text kinds (`expense_draft`, `context_reset`) are skipped. Returns `""` when empty.
- `clear_context(db, room_id, *, up_to_id, emit) -> RoomMessage` — the `/clear`
  handler: under `_agent_lock`, gather live messages (`watermark < id < up_to_id`, so
  the `/clear` command line itself is excluded), summarize them
  (`summarize.summarize_messages`, `kind="clear"`), `memory.append_summary(...)` with
  `through_id=up_to_id`, then `post_message(kind="context_reset", body="🧹 Đã lưu tóm
  tắt vào bộ nhớ; ngữ cảnh đã xoá.")`. If summarization returns `""`, still post the
  divider and advance the watermark to `up_to_id` (the user explicitly asked to clear;
  we don't block on a summarizer failure — but we log it). Returns the divider message.
- `run_bot_turn(...)` gains a pre-agent step (still under `_agent_lock`):
  1. **Rollover:** `older = memory.messages_to_summarize(s, room_id, watermark=wm,
     older_than=now_ict() - timedelta(weeks=settings.memory_window_weeks))`. If
     non-empty, summarize (`kind="rollover"`) and `append_summary(through_id=max(older
     ids))`. On summary failure, skip (watermark unchanged) — retried next turn.
  2. Reload watermark + `memory.load_memory(room_id)`.
  3. `history = build_history(s, room_id, watermark=wm, before_id=current_msg_id,
     limit=...)`.
  4. Pass `memory=` and `history=` into `run_turn`.

  `run_bot_turn` therefore needs the **triggering message id** (`before_id`) — threaded
  in from the route (see below).

### Changed: `app/agent.py`

- `run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None)`.
- Prompt assembly becomes:
  ```
  <system prompt>

  # Bộ nhớ dài hạn        (only if memory)
  <memory>

  # Lịch sử hội thoại (gần đây)   (only if history)
  <history>

  # Tin nhắn người dùng
  <user_text>
  ```
  When `memory` and `history` are both falsy, output equals today's exactly.

### Changed: `app/main.py`

In `POST /api/rooms/{room_id}/messages`, after persisting + publishing the user message,
**before** the `mentions_bot` branch:

```python
if chat.is_clear_command(body.body):
    await hub.publish(room_id, {"type": "bot.typing"})
    async def _run_clear():
        try:
            div = await chat.clear_context(db, room_id, up_to_id=payload["id"], emit=emit)
            await hub.publish(room_id, {"type": "message", **chat.message_to_dict(div, None)})
        finally:
            await hub.publish(room_id, {"type": "bot.done"})
    # spawn as background task, same pattern as the bot turn
    ...
    return {"ok": True, "id": payload["id"]}
```

`/clear` and `@bot …` are mutually exclusive (a `/clear` message never also triggers a
normal bot turn). The `run_bot_turn` call passes the new `before_id=payload["id"]`.

### Changed: `app/config.py`

Two new settings (env-overridable, with `_int_env`):

- `memory_window_weeks` — default `10` (`MEMORY_WINDOW_WEEKS`).
- `history_max_messages` — safety cap on rendered recent history, default `200`
  (`HISTORY_MAX_MESSAGES`).

### Changed: frontend

Add a centered, muted divider render for `kind === "context_reset"` in the chat message
list (currently unknown kinds are ignored, so this is purely additive; no composer
change — `/clear` is just typed text). The divider shows the message `body`.

## Data flow (recap)

```
User types "/clear"
  → route persists "/clear" (visible)  → is_clear_command → clear_context (bg task):
      summarize(live msgs) → memory.md append + watermark = /clear msg id
      → post context_reset divider  (no bot turn)

User types "@bot ai trả tuần này"
  → route persists msg  → run_bot_turn (bg task, under _agent_lock):
      rollover(msgs older than 10w, above watermark) → maybe append memory.md
      → history = msgs (watermark < id < this msg)
      → run_turn(user_text, memory=memory.md, history=history)
      → normal tool loop / reply (unchanged downstream)
```

## Testing (TDD)

Backend (pytest, existing `db` fixture + Cursor-mock pattern from `test_agent.py`):

- `test_chat_is_clear_command` — matches `/clear`, `  /clear `, `/CLEAR`, `@bot /clear`,
  `@bot /clear`; rejects `/cleared`, `/clear now`, `clear`, ``.
- `test_build_history` — respects watermark floor, excludes `before_id` and anything
  ≥ it, caps to `limit` (keeps the most recent), labels human vs `chiatienan`, skips
  `expense_draft`/`context_reset`, returns `""` when empty.
- `test_memory_persistence` — `append_summary` writes a dated section and advances the
  meta watermark; `load_memory`/`read_watermark` round-trip; missing files ⇒ `""`/`0`.
- `test_messages_to_summarize` — watermark + `older_than` filtering and kind filter.
- `test_clear_context` (summarize mocked) — appends to memory.md, advances watermark to
  `up_to_id`, posts a `context_reset` message, and (via route) does **not** spawn a bot
  turn; summarize-returns-`""` still posts the divider and advances the watermark.
- `test_run_turn_prompt_sections` — `memory=`/`history=` produce the two labeled
  sections; both `None` ⇒ prompt unchanged from baseline.
- `test_run_bot_turn_rollover` (summarize mocked) — seeding messages older than the
  window triggers one `append_summary`; recent messages stay in the built history.

Frontend (vitest/RTL, alongside existing `message-list` tests): a `context_reset`
message renders as the divider with its body text.

## Out of scope (YAGNI)

- Per-user permissions on `/clear` (any room member may clear).
- Undo of a `/clear`, or editing `memory.md` from the UI.
- Token-based (vs count/time-based) trimming.
- Summarizing on period-settle (only `/clear` + 10-week rollover trigger summaries).
- Migrating memory across a workspace reset (files live in the workspace volume; a
  wiped volume loses long-term memory, same durability class as `.cursor-store`).

## Risks / notes

- **Extra LLM calls & latency.** `/clear` and each rollover cost one summarization call
  and take a few seconds; the UI shows a typing indicator. Rollover runs at most when
  messages actually age past 10 weeks (rare in practice), inside the existing lock.
- **Bridge flakiness.** `summarize.py` reuses the resilient launch/retry path, and a
  failed summary degrades gracefully (watermark not advanced ⇒ retried; `/clear` still
  divides).
- **Single-writer assumption.** Memory writes happen under `_agent_lock` (bot turns) or
  in the `/clear` handler which also takes `_agent_lock`, preserving the single-writer
  invariant for both the ledger and the per-room memory files.
