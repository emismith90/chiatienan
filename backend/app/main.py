"""FastAPI entrypoint: mounts ``/api/messages``, ``/admin``, ``/health``.

Startup builds the DB, and — when bot credentials are present — the Bot Framework
adapter, the single-consumer worker, and the ``TeamsBot`` that ties them
together. Without bot creds the app still boots so ``/admin`` and ``/health``
work (useful for setting up the roster before the bot is registered).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.admin import router as admin_router
from app.bridge_smoke import run_bridge_smoke
from app.config import settings
from app.db import get_db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("chiatienan")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = get_db()
    app.state.bot = None
    app.state.worker = None

    if settings.microsoft_app_id and settings.microsoft_app_password:
        # Imported lazily so the app can boot (health/admin) without botbuilder
        # creds configured yet.
        from app.teams import TeamsBot, build_adapter
        from app.worker import Worker

        holder: dict = {}

        async def _send(reference, reply):
            await holder["bot"].send_reply(reference, reply)

        worker = Worker(app.state.db, send=_send)
        bot = TeamsBot(build_adapter(), worker)
        holder["bot"] = bot
        worker.start()
        app.state.bot = bot
        app.state.worker = worker
        log.info("chiatienan bot configured (app_id=%s…)", settings.microsoft_app_id[:8])
    else:
        log.warning("MICROSOFT_APP_ID/PASSWORD not set — /api/messages disabled; /admin + /health only")

    try:
        yield
    finally:
        if app.state.worker is not None:
            await app.state.worker.stop()


app = FastAPI(title="chiatienan — Teams lunch bot", lifespan=lifespan)
app.include_router(admin_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/messages")
async def messages(request: Request):
    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        return JSONResponse(
            {"error": "bot not configured (set MICROSOFT_APP_ID/PASSWORD)"}, status_code=503
        )
    body = await request.json()
    auth_header = request.headers.get("Authorization", "")
    try:
        await bot.process_request(body, auth_header)
    except Exception as exc:  # noqa: BLE001 — auth failure etc.
        log.warning("inbound activity rejected: %s", exc)
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return Response(status_code=200)


@app.post("/internal/bridge-smoke")
async def bridge_smoke(x_admin_password: str | None = Header(default=None)):
    """Guarded Cursor SDK bridge validation (B3) — see app.bridge_smoke."""
    if not settings.admin_password or x_admin_password != settings.admin_password:
        raise HTTPException(status_code=401, detail="unauthorized")
    return await run_bridge_smoke()
