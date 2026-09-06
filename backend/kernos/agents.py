"""``kernos.agents``: a manager agent asking its sub-agents (design §6, plan Task 7.1).

:class:`DelegationPack` is a kernos pack (id ``delegation``) the host enables for a turn
whose agent has a non-empty ``delegates_to``. For every sub it generates a tool
``ask_<sub_slug>(task)``; executing one is a **nested pipeline run** of the sub's
profile in the same space, for the same principal — the host supplies it as
``run_sub`` — and the tool hands the model the sub's final text and its structured
tool results.

Three rules keep money safety (design D3) intact across the boundary:

* **Results merge, text does not.** The payload the model reads is ``{ok, text,
  results, capped}``; the *recorded* invocation is ``{ok, agent, results}`` (the
  executor contract ``_record``), so a number that appears only in the sub's prose
  never backs the manager's reply. Every tool call the sub made is appended to the
  manager's ``TurnResult.tools`` tagged ``from_agent=<slug>``.
* **The sub's proposals are data.** A sub's ``propose_*`` creates no card (the render
  stage reads own invocations only); the tool description says so, and that the
  manager must call ``propose_*`` itself for a card.
* **The sub runs inside the manager's budget** (Phase 7 review F1): its ``max_seconds``
  is the smaller of its own cap and what the manager has left minus a margin, its
  ``max_tools`` the smaller of its own cap and the manager's remaining calls. Below a
  five-second floor the tool refuses without running.

Recursion is bounded by the **root** agent's ``max_depth`` (F8): ``ask_*`` tools exist
only while ``depth + 1 < max_depth`` — with the default 2 the manager delegates and its
subs do not. Cycles in ``delegates_to`` are legal and terminate there.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from kernos.packs import BasePack, PackTool, err

log = logging.getLogger("kernos.agents")

#: Seconds kept back from the manager's remaining budget so it can still answer after
#: the sub returns (F1). A pack config, not a profile field: it is a property of the
#: nesting, not of any one agent.
DEFAULT_MARGIN_SECONDS = 15
#: A nested run with less than this cannot do anything useful; refuse instead.
FLOOR_SECONDS = 5

_TASK_SCHEMA = {
    "type": "object",
    "properties": {"task": {"type": "string", "description": "What to find out or do, in full — the sub-agent sees nothing else of this conversation."}},
    "required": ["task"],
    "additionalProperties": False,
}


def tool_name(slug: str) -> str:
    return "ask_" + slug.replace("-", "_")


def describe(sub: dict) -> str:
    """The manifest description: who the sub is, what it does, and the rule that its
    proposals are data (F2)."""
    text = f"Ask the sub-agent {sub['name']}"
    if sub.get("description"):
        text += f" — {sub['description'].strip()}"
    text += (". Returns its answer (`text`) and the structured results of every tool it called (`results`). "
             "Its numbers are trustworthy only where they appear in `results`; anything it merely says is not. "
             "A proposal the sub-agent makes is information for you, not a card: to record anything, "
             "call the propose_* tool yourself.")
    return text


class DelegationPack(BasePack):
    id, version, handles_money = "delegation", "1", False

    def __init__(self, agents_of: Callable[[dict], list[dict]],
                 run_sub: Callable[..., Awaitable[dict]], *, margin_seconds: int = DEFAULT_MARGIN_SECONDS) -> None:
        """``agents_of(agent) -> [sub, …]`` resolves an agent's ``delegates_to`` to the
        sub records it may ask (the host skips and logs anything that is not a ``sub``
        of the same business). ``run_sub(tool_ctx, sub, task, budget={max_tools,
        max_seconds}) -> {text, results, capped, invocations, error}`` is the nested run."""
        self._agents_of, self._run_sub, self._margin = agents_of, run_sub, margin_seconds

    def tools(self, ctx: Any) -> dict[str, PackTool]:
        agent = getattr(ctx, "agent", None)
        if not agent or not agent.get("delegates_to"):
            return {}
        depth = getattr(ctx, "depth", 0) or 0
        max_depth = getattr(ctx, "max_depth", None) or agent.get("max_depth") or 2
        if depth + 1 >= max_depth:
            return {}
        out: dict[str, PackTool] = {}
        for sub in self._agents_of(agent):
            name = tool_name(sub["slug"])
            out[name] = PackTool(name, describe(sub), _TASK_SCHEMA, self._asker(ctx, sub))
        return out

    def _asker(self, ctx: Any, sub: dict):
        async def ask(args: dict | None) -> dict:
            task = (args or {}).get("task")
            if not isinstance(task, str) or not task.strip():
                return err("Missing task: say what the sub-agent should find out or do.")
            budget = self.budget(ctx)
            if budget["max_seconds"] < FLOOR_SECONDS:
                return err("no time budget left to delegate")
            out = await self._run_sub(ctx, sub, task.strip(), budget=budget)
            index = max(getattr(ctx, "calls_made", 1) - 1, 0)
            for inv in out.get("invocations") or []:
                ctx.sub_invocations.append((index, inv))
            ok = not out.get("error")
            results = out.get("results") or []
            payload = {"ok": ok, "text": out.get("text") or "", "results": results,
                       "capped": bool(out.get("capped")),
                       "_record": {"ok": ok, "agent": sub["slug"], "results": results}}
            if not ok:
                payload["error"] = payload["_record"]["error"] = out["error"]
            return payload
        return ask

    def budget(self, ctx: Any) -> dict:
        """What the manager has left for a sub: ``max_seconds − elapsed − margin`` and
        ``max_tools − own calls − sub calls so far`` (floor 1). The manager's caps come
        from its ``EngineSpec``; a context without one runs on the profile defaults."""
        spec = getattr(ctx, "engine_spec", None)
        max_seconds = getattr(spec, "max_seconds", 120)
        max_tools = getattr(spec, "max_tools", 40)
        started = getattr(ctx, "started_at", None)
        elapsed = (time.monotonic() - started) if started is not None else 0.0
        # `calls_made` counts the in-flight `ask_*` call too; the budget is what was spent before it
        used = max((getattr(ctx, "calls_made", 0) or 0) - 1, 0) + len(getattr(ctx, "sub_invocations", None) or [])
        return {"max_seconds": int(max_seconds - elapsed - self._margin),
                "max_tools": max(1, max_tools - used)}
