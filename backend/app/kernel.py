"""chiatienan's kernos composition root (plan Tasks 1.8, 2.4).

One place wires the framework to this host: the plugin registry (framework
plugins plus this app's), the host adapters over a ``Database``, the content
store, boot seeding of today's configuration, and the database-backed resolver.
``chat.py`` only ever calls ``kernel.resolve`` and ``kernel.pipeline_for``.

Kernels are cached per ``Database`` object because the adapters close over it —
production has one, the test suite has one per test.
"""
from __future__ import annotations

import hashlib
import json
import logging
import weakref

from app.config import settings
from app.db import Database
from app.default_profile import build_default_spec
from app.hostadapters import build_adapters
from app.plugins.prompt import PhoenixSystemPrompt
from app.plugins.run import LegacyRunTurn
from app.plugins.validate import FabricatedCommit, UnbackedAmounts
from kernos.adapters import HostAdapters
from kernos.content.traces import StoreTraces
from kernos.data import CollectionsPack, DataStore
from kernos.eval import GraderRegistry, eval_gate
from kernos.content import (
    ContentStore, DbResolver, Invalid, NotFound, ProfileSpec, PublishGates, Resolver, Runtime, StaticResolver, ensure_seeded,
)
from kernos.kernel import Pipeline
from kernos.packs import PackRegistry
from kernos.plugins import (
    Cards as KernelCards, ImageLookback, MemoryLoad, ModelPassthrough, PackRender, RecentHistory,
    Rollover, SectionsMessage, TemplatePrompt, Trace, EvalCapture, validators,
)
from kernos.registry import Registry

log = logging.getLogger("chiatienan")


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


