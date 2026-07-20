# Sample: Cursor SDK + AG-UI + Image — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift Atlas's Cursor-SDK-backed chat agent (frontend + FastAPI backend + AG-UI translation + image attachments) into a standalone, self-contained boilerplate at `/Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image/`.

**Architecture:** A Next.js chat UI POSTs an AG-UI `RunAgentInput` to a FastAPI `/agui` endpoint; the backend runs the Cursor SDK agent (bridge → `agents.create` → `send`, text or multimodal `UserMessage`) and streams the Cursor event stream translated into AG-UI SSE events. Approach: **verbatim lift + decouple in place** — copy the battle-tested Atlas files, then surgically remove auth/Postgres/atlas-mcp/SQL-tools/role coupling.

**Tech Stack:** Backend — Python 3.12+, FastAPI, `cursor-sdk>=0.1.7`, `ag-ui-protocol>=0.1`, uvicorn, pydantic, pytest. Frontend — Next.js 16, React 19, Tailwind v4, `@radix-ui/react-select`, recharts, react-markdown + remark-gfm, lucide-react, vitest.

**Spec:** `Niteco.Atlas/docs/superpowers/specs/2026-06-19-sample-cursor-sdk-with-image-design.md`

## Global Constraints

- **Target dir:** `/Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image/` — its own `git init` repo, independent of the `Niteco.Atlas` repo. All task commits happen inside this dir.
- **Source files** to lift come from `/Users/hungle/Documents/Projects/Atlas/Niteco.Atlas/` — copy the **current on-disk** version (the working tree has uncommitted edits to several; do not use `git show HEAD`).
- **No Atlas coupling** in the result: no JWT/RBAC, no Postgres/psycopg2/SQLAlchemy, no atlas-mcp, no SQL/Python sandbox tools, no skills, no role personas, no agno backend.
- **Backend deps pinned:** `cursor-sdk>=0.1.7`, `ag-ui-protocol>=0.1`.
- **Cursor API key:** a user/service-account key from the Cursor dashboard (**not** a Team Admin key). One key only (`CURSOR_API_KEY`) — no personal/SA split.
- **Vision model:** image attachments require a vision-capable model (`composer-2.5`); documented in the README.
- **localStorage keys:** rename `atlas-*` → `sample-chat-*` so the sample never collides with a real Atlas tab.
- **`/models` response shape:** `{"models": [<id string>, ...], "default": "<id>"}` (this is what the lifted frontend consumes — supersedes the spec's loose `[{id,displayName}]` phrasing).

---

### Task 1: Sample repo scaffold + backend package + config

**Files:**
- Create: `sample-cursor-sdk-with-image/.gitignore`
- Create: `sample-cursor-sdk-with-image/.env.example`
- Create: `sample-cursor-sdk-with-image/backend/pyproject.toml`
- Create: `sample-cursor-sdk-with-image/backend/app/__init__.py` (empty)
- Create: `sample-cursor-sdk-with-image/backend/app/config.py`
- Test: `sample-cursor-sdk-with-image/backend/tests/test_config.py`

**Interfaces:**
- Produces: `app.config.Settings` dataclass-like object exposing `cursor_api_key: str`, `cursor_model: str` (default `"composer-2.5"`), `cursor_workspace: str`, `cursor_api_base: str`, `max_tools: int` (default 500), `max_seconds: int` (default 1800), `cors_origins: list[str]`. Module-level singleton `settings = Settings.from_env()`.

- [ ] **Step 1: Create the sample dir and init git**

```bash
mkdir -p /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image/backend/app
mkdir -p /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image/backend/tests
cd /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image
git init
```

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.egg-info/
.venv/
node_modules/
.next/
.env
*.db
.cursor-store/
```

- [ ] **Step 3: Write `.env.example`**

```
# Backend
CURSOR_API_KEY=            # required; user/service-account key (NOT a Team Admin key)
CURSOR_SDK_MODEL=composer-2.5
CURSOR_SDK_WORKSPACE=/tmp/sample-cursor-agent
CURSOR_API_BASE=https://api.cursor.com
CURSOR_AGENT_MAX_TOOLS=500
CURSOR_AGENT_MAX_SECONDS=1800
CORS_ORIGINS=http://localhost:3000
# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000
```

- [ ] **Step 4: Write `backend/pyproject.toml`**

```toml
[project]
name = "sample-cursor-agent"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "cursor-sdk>=0.1.7",
    "ag-ui-protocol>=0.1",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic>=2",
]

[project.optional-dependencies]
test = ["pytest>=8", "httpx>=0.27"]

[tool.setuptools.packages.find]
where = ["."]
include = ["app*"]
```

- [ ] **Step 5: Write the failing test `backend/tests/test_config.py`**

```python
import os
from app.config import Settings


def test_defaults_when_env_absent(monkeypatch):
    for k in ("CURSOR_SDK_MODEL", "CURSOR_AGENT_MAX_TOOLS", "CURSOR_AGENT_MAX_SECONDS", "CORS_ORIGINS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CURSOR_API_KEY", "k")
    s = Settings.from_env()
    assert s.cursor_api_key == "k"
    assert s.cursor_model == "composer-2.5"
    assert s.max_tools == 500
    assert s.max_seconds == 1800
    assert s.cors_origins == ["http://localhost:3000"]


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "k")
    monkeypatch.setenv("CURSOR_SDK_MODEL", "gemini-2.5-pro")
    monkeypatch.setenv("CURSOR_AGENT_MAX_TOOLS", "0")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.com,http://b.com")
    s = Settings.from_env()
    assert s.cursor_model == "gemini-2.5-pro"
    assert s.max_tools == 0
    assert s.cors_origins == ["http://a.com", "http://b.com"]
```

- [ ] **Step 6: Run the test, verify it fails**

Run: `cd backend && pip install -e ".[test]" && pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 7: Write `backend/app/config.py`**

