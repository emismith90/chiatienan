"""A scripted sidecar for tests and hosts with no Node (Phase 9 review F11).

``ScriptedBridge`` yields the reply lines one ``run`` command would get from the real
sidecar — ``agent.*`` events, ``tool_call``s the engine answers through the host's
executor, and the closing ``turn_done`` — exactly as ``tests/test_agent.py``'s scripts
are written. ``ScriptedEngine`` is the real :class:`~kernos.engine.pi.PiEngine` over
such a bridge, so the ``_record`` contract, the hydration and the error path are the
production code, not a copy.
"""
from __future__ import annotations

import json

from kernos.engine.pi.engine import PiEngine


class ScriptedBridge:
    """One bridge, several runs: the first ``run`` command gets ``scripts[0]``, every later
    one the next script (a sub-agent's nested run, for instance). Records every command
    (``runs``) and everything Python sent back (``sent``)."""

    def __init__(self, *scripts: list[dict]) -> None:
        self._scripts = [list(s) for s in scripts]
        self.runs: list[dict] = []
        self.sent: list[dict] = []

    async def request(self, command: dict):
        self.runs.append(command)
        if not self._scripts:
            yield {"type": "fatal", "req_id": command["req_id"], "message": "scripted bridge has no script for this run"}
            return
        for message in self._scripts.pop(0):
            yield dict(message, req_id=command["req_id"])

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    async def aclose(self) -> None:
        return None

    def tool_result(self, call_id: str) -> dict:
        """What Python sent back for one ``tool_call`` (decoded)."""
        for m in self.sent:
            if m["type"] == "tool_result" and m["call_id"] == call_id:
                return json.loads(m["content"])
        raise AssertionError(f"no tool_result for {call_id}")


class ScriptedEngine(PiEngine):
    """``PiEngine`` over a :class:`ScriptedBridge`; ``.bridge`` exposes the recording."""

    def __init__(self, *scripts: list[dict]) -> None:
        self.bridge = ScriptedBridge(*scripts)
        super().__init__(self.bridge)
