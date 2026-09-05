"""chiatienan's kernos composition root (plan Task 1.8).

One place wires the framework to this host: the plugin registry (framework
plugins plus this app's), the host adapters over a ``Database``, and the resolver.
Phase 1 resolves every room to the seeded default profile; Phase 2 swaps in the
database-backed resolver without touching ``chat.py``.

Kernels are cached per ``Database`` object because the adapters close over it —
production has one, the test suite has one per test.
"""
from __future__ import annotations

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
from kernos.content import ProfileSpec, Resolver, StaticResolver
from kernos.kernel import Pipeline
from kernos.plugins import (
    ImageLookback, MemoryLoad, ModelPassthrough, RecentHistory, Rollover, SectionsMessage,
)
from kernos.registry import Registry


class Kernel:
    def __init__(self, db: Database, resolver: Resolver | None = None) -> None:
        self.db = db
        self.adapters: HostAdapters = build_adapters(db)
        self.registry = Registry()
        self.registry.register_all([
            Rollover(self.adapters), MemoryLoad(self.adapters), RecentHistory(self.adapters),
            ImageLookback(self.adapters), SectionsMessage(), ModelPassthrough(),
            PhoenixSystemPrompt(), LegacyRunTurn(), LunchRender(),
            FabricatedCommit(), UnbackedAmounts(), Cards(self.adapters),
        ])
        self.resolver: Resolver = resolver or StaticResolver(build_default_spec(settings))
        self._pipelines: dict[int, Pipeline] = {}

    def resolve(self, room_id: int) -> ProfileSpec:
        return self.resolver.resolve(str(room_id))

    def pipeline_for(self, spec: ProfileSpec) -> Pipeline:
        # Cached by spec identity: the static resolver hands back one object, and
        # Phase 2's resolver caches published versions the same way.
        key = id(spec)
        if key not in self._pipelines:
            self._pipelines[key] = self.registry.build_pipeline(spec.pipeline_dict())
        return self._pipelines[key]


_kernels: "weakref.WeakKeyDictionary[Database, Kernel]" = weakref.WeakKeyDictionary()


def kernel_for(db: Database) -> Kernel:
    k = _kernels.get(db)
    if k is None:
        k = Kernel(db)
        _kernels[db] = k
    return k
