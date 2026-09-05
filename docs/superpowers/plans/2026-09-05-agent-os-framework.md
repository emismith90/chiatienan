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
  18 `monkeypatch.setattr` sites across 4 test files (`test_chat.py` 11,
  `test_chat_payment_turn.py` 3, `test_bill_image_carryover.py` 2, `test_api.py` 2) patch
  `app.agent.run_turn` or `app.chat.run_bot_turn`; Phase 1 keeps both names importable,
  looks them up **at call time**, and passes the resolved spec through
  `ToolContext.engine_spec` — the one argument every fake ignores (review finding 1).
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

- [x] An independent reviewer (a second Fable instance, read-only, with the repository)
      attacked the v3 design and this plan. Verdict: **NO-GO as written; GO WITH CHANGES
      once findings 1–6 are fixed in the docs.** All fourteen findings were verified
      against the code and dispositioned below; the design is now v3.1.
- [x] Commit: `docs(kernos): record the pre-implementation review`

**Findings and dispositions:**

| # | Sev | Finding (verified) | Disposition |
|---|---|---|---|
| 1 | blocker | "Look up `app.agent.run_turn` at call time" leaves `prompt`/`model` stages dead: `run_turn` builds `system`/`message` itself from `settings`; its frozen 6-arg signature cannot take a spec, and the 13 fakes in `test_chat.py`/`test_bill_image_carryover.py` declare that exact signature. 18 patch sites, not 14 (`test_chat.py` has 11). | **Accepted.** The spec rides on the one argument every fake ignores: `ToolContext` gains `engine_spec: EngineSpec \| None = None` plus `system`/`message` overrides; `run_turn` uses them when present, else builds as today (Task 1.4). Constraints corrected to 18 sites. |
| 2 | blocker | Moving `_maybe_rollover` to `after` changes behaviour: today it runs first under the lock, so the ageing turn sees the new summary and the advanced watermark. It also calls the summariser (an LLM call) which no protocol covered. | **Accepted.** `kernos.context.rollover` is the first `context` plugin; `Completion` protocol added (§4.6). |
| 3 | blocker | `validate → render` inverts `chat.py`: moneyguard only runs on the free-prose fallback; a generic pre-render validator would warn on settlement bodies and could block a settle turn saying "Đã ghi". `fabricated_commit` needs `meal_exists` + history. | **Accepted.** Order is `render → validate → persist`; `render` yields an `outcome` (draft \| body with `claimed_by_pack`); validators no-op when claimed; `TurnContext` gains `before_id`, `history`, `tool_ctx`. |
| 4 | blocker | `backed_amounts` counts numbers in tool *results*; an `ask_<sub>` result carrying prose launders hallucinated numbers; merging results lets `last_result("propose_meal")` pick a sub-agent's draft. | **Accepted.** Sub-agent invocations tagged `from_agent`; their `text` excluded from the recorded result; drafts read from untagged invocations only (§6). |
| 5 | major | Six protocols are not enough: drafts with supersede, the summariser, ledger lookup, clock, superseded-card republish (not an `agent.*` event), `RoomHub` busy markers; `HistorySource` would bake `phoenix:` in. | **Accepted.** Added `CardStore`, `Completion`, `Clock`; `message.republished` event; `bot_label` parameter; busy markers documented host-side (§4.6, §12.4). |
| 6 | major | The benchmark calls `app.agent.run_turn` directly (`bench/run.py:122`) and never enters `run_bot_turn`; it proves the engine move, not the pipeline swap. | **Accepted.** Task 1.0 records golden fixtures from the *current* `run_bot_turn` for five branches before any code moves; Task 1.8 replays them through the pipeline. |
| 7 | major | `pi_bridge._child_env` reads `settings.pi_model`; `SIDECAR_DIR` is relative to `app/` — both break layering; three log lines for one turn. | **Accepted.** Both become constructor parameters; exactly one `[agent] turn` log line, kept in `run_turn` (Task 1.4). |
| 8 | major | `build_system_prompt` has conditionals (`who` sentence, `member_id`) pinned by `test_prompt.py`; pure `{{var}}` cannot reproduce it. | **Accepted.** Phase 1 keeps the persona block as code (`app.prompt.phoenix` plugin); template conditionals are a Phase 2 content-plane decision (§0.2). |
| 9 | major | Gating happens in `main.py:626-655` before `run_bot_turn`; `/clear` never enters a turn; `replies_to_bot_question` missing. | **Accepted.** `gate` is a host-invoked kernel helper, not a stage; Phase 1 leaves `main.py` untouched (§4.1). |
| 10 | major | Reflexivity holes: `eval.suites` not blacklisted; `rules` in scope includes `money-safety`; `cms_add_eval_case` seeds the judging suite; a `sub` steward cannot reach `auto_published`. | **Accepted.** `eval.*` and rules tagged `money` blacklisted; gate 4 runs `review: false` only; steward auto-publish limited to its own profile (§8.3, §8.5, §9). |
| 11 | major | `get(id, version=None)` → latest breaks snapshot discipline; identical `id@version` with a changed schema is undetectable. | **Accepted.** Version mandatory in published specs; registry stores a schema hash and refuses a mismatch (§4.3). |
| 12 | minor | Unverifiable proofs: benchmark needs a key; "pass unedited" unenforced; Phase 4 "reproduces r3 verdicts" impossible across 3 non-deterministic repeats; "byte-equals" must exclude `req_id`/`turn_id`. | **Accepted.** Benchmark run manually with the results commit hash recorded; a diff check on `backend/tests/` (Task 1.8); Phase 4 proof is `bench.regrade` of stored r3 records through the grader plugins; equivalence excludes ids and freezes `today`. |
| 13 | minor | Phase numbers inconsistent (§8.7, §7.4); gate 2 needs pack `handles_money` before packs exist; `kernos.content.probe` wrapping `bench` breaks layering; Phase 1 too big for one PR. | **Accepted.** Numbers fixed; Phase 2 reads `handles_money` from plugins and from the seeded profile's metadata until Phase 3; the probe is a host-provided `ModelProbe`; Phase 1 is cut into three PRs (1a/1b/1c below). |
| 14 | minor | `Plugin.run` sync vs async work; `Verdict.patch` untyped; `TurnContext` missing fields Task 1.8 uses; Python `model` stage duplicates `resolveModel` in `session.js`; superseded emits would move inside the lock. | **Accepted.** `run` is async; `Verdict.replacement: Body \| None`; fields added; `model` stage is a passthrough in Phase 1 (the sidecar keeps routing); `pending_events` flushed after the lock. |

