# Agent OS framework (`kernos`) — Implementation Plan

> **For agentic workers:** implement task-by-task, in order. Steps use checkbox
> (`- [ ]`) syntax for tracking. Every task ends in a commit. Do not batch tasks.

**Design:** [`../specs/2026-09-05-agent-cms-design.md`](../specs/2026-09-05-agent-cms-design.md)
(v3). Section numbers below refer to it.

**Goal:** turn the agent half of chiatienan into a portable framework — a kernel that
runs a defined turn pipeline, a content plane (the CMS) that configures every
component, and data / observability / eval planes — with chiatienan as the first
host and a poker ledger as the second business. **Phase 1 changes no behaviour**, and
the existing test suites and benchmark are the proof.

**Tech stack:** Python 3.11/3.12 · FastAPI · SQLAlchemy 2 · SQLite (WAL) · pytest ·
`jsonschema` (new) · Node ≥ 22.19 plain ESM for the sidecar · `node --test`.

---

## Global constraints

- **D3 stands.** All money is integer VND; tools own every number; nothing in this
  plan moves arithmetic into the kernel, the sidecar or a model's prose.
- **`run_turn`'s signature and `TurnResult`'s shape are frozen**
  (`run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None)`).
  14 `monkeypatch.setattr` sites across 4 test files patch `app.agent.run_turn` or
  `app.chat.run_bot_turn`; Phase 1 must keep both names importable from those modules
  and must look them up **at call time** (module attribute, not a captured reference)
  so the patches keep working.
- **The `agent.*` SSE event names are frozen** — the frontend consumes them.
- **Layering is a test, not a convention.** `kernos` imports neither `app`, `packs`
  nor `ledger_core`; `ledger_core` imports neither `app` nor `packs`; `packs` never
  import `app`. `tests/test_layering.py` walks the import graph with `ast` and fails on
  any reverse edge (Task 1.1).
- The backend stays a **single process**; the writer lock and the SSE hub are
  in-process. Nothing here adds `--workers`.
- Backend tests run from `backend/` with `pytest -q`; sidecar tests from
  `backend/agent_sidecar/` with `node --test`; frontend from `frontend/` with `npm test`.
- **The benchmark needs `OPEN_ROUTER_KEY`.** In an environment without it, record
  "not run here" in the task and run it where the key exists before merging Phase 1
  and Phase 3. Equality target: `bench/results/pi-typical-r3.json`.
- The sidecar stays at `backend/agent_sidecar/` through Phase 8. Its path is a boot
  parameter of `PiEngine`, so moving it (Phase 9) is a path change, not a refactor.
- **No secret, real name, bank account or `qr_url` is committed.** Unchanged.

---

## Phase 0 — Review before code

### Task 0.1: Second-reviewer pass over the design and this plan

- [ ] Hand both documents to an independent reviewer with repository access and a
      brief to attack: layering leaks, Phase 1 behaviour drift, the frozen contracts,
      D3 across sub-agents, the reflexivity clause, the stage list, and anything the
      plan cannot verify.
- [ ] Record the findings and their disposition in the section below. Anything that
      changes an interface is fixed in the design *before* Task 1.1 starts.
- [ ] Commit: `docs(kernos): record the pre-implementation review`

**Findings and dispositions:** _(filled by Task 0.1)_

---

## Phase 1 — The kernel, with today's behaviour as plugins (zero behaviour change)

Order matters: primitives first, then the engine behind a protocol, then the host
adapters, then the plugins, then the swap of `run_bot_turn`, then the proof.

### Task 1.1: Package skeleton and the layering test

**Files:** create `backend/kernos/__init__.py`, `backend/kernos/{kernel,registry,engine,content,adapters}/__init__.py`;
create `backend/tests/test_layering.py`; modify `backend/pyproject.toml`.

- [ ] Add `kernos*`, `ledger_core*`, `packs*` to `[tool.setuptools.packages.find] include`
      and `jsonschema>=4` to `dependencies`.
