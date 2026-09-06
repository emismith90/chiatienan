"""chiatienan's kernos composition root (plan Tasks 1.8, 2.4).

One place wires the framework to this host: the plugin registry (framework
plugins plus this app's), the host adapters over a ``Database``, the content
store, boot seeding of today's configuration, and the database-backed resolver.
``chat.py`` only ever calls ``kernel.resolve`` and ``kernel.pipeline_for``.

Kernels are cached per ``Database`` object because the adapters close over it —
production has one, the test suite has one per test.
"""
from __future__ import annotations

import logging
import weakref

from app.config import settings
from app.db import Database
from app.default_profile import build_default_spec
from app.hostadapters import build_adapters
from app.plugins.prompt import PhoenixSystemPrompt
from app.plugins.run import LegacyRunTurn
from app.plugins.validate import FabricatedCommit, UnbackedAmounts
from kernos.content import (  # noqa: F401
    ContentStore, DbResolver, ProfileSpec, Resolver, StaticResolver, ensure_seeded, ensure_sub_agent,
)
from kernos.data import DataStore
from kernos.host import BaseKernel

log = logging.getLogger("chiatienan")


class Kernel(BaseKernel):
    """chiatienan's kernel: the framework's :class:`BaseKernel` plus this host's packs,
    plugins, seeding, resolver and probe (Phase 9 review F2)."""

    def __init__(self, db: Database, resolver: Resolver | None = None, *, eval_mode: bool = False) -> None:
        self.db = db
        self.default_spec = build_default_spec(settings)
        store = ContentStore(db.session)
        super().__init__(store, DataStore(db.session, audit=store.log), build_adapters(db),
                         runtime=self.default_spec.runtime, eval_mode=eval_mode)
        from app.packs import host_packs
        self.register_packs(*host_packs())
        self.register_framework_packs()
        self.register_framework_plugins()
        self.registry.register_all([PhoenixSystemPrompt(), LegacyRunTurn(),
                                    FabricatedCommit(self.packs), UnbackedAmounts(self.packs)])
        self.seed_report = ensure_seeded(
            self.store, business_slug=BUSINESS_SLUG, business_name="Lunch ledger",
            spec=self.default_spec, agent_slug="phoenix", agent_name="Phoenix",
            sources=default_sources(), catalogue_rows=catalogue_rows(settings))
        from app.poker_profile import BUSINESS_SLUG as POKER_SLUG, build_poker_spec, poker_sources
        self.poker_report = ensure_seeded(
            self.store, business_slug=POKER_SLUG, business_name="Poker ledger",
            spec=build_poker_spec(settings), agent_slug="dealer", agent_name="Dealer", sources=poker_sources())
        from app.steward_profile import CAPABILITIES, DESCRIPTION, NAME, SLUG, build_steward_spec
        self.steward_report = ensure_sub_agent(
            self.store, self.seed_report["business_id"], slug=SLUG, name=NAME,
            spec=build_steward_spec(settings), description=DESCRIPTION,
            # what it may draft against: the lunch profile it reviews, never its own alone
            capabilities={**CAPABILITIES, "manages_profiles": [self.seed_report["profile_id"]]})
        self.default_business_id = self.seed_report["business_id"]
        self.build_gates()
        self.resolver = resolver or DbResolver(
            self.store, default_business_slug=BUSINESS_SLUG,
            runtime=self.default_spec.runtime, fallback=self.default_spec)
        from app.modelprobe import BenchModelProbe
        self.probe = BenchModelProbe()

    # ------------------------------------------------------------------ hooks

    def on_packs_registered(self, packs: list) -> None:
        """Hand the host's draft store and the ledger what the packs contribute: draft
        kinds (``app.drafts``) and debt edges (``ledger_core``)."""
        import ledger_core
        from app import drafts
        drafts.set_draft_kinds({k: dk for p in self.packs.list() for k, dk in p.draft_kinds().items()})
        ledger_core.configure(edge_sources=[p.contributions for p in self.packs.list()],
                              timeline_sources=[p.timeline for p in self.packs.list()])

    def null_tool_context(self):
        from app.tools import ToolContext
        return ToolContext(db=Database("sqlite:///:memory:"), room_id=0)

    def sub_tool_context(self, parent, *, sub: dict, depth: int, caps: dict):
        from app.tools import ToolContext
        return ToolContext(
            db=self.db, room_id=parent.room_id, sender_member_id=parent.sender_member_id,
            sender_name=parent.sender_name, turn_mentions=list(parent.turn_mentions),
            agent=sub, depth=depth, max_depth=parent.max_depth, caps_override=caps)

    def eval_runner_argv(self, suite_slug: str, version_id: int, run_id: int) -> list[str]:
        import sys
        return [sys.executable, "-m", "app.evalhost", "run", "--suite", suite_slug,
                "--version", str(version_id), "--run-id", str(run_id)]

    # --------------------------------------------------------------- host-only

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
