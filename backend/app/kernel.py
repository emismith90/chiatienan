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
from app.plugins.persist import Cards
from app.plugins.prompt import PhoenixSystemPrompt
from app.plugins.render import LunchRender
from app.plugins.run import LegacyRunTurn
from app.plugins.validate import FabricatedCommit, UnbackedAmounts
from kernos.adapters import HostAdapters
from kernos.content import (
    ContentStore, DbResolver, ProfileSpec, PublishGates, Resolver, Runtime, StaticResolver, ensure_seeded,
)
from kernos.kernel import Pipeline
from kernos.plugins import (
    ImageLookback, MemoryLoad, ModelPassthrough, RecentHistory, Rollover, SectionsMessage,
    TemplatePrompt,
)
from kernos.registry import Registry


class Kernel:
    def __init__(self, db: Database, resolver: Resolver | None = None) -> None:
        self.db = db
        self.adapters: HostAdapters = build_adapters(db)
        self.registry = Registry()
        self.registry.register_all([
            Rollover(self.adapters), MemoryLoad(self.adapters), RecentHistory(self.adapters),
            ImageLookback(self.adapters), SectionsMessage(), TemplatePrompt(self.adapters),
            ModelPassthrough(),
            PhoenixSystemPrompt(), LegacyRunTurn(), LunchRender(),
            FabricatedCommit(), UnbackedAmounts(), Cards(self.adapters),
        ])
        self.default_spec = build_default_spec(settings)
        self.store = ContentStore(db.session)
        self.seed_report = ensure_seeded(
            self.store, business_slug=BUSINESS_SLUG, business_name="Lunch ledger",
            spec=self.default_spec, agent_slug="phoenix", agent_name="Phoenix",
            sources=default_sources(), catalogue_rows=catalogue_rows(settings))
        self.gates = PublishGates(self.registry, self.store, clock=self.adapters.clock)
        self.resolver: Resolver = resolver or DbResolver(
            self.store, default_business_slug=BUSINESS_SLUG,
            runtime=self.default_spec.runtime, fallback=self.default_spec)
        self._pipelines: dict[str, Pipeline] = {}
        self.store.on_change.append(self.invalidate)

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