- [ ] `test_layering.py`: parse every `.py` under `backend/{kernos,ledger_core,packs,app}`
      with `ast`, collect top-level package of each import, assert the allowed edges
      only: `app → {kernos, ledger_core, packs, app}`, `packs → {kernos, ledger_core, packs}`,
      `ledger_core → {kernos, ledger_core}`, `kernos → {kernos}`. Missing directories
      are skipped so the test passes before Phase 3 creates them.
- [ ] Verify: `pytest tests/test_layering.py -q` passes; `pytest -q` count unchanged.
- [ ] Commit: `kernos: package skeleton and the layering test`

### Task 1.2: Kernel primitives

**Files:** create `backend/kernos/kernel/{context.py,plugin.py,pipeline.py,events.py}`;
create `backend/tests/kernos/test_pipeline.py`.

- [ ] `context.py`: `Stage` (`StrEnum`: `resolve, gate, context, prompt, model, run,
      validate_args, validate_result, validate, render, persist, after`);
      `TurnContext` dataclass (space_id, principal, text, images, profile, memory,
      history, knowledge, system, message, model, vision_model, thinking, caps,
      result: TurnResult | None, body, attachments, trace: list[dict], stopped: bool,
      reply_override: str | None); `Verdict(ok, severity: "warn"|"block", reason,
      patch=None)`.
- [ ] `plugin.py`: `Plugin` protocol (`id`, `version`, `stage`, `config_schema`,
      `handles_money`, `run(ctx, config)`), `PluginRef(plugin, version, config)`.
- [ ] `pipeline.py`: `Pipeline(stages: dict[Stage, list[tuple[Plugin, dict]]])` with
      `async run(ctx)`: iterate stages in order; list stages run every plugin in order;
      a `Verdict` with `block` sets `ctx.stopped` and `reply_override` and skips to
      `persist`; every plugin appends `{stage, plugin, version, ms, outcome}` to
      `ctx.trace`; a plugin exception is recorded and re-raised (the caller decides).
      Single-owner stages (`resolve`, `model`, `run`, `render`) must have exactly one
      entry — validated at construction.
- [ ] `events.py`: `TurnEvent` dataclass + the typed names of §12.4; a
      `LegacyAgentEventSink` that maps them to the frozen `agent.*` dicts.
- [ ] Tests: stage order; block short-circuits; trace shape; single-owner enforcement;
      exception recorded in trace.
- [ ] Commit: `kernos: TurnContext, Plugin protocol, Pipeline runner, TurnEvent`

### Task 1.3: Registry

**Files:** create `backend/kernos/registry/{__init__.py,registry.py}`; create
`backend/tests/kernos/test_registry.py`.

- [ ] `Registry.register(plugin)` (duplicate id+version is an error), `get(id, version=None)`
      (latest when omitted), `list()`, `validate_config(id, version, config)` via
      `jsonschema.Draft202012Validator`, `describe()` → `[{id, version, stage,
      config_schema, handles_money}]` (the admin API's registry payload), and
      `load_entry_points(group="kernos.plugins")`.
- [ ] `build_pipeline(registry, spec_pipeline: dict) -> Pipeline` — resolves
      `{plugin, version, config}` triples, validates each config, raises a single
      aggregated error naming every bad triple.
- [ ] Tests incl. an invalid config rejected with the JSON-pointer path in the message.
- [ ] Commit: `kernos: plugin registry with schema-validated pipeline construction`

### Task 1.4: `Engine` protocol and `PiEngine`

**Files:** create `backend/kernos/engine/{base.py,pi/__init__.py,pi/bridge.py,pi/engine.py}`;
modify `backend/app/pi_bridge.py` and `backend/app/agent.py` to thin re-exports.

- [ ] `base.py`: `EngineSpec` (system, skills, context_files, model, vision_model,
      thinking, builtin_tools, max_tools, max_seconds, cwd, agent_dir, settings: dict,
      extensions: list), `ToolSpec(name, description, schema)`, `Engine` protocol
      `run(spec, message, images, tools, call_tool, emit) -> TurnResult`.
- [ ] `pi/bridge.py`: **move** `app/pi_bridge.py` here unchanged except: the sidecar
      entry and the key-env names become constructor parameters with today's values as
      defaults; `get_bridge()` stays in `app.pi_bridge` as a thin wrapper so
      `test_pi_bridge.py` and `pi_smoke.py` pass unedited.
- [ ] `pi/engine.py`: `PiEngine(bridge)` implements `Engine.run` with the body of
      today's `agent.run_turn` loop (`agent.*` forwarding, `tool_call` → `call_tool`,
      `turn_done` hydration, the one-line log). `TurnResult`/`ToolInvocation` move to
      `kernos/engine/base.py`; `app.agent` re-exports them (import path unchanged).
