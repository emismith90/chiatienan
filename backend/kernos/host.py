"""``BaseKernel``: what every host's composition root has in common (design §12.2; Phase
9 review F2).

A host subclasses it, registers its own packs and plugins, seeds its content, sets
``resolver`` and ``default_business_id``, and implements four hooks: how a tool context is
built with no space (``null_tool_context``), how a sub-agent's tool context is derived
from its manager's (``sub_tool_context``), what command runs an eval job
(``eval_runner_argv``; ``None`` = this host cannot), and what to do when packs are
registered (``on_packs_registered``). Everything else — the framework plugins and packs,
pipeline caching, delegation, proposals, eval jobs, the gate helpers — lives here once.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from kernos.adapters import HostAdapters
from kernos.content import (
    ContentStore, Invalid, NotFound, ProfileSpec, PublishGates, Resolver, Runtime,
)
from kernos.content.traces import StoreTraces
from kernos.eval import GraderRegistry, eval_gate
from kernos.kernel import Body, Pipeline, Stage, TurnContext
from kernos.kernel.events import SUB_FINISHED, SUB_STARTED, TurnEvent
from kernos.packs import PackRegistry
from kernos.plugins import (
    Cards, EvalCapture, ImageLookback, MemoryLoad, ModelPassthrough, PackRender, RecentHistory,
    Rollover, SectionsMessage, TemplatePrompt, Trace, validators,
)
from kernos.registry import Registry

log = logging.getLogger("kernos.host")


class _SubSink:
    """The sub-agent's live events, seen through the manager's sink (Phase 7 review F3):
    its tool events are forwarded under the **manager's** ``turn_id`` with ``agent``
    added; its ``run.started/finished``, ``text.delta`` and ``run.error`` are dropped —
    ``sub.started/finished`` on the manager's turn replace them — so the room's
    timeline never shows a second turn or a sub's prose as the reply."""

    _FORWARD = frozenset({"agent.tool.start", "agent.tool.result"})

    def __init__(self, parent, turn_id: str | None, agent: str) -> None:
        self._parent, self._turn_id, self._agent = parent, turn_id, agent

    async def emit(self, event) -> None:
        if self._parent is None:
            return
        event.turn_id = self._turn_id
        event.data = {**event.data, "agent": self._agent}
        await self._parent.emit(event)

    async def emit_raw(self, payload: dict) -> None:
        if self._parent is None or payload.get("type") not in self._FORWARD:
            return
        await self._parent.emit_raw({**payload, "turn_id": self._turn_id, "agent": self._agent})