```python
"""Env-var settings for the sample Cursor SDK agent (no DB, single API key)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    cursor_api_key: str
    cursor_model: str
    cursor_workspace: str
    cursor_api_base: str
    max_tools: int
    max_seconds: int
    cors_origins: list[str]

    @classmethod
    def from_env(cls) -> "Settings":
        origins = [o.strip() for o in (os.environ.get("CORS_ORIGINS") or "http://localhost:3000").split(",") if o.strip()]
        return cls(
            cursor_api_key=(os.environ.get("CURSOR_API_KEY") or "").strip(),
            cursor_model=(os.environ.get("CURSOR_SDK_MODEL") or "").strip() or "composer-2.5",
            cursor_workspace=(os.environ.get("CURSOR_SDK_WORKSPACE") or "").strip() or "/tmp/sample-cursor-agent",
            cursor_api_base=((os.environ.get("CURSOR_API_BASE") or "").strip() or "https://api.cursor.com").rstrip("/"),
            max_tools=_int_env("CURSOR_AGENT_MAX_TOOLS", 500),
            max_seconds=_int_env("CURSOR_AGENT_MAX_SECONDS", 1800),
            cors_origins=origins,
        )


settings = Settings.from_env()
```

- [ ] **Step 8: Run the test, verify it passes**

Run: `cd backend && pytest tests/test_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 9: Commit**

```bash
cd /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image
git add -A
git commit -m "chore: scaffold sample repo + backend config"
```

---

### Task 2: `cursor_runner.py` — model wiring (lift + remove DB)

**Files:**
- Create: `backend/app/cursor_runner.py`
- Test: `backend/tests/test_cursor_runner.py`
- Source: `Niteco.Atlas/apps/backend/atlas/agent/cursor_runner.py`

**Interfaces:**
- Produces: `resolve_model_selection(api_key: str, model: str, reasoning: str | None = None) -> ModelSelection`, `default_cursor_model() -> str`, `resolve_cursor_api_key(api_key: str | None = None) -> str`, `agent_turn_budget() -> tuple[int, int]`, `format_cursor_agent_failure(err) -> str`, exceptions `CursorRunnerError` / `CursorRunnerRunStatusError`, plus the pure helpers `_parse_model_specifier`, `_build_alias_index`, `model_list_item_to_selection`.

- [ ] **Step 1: Copy the source file**

```bash
cp /Users/hungle/Documents/Projects/Atlas/Niteco.Atlas/apps/backend/atlas/agent/cursor_runner.py \
   /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image/backend/app/cursor_runner.py
```

- [ ] **Step 2: Remove the DB-backed settings layer**

In `backend/app/cursor_runner.py`, **delete** these symbols entirely:
- `_CURSOR_SETTINGS_KEYS`, `_CURSOR_SETTINGS_CACHE`, `_CURSOR_SETTINGS_TTL`, `_read_cursor_settings` (the psycopg2 reader)
- `_env_setting` (it imports `atlas.config`)
- `_is_composer_model` and the personal/SA branching in `resolve_cursor_api_key`
- the entire `CursorRunner` class, `_workspace_lock`, `_workspace_locks*`, `_finalize_run`, `_format_run_result_error`, `extract_assistant_text`, `extract_first_json_object`, `FENCE_RE`, `RUN_STATUS_ERROR_*` (the headless one-shot runner — the streaming agent doesn't use it)

Keep: `resolve_model_selection`, `_list_models`, `_find_model_by_name`, `_alias_index`, `_build_alias_index`, `_parse_model_specifier`, all `_variant_*` / `_default_variant` / `_non_fast_variant` / `model_list_item_to_selection` helpers, `_reasoning_*` + `_UI_REASONING_LEVELS` + `_EFFORT_RANK`, `MODEL_SUFFIX_OVERRIDES`, `format_cursor_agent_failure`, `CursorRunnerError`, `CursorRunnerRunStatusError`, `_cached_models_for_key`, `_cached_aliases_for_key`.

- [ ] **Step 3: Rewrite the config-dependent functions over `app.config`**

Replace `_CURSOR_API_BASE`, `resolve_cursor_api_key`, `default_cursor_model`, `agent_turn_budget` with:

```python
from app.config import settings

_CURSOR_API_BASE = settings.cursor_api_base


def resolve_cursor_api_key(api_key: str | None = None) -> str:
    """Single-key resolution: explicit arg > CURSOR_API_KEY env."""
    if api_key and api_key.strip():
        return api_key.strip()
    key = settings.cursor_api_key
    if not key:
        raise CursorRunnerError(
            "Missing Cursor SDK API key. Set CURSOR_API_KEY (a user/service-account "
            "key from the Cursor dashboard; not a Team Admin key)."
        )
    return key


def default_cursor_model() -> str:
    return settings.cursor_model


def agent_turn_budget() -> tuple[int, int]:
    return (settings.max_tools, settings.max_seconds)
```

Delete the now-unused `agent_backend` function. Update the module docstring's first paragraph to drop the agno/Atlas references (one-line edit). Confirm no remaining `from atlas.` or `import psycopg2` lines: `grep -n "atlas\.\|psycopg2" backend/app/cursor_runner.py` must return nothing.

- [ ] **Step 4: Write the failing test `backend/tests/test_cursor_runner.py`** (pure helpers, no network)

```python
from app.cursor_runner import _parse_model_specifier, _build_alias_index, model_list_item_to_selection


def test_parse_model_specifier_strips_fast_suffix():
    base, overrides = _parse_model_specifier("composer-2.5-fast")
    assert base == "composer-2.5"
    assert overrides == [__import__("cursor_sdk").ModelParameterValue(id="fast", value="true")]


def test_parse_model_specifier_no_suffix():
    base, overrides = _parse_model_specifier("gemini-2.5-pro")
    assert base == "gemini-2.5-pro"
    assert overrides is None


def test_build_alias_index_first_claimant_wins():
    items = [
        {"id": "gpt-5.5", "aliases": ["gpt"]},
        {"id": "gpt-5.4", "aliases": ["gpt"]},
        {"id": "claude-opus-4-8", "aliases": ["opus-4-8", "Opus"]},
    ]
    idx = _build_alias_index(items)
    assert idx["gpt"] == "gpt-5.5"
    assert idx["opus-4-8"] == "claude-opus-4-8"
    assert idx["opus"] == "claude-opus-4-8"
