"""Run one PWA room-chat turn to completion, through the Pi sidecar.

Assembles ONE result: the final assistant text plus the **structured results of
every tool call**. :mod:`app.chat` renders the bot's reply from those structured
results (never from LLM-transcribed numbers), so a ``settle_period`` payload's
amounts and QR URLs reach the user exactly as the tool computed them (design D3).

**This module is a shim, deliberately.** It builds an :class:`EngineSpec`, hands
the turn to :class:`kernos.engine.pi.PiEngine`, executes tools when asked, and
logs one line. Everything about how pi behaves — model resolution, event shapes,
turn caps, answer assembly, narration stripping, error formatting — lives in
``agent_sidecar/`` (Pi design §3.1); everything about the wire lives in
``kernos.engine``.

``run_turn``'s signature and ``TurnResult``'s shape are **frozen**: 18
``monkeypatch.setattr`` sites across 4 test files depend on them, and
``chat.py`` is the only production caller. The kernos pipeline reaches this
function through ``ToolContext.engine_spec`` / ``system_override`` /
``message_override`` (plan Task 1.4) — the one argument every fake ignores.
"""
from __future__ import annotations

import inspect
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from app.config import settings
from app.prompt import build_system_prompt
from app.tools import ToolContext, build_tools, tool_manifest
from kernos.engine import EngineSpec, ToolInvocation, TurnResult  # noqa: F401  (re-exported)

logger = logging.getLogger("chiatienan")

#: Skill bodies and the always-on rules file ship as text, so nothing is written
#: to disk. See ``session.js``: pi's skill mechanism needs a real file and defers
#: the body to ``read``, which the sidecar disables — so bodies go out as context
#: files instead.
_SKILLS_DIR = Path(__file__).resolve().parent / "agent_skills" / "skills"
_RULES_DIR = Path(__file__).resolve().parent / "agent_skills" / "rules"


def _render_prompt(user_text: str, *, sender_name: str | None = None,
                   memory: str | None = None, history: str | None = None,
                   image_count: int = 0) -> str:
    """Assemble the turn's user message — see :func:`kernos.plugins.render_sections`,
    which this delegates to with the default (Vietnamese) section headers. Kept
    here because the pipeline's ``app.run.legacy`` seam and the tests call it by
    this name; ``sender_name`` is accepted for signature compatibility."""
    from kernos.plugins import render_sections

    return render_sections(user_text, memory=memory, history=history, image_count=image_count)


def _read_skills() -> list[dict]:
    """The ``SKILL.md`` bodies, as text. Their frontmatter is already
    ``name`` + ``description``, which is exactly what pi wants."""
    skills = []
    if not _SKILLS_DIR.is_dir():
        return skills
    for directory in sorted(_SKILLS_DIR.iterdir()):
        path = directory / "SKILL.md"
        if path.is_file():
            skills.append({"name": directory.name, "description": "",
                           "body": path.read_text(encoding="utf-8")})
    return skills


def _read_context_files() -> list[dict]:
    """``money-safety.mdc`` had no pi always-apply equivalent, so it ships as a
    context file — which pi loads into every system prompt."""
    files = []
    for path in sorted(_RULES_DIR.glob("*.mdc")) if _RULES_DIR.is_dir() else []:
        files.append({"path": path.stem, "content": path.read_text(encoding="utf-8")})
    return files


def default_engine_spec() -> EngineSpec:
    """Today's configuration as an :class:`EngineSpec`: env settings, the skill
    files and the rules file. ``system`` is left ``None`` — it depends on the
    sender and is rendered per turn. This is what the pipeline's seeded default
    profile must reproduce (plan Task 1.7)."""
    return EngineSpec(
        model=settings.pi_model,
        vision_model=settings.pi_vision_model,
        thinking=settings.pi_thinking,
        builtin_tools=list(settings.pi_builtin_tools),
        max_tools=settings.pi_max_tools,
        max_seconds=settings.pi_max_seconds,
        # pi needs a real cwd and its own config dir. Keeping the latter under
        # DATA_DIR lets it cache the model catalogue across turns instead of
        # refetching it every time.
        cwd=str(Path(settings.data_dir) / "pi-cwd"),
        agent_dir=str(Path(settings.data_dir) / "pi-agent"),
        skills=_read_skills(),
        context_files=_read_context_files(),
    )