class BaseKernel:
    def __init__(self, store: ContentStore, data: Any, adapters: HostAdapters, *, runtime: Runtime,
                 resolver: Resolver | None = None, eval_mode: bool = False,
                 admin_url: str = "/api/admin/proposals/{id}") -> None:
        self.store, self.data, self.adapters, self.runtime = store, data, adapters, runtime
        self.resolver: Resolver | None = resolver
        #: True inside an eval host: agent-conditional packs expose read tools only and
        #: nothing may start a job (Phase 8 review F5).
        self.eval_mode = eval_mode
        self.admin_url = admin_url
        self.packs = PackRegistry()
        self.graders = GraderRegistry()
        self.registry = Registry()
        self.gates: PublishGates | None = None
        #: The business an unbound space belongs to; the host sets it after seeding.
        self.default_business_id: int | None = None
        self._pipelines: dict[str, Pipeline] = {}
        self.store.on_change.append(self.invalidate)

    # ------------------------------------------------------------------ hooks

    def null_tool_context(self) -> Any:
        """A tool context with no space, for building a pack's static tool names."""
        raise NotImplementedError

    def sub_tool_context(self, parent: Any, *, sub: dict, depth: int, caps: dict) -> Any:
        """A sub-agent's tool context derived from its manager's (same space and sender)."""
        raise NotImplementedError

    def eval_runner_argv(self, suite_slug: str, version_id: int, run_id: int) -> list[str] | None:
        """The command that fills an eval run, or ``None`` when this host runs no jobs."""
        return None

    def on_packs_registered(self, packs: list) -> None:
        """Called after every ``register_packs`` with the packs just added."""
        return None

    # -------------------------------------------------------------- composition

    def register_packs(self, *packs) -> None:
        self.packs.register_all(packs)
        for pack in packs:
            self.graders.register_all(pack.graders())
        self.on_packs_registered(list(packs))

    def register_framework_packs(self) -> None:
        """Collections, delegation and the CMS pack — every host gets them."""
        from kernos.agents import DelegationPack
        from kernos.data import CollectionsPack
        from kernos.osadmin import OsAdminPack
        self.register_packs(
            CollectionsPack(self.data, self.business_for),
            DelegationPack(self.subs_of, self.run_sub),
            OsAdminPack(self.store, gates=lambda: self.gates, describe=self.agent_space,
                        start_run=lambda *a, **kw: self.start_eval_run(*a, **kw), traces=StoreTraces(self.store),
                        eval_mode=self.eval_mode, admin_url=self.admin_url,
                        # a proposal becomes a card the room can confirm; the gates still
                        # decide, and a refusal leaves it pending (plan Phase 10.3)
                        approve=self.approve_proposal),
        )

    def register_framework_plugins(self) -> None:
        self.registry.register_all([
            Rollover(self.adapters), MemoryLoad(self.adapters), RecentHistory(self.adapters),
            ImageLookback(self.adapters), SectionsMessage(), TemplatePrompt(self.adapters),
            ModelPassthrough(), PackRender(self.packs), Cards(self.adapters, self.packs),
            Trace(StoreTraces(self.store)), EvalCapture(self.capture_case, self.packs, self.adapters),
            *validators(),
        ])

    def register_engine(self, engine) -> None:
        """The framework run stage over an engine (`kernos.run.engine`, Phase 9 F1)."""
        from kernos.plugins.run import EngineRun
        self.registry.register(EngineRun(engine, self.packs))

    def build_gates(self, **kw) -> PublishGates:
        self.gates = PublishGates(
            self.registry, self.store, clock=self.adapters.clock,
            eval_gate=lambda spec, *, profile_id, version_id: eval_gate(
                self.store, spec, profile_id=profile_id, version_id=version_id),
            packs=self.packs, tool_names_of=self.static_tool_names, **kw)
        return self.gates

    # --------------------------------------------------------------- tool names

    def static_tool_names(self, pack) -> set[str] | None:
        """A pack's tool names for gate 1, or ``None`` when they depend on the space
        (`collections`)."""
        if pack.id == "collections":
            return None
        if getattr(pack, "all_tool_names", None) is not None:      # agent-conditional tools (Phase 8 F8)
            return set(pack.all_tool_names)
        try:
            return set(pack.tools(self.null_tool_context()))
        except Exception:  # noqa: BLE001
            return None

    def reserved_tool_names(self) -> set[str]:
        """Every registered pack's tool names — a collection may not generate one of them
        (Phase 5 review F6)."""
        names: set[str] = set()
        ctx = None
        for pack in self.packs.list():
            if pack.id == "collections":
                continue
            if getattr(pack, "all_tool_names", None) is not None:
                names |= set(pack.all_tool_names)
                continue
            try:
                ctx = ctx if ctx is not None else self.null_tool_context()
                names |= set(pack.tools(ctx))
            except Exception:  # noqa: BLE001 — a pack that needs a real space contributes nothing here
                continue
        return names

    # ------------------------------------------------------------------ agents

    def agent_space(self, space_id: int | str) -> dict | None:
        """What a space resolves to (`agent`, `profile_id`, `version_id`), or ``None`` for a
        static resolver."""
        if not hasattr(self.resolver, "describe"):
            return None
        return self.resolver.describe(str(space_id))

    def agent_for(self, space_id: int | str) -> dict | None:
        return (self.agent_space(space_id) or {}).get("agent")

    def business_for(self, space_id: int | str) -> int:
        """The business a space belongs to: its bound agent's, else the default's."""
        agent = self.agent_for(space_id)
        if agent is not None:
            return agent["business_id"]
        if self.default_business_id is None:
            raise Invalid("no default business: the host must set default_business_id")
        return self.default_business_id

    def subs_of(self, agent: dict) -> list[dict]:
        """The sub-agents an agent's ``delegates_to`` names; an entry that is not a
        ``sub`` of the same business is logged and skipped."""
        out = []
        for entry in agent.get("delegates_to") or []:
            try:
                sub = self.store.get_agent(int(entry))
            except (NotFound, TypeError, ValueError):
                log.warning("agent %s delegates to %r, which is no agent", agent.get("slug"), entry)
                continue
            if sub["role"] != "sub" or sub["business_id"] != agent["business_id"]:
                log.warning("agent %s delegates to %s, a %s of business %s; skipped",
                            agent.get("slug"), sub["slug"], sub["role"], sub["business_id"])
                continue
            out.append(sub)
        return out

    async def run_sub(self, tool_ctx, sub: dict, task: str, *, budget: dict) -> dict:
        """A sub-agent's turn nested inside the manager's tool call (design §6, plan
        Task 7.1): the sub's published profile runs its pipeline ``context → validate``
        — it posts nothing and is traced as a span of the manager — in the same space,
        for the same principal, with ``text=task`` and caps clamped to the manager's
        remaining budget. Returns ``{text, results, capped, invocations, error}``."""
        parent: TurnContext = tool_ctx.turn
        slug = sub["slug"]
        stored = self.store.published_spec(sub["profile_id"])
        if stored is None:
            return {"text": "", "results": [], "capped": False, "invocations": [],
                    "error": f"sub-agent {slug} has no published profile"}
        spec = ProfileSpec.model_validate(stored).with_runtime(self.runtime)
        caps = {"max_seconds": min(spec.caps.max_seconds, budget["max_seconds"]),
                "max_tools": min(spec.caps.max_tools, budget["max_tools"])}
        depth = (parent.depth if parent is not None else tool_ctx.depth) + 1
        sub_tool_ctx = self.sub_tool_context(tool_ctx, sub=sub, depth=depth, caps=caps)
        manager_turn_id = tool_ctx.turn_id
        parent_sink = parent.sink if parent is not None else None
        space_id = str(parent.space_id if parent is not None else getattr(tool_ctx, "space_id", ""))
        ctx = TurnContext(
            space_id=space_id,
            principal=parent.principal if parent is not None else None,
            text=task,
            images=list(parent.images) if parent is not None else [],
            before_id=parent.before_id if parent is not None else None,
            depth=depth, profile=spec, tool_ctx=sub_tool_ctx,
            sink=_SubSink(parent_sink, manager_turn_id, slug),
            extras={"agent": sub, "max_depth": tool_ctx.max_depth},
        )
        if parent_sink is not None:
            await parent_sink.emit(TurnEvent(SUB_STARTED, manager_turn_id, {"agent": slug, "task": task}))
        started = time.perf_counter()
        error = None
        try:
            await self.pipeline_for(spec).run(ctx, through=Stage.validate)
        except Exception as exc:  # noqa: BLE001 — a failed sub is a tool error, never a dead manager turn
            log.exception("[agents] sub %s failed", slug)
            error = f"{type(exc).__name__}: {exc}"
        result = ctx.result
        invocations = list(getattr(result, "tools", None) or [])
        for inv in invocations:
            if inv.from_agent is None:
                inv.from_agent = slug
        if parent is not None:
            # a deeper sub's rows keep their own span (they joined this ctx the same way)
            parent.trace.extend({**row, "span": row.get("span", slug), "depth": row.get("depth", depth)} for row in ctx.trace)
        if error is None and result is not None and result.error:
            error = result.error
        # a blocked sub hands the manager the replacement body, never the forged prose (F5)
        text = ctx.outcome.text if isinstance(ctx.outcome, Body) else (result.final_text if result is not None else "")
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        if parent_sink is not None:
            await parent_sink.emit(TurnEvent(SUB_FINISHED, manager_turn_id, {
                "agent": slug, "elapsed_ms": elapsed_ms, "tools": [inv.name for inv in invocations], "error": error}))
        return {"text": text, "results": [{"name": inv.name, "result": inv.result} for inv in invocations],
                "capped": bool(getattr(result, "capped", False)), "invocations": invocations, "error": error}

    # -------------------------------------------------------------------- eval

    def capture_case(self, space_id, case: dict, keep_days: int) -> None:
        """`kernos.after.eval_capture`'s sink: a `review: true` case in the space's
        business, with retention for unreviewed captures."""
        from datetime import datetime, timedelta, timezone
        bid = self.business_for(space_id)
        self.store.put_case(bid, case["id"], case, actor="kernos:eval_capture", tags=case.get("tags") or ["captured"],
                            source="captured", review=True)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat(timespec="seconds")
        self.store.prune_cases(bid, source="captured", review=True, older_than=cutoff)

    def start_eval_run(self, suite_slug: str, version_id: int, *, actor: str, agent_id: int | None = None) -> dict:
        """Create the run row and spawn the host's eval job to fill it — a job, never a
        request the serving process waits on (Phase 4 review F3). With an ``agent_id``
        the run carries the agent (Phase 7 F12). Refused in eval mode (F5)."""
        from kernos.eval import spec_sha
        if self.eval_mode:
            raise Invalid("an eval run cannot start a job")
        if self.eval_runner_argv(suite_slug, version_id, 0) is None:
            raise Invalid("this host cannot run evals")
        version = self.store.get_version(version_id)
        business_id = self.store.get_profile(version["profile_id"])["business_id"]
        suite = self.store.get_suite(business_id, suite_slug)
        run = self.store.create_run(suite["id"], version_id, spec_sha(ProfileSpec.model_validate(version["spec"])),
                                    actor=actor, judge_model=(suite.get("judge") or {}).get("model"), agent_id=agent_id)
        self.spawn(self.eval_runner_argv(suite_slug, version_id, run["id"]))
        return run

    @staticmethod
    def spawn(argv: list[str]) -> None:
        import subprocess
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True)

    # ----------------------------------------------------------------- proposals

    def approve_proposal(self, proposal_id: int, *, actor: str, override_reason: str | None = None) -> dict:
        """Publish a proposed version through the gates, then write the source changes it
        carries so future drafts keep them (design §8.4; Phase 8 review F2/F10/F11). The
        approver is never an agent. A gate failure or a source edited since the draft
        leaves the proposal ``pending`` with ``last_error`` and re-raises. Gate 2's
        ``override_reason`` defaults to the approval itself."""
        from kernos.content import Conflict, GateError, PreconditionFailed
        if actor.startswith("agent:"):
            raise Invalid("a proposal is decided by a non-agent")
        prop = self.store.get_proposal(proposal_id)
        if prop["status"] != "pending":
            raise Conflict(f"proposal #{proposal_id} is {prop['status']}")
        try:
            for change in prop["source_changes"] or []:
                current = self._source_etag(prop["business_id"], change["kind"], change["slug"])
                if current != change.get("if_match"):
                    raise PreconditionFailed(
                        f"source {change['kind']}/{change['slug']} changed since the proposal was drafted")
            self.store.publish(prop["version_id"], actor=actor, gates=self.gates, note=f"proposal #{proposal_id}",
                               override_reason=override_reason or f"proposal #{proposal_id} approved by {actor}: {prop['rationale']}")
        except (GateError, PreconditionFailed, Conflict) as exc:
            self.store.decide_proposal(proposal_id, status="pending", by=None, actor=actor, error=str(exc))
            raise
        agent = self.store.get_agent(prop["agent_id"])
        self.store.apply_source_changes(prop["business_id"], prop["source_changes"], actor=f"agent:{agent['slug']}",
                                        audit={"proposal": prop["id"], "approved_by": actor})
        return self.store.decide_proposal(proposal_id, status="approved", by=actor, actor=actor)

    def reject_proposal(self, proposal_id: int, *, actor: str) -> dict:
        from kernos.content import Conflict
        prop = self.store.get_proposal(proposal_id)
        if prop["status"] != "pending":
            raise Conflict(f"proposal #{proposal_id} is {prop['status']}")
        if self.store.get_version(prop["version_id"])["status"] == "draft":
            self.store.retire(prop["version_id"], actor=actor)
        return self.store.decide_proposal(proposal_id, status="rejected", by=actor, actor=actor)

    def _source_etag(self, business_id: int, kind: str, slug: str) -> str | None:
        try:
            return self.store.get_source(business_id, kind, slug)["etag"]
        except NotFound:
            return None

    # ---------------------------------------------------------------- pipelines

    def resolve(self, space_id: int | str) -> ProfileSpec:
        return self.resolver.resolve(str(space_id))

    def pipeline_for(self, spec: ProfileSpec) -> Pipeline:
        # Keyed by the pipeline's content, not the spec object's identity (review
        # finding 5): two versions with the same pipeline share one, and a publish
        # that changes the pipeline can never be served a stale one.
        key = hashlib.sha256(json.dumps(spec.pipeline_dict(), sort_keys=True).encode()).hexdigest()
        if key not in self._pipelines:
            self._pipelines[key] = self.registry.build_pipeline(spec.pipeline_dict())
        return self._pipelines[key]

    def invalidate(self) -> None:
        self._pipelines.clear()
        invalidate = getattr(self.resolver, "invalidate", None)
        if invalidate:
            invalidate()