class Kernel:
    def __init__(self, db: Database, resolver: Resolver | None = None, *, eval_mode: bool = False) -> None:
        self.db = db
        #: True inside the eval host: agent-conditional packs expose read tools only and
        #: nothing may start a job (Phase 8 review F5).
        self.eval_mode = eval_mode
        self.adapters: HostAdapters = build_adapters(db)
        from app.packs import host_packs
        self.packs = PackRegistry()
        self.graders = GraderRegistry()
        self.register_packs(*host_packs())
        self.store = ContentStore(db.session)
        self.data = DataStore(db.session, audit=self.store.log)
        self.register_packs(CollectionsPack(self.data, self.business_for))
        from kernos.agents import DelegationPack
        self.register_packs(DelegationPack(self.subs_of, self.run_sub))
        self.registry = Registry()
        self.registry.register_all([
            Rollover(self.adapters), MemoryLoad(self.adapters), RecentHistory(self.adapters),
            ImageLookback(self.adapters), SectionsMessage(), TemplatePrompt(self.adapters),
            ModelPassthrough(),
            PhoenixSystemPrompt(), LegacyRunTurn(), PackRender(self.packs),
            KernelCards(self.adapters, self.packs),
            FabricatedCommit(self.packs), UnbackedAmounts(),
            Trace(StoreTraces(self.store)),
            EvalCapture(self.capture_case, self.packs, self.adapters),
            *validators(),
        ])
        self.default_spec = build_default_spec(settings)
        self.seed_report = ensure_seeded(
            self.store, business_slug=BUSINESS_SLUG, business_name="Lunch ledger",
            spec=self.default_spec, agent_slug="phoenix", agent_name="Phoenix",
            sources=default_sources(), catalogue_rows=catalogue_rows(settings))
        from app.poker_profile import BUSINESS_SLUG as POKER_SLUG, build_poker_spec, poker_sources
        self.poker_report = ensure_seeded(
            self.store, business_slug=POKER_SLUG, business_name="Poker ledger",
            spec=build_poker_spec(settings), agent_slug="dealer", agent_name="Dealer", sources=poker_sources())
        self.gates = PublishGates(
            self.registry, self.store, clock=self.adapters.clock,
            eval_gate=lambda spec, *, profile_id, version_id: eval_gate(
                self.store, spec, profile_id=profile_id, version_id=version_id),
            packs=self.packs, tool_names_of=self.static_tool_names)
        self.resolver: Resolver = resolver or DbResolver(
            self.store, default_business_slug=BUSINESS_SLUG,
            runtime=self.default_spec.runtime, fallback=self.default_spec)
        self._pipelines: dict[str, Pipeline] = {}
        self.store.on_change.append(self.invalidate)
        from app.modelprobe import BenchModelProbe
        self.probe = BenchModelProbe()

    def register_packs(self, *packs) -> None:
        """Register packs and hand the host's draft store and the ledger what they
        contribute: draft kinds (``app.drafts``) and debt edges (``ledger_core``)."""
        import ledger_core
        from app import drafts

        self.packs.register_all(packs)
        for pack in packs:
            self.graders.register_all(pack.graders())
        drafts.set_draft_kinds({k: dk for p in self.packs.list() for k, dk in p.draft_kinds().items()})
        ledger_core.configure(edge_sources=[p.contributions for p in self.packs.list()],
                              timeline_sources=[p.timeline for p in self.packs.list()])

    def static_tool_names(self, pack) -> set[str] | None:
        """A pack's tool names for gate 1, or ``None`` when they depend on the space
        (`collections`)."""
        if pack.id == "collections":
            return None
        from app.tools import ToolContext
        try:
            return set(pack.tools(ToolContext(db=Database("sqlite:///:memory:"), room_id=0)))
        except Exception:  # noqa: BLE001
            return None

    def reserved_tool_names(self) -> set[str]:
        """Every registered pack's tool names (built with the null context, as the probe
        does) — a collection may not generate one of them (Phase 5 review F6)."""
        from app.tools import ToolContext
        ctx = ToolContext(db=Database("sqlite:///:memory:"), room_id=0)
        names: set[str] = set()
        for pack in self.packs.list():
            if pack.id == "collections":
                continue
            try:
                names |= set(pack.tools(ctx))
            except Exception:  # noqa: BLE001 — a pack that needs a real space contributes nothing here
                continue
        return names

    def agent_for(self, space_id: int | str) -> dict | None:
        """The agent record a space runs (its binding's, else the default's), or ``None``
        when the resolver has no notion of agents (a static spec)."""
        if not hasattr(self.resolver, "describe"):
            return None
        return (self.resolver.describe(str(space_id)) or {}).get("agent")

    def subs_of(self, agent: dict) -> list[dict]:
        """The sub-agents an agent's ``delegates_to`` names; an entry that is not a
        ``sub`` of the same business is logged and skipped (the store refuses to save
        one, so this is belt and braces)."""
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
        remaining budget. Never takes ``chat._agent_lock`` (the manager's turn holds it).
        Returns ``{text, results, capped, invocations, error}``."""
        import time as _time
        from app.tools import ToolContext
        from kernos.kernel import Body, Stage, TurnContext
        from kernos.kernel.events import SUB_FINISHED, SUB_STARTED, TurnEvent

        parent: TurnContext = tool_ctx.turn
        slug = sub["slug"]
        stored = self.store.published_spec(sub["profile_id"])
        if stored is None:
            return {"text": "", "results": [], "capped": False, "invocations": [],
                    "error": f"sub-agent {slug} has no published profile"}
        spec = ProfileSpec.model_validate(stored).with_runtime(self.default_spec.runtime)
        caps = {"max_seconds": min(spec.caps.max_seconds, budget["max_seconds"]),
                "max_tools": min(spec.caps.max_tools, budget["max_tools"])}
        depth = (parent.depth if parent is not None else tool_ctx.depth) + 1
        sub_tool_ctx = ToolContext(
            db=self.db, room_id=tool_ctx.room_id, sender_member_id=tool_ctx.sender_member_id,
            sender_name=tool_ctx.sender_name, turn_mentions=list(tool_ctx.turn_mentions),
            agent=sub, depth=depth, max_depth=tool_ctx.max_depth, caps_override=caps)
        manager_turn_id = tool_ctx.turn_id
        parent_sink = parent.sink if parent is not None else None
        ctx = TurnContext(
            space_id=str(tool_ctx.room_id),
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
        started = _time.perf_counter()
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
        elapsed_ms = round((_time.perf_counter() - started) * 1000, 1)
        if parent_sink is not None:
            await parent_sink.emit(TurnEvent(SUB_FINISHED, manager_turn_id, {
                "agent": slug, "elapsed_ms": elapsed_ms, "tools": [inv.name for inv in invocations], "error": error}))
        return {"text": text, "results": [{"name": inv.name, "result": inv.result} for inv in invocations],
                "capped": bool(getattr(result, "capped", False)), "invocations": invocations, "error": error}

    def business_for(self, space_id: int | str) -> int:
        """The business a space belongs to: its bound agent's, else the default's."""
        info = self.resolver.describe(str(space_id)) if hasattr(self.resolver, "describe") else {}
        agent = info.get("agent") if info else None
        if agent is not None:
            return agent["business_id"]
        return self.seed_report["business_id"]

    def capture_case(self, space_id, case: dict, keep_days: int) -> None:
        """`kernos.after.eval_capture`'s sink: a `review: true` case in the space's
        business, with retention for unreviewed captures."""
        from datetime import datetime, timedelta, timezone
        bid = self.business_for(space_id)
        self.store.put_case(bid, case["id"], case, actor="kernos:eval_capture", tags=case.get("tags") or ["captured"],
                            source="captured", review=True)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat(timespec="seconds")
        self.store.prune_cases(bid, source="captured", review=True, older_than=cutoff)

    def import_eval_suite(self, business_id: int, *, actor: str) -> dict:
        """The lunch business imports the benchmark corpus; any other business imports
        the golden cases its packs ship (`ToolPack.eval_cases`)."""
        from app.evalhost import import_lunch_suite, import_pack_suite
        business = self.store.get_business(business_id)
        if business["slug"] == BUSINESS_SLUG:
            return import_lunch_suite(self.store, business_id, actor=actor)
        spec = ProfileSpec.model_validate(self.store.published_spec(self.store.default_agent(business_id)["profile_id"]))
        packs = [pack for pack, _ in self.packs.enabled(spec.tool_packs) if pack.eval_cases()]
        return import_pack_suite(self.store, business_id, packs, actor=actor)

    def start_eval_run(self, suite_slug: str, version_id: int, *, actor: str, agent_id: int | None = None) -> dict:
        """Create the run row and spawn `python -m app.evalhost run …` to fill it — a
        job, never a request the serving process waits on (Phase 4 review F3). With an
        ``agent_id`` the run carries the agent and the eval host hands its record to every
        turn's context (Phase 7 F12). Refused in eval mode (F5)."""
        import sys
        from kernos.eval import spec_sha
        if self.eval_mode:
            raise Invalid("an eval run cannot start a job")
        version = self.store.get_version(version_id)
        business_id = self.store.get_profile(version["profile_id"])["business_id"]
        suite = self.store.get_suite(business_id, suite_slug)
        run = self.store.create_run(suite["id"], version_id, spec_sha(ProfileSpec.model_validate(version["spec"])),
                                    actor=actor, judge_model=(suite.get("judge") or {}).get("model"), agent_id=agent_id)
        self.spawn([sys.executable, "-m", "app.evalhost", "run", "--suite", suite_slug,
                    "--version", str(version_id), "--run-id", str(run["id"])])
        return run

    # ----------------------------------------------------------------- proposals

    def approve_proposal(self, proposal_id: int, *, actor: str, override_reason: str | None = None) -> dict:
        """Publish a proposed version through the gates, then write the source changes it
        carries so future drafts keep them (design §8.4; Phase 8 review F2/F10/F11). The
        approver is never an agent. A gate failure or a source edited since the draft
        leaves the proposal ``pending`` with ``last_error`` and re-raises. Gate 2's
        ``override_reason`` (a money profile that keeps a risky builtin tool) defaults to
        the approval itself — the proposal's rationale, attributed to the approver."""
        from kernos.content import Conflict, GateError, PreconditionFailed
        if actor.startswith("agent:"):
            raise Invalid("a proposal is decided by a non-agent")
        prop = self.store.get_proposal(proposal_id)
        if prop["status"] != "pending":
            raise Conflict(f"proposal #{proposal_id} is {prop['status']}")
        try:
            # every source must still be as it was when drafted, before anything is published
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
        self._apply_source_changes(prop, actor)
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

    def _apply_source_changes(self, prop: dict, actor: str) -> None:
        agent = self.store.get_agent(prop["agent_id"])
        for change in prop["source_changes"] or []:
            fm = dict(change.get("frontmatter") or {})
            fm["audit"] = {"proposal": prop["id"], "approved_by": actor}
            self.store.put_source(prop["business_id"], change["kind"], change["slug"], body=change["body"],
                                  title=change.get("title", ""), frontmatter=fm, actor=f"agent:{agent['slug']}",
                                  if_match=change.get("if_match"))

    @staticmethod
    def spawn(argv: list[str]) -> None:
        import subprocess
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True)

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


