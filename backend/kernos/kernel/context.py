"""The objects a turn is made of.

``TurnContext`` is the one mutable record carried through every stage of the
pipeline (design §4.2). Plugins read and write its fields; the runner appends to
``trace``. Nothing here knows what a room, a meal or a member is — a host puts
its own objects in ``principal``, ``tool_ctx`` and ``extras``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class Stage(StrEnum):
    """The stages of a turn, in the order the runner executes them.

    ``resolve`` and ``gate`` are not run by the :class:`~kernos.kernel.pipeline.Pipeline`:
    the pipeline is *built from* the resolved profile, so resolution happens
    before it exists, and gating decides whether a turn exists at all. Both keep
    an entry here so a trace can record them under the same vocabulary.
    """

    gate = "gate"
    resolve = "resolve"
    context = "context"
    prompt = "prompt"
    model = "model"
    run = "run"
    validate_args = "validate_args"        # inside `run`, per tool call
    validate_result = "validate_result"    # inside `run`, per tool call
    render = "render"
    validate = "validate"
    persist = "persist"
    after = "after"


#: Stages the pipeline executes, in order. `validate_args`/`validate_result` are
#: consulted by the `run` stage's tool executor rather than run as stages.
PIPELINE_ORDER: tuple[Stage, ...] = (
    Stage.context, Stage.prompt, Stage.model, Stage.run,
    Stage.render, Stage.validate, Stage.persist, Stage.after,
)

#: Stages that must have exactly one plugin.
SINGLE_OWNER: frozenset[Stage] = frozenset({Stage.model, Stage.run, Stage.render})

Severity = Literal["warn", "block"]


@dataclass(frozen=True)
class Principal:
    """Whoever sent the message. ``id`` is host-opaque (a member id, a user id…)."""

    id: str | int | None
    name: str | None = None


@dataclass
class Draft:
    """A card the host will persist for a human to confirm — never a ledger write."""

    kind: str
    payload: dict


@dataclass
class Body:
    """A reply body. ``claimed_by_pack`` marks a body a pack built from structured
    tool results (a settlement, a statement…): reply validators skip those, because
    their numbers came from tools by construction."""

    text: str
    attachments: dict | None = None
    claimed_by_pack: bool = False


Outcome = Draft | Body


@dataclass
class Verdict:
    """What a validator returns. ``block`` replaces the outcome with ``replacement``
    and skips the remaining validators of that stage; ``warn`` is recorded only."""

    ok: bool
    severity: Severity = "warn"
    reason: str = ""
    replacement: Body | None = None

    @classmethod
    def passed(cls) -> "Verdict":
        return cls(ok=True)


@dataclass
class TurnContext:
    # identity of the turn
    space_id: str
    principal: Principal
    text: str
    turn_id: str | None = None
    images: list = field(default_factory=list)
    before_id: int | None = None
    depth: int = 0
    # what resolve produced
    profile: Any = None
    # what context plugins produce
    memory: str | None = None
    history: str | None = None
    knowledge: Any = None
    # what prompt/model plugins produce
    system: str | None = None
    message: str | None = None
    model: str | None = None
    vision_model: str | None = None
    thinking: str | None = None
    caps: dict = field(default_factory=dict)
    # host-owned objects, opaque to the kernel
    tool_ctx: Any = None
    sink: Any = None                     # EventSink for live events during `run`
    extras: dict = field(default_factory=dict)
    # what run/render/persist produce
    result: Any = None                   # kernos.engine.base.TurnResult
    outcome: Outcome | None = None
    persisted: Any = None
    superseded: list = field(default_factory=list)
    pending_events: list = field(default_factory=list)   # emitted by the caller, after its lock
    # bookkeeping
    trace: list[dict] = field(default_factory=list)
    stopped: bool = False

    def record(self, stage: Stage | str, plugin: str, version: str, ms: float,
               outcome: str, **extra: Any) -> None:
        entry = {"stage": str(stage), "plugin": plugin, "version": version,
                 "ms": round(ms, 3), "outcome": outcome}
        if extra:
            entry.update(extra)
        self.trace.append(entry)
