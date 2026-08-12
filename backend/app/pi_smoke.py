"""``/internal/bridge-smoke``: is the sidecar alive and answering?

The cheapest possible signal, and deliberately **not** under
``chat._agent_lock`` — a smoke check has to be answerable while a turn is running,
which is why every RPC message carries a ``req_id``.

The response shape is kept for ``deploy/DEBUGGING.md`` compatibility, so
``messages_seen`` has to mean something rather than be fabricated: it is the count
of JSONL messages the sidecar emitted for this ``req_id`` (1 for a healthy ping).
"""
from __future__ import annotations

import logging
import time
import uuid

logger = logging.getLogger("chiatienan")


async def run_bridge_smoke(prompt: str = "Reply with the single word: pong.") -> dict:
    """Ping the sidecar. Never raises — a smoke check reports, it does not fail."""
    from app.pi_bridge import get_bridge

    req_id = f"ping-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    messages_seen = 0
    text = ""
    error = None

    try:
        bridge = get_bridge()
        async for message in bridge.request({"type": "ping", "req_id": req_id}):
            messages_seen += 1
            if message.get("type") == "pong":
                text = message.get("text") or "pong"
            elif message.get("type") == "fatal":
                error = message.get("message") or "sidecar failed"
    except Exception as exc:  # noqa: BLE001 — report, do not raise
        error = f"{type(exc).__name__}: {exc}"

    return {
        "ok": error is None and bool(text),
        "elapsed_s": round(time.monotonic() - started, 1),
        "messages_seen": messages_seen,
        "text": text,
        **({"error": error} if error else {}),
    }