```

- [ ] **Step 5: Run the test, verify it fails then passes**

Run: `cd backend && pytest tests/test_cursor_runner.py -v`
Expected: PASS after Step 2-3 edits (the helpers are pure). If it errors on import, fix the leftover `atlas.`/`psycopg2` references flagged in Step 3.

- [ ] **Step 6: Commit**

```bash
cd /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image
git add -A && git commit -m "feat(be): lift cursor_runner model wiring, remove DB/key-split coupling"
```

---

### Task 3: `cursor_agui.py` — Cursor→AG-UI translator (verbatim) + ported tests

**Files:**
- Create: `backend/app/cursor_agui.py`
- Test: `backend/tests/test_cursor_agui.py`
- Source: `Niteco.Atlas/apps/backend/atlas/agent/cursor_agui.py` and `Niteco.Atlas/apps/backend/tests/agent/test_cursor_agui.py`

**Interfaces:**
- Consumes: `agent_turn_budget` from `app.cursor_runner` (Task 2).
- Produces: `CursorAguiTranslator`, `cursor_run_to_agui(run, thread_id, run_id, error_hint="", *, max_tools=None, max_seconds=None)` (async generator of AG-UI events), `translate_messages(messages, thread_id, run_id) -> list`.

- [ ] **Step 1: Copy the source file verbatim**

```bash
cp /Users/hungle/Documents/Projects/Atlas/Niteco.Atlas/apps/backend/atlas/agent/cursor_agui.py \
   /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image/backend/app/cursor_agui.py
```

- [ ] **Step 2: Fix the one internal import**

In `backend/app/cursor_agui.py`, find the lazy import inside `cursor_run_to_agui`:
`from atlas.agent.cursor_runner import agent_turn_budget` → change to `from app.cursor_runner import agent_turn_budget`.
Verify nothing else references `atlas.`: `grep -n "atlas\." backend/app/cursor_agui.py` returns nothing.

- [ ] **Step 3: Copy + adapt the existing test**

```bash
cp /Users/hungle/Documents/Projects/Atlas/Niteco.Atlas/apps/backend/tests/agent/test_cursor_agui.py \
   /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image/backend/tests/test_cursor_agui.py
```
Then fix imports in the copied test: replace every `from atlas.agent.cursor_agui import ...` with `from app.cursor_agui import ...`. Remove any test that depends on Atlas-only fixtures (e.g. anything importing `atlas.`); keep the pure translator tests (fake-message → AG-UI event-sequence assertions, the tool-unwrap, the turn-cap, the interrupt close-out). `grep -n "atlas\." backend/tests/test_cursor_agui.py` must return nothing.

- [ ] **Step 4: Run the tests, verify they pass**

Run: `cd backend && pytest tests/test_cursor_agui.py -v`
Expected: PASS (the translator is pure; the only dependency `agent_turn_budget` resolves from Task 2).

- [ ] **Step 5: Commit**

```bash
cd /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image
git add -A && git commit -m "feat(be): lift cursor_agui translator + tests (verbatim)"
```

---

### Task 4: `images.py` — image sanitization + tests

**Files:**
- Create: `backend/app/images.py`
- Test: `backend/tests/test_images.py`

**Interfaces:**
- Produces: `sanitize_images(raw) -> list[dict[str, str]] | None` — returns `[{"data": <base64>, "mimeType": <mime>}]` or `None`.

- [ ] **Step 1: Write `backend/app/images.py`** (reproduced from Atlas `_sanitize_images`, verbatim logic)

```python
"""Server-side image-attachment validation for the Cursor agent (vision)."""
from __future__ import annotations

import logging

_IMAGE_ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_IMAGE_MAX_COUNT = 4
_IMAGE_MAX_BYTES = 5 * 1024 * 1024
_IMAGE_MAX_TOTAL_BYTES = 6 * 1024 * 1024

logger = logging.getLogger("sample-cursor-agent")


def sanitize_images(raw) -> list[dict[str, str]] | None:
    """Validate ``forwardedProps.images`` into a clean list of {data, mimeType}.

    Each item must be {"data": <base64>, "mimeType": <allowed image type>}.
    Drops a leading ``data:...;base64,`` prefix, rejects disallowed mime types,
    enforces per-image + total size caps and a max count. Returns ``None`` when
    nothing is usable so callers keep the plain-text send path.
    """
    if not isinstance(raw, list):
        return None
    out: list[dict[str, str]] = []
    total = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        mime = str(item.get("mimeType") or "").strip().lower()
        data = item.get("data")
        if mime not in _IMAGE_ALLOWED_MIME or not isinstance(data, str) or not data:
            continue
        if data.startswith("data:"):
            _, _, data = data.partition(",")
        data = data.strip()
        if not data:
            continue
        size = (len(data) * 3) // 4
        if size > _IMAGE_MAX_BYTES or total + size > _IMAGE_MAX_TOTAL_BYTES:
            logger.warning("Dropping image attachment over size budget (mime=%s)", mime)
            continue
        out.append({"data": data, "mimeType": mime})
        total += size
        if len(out) >= _IMAGE_MAX_COUNT:
            break
    return out or None
```

- [ ] **Step 2: Write the failing test `backend/tests/test_images.py`**

```python
import base64
from app.images import sanitize_images


def _b64(n: int) -> str:
    return base64.b64encode(b"x" * n).decode()


def test_none_when_not_a_list():
    assert sanitize_images(None) is None
    assert sanitize_images("nope") is None


def test_accepts_valid_png_and_strips_data_url_prefix():
    out = sanitize_images([{"mimeType": "image/png", "data": "data:image/png;base64," + _b64(10)}])
    assert out == [{"data": _b64(10), "mimeType": "image/png"}]


def test_rejects_disallowed_mime():
    assert sanitize_images([{"mimeType": "image/svg+xml", "data": _b64(10)}]) is None


def test_per_image_size_cap():
    big = _b64(6 * 1024 * 1024)  # ~6 MB > 5 MB cap
    assert sanitize_images([{"mimeType": "image/png", "data": big}]) is None


def test_max_count_is_four():
    items = [{"mimeType": "image/png", "data": _b64(10)} for _ in range(6)]
    out = sanitize_images(items)
    assert out is not None and len(out) == 4
```

- [ ] **Step 3: Run the tests**

Run: `cd backend && pytest tests/test_images.py -v`
Expected: PASS (5 passed).

- [ ] **Step 4: Commit**

```bash
cd /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image
git add -A && git commit -m "feat(be): image sanitization + tests"
```

---

### Task 5: `tools.py` (demo tool + MCP stub) + `prompt.py`

**Files:**
- Create: `backend/app/tools.py`
- Create: `backend/app/prompt.py`
- Test: `backend/tests/test_tools.py`

**Interfaces:**
- Produces: `tools.build_demo_tools() -> dict[str, CustomTool]`, `prompt.build_system_prompt() -> str`.

- [ ] **Step 1: Write the failing test `backend/tests/test_tools.py`**

```python
from app.tools import build_demo_tools