Verified correct by the reviewer: the §1 `run` field inventory; `SettingsManager.inMemory`
and `extensionFactories` as real options; `run_turn` never raises; the `test_api` fakes
accept `*a, **k`; the images carry-over and `before_id` threading; the sidecar's
`summarize` being specified-not-inherited; 65 sidecar tests; `require_admin` for Task 1.9;
the layering test design.

---

## Phase 1 — The kernel, with today's behaviour as plugins (zero behaviour change)

Three pull requests, each independently green and each proving something:

| PR | Tasks | Proves |
|---|---|---|
| **1a** | 1.0 – 1.4 | the engine moves behind a protocol with no production path changed (benchmark-provable) |
| **1b** | 1.5 – 1.7 | adapters, plugins and the seeded profile exist and are unit-tested; no production path touched |
| **1c** | 1.8 – 1.10 | the swap: `run_bot_turn` runs the pipeline and the golden fixtures replay byte-identical |

### Task 1.0: Record golden fixtures from the current `run_bot_turn` (before anything moves)

**Files:** create `backend/tests/golden/turn_fixtures.py`, `backend/tests/test_run_bot_turn_golden.py`.

- [ ] Drive today's `chat.run_bot_turn` with a fake `run_turn` (the shape the 11 fakes in
      `test_chat.py` use) for five branches: meal draft (`propose_meal`), payment draft
      (`propose_payment`), settlement body (`settle_period`), free-prose fallback with an
      unbacked amount (warn path), and a fabricated-commit body (block path). Also the
      cancelled-draft republish.
- [ ] For each, record: the persisted `RoomMessage` (kind, body, attachments), every
      `emit`ted event in order, and the superseded/cancelled payloads. Store as Python
      literals with `turn_id`/ids normalised; freeze `now_ict`.
- [ ] The test asserts the current code reproduces the fixtures. It must pass
      **before** Task 1.1 and stay untouched through 1.10.
