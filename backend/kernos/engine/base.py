"""The engine boundary (design §4.6): what runs the model loop for one turn.

``EngineSpec`` is the configuration half of the ``run`` command the Pi sidecar
takes today — everything a profile decides, minus the per-turn parts (message,
images, tool manifest). ``TurnResult`` is the frozen result shape ``app.chat``
renders from; it moved here unchanged from ``app.agent`` and is re-exported
there.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


@dataclass
class ToolInvocation:
    name: str
    args: dict | None
    result: object
    #: Set when a sub-agent made the call (design §6). ``None`` = this agent's own.
    from_agent: str | None = None


@dataclass
class TurnResult:
    final_text: str = ""
    tools: list[ToolInvocation] = field(default_factory=list)
    error: str | None = None
    turn_id: str | None = None
    #: The turn hit ``max_seconds`` or ``max_tools`` and was cut short. **Not an
    #: error** — a cap keeps whatever was accumulated (see ``turn.js:runTurn``) — but
    #: the caller has to know, because a cap that lands before the model has
    #: written anything leaves ``final_text`` empty and is indistinguishable from a
    #: provider returning nothing. Production, 2026-08-14 room 3: "ăn gì ngon ngon
    #: đi mày" ran `suggest_lunch` and was cut at 120.6s with 0 characters, and the
    #: room got the same bare "(no response)" as a genuinely empty completion 100
    #: minutes earlier. The same question answered in 69.0s / 344ch just before and
    #: 79.5s / 602ch just after, so it was a timeout, not a failure — and the room
    #: could not tell.
    capped: bool = False
    #: Token/cost/elapsed as the engine reported them, for the log line only.
    #: ``None`` when the engine did not say — never 0, which would read as "free".
    stats: dict | None = None

    def last_result(self, name: str) -> dict | None:
        """Most-recent successful (``ok``) result dict for a given tool name."""
        for inv in reversed(self.tools):
            if inv.name == name and isinstance(inv.result, dict) and inv.result.get("ok"):
                return inv.result
        return None

    def all_results(self, name: str) -> list[dict]:
        """All successful (``ok``) result dicts for a tool name, in call order."""
        return [inv.result for inv in self.tools
                if inv.name == name and isinstance(inv.result, dict) and inv.result.get("ok")]


@dataclass(frozen=True)
class ToolSpec:
    """What the model is told about a tool: the manifest entry."""

    name: str
    description: str
    schema: dict

    def to_wire(self) -> dict:
        return {"name": self.name, "description": self.description, "schema": self.schema}


@dataclass
class EngineSpec:
    """The profile-decided half of a turn.

    ``system`` may be ``None`` when the caller renders it per turn (it depends on
    the sender). ``settings`` and ``extensions`` are new with kernos and are left
    off the wire when empty, so the command stays byte-identical to today's.
    """

    model: str
    vision_model: str | None
    thinking: str
    builtin_tools: list[str]
    max_tools: int
    max_seconds: int
    cwd: str
    agent_dir: str
    system: str | None = None
    skills: list[dict] = field(default_factory=list)
    context_files: list[dict] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    extensions: list = field(default_factory=list)

    def to_run_command(self, *, req_id: str, turn_id: str, message: str,
                       images: list | None, tools: list[dict | ToolSpec]) -> dict:
        command = {
            "type": "run",
            "req_id": req_id,
            "turn_id": turn_id,
            "system": self.system or "",
            "message": message,
            "images": list(images or []),
            "tools": [t.to_wire() if isinstance(t, ToolSpec) else t for t in tools],
            "skills": list(self.skills),
            "context_files": list(self.context_files),
            "cwd": self.cwd,
            "agent_dir": self.agent_dir,
            "model": self.model,
            "vision_model": self.vision_model,
            "thinking": self.thinking,
            "builtin_tools": list(self.builtin_tools),
            "max_tools": self.max_tools,
            "max_seconds": self.max_seconds,
        }
        if self.settings:
            command["settings"] = dict(self.settings)
        if self.extensions:
            command["extensions"] = list(self.extensions)
        return command


#: ``(name, args) -> result payload``. The host owns tool execution and its error
#: policy (``{ok: false}`` on failure, never a raise that kills the turn).
ToolExecutor = Callable[[str, dict], Awaitable[Any]]

#: ``(event) -> None``; receives already-formatted live events.
EventEmitter = Callable[[dict], Awaitable[None]]


class Engine(Protocol):
    async def run(self, spec: EngineSpec, *, turn_id: str, message: str, images: list | None,
                  tools: list[dict | ToolSpec], call_tool: ToolExecutor,
                  emit: EventEmitter | None) -> TurnResult: ...
