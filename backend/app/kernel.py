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
    ContentStore, DbResolver, ProfileSpec, PublishGates, Resolver, Runtime, StaticResolver, ensure_seeded,
)
from kernos.kernel import Pipeline
from kernos.packs import PackRegistry
from kernos.plugins import (
    Cards as KernelCards, ImageLookback, MemoryLoad, ModelPassthrough, PackRender, RecentHistory,
    Rollover, SectionsMessage, TemplatePrompt, Trace, EvalCapture, validators,
)
from kernos.registry import Registry


class Kernel:
    def __init__(self, db: Database, resolver: Resolver | None = None) -> None:
        self.db = db
        self.adapters: HostAdapters = build_adapters(db)
        from app.packs import host_packs
        self.packs = PackRegistry()
        self.graders = GraderRegistry()
        self.register_packs(*host_packs())
        self.store = ContentStore(db.session)
        self.data = DataStore(db.session, audit=self.store.log)
        self.register_packs(CollectionsPack(self.data, self.business_for))
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

    def start_eval_run(self, suite_slug: str, version_id: int, *, actor: str) -> dict:
        """Create the run row and spawn `python -m app.evalhost run …` to fill it — a
        job, never a request the serving process waits on (Phase 4 review F3)."""
        import subprocess
        import sys
        from kernos.eval import spec_sha
        version = self.store.get_version(version_id)
        business_id = self.store.get_profile(version["profile_id"])["business_id"]
        suite = self.store.get_suite(business_id, suite_slug)
        run = self.store.create_run(suite["id"], version_id, spec_sha(ProfileSpec.model_validate(version["spec"])),
                                    actor=actor, judge_model=(suite.get("judge") or {}).get("model"))
        self.spawn([sys.executable, "-m", "app.evalhost", "run", "--suite", suite_slug,
                    "--version", str(version_id), "--run-id", str(run["id"])])
        return run

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