- [ ] `app/agent.py`: `run_turn(...)` keeps its frozen signature and body shape but
      builds an `EngineSpec` from `settings`/`prompt.py`/skill files exactly as today
      and calls `PiEngine`. `_render_prompt`, `_read_skills`, `_read_context_files`
      stay here for Task 1.7 to reuse.
- [ ] Sidecar: `session.js` accepts optional `settings` (→ `SettingsManager.inMemory`)
      and `extensions` (looked up in a new `extensions.js` registry; empty today),
      both ignored when absent. Add tests in `test/session.test.js`.
- [ ] Verify: `pytest -q` unchanged; `node --test` +N new, none failing.
- [ ] Commit: `kernos: Engine protocol; PiEngine wraps the bridge and the turn loop`

### Task 1.5: Host adapter protocols and chiatienan's implementations

**Files:** create `backend/kernos/adapters/{protocols.py,memory.py}`; create
`backend/app/hostadapters.py`; tests `backend/tests/kernos/test_adapters_inmemory.py`.

- [ ] `protocols.py`: `HistorySource`, `MemoryStore`, `KnowledgeSource`, `EventSink`,
      `MessageStore`, `ToolExecutor` exactly as §4.6.
- [ ] `memory.py`: in-memory implementations of all six (for tests and the example host).
- [ ] `app/hostadapters.py`: `RoomHistory` (wraps `chat.build_history`), `RoomMemory`
      (wraps `memory.py`), `RoomKnowledge` (wraps `knowledge.snapshot`), `SseSink`
      (wraps the `emit` coroutine + `LegacyAgentEventSink`), `RoomMessages`
      (wraps `chat.post_message`). Pure delegation; no logic.
- [ ] Commit: `kernos: host adapter protocols, in-memory adapters, chiatienan adapters`

### Task 1.6: Today's behaviour as plugins

**Files:** create `backend/kernos/plugins/{gate.py,context.py,prompt.py,after.py}`
(host-agnostic); create `backend/app/plugins/{gate.py,validate.py,render.py,persist.py}`
(chiatienan-specific until Phase 3 moves them into packs); tests per plugin.

Each plugin is a **move** of an existing block, with its comments. Where today's code
reads `settings.*`, the plugin reads `config[...]` and the seeded profile (Task 1.7)
supplies today's env values.

| Plugin id | Stage | Moved from |
|---|---|---|
| `kernos.context.memory` | context | `run_bot_turn`: `_maybe_rollover` + `memory.load_memory` (rollover itself is `after`; here only the load) |
| `kernos.context.history` | context | `build_history(...)` call with watermark/limit |
| `kernos.context.images` | context | `recent_images(...)` carry-over |
| `kernos.prompt.template` | prompt | `prompt.build_system_prompt` → renders `spec.prompt.body` with the closed variable set; Phase 1's body is the *current string* with `{{sender.name}}`, `{{sender.member_id}}`, `{{today}}` substituted |
| `kernos.prompt.sections` | prompt | `agent._render_prompt` (section headers from config) |
| `kernos.after.rollover` | after | `_maybe_rollover` |
| `kernos.after.turn_log` | after | the `[agent] turn … done` log line |
| `app.gate.mention` | gate | `mentions_bot` / `is_clear_command` (`/clear` stays a code command) |
| `app.validate.fabricated_commit` | validate | `chat.py:645-656` block, severity `block` |
| `app.validate.unbacked_amounts` | validate | `chat.py:664-677`, severity `warn` |
| `app.render.lunch` | render | the `_settlement_body … _random_pick_body` chain + `_empty_turn_body` |
| `app.persist.drafts` | persist | the `propose_meal` / `propose_payment` draft branches, `post_message`, superseded/cancelled card republish |

