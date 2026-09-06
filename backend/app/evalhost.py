"""This host's side of eval (plan Task 4.3): the lunch suite as content, the world a
case runs in, the turn against a **candidate** spec, the judge, and the job entry
point.

    python -m app.evalhost run --suite lunch-typical --version 12 [--run-id 3]

A run is a job, not a request (review F3): it rebuilds a fresh database per case,
freezes the process clock to the case's day and drives the candidate pipeline
directly — never ``chat.run_bot_turn``, never the serving process's
``_agent_lock``. The admin route only creates the run row and spawns this module.

Corpus **import** is the one thing here that needs ``bench`` (the golden cases live
in ``tests/golden`` and their messages in ``bench.corpus``); it is imported lazily
and is dev-only — the documented layering exception next to ``app/modelprobe.py``.
Runs themselves need no ``bench``.
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import tempfile
import traceback

from app.config import settings
from app.db import Database
from app.evalworld import build_world, frozen_clock
from kernos.content import ProfileSpec, StaticResolver
from kernos.eval import EvalCase, Runner, spec_sha
from kernos.kernel import LegacyAgentEventSink, Principal, TurnContext

SUITE_SLUG = "lunch-typical"
RUBRIC_SLUG = "prose"

#: The lunch suite's graders: the pack's plugins, named as the bench records name them.
LUNCH_GRADERS = [
    {"plugin": "lunch_ledger.eval.tool_selection", "name": "tool_selection"},
    {"plugin": "lunch_ledger.eval.ledger_state", "name": "ledger_state"},
    {"plugin": "lunch_ledger.eval.prose", "name": "prose_quality"},
]


def import_lunch_suite(store, business_id: int, *, actor: str = "admin", corpus_name: str = "typical") -> dict:
    """``bench.corpus.load(corpus_name)`` → ``kn_eval_cases`` (ids preserved, images kept),
    the suite and its rubric. Idempotent: upserts by slug."""
    from bench import corpus  # dev-only; see the module docstring
    from packs.lunch_ledger.eval import PROSE_RUBRIC

    cases = corpus.load(corpus_name)
    for case in cases:
        store.put_case(business_id, case.id, case.to_eval_case(tags=[case.source]).to_dict(),
                       actor=actor, tags=[case.source], source="imported", review=False)
    store.put_rubric(business_id, RUBRIC_SLUG, PROSE_RUBRIC, actor=actor)
    suite = store.put_suite(business_id, SUITE_SLUG, actor=actor, case_slugs=[c.id for c in cases],
                            graders=LUNCH_GRADERS,
                            judge={"model": settings.bench_judge_model, "rubric": RUBRIC_SLUG}, repeat=1)
    return {"cases": len(cases), "suite": suite["slug"], "rubric": RUBRIC_SLUG,
            "sources": sorted({c.source for c in cases})}


def fixtures_for(spec: ProfileSpec, kernel=None) -> dict:
    """The union of the enabled packs' fixtures; a step two packs provide is refused
    (Phase 6 review F9), like a tool name two packs provide."""
    from app.kernel import kernel_for
    packs = (kernel or kernel_for(Database(settings.database_url))).packs
    out: dict = {}
    for pack, _ in packs.enabled(spec.tool_packs):
        for name, fn in pack.fixtures().items():
            if name in out:
                raise ValueError(f"fixture step {name!r} is provided by two enabled packs")
            out[name] = fn
    return out


class EvalWorld:
    """One case's world: its own SQLite file, the room the fixtures built, the key→id map."""

    def __init__(self, case, fixtures: dict | None = None) -> None:
        self._dir = tempfile.mkdtemp(prefix=f"eval-{case.id}-")
        self.db = Database(f"sqlite:///{self._dir}/eval.db")
        self.db.create_all()
        self.space_id, self.ids, self.drafts = build_world(self.db, case, fixtures)

    def close(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)


def world_factory(case) -> EvalWorld:
    return EvalWorld(case)


async def run_turn(case, world: EvalWorld, spec: ProfileSpec, *, agent: dict | None = None):
    """One turn of the candidate pipeline in the case's world, clock frozen to its day.

    The kernel is built over the world's database with a static resolver, so the
    pipeline is exactly the candidate's. A case's ``history`` (prod captures) is not
    replayed through the room — the pipeline reads the world's own messages; the
    committed corpora carry none. ``agent`` (the run's, when it has one) reaches the
    turn's ``extras`` and tool context so an agent-conditional pack shows its tools; the
    kernel runs in eval mode, so nothing in the turn can start a job (Phase 8 F5).
    """
    from app.kernel import Kernel
    from app.tools import ToolContext

    kernel = Kernel(world.db, resolver=StaticResolver(spec), eval_mode=True)
    sender = world.ids.get(case.actor)
    sender_name = next((m["display_name"] for m in case.members if m.get("key") == case.actor), None)
    ctx = TurnContext(
        space_id=str(world.space_id), principal=Principal(sender, sender_name), text=case.message,
        images=list(case.images or []), profile=spec,
        tool_ctx=ToolContext(db=world.db, room_id=world.space_id, sender_member_id=sender,
                             sender_name=sender_name, turn_mentions=[], agent=agent),
        sink=LegacyAgentEventSink(None),
        extras={"agent": agent} if agent is not None else {},
    )
    with frozen_clock(case.day):
        await kernel.pipeline_for(spec).run(ctx)
    return ctx.result


