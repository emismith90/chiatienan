# Cursor SDK Boilerplate — Chat with Images

A standalone boilerplate demonstrating the **Cursor SDK** (AG-UI streaming protocol) wired to a Next.js chat frontend with image attachment support. Lifted and decoupled from a larger app (Atlas) — no authentication, no Postgres, no shared packages.

---

## What it is

| Layer | Stack |
|---|---|
| Frontend | Next.js 16, React 19, Tailwind CSS, AG-UI client |
| Backend | FastAPI, Python 3.12, `cursor-sdk`, AG-UI server |
| Streaming | Server-Sent Events via AG-UI protocol |
| Images | FileReader → base64 → `forwardedProps.images` → multimodal UserMessage |

---

## Architecture

```
Browser
  │
  ├─ POST /agui   (SSE stream, body: { prompt, model?, images? })
  │       │
  │       └─► Cursor SDK agent ──► AG-UI events (TEXT_MESSAGE_*, TOOL_CALL_*, RUN_*)
  │
  └─ GET  /models  →  { models: [...], default: "composer-2.5" }


Image path
  FileReader (browser)
    → base64 data-URL
    → forwardedProps.images[]
    → backend sanitize_images()
    → multimodal UserMessage content parts
    → Cursor SDK (vision model required)
```

---

## Prerequisites

- **Docker** (for the compose path) or Node 22 + Python 3.12 (for local dev).
- A **Cursor user or service-account API key** — get it from your Cursor account settings.
  > **NOTE:** This must be a *user* or *service-account* key. Team Admin keys do not work with the SDK.

---

## Setup

```bash
cp .env.example .env
# Edit .env and set CURSOR_API_KEY=<your-key>
```

`.env.example` already contains sensible defaults for all other variables.

---

## Run with Docker Compose

```bash
docker compose up --build
```

Open [http://localhost:3000](http://localhost:3000).

The backend API is available at [http://localhost:8000](http://localhost:8000).

---

## Run locally (without Docker)

**Backend**

```bash
cd backend
pip install -e ".[test]"
uvicorn app.main:app --reload
# Listening on http://localhost:8000
```

**Frontend** (separate terminal)

```bash
cd frontend
npm install
npm run dev
# Listening on http://localhost:3000
```

Set `CURSOR_API_KEY` in your shell or in `.env` before starting the backend.

---

## Vision caveat

Image attachments (the paperclip button) require a **vision-capable model**.
The default model is `composer-2.5` which supports vision.
If you switch to a text-only model the backend will strip the image parts; the
request will succeed but the model will not see the images.

---

## Extending

### Add a custom tool

Edit `backend/app/tools.py`. The `build_demo_tools()` function returns a
`dict[str, CustomTool]`. Add a new entry:

```python
from cursor_sdk import CustomTool

def build_demo_tools() -> dict[str, CustomTool]:
    def my_tool(args, ctx):
        return {"result": "hello"}

    return {
        # ... existing tools ...
        "my_tool": CustomTool(
            execute=my_tool,
            description="A short description the model will see.",
            input_schema={"type": "object", "properties": {"arg1": {"type": "string"}}},
        ),
    }
```

The backend picks up `build_demo_tools()` automatically via `cursor_agent.py`.

### Attach an MCP server

Uncomment the `HttpMcpServerConfig` stub at the bottom of `backend/app/tools.py`:

```python
from cursor_sdk import HttpMcpServerConfig

def build_mcp_servers() -> dict:
    return {
        "my-mcp": HttpMcpServerConfig(
            url="https://my-host/mcp",
            headers={"Authorization": "Bearer <token>"},
        )
    }
```

Then pass `mcp_servers=build_mcp_servers()` into `AgentOptions` in
`backend/app/cursor_agent.py`.

---

## Tests

**Backend** (30 tests — unit + integration with mocked Cursor SDK)

```bash
cd backend
pytest -v
```

**Frontend** (Vitest — chat-payload shape unit tests)

```bash
cd frontend
npm test
```

---

## Manual e2e checklist

Run these checks after `docker compose up --build` (or local dev setup) with a
real `CURSOR_API_KEY` and the `composer-2.5` model:

1. **Text streaming** — Send "Hello" in the chat box. The assistant reply should
   stream in token by token.

2. **Tool call** — Send "What time is it in Asia/Tokyo?". A `get_current_time`
   tool-call card should appear in the message timeline with a result.

3. **Image attachment** — Click the paperclip, attach any PNG, and ask "What's
   in this image?". The model should describe the image (requires `composer-2.5`
   or another vision model).

4. **Persistence** — Reload the page. The conversation should restore from
   `localStorage` (`sample-chat-conversations` key).

5. **Models endpoint** — Run:
   ```bash
   curl -s localhost:8000/models
   ```
   Expected response shape: `{"models":[...],"default":"composer-2.5"}`.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CURSOR_API_KEY` | — | **Required.** User/service-account key. |
| `CURSOR_SDK_MODEL` | `composer-2.5` | Model used by the backend agent. |
| `CURSOR_SDK_WORKSPACE` | `/tmp/sample-cursor-agent` | Scratch workspace for the SDK. |
| `CURSOR_API_BASE` | `https://api.cursor.com` | Override for testing. |
| `CURSOR_AGENT_MAX_TOOLS` | `500` | Tool-call budget per run. |
| `CURSOR_AGENT_MAX_SECONDS` | `1800` | Timeout per run. |
| `CORS_ORIGINS` | `http://localhost:3000` | Allowed CORS origins. |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend URL seen by the browser. **Built at image build-time** — pass as a build arg, rebuild image to change. |
