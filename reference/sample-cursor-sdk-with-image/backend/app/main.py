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