- [ ] Commit: `tests: golden fixtures for run_bot_turn's five outcome branches`

### Task 1.1: Package skeleton and the layering test

**Files:** create `backend/kernos/__init__.py`, `backend/kernos/{kernel,registry,engine,content,adapters,plugins}/__init__.py`;
create `backend/tests/test_layering.py`; modify `backend/pyproject.toml`.

- [ ] Add `kernos*`, `ledger_core*`, `packs*` to `[tool.setuptools.packages.find] include`
      and `jsonschema>=4` to `dependencies`.
- [ ] `test_layering.py`: parse every `.py` under `backend/{kernos,ledger_core,packs,app}`
      with `ast`, collect the top-level package of each import, assert only the allowed
      edges: `app → {kernos, ledger_core, packs, app, bench-free}`, `packs → {kernos,
      ledger_core, packs}`, `ledger_core → {kernos, ledger_core}`, `kernos → {kernos}`.
      Missing directories are skipped so the test passes before Phase 3.
- [ ] Verify: `pytest -q` — 986 passed + the new ones; nothing edited.
- [ ] Commit: `kernos: package skeleton and the layering test`

### Task 1.2: Kernel primitives

**Files:** create `backend/kernos/kernel/{context.py,plugin.py,pipeline.py,events.py}`;
create `backend/tests/kernos/test_pipeline.py`.

- [ ] `context.py`: `Stage` (`StrEnum`: `resolve, context, prompt, model, run,
      validate_args, validate_result, render, validate, persist, after`); `Draft(kind,
      payload)`, `Body(text, attachments, claimed_by_pack)`, `Outcome = Draft | Body`;
      `TurnContext` dataclass with exactly the fields of design §4.2 (`space_id, principal,
      turn_id, text, images, before_id, depth, profile, memory, history, knowledge, system,
      message, model, vision_model, thinking, caps, tool_ctx, result, outcome, persisted,
      superseded, pending_events, trace`); `Verdict(ok, severity, reason, replacement)`.
- [ ] `plugin.py`: `Plugin` protocol (`id`, `version`, `stage`, `config_schema`,
      `handles_money`, `async run(ctx, config)`), `PluginRef(id, version, config)`.
- [ ] `pipeline.py`: `Pipeline(stages)`; `async run(ctx)`: stages in order; list stages
      run each plugin; a `block` verdict replaces `ctx.outcome` and skips to `persist`;
      every plugin appends `{stage, plugin, version, ms, outcome}` to `ctx.trace`; an
      exception is recorded then re-raised. Single-owner stages (`resolve, model, run,
      render`) must have exactly one entry, checked at construction. `pending_events` are
      **not** emitted by the pipeline; the caller flushes them after its lock.
- [ ] `events.py`: `TurnEvent` + the §12.4 names incl. `message.republished`;
      `LegacyAgentEventSink` mapping to the frozen `agent.*` dicts and the
      `{"type":"message", …}` republish.
- [ ] Tests: order; block short-circuit; trace shape; single-owner enforcement;
      exception recorded; events not auto-flushed.
- [ ] Commit: `kernos: TurnContext, Outcome, Plugin protocol, Pipeline runner, TurnEvent`

### Task 1.3: Registry

**Files:** create `backend/kernos/registry/registry.py`; tests `backend/tests/kernos/test_registry.py`.

- [ ] `Registry.register(plugin)` — duplicate `id@version` with an identical schema hash
      is idempotent; with a different hash it raises. `get(id, version)` — **version
      required**. `list()`, `validate_config(...)` via `jsonschema.Draft202012Validator`
      (error message carries the JSON-pointer path), `describe()`, `schema_hash(plugin)`,
      `load_entry_points(group="kernos.plugins")`.
- [ ] `build_pipeline(registry, spec_pipeline)` — resolves `{id, version, config}` triples,
      aggregates every validation error into one exception.
- [ ] Commit: `kernos: plugin registry with mandatory versions and schema hashes`

### Task 1.4: `Engine` protocol and `PiEngine` — the seam the fakes ignore

**Files:** create `backend/kernos/engine/{base.py,pi/bridge.py,pi/engine.py}`; modify
`backend/app/pi_bridge.py`, `backend/app/agent.py`, `backend/app/tools.py` (`ToolContext`).