def test_demo_tools_has_get_current_time():
    tools = build_demo_tools()
    assert "get_current_time" in tools


def test_get_current_time_utc_returns_iso_with_offset():
    tools = build_demo_tools()
    result = tools["get_current_time"].execute({"timezone": "UTC"}, None)
    assert result["timezone"] == "UTC"
    assert result["iso"].endswith("+00:00")


def test_get_current_time_invalid_tz_falls_back_to_utc():
    tools = build_demo_tools()
    result = tools["get_current_time"].execute({"timezone": "Not/AZone"}, None)
    assert result["timezone"] == "UTC"
```

- [ ] **Step 2: Run it, verify it fails**

Run: `cd backend && pytest tests/test_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.tools'`.

- [ ] **Step 3: Write `backend/app/tools.py`**

```python
"""Demo custom tool for the sample Cursor agent + an MCP-server stub.

The tool is trivial on purpose — its value is making the AG-UI TOOL_CALL_*
events fire so the frontend tool-call timeline is exercised. The commented
HttpMcpServerConfig block shows how to attach a real MCP server.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cursor_sdk import CustomTool

_TIME_SCHEMA = {
    "type": "object",
    "properties": {
        "timezone": {"type": "string", "description": "IANA timezone name, e.g. 'Asia/Tokyo'. Defaults to UTC."}
    },
}


def build_demo_tools() -> dict[str, CustomTool]:
    def get_current_time(args: Mapping[str, Any], ctx: Any) -> dict:
        name = str((args or {}).get("timezone") or "UTC").strip() or "UTC"
        try:
            tz = timezone.utc if name.upper() == "UTC" else ZoneInfo(name)
            label = name
        except (ZoneInfoNotFoundError, ValueError):
            tz, label = timezone.utc, "UTC"
        return {"iso": datetime.now(tz).isoformat(), "timezone": label}

    return {
        "get_current_time": CustomTool(
            execute=get_current_time,
            description="Return the current date/time, optionally for a given IANA timezone.",
            input_schema=_TIME_SCHEMA,
        )
    }


# --- Attaching an MCP server (example; not wired by default) --------------- #
# from cursor_sdk import HttpMcpServerConfig
#
# def build_mcp_servers() -> dict:
#     return {
#         "my-mcp": HttpMcpServerConfig(
#             url="https://my-host/mcp",
#             headers={"Authorization": "Bearer <token>"},
#         )
#     }
# Then pass `mcp_servers=build_mcp_servers()` into AgentOptions in cursor_agent.py.
```

- [ ] **Step 4: Write `backend/app/prompt.py`**

```python
"""Static system prompt for the sample agent (replaces Atlas's role-based prompt)."""
from __future__ import annotations


def build_system_prompt() -> str:
    return (
        "You are a helpful assistant in a demo chat app built on the Cursor SDK.\n"
        "You can call the `get_current_time` tool when asked about the current time.\n"
        "When the user attaches an image, describe or analyze it.\n"
        "To render a chart or table in the UI, emit a fenced ```json block with one of:\n"
        '  {"type":"bar_chart","data":[...],"xKey":"...","yKeys":["..."],"title":"..."}\n'
        '  {"type":"table","columns":[{"key":"k","label":"L"}],"rows":[...],"title":"..."}\n'
        "Otherwise answer in plain markdown."
    )
```

- [ ] **Step 5: Run the tests, verify they pass**

Run: `cd backend && pytest tests/test_tools.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
cd /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image
git add -A && git commit -m "feat(be): demo get_current_time tool + MCP stub + static prompt"
```

---

### Task 6: `cursor_agent.py` — orchestration (lift + decouple)

**Files:**
- Create: `backend/app/cursor_agent.py`
- Test: `backend/tests/test_cursor_agent_smoke.py`
- Source: `Niteco.Atlas/apps/backend/atlas/agent/cursor_agent.py`

**Interfaces:**
- Consumes: `app.prompt.build_system_prompt`, `app.tools.build_demo_tools`, `app.cursor_agui.cursor_run_to_agui`, `app.cursor_runner.{default_cursor_model, resolve_cursor_api_key, resolve_model_selection}`, `app.config.settings`.
- Produces: `run_agent_cursor(run_input, *, model_override: str | None = None, images: list[dict] | None = None)` — async generator of AG-UI events.

- [ ] **Step 1: Copy the source file**

```bash
cp /Users/hungle/Documents/Projects/Atlas/Niteco.Atlas/apps/backend/atlas/agent/cursor_agent.py \
   /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image/backend/app/cursor_agent.py
