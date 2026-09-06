# Handoff — the remaining work on `claude/headless-cms-pi-harness-nn18pb`

Written 2026-09-06 for whoever continues (a different model may pick this up). Everything
below is self-contained: what is done, the rules that governed the work, and a step-by-step
plan for what remains — Phase 9 (Portability) and the closing notes.

## 0. State of the branch

- Branch: `claude/headless-cms-pi-harness-nn18pb` (develop and push **only** here; no PR
  unless the user asks). Every commit so far is pushed.
- Phases 1–8 of `docs/superpowers/plans/2026-09-05-agent-os-framework.md` are implemented,
  reviewed (a second-model "review gate" per phase, findings recorded in the plan) and
  documented (design spec "as built" sections, README, plan "state of play").
- Last verified run: `1251 passed, 1 skipped` (backend), sidecar `70/70`, golden `9/9`,
  layering green, no pre-existing test edited.
- Phase 9's plan section is expanded (facts, decisions 1–6, Tasks 9.1–9.4) and its review
  gate was **launched but its findings may not be recorded yet**. Check the plan: if the
  Phase 9 section still says `### Review gate — _(to be filled …)_`, run the gate again
  (§3 below) before writing code.

## 1. Rules that governed every phase (keep them)

1. **Zero behaviour change for the lunch bot.** `tests/test_run_bot_turn_golden.py` (9
   fixtures, byte-identical) must pass; every seeded profile/agent keeps today's behaviour;
   new capabilities are opt-in (a pack in `tool_packs`, a capability on the agent).
2. **Pre-existing tests are never edited.** Check with:
   ```bash
   cd /home/user/chiatienan
   comm -12 <(git diff --name-only origin/main -- backend/tests | sort) \
            <(git ls-tree -r origin/main --name-only backend/tests | sort)
   ```
   Empty output = good. (Run from the repo root; a `backend/`-relative path returns nothing
   from `git ls-tree` and misleads.) If a framework change breaks a pre-existing test's
   duck-typed fake, adapt the framework (see `kernos/plugins/persist.py::_results`).
3. **Layering** (`tests/test_layering.py`): `kernos → kernos`; `ledger_core → kernos`;
   `packs → kernos, ledger_core, packs`; `app → all`; `bench → all`; documented exceptions
   only (`app/modelprobe.py`, `app/evalhost.py` → `bench`, lazy). `kernos/` never imports
   `app`, `packs`, `ledger_core`, `bench`.
4. **Money safety (design D3).** Tools own every number; the model never computes or re-types
   money. New tool results that carry numbers the model may quote must be *evidence* on
   purpose; anything else records a reference only (`_record`, see `kernos/engine/pi/engine.py`)
   and/or is a non-evidence pack (`BasePack.evidence = False`).
5. **No secrets in git**, no real names, bank accounts or `qr_url`s in fixtures/docs.
6. **Benchmark** needs `OPEN_ROUTER_KEY` (absent here → say "not run here").
7. **Commit messages** end with:
   ```
   Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
   Claude-Session: https://claude.ai/code/session_01LDcSPPyFYnkFWn4x2PfssJ
   ```
   (If a different model continues, keep the trailer the harness gives you.) No model
   identifiers in code, comments or docs.
8. **Per phase**: expand the plan section (facts from the code, decisions, tasks with proof
   lists) → review gate → record findings F1… with dispositions in the plan → fold them into
   the tasks → implement with tests → tick tasks, add an "as built" note → commit + push →
   docs task (design "as built", README, state of play).

## 2. How to run things

```bash
cd /home/user/chiatienan/backend
.venv/bin/python -m pytest tests -q -p no:cacheprovider          # full suite (~60 s)
.venv/bin/python -m pytest tests/test_run_bot_turn_golden.py tests/test_layering.py -q -p no:cacheprovider
cd agent_sidecar && node --test                                    # sidecar (70 tests)
```
Commit as the repo's existing author:
```bash
git add -A && \
git -c user.name="$(git log -1 --format=%an)" -c user.email="$(git log -1 --format=%ae)" commit -q -F - <<'MSG'
<title>

<body>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LDcSPPyFYnkFWn4x2PfssJ
MSG
git push -u origin claude/headless-cms-pi-harness-nn18pb
```

## 3. The review gate (how it was run)