async def run_turn(user_text: str, ctx: ToolContext, images=None, emit=None,
                   memory=None, history=None) -> TurnResult:
    """Run one turn to completion and return the assembled :class:`TurnResult`.

    ``emit`` — optional ``Callable[[dict], Awaitable[None]]`` — receives the live
    ``agent.*`` timeline events for this turn's SSE stream. The sidecar emits them
    already in the app's wire format, so they are forwarded untouched.
    """
    from app.pi_bridge import get_bridge
    from kernos.engine.pi import PiEngine

    turn_id = uuid.uuid4().hex[:12]
    started = time.monotonic()

    spec = getattr(ctx, "engine_spec", None) or default_engine_spec()
    system = getattr(ctx, "system_override", None)
    if system is None:
        system = spec.system if spec.system is not None else build_system_prompt(
            sender_name=ctx.sender_name, sender_id=ctx.sender_member_id)
    message = getattr(ctx, "message_override", None)
    if message is None:
        message = _render_prompt(user_text, sender_name=ctx.sender_name, memory=memory,
                                 history=history, image_count=len(images or []))
    spec = replace(spec, system=system, **(getattr(ctx, "caps_override", None) or {}))   # a nested run's budget (Phase 7)
    ctx.turn_id = turn_id
    if getattr(ctx, "started_at", None) is None:
        ctx.started_at = started

    tools = build_tools(ctx)

    async def call_tool(name: str, args: dict):
        ctx.calls_made += 1
        tool = tools.get(name)
        if tool is None:
            return {"ok": False, "error": f"unknown tool {name}"}
        before = getattr(ctx, "validate_call", None)
        if before is not None:                         # the profile's tool_args rules (plan Task 6.2)
            refused = await before(name, args)
            if refused is not None:
                return refused
        try:
            result = tool.execute(args)
            if inspect.isawaitable(result):            # the delegation pack's nested run
                result = await result
        except Exception as exc:  # noqa: BLE001 — a tool must not kill the turn
            logger.exception("[agent] tool %s raised", name)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        after = getattr(ctx, "validate_result", None)
        if after is not None:
            refused = await after(name, args, result)
            if refused is not None:
                return refused
        return result

    engine = PiEngine(get_bridge())
    result = await engine.run(spec, turn_id=turn_id, message=message, images=list(images or []),
                              tools=tool_manifest(ctx), call_tool=call_tool, emit=emit)
    if getattr(ctx, "sub_invocations", None):
        result.tools = _merge_sub_invocations(result.tools, ctx.sub_invocations)
    stats = result.stats or {}

    # One line per turn so the log mirror (and /internal/debug/logs) shows where a
    # 20–80s turn actually went: how many tool round-trips, and which. The agent.*
    # timeline has this live but never persists it.
    logger.info(
        "[agent] turn %s done in %.1fs tools=%d (%s) images=%d text=%dch"
        " tokens=%s cost=%s%s%s",
        turn_id, time.monotonic() - started, len(result.tools),
        ",".join(inv.name for inv in result.tools) or "-",
        len(images or []), len(result.final_text),
        stats.get("tokens", "?"), stats.get("cost", "?"),
        " CAPPED" if result.capped else "",
        f" ERROR={result.error}" if result.error else "",
    )
    return result


def _merge_sub_invocations(own: list, subs: list) -> list:
    """The manager's own invocations with each sub-agent's calls right after the
    ``ask_*`` call that made them (``(own_call_index, invocation)`` pairs), so the
    record reads in the order things happened (design §6)."""
    after = defaultdict(list)
    for index, inv in subs:
        after[index].append(inv)
    merged = []
    for i, inv in enumerate(own):
        merged.append(inv)
        merged.extend(after.pop(i, []))
    for rest in after.values():
        merged.extend(rest)
    return merged
