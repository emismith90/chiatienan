# Sample: Cursor SDK + AG-UI + Image — Standalone Boilerplate

**Date:** 2026-06-19
**Status:** Approved design — ready for implementation plan
**Target:** `/Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image/` (sibling of `Niteco.Atlas`; the parent `Atlas/` folder is not a git repo, so the sample is a self-contained directory)

## Goal

Lift Atlas's **Cursor-SDK-backed chat agent** out of the Atlas application into a standalone,
self-contained boilerplate that demonstrates the full stack end-to-end:

- **fe** — a Next.js chat UI that POSTs an AG-UI `RunAgentInput`, consumes the AG-UI SSE stream,
  renders streamed text + a tool-call timeline + rich renderables (charts/tables/images), and lets
  the user attach an image.
- **be** — a FastAPI service that runs the Cursor SDK agent (bridge → `agents.create` → `send`) and
  streams the result.
- **ag-ui** — the translation layer that maps the Cursor SDK event stream onto AG-UI protocol events.
- **with-image** — multimodal image attachments sent to a vision-capable model on the current turn.

The boilerplate is a **faithful port** of the real Atlas chat (rich renderables, typing effect,
conversation history, model selector preserved) with all Atlas-specific coupling removed (no auth,
no Postgres, no atlas-mcp, no SQL/Python tools, no role personas).

## Approach (chosen)

**Verbatim lift + decouple in place.** Copy the real, battle-tested Atlas files into the new tree,
then surgically cut Atlas coupling. This preserves hard-won behavior the originals already solved:

- Model **variant-param resolution** — bare parameterized model ids (e.g. `composer-2.5`) fail with
  an opaque `RUN_LIFECYCLE_STATUS_ERROR`; they must be sent as a resolved `ModelSelection` with the
  `fast` param. `resolve_model_selection` handles this and the alias index.
- The Cursor **MCP/custom-tool unwrap** (`name=='mcp'` wrapper → real tool name + clean result).
- The **turn-storm cap** (hard cancel after N tools / wall-clock seconds) and **interrupt close-out**
  (close open tool spinners + emit a terminal event on bridge death).
- The **multimodal image send** (`UserMessage` + `SDKImage.data_image`).

Rejected alternatives: clean re-implementation (re-derives the subtle bits, higher bug risk);
shared internal package (a refactor *of Atlas*, not a standalone boilerplate — out of scope now).

## Source files being lifted (current on-disk versions)

The working tree has uncommitted edits to several of these; the extraction copies the **current**
on-disk content, not `HEAD`.

Backend (`Niteco.Atlas/apps/backend/atlas/agent/`):
- `cursor_agui.py` — Cursor stream → AG-UI translator (`CursorAguiTranslator`, `cursor_run_to_agui`).
- `cursor_agent.py` — orchestration (`run_atlas_agent_cursor`): bridge launch, agent create, send.
- `cursor_runner.py` — model wiring (`resolve_model_selection`, model list + alias index, variants).
- `cursor_tools.py` — `CustomTool` registration pattern (shape reused for the demo tool).
- `atlas_agent.py` — the `/api/copilot/agui` route handler, `_sanitize_images`, the model list.

Frontend (`Niteco.Atlas/apps/frontend/src/`):
- `hooks/use-atlas-chat.ts` — SSE consumption + image send + model selection.
- `hooks/use-chat-sidebar.ts` — sidebar open/pin/width state.
- `components/chat/atlas-chat-sidebar.tsx` — the chat UI.
- `components/chat/chat-renderers.tsx` — renderable parsing (bar/line/pie/table/image/suggested_actions).
- `lib/chat-payload.ts` — ChatMessage[] → AG-UI message[] transform.
- `lib/chat-storage.ts` + `hooks/use-conversations.ts` — localStorage conversation history.
- shadcn primitives: `components/ui/{button,select,table}.tsx`.

## Architecture

```
Browser (Next.js)                          FastAPI backend                     Cursor SDK
─────────────────                          ───────────────                     ──────────
chat-sidebar.tsx                           POST /agui
  └─ use-chat.ts ── POST RunAgentInput ──►   ├─ parse RunAgentInput
       forwardedProps:{model, images}        ├─ sanitize_images()
                                             ├─ run_agent_cursor(...)  ────────► launch_bridge
  ◄── SSE: RUN_STARTED ◄────────────────┐    │    ├─ resolve_model_selection ──► Cursor.models.list()
  ◄── TEXT_MESSAGE_START/CONTENT/END ◄──┤    │    ├─ build demo CustomTool
  ◄── TOOL_CALL_START/ARGS/END/RESULT ◄─┤    │    ├─ (commented) HttpMcpServerConfig
  ◄── RUN_FINISHED / RUN_ERROR ◄────────┘    │    ├─ AsyncClient.launch_bridge
       │                                      │    ├─ agents.create(AgentOptions)
       ▼                                      │    ├─ agent.send(text | UserMessage+images)
  chat-renderers.tsx parses ```json```        │    └─ cursor_run_to_agui(run) ─ yields AG-UI events
  blocks → recharts / shadcn Table / <img>    └─ StreamingResponse(EventEncoder.encode)

                                           GET /models  ── Cursor.models.list() ─► [{id, displayName}]
```