```

- [ ] **Step 2: Replace the module docstring + workspace constants**

Set `_WORKSPACE` from config:
```python
from app.config import settings
_WORKSPACE = settings.cursor_workspace
_STORE_ROOT = os.path.join(_WORKSPACE, ".cursor-store")
```
Keep `_ensure_workspace`, `_message_role_content`, `_render_prompt`, `_build_send_message` verbatim (they're already decoupled; `_build_send_message` uses `SDKImage`/`UserMessage` — keep as-is).

- [ ] **Step 3: Rewrite the `run_atlas_agent_cursor` signature + body into `run_agent_cursor`**

Replace the whole function with the decoupled version (drops `tz`, `user_email`, `user_role`, `page_filter`, `reasoning`, `credential_headers`, `run_id`, `graph_mode`, `disable_skills`, `disable_sql`; static prompt; demo tools; empty MCP):

```python
async def run_agent_cursor(
    run_input,
    *,
    model_override: str | None = None,
    images: list[dict[str, str]] | None = None,
):
    """Async generator of AG-UI events for one chat turn (Cursor SDK backend).

    ``images`` (when present) are this turn's attachments — a list of
    {"data": <base64>, "mimeType": ...} dicts already sanitized by the route.
    They attach to THIS send only; history is replayed as plain text, so a
    vision-capable model (composer-2.5) reads them on this turn.
    """
    from cursor_sdk import (
        AgentOptions,
        AsyncClient,
        LocalAgentOptions,
        LocalSendOptions,
        SendOptions,
    )

    from app.cursor_agui import cursor_run_to_agui
    from app.cursor_runner import default_cursor_model, resolve_cursor_api_key, resolve_model_selection
    from app.prompt import build_system_prompt
    from app.tools import build_demo_tools

    workspace = _ensure_workspace()
    model_name = model_override or default_cursor_model()
    api_key = resolve_cursor_api_key()
    selection = resolve_model_selection(api_key, model_name, reasoning="medium")

    prompt = _render_prompt(build_system_prompt(), list(run_input.messages))

    local = LocalAgentOptions(
        cwd=workspace,
        custom_tools=build_demo_tools(),
        store={"type": "sqlite", "root_dir": _STORE_ROOT},
    )
    # To attach an MCP server, pass mcp_servers=build_mcp_servers() here (see tools.py).
    options = AgentOptions(model=selection, api_key=api_key, local=local, mcp_servers={})
    message = _build_send_message(prompt, images)

    async with await AsyncClient.launch_bridge(workspace=workspace, local=local) as client:
        async with await client.agents.create(options) as agent:
            run = await agent.send(
                message, SendOptions(model=selection, local=LocalSendOptions(force=True))
            )
            error_hint = (
                f"model '{selection.id}' could not run via the Cursor SDK. Some models "
                "aren't available on the local bridge for every plan — composer-2.5 is "
                "known to work, and image attachments need a vision-capable model."
            )
            async for event in cursor_run_to_agui(
                run, run_input.thread_id, run_input.run_id, error_hint=error_hint
            ):
                yield event
```

Verify no Atlas references remain: `grep -n "atlas\.\|_atlas_mcp_url\|_build_system_prompt\|HttpMcpServerConfig\|build_custom_tools" backend/app/cursor_agent.py` returns nothing (the `HttpMcpServerConfig` import is now only in tools.py's comment).

- [ ] **Step 4: Write a smoke test `backend/tests/test_cursor_agent_smoke.py`** (import + signature; no live SDK call)

```python
import inspect
from app.cursor_agent import run_agent_cursor


def test_run_agent_cursor_is_async_generator_with_expected_signature():
    assert inspect.isasyncgenfunction(run_agent_cursor)
    params = inspect.signature(run_agent_cursor).parameters
    assert "model_override" in params and "images" in params
    # decoupled: no Atlas-era params
    for gone in ("user_email", "user_role", "page_filter", "credential_headers", "graph_mode"):
        assert gone not in params
```

- [ ] **Step 5: Run it**

Run: `cd backend && pytest tests/test_cursor_agent_smoke.py -v`
Expected: PASS (1 passed). The live send is covered by the Task 12 e2e.

- [ ] **Step 6: Commit**

```bash
cd /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image
git add -A && git commit -m "feat(be): lift cursor_agent orchestration, decouple to static prompt + demo tools"
```

---

### Task 7: `main.py` — FastAPI routes (`/agui`, `/models`, `/health`) + Dockerfile

**Files:**
- Create: `backend/app/main.py`
- Create: `backend/Dockerfile`
- Test: `backend/tests/test_main.py`

**Interfaces:**
- Consumes: `app.cursor_agent.run_agent_cursor`, `app.images.sanitize_images`, `app.cursor_runner.{resolve_cursor_api_key, default_cursor_model}`, `app.config.settings`.
- Produces: FastAPI `app` with `POST /agui`, `GET /models`, `GET /health`.

- [ ] **Step 1: Write the failing test `backend/tests/test_main.py`**

```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_agui_rejects_bad_body():
    r = client.post("/agui", json={"not": "a run input"})
    assert r.status_code == 422


def test_models_shape(monkeypatch):
    # Stub the SDK catalog so the test is offline.
    import app.main as m

    class _Model:
        def __init__(self, mid):
            self.id = mid

    monkeypatch.setattr(m, "_list_catalog", lambda: [_Model("composer-2.5"), _Model("gemini-2.5-pro")])
    monkeypatch.setattr(m, "default_cursor_model", lambda: "composer-2.5")
    r = client.get("/models")
    assert r.status_code == 200
    body = r.json()
    assert body["default"] == "composer-2.5"
    assert "composer-2.5" in body["models"]
```

- [ ] **Step 2: Run it, verify it fails**

Run: `cd backend && pytest tests/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 3: Write `backend/app/main.py`**

```python
"""FastAPI entrypoint for the sample Cursor SDK + AG-UI agent (no auth)."""
from __future__ import annotations

import logging

from ag_ui.core import EventType, RunAgentInput, RunErrorEvent
from ag_ui.encoder import EventEncoder
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.cursor_agent import run_agent_cursor
from app.cursor_runner import default_cursor_model, resolve_cursor_api_key
from app.images import sanitize_images

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sample-cursor-agent")

app = FastAPI(title="Sample Cursor SDK + AG-UI + Image")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
encoder = EventEncoder()


def _list_catalog():
    """Cursor model catalog (wrapped so tests can stub it)."""
    from cursor_sdk import Cursor

    return Cursor.models.list(api_key=resolve_cursor_api_key())


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/models")
async def models():
    default = default_cursor_model()
    try:
        ids = [m.id for m in _list_catalog()]
    except Exception as exc:  # noqa: BLE001 — degrade to just the default
        log.warning("model catalog fetch failed: %s", exc)
        ids = []
    if default not in ids:
        ids = [default, *ids]
    return {"models": ids, "default": default}


@app.post("/agui")
async def agui(request: Request):
    try:
        body = await request.json()
        run_input = RunAgentInput.model_validate(body)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "Invalid request", "detail": str(exc)}, status_code=422)

    forwarded = body.get("forwardedProps") or {}
    model_override = (forwarded.get("model") or "").strip() or None
    images = sanitize_images(forwarded.get("images"))

    async def event_stream():
        try:
            async for event in run_agent_cursor(
                run_input, model_override=model_override, images=images
            ):
                yield encoder.encode(event)
        except Exception as exc:  # noqa: BLE001 — surface as a terminal AG-UI error
            log.error("agent stream failed: %s", exc, exc_info=True)
            yield encoder.encode(RunErrorEvent(type=EventType.RUN_ERROR, message=str(exc)))

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 4: Run the tests, verify they pass**

Run: `cd backend && pytest tests/test_main.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Write `backend/Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && pytest -v`
Expected: PASS (config + cursor_runner + cursor_agui + images + tools + cursor_agent_smoke + main).