Spawn one reviewer agent (the Agent tool, `subagent_type: general-purpose`, the strongest
model available; earlier gates used `model: "fable"`) with a prompt that: names the plan
section to review and the design sections; lists the code files whose facts to verify;
asks for security/money-safety/zero-behaviour-change/layering/test-gap/simplicity findings;
and demands the table format `| id | sev | finding | disposition |` plus a "Confirmed by the
gate:" paragraph. Then paste the table under `### Review gate (second <model>, <date>) —
findings and dispositions`, write a disposition per row ("Taken. …" / "Not taken because
…"), add a **Decisions as amended** paragraph, and fold each disposition into the task
bullets and proof lists. Every earlier phase's section shows the exact format.

## 4. Where things live (map for Phase 9)

| Concern | Files |
|---|---|
| Kernel/pipeline | `kernos/kernel/{context,pipeline,events,plugin}.py` — `Pipeline.run(ctx, through=)`, `TurnContext`, `TurnEvent`, `to_legacy`, `LegacyAgentEventSink` |
| Engine boundary | `kernos/engine/base.py` (`Engine` protocol, `EngineSpec.to_run_command`, `TurnResult` own-only reads), `kernos/engine/pi/{bridge,engine}.py` (`PiBridge`, `PiEngine` with the `_record` contract) |
| Host composition | `app/kernel.py::Kernel` (registry wiring, seeding, `pipeline_for`, `agent_for`, `subs_of`, `run_sub`, `approve_proposal`, `start_eval_run`, `static_tool_names`, `reserved_tool_names`, `business_for`, `capture_case`, `eval_mode`), `app/hostadapters.py::build_adapters`, `app/chat.py::run_bot_turn`, `app/plugins/{prompt,run,validate}.py`, `app/tools.py::ToolContext` |
| Content plane | `kernos/content/{models,store,spec,gates,boot,resolve,capabilities,traces}.py`, `kernos/api/admin.py` |
| Packs | `kernos/packs.py` (`BasePack`, `PackTool`, `DraftKind`, `PackRegistry`), `kernos/agents.py` (delegation), `kernos/osadmin.py` (CMS tools), `kernos/data/` (collections), `packs/{lunch_ledger,ledger_tools,poker_ledger}`, `app/packs/` |
| Eval | `kernos/eval/`, `app/evalhost.py`, `app/evalworld.py` |
| Adapters | `kernos/adapters/protocols.py` (9 protocols + `HostAdapters`), `kernos/adapters/memory.py` (in-memory implementations, `RecordingSink`) |
| Sidecar | `agent_sidecar/{main,session,turn,extensions,schema}.js`, tests in `agent_sidecar/test/` |
| Tests style | `tests/test_delegation.py` (`ScriptedBridge` — one bridge, several `run`s), `tests/test_os_admin.py`, `tests/test_proposals.py`, `tests/kernos/*` |

Legacy event shapes the sidecar emits (`agent_sidecar/turn.js`): `agent.run.started {turn_id}`,
`agent.text.delta {turn_id, delta}`, `agent.tool.start {turn_id, call_id, name, args}`,
`agent.tool.result {turn_id, call_id, name, status: completed|error, result}`,
`agent.run.finished {turn_id}`, `agent.run.error {turn_id, message}`. The Python side adds
`agent.sub.started/finished` (with `agent`) and forwards a sub's tool events with `agent`.

## 5. Phase 9 — detailed plan (what to build, in order)

The authoritative text is the "## Phase 9 — Portability" section of the main plan (facts,
decisions 1–6, Tasks 9.1–9.4). This section adds the implementation detail. **Apply the
review gate's dispositions first**; where they conflict with the detail below, the gate wins.

### 5.1 PR 9a — `kernos.host.BaseKernel`, `ScriptedEngine`, `AguiEventSink`

**Goal:** the framework parts compose without `app`. `app.kernel.Kernel` becomes a subclass
with identical public behaviour.

1. `kernos/host.py` — `class BaseKernel`:
   - `__init__(self, *, store, adapters, packs=None, registry=None, resolver, traces=None,
     eval_mode=False, tool_context_factory, runtime)`; `tool_context_factory(db_or_none,
     space_id, **kw) -> object` is how the host supplies its `ToolContext` (chiatienan:
     `app.tools.ToolContext`; the example host: a tiny dataclass with the fields the
     framework reads — `space_id`/`room_id`, `agent`, `depth`, `max_depth`, `turn`,
     `turn_id`, `started_at`, `calls_made`, `caps_override`, `sub_invocations`,
     `engine_spec`, `tool_config`, `validate_call`, `validate_result`).
   - Move from `app.kernel.Kernel`, unchanged in behaviour: `register_packs` (but the two
     host callbacks — `app.drafts.set_draft_kinds` and `ledger_core.configure` — become
     `on_packs_registered` hooks the subclass overrides), the framework plugin wiring
     (`Rollover, MemoryLoad, RecentHistory, ImageLookback, SectionsMessage, TemplatePrompt,
     ModelPassthrough, PackRender, Cards, Trace, EvalCapture, *validators()`), registration
     of `DelegationPack`, `OsAdminPack`, `CollectionsPack`, `pipeline_for`/`invalidate`,
     `agent_space`/`agent_for`/`subs_of`/`run_sub`, `approve_proposal`/`reject_proposal`/
     `_apply_source_changes`, `start_eval_run`/`spawn`, `static_tool_names`/
     `reserved_tool_names`, `business_for`, `capture_case`, `resolve`.
   - `run_sub` today imports `app.tools.ToolContext` and reads `parent.tool_ctx.room_id`:
     replace with `self.tool_context_factory(...)` and `space_id`.
   - `static_tool_names`/`reserved_tool_names` build a null `ToolContext(db=Database(...))`:
     replace with `self.null_tool_context()` (subclass provides).
   - Keep `app.kernel.Kernel(db, resolver=None, *, eval_mode=False)` **signature and every
     public attribute** (`db`, `adapters`, `packs`, `graders`, `store`, `data`, `registry`,
     `default_spec`, `seed_report`, `poker_report`, `gates`, `resolver`, `probe`, `eval_mode`),
     plus `kernel_for(db)` caching by `Database` (weakref). The eval host and ~15 tests use
     `Kernel(db, resolver=StaticResolver(spec), eval_mode=True)` and `kernel_for(db)`.
   - `kernos/api/admin.py`'s docstring: the kernel contract is `BaseKernel`.
2. `kernos/engine/fake.py` — `class ScriptedEngine` implementing `Engine`:
   - `__init__(self, script: list[dict])` where entries are the sidecar's replies
     (`{"type": "agent.run.started"...}`, `{"type": "tool_call", "call_id", "name", "args"}`,
     `{"type": "turn_done", "final_text", "error", "capped", "stats"}`), exactly what
     `tests/test_agent.py::FakeBridge` scripts contain.
   - `run(spec, *, turn_id, message, images, tools, call_tool, emit)` → a `TurnResult`:
     forward `agent.*` entries to `emit`, on `tool_call` await `call_tool(name, args)`,
     apply the `_record` rule (record `payload["_record"]` when present), append the
     `ToolInvocation`, and record what would have been sent back (`self.sent`) for tests;
     hydrate `turn_done` fields; never raise.
   - Also record the run command (`spec.to_run_command(...)`) on `self.runs` so tests can
     assert the manifest/caps as the delegation tests do with `ScriptedBridge.runs`.
   - Do **not** replace `PiEngine`; do not touch `tests/test_delegation.py`'s bridge.
3. `kernos/api/agui.py` — `class AguiEventSink`:
   - `__init__(self, write: Callable[[dict], Awaitable[None]], *, thread_id: str)`.
   - `emit(TurnEvent)` and `emit_raw(legacy_dict)` → AG-UI events via `to_agui(...)`:
     `run.started → RUN_STARTED {threadId, runId}`; `text.delta → TEXT_MESSAGE_START
     {messageId, role: "assistant"}` on the first delta of a message, then
     `TEXT_MESSAGE_CONTENT {messageId, delta}`, and `TEXT_MESSAGE_END` before the next
     tool call or at run end; `tool.start → TOOL_CALL_START {toolCallId, toolCallName,
     parentMessageId}` + `TOOL_CALL_ARGS {toolCallId, delta: json(args)}` + `TOOL_CALL_END`;
     `tool.result → TOOL_CALL_RESULT {messageId, toolCallId, content: json(result)}`;
     `sub.started/finished → STEP_STARTED/STEP_FINISHED {stepName: agent}`; `run.finished →
     RUN_FINISHED`; `run.error → RUN_ERROR {message}`; `validation.* / message.republished →
     CUSTOM {name, value}`. Every event carries `type`, `timestamp`.
   - Legacy dicts come from `PiEngine` through `emit_raw`; typed events from kernel
     plugins through `emit`. Provide `from_legacy(dict) -> TurnEvent | None` (inverse of
     `to_legacy`) and test the round trip both ways with `tests/kernos/test_agui.py`.
4. Tests: `tests/kernos/test_agui.py`, `tests/kernos/test_fake_engine.py` (the engine
   reproduces a `FakeBridge` script's `TurnResult` field for field, incl. `_record`),
   `tests/test_base_kernel.py` (`isinstance(kernel_for(db), BaseKernel)`; the full suite is
   the regression proof). Commit: `kernos.host: BaseKernel, a scripted engine and the AG-UI
   sink (PR 9a)`.

### 5.2 PR 9b — package export/import

1. `kernos/content/package.py`:
   - `export_profile(store, profile_id, *, version_id=None) -> dict[str, bytes]`:
     `package.json` = `{"name": f"kernos-{business_slug}-{profile_name}", "version":
     f"{version}.0.0", "keywords": ["pi-package"], "pi": {"skills": ["./skills"],
     "prompts": ["./prompts"]}, "kernos": {"business": slug, "profile_id", "version",
     "spec_sha"}}`; `skills/<slug>/SKILL.md` with frontmatter `name`, `description` (pi's
     skill format: `agent_sidecar/node_modules/@earendil-works/pi-coding-agent/docs/skills.md`);
     `prompts/<slug>.md` with `description` frontmatter for each `templates` entry;
     `AGENTS.md` = prompt body, then `## Rule: <slug>` sections (pi loads `AGENTS.md` as a
     context file — `docs/quickstart.md`); `settings.json` with `defaultModel` /
     `defaultProvider` (from the model id's prefix when `provider/model`), `thinkingLevel`,
     and `compaction`/other keys copied from `spec.settings`; `kernos.json` = the stored
     spec (`ProfileSpec.stored()` — never `runtime`). Assert no key/secret-like string.
   - `import_package(store, business_id, files: dict[str, bytes], *, actor) -> dict`:
     refuse `actor.startswith("agent:")`; reject any path with `..` or absolute (zip-slip);
     size cap (e.g. 2 MB total); parse `skills/**/SKILL.md` (frontmatter → `description`,
     `delivery` default inline; slug = directory name, validated `^[a-z][a-z0-9_-]{0,78}$`),
     `prompts/*.md` → `template` sources, `AGENTS.md` (only when no `kernos.json`) → the
     `prompt`/`system` source and `rule` sources per `## Rule:` section; write with
     `store.put_source(business_id, kind, slug, body=..., title=..., frontmatter=...,
     actor=actor)`; with `kernos.json` → `store.create_draft(profile_id, actor=actor,
     base_spec=spec, snapshot=True)` on the business's default profile (create one if none)
     — **never publish**; return `{sources: [...], draft: version|None, ignored: [paths]}`
     (`extensions/`, `themes/` ignored). Because sources are upstream of every draft
     (Phase 8 lesson), the docs must say an import changes the business's future drafts;
     consider requiring `?replace=true` to overwrite an existing source (gate finding may
     decide).
   - Admin routes: `GET /api/admin/profiles/{id}/export` → zip
     (`application/zip`); `POST /api/admin/businesses/{id}/import` → multipart or raw zip
     body; `_wrap` errors as elsewhere.
2. Tests: `tests/kernos/test_package.py` (round trip on the lunch profile; no `runtime`;
   money rule keeps its tag; a stock pi package (skills + prompts only) creates sources
   only; zip-slip path refused; `agent:*` refused), `tests/test_admin_package.py` (routes).
   Commit: `kernos.content.package: export a profile as a Pi package, import a package as
   sources and a draft (PR 9b)`.

### 5.3 PR 9c — the sidecar move and the example host

1. `git mv backend/agent_sidecar backend/kernos_sidecar`; then add a **symlink**
   `backend/agent_sidecar -> kernos_sidecar` (`ln -s kernos_sidecar backend/agent_sidecar`;
   git stores symlinks) so the two `origin/main` tests
   (`tests/test_tool_schemas_fixture.py`, `tests/test_tools_manifest.py`) and `bench/`
   keep resolving `agent_sidecar/...`. If the gate rejected the symlink, the alternative is
   to leave the directory where it is and only rename the npm package — pick what the gate
   said.
2. Update: `app/pi_bridge.py::SIDECAR_DIR` → `kernos_sidecar`; `backend/Dockerfile`
   (`COPY kernos_sidecar ./kernos_sidecar`, `npm ci` there; do **not** rely on the symlink
   in the image; check `bench/run.py` and `pi_smoke` paths), `.github/workflows/ci.yml`
   (`working-directory: backend/kernos_sidecar`, `cache-dependency-path`), `README.md`,
   `package.json` name `kernos-sidecar`, version `0.9.0`; `session.js`: temp-dir names
   `kernos-pi-cwd`/`kernos-pi-agent`; keep `KEY_ENV` if the sidecar reads it (check first:
   `grep -n KEY_ENV agent_sidecar/*.js`), otherwise remove. Historical docs
   (`docs/superpowers/*`) keep their `agent_sidecar` mentions — do not rewrite history.
3. `examples/minimal_host/host.py` (≈120 lines): `HelloPack(BasePack)` with one tool
   `say_hello(name)` returning `{"ok": True, "greeting": f"Hello, {name}!"}` and a `render`
   that turns the last own result into a `Body`; a `ProfileSpec` built in code (models,
   pipeline naming the framework plugins only: `kernos.context.memory`,
   `kernos.context.history`, `kernos.prompt.template`, `kernos.prompt.sections`,
   `kernos.model.passthrough`, a run plugin — **note**: today the only run plugin is
   `app.run.legacy`, so PR 9a must add a framework run plugin `kernos.run.engine` that
   drives an `Engine` directly from `ctx` (build `EngineSpec` via `profile.to_engine_spec()`,
   tools via `compose_tools`, executor with the per-call validators, `_record` honoured by
   the engine) — the example host uses it with `ScriptedEngine`; `kernos.render.packs`,
   `kernos.persist.cards`, `kernos.after.trace`); in-memory adapters (`kernos.adapters.
   memory`); a `ContentStore` over an in-memory SQLite engine (`kernos.content.schema.bind`,
   `sessions_for`); `ensure_seeded(...)`; `BaseKernel(...)`; FastAPI app mounting
   `admin_router(lambda: kernel)` at `/admin` and `POST /spaces/{id}/turns` that runs the
   pipeline and streams AG-UI events (collect into the JSON response for the test).
4. Tests: `tests/test_minimal_host.py` — imports `examples.minimal_host.host` after
   installing a `sys.meta_path` finder (or `sys.modules` sentinel) that raises on any
   import of `app`, `packs`, `ledger_core`, `bench`; runs one turn (`say_hello`), asserts
   the body, the AG-UI event sequence (`RUN_STARTED … TOOL_CALL_START/ARGS/END,
   TOOL_CALL_RESULT, TEXT_MESSAGE_*, RUN_FINISHED`), and `GET /admin/registry`.
   `tests/test_layering.py`: add `"examples": {"kernos", "examples"}` to `LAYERS`/`ALLOWED`
   (the test skips missing dirs, so add the layer name). `pyproject.toml`: add `examples*`
   to the package `include` only if the test needs it importable (a `conftest` path insert
   is simpler and keeps `examples` out of the distribution).
5. Verify: sidecar tests from the new directory; the two pinned tests unchanged; golden
   9/9; full suite. Commit: `kernos_sidecar + examples/minimal_host: the sidecar moves, a
   host with no chiatienan on its path runs a turn (PR 9c)`.

### 5.4 Task 9.4 — docs and close-out

- Design §12 "as built" (BaseKernel, `kernos.run.engine`, `ScriptedEngine`, the AG-UI sink
  mapping table, the export file list, the symlink decision); README (kernos_sidecar,
  export/import routes, the example host, how to run it); `TODO.md`: close "BIG: agent
  engine export/import" with the file format and the import flow; `kernos/__init__.py`
  `__version__ = "0.9.0"`; `scripts/split_kernos.sh` (prints the `git subtree split`
  command for `backend/kernos`, `backend/kernos_sidecar`, `backend/examples` — documented,
  not run); plan: tick 9.1–9.4, "Phase 9 — state of play", and a closing paragraph at the
  end of the plan summarising all nine phases (test counts, what is opt-in, known limits:
  `ask_*` not in eval runs, steward schedule is the operator's, frontend cards for
  `game_draft`/proposals pending, benchmark not run here).

## 6. Things that bit us (so they do not bite again)

- `git ls-tree origin/main -- <path>` must be run from the repo root with `backend/...`
  paths; from `backend/` it silently returns nothing (that is how PR 7a edited a
  pre-existing test; PR 8a restored it).
- Every publish of the lunch profile needs `override_reason` (gate 2: a money profile with
  a risky builtin tool). Tests use `override_reason="test: …"`; boot uses `bypass_gates`.
- Gate 5 compares specs **as stored** (no `runtime`); a resolved `ProfileSpec` carries the
  host's runtime — use `_stored()` in `kernos/content/gates.py` for any new comparison.
- `PiEngine` records `payload["_record"]` when present and sends the payload without it;
  the sidecar keys pending tool calls by `req_id:call_id` (nested runs share one bridge).
- A test that scripts two runs on one bridge must use distinct `call_id`s per run when it
  reads results back by id (`ScriptedBridge.tool_result`).
- `ensure_seeded` re-puts a source only while `updated_by == "boot"`.
- The eval world is a fresh DB per case: no agent tree there, so `ask_*` never appears in
  an eval run (documented limit).
- Summaries (`kernos.plugins.after.summarize`) add keys only when present
  (`agent_log`, verdict `span`) — tests compare exact dicts.