Each SSE line is `data: {json}\n\n`, encoded by `ag_ui.encoder.EventEncoder`.

## Directory structure

```
sample-cursor-sdk-with-image/
├── README.md                 # what it is, run steps, "vision needs composer-2.5" note
├── docker-compose.yml        # backend (:8000) + frontend (:3000)
├── .env.example              # CURSOR_API_KEY, CURSOR_SDK_MODEL, NEXT_PUBLIC_API_URL, caps
├── .gitignore
├── backend/
│   ├── pyproject.toml        # cursor-sdk>=0.1.7, ag-ui-protocol>=0.1, fastapi, uvicorn, pydantic
│   ├── Dockerfile
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py           # FastAPI app: POST /agui (SSE), GET /models, GET /health, CORS
│   │   ├── cursor_agent.py   # run_agent_cursor: bridge → create → send (text|image)
│   │   ├── cursor_agui.py    # CursorAguiTranslator + cursor_run_to_agui (≈ verbatim)
│   │   ├── cursor_runner.py  # resolve_model_selection + model list (env key, no DB)
│   │   ├── tools.py          # demo CustomTool (get_current_time) + commented MCP stub
│   │   ├── images.py         # sanitize_images (base64 + mime validation)
│   │   ├── prompt.py         # small static system prompt
│   │   └── config.py         # env-var settings (CURSOR_API_KEY, model, caps)
│   └── tests/
│       ├── test_cursor_agui.py     # ported pure translator tests (fake messages)
│       └── test_images.py          # sanitize_images validation tests
└── frontend/
    ├── package.json          # next, react, recharts, react-markdown, remark-gfm, lucide-react,
    │                         # @radix-ui/react-select, tailwindcss v4, clsx, tailwind-merge, cva
    ├── Dockerfile
    ├── next.config.ts
    ├── tsconfig.json
    ├── postcss.config.mjs
    ├── components.json
    └── src/
        ├── app/
        │   ├── layout.tsx
        │   ├── page.tsx       # mounts the chat sidebar full-screen
        │   └── globals.css    # tailwind + chart color vars (--color-chart-1..5)
        ├── components/
        │   ├── chat/
        │   │   ├── chat-sidebar.tsx
        │   │   └── chat-renderers.tsx
        │   └── ui/
        │       ├── button.tsx
        │       ├── select.tsx
        │       └── table.tsx
        ├── hooks/
        │   ├── use-chat.ts
        │   ├── use-chat-sidebar.ts
        │   └── use-conversations.ts
        └── lib/
            ├── api.ts         # API base URL from NEXT_PUBLIC_API_URL
            ├── chat-payload.ts
            ├── chat-storage.ts
            └── utils.ts       # cn() helper
```

## Backend design

### `config.py` — env settings (replaces Atlas DB-backed settings)

Single source of runtime config from env vars (no Postgres):

| Env var | Default | Purpose |
|---|---|---|
| `CURSOR_API_KEY` | (required) | Cursor SDK key (a user/service-account key from the Cursor dashboard — **not** a Team Admin key) |
| `CURSOR_SDK_MODEL` | `composer-2.5` | Default model when the request sends no override |
| `CURSOR_SDK_WORKSPACE` | `/tmp/sample-cursor-agent` | Scratch cwd for the local bridge |
| `CURSOR_API_BASE` | `https://api.cursor.com` | REST base for the model/alias catalog |
| `CURSOR_AGENT_MAX_TOOLS` | `500` | Turn cap: max completed tool calls (`0` = off) |
| `CURSOR_AGENT_MAX_SECONDS` | `1800` | Turn cap: max wall-clock seconds (`0` = off) |
| `CORS_ORIGINS` | `http://localhost:3000` | Allowed browser origins |

### `cursor_runner.py` — model wiring (lifted, DB removed)

**Keep verbatim:** `resolve_model_selection`, `_list_models`, `_alias_index`, `_build_alias_index`,
`_parse_model_specifier`, all `_variant_*` / `model_list_item_to_selection` helpers, the
`_reasoning_*` mapping, `format_cursor_agent_failure`, `CursorRunnerError` /
`CursorRunnerRunStatusError`.