- [ ] **Step 7: Commit**

```bash
cd /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image
git add -A && git commit -m "feat(be): FastAPI /agui + /models + /health routes, Dockerfile"
```

---

### Task 8: Frontend scaffold (Next.js + Tailwind + lib helpers)

**Files:**
- Create: `frontend/package.json`, `frontend/next.config.ts`, `frontend/tsconfig.json`, `frontend/postcss.config.mjs`, `frontend/components.json`, `frontend/vitest.config.ts`
- Create: `frontend/src/app/{layout.tsx,page.tsx,globals.css}`
- Create: `frontend/src/lib/{utils.ts,api.ts}`
- Source for configs: copy + trim from `Niteco.Atlas/apps/frontend/`

**Interfaces:**
- Produces: `lib/api.ts` exporting `API_URL: string` (from `process.env.NEXT_PUBLIC_API_URL` || `http://localhost:8000`) and `agUiUrl()`/`modelsUrl()` helpers; `lib/utils.ts` exporting `cn(...)`.

- [ ] **Step 1: Write `frontend/package.json`** (versions copied from Atlas `apps/frontend/package.json`)

```json
{
  "name": "sample-cursor-frontend",
  "private": true,
  "scripts": {
    "dev": "next dev -p 3000",
    "build": "next build",
    "start": "next start -p 3000",
    "test": "vitest run"
  },
  "dependencies": {
    "next": "16.1.6",
    "react": "19.2.3",
    "react-dom": "19.2.3",
    "recharts": "3.8.0",
    "react-markdown": "10.1.0",
    "remark-gfm": "4.0.1",
    "lucide-react": "0.577.0",
    "@radix-ui/react-select": "2.2.6",
    "class-variance-authority": "0.7.1",
    "clsx": "2.1.1",
    "tailwind-merge": "3.4.0"
  },
  "devDependencies": {
    "@tailwindcss/postcss": "4",
    "tailwindcss": "4",
    "typescript": "5",
    "@types/node": "20",
    "@types/react": "19",
    "@types/react-dom": "19",
    "vitest": "2",
    "jsdom": "25",
    "@testing-library/react": "16"
  }
}
```
(If a listed version is unavailable at install time, match whatever `Niteco.Atlas/apps/frontend/package.json` currently pins.)

- [ ] **Step 2: Copy + trim the config files**

Copy these from `Niteco.Atlas/apps/frontend/` and strip any Atlas-only content (path aliases must keep `@/*` → `./src/*`):
```bash
SRC=/Users/hungle/Documents/Projects/Atlas/Niteco.Atlas/apps/frontend
DST=/Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image/frontend
cp $SRC/tsconfig.json $DST/tsconfig.json
cp $SRC/postcss.config.mjs $DST/postcss.config.mjs
cp $SRC/components.json $DST/components.json
cp $SRC/vitest.config.ts $DST/vitest.config.ts
```
Write a minimal `frontend/next.config.ts`:
```typescript
import type { NextConfig } from "next";
const nextConfig: NextConfig = { output: "standalone" };
export default nextConfig;
```

- [ ] **Step 3: Write `frontend/src/lib/utils.ts`**

```typescript
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

- [ ] **Step 4: Write `frontend/src/lib/api.ts`**

```typescript
export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export const agUiUrl = () => `${API_URL}/agui`;
export const modelsUrl = () => `${API_URL}/models`;
```

- [ ] **Step 5: Write `frontend/src/app/globals.css`** (Tailwind v4 + chart color vars the renderers use)

```css
@import "tailwindcss";

:root {
  --color-chart-1: #2563eb;
  --color-chart-2: #16a34a;
  --color-chart-3: #f59e0b;
  --color-chart-4: #db2777;
  --color-chart-5: #7c3aed;
}

html, body { height: 100%; margin: 0; }
```

- [ ] **Step 6: Write `frontend/src/app/layout.tsx`**

```tsx
import "./globals.css";
import type { ReactNode } from "react";

