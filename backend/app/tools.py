"""The host's tool composition point (plan Task 3.3).

The tool bodies live in the packs — the money tools in the framework's
``packs/lunch_ledger``, the restaurant and roster tools in ``app/packs`` — and
this module is what the host and every test build them through: ``ToolContext``
(the per-turn context the tools close over), ``CustomTool`` (the shape the
executor runs), ``build_tools`` (the enabled packs' tools, in the legacy order)
and ``tool_manifest`` (what the sidecar is told).

Money-safety (design D3) is unchanged by the move: the model decides *when* to
call a tool; the tools own all arithmetic and all QR-building, and numbers that
end up in a QR never round-trip tool → LLM → tool.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from app.clock import today_ict
from app.db import Database

logger = logging.getLogger("chiatienan")


@dataclass
class CustomTool:
    """The LLM-facing tool shape, owned here now that the vendor SDK is gone.

    Same three fields the SDK's class carried, so every registration and every
    executor body are byte-identical to what they were. Nothing about arithmetic
    changed with the engine.
    """

    execute: object
    description: str
    input_schema: dict


@dataclass
class ToolContext:
    """Per-turn context the tools close over (never seen by the model).

    Room-scoped: every tool call is confined to ``room_id``, and the sender is
    whoever is logged in for this PWA session (``sender_member_id``) — a plain
    room member id, not any external chat-platform identity.
    """

    db: Database
    room_id: int
    sender_member_id: int | None = None
    sender_name: str | None = None
    # People @mentioned in this message (bot mention already stripped):
    turn_mentions: list[dict] = field(default_factory=list)
    # Names this turn looked up that pinned to no member (or to two). Kept so
    # ``propose_meal`` can refuse to quietly leave that person out of the split
    # — see ``packs.lunch_ledger.tools._dropped_names``.
    unknown_names: dict[str, str] = field(default_factory=dict)
    # The kernos seam (plan Task 1.4, review finding 1). When a pipeline resolved
    # a profile it puts the engine spec and the rendered system/message here, and
    # ``agent.run_turn`` uses them instead of building from ``settings``. ``None``
    # means "build as today". Riding on ``ToolContext`` — the one argument every
    # test fake of ``run_turn`` ignores — is what lets the frozen six-argument
    # signature stay frozen.
    engine_spec: object | None = None
    system_override: str | None = None
    message_override: str | None = None
    # Which packs/tools the resolved profile enables (`spec.tool_packs`, dumped), set
    # by the pipeline's run plugin (plan Task 3.1). `None` = today's 19 tools.
    tool_config: dict | None = None
    # What the framework's packs need from this host (plan Task 3.3): the card
    # store (pending drafts, cancel), the local date, and the uniform draw. Filled
    # by :func:`_inject` when left ``None``, so the tests that patch
    # ``app.tools.today_ict`` / ``app.tools.random.choice`` still steer the tools.
    cards: Any = None
    today: Callable[[], date] | None = None
    choice: Callable[[list], Any] | None = None
    # The profile's tool-scope validation rules (plan Task 6.2), set by the run plugin:
    # ``await validate_call(name, args) -> {ok: False, error} | None`` before a tool runs,
    # ``await validate_result(name, args, result)`` after. ``None`` = no rules.
    validate_call: Callable | None = None
    validate_result: Callable | None = None

    @property
    def space_id(self):
        """The pack-side name for the room (design §3: a *space*)."""
        return self.room_id


def _inject(ctx: ToolContext) -> ToolContext:
    if ctx.cards is None:
        from app.hostadapters import RoomCards
        ctx.cards = RoomCards(ctx.db)
    if ctx.today is None:
        ctx.today = lambda: today_ict()      # looked up at call time: tests patch it here
    if ctx.choice is None:
        ctx.choice = lambda pool: random.choice(pool)
    return ctx


def _from_pack(tool) -> CustomTool:
    return CustomTool(execute=tool.execute, description=tool.description, input_schema=tool.schema)


def _legacy_build_tools(ctx: ToolContext) -> dict[str, CustomTool]:
    """All 19 tools, in the order this module has always listed them: the three
    host packs composed with no profile — every test fake, the bench, the probe."""
    from app.packs import LEGACY_ORDER, host_packs

    _inject(ctx)
    tools = {}
    for pack in host_packs():
        tools.update(pack.tools(ctx))
    return {name: _from_pack(tools[name]) for name in LEGACY_ORDER}


def build_tools(ctx: ToolContext) -> dict[str, CustomTool]:
    """The tools this turn may call.

    With no ``tool_config`` on the context — every test fake, the bench, the probe —
    this is today's 19 tools in today's order. With one, the enabled packs are asked,
    the per-tool overrides applied, and the result put back into legacy order so the
    manifest the sidecar receives is stable (review F7).
    """
    if ctx.tool_config is None:
        return _legacy_build_tools(ctx)
    from app.kernel import kernel_for
    from app.packs import LEGACY_ORDER
    from kernos.packs import compose_tools

    _inject(ctx)
    composed = compose_tools(kernel_for(ctx.db).packs, ctx.tool_config.get("packs", []), ctx)
    ordered = [n for n in LEGACY_ORDER if n in composed] + [n for n in composed if n not in LEGACY_ORDER]
    return {n: _from_pack(composed[n]) for n in ordered}


def tool_manifest(ctx: ToolContext | None = None) -> list[dict]:
    """`[{name, description, schema}]` for the sidecar's `run` command.

    Built from `build_tools` so the manifest can never drift from the tools that
    actually execute — the model must be told about exactly the schema the tool will
    validate against. With a ``ctx`` the per-turn tool selection applies; without one
    (the schema fixture, the probe) it is the full legacy set.
    """
    if ctx is None:
        from app.db import Database
        ctx = ToolContext(db=Database("sqlite:///:memory:"), room_id=0)
    return [
        {"name": name, "description": tool.description, "schema": tool.input_schema}
        for name, tool in build_tools(ctx).items()
    ]