BUSINESS_SLUG = "lunch"

#: Probe results the Pi port recorded (plan 2026-08-12, Task 0) for the two models
#: the env ships with. Seeded only for those ids; any other configured model starts
#: with no probe and gate 3 asks for one before it can be published as a change.
_RECORDED_PROBES = {
    "~deepseek/deepseek-v4-flash-latest": {
        "provider": "openrouter", "name": "DeepSeek V4 Flash Latest", "input": ["text"],
        "context_window": 1_048_576, "reasoning": False,
        "probe": {"ok": True, "checked_at": "2026-08-12T00:00:00+00:00", "schemas": ["propose_meal", "update_member", "settle_period"],
                  "source": "bench.probe_models — cursor-to-pi plan Task 0 (3/3)"},
    },
    "qwen/qwen3-vl-30b-a3b-instruct": {
        "provider": "openrouter", "name": "Qwen3 VL 30B A3B Instruct", "input": ["text", "image"],
        "context_window": 262_144, "reasoning": False,
        "probe": {"ok": True, "checked_at": "2026-08-12T00:00:00+00:00", "schemas": ["propose_meal", "update_member", "settle_period", "bill image"],
                  "source": "bench.probe_models — cursor-to-pi plan Task 0 (4/4)"},
    },
}


def catalogue_rows(settings) -> list[dict]:
    rows = []
    for model_id in {settings.pi_model, settings.pi_vision_model}:
        if not model_id:
            continue
        recorded = _RECORDED_PROBES.get(model_id, {"provider": settings.pi_provider or "openrouter"})
        rows.append({"model_id": model_id, **recorded})
    return rows


def default_sources() -> list[dict]:
    """The skill and rule files as sources, so the seeded business snapshots to itself."""
    from app.agent import _read_context_files, _read_skills
    from app.default_profile import _MONEY_RULES

    out = [{"kind": "skill", "slug": k["name"], "title": k["name"], "body": k["body"],
            "frontmatter": {"description": k["description"], "delivery": "inline"}} for k in _read_skills()]
    out += [{"kind": "rule", "slug": f["path"], "title": f["path"], "body": f["content"],
             "frontmatter": {"tags": ["money"] if f["path"] in _MONEY_RULES else []}}
            for f in _read_context_files()]
    return out


_kernels: "weakref.WeakKeyDictionary[Database, Kernel]" = weakref.WeakKeyDictionary()


def kernel_for(db: Database) -> Kernel:
    k = _kernels.get(db)
    if k is None:
        k = Kernel(db)
        _kernels[db] = k
    return k