def import_pack_suite(store, business_id: int, packs: list, *, actor: str = "admin") -> dict:
    """A business whose packs ship their golden cases (``ToolPack.eval_cases``) gets them
    as content: the cases, the packs' graders as the suite, and a rubric when a pack
    ships one. Idempotent."""
    cases, graders, rubric = [], [], None
    for pack in packs:
        for raw in pack.eval_cases():
            case = EvalCase.from_dict(raw)
            store.put_case(business_id, case.id, case.to_dict(), actor=actor, tags=case.tags or [pack.id],
                           source="imported", review=case.review)
            cases.append(case.id)
        for plugin_id in pack.graders():
            graders.append({"plugin": plugin_id, "name": plugin_id.rsplit(".", 1)[-1]})
        rubric = rubric or (pack.content() or {}).get("rubric")
    if not cases:
        raise ValueError("none of the business's packs ships eval cases")
    slug = f"{packs[0].id}-golden"
    if rubric:
        store.put_rubric(business_id, RUBRIC_SLUG, rubric, actor=actor)
    suite = store.put_suite(business_id, slug, actor=actor, case_slugs=cases, graders=graders,
                            judge={"model": settings.bench_judge_model, "rubric": RUBRIC_SLUG if rubric else None}, repeat=1)
    return {"cases": len(cases), "suite": suite["slug"], "graders": [g["name"] for g in graders]}


def judge_for(suite: dict):
    """The suite's LLM judge, or ``None`` (prose then reports *not graded*, never blocks)."""
    model = (suite.get("judge") or {}).get("model")
    if not model:
        return None
    from bench.judge import openrouter_judge  # dev/job-only, like the importer
    return openrouter_judge(model)


def suite_cases(store, business_id: int, suite: dict) -> list[EvalCase]:
    by_slug = {c["slug"]: c for c in store.list_cases(business_id, full=True)}
    out = []
    for slug in suite["case_slugs"]:
        row = by_slug.get(slug)
        if row is None:
            raise KeyError(f"suite {suite['slug']!r} names a case that does not exist: {slug!r}")
        out.append(EvalCase.from_dict({**row["case"], "review": row["review"], "tags": row["tags"]}))
    return out


async def run_suite(kernel, suite_slug: str, version_id: int, *, actor: str = "eval",
                    run_id: int | None = None, run_turn=run_turn, world_factory=world_factory,
                    judge=None, agent_id: int | None = None) -> dict:
    """Run one suite against one profile version and store the run. ``run_turn`` /
    ``world_factory`` / ``judge`` are injectable for tests; production uses the ones above.
    The run's agent (``agent_id``, or the existing run row's) is closed over ``run_turn``."""
    import functools
    store = kernel.store
    version = store.get_version(version_id)
    business_id = store.get_profile(version["profile_id"])["business_id"]
    spec = ProfileSpec.model_validate(version["spec"])
    suite = store.get_suite(business_id, suite_slug)
    judge = judge if judge is not None else judge_for(suite)
    run = store.get_run(run_id) if run_id is not None else store.create_run(
        suite["id"], version_id, spec_sha(spec), actor=actor,
        judge_model=getattr(judge, "model", None) if judge else None, agent_id=agent_id)
    if run.get("agent_id") is not None:
        run_turn = functools.partial(run_turn, agent=store.get_agent(run["agent_id"]))
    try:
        graders = [kernel.graders.build(ref, judge=judge) for ref in suite["graders"]]
        cases = suite_cases(store, business_id, suite)
        if world_factory is globals()["world_factory"]:            # the default: the candidate's packs' fixtures
            fixtures = fixtures_for(spec, kernel)
            world_factory = lambda case: EvalWorld(case, fixtures)  # noqa: E731
        runner = Runner(graders, world_factory=world_factory, run_turn=run_turn,
                        repeat=suite.get("repeat") or 1, judge_model=getattr(judge, "model", None) if judge else None)
        out = await runner.run(cases, spec)
        return store.finish_run(run["id"], status="done", records=out["records"], summary=out["summary"])
    except Exception as exc:  # noqa: BLE001 — a failed run is a stored fact, not a crash
        return store.finish_run(run["id"], status="failed", error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="app.evalhost", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run one suite against one profile version (a job)")
    run.add_argument("--suite", required=True)
    run.add_argument("--version", type=int, required=True, help="profile version **id**")
    run.add_argument("--run-id", type=int, default=None, help="finish this existing run row")
    run.add_argument("--agent", type=int, default=None, help="agent id whose record every turn gets")
    imp = sub.add_parser("import", help="import the lunch corpus as the suite's content")
    imp.add_argument("--corpus", default="typical")
    args = parser.parse_args(argv)

    from app.kernel import kernel_for
    kernel = kernel_for(Database(settings.database_url))
    if args.cmd == "import":
        print(import_lunch_suite(kernel.store, kernel.seed_report["business_id"], corpus_name=args.corpus))
        return 0
    result = asyncio.run(run_suite(kernel, args.suite, args.version, run_id=args.run_id, agent_id=args.agent))
    print(f"run #{result['id']}: {result['status']}", result.get("summary", {}).get("graders"), file=sys.stderr)
    return 0 if result["status"] == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