- [ ] `base.py`: `EngineSpec`, `ToolSpec`, `TurnResult`, `ToolInvocation` (with
      `from_agent: str | None = None`), `Engine` protocol.
- [ ] `pi/bridge.py`: move `app/pi_bridge.py` here with **constructor parameters** for
      `sidecar_entry`, `key_env`, `pi_key_env`, and `child_env_defaults: dict` (what
      `_child_env` used to read from `settings`). `app.pi_bridge` becomes a shim that
      constructs it with today's values and keeps `get_bridge()`, `BridgeError`,
      `SIDECAR_DIR`, `SIDECAR_ENTRY`, `KEY_ENV`, `PI_KEY_ENV` importable
      (`test_pi_bridge.py`, `pi_smoke.py`, `main.py` import them).
- [ ] `pi/engine.py`: `PiEngine(bridge)` — the loop body of today's `run_turn`
      (`agent.*` forwarding, `tool_call` → `call_tool`, `turn_done` hydration). **No log
      line here.**
- [ ] `app/tools.py`: `ToolContext` gains `engine_spec: EngineSpec | None = None`,
      `system_override: str | None = None`, `message_override: str | None = None`
      (defaulted; every existing constructor call and fake is unaffected).
- [ ] `app/agent.py`: `run_turn(...)` frozen signature. If `ctx.engine_spec` is set it is
      used (with the overrides for `system`/`message`); otherwise the spec is built exactly
      as today. Either way it calls `PiEngine` and logs the **one** `[agent] turn … done`
      line. `TurnResult`/`ToolInvocation` re-exported from here.
- [ ] Equivalence test: with `engine_spec=None`, the command handed to the bridge is
      field-for-field what today's code builds (ids excluded, `today` frozen).
- [ ] Sidecar: `session.js` accepts optional `settings` → `SettingsManager.inMemory` and
      `extensions` → a new `extensions.js` registry (empty); ignored when absent; tests.
- [ ] Verify: `pytest -q` 986 + new, nothing edited; `node --test` green. Benchmark where a
      key exists; record the results file's commit hash here.
- [ ] Commit: `kernos: Engine protocol; PiEngine behind run_turn via ToolContext.engine_spec`

**PR 1a ends here.**

### Task 1.5: Host adapter protocols and chiatienan's implementations

**Files:** create `backend/kernos/adapters/{protocols.py,memory.py}`; create
`backend/app/hostadapters.py`; tests.

- [ ] `protocols.py`: `HistorySource` (`render(space_id, *, bot_label, since_id, limit,
      before_id)`), `MemoryStore`, `KnowledgeSource`, `EventSink`, `MessageStore`,
      `CardStore`, `Completion`, `Clock`, `ToolExecutor` — as design §4.6.
- [ ] `memory.py`: in-memory implementations of every protocol.
- [ ] `app/hostadapters.py`: pure delegation to `chat.build_history`, `memory.py`,
      `knowledge.snapshot`, the SSE `emit`, `chat.post_message`, `drafts.create_draft` /
      `create_payment_draft`, `summarize.summarize_messages`, `clock.now_ict/today_ict`.
- [ ] Commit: `kernos: host adapter protocols, in-memory adapters, chiatienan adapters`

### Task 1.6: Today's behaviour as plugins

**Files:** create `backend/kernos/plugins/{context.py,prompt.py}` (host-agnostic);
create `backend/app/plugins/{prompt.py,run.py,render.py,validate.py,persist.py}`.

Each plugin is a **move** of an existing block, with its comments. Where today's code
reads `settings.*`, the plugin reads `config[...]`; the seeded profile supplies today's
values.