export const metadata = { title: "Sample Cursor SDK Chat" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
```

- [ ] **Step 7: Write a placeholder `frontend/src/app/page.tsx`** (replaced in Task 11)

```tsx
export default function Home() {
  return <main style={{ padding: 24 }}>Chat mounts here (Task 11).</main>;
}
```

- [ ] **Step 8: Install + build**

Run: `cd frontend && npm install && npm run build`
Expected: build succeeds (placeholder page compiles).

- [ ] **Step 9: Commit**

```bash
cd /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image
git add -A && git commit -m "feat(fe): scaffold Next.js app, tailwind, lib helpers"
```

---

### Task 9: shadcn UI primitives + `chat-payload.ts` + `chat-storage.ts` (+ ported payload test)

**Files:**
- Create: `frontend/src/components/ui/{button.tsx,select.tsx,table.tsx}`
- Create: `frontend/src/lib/chat-payload.ts`, `frontend/src/lib/chat-storage.ts`
- Test: `frontend/src/lib/__tests__/chat-payload.test.ts`
- Source: `Niteco.Atlas/apps/frontend/src/components/ui/`, `src/lib/chat-payload.ts`, `src/lib/chat-storage.ts`, `src/lib/__tests__/chat-payload.test.ts`

**Interfaces:**
- Produces: `chat-payload.ts` (`toApiMessages(...)` or the existing export name — preserve it), `chat-storage.ts` (conversation CRUD over localStorage with keys renamed to `sample-chat-*`), shadcn `Button`, `Select*`, `Table*`.

- [ ] **Step 1: Copy the shadcn primitives verbatim**

```bash
SRC=/Users/hungle/Documents/Projects/Atlas/Niteco.Atlas/apps/frontend/src
DST=/Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image/frontend/src
mkdir -p $DST/components/ui $DST/lib/__tests__
cp $SRC/components/ui/button.tsx $DST/components/ui/button.tsx
cp $SRC/components/ui/select.tsx $DST/components/ui/select.tsx
cp $SRC/components/ui/table.tsx $DST/components/ui/table.tsx
```
These import only `@/lib/utils` (`cn`) + radix/cva — already satisfied. If `select.tsx`/`table.tsx` import other ui primitives, copy those too (`grep -n "@/components/ui" $DST/components/ui/*.tsx`).

- [ ] **Step 2: Copy `chat-payload.ts` + its test**

```bash
cp $SRC/lib/chat-payload.ts $DST/lib/chat-payload.ts
cp $SRC/lib/__tests__/chat-payload.test.ts $DST/lib/__tests__/chat-payload.test.ts
```
Fix imports in both: any `@/types`/`@/lib/...` path that doesn't exist in the sample must be repointed or the referenced type inlined. `chat-payload.ts` should depend only on the `ChatMessage`/`APIMessage` types — if those live in `@/types`, copy that type file too (`cp $SRC/types/chat.ts $DST/types/chat.ts` if present) or inline the minimal type. Verify: `grep -rn "@/" $DST/lib/chat-payload.ts` resolves to files that exist.

- [ ] **Step 3: Copy + decouple `chat-storage.ts`**

```bash
cp $SRC/lib/chat-storage.ts $DST/lib/chat-storage.ts
```
Rename the localStorage keys: `atlas-chat-conversations` → `sample-chat-conversations` (and any other `atlas-chat-*` key in the file). Keep the 50-conversation / 2 MB cap + the tool-result/image-data stripping logic. Verify: `grep -n "atlas" $DST/lib/chat-storage.ts` returns nothing.

- [ ] **Step 4: Run the ported payload test**

Run: `cd frontend && npm run test -- chat-payload`
Expected: PASS (the transform is pure).

- [ ] **Step 5: Commit**

```bash
cd /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image
git add -A && git commit -m "feat(fe): shadcn primitives + chat-payload (tested) + chat-storage"
```

---

### Task 10: Chat hooks (`use-chat`, `use-chat-sidebar`, `use-conversations`)

**Files:**
- Create: `frontend/src/hooks/{use-chat.ts,use-chat-sidebar.ts,use-conversations.ts}`
- Source: `Niteco.Atlas/apps/frontend/src/hooks/{use-atlas-chat.ts,use-chat-sidebar.ts,use-conversations.ts}`

**Interfaces:**
- Consumes: `lib/api.ts` (`agUiUrl`, `modelsUrl`), `lib/chat-payload.ts`, `lib/chat-storage.ts`.
- Produces: `useChat(...)` (renamed from `useAtlasChat`) returning `{ messages, sendMessage, isStreaming, model, setModel, models, ... }`; `useChatSidebar()`; `useConversations()`.

- [ ] **Step 1: Copy the three hooks**

```bash
SRC=/Users/hungle/Documents/Projects/Atlas/Niteco.Atlas/apps/frontend/src/hooks
DST=/Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image/frontend/src/hooks
mkdir -p $DST
cp $SRC/use-atlas-chat.ts $DST/use-chat.ts
cp $SRC/use-chat-sidebar.ts $DST/use-chat-sidebar.ts
cp $SRC/use-conversations.ts $DST/use-conversations.ts
```

- [ ] **Step 2: Decouple `use-chat.ts`**

Edits (the file keeps its SSE-parsing, image-encode, and model-selection logic):
- Rename the exported hook `useAtlasChat` → `useChat`.
- Replace the fetch target: any hardcoded `/api/copilot/agui` → `agUiUrl()` from `@/lib/api`; any `/api/copilot/models?backend=...` → `modelsUrl()` (drop the `?backend=` param).
- Remove `forwardedProps` fields `tz`, `pageFilter`, `agentBackend`, `reasoning` and any imports of page/tz context and `useAuth`. Keep `model` and `images` in `forwardedProps`.
- Rename localStorage key `atlas-chat-model` → `sample-chat-model`.
- Verify: `grep -n "atlas\|copilot\|pageFilter\|useAuth\|agentBackend" $DST/use-chat.ts` returns nothing.

- [ ] **Step 3: Decouple `use-chat-sidebar.ts` + `use-conversations.ts`**

- `use-chat-sidebar.ts`: rename localStorage key `atlas-chat-sidebar-width` → `sample-chat-sidebar-width`.
- `use-conversations.ts`: repoint imports to the sample's `@/lib/chat-storage`; remove any `useAuth` usage. Verify both files: `grep -n "atlas\|useAuth" $DST/use-chat-sidebar.ts $DST/use-conversations.ts` returns nothing.

- [ ] **Step 4: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors in the hooks (sidebar component arrives in Task 11; if tsc flags the unused `page.tsx` placeholder only, that's fine — fix any hook-level type errors now).

- [ ] **Step 5: Commit**

```bash
cd /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image
git add -A && git commit -m "feat(fe): chat hooks (SSE + image + model select), decoupled from Atlas"
```

---

### Task 11: Chat UI — `chat-renderers.tsx` (verbatim) + `chat-sidebar.tsx` (decouple) + mount

**Files:**
- Create: `frontend/src/components/chat/{chat-renderers.tsx,chat-sidebar.tsx}`
- Modify: `frontend/src/app/page.tsx`
- Source: `Niteco.Atlas/apps/frontend/src/components/chat/{chat-renderers.tsx,atlas-chat-sidebar.tsx}`

**Interfaces:**
- Consumes: `useChat`, `useChatSidebar`, `useConversations` (Task 10), the renderers, the ui primitives.
- Produces: default-exported `ChatSidebar` component mounted by `app/page.tsx`.

- [ ] **Step 1: Copy `chat-renderers.tsx` verbatim**

```bash
SRC=/Users/hungle/Documents/Projects/Atlas/Niteco.Atlas/apps/frontend/src/components/chat
DST=/Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image/frontend/src/components/chat
mkdir -p $DST
cp $SRC/chat-renderers.tsx $DST/chat-renderers.tsx
```
Fix imports only: repoint `@/components/ui/*` (exists), `@/lib/utils` (exists). Verify: `grep -n "@/" $DST/chat-renderers.tsx` all resolve. It uses recharts + shadcn Table + react-markdown — all installed.

- [ ] **Step 2: Copy + decouple `chat-sidebar.tsx`**

```bash
cp $SRC/atlas-chat-sidebar.tsx $DST/chat-sidebar.tsx
```
Edits:
- Import `useChat` (not `useAtlasChat`), `useChatSidebar`, `useConversations` from the sample hooks; `chat-renderers` from `./chat-renderers`.
- Remove `useAuth` and its admin-gating: always render the full tool-call timeline (delete the `isAdmin ? detailed : simplified` branch, keep the detailed branch).
- Remove the page-context / tz-context imports and usages.
- Remove the agno/cursor backend (brain-icon) selector — keep only the model selector (fed by `useChat`'s `models`).
- Remove the feedback thumbs (the `PUT /feedback` call has no backend endpoint) — delete the thumb buttons + their handler.
- Keep: multi-bubble layout, composer with image attach + preview, model selector, conversation history, typing effect, scroll behavior.
- Ensure the component is default-exported as `ChatSidebar`.
- Verify: `grep -n "atlas\|useAuth\|copilot\|pageFilter\|agentBackend\|feedback" $DST/chat-sidebar.tsx` returns nothing (a stray comment is fine to remove).

- [ ] **Step 3: Mount it in `frontend/src/app/page.tsx`**

```tsx
"use client";
import ChatSidebar from "@/components/chat/chat-sidebar";

export default function Home() {
  return (
    <main style={{ height: "100vh" }}>
      <ChatSidebar />
    </main>
  );
}
```

- [ ] **Step 4: Type-check + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: no type errors; production build succeeds.

- [ ] **Step 5: Commit**

```bash
cd /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image
git add -A && git commit -m "feat(fe): chat renderers (verbatim) + decoupled chat sidebar, mounted"
```

---

### Task 12: docker-compose + README + frontend Dockerfile + e2e smoke

**Files:**
- Create: `docker-compose.yml`
- Create: `frontend/Dockerfile`
- Create: `README.md`

**Interfaces:**
- Consumes: backend Dockerfile (Task 7), frontend build (Task 11).

- [ ] **Step 1: Write `frontend/Dockerfile`**

```dockerfile
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
ENV NEXT_PUBLIC_API_URL=http://localhost:8000
RUN npm run build

FROM node:22-slim
WORKDIR /app
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
```
(If `public/` does not exist, create an empty `frontend/public/.gitkeep` so the COPY succeeds.)

- [ ] **Step 2: Write `docker-compose.yml`**

```yaml
services:
  backend:
    build: ./backend
    ports: ["8000:8000"]
    environment:
      CURSOR_API_KEY: ${CURSOR_API_KEY}
      CURSOR_SDK_MODEL: ${CURSOR_SDK_MODEL:-composer-2.5}
      CORS_ORIGINS: http://localhost:3000
  frontend:
    build: ./frontend
    ports: ["3000:3000"]
    environment:
      NEXT_PUBLIC_API_URL: http://localhost:8000
    depends_on: [backend]
```

- [ ] **Step 3: Write `README.md`**

Cover: what it is (fe + be + ag-ui + image, lifted from Atlas); architecture diagram (copy from the spec); prerequisites (a Cursor user/service-account key — **not** Team Admin); `cp .env.example .env` and set `CURSOR_API_KEY`; run with `docker compose up --build` (open `http://localhost:3000`) or run each tier locally (`cd backend && pip install -e ".[test]" && uvicorn app.main:app --reload` / `cd frontend && npm install && npm run dev`); the **vision caveat** (image attachments require a vision-capable model — composer-2.5); how to add a custom tool (`tools.py`) and an MCP server (uncomment the `HttpMcpServerConfig` stub); test commands (`pytest`, `npm run test`).

- [ ] **Step 4: Backend tests + frontend build gate**

Run: `cd backend && pytest -v` → all pass.
Run: `cd frontend && npm run test && npm run build` → tests pass, build succeeds.

- [ ] **Step 5: Live e2e smoke (requires a real `CURSOR_API_KEY` in `.env`)**

```bash
cp .env.example .env   # then edit .env: set CURSOR_API_KEY
docker compose up --build -d
```
Verify in a browser at `http://localhost:3000`:
1. Send "Hello" → assistant text streams in.
2. Send "What time is it in Asia/Tokyo?" → a `get_current_time` tool call appears in the timeline with a result.
3. Attach a PNG + ask "what's in this image?" (model = composer-2.5) → a vision answer renders.
4. Reload the page → the conversation is restored from localStorage.
5. `curl -s localhost:8000/models` → `{"models":[...],"default":"composer-2.5"}`.

Record the outcome of each check. If a check fails, debug before marking the task done (do not claim success without observing the behavior).

- [ ] **Step 6: Commit**

```bash
cd /Users/hungle/Documents/Projects/Atlas/sample-cursor-sdk-with-image
git add -A && git commit -m "feat: docker-compose + frontend Dockerfile + README + e2e verified"
```

---

## Self-Review

**Spec coverage** (every spec section maps to a task):
- Backend `config.py` → T1; `cursor_runner.py` → T2; `cursor_agui.py` → T3; `images.py` → T4; `tools.py`+`prompt.py` → T5; `cursor_agent.py` → T6; `main.py` (`/agui`,`/models`,`/health`,CORS) → T7.
- Frontend scaffold/configs/globals → T8; ui primitives + `chat-payload` + `chat-storage` → T9; hooks → T10; `chat-renderers` + `chat-sidebar` + mount → T11.
- Demo tool + MCP stub → T5/T6; image flow (sanitize → multimodal send) → T4/T6/T7; env/config → T1; docker-compose + README → T12.
- Verification: pure translator tests → T3; image-sanitize tests → T4; payload test → T9; e2e smoke → T12.
- Decoupling map (auth/Postgres/atlas-mcp/SQL tools/role prompt/agno/feedback removed) → enforced by the `grep` gates in T2/T3/T6/T9/T10/T11.

**Out-of-scope items** (auth, Postgres logging, atlas-mcp, SQL/Python tools, skills, agno, Postgres session store, shared package) are intentionally not implemented — confirmed against the spec's "Out of scope" section.

**Type consistency:** `run_agent_cursor(run_input, *, model_override, images)` is produced in T6 and consumed identically in T7. `/models` returns `{"models": [...], "default": ...}` in T7 and is consumed by `useChat`/`modelsUrl` in T10. `sanitize_images` (T4) → consumed in T7. `agent_turn_budget` (T2) → consumed in T3. Hook rename `useAtlasChat`→`useChat` (T10) → imported in T11. localStorage keys renamed consistently to `sample-chat-*` across T9/T10.

**Placeholder scan:** no TBD/TODO; copy-and-edit steps name exact source paths + exact symbols to delete/rename + a `grep` gate to confirm decoupling; all new modules show full code.