- [ ] Implement each; unit-test each against the same fixtures the `test_chat*.py`
      files use today (import their helpers, do not copy them).
- [ ] Commit per group: `kernos: context/prompt/after plugins` ·
      `app: gate/validate/render/persist plugins`

### Task 1.7: Seeded default profile and the resolver

**Files:** create `backend/kernos/content/{spec.py,resolve.py}`; create
`backend/app/default_profile.py`; tests `backend/tests/kernos/test_spec.py`,
`backend/tests/test_default_profile.py`.

- [ ] `spec.py`: `ProfileSpec` (pydantic) with the §5.1 shape (persona, prompt, rules,
      skills, templates, models, retry, caps, builtin_tools, memory, settings, pipeline,
      tool_packs, validation, eval). `to_engine_spec(...)` produces the `EngineSpec`.
- [ ] `resolve.py`: `Resolver` protocol `resolve(space_id) -> ProfileSpec`; Phase 1's
      `StaticResolver(spec)`.
- [ ] `app/default_profile.py`: `build_default_spec(settings)` reads `prompt.py`'s
      string (turned into a template), the five `SKILL.md`, `money-safety.mdc`, and
      every `PI_*`/memory env value into a `ProfileSpec` whose `pipeline` lists the
      Task 1.6 plugins in today's order.
- [ ] **Equivalence test:** `to_engine_spec(build_default_spec(settings))` plus the
      rendered prompt/message equals, field for field, the `run` command
      `app.agent.run_turn` builds today for the same inputs (assert on the dict, not
      on a golden file, so a settings change fails loudly).
- [ ] Commit: `kernos: ProfileSpec, StaticResolver, and chiatienan's seeded default profile`

### Task 1.8: `run_bot_turn` becomes the pipeline

**Files:** modify `backend/app/chat.py`; create `backend/app/kernel.py` (composition root).

- [ ] `app/kernel.py`: builds the `Registry` (registers Task 1.6 plugins), the
      `StaticResolver(build_default_spec(settings))`, the `PiEngine`, the host adapters,
      and exposes `get_pipeline(spec)`.
- [ ] `chat.run_bot_turn(...)`: same signature; body becomes: build `TurnContext`,
      `spec = resolver.resolve(room_id)`, `await pipeline.run(ctx)` under `_agent_lock`,
      return `ctx.persisted_message`. The `run` stage plugin calls
      **`app.agent.run_turn` looked up at call time** (see Global constraints).
- [ ] Verify: `pytest -q` — **all 751 pass unedited**. Any edit to an existing test is
      a behaviour change and must be justified in the commit message, or reverted.
- [ ] Verify: `node --test` green; `npm test` green (untouched).
- [ ] Commit: `chat: run the @phoenix turn through the kernos pipeline (no behaviour change)`

### Task 1.9: The resolved-profile endpoint and the proof

**Files:** modify `backend/app/main.py`; tests `backend/tests/test_admin_resolved.py`.

- [ ] `GET /api/admin/rooms/{room_id}/resolved` (guarded by `require_admin`): returns
      `{spec, engine_spec, pipeline: [{stage, plugin, version, config}], trace_sample}`.
- [ ] Test: the `engine_spec` for a seeded room equals the command Task 1.7's
      equivalence test asserts.
- [ ] Benchmark (where a key exists): `python -m bench.run --corpus typical --repeat 3`
      and `bench.report --compare` against `pi-typical-r3.json`; record the result in
      this file under "Phase 1 — state of play".
- [ ] Commit: `admin: expose the resolved profile; record the Phase 1 benchmark`

### Task 1.10: Docs

- [ ] README "Architecture" and the backend-modules table: add `kernos/`, note that
      `agent.py`/`pi_bridge.py` are shims over `kernos.engine.pi`.
- [ ] `TODO.md`: point the export/import item at the plan.
- [ ] Commit: `docs: kernos in the README`

**Phase 1 — state of play:** _(filled as tasks complete)_

---

## Phase 2 — The content plane

