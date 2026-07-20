"""chiatienan FastAPI app — deployment skeleton.

Routes:
  GET  /health                 liveness (Caddy / uptime checks)
  POST /api/messages           Teams webhook — stub (botbuilder wiring lands later)
  POST /internal/bridge-smoke  guarded Cursor SDK bridge validation (B3)
"""
from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Request

from app.bridge_smoke import run_bridge_smoke
from app.config import settings

app = FastAPI(title="chiatienan")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/messages")
async def messages(request: Request) -> dict:
    # Teams Bot Connector webhook. Real handling (verify, enqueue, proactive
    # reply) is added in the implementation phase; stub returns 200 so channel
    # configuration can be validated.
    return {"status": "not_implemented"}


@app.post("/internal/bridge-smoke")
async def bridge_smoke(x_admin_password: str | None = Header(default=None)) -> dict:
    if not settings.admin_password or x_admin_password != settings.admin_password:
        raise HTTPException(status_code=401, detail="unauthorized")
    return await run_bridge_smoke()