| Plugin id | Stage | Moved from | Note |
|---|---|---|---|
| `kernos.context.rollover` | context (first) | `_maybe_rollover` | via `Completion` + `MemoryStore` |
| `kernos.context.memory` | context | `memory.load_memory` | |
| `kernos.context.history` | context | `build_history(...)` | `bot_label` from `persona.handle` |
| `kernos.context.images` | context | `recent_images(...)` carry-over | only when `ctx.images` is empty |
| `kernos.prompt.sections` | prompt | `agent._render_prompt` | section headers from config |
| `app.prompt.phoenix` | prompt | `prompt.build_system_prompt` | code in Phase 1 (finding 8) |
| `kernos.model.passthrough` | model | — | the sidecar keeps routing text/vision |
| `app.run.legacy` | run | — | sets `tool_ctx.engine_spec` + overrides, then calls **`app.agent.run_turn` looked up at call time** |
| `app.render.lunch` | render | the `_settlement_body … _random_pick_body` chain, `_empty_turn_body`, and the *decision* between meal draft / payment draft / body | yields `Draft` or `Body(claimed_by_pack=…)` |
| `app.validate.fabricated_commit` | validate | `chat.py:645-656` | `block`; needs `_meal_exists` (app code) |
| `app.validate.unbacked_amounts` | validate | `chat.py:664-677` | `warn` |
| `app.persist.cards` | persist | `drafts.create_*`, `post_message`, superseded + cancelled collection | fills `persisted`, `superseded`, `pending_events` |

- [ ] Implement each with unit tests reusing `test_chat*.py` helpers (import, do not copy).
- [ ] Commit per group.

### Task 1.7: Seeded default profile and the resolver

**Files:** create `backend/kernos/content/{spec.py,resolve.py}`; create
`backend/app/default_profile.py`; tests.

- [ ] `spec.py`: `ProfileSpec` (pydantic) per design §5.1; `to_engine_spec(...)`.
- [ ] `resolve.py`: `Resolver` protocol; `StaticResolver(spec)`.
- [ ] `app/default_profile.py`: `build_default_spec(settings)` from `prompt.py`, the five
      `SKILL.md`, `money-safety.mdc` (tagged `money`), every `PI_*`/memory env value, and
      the Task 1.6 plugin list in today's order with today's values.
- [ ] Equivalence test: `to_engine_spec(build_default_spec(settings))` equals the
      `engine_spec=None` command from Task 1.4 (ids excluded, `today` frozen).
- [ ] Commit: `kernos: ProfileSpec, StaticResolver, chiatienan's seeded default profile`

**PR 1b ends here.**

### Task 1.8: `run_bot_turn` becomes the pipeline

**Files:** modify `backend/app/chat.py`; create `backend/app/kernel.py`.

- [ ] `app/kernel.py`: composition root — registry with the Task 1.6 plugins,
      `StaticResolver(build_default_spec(settings))`, `PiEngine`, adapters, `get_pipeline(spec)`.
- [ ] `chat.run_bot_turn(...)`: same signature; builds `TurnContext`, resolves, runs the
      pipeline under `_agent_lock`, flushes `pending_events` **after** the lock (as
      `chat.py:697-699` does today), returns `ctx.persisted`.
- [ ] Verify: `pytest -q` — every pre-existing test passes **unedited**;
      `git diff --name-only <base> -- backend/tests | grep -v 'tests/kernos/\|test_layering\|golden/turn_fixtures\|test_run_bot_turn_golden'`
      prints nothing. The Task 1.0 golden test passes against the pipeline.
- [ ] Verify: `node --test`; `npm test` untouched.
- [ ] Commit: `chat: run the @phoenix turn through the kernos pipeline (no behaviour change)`

### Task 1.9: The resolved-profile endpoint

- [ ] `GET /api/admin/rooms/{room_id}/resolved` (guarded by `require_admin`): `{spec,
      engine_spec, pipeline, trace_sample}`; test equals the Task 1.7 command.
- [ ] Commit: `admin: expose the resolved profile`

### Task 1.10: Docs

- [ ] README architecture + module table; `TODO.md` pointer. Record the Phase 1 benchmark
      run (commit hash of the results file, or "not run here").
- [ ] Commit: `docs: kernos in the README`

**PR 1c ends here.**

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
- **2.3** Publish gates 1, 2, 3, 5 (§9); `override_reason` in the audit log; the
  probe is a host-provided `ModelProbe` (chiatienan's wraps `bench.probe_models`,
  keeping `bench → app` out of `kernos`); `handles_money` is read from plugins and from
  the seeded profile's metadata until Phase 3 adds packs.
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
- **Proof:** `bench.regrade` of the stored `pi-typical-r3.json` records through the
  grader plugins yields identical verdicts (no model calls; deterministic).

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