**Decouple:** delete `_read_cursor_settings` (psycopg2) and the personal/SA key split. Replace
`resolve_cursor_api_key`, `default_cursor_model`, `agent_turn_budget` with thin readers over
`config.py`. Drop the headless `CursorRunner` class and `_workspace_lock` (the streaming agent
doesn't use them).

### `cursor_agui.py` — Cursor → AG-UI translator (verbatim)

Copied as-is. Pure, side-effect-free `CursorAguiTranslator` + async `cursor_run_to_agui` driver.
Retains: text-delta streaming, tool-call start/args/end/result with the `name=='mcp'` unwrap, the
turn-storm cap (cancel + honest note + graceful `RUN_FINISHED`), interrupt close-out on bridge death.
Only change: `agent_turn_budget` now imported from `config.py`/`cursor_runner.py` (env-backed).

### `cursor_agent.py` — orchestration (`run_agent_cursor`)

Keeps the core flow: resolve model → resolve key → `_build_send_message` (text vs multimodal
`UserMessage`) → `AsyncClient.launch_bridge` → `agents.create(AgentOptions(model, api_key, local,
mcp_servers))` → `agent.send(message, SendOptions(model, local=force))` → stream via
`cursor_run_to_agui`.

**Decouple — simplified signature:**
```python
async def run_agent_cursor(
    run_input,                 # ag_ui RunAgentInput
    *,
    model_override: str | None,
    images: list[dict] | None, # [{"data": <base64>, "mimeType": ...}]
):
```
Removed params: `tz`, `user_email`, `user_role`, `page_filter`, `reasoning`, `credential_headers`,
`run_id`, `graph_mode`, `disable_skills`, `disable_sql`.

- System prompt: `prompt.build_system_prompt()` (static) instead of role-based `_build_system_prompt`.
- Tools: `tools.build_demo_tools()` instead of `build_custom_tools` (SQL/Python).
- MCP: `mcp_servers={}` by default; a **commented** `HttpMcpServerConfig` example shows how to attach
  one (this is the "MCP stub" deliverable).
- `_render_prompt` (system preamble + replayed transcript) kept — Cursor's Agent has no
  `instructions` field, so the prompt is sent as a preamble and history replayed inline.
- Reasoning: `run_agent_cursor` takes no `reasoning` param; internally it calls
  `resolve_model_selection(..., reasoning="medium")` with a module constant (the UI reasoning picker
  is dropped). On models with no reasoning/effort param (composer-2.5, gemini) this is a silent no-op.

### `tools.py` — demo tool + MCP stub

```python
def build_demo_tools() -> dict[str, CustomTool]:
    def get_current_time(args, ctx) -> dict:
        # returns {"iso": ..., "timezone": ...} for the requested tz (default UTC)
        ...
    return {"get_current_time": CustomTool(
        execute=get_current_time,
        description="Return the current date/time, optionally for a given IANA timezone.",
        input_schema={"type":"object","properties":{"timezone":{"type":"string"}}},
    )}
```
Trivial on purpose — its value is making `TOOL_CALL_START/ARGS/END/RESULT` fire so the timeline UI is
exercised. A commented `HttpMcpServerConfig(url=..., headers=...)` block documents the MCP path.

### `images.py` — `sanitize_images` (lifted)

Port `_sanitize_images` verbatim: validate `forwardedProps.images` into a clean
`[{"data": <base64>, "mimeType": <allowed>}]` list. Allowed mimes: `image/png`, `image/jpeg`,
`image/webp`, `image/gif`. Strips a leading `data:...;base64,` prefix; rejects disallowed mimes;
enforces a per-image size cap (base64 length → approx decoded bytes).

### `main.py` — FastAPI routes (auth removed)

- `POST /agui` — parse `RunAgentInput`; read `forwardedProps.model` + `forwardedProps.images`
  (sanitized); stream `run_agent_cursor(...)` through `EventEncoder` as
  `StreamingResponse(media_type="text/event-stream")`. On exception mid-stream → encode a
  `RUN_ERROR` event. **No** `get_current_user`, conversation logging, feedback, thread-owner check,
  or credential forwarding.
- `GET /models` — `Cursor.models.list(api_key=...)` mapped to `[{"id","displayName"}]` for the
  frontend model picker. (Replaces Atlas's `/api/copilot/models`.)
- `GET /health` — liveness.
- CORS middleware from `CORS_ORIGINS`.

## Frontend design

### `use-chat.ts` (from `use-atlas-chat.ts`)

Keep: SSE consumption + event→`ChatMessage[]` accumulation (TEXT/TOOL_CALL events), image
upload/encode (FileReader → strip prefix → raw base64 → `forwardedProps.images`), per-request model
override, streaming/typing state.

Decouple: drop `tz`, `pageFilter`, `agentBackend` from `forwardedProps`; drop `useAuth`; point the
fetch at `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`) `/agui`.

### `chat-sidebar.tsx` (from `atlas-chat-sidebar.tsx`)

Keep: multi-bubble layout (reasoning / tool-call / answer), composer with image attach + preview
(PNG/JPEG/WebP/GIF, ≤5 MB each, ≤4 images, ≤6 MB total), model selector (fed by `GET /models`),
conversation history sidebar, tool-call timeline, typing effect, Claude.ai-style scroll.

Decouple: remove `useAuth` admin-gating (always render the full tool timeline — it's a dev sample);
remove page/tz context; remove the agno/cursor backend selector (cursor only); remove the
feedback thumbs entirely (they were Postgres-backed and there is no backend feedback endpoint).

### `chat-renderers.tsx` (verbatim)

Copied as-is. Parses ```json``` fences after `TEXT_MESSAGE_END` into:
`bar_chart` / `line_chart` / `pie_chart` (recharts), `table` (shadcn Table, sortable),
`image` (base64 `<img>`), `suggested_actions` (chips). Plain text → `ReactMarkdown` + `remark-gfm`.

### `use-conversations.ts` + `chat-storage.ts` (verbatim, DB-free)

localStorage persistence — max 50 conversations / 2 MB; strips tool results + image data on overflow.
Keys: `sample-chat-conversations`, `sample-chat-model`, `sample-chat-sidebar-width` (renamed from
`atlas-*`).

### `chat-payload.ts` (verbatim)

ChatMessage[] → AG-UI message[]: coalesce consecutive assistant bubbles, drop context/reasoning
messages, filter null tool results.

## Data flow details

**AG-UI events emitted:** `RUN_STARTED`, `TEXT_MESSAGE_START/CONTENT/END`,
`TOOL_CALL_START/ARGS/END/RESULT`, `RUN_FINISHED`, `RUN_ERROR`.

**Image path:** client encodes each attachment to raw base64 + mime → `forwardedProps.images` →
backend `sanitize_images` → `_build_send_message` wraps the turn in
`UserMessage(text=prompt, images=[SDKImage.data_image(data, mime)])`. Images attach to **this** send
only; history is replayed as plain text, so prior-turn images are not resent. **Vision requires a
vision-capable model** (composer-2.5) — documented in the README and surfaced via the existing
`error_hint` when a model can't run.

## Configuration & env (`.env.example`)

```
# Backend
CURSOR_API_KEY=            # required; user/service-account key (NOT a Team Admin key)
CURSOR_SDK_MODEL=composer-2.5
CURSOR_AGENT_MAX_TOOLS=500
CURSOR_AGENT_MAX_SECONDS=1800
CORS_ORIGINS=http://localhost:3000
# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Verification

1. **Backend unit (pure, no live SDK):** port `test_cursor_agui.py` — feed fake Cursor messages
   through `translate_messages` / `CursorAguiTranslator` and assert the AG-UI event sequence
   (text streaming, tool unwrap, turn cap, interrupt close-out). Port the image-sanitize tests.
   `cd backend && pip install -e ".[test]" && pytest`.
2. **Smoke e2e (live, needs a real `CURSOR_API_KEY`):** `docker compose up --build`, open
   `http://localhost:3000`:
   - text turn → streamed answer renders;
   - ask "what time is it in Tokyo?" → `get_current_time` tool call shows in the timeline;
   - attach a PNG + ask about it (composer-2.5) → vision answer renders;
   - reload → conversation history persists from localStorage.
3. README documents both paths and the vision-model caveat.

## Out of scope (documented follow-ups)

- Auth / RBAC, Postgres conversation logging, per-user credential forwarding.
- The atlas-mcp server, SQL/Python sandbox tools, the skills system, role personas.
- The agno backend (only the Cursor path is extracted).
- The Postgres `LocalAgentStoreHandler` session store (the SDK's built-in sqlite store is used).
- Publishing the cursor→AG-UI core as a shared package (Approach C — future).

## Packaging / spec location

- The sample is created as its **own standalone git repository** (`git init` at the sample root, with
  its own `.gitignore`), so the boilerplate is ready to clone/push and supports per-task commits.
  Its commits are independent of the `Niteco.Atlas` repo.
- This design doc lives in `Niteco.Atlas/docs/superpowers/specs/` (the Atlas git repo) for tracking
  and is committed there.