- **2.1** `kn_` tables (`businesses, agents, profiles, profile_versions, prompts, rules,
  skills, prompt_templates, model_catalogue, audit_log`) under `kernos.content.models`
  with its own `Base`; `kernos.bind(engine)`; additive column sync reusing the pattern of
  `db._sync_additive_columns`. `rooms` gains `manager_agent_id`, `agent_overrides`.
- **2.2** Sources CRUD with etags (the `knowledge.py` pattern); draft version from sources;
  snapshot-on-publish; `DbResolver` (space → agent → published version, cached by
  version id); `StaticResolver` remains for hosts without a DB.
- **2.3** Publish gates 1, 2, 3, 5 (§9); `override_reason` in the audit log;
  `bench.probe_models` wrapped as `kernos.content.probe`.
- **2.4** Mountable admin router `kernos.api.admin_router(os)` with the §5.2 routes,
  `GET …/registry`, `GET …/plugins/{id}/schema`; chiatienan mounts it under `/api/admin`
  behind `require_admin`.
- **2.5** Boot: on first start with empty tables, insert the seeded default business,
  agent, profile and version from `build_default_spec` — a fresh install runs today's bot.
- **Proof:** a room bound to an edited profile runs the edit; an unbound room runs the
  seeded default; API tests for every route; publish refused on each gate.

## Phase 3 — Packs and the ledger domain

- **3.1** `ToolPack` protocol (§4.2) and `DraftKind`; `kernos.packs` registry alongside
  plugins.
- **3.2** Extract `ledger_core` from `app`: `money.py`, `ledger.py`, `qr.py`,
  `periods.py`, `roster.py`, `drafts.py` (generalised over `DraftKind`), `moneyguard.py`,
  member models. `app` re-exports for one release.
- **3.3** `packs/lunch_ledger`: tools (from `tools.py`), renderers (from `app.plugins.render`),
  draft kinds, `balance_contributions` (from `build_debt_edges`), `fixtures` (from
  `bench.world`), `seed` (from `seed_places`), models (`Meal`, `MealShare`, `Place`).
- **3.4** `ledger.period_balances` → Σ `pack.balance_contributions` then payments FIFO;
  `chat` render → first pack that claims the result type.
- **Proof:** benchmark equality again; a stub pack with two tools runs end to end.

## Phase 4 — Observe and eval

- **4.1** `kn_turn_traces`; the trace written at `after`; `GET …/turns/{id}`.
- **4.2** Eval tables (§5.5); grader plugins wrapping `bench.graders`; judge protocol
  wrapping `bench.judge`; fixtures from packs; `kernos.eval.run(suite, version)`.
- **4.3** Import `bench.corpus` typical/week/meals as the lunch suite, ids preserved;
  rubric as content.
- **4.4** Gate 4; `eval_capture` plugin.
- **Proof:** the imported suite reproduces `pi-typical-r3.json` verdicts.

## Phase 5 — Data plane

- `Collection` content type, `kn_documents`, generated `{collection}_find/upsert/delete`
  tools with schema validation, aggregation refused by construction.

## Phase 6 — `poker_ledger`

- Pack per §7.2; `chips-conserved` validation rule; skills; suite; a poker business.
  **Proof:** its suite green; the lunch suite unchanged.

## Phase 7 — Agents and sub-agents

- `ask_<sub>` generated tools; nested pipeline run with `depth+1` and min caps; result
  merging; `sub.*` events. **Proof:** merged results pass `unbacked_amounts`.

## Phase 8 — AI-ready

- `os_admin` pack (§8.2); `capabilities`, `self_change_scope`, blacklist; `kn_change_proposals`
  and the proposal card; in-turn loop bounds; steward brief as a prompt template.
  **Proof:** the §8.5 steward scenario end to end.

## Phase 9 — Portability

- `examples/minimal_host`; `kernos.api.agui` sink; Pi-package export/import; sidecar
  extension registry; move the sidecar to `backend/kernos_sidecar/` and update
  Dockerfile/CI; packaging metadata for PyPI/npm; `git subtree split` rehearsal.
  **Proof:** the example host runs a "hello" business with no `app`/`packs` on its path.
