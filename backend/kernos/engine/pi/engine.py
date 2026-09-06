"""``PiEngine``: one turn through the sidecar, hydrated into a ``TurnResult``.

The loop body of what ``app.agent.run_turn`` used to do (plan Task 1.4). It
forwards ``agent.*`` events untouched, hands every ``tool_call`` to the host's
executor and posts the ``tool_result`` back, and hydrates ``turn_done`` field
for field. It never raises: a dead bridge is an ``error`` on the result and an
``agent.run.error`` event, exactly as before. It writes no log line of its own —
the host logs one summary line per turn.
"""
from __future__ import annotations

import json
import logging

from kernos.engine.base import EngineSpec, EventEmitter, ToolExecutor, ToolInvocation, ToolSpec, TurnResult

logger = logging.getLogger("kernos.engine.pi")


def _json_dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


class PiEngine:
    def __init__(self, bridge) -> None:
        self._bridge = bridge

    async def run(self, spec: EngineSpec, *, turn_id: str, message: str, images: list | None,
                  tools: list[dict | ToolSpec], call_tool: ToolExecutor,
                  emit: EventEmitter | None) -> TurnResult:
        result = TurnResult(turn_id=turn_id)
        command = spec.to_run_command(req_id=f"run-{turn_id}", turn_id=turn_id,
                                      message=message, images=images, tools=tools)

        async def _emit(event: dict) -> None:
            if emit:
                await emit(event)

        try:
            async for reply in self._bridge.request(command):
                kind = reply.get("type", "")

                if kind.startswith("agent."):
                    # Already the frontend's format. Forward, do not interpret.
                    await _emit(reply)

                elif kind == "tool_call":
                    name = reply.get("name") or ""
                    args = reply.get("args") or {}
                    payload = await call_tool(name, args)
                    # The executor contract (design §6): a payload carrying ``_record`` is
                    # recorded as that value and sent to the model without it — so what
                    # the model reads (a sub-agent's prose) and what backs the reply's
                    # numbers (structured results) can differ on purpose.
                    record = payload
                    if isinstance(payload, dict) and "_record" in payload:
                        record = payload["_record"]
                        payload = {k: v for k, v in payload.items() if k != "_record"}
                    result.tools.append(ToolInvocation(name=name, args=args, result=record))
                    await self._bridge.send({
                        "type": "tool_result", "req_id": command["req_id"],
                        "call_id": reply.get("call_id"),
                        "content": _json_dumps(payload),
                    })

                elif kind == "turn_done":
                    result.final_text = reply.get("final_text") or ""
                    result.error = reply.get("error")
                    result.capped = bool(reply.get("capped"))
                    # The sidecar reports `null` rather than 0 when the provider did
                    # not say, so an unknown cost never reads as "free".
                    result.stats = reply.get("stats") or {}
                    # `tools` is already accumulated from the round-trips above, which
                    # is the authoritative list: those results came from the real DB.

                elif kind == "fatal":
                    result.error = reply.get("message") or "sidecar failed"
                    await _emit({"type": "agent.run.error", "turn_id": turn_id,
                                 "message": result.error})
        except Exception as exc:  # noqa: BLE001 — a failed turn must still be a reply
            logger.exception("[pi] turn %s failed", turn_id)
            result.error = f"{type(exc).__name__}: {exc}"
            await _emit({"type": "agent.run.error", "turn_id": turn_id, "message": result.error})

        return result
