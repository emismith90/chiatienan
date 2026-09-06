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

**Phase 1 — state of play (2026-09-05):** Tasks 1.0–1.10 done on
`claude/headless-cms-pi-harness-nn18pb`, as three commit groups (1a `6a9ae46`, 1b
`d5e4f6f`, 1c from `919ac17`).

| check | result |
|---|---|
| backend `pytest -q` | 1057 passed, 1 skipped (986 + 1 before Phase 1; every pre-existing test unedited — `git diff origin/main -- backend/tests` lists only new files) |
| sidecar `node --test` | 69 / 69 (65 + 4 for `settings`/`extensions`) |
| golden fixtures (Task 1.0) | 9 / 9 replay byte-identical through the pipeline |
| `GET /api/admin/rooms/{id}/resolved` | `engine_spec` equals `agent.default_engine_spec()`; pipeline lists today's plugins in order |
| benchmark `bench.run --corpus typical --repeat 3` | **not run here** — no `OPEN_ROUTER_KEY` in this environment. Run where the key exists before merging and record the results commit hash here. Note the benchmark exercises `run_turn` (the engine move), not the pipeline; the golden fixtures cover the swap. |

Deviations from the task text, all behaviour-neutral: the runner does not execute
`resolve` (the pipeline is built *from* the resolved profile, so `Kernel.resolve`
runs before it); `kernos.context.images` takes no lookback config yet because
`chat.recent_images` still reads its window from env (Phase 2 moves it to the
profile's `memory` block); `HistorySource.render` accepts `bot_label` and
chiatienan's adapter ignores it until Phase 3, since `chat._render_messages` writes
the label itself today.

---

## Phase 2 — The content plane

**Goal:** profiles become data: sources an editor changes, versions that snapshot
them, a publish step with gates, a resolver that maps a space to what it runs, and
a mountable admin API — with every existing room still running the seeded default
byte for byte until someone binds it to something else.

**Gate:** a Fable review of this section before Task 2.1 — done 2026-09-05, verdict
**GO WITH CHANGES**; thirteen findings, all accepted and folded into the tasks below.

| # | Sev | Finding | Disposition |
|---|---|---|---|
| 1 | blocker | Boot creates no `kn_sources`, so the first human draft's per-kind snapshot would replace the seeded rules/skills with nothing | boot seeds sources from the skill/rule files; snapshot replaces per kind; test "draft from an untouched seeded profile equals the published spec" (2.2, 2.4) |
| 2 | blocker | `runtime.cwd/agent_dir` would be stored as content (boot layer, §0.3); per-environment values break re-sync and let an agent move `bash`'s cwd | `runtime` stripped from stored specs; the host injects it at resolve; excluded from re-sync equality (2.2, 2.4) |
| 3 | major | Gate 5 blacklist misses `extensions`, `settings` (raw Pi passthrough incl. `packages[]`, `shellPath`), `runtime`, `caps`; deleting `meta.handles_money` would bypass gate 2 | all added; a removed `meta.handles_money` counts as a blacklisted change (2.3) |
| 4 | major | `actor: "boot"` from the request body bypasses every gate | bypass is `bypass_gates=True` on `store.publish`, only `ensure_seeded` passes it; router rejects `boot*` actors, defaults to `admin` (2.2, 2.5) |
| 5 | major | `Kernel.pipeline_for` keyed by `id(spec)` can serve a stale pipeline after id reuse | key = sha256 of the pipeline dict; `Kernel.invalidate()` clears both caches (2.4) |
| 6 | major | `prompt.body`/`prompt.append` are inert while `app.prompt.phoenix` renders from code, so binding `append_sections` would do nothing | **new Task 2.7**: `kernos.prompt.template` with `{{var}}` and `{{#if}}…{{else}}…{{/if}}`; the seeded prompt becomes content, asserted equal to `build_system_prompt` for every sender case; `append` honoured |
| 7 | major | Rollback re-runs gate 3; after the seeded probes age out, the incident path needs a network probe | rollback skips gate 3 (the version passed when published); `override_reason` accepted (2.2) |
| 8 | major | `ProfileSpec` is mutable; in-place overrides would leak between spaces | `frozen=True` on every spec model; overrides via `model_copy(update=…)`; `BindingOverrides(extra="forbid")` (2.1, 2.4) |
| 9 | minor | `bench` is not in the prod image and imports `app` | `app/modelprobe.py` imports `bench.probe_models` inside the method; 501 on `ImportError` (2.5) |
| 10 | minor | `kn_businesses.default_*_id` ↔ `kn_agents.business_id` FK cycle; no way to pick the default business later; timestamp format | `kn_agents.is_default` (one per business, enforced in the store); `DbResolver(default_business_slug=…)`; UTC `isoformat(timespec="seconds")` (2.1, 2.4) |
| 11 | minor | Etag excludes `title`; `If-Match` mismatch should be 412; binding to a non-manager; no retire route | etag includes title; 412 / 409 / 422 as stated; `PUT /binding` 422 unless `role == manager`; `POST …/retire` (2.2, 2.5) |
| 12 | minor | Seeding happens on the first turn, not at boot | `main.py` startup hook calls `kernel_for(get_db())` (2.4) |
| 13 | — | Sizing | PR 2a = 2.1–2.3 (framework only), PR 2b = 2.4 + 2.7, PR 2c = 2.5–2.6 |

### Decisions taken for this phase (deviations from the design text, with reasons)

- **Space bindings are a framework table**, `kn_space_bindings(space_id, agent_id,
  overrides)`, not a column on the host's tenant row (design §5.1 said host-owned).
  A new host then needs no schema change to bind a space; chiatienan's `rooms`
  table stays untouched. `resolve(space_id)` reads the binding itself.
- **One `kn_sources` table** with a `kind` column (`prompt | rule | skill | template`)
  instead of four tables of identical shape. Same content model, less DDL.
- **Boot re-syncs the seeded profile.** The seeded business's default profile is
  `managed_by = "boot"`: on every start, if `build_default_spec()` differs from its
  published spec (env or code changed), boot publishes a new version with actor
  `boot`, gates bypassed. The moment a human publishes to that profile it becomes
  `managed_by = "human"` and boot leaves it alone. This is what keeps env the source
  of truth for an unedited install — the zero-behaviour-change promise across
  deploys, not just across this refactor.
- **Gate 3 (model probe) applies to model *changes*.** A publish whose `models` equal
  the currently published version's needs no fresh probe; one that changes them needs
  a catalogue row with `probe.ok` within `probe_max_age_days` (default 30). Boot seeds
  the catalogue with the two configured models and the 2026-08-12 probe results the
  Pi plan recorded, so day one is consistent.
- **Version status** is `draft | published | superseded | retired`. Publishing moves the
  previous `published` to `superseded`; `rollback(version)` republishes a superseded
  version (gates re-run). "The previous version stays publishable" (design §9) is
  therefore literal.
- **Actors are strings** (`boot`, `admin`, `agent:<slug>`), recorded on every version
  and audit row; there is no identity system (decided). `boot` is not acceptable from
  the API; gate bypass is a code-level flag only `ensure_seeded` passes.
- **`runtime` is never stored.** Stored specs omit `runtime`; the host injects its
  paths when resolving. Re-sync compares stored specs, so an environment's paths can
  never trigger a republish.
- **The seeded prompt becomes content in Phase 2** (Task 2.7), rendered by
  `kernos.prompt.template`, and is asserted equal to `build_system_prompt` for every
  sender case so the change is behaviour-neutral.

### Task 2.1: Content tables and `bind()`

**Files:** create `backend/kernos/content/{models.py,schema.py}`; tests `backend/tests/kernos/test_content_models.py`.

- [ ] `models.py`: its own `Base`; tables `kn_businesses (id, slug UNIQUE, name,
      description, tool_packs JSON, plugins_allowed JSON, seed JSON, created_at)`, `kn_profiles (id,
      business_id FK, name, managed_by, published_version_id, created_at)`,
      `kn_profile_versions (id, profile_id FK, version INT, status, spec JSON, actor,
      note, created_at, published_at; UNIQUE(profile_id, version))`, `kn_sources (id,
      business_id FK, kind, slug, title, body TEXT, frontmatter JSON, etag, updated_by,
      updated_at; UNIQUE(business_id, kind, slug))`, `kn_agents (id, business_id FK,
      slug, name, role, is_default BOOL, profile_id FK, delegates_to JSON, capabilities
      JSON, max_depth, created_at; UNIQUE(business_id, slug))` — one `is_default` per
      business, enforced in the store, `kn_space_bindings (space_id PK,
      agent_id FK, overrides JSON, updated_at)`, `kn_model_catalogue (id, provider,
      model_id UNIQUE, name, input JSON, context_window, max_tokens, cost JSON,
      reasoning, probe JSON, updated_at)`, `kn_audit_log (id, actor, action, entity,
      entity_id, before JSON, after JSON, at)`. Timestamps are UTC `isoformat(timespec="seconds")` strings the framework writes
      itself. Every spec model gets `frozen=True` (finding 8) and a `BindingOverrides`
      model (`append_sections`, `handle`, `language`; `extra="forbid"`) is added.
- [ ] `schema.py`: `bind(engine)` = `Base.metadata.create_all` + a generic
      `sync_additive_columns(engine, metadata)` (the pattern of `app.db`, parameterised
      by metadata; `app.db` keeps its own copy for now).
- [ ] Tests: bind on a fresh SQLite creates every table; a second bind is a no-op; a
      column added to a model appears on an existing table.
- [ ] Commit: `kernos: content tables and bind()`

### Task 2.2: `ContentStore` — sources, drafts, publish, rollback, audit

**Files:** create `backend/kernos/content/store.py`; tests `backend/tests/kernos/test_content_store.py`.

- [ ] `ContentStore(session_factory)`. Businesses: `create/get/list/update`. Sources:
      `put_source(business_id, kind, slug, *, title, body, frontmatter, actor,
      if_match=None)` — etag = sha256 of `(kind, slug, title, body, frontmatter)`; a mismatched
      `if_match` raises `PreconditionFailed` (HTTP 412); `delete_source(..., if_match)`; `list_sources(kind=)`.
- [ ] Profiles: `create_profile(business_id, name, *, managed_by="human")`,
      `get/list`. Versions: `create_draft(profile_id, *, actor, from_version=None,
      note=None) -> version`: the new spec is the previous published spec (or
      `from_version`'s, or the business's `seed["spec"]` when none) with the business's
      **sources snapshotted in**: `rules` ← kind `rule` (tags from frontmatter),
      `skills` ← kind `skill` (description/delivery from frontmatter), `templates` ←
      kind `template`, `prompt.body` ← kind `prompt` slug `system` when present. **Snapshot replaces per
      kind**, and the stored spec always omits `runtime` (finding 2).
      `update_draft(version_id, patch: dict, *, actor)` — deep-merges JSON into a
      **draft** only (anything else raises), re-validates as `ProfileSpec`.
- [ ] `publish(version_id, *, actor, override_reason=None, gates, bypass_gates=False)` —
      runs the gates (Task 2.3) unless `bypass_gates` (only `ensure_seeded` passes it); on success: status `published`, previous published → `superseded`,
      `profile.published_version_id` moves, `profile.managed_by = "human"` unless actor
      is `boot`, audit row with before/after version ids and the override reason.
      `rollback(profile_id, version_id, *, actor, gates, override_reason=None)` — same
      path for a `superseded` version, **skipping gate 3** (it passed when published). `retire(version_id)` for drafts/superseded.
- [ ] Audit: `log(actor, action, entity, entity_id, before, after)`; `audit(limit, since)`.
- [ ] Tests: etag conflict; draft snapshots sources and a later source edit does not
      change it; a draft from a seeded profile whose sources are untouched equals the
      published spec (finding 1); update_draft rejects a published version; publish flips statuses and
      writes audit; rollback republishes; `managed_by` flips to human on a human publish.
- [ ] Commit: `kernos: ContentStore with snapshot-on-publish and audit`

### Task 2.3: Publish gates

**Files:** create `backend/kernos/content/gates.py`; tests `backend/tests/kernos/test_gates.py`.

- [ ] `GateFailure(gate, message)`; `PublishGates(registry, catalogue, *, clock,
      probe_max_age_days=30, money_tools=frozenset({"bash","write","edit"}))` with
      `check(spec, *, previous, actor, override_reason) -> list[GateFailure]`:
      1. **schema** — `ProfileSpec` validates; `registry.build_pipeline` succeeds
         (every id@version known, every config valid, single-owner stages satisfied);
         `skills[].delivery == "discoverable"` requires `"read" in builtin_tools`.
      2. **money** — `handles_money = spec.meta.get("handles_money") or any plugin in the
         pipeline has handles_money`; with any of `money_tools` in `builtin_tools` and
         no `override_reason` → fail.
      3. **probe** — for each of `models.text`, `models.vision` that differs from
         `previous`: catalogue row exists with `probe.ok` and `probe.checked_at` within
         the max age, else fail.
      5. **reflexivity** — when `actor` starts with `agent:`: `blacklisted_changes(previous,
         spec)` non-empty → fail. Blacklist: `builtin_tools`, `models`, `tool_packs`,
         `pipeline`, `eval`, `extensions`, `settings`, `runtime`, `caps`, any
         `validation[]` entry with `on_fail == "block"`, any `rules[]` entry tagged
         `money`, and `meta.handles_money` (a removed key counts as a change).
      Gate 4 (eval) is a hook, `eval_gate: Callable | None`, wired in Phase 4.
      There is no actor-based bypass (finding 4); `store.publish(bypass_gates=True)`
      is the only way around the gates and only boot seeding uses it.
- [ ] Tests, one per gate plus the boot bypass and the "models unchanged needs no
      probe" case.
- [ ] Commit: `kernos: publish gates 1, 2, 3 and 5`

### Task 2.4: `DbResolver`, boot seeding, and the host wiring

**Files:** modify `backend/kernos/content/resolve.py`; create `backend/kernos/content/boot.py`;
modify `backend/app/kernel.py`, `backend/app/db.py`; tests `backend/tests/kernos/test_resolver.py`,
`backend/tests/test_boot_seed.py`.

- [ ] `DbResolver(store, *, default_business_slug, runtime: Runtime, fallback:
      ProfileSpec)`: `resolve(space_id)` → binding → agent → profile → published version
      → `ProfileSpec` with the host's `runtime` injected and binding overrides applied
      by `model_copy` (never in place; finding 8); no binding → the default business's
      `is_default` agent; no content → `fallback`. Cached by `(version_id, space_id)`;
      `invalidate()` on publish/bind, called by the store through a hook.
- [ ] `boot.py`: `ensure_seeded(store, *, business_slug, business_name, agent_slug,
      spec, sources, catalogue_rows)` — idempotent; creates business, **its sources
      from the skill/rule files (finding 1)**, profile (`managed_by="boot"`), version 1
      published with `bypass_gates=True`, the `is_default` manager agent; on later runs
      re-puts changed sources and republishes only when `managed_by == "boot"` and the
      stored spec (without `runtime`) differs; upserts the catalogue rows.
- [ ] `app/db.py`: `Database.create_all()` also calls `kernos.content.bind(engine)`.
      `app/kernel.py`: `Kernel` builds the store, runs `ensure_seeded` with
      `build_default_spec(settings)` and the two configured models' 2026-08-12 probe
      records, and uses `DbResolver` with the static spec as fallback; `Kernel.resolve`
      accepts a `space_id` string too (rooms are `str(room_id)`); `pipeline_for` is
      keyed by a hash of the pipeline dict and `Kernel.invalidate()` clears both caches
      (finding 5); `main.py` gains a startup hook that builds the kernel (finding 12).
- [ ] Verify: the nine golden fixtures still replay byte-identical (resolved from the
      DB now); full suite green; `GET …/resolved` unchanged.
- [ ] Commit: `kernos: DbResolver and boot seeding; chiatienan resolves from the content plane`

### Task 2.5: The admin router

**Files:** create `backend/kernos/api/{__init__.py,admin.py}`; modify `backend/app/main.py`;
tests `backend/tests/test_admin_api.py`.

- [ ] `admin_router(get_kernel, *, dependencies=())` → `fastapi.APIRouter`. Routes:
      `GET /registry`, `GET /plugins/{id}/{version}/schema`; `GET|POST /businesses`,
      `GET|PATCH /businesses/{id}`; `GET|POST /businesses/{id}/sources`,
      `GET|PUT|DELETE /businesses/{id}/sources/{kind}/{slug}` (`If-Match` on PUT/DELETE →
      412 on mismatch, `ETag` on GET); `GET|POST /profiles`, `GET /profiles/{id}`,
      `GET|POST /profiles/{id}/versions`, `GET|PATCH /profiles/{id}/versions/{v}`,
      `POST /profiles/{id}/versions/{v}/publish` (`{actor?, override_reason?}`; actor
      defaults to `admin`, `boot*` rejected with 422; gate failures → 422 with the list;
      state conflicts → 409), `POST /profiles/{id}/versions/{v}/retire`,
      `POST /profiles/{id}/rollback`; `GET|POST /agents`,
      `GET|PATCH /agents/{id}`; `GET|PUT|DELETE /spaces/{space_id}/binding` (422 unless the agent's `role == manager`;
      overrides validated as `BindingOverrides`);
      `GET /spaces/{space_id}/resolved`; `GET /catalogue/models`,
      `POST /catalogue/models/{model_id}/probe` (runs the host `ModelProbe` if
      configured, else 501); `GET /audit`.
- [ ] chiatienan: mount under `/api/admin` behind `require_admin`; keep
      `GET /api/admin/rooms/{id}/resolved` as an alias of the spaces route; provide
      `app/modelprobe.py`, a `ModelProbe` that imports `bench.probe_models` inside the
      method and reports 501 on `ImportError` (finding 9; `bench` is not in the image).
- [ ] Tests: every route with a `TestClient`; an edited source → draft → publish →
      bound room runs the edit (assert through `GET …/resolved` **and** a golden-style
      `run_bot_turn` with a fake engine seeing the new `skills`); an unbound room still
      resolves to the seeded default; publish refused on each gate with the failure
      list in the body.
- [ ] Commit: `kernos: mountable admin router; chiatienan mounts it`

### Task 2.7: The prompt becomes content — `kernos.prompt.template`

**Files:** create `backend/kernos/plugins/template.py`; modify `backend/app/default_profile.py`,
`backend/app/kernel.py`; tests `backend/tests/kernos/test_template.py`, `backend/tests/test_prompt_content.py`.

- [ ] A deliberately tiny renderer: `{{path.to.var}}` substitution and
      `{{#if path}}…{{else}}…{{/if}}` (nestable, truthiness = non-empty / not None). No
      loops, no expressions, no I/O. Unknown variables fail at render **and** at
      publish (gate 1 validates the body against the closed variable set).
- [ ] Variables: `persona.handle|name|aliases|language`, `sender.name`,
      `sender.member_id`, `today` (from the host `Clock`), `space.id`. The closed set
      is documented in the plugin's `config_schema` description.
- [ ] Plugin `kernos.prompt.template@1`, stage `prompt`: `ctx.system = render(body) +
      "\n\n".join(prompt.append)`.
- [ ] `app/default_profile.py`: `prompt.body` = today's `build_system_prompt` text
      turned into a template with the `{{#if sender.name}}` / `{{#if sender.member_id}}`
      blocks; the seeded pipeline's `prompt` stage becomes `[kernos.prompt.template,
      kernos.prompt.sections]`. `app.prompt.phoenix` stays registered for profiles that
      name it.
- [ ] **Equivalence test:** for every combination of `sender_name ∈ {None, "An"}` and
      `sender_id ∈ {None, 7}` with a frozen `today`, the rendered template equals
      `build_system_prompt(...)` exactly. The golden fixtures and the seam test keep
      passing; `GET …/resolved` now shows the prompt as content.
- [ ] Commit: `kernos: prompt template plugin; the seeded prompt becomes content`

### Task 2.6: Docs and state of play

- [ ] README: admin API summary; plan state of play; design §5.1 updated for the
      decisions above.
- [ ] Commit: `docs: Phase 2 state of play`

**Phase 2 — state of play (2026-09-05):** Tasks 2.1–2.7 done, as three commit groups
(2a `3596c1f` framework only; 2b `f3b0a2b` prompt-as-content, resolver, boot, wiring;
2c from the commit after). 

| check | result |
|---|---|
| backend `pytest -q` | 1094 passed, 1 skipped; every pre-existing test unedited |
| golden fixtures | 9 / 9 replay byte-identical with the profile resolved from the database |
| prompt as content | the template renders equal to the pre-change `build_system_prompt` for every sender case, from code and through `kernos.prompt.template` (`tests/legacy_prompt.py` is the oracle) |
| resolved spec | `== build_default_spec(settings)`; engine half `== agent.default_engine_spec()` |
| admin API | end-to-end: source edit → draft → publish → bind → the bound room's turn hands the engine the edit; the unbound room does not |
| gates | each refused with its failure list; a human publish to the seeded profile needs an `override_reason` because today's env enables `bash` on a money profile — by design |

Two things found while implementing, both fixed before commit: `create_draft`
ignored an explicit `base_spec` once a version was published (boot re-sync would
never have seen a changed env), and registry lookup errors were not mapped to HTTP
statuses. One consequence to know: with `PI_BUILTIN_TOOLS=read,write,bash` (today's
default) every human publish of the lunch profile requires a reason; flipping the
default to `[]` for new profiles is decision 5 in the design and is still open.

## Phase 3 — Packs and the ledger domain

**Goal:** a second business can exist. That needs (a) a `ToolPack` interface the kernel
runs through, (b) the money domain both lunch and poker share extracted into
`ledger_core`, (c) the lunch business as a real pack, and (d) the three places the host
still hard-codes lunch — balances, draft commits, the render chain — generalised.
Every step is behaviour-neutral for lunch; the golden fixtures and the full suite
(with no pre-existing test edited) are the oracle, the benchmark where a key exists.

**Gate:** a Fable review of this section before Task 3.1 — done 2026-09-05, verdict
**GO WITH CHANGES**; eight findings, all verified and accepted:

| # | Sev | Finding | Disposition |
|---|---|---|---|
| F1 | high | Switching the seeded pipeline to `kernos.render.packs` in 3a edits three id-pinning tests and makes the golden prove a path prod (human-published) would not run | 3a keeps `app.render.lunch` / `app.persist.cards` as the seeded ids and makes them one-line delegates to the kernel plugins, so both ids run one code path; the ids switch in 3d together with the (branch-added, not pre-existing) pin tests |
| F2 | high | `debug_api.dumpable_tables()` enumerates `app.models.Base.metadata` only — moving the ledger tables would drop `meals.csv` from the prod export API | 3.2: union of the app, `ledger_core` and `kn_` metadata; a test asserts `meals` is listed |
| F3 | high | `record_meal`/`record_payment` query the host `Member` to raise "does not exist"; `Meal.place_id → places.id` is also a host FK | `ledger_core` gets a `MemberDirectory` protocol (`ids_in_space`, `names`) bound by the host shim; `place_id` joins the dropped-FK list |
| F4 | medium | `contributions(session, space_id, window)` invites re-windowing edges before FIFO — the exact bug `debt_breakdown` fixed | `contributions(session, space_id)` returns **all** edges; the core runs FIFO then windows by `occurred_on`, as today |
| F5 | medium | `DebtEdge.meal_id`/`dish` are on the wire (statement bodies, `main.py` filters, frontend cards) | rename on the dataclass only, with `meal_id`/`dish` kept as read-only properties; every serialiser keeps emitting them |
| F6 | medium | `app/packs/lunch.py` importing `bench.world` pulls `bench` into the app graph and the prod image | fixtures live in `app/packs/lunch_fixtures.py` from 3a and `bench.world` imports them; `bench` joins the layering test with `app → bench` forbidden except the documented lazy import in `app/modelprobe.py` |
| F7 | low | Manifest identity: order is pinned by `test_tools_manifest.py` and the sidecar fixture; the literal order interleaves money and places tools | `build_tools` composes then reorders to `LEGACY_ORDER`; a test asserts the order; `run_turn` calls `tool_manifest(ctx)` so the per-turn filter reaches the model |
| F8 | low | `PackTool.execute` must stay sync; `models()` binds per pack; empty-turn strings are host text; draft stamping belongs to the kernel | execute sync; `Database.create_all` calls each pack's `bind`; the three empty-turn strings are `PackRender` config with today's text as defaults; `PackRender` stamps `raw_input`/`logged_by`/`turn_id` after the pack returns a `Draft` |

Also confirmed by the gate: `propose_meal`'s place guess is the money pack's **only** places
dependency, so 3c injects a `PlaceResolver` through `ToolContext` and the two-pack split
holds; `period_balances` has no app callers (test-only), so reimplementing it over
contributions is safe.

### Facts that shape this phase (from the code, 2026-09-05)

- Tests import `app.models` (28 files), `app.tools` (17), `app.money` (6), `app.periods`,
  `app.qr`, `app.places`… Every module that moves keeps an `app.*` shim that re-exports
  the same names, or the "no pre-existing test edited" rule fails.
- `ledger.py` derives balances from **meals** specifically (`period_balances`,
  `debt_breakdown`, `period_transfer_inputs`) and `DebtEdge` carries `meal_id` and
  `dish`. Payments may be **linked to a meal** (`Payment.meal_id`) and FIFO application
  uses that link.
- `drafts.py` is two things glued together: draft **payload** logic (normalise items,
  signature for supersede, `_EDITABLE`, commit → `ledger.record_meal`) and draft
  **persistence** as `RoomMessage` rows of kind `expense_draft`/`payment_draft` via
  `chat.post_message`. The first is domain; the second is the host's `CardStore`.
- `qr.py` reads `settings.qr_base_url/qr_template`; `ledger.py` reads `app.clock`;
  `seed_places.py` reads `app.db.get_db`. None of that may survive in `ledger_core`.
- **SQLAlchemy foreign keys cannot cross declarative bases.** `Meal.room_id → rooms.id`
  and `MealShare.member_id → members.id` are FKs into host tables. If the ledger tables
  move to a `ledger_core` base they either lose those constraints (plain indexed
  integers, as `kn_*` already does with `space_id`) or the models are composed with
  declarative mixins on the host's base. Existing production tables keep whatever
  constraints they were created with either way; only fresh installs differ.
- Places / observations / memos / knowledge (~1,300 lines) are lunch **domain** but are
  also the host's knowledge panel and its memory files. They are not money.

### Decisions taken for this phase

1. **Four PRs, each behaviour-neutral:** 3a pack interface with a thin lunch wrapper
   living in `app`; 3b `ledger_core` extraction with shims; 3c the real
   `packs/lunch_ledger`; 3d generalised balances, draft kinds and render.
2. **Ledger tables move to `ledger_core.models` on their own base; cross-package
   references become plain indexed integers** (`room_id`, member ids, `place_id`), validated in
   code where it matters (`roster` already does). Same table names, same columns, no
   data migration. Rationale: the `kn_*` tables set the precedent, mixins would force
   every host to compose the models, and a poker pack needs the same freedom for
   `games`/`game_entries`. Stated cost: a fresh install has no DB-level FK from
   `meals` to `rooms`/`members`. `Member`, `Room`, `Session`, `RoomMessage`, `Place`
   stay host models.
3. **`DebtEdge` generalises to `(debtor, creditor, ref_kind, ref_id, label, occurred_on,
   amount)`**; `meal_id`/`dish` become `ref_kind="meal"`/`label`. `Payment.meal_id`
   becomes the generic link `(ref_kind, ref_id)` **in Python only** — the column keeps
   its name (`meal_id`) and a `ref_kind` column is added additively with default
   `"meal"`, so existing rows are unchanged.
4. **Contributions, not balances, are the pack interface:** `pack.contributions(session,
   space_id) -> list[DebtEdge]` — every edge, unwindowed (F4). The core sums edges from every enabled pack,
   applies payments FIFO as today, and derives statements, outstanding pairs and
   transfers from that one list. `lunch_ledger.contributions` is today's
   `build_debt_edges(period_transfer_inputs(...))`, so the numbers cannot move.
5. **Two lunch packs.** `packs/lunch_ledger` (money: meals, payments, settlement, member
   CRUD) and `app/packs/places.py` (find_places, suggest_lunch, remember, forget,
   add_place) — the second stays in the host until Phase 5 gives knowledge a
   framework home, because it is welded to the host's memory files and panel.
6. **Tool selection is content, tool bodies are code.** `profile.tool_packs[]` lists
   enabled packs with per-tool `{enabled, description}`; `app.tools.build_tools` becomes
   the composition point that asks the enabled packs and applies the overrides. The
   filter reaches it through `ToolContext.tool_config` (same seam as `engine_spec`).
   `tool_manifest()` follows the same filter so the model is told exactly what it may
   call.
7. **`bench.world` fixture steps become `pack.fixtures()`**, keyed by today's step
   kinds (`add_member`, `meal_confirmed`, `leave_pending`, `confirm_pending`,
   `payment`); `bench.world.build_world` dispatches to them, so Phase 4 inherits a
   pack-provided world builder without a second implementation.

### Task 3.1 (PR 3a): the `ToolPack` interface and a thin lunch wrapper in the host

**Files:** create `backend/kernos/packs.py`, `backend/kernos/plugins/render.py`,
`backend/kernos/plugins/persist.py`; create `backend/app/packs/{__init__.py,lunch.py,places.py}`;
modify `backend/app/tools.py` (filter), `backend/app/default_profile.py`, `backend/app/kernel.py`;
tests `backend/tests/kernos/test_packs.py`, `backend/tests/test_app_packs.py`.

- [ ] `kernos/packs.py`: `ToolPack` protocol — `id`, `version`, `handles_money`,
      `tools(ctx) -> dict[str, PackTool]` (`PackTool(name, description, schema, execute)`),
      `draft_kinds() -> dict[str, DraftKind]` (`DraftKind(kind, commit(session, space_id,
      payload, logged_by) -> Any, editable: frozenset[str])`), `render(result) -> Outcome |
      None`, `contributions(session, space_id, window) -> list`, `fixtures() ->
      dict[str, Callable]`, `seed(session, space_id)`, `models() -> list[type]`. Plus a
      `PackRegistry` (`register`, `get(id)`, `describe()`), the `apply_tool_overrides(tools,
      spec.tool_packs)` helper, and `packs_for(spec, registry)` in profile order.
- [ ] `kernos.plugins.render.PackRender` (`kernos.render.packs@1`, stage `render`): asks
      each enabled pack in profile order; the first `Outcome` wins; a `Draft` is stamped
      with `raw_input`/`logged_by`/`turn_id` by the kernel, never by the pack (F8d); none
      → `Body(final_text or empty-turn body, None, claimed_by_pack = not final_text)`. The
      three empty-turn strings are plugin config with today's English as defaults (F8c).
- [ ] `kernos.plugins.persist.Cards` (`kernos.persist.cards@1`): today's
      `app.persist.cards` over `CardStore`/`MessageStore` — it is already host-agnostic;
      the cancelled-card republish reads `result.all_results("cancel_draft")` through the
      pack's declared `cancel_tool` name rather than a literal.
- [ ] `app/packs/lunch.py`: `LunchLedgerPack` **wrapping today's modules** — `tools()`
      from `app.tools.build_tools` (money tools only), `draft_kinds()` from `app.drafts`
      (`expense_draft`, `payment_draft`), `render()` = today's `app.plugins.render.LunchRender`
      decision, `fixtures()` from `app/packs/lunch_fixtures.py` — the five `bench.world` step
      kinds re-homed there, and `bench.world` imports them back (F6) — `handles_money=True`. `app/packs/places.py`: the five
      places tools + `seed_places`.
- [ ] `app/tools.py`: `ToolContext.tool_config: dict | None = None`; `build_tools(ctx)`
      composes from the kernel's pack registry when the seam is set and applies
      enable/description overrides; `tool_manifest(ctx=None)` follows. With the seam
      unset (the 18 fakes, the bench) it returns exactly today's 19 tools **in today's
      order** (`LEGACY_ORDER`, asserted; F7); `run_turn` calls `tool_manifest(ctx)`.
- [ ] Seeded profile: `tool_packs = [{"pack": "lunch_ledger"}, {"pack": "lunch_places"}]`;
      the render/persist ids stay `app.render.lunch` / `app.persist.cards`, now one-line
      delegates to `PackRender` / the kernel `Cards` (F1). The kernel ids are registered
      too; the seeded pipeline switches to them in 3d.
- [ ] Proof: golden 9/9; a test profile with `pick_random` disabled has no `pick_random`
      in the manifest the engine receives and the tool is refused if called; full suite
      unedited.
- [ ] Commit: `kernos: ToolPack interface, pack render/persist plugins, lunch as a wrapped pack`

### Task 3.2 (PR 3b): extract `ledger_core`

**Files:** create `backend/ledger_core/{__init__.py,models.py,schema.py,money.py,periods.py,
qr.py,roster.py,ledger.py,drafts.py,moneyguard.py,notes.py}`; modify the corresponding
`backend/app/*.py` into re-export shims; modify `backend/app/db.py`, `backend/app/models.py`.

- [ ] Move `money.py`, `periods.py`, `moneyguard.py`, `notes.py` verbatim (pure).
- [ ] `qr.py`: `make_qr_url(member, amount, *, base_url, template)`; the `app.qr` shim
      binds `settings`. `roster.py`: takes the `Member` model as a module-level
      `configure(member_model=...)` or, simpler, `ledger_core.models` **owns `Member`
      too**? — **No**: `Member` carries auth (`pin`, sessions) and stays host; `roster`
      functions take the member class as a parameter with the shim binding it.
- [ ] `ledger_core/models.py`: `Base`, `Meal`, `MealShare`, `Payment` (+ additive
      `ref_kind` default `"meal"`), `Settlement`; `room_id`/member ids as indexed
      `Integer` (decision 2). `ledger_core.bind(engine)` = create + additive sync
      (reuse `kernos.content.schema.sync_additive_columns`). `app/models.py` re-exports
      the four classes; `Database.create_all` calls `ledger_core.bind`.
- [ ] `ledger_core/ledger.py`: today's `ledger.py` with `clock` injected (`Clock`
      protocol from kernos), a `MemberDirectory` protocol for the existence checks (F3),
      and `DebtEdge` generalised with `meal_id`/`dish` kept as properties (F5); `record_meal`,
      `record_payment`, `void_meal`, `period_*`, `debt_breakdown`, `statement_for`,
      `outstanding_pairs`, `period_timeline`, `record_settlement`, `last_settlement`.
- [ ] `ledger_core/drafts.py`: the **payload half** — `sync_items`, `signature`,
      `EDITABLE`, `commit_meal_payload(session, space_id, payload, logged_by)`,
      `commit_payment_payload(...)`, `recommit(...)`. The `RoomMessage` half stays in
      `app/drafts.py`, which now calls into it.
- [ ] `app/debug_api.dumpable_tables()` unions the app, `ledger_core` and `kn_` metadata;
      test asserts `meals` is listed (F2).
- [ ] Proof: full suite unedited; `test_layering` green (`ledger_core → kernos` only);
      the benchmark where a key exists.
- [ ] Commit: `ledger_core: the money domain, extracted behind app.* shims`

### Task 3.3 (PR 3c): the real `packs/lunch_ledger` — done

- [x] Money tools out of `app/tools.py` into `packs/lunch_ledger/tools.py` (imports
      `kernos` and `ledger_core` only), the render decision **and the deterministic
      bodies** into `packs/lunch_ledger/render.py`, the fixtures into
      `packs/lunch_ledger/fixtures.py`; `app/packs/lunch.py` is the registration
      (`LunchLedgerPack(qr=app.qr.make_qr_url, place_resolver=resolve_place)`).
      `app/tools.py` keeps `ToolContext`, `CustomTool`, `build_tools`, `tool_manifest`
      and a `_legacy_build_tools` that composes the host's packs in `LEGACY_ORDER`.
- [x] `bench.world` takes the fixtures from `lunch_ledger_pack().fixtures()` and runs
      them through a `_World` (members and cards are the host's tables);
      `bench.probe_models._tool_schemas` was already on the composition point.
- [x] Proof: full suite unedited (1111 passed, 1 skipped; sidecar 69/69); golden 9/9;
      layering green with `packs/` now present; `tests/test_lunch_ledger_pack.py` runs
      the pack against a stub host (in-memory card store, stub QR, no place resolver,
      its own clock and draw) with no `app` import on the pack side.
- [x] Commit: `packs: lunch_ledger owns the money tools, render and fixtures`

**Deviations from the task text, and why:**

1. **Member CRUD is a host pack, `room_members`** (`app/packs/members.py`), not part of
   `lunch_ledger`. `add_member`/`update_member`/`delete_member` create sign-in accounts
   (`app.accounts`: PIN, sessions, aliases) — the host's Principal, per design §3 — so
   they cannot live under `packs/`. Any ledger business on this host (poker too)
   enables `room_members` next to its own pack; the seeded profile's `tool_packs` is now
   `[lunch_ledger, room_members, lunch_places]` and boot re-syncs it. Tool order and
   the manifest are unchanged (`LEGACY_ORDER` still governs).
2. **What the pack needs from a host is an explicit, duck-typed contract**, documented
   at the top of `packs/lunch_ledger/tools.py`: on the per-turn context `db.session()`,
   `space_id`, `sender_member_id`, `turn_mentions`, `unknown_names`, `cards`
   (`kernos.adapters.CardStore`), `today()`, `choice()`; at registration `qr(payee,
   amount, note)` and an optional `place_resolver(session, space_id, text) → (place,
   confident)`. `ToolContext` grew `cards`/`today`/`choice` (filled by `app.tools._inject`
   so the pre-existing tests that patch `app.tools.today_ict` and `app.tools.random.choice`
   still steer the tools) and a `space_id` property over `room_id`.
3. **`CardStore` gained `pending(space_id)` and `cancel(space_id, card_id)`** — the two
   draft-store operations `settle_period` and `cancel_draft` need; `RoomCards` delegates to
   `app.drafts`, `InMemoryCards` implements them for tests. `cancel` raises `ValueError`
   (a `LedgerError` is one) and the pack turns it into a clarifying question.
4. **Fixtures build through a `world`** (`space_id`, `session()`, `add_member`,
   `create_card`, `commit_card`) rather than the host's `Member`/`drafts` modules —
   the only way `fixtures()` can live in `packs/`. `bench.world._World` is the host's.
5. **The reply bodies moved with the decision** (`render_bot_attachments`,
   `_settlement_body`, `_settle_blocked_body`, `_statement_body`, `_summary_body`,
   `_random_pick_body`): `decide()` calls them, and a pack under `packs/` cannot import
   `app.chat`. `app.chat` re-exports them for `tests/test_chat_bodies.py` and
   `bench.graders`. This is the first half of 3.4's "chat.py loses the last lunch
   literals"; `_meal_body`, `_payment_body` and `_FABRICATED_COMMIT_BODY` stay for 3d
   (they belong to the commit routes and the validator, not to `render`).
6. **`DraftKind.commit` now points at `ledger_core.drafts.record_meal_payload` and a
   `record_payment_transfers` wrapper** — the domain half of a commit, with the
   documented `(session, space_id, payload, *, logged_by)` shape — instead of the host's
   `app.drafts.commit_draft`. Nothing calls `DraftKind.commit` yet; 3.4's `commit_any`
   dispatch will (card load and status flip stay host-side).
7. `kernos.packs.err(message)` is the `{"ok": False, "error": …}` convention as a
   function, shared by the three packs.

### Task 3.4 (PR 3d): generalise what the host still hard-codes — done

- [x] `ledger_core.ledger.debt_breakdown` (and so `period_balances`, `period_transfers`,
      `statement_for`, `outstanding_pairs`) takes its edges from the registered
      **edge sources** — `Σ pack.contributions(session, space_id)`, unwindowed — applies
      payments FIFO over that one list and windows afterwards (F4). `ledger.meal_edges`
      is the meals' half, `LunchLedgerPack.contributions` returns exactly it, and
      `Kernel.register_packs` hands every pack's `contributions` to
      `ledger_core.configure(edge_sources=…)`. With none registered the ledger reads its
      own meals. `tests/test_ledger_contributions.py` asserts `debt_breakdown` equality
      over the golden week (inline computation = default = via the pack) and that a
      second source's edge changes balances and transfers, and is windowed with the rest.
- [x] `app/drafts` is generic over `DraftKind`: `create_card` / `list_pending_drafts` /
      `update_draft` / `commit_any` dispatch on the kinds the kernel registered
      (`set_draft_kinds`); `create_draft`, `create_payment_draft`, `commit_draft`,
      `commit_payment_draft` are thin wrappers on the one path with today's error
      strings; the commit/recommit routes in `main.py` are unchanged. `DraftKind` grew
      `card` (the confirmation the room sees, built from the commit result),
      `prepare` (payload normalisation on create and edit — lunch's `sync_items`) and
      `signature` (re-proposal identity for superseding; `None` = never). `RoomCards.create`
      is one line.
- [x] The seeded pipeline runs `kernos.render.packs` / `kernos.persist.cards`; the three
      id-pin tests added on this branch are updated (F1) and the `app.render.lunch` /
      `app.persist.cards` delegates are deleted (branch-added, never deployed).
- [x] `chat.py` lost the last lunch literals: `_meal_body` and `_payment_body` joined the
      other bodies in `packs/lunch_ledger/render.py` (re-exported for the pre-existing
      tests and `bench.graders`); `_meal_exists` and `_FABRICATED_COMMIT_BODY` moved to
      `app/plugins/validate.py`, their only user. `chat.py` keeps messages, history,
      images, `run_bot_turn`, rollover and the generic `_empty_turn_body`.
- [x] Proof: golden 9/9; full suite unedited (1116 passed, 1 skipped; sidecar 69/69);
      `tests/test_stub_pack.py`: a pack with two tools and one draft kind, registered on
      the kernel and enabled by a published profile, runs a turn end to end — the engine
      is offered only its tools, its `Draft` becomes a card of its own kind through the
      kernel's persist stage and the generic store, and `commit_any` commits it with the
      stub's `commit`/`card`.
- [x] Commit: `kernos/ledger_core: balances from pack contributions; drafts and render by pack`

**Deviations:** edge sources and draft kinds are registered *per kernel*, not per
space — the data is space-scoped, so summing every registered pack's edges over a
space equals summing the enabled ones', and a kind a space's profile does not enable
never produces a card there. Per-space enablement can arrive with the poker business
(Phase 6) if a host ever runs two money packs over one table. `recommit_draft` (edit a
committed meal: void + re-record, repoint payments) stays expense-specific in
`app/drafts.py`; it reaches the pack only for the card.

### Task 3.5: Docs and state of play

- [x] Design §4.2/§7.3 updated with the interface as built and decision 2's FK note;
      README module table; plan state of play.

**Phase 3 — state of play (2026-09-06):** Tasks 3.1–3.5 done, as four commit groups
(9fc901e 3a, d2f944e 3b, d3efbb2 3c, 3d). `backend/ledger_core` is the money domain
(models on their own `Base`, FIFO edges, netting, periods, VietQR, drafts payloads,
member directory, clock, edge sources); `backend/packs/lunch_ledger` is the lunch
business as a pack importing `kernos` and `ledger_core` only (11 tools, render decision
and bodies, two draft kinds with commit/card/prepare/signature, contributions, fixtures
over a world); `app/packs` holds the host's registration and the two host packs
(`lunch_places`, `room_members`). `app/tools.py` is the composition point; `app/drafts.py`
and `app/hostadapters.RoomCards` know no business; the seeded pipeline runs the kernel's
render and persist plugins. Baseline 986 → 1116 backend tests (+1 skipped), sidecar 69;
golden 9/9; `test_layering` covers `kernos`, `ledger_core`, `packs`, `app`, `bench`.
Benchmark: not run here (no `OPEN_ROUTER_KEY`). Phase 3 gate findings F1–F8 all landed
(F5's `DebtEdge` rename left as-is: `meal_id`/`dish` are fields, on the wire unchanged).

## Phase 4 — Observe and eval

### Facts that shape this phase (from the code, 2026-09-06)

- `TurnContext.trace` already records every plugin run (stage, id, version, ms,
  outcome, reason/error) and `Stage.after` is in `PIPELINE_ORDER`, but nothing writes
  the trace anywhere, and a plugin that raises stops the pipeline **before** `after`.
  `TurnResult` carries `tools`, `final_text`, `error`, `capped`, `turn_id`, `stats`
  (tokens/cost from the sidecar). The one-line `[agent] turn … done` log is the only
  observability today.
- `bench/` is a complete eval harness: `corpus.Case` (id, source, day, actor, members,
  prior_steps, message, history, images, had_images, expect), loaders for
  `meals`/`week`/`bills`/`prod`/`typical` (goldens imported from `tests/golden`),
  `graders` — `grade_tool_selection(case, record)` (generic: tool calls vs
  `expect.tools`/`expect.args`, money args compared, `passed` tri-state),
  `grade_ledger_state(case, record, db, ids)` (ledger-specific: balances, shares,
  transfers, QR, blocked), `grade_prose(case, record, judge)` (unbacked amounts via
  `moneyguard`, then an injected judge), `summarize_cost_latency` — `judge`
  (OpenRouter judge + the agent-as-judge file exchange), `world.build_world` (Phase 3:
  through the pack's fixtures), `run` (record schema `RECORD_VERSION = 1`, one fresh DB
  and world per case, graded while the world is alive), `regrade` (re-grades
  `tool_selection` from a stored results file, no model calls), `report`.
  `bench/results/pi-typical-r3.json` holds 111 graded records.
- Layering: `bench → everything`, `app → bench` forbidden except `app/modelprobe.py`
  (lazy). `kernos` imports nothing else. So a grader that lives in `kernos` cannot
  import `bench`, and the lunch-specific grading (`ledger_state`) cannot live in
  `kernos` at all.
- `PublishGates` has the gate-4 hook (`eval_gate: Callable[[ProfileSpec], list[GateFailure]]`)
  and `ProfileSpec.eval = {suites: [], gate: {}}` — both unused. Gate 5 already blacklists
  `eval` for agent actors.
- The content store is `ContentStore` over `kn_` tables with `on_change`, audit log and
  the admin router; adding tables follows `kernos/content/models.py` + `schema.bind`.

### Decisions taken for this phase

1. **Traces are the log.** `kernos.after.trace` writes `ctx.trace` plus a summary
   (space, principal, profile version, model, tools called, tokens, cost, elapsed,
   `capped`, error, outcome kind, validator verdicts) to `kn_turn_traces` through a
   `TraceStore` adapter (host: the content DB). `Pipeline.run` executes the `after`
   stage in a `finally` so a turn that raised is traced with its error; an `after`
   plugin that raises is logged, never re-raised (observability must not break the
   turn). Retention is a plugin config (`keep_days`, default 30; pruned on write).
2. **Grading is a plugin, graders live where their knowledge lives.** `kernos.eval`
   defines `EvalCase` (the `bench.corpus.Case` fields, JSON-serialisable; `Case` stays
   as the bench's own dataclass with `to_eval_case()`), the record shape (`RECORD_VERSION`
   preserved), `Grader` protocol `grade(case, record, world) -> Verdict(passed: bool|None,
   reason)`, `GraderRegistry`, `Judge` protocol `(case, record, rubric) -> dict`. The
   **generic** graders move into `kernos.eval.graders`: `tool_selection` (the compared
   arg names, the unordered/multiset/sender-defaulted lists become **config** with
   lunch's `MONEY_ARGS` as the seeded values) and `prose` (an injected
   `unbacked_amounts` checker and judge). `ledger_state` moves to
   `packs/lunch_ledger/eval.py` with `compare_settlement`, `balances_by_member`,
   `_draft_delta`, `_compare_draft`; `PROSE_RUBRIC` becomes the pack's seeded rubric.
   `bench.graders` becomes a shim (`grade_* = ` the plugin bodies with today's
   signatures) so `bench.run`, `bench.regrade`, `bench.report` and the pre-existing
   bench tests are unchanged. Proof of identity: `bench.regrade` over the stored
   `pi-typical-r3.json` changes **0** verdicts.
3. **Eval content = four tables** as §5.5: `kn_eval_cases` (business, slug, case JSON =
   `EvalCase.to_dict()`, tags, source `manual|imported|captured`, review bool),
   `kn_eval_suites` (business, slug, case_slugs, graders `[{plugin, config}]`, judge
   `{model, rubric}`), `kn_rubrics` (business, slug, body), `kn_eval_runs` (suite, profile
   version, spec sha256, started/finished, status, records JSON, summary JSON). Store
   CRUD + admin routes under `/businesses/{id}/eval/*` and
   `/profiles/{id}/versions/{v}/eval` (run) / `/eval/runs/{id}` (read).
4. **The runner is kernel orchestration over host services.** `kernos.eval.Runner(
   graders, world_factory, run_turn, judge=None)`: for each case, `world_factory(case)
   → world` (space id, key→id map, `db`, `close()`), the turn through `run_turn(case,
   world, spec)` (the host runs its pipeline against the **candidate** spec via a
   `StaticResolver`), grade while the world is alive, summarise (pass rates per grader
   over `passed is not None`, cost/latency), persist as an `EvalRun`. chiatienan's
   `app/evalhost.py` provides `world_factory` (bench.world through the pack fixtures)
   and `run_turn` (a fresh `Database`, `chat.run_bot_turn` with the candidate spec) —
   the only new `app → bench` edge, added to the layering exceptions next to
   `modelprobe.py`, lazy.
5. **Gate 4 reads runs, it does not run models inside a publish.** A publish whose spec
   names `eval.suites` requires, for each suite, a completed `EvalRun` whose `spec_sha`
   equals the candidate's; the gate refuses when a **money grader** (`ledger_state`,
   `tool_selection`) pass rate is below `spec.eval.gate[grader]` (default 1.0 when the
   suite names the grader and the gate is silent), or when no run exists ("run the
   suite first"). Prose graders report in the summary, never block. `review: true`
   cases are excluded from gate runs (§9.5). Boot's seeded profile names no suite, so
   nothing about today's publish path changes until a human names one.
6. **`eval_capture` writes review cases, nothing else.** `kernos.after.eval_capture`
   (config `sample: 1.0`, `only_tool_turns: true`) stores each captured turn as
   `kn_eval_cases` row `source: captured, review: true` with `message`, `actor` (the
   principal id as key `"sender"`), `day`, `history` (as rendered), `had_images`, and
   `expect = {tools: [names], args: {money args}}` = what production did; no `members`
   / `prior_steps` (a captured case is replayable only after a human adds a world).
   It is registered but **not** in the seeded pipeline (opt-in per profile) — a row per
   turn is a business decision.
7. **The lunch suite is imported, not re-authored.** `POST /businesses/{id}/eval/import`
   runs the host-provided importer; chiatienan's imports `bench.corpus.load("typical")`
   as `kn_eval_cases` (`slug = case.id`, `source: imported`, images dropped with
   `had_images` kept) plus suite `lunch-typical` (graders `kernos.eval.tool_selection`
   with lunch's arg config, `lunch_ledger.eval.ledger_state`, `kernos.eval.prose`;
   judge `{model: settings.bench_judge_model or null, rubric: "prose"}`) and rubric
   `prose`. Idempotent (upsert by slug).

### Review gate (second Fable, 2026-09-06) — findings and dispositions

| id | sev | finding | disposition |
|---|---|---|---|
| F1 | high | The "generic" graders are not generic: `grade_tool_selection` uses `_share_map` (`app.money.prorate_items/split_shares`) and special-cases `propose_meal`; `grade_prose` needs `posted_body_kind`, which hardcodes lunch tool/result names. A kernos grader cannot import either. | Taken, option (a): `kernos.eval.graders.ToolSelection(config, equivalence={tool: fn})` — the per-tool "same money under another encoding" hook is injected; `Prose(unbacked, judge, outcome_kind)` takes the outcome classifier. `packs/lunch_ledger/eval.py` provides `share_map` and `posted_body_kind` and registers both graders with its hooks; the `bench.graders` shim wires the same, so identity holds. |
| F2 | high | Vacuous-pass vectors in gate 4: rate over `passed is not None` means graders that raise on every case, an unregistered pack grader or a mis-built world yield an undefined rate and the gate passes. | Taken. Gate rule: every blocking grader needs `graded ≥ 1` and `grader_raised == 0`, else refuse with the reason; summary separates `ungraded_no_expectation` from `ungraded_grader_raised`; a `run_turn`/world exception sets `record.error` so money graders return `False`; an unknown grader ref fails the run (`status: failed`), which the gate refuses. Proof line added. |
| F3 | high | Running the suite in the serving process is unsafe: `run_bot_turn` takes `_agent_lock` per case, `frozen_clock` patches the global clock every production turn reads, and a fresh `Kernel()` re-runs boot seeding and resets the module-level draft kinds / edge sources. | Taken. A run is a **job**: `python -m app.evalhost run --suite S --version V` in its own process; `POST …/versions/{v}/eval` creates the run row (`status: running`), spawns it and returns 202 with the run id. The job drives the candidate pipeline directly — `Kernel(db, resolver=StaticResolver(spec)).pipeline_for(spec).run(ctx)` — never `run_bot_turn`, never the lock. In tests the runner is called in-process with a fake `run_turn`. |
| F4 | high | The gate-4 hook gets the spec only; suites are per business and runs per version. | Taken. `eval_gate(spec, *, profile_id, version_id)`; `store.publish` passes them; `spec_sha` defined once in `kernos.eval` and used by run and gate. `tests/kernos/test_gates.py` (branch-added) updated. |
| F5 | med | `Dockerfile` copies only `pyproject.toml` and `app/` — `kernos`, `ledger_core`, `packs` are missing (a Phase 1–3 gap), and the eval path would import `bench` in prod. | Taken. 4a fixes the Dockerfile COPY (prerequisite). `frozen_clock`, the world seeding and `_World` move to `app/evalworld.py`; `bench.world` imports them (the F6 pattern), so runs work wherever the app runs; only the corpus **import** stays dev-only (`bench.corpus`, lazy, documented exception). |
| F6 | med | Importing `typical` with images dropped plants guaranteed failures (bills B1–B3); `typical` is 23 cases without the gitignored prod file, 37 with. | Taken. Images stay in the case JSON (base64). Proof states both counts; the importer upserts by slug so it is idempotent in either environment. |
| F7 | med | The identity proof is weak: `pi-typical-r3.json` has 0 `False` tool_selection verdicts, so "0 changed" cannot catch a more lenient grader; regrade never touches `ledger_state`/`prose`. | Taken. Identity runs over `pi-typical-r3.json` **and** `pi-typical-r3-before-fixes.json` (36 False), asserting `changed == 0` and the expected `skipped`; `ledger_state`/`prose` relocation is covered by the pre-existing oracle tests in `test_bench_run.py`/`test_bench_graders.py`, said so in the proof. |
| F8 | med | Gate 4 vs rollback (a superseded version has no run) and vs threshold edits (whole-spec sha invalidates the run when only `eval.*` changed). | Taken. `skip_eval` next to `skip_probe`, set by `rollback`; `spec_sha` is over `stored()` minus `eval`; tests for no-op edit / threshold edit / prompt edit. |
| F9 | med | `eval_capture` as written is not replayable (all tool names become mandatory, args carry db ids, `actor` is an id, no retention, and review-exclusion is moot since suites list cases by slug). | Taken. Capture only the pack's money tools (`ToolPack.money_tools`, default empty; lunch = `MONEY_TOOLS`); store a `members` snapshot keyed `m{id}` (display_name, nickname — no bank fields) and rewrite ids→keys in `args` and `actor`; `keep_days` for unreviewed captured rows; the Runner always skips `review: true` cases and records them as skipped. |
| F10 | med | Blocking graders by hardcoded name; three unreconciled namespaces (gate keys, suite plugin ids, record `grades` keys); no `repeat`. | Taken. `Grader.blocking: bool` declared by the grader; a suite grader entry has `name` (default: the id's last segment) used as the key in `record.grades` and in `eval.gate`; `repeat` on the suite (default 1 — a gate run is a publish precondition; comparison runs keep bench's 3) and the gate rate is over all records of the run. |
| F11 | low | after-in-finally details: `turn_id` is `None` when the turn raised before `run`; `finally` runs on `CancelledError`; the after plugin's own trace row is appended after it returns; summary must tolerate `result`/`outcome` being `None`. | Taken. Trace rows keyed by their own id, `turn_id` nullable + indexed, the route accepts either; `after` plugins guarded with `except Exception`, `BaseException` propagates after recording; (c) documented; a test with a raising `context` plugin. |
| F12 | low | The trace should carry tool calls with args and results (§8.6, Phase 8's `cms_add_eval_case(turn_id)`), not names only. | Taken. `tools: [{name, args, result}]` in the trace row (the eval record shape), so a trace → eval case is a copy. PII noted (settlement results carry `qr_url`); retention is the control. |
| F13 | low | `settings.bench_judge_model` does not exist; the judge needs `OPEN_ROUTER_KEY`. | Taken. `Settings.bench_judge_model` (env `BENCH_JUDGE_MODEL`, default none); `judge_model` recorded on the run; no judge → prose not graded, never blocks. |

Confirmed by the gate: the facts block above (trace recorded but unpersisted, `after` in `PIPELINE_ORDER`, error re-raised before `after`), the layering edges, the unused gate-4 hook, `LedgerState` fitting the pack within layering, the golden test's insensitivity to a side table, and the shim surface the pre-existing bench tests need.

### Task 4.1 (PR 4a): the turn trace — done

**Files:** `kernos/kernel/pipeline.py`, `kernos/plugins/after.py` (new), `kernos/adapters/protocols.py`
(`TraceStore`), `kernos/adapters/memory.py`, `kernos/content/models.py` (`TurnTrace`),
`kernos/content/store.py` (write/list/get/prune), `kernos/api/admin.py`, `app/kernel.py`,
`app/default_profile.py`, tests.

- [x] Prerequisite (F5): `Dockerfile` copies `kernos/`, `ledger_core/`, `packs/` next to `app/`.
- [x] `Pipeline.run`: stages before `after` as today; `after` in `finally`, each plugin
      guarded (`except Exception` → log + `ctx.record(..., "error")`, no re-raise;
      `BaseException` propagates after recording — F11).
- [x] `kernos.after.trace` plugin: summary + `tools: [{name, args, result}]` (F12) + trace →
      `TraceStore.write(...)`; tolerates `ctx.result`/`ctx.outcome` being `None`; `keep_days`
      config prunes older rows on write. `InMemoryTraces`.
- [x] `kn_turn_traces` (id, space_id, turn_id nullable+indexed, profile_version_id?, started,
      finished, summary JSON, tools JSON, trace JSON); `ContentStore.write_trace/list_traces/
      get_trace/prune_traces`; `GET /spaces/{space_id}/turns?limit` (summaries), `GET
      /spaces/{space_id}/turns/{ref}` (full; `ref` is a row id or a turn id — F11).
- [x] Seeded pipeline gains `after: [kernos.after.trace]`; the golden replays still
      byte-identical (the trace is a side table; `test_run_bot_turn_golden` compares the
      persisted reply). Boot re-syncs the seeded profile.
- [x] Proof: a turn writes one trace row whose summary names the tools and the outcome and
      whose `tools` carry args and results; a `context` plugin and a `run` plugin that raise
      each still leave a trace with `error` (and `turn_id` null for the former); the admin
      routes return it by row id and by turn id; pruning removes rows older than
      `keep_days`; full suite unedited.
- [x] Commit: `kernos: turn traces — after stage in finally, kn_turn_traces, admin turns API`

Notes: the trace plugin takes a `TraceStore` directly (`kernos.content.traces.StoreTraces`
over the content store) rather than a new `HostAdapters` field — one fewer thing a host
must wire; `turn_id` falls back to the result's when the run plugin did not set it;
`profile_version_id` is stored but null until the resolver exposes the version it
served (Task 4.3 wires it with gate 4). 1125 tests, 1 skipped; sidecar 69.

### Task 4.2 (PR 4b): eval core — cases, graders, runner, tables — done

**Files:** `kernos/eval/{__init__.py,case.py,graders.py,runner.py}` (new), `kernos/content/
models.py` (+4 tables), `kernos/content/store.py`, `kernos/api/admin.py`, `packs/lunch_ledger/eval.py`
(new), `bench/graders.py` → shim, `bench/corpus/__init__.py` (`Case.to_eval_case`), tests.

- [x] `EvalCase` dataclass + `to_dict/from_dict` (images kept — F6); `Record` helpers
      (`RECORD_VERSION = 1` moves here, `bench.run` imports it); `Verdict`; `Grader` protocol
      with `blocking: bool` (F10) + `GraderRegistry` (`register(id, factory)`, `build(ref)`
      where a suite entry is `{plugin, name?, config}` and `name` defaults to the id's last
      segment — the key in `record.grades` and in `eval.gate`); `Judge` protocol;
      `spec_sha(spec)` over `stored()` minus `eval` (F4, F8).
- [x] `kernos.eval.graders.ToolSelection(config, equivalence)` = `grade_tool_selection` with
      `compared_args`, `unordered`, `member_amount_lists`, `sender_defaulted` from config and
      the per-tool equivalence hook injected (F1); `Prose(unbacked, judge, outcome_kind)` =
      `grade_prose` with the amount checker and the outcome classifier injected. Both
      `blocking`: `ToolSelection` yes, `Prose` no.
- [x] `packs/lunch_ledger/eval.py`: `LedgerState` grader (blocking; + `compare_settlement`,
      `balances_by_member`, draft comparison — verbatim), `share_map` (the propose_meal
      equivalence), `posted_body_kind`, `PROSE_RUBRIC`, `MONEY_ARGS`, `graders()` → the
      pack's registrations with its hooks (`ToolPack.graders()` and `money_tools` join the
      protocol with `BasePack` defaults `{}` / `frozenset()`).
- [x] `bench.graders` shim: `grade_tool_selection`, `grade_ledger_state`, `grade_prose`,
      `compare_settlement`, `balances_by_member`, `posted_body_kind`, `summarize_cost_latency`,
      `PROSE_RUBRIC`, `MONEY_ARGS`, `Verdict` with today's signatures.
- [x] Tables + store CRUD (`put_case/list_cases/get_case/delete_case`, `put_suite/…`,
      `put_rubric/…`, `create_run/finish_run/get_run/list_runs`) + admin routes.
- [x] `kernos.eval.Runner`: sequential, `repeat` per suite (default 1), one world per case
      and repetition, `review: true` cases skipped and recorded as such (F9), grades
      isolated (a grader that raises → `passed: None, reason: grader raised`, counted as
      `ungraded_grader_raised` — F2), a `run_turn`/world exception → `record.error`; summary
      per grader `{name, blocking, passed, failed, ungraded_no_expectation,
      ungraded_grader_raised, rate}` + cost/latency + `judge_model`; an unknown grader ref
      → run `status: failed`; `run(suite, cases, spec, version_id) -> EvalRun`.
- [x] Proof: `tests/test_eval_regrade_identity.py` — `bench.regrade` over
      `bench/results/pi-typical-r3.json` and `pi-typical-r3-before-fixes.json` changes 0
      verdicts through the plugin graders with the expected `skipped` counts (F7);
      pre-existing `test_bench_graders.py`/`test_bench_run.py` unedited and green (they are
      the oracle for `ledger_state`/`prose`); `tests/kernos/test_eval_core.py` runs the
      `Runner` over stub cases with a stub world and a fake `run_turn` (one pass, one fail,
      one ungraded, one review-skipped, one raising grader) and checks the summary, the
      stored run, and `spec_sha` (no-op edit and threshold edit keep it, a prompt edit
      changes it).
- [x] Commit: `kernos.eval: cases, graders as plugins, runner and eval tables; lunch graders in the pack`

Notes: the pack registers its graders under **pack-qualified** ids
(`lunch_ledger.eval.{tool_selection,ledger_state,prose}`) built from the kernos
`ToolSelection`/`Prose` classes with its hooks — two money packs on one kernel never
collide on a generic id; a suite entry's `name` (e.g. `prose_quality`) is the record key.
`ToolPack` gained `money_tools` and `graders()`; the kernel keeps a `GraderRegistry` fed
by `register_packs`. Identity: 0 changed on both stored runs (skipped 42/57 without the
prod corpus, 0/15 with it). A suite with runs cannot be deleted (`Conflict`): runs are
gate evidence. 1135 tests, 1 skipped; sidecar 69.

### Task 4.3 (PR 4c): the lunch suite as content; gate 4; eval_capture — done

**Files:** `app/evalhost.py` (new; layering exception), `kernos/content/gates.py`,
`kernos/eval/gate.py` (new), `kernos/plugins/after.py` (`eval_capture`), `app/kernel.py`,
`kernos/api/admin.py`, `tests/test_layering.py` (exception), tests.

- [x] `app/evalworld.py`: `frozen_clock`, the member seeding and `_World` move here from
      `bench.world`, which imports them (F5) — runs need no `bench`.
- [x] `app/evalhost.py`: `import_lunch_suite(store, business_id)` (decision 7; lazy
      `bench.corpus`, the documented exception), `world_factory(case)` and
      `run_turn(case, world, spec)` for the `Runner` — the candidate pipeline driven
      directly through `Kernel(db, resolver=StaticResolver(spec))`, a fresh DB per case,
      never `run_bot_turn` or `_agent_lock` (F3) — `judge_for(spec)` from
      `Settings.bench_judge_model` (F13); a `__main__` that runs one suite against one
      version as a **job** and writes the run.
- [x] `POST /businesses/{id}/eval/import` → the importer; `POST /profiles/{id}/versions/{v}/eval?suite=`
      creates the run row (`status: running`), spawns `python -m app.evalhost …` and returns
      202 with the run id; `GET /eval/runs/{id}` reads it.
- [x] `kernos.eval.gate.eval_gate(store, spec, *, profile_id, version_id)` per decisions 5
      and F2/F8/F10: for each named suite the latest run with the candidate's `spec_sha`,
      `status: done`; every blocking grader `graded ≥ 1`, `grader_raised == 0`, rate ≥
      `eval.gate[name]` (default 1.0); `skip_eval` for rollback. Wired into `PublishGates`
      by the kernel.
- [x] `kernos.after.eval_capture` per decision 6 as amended by F9 (money tools only,
      member snapshot keyed `m{id}` without bank fields, ids→keys, `keep_days`); registered,
      opt-in.
- [x] Proof: importing yields one `kn_eval_cases` row per `typical` case (23 without the
      gitignored prod corpus, 37 with) with the golden ids preserved, images kept, and a
      suite naming three graders; importing twice changes nothing; a profile that names
      the suite cannot publish without a run; a run with a fake engine that fails one
      money case blocks the publish and the failure names the case; a run whose grader
      raises on every case blocks; a prose failure alone does not block; rollback skips
      the gate; a captured turn appears as a `review: true` case with keys, not ids, and
      the Runner skips it; full suite unedited.
- [x] Commit: `kernos.eval: lunch suite imported as content, gate 4 over stored runs, eval_capture`

Notes: `ToolSelection` resolves the expectation's member keys against `world.ids` itself
when the kernel runner hands it a world (`bench.run` pre-resolves and passes none) —
the runner would otherwise have compared keys to ids. The lunch pack's `money_tools`
(what capture records) excludes the scaffolding lookups `find_members`, `resolve_period`,
`resolve_date`. The eval `run_turn` drives the candidate pipeline over the world's own
database; a case's `history` string (prod captures) is not replayed through the room —
the committed corpora carry none, and a captured case's history is kept for the human
who adds its world. `Settings.bench_judge_model` is unset here, so prose is *not graded*
in every run this environment can make. 1141 tests, 1 skipped; sidecar 69.

### Task 4.4: Docs and state of play

- [x] Design §5.5/§8.6/§9.4 aligned with decisions 1–7 as amended by the gate; README
      (traces API, eval API); plan state of play.

**Proof for the phase:** `bench.regrade` of the stored `pi-typical-r3.json` through the
grader plugins yields identical verdicts (0 changed; no model calls); a captured turn
appears as a `review: true` case; the seeded pipeline traces every turn.

**Phase 4 — state of play (2026-09-06):** Tasks 4.1–4.4 done as three commits (6731f84 4a,
fbff454 4b, 4c). Every turn leaves a `kn_turn_traces` row (plugins, tool calls with args
and results, summary) written from an `after` stage the pipeline runs in a `finally`;
`GET /api/admin/spaces/{id}/turns[/{ref}]`. `kernos.eval` holds cases, tri-state
verdicts, graders with a declared `blocking`, a registry with per-suite names, the run
identity (`spec_sha` = stored spec minus `eval`), and a runner that rebuilds one world
per case, isolates grader failures and skips review cases; the business-neutral
graders take their lunch knowledge by injection and the lunch pack registers all three
under its own ids; `bench.graders` is a shim and regrading both stored runs changes 0
verdicts. Four eval tables with store CRUD and admin routes; `app/evalhost.py` imports
the `typical` corpus as the `lunch-typical` suite (23 cases here, 37 with the gitignored
prod file), runs a suite as a **job** (`python -m app.evalhost run …`, spawned by
`POST …/versions/{v}/eval` → 202), and gate 4 reads the latest completed run matching
the candidate's sha, refusing on a blocking grader that graded nothing, raised, or fell
below `eval.gate`; rollback skips it. `kernos.after.eval_capture` (opt-in) stores a turn's
money-tool calls as a keyed `review: true` case with a bank-free member snapshot. The
Dockerfile now ships `kernos`, `ledger_core`, `packs`. Baseline 986 → 1141 tests (+1
skipped), sidecar 69; golden 9/9. Review findings F1–F13 all landed.

## Phase 5 — Data plane

### Facts that shape this phase (from the code, 2026-09-06)

- The design (§5.3, decision 5) is specific: a `Collection` is **content** — name, JSON
  Schema, indexed fields — stored in **one** space-scoped documents table, with
  **generated** tools `{collection}_find` / `{collection}_upsert` / `{collection}_delete`
  that validate against the schema and never aggregate; `handles_money` is false and
  stays false. Pack-owned tables (`ToolPack.bind`) remain the home for anything numbers
  are derived from.
- Tools reach the model only through packs: `compose_tools(registry, spec.tool_packs, ctx)`
  in profile order, per-tool overrides applied, a name from two packs is an error. A
  pack's `tools(ctx)` may be dynamic — `ctx.space_id` is on the host's `ToolContext`
  (Task 3.3) and the kernel knows a space's business (`Kernel.business_for`, Task 4.3).
- Gate 1 does **not** check `tool_packs[].pack` against a registry (only the reflexivity
  blacklist names the field); a profile naming an unknown pack fails at the first turn
  (`PackError` in `compose_tools`), not at publish.
- `jsonschema` 4.26 is already a dependency (plugin `config_schema` validation).
- The host's knowledge modules — places, memos, observations, `knowledge.snapshot`
  (~1,300 lines, with their own admin UI, memory files and the `lunch_places` pack) — are
  lunch's and the host's; the design lists them as "already content; unchanged" (§2).
- `Business.seed` (JSON) holds the boot business's `spec` (the base of a first draft);
  `debug_api._all_tables` exports every `kn_` table; `Database.create_all` binds new
  `kn_` tables with no host change. The sidecar converts tool schemas with six JSON
  Schema keywords only (`agent_sidecar/schema.js`) and throws on the rest.

### Decisions taken for this phase

1. **Collections are content; documents are data.** `kn_collections` (business, slug,
   name, description, `schema` JSON — a JSON Schema object, checked with
   `Draft202012Validator.check_schema` on write — `key` (the document field that is the
   id, or none → generated), `indexed` (fields `find` may filter by), updated_at) and
   `kn_documents` (id, business_id, space_id, collection, doc_id, `data` JSON, created/
   updated at/by; unique on space+collection+doc_id). One documents table (decision 5).
   Definitions are audited content; document writes are audited too (actor = the
   principal or the admin) — they are what an agent remembers on the room's behalf.
2. **The generated tools are one pack, `kernos.data.CollectionsPack`** (id `collections`,
   `handles_money: False`, `money_tools` empty). `tools(ctx)` lists the business's
   collections for `ctx.space_id` and generates three tools per collection whose
   descriptions and input schemas come from the definition (the schema's `properties`
   are listed in the description; `upsert.data` **is** the collection schema, so the
   engine validates shape before the tool does). `find(where, limit≤50)` filters by
   equality on `indexed` fields only and returns documents as stored — no count, no sum,
   no group: **aggregation is refused by construction** because no tool computes one.
   `upsert` validates `data` against the schema and writes; `delete` by `doc_id`. Errors
   are clarifying questions (`kernos.packs.err`). Writes are immediate (facts, not money;
   design §5.3) — a business that wants confirmation puts a rule in its prompt.
3. **Opt-in per profile** by `tool_packs: [{pack: "collections"}]`; per-tool overrides
   work on generated names (`rota_find`). The seeded lunch profile does not enable it.
4. **Gate 1 checks pack ids.** `PublishGates` takes an optional `packs` registry; every
   `tool_packs[].pack` must be registered. Per-tool override names are not checked at
   publish (a collections pack's tool names depend on the space's business) — they fail
   at compose time as today.
5. **Admin API**: `/businesses/{id}/collections[/{slug}]` GET/PUT/DELETE (delete refused
   while documents exist), `/spaces/{space_id}/collections/{slug}/documents[/{doc_id}]`
   GET/PUT/DELETE with the same validation the tools apply.
6. **The host's knowledge modules stay where they are.** Phase 3 said Phase 5 "gives
   knowledge a home"; the home is `Collection` (a rota, house rules) or a pack with its
   own tables. Places/memos/observations are not migrated: they have a UI, memory files
   and resolution logic that a schema-validated document store would not carry, and
   moving them is a rewrite with no user-visible gain. `lunch_places` stays a host pack.
7. ~~Seeded collections~~ — dropped (F5): a business gets its collections through the
   admin API (Phase 6's poker `house-rules` is one `PUT`).

### Review gate (second Fable, 2026-09-06) — findings and dispositions

| id | sev | finding | disposition |
|---|---|---|---|
| F1 | high | The sidecar converts tool schemas with exactly six JSON Schema keywords (`type, properties, required, items, description, enum`; `agent_sidecar/schema.js`) and **throws** on anything else while converting the whole manifest — one collection with `additionalProperties: false` or `minimum` would kill every turn of the business, lunch tools included. | Taken. `put_collection` validates the schema against a kernos-defined **safe subset** = those six keywords, scalar `type` (unions of scalars only), `enum` on strings only, objects declare `properties`, arrays declare `items`; anything else is refused naming the keyword and path. `find.limit` is bounded in Python. Widening the subset means widening `schema.js` and its tests together. Proof converts the generated manifest with the sidecar's own converter. |
| F2 | high | Collection definitions are live, unversioned content that rewrite the manifest of a published profile (no snapshot, no probe); a schema edit can orphan documents; an `agent:*` actor could edit its own tools via `PUT /collections`. | Taken. Stated as designed: definitions are live content like places (audited), the snapshot/probe exception noted in §5.3/§9. `put_collection` on a collection with documents re-validates every document and refuses with the failing `doc_id`s unless `force`. Definition writes refuse `agent:*` actors in code (`DataStore`, like `_actor` refuses `boot*`); documents stay agent-writable. Phase 8's blacklist gains "collection definitions". |
| F3 | med | Scoping on re-bind is undefined: keying documents by `(space, slug)` surfaces business A's rows under business B's same-named collection; adding `business_id` to the key makes writes collide invisibly. `ToolContext.space_id` is an int, `kn_` tables store strings. | Taken. Documents are keyed by **`collection_id`** (FK): `unique(space_id, collection_id, doc_id)`; `find`/admin by `(collection_id, str(space_id))`; re-binding hides the other business's documents (intended); a slug rename orphans nothing; "refused while documents exist" is one FK count. No `business_id` column on documents (derivable). Unbound spaces resolve to the default business (`Kernel.business_for`). `str(space_id)` at the pack boundary. |
| F4 | med | "Aggregation refused by construction" over-claims: `find` returns rows and the model can sum them in prose. Position: not a D3 violation, numbers stay allowed in schemas; the real guard is the reply validator `unbacked_amounts`, which fires on a model-computed sum. | Taken. Restated: no tool computes an aggregate; a derived amount in the reply is caught by `unbacked_amounts` like any other. `find` returns rows in `doc_id` order with a boolean `more` when capped — a flag, not a count. Proof: a fake reply totalling two rows gets a verdict in the trace; a reply quoting one row's value does not. |
| F5 | med | Wrong fact: `Business.seed` is used (boot stores `seed.spec`; `create_draft` reads it). Decision 7's `seed.collections` would only run for the boot business. | Taken. Fact fixed; decision 7 dropped — poker's `house-rules` is one admin `PUT`. |
| F6 | med | Turn-time failures have a whole-space blast radius: a slug whose generated name collides with another pack's tool, or an override naming a missing tool, raises in `compose_tools` inside the turn. Provider tool-name limits (64 chars). | Taken. `put_collection(..., reserved)` refuses a slug whose generated names collide with any registered pack's tool names (the kernel passes the union, built with the null context as the probe does); slug ≤ 57 chars, `[a-z][a-z0-9_]*`. `CollectionsPack.tools(ctx)` never raises: a bad definition is logged and skipped. Gate 1 checks pack ids and, for every pack except `collections`, that override names exist. |
| F7 | med | "Small by construction" is asserted, not built; `count_documents` is a promise code cannot keep. | Taken. A per-`(space, collection)` cap of 1,000 documents enforced by `upsert` (a clarifying error when full); admin listing paginated by `doc_id` (`limit`, `after`) with a `more` flag; `count_documents` dropped. `key` must be a required string property with values `^[A-Za-z0-9_.:@-]{1,80}$` (they become path segments). |
| F8 | low | Wiring: `register_packs(*host_packs())` runs before `self.store` exists, so the collections pack registers after; `tools(ctx)` runs twice per turn (`build_tools` and `tool_manifest`). Router contract; models location. | Taken. Registered after the store (a second `register_packs` call — harmless, stated). The double call is accepted and stated (two cheap reads). The admin router's `get_kernel()` contract lists `data` and `business_for`; tables live in `kernos/content/models.py` (one `Base`, so the debug export sees them), the store in `kernos/data`. |
| F9 | low | Immediate writes: keep (consistent with `add_place`/`remember`). `delete` is irreversible with no card. | Taken. `{slug}_delete` returns the deleted document and the audit row's `before` carries it; the description says "permanently". No card because a `Draft` needs a `DraftKind` + commit — the wrong shape for facts. |
| F10 | low | Proof gaps: why gate 1's new check cannot break the branch; manifest stability unstated; sidecar fixture untouched. | Taken. Added to Task 5.2's proof. |

### Task 5.1 (PR 5a): collections and documents as content — done

**Files:** `kernos/content/models.py` (+2 tables), `kernos/data/{__init__.py,store.py}` (new),
`kernos/content/boot.py` (seed collections), `kernos/api/admin.py`, tests.

- [x] Tables per decisions 1/F3 (`kn_collections`; `kn_documents` keyed by `collection_id`,
      unique on `space_id, collection_id, doc_id`); `kernos.data.DataStore(session_factory,
      audit=store.log)`: `put_collection(business_id, slug, *, name, schema, key, indexed,
      description, actor, reserved=(), force=False)` — slug `[a-z][a-z0-9_]{0,56}`, schema
      in the **safe subset** (F1), `key` a required string property, `indexed` ⊆ properties,
      generated names not in `reserved` (F6), existing documents re-validated (F2), actor
      `agent:*` refused (F2); `get/list/delete_collection` (delete refused with documents);
      `validate(collection, data)`; `upsert_document(collection_id, space_id, data, *, actor,
      doc_id=None)` (id from `key`, values `^[A-Za-z0-9_.:@-]{1,80}$`; cap 1,000 per space
      and collection — F7); `get_document`; `find_documents(collection_id, space_id, *,
      where, limit)` (equality on `indexed` fields only, `doc_id` order, `more` flag — F4/F7);
      `list_documents(..., limit, after)`; `delete_document` returning the row (F9).
- [x] Admin routes per decision 5, documents paginated; the router's `get_kernel()`
      contract lists `data` and `business_for` (F8).
- [x] Proof: a schema outside the safe subset (`additionalProperties`, `minimum`, `$ref`,
      `enum` on integers) is refused naming the keyword; `key` not required or not a string
      refused; `indexed` outside the properties refused; a reserved name refused; an
      `agent:*` actor cannot define or delete a collection but can write documents; a
      schema edit that invalidates an existing document is refused naming it (and taken
      with `force`); upsert of invalid data refused with the schema error; the 1,001st
      document refused; `find` filters only on indexed fields and refuses others, orders by
      `doc_id`, flags `more`; delete of a collection with documents refused; full suite
      unedited.
- [x] Commit: `kernos.data: collections as content, one documents table, admin API`

Notes: `kernos.data.schema.check_schema` mirrors `agent_sidecar/schema.js` rule for rule; `DataStore` takes the content store's `log` for audit so a document write and its audit row share one transaction. 1149 tests, 1 skipped.

### Task 5.2 (PR 5b): the generated tools — done

**Files:** `kernos/data/pack.py` (new), `kernos/content/gates.py` (pack ids), `app/kernel.py`
(register the pack, pass `packs` to the gates), tests.

- [x] `CollectionsPack(data_store, business_of)` per decision 2; tool names
      `{slug}_{find,upsert,delete}`; a collection's `description` and `properties` in the
      tool descriptions; `upsert.input_schema.properties.data = collection.schema` (safe
      subset, so the sidecar converts it); `tools(ctx)` never raises — a bad definition is
      logged and skipped (F6); registered after the store, called twice per turn (F8).
- [x] Gate 1: `PublishGates(packs=…, tool_names_of=…)`: unknown pack id → `schema` failure
      naming it; an override naming a tool the pack lacks → `schema` failure, for every pack
      whose names are static (`tool_names_of` returns `None` for `collections`).
- [x] The kernel passes `reserved` = the union of registered packs' tool names (null
      context) to `put_collection`.
- [x] Proof: with a `rota` collection and a profile enabling `collections`, the manifest
      the engine receives has exactly the three generated tools after the lunch ones (the
      legacy order first, unknown names appended — the sidecar fixture test uses the
      legacy manifest and never sees them), and the sidecar's own `schema.js` converts that
      manifest; `rota_upsert` with wrong data returns a clarifying error and writes
      nothing, a good upsert then `rota_find` returns the document, a `where` on a
      non-indexed field is refused, `rota_delete` removes it and returns it, and the trace
      shows the calls; a profile naming pack `nope` or an override for a missing tool
      cannot publish (gate 1 reads the live pack registry, which is why the seeded profile
      and the stub-pack test still pass); an end-to-end turn (`tests/test_collections_turn.py`)
      through `run_bot_turn` with a fake engine calling `rota_upsert` persists the document
      and the reply is the model's prose (no card); a fake reply that totals two `find`
      rows gets an `unbacked_amounts` verdict in the trace while one quoting a row's value
      does not (F4); full suite unedited; layering green.
- [x] Commit: `kernos.data: generated find/upsert/delete tools per collection; gate 1 checks pack ids`

Notes: gate 1 takes `packs` and `tool_names_of`; the kernel's `static_tool_names` builds a pack's names with the null context and returns `None` for `collections`. The end-to-end test converts the generated manifest with `agent_sidecar/schema.js` under node. 1155 tests, 1 skipped; sidecar 69.

### Task 5.3: Docs and state of play

- [x] Design §5.3 as built; README (collections API); plan state of play.

**Proof for the phase:** schema-validated CRUD through the model; no tool computes an
aggregate, and a derived amount in the reply is caught by `unbacked_amounts`.

**Phase 5 — state of play (2026-09-06):** Tasks 5.1–5.3 done as two commits (5d70bdb 5a, 5b).
`kernos.data`: collections are audited, live content (a JSON Schema in the sidecar-safe
six-keyword subset, a required string `key`, `indexed` filter fields; definitions refuse
agent actors, reserved tool names and schema edits that orphan documents), documents live
in one table keyed by collection id and space (1,000 per space and collection), and
`CollectionsPack` generates `{slug}_find/_upsert/_delete` per collection for any profile
that enables `collections` — schema-validated writes, `find` returning rows in `doc_id`
order with a `more` flag and never a count; a model-computed total in the reply is
caught by `unbacked_amounts`. Gate 1 now checks pack ids and static override names.
Admin routes for definitions and paginated documents. The seeded lunch profile is
unchanged; the host's knowledge modules stay where they are. Baseline 986 → 1155
tests (+1 skipped), sidecar 69; golden 9/9. Review findings F1–F10 all landed.

## Phase 6 — `poker_ledger`

### Facts that shape this phase (from the code, 2026-09-06)

- `ledger_core` is the shared money domain (members, cash `payments`, FIFO debt edges,
  netting, VietQR, periods, statements, settlements) and its edges come from registered
  **edge sources** (`pack.contributions`, Task 3.4). `Payment.ref_kind` exists (default
  `"meal"`) but `Payment.meal_id` is a foreign key to `meals` — a payment cannot target a
  game row. `DebtEdge` still carries `meal_id`/`dish` (F5 rename deferred); FIFO sorts by
  `(occurred_on, meal_id)` and targets payments by `(debtor, creditor, meal_id)`.
- The tools the two businesses share — `find_members`, `propose_payment`, `settle_period`,
  `member_statement`, `get_period_summary`, `resolve_period`, `resolve_date`,
  `pick_random`, `cancel_draft` — live in `packs/lunch_ledger` with the `payment_draft`
  kind and the settlement/statement/summary/random-pick bodies. Nothing about them is
  lunch except the module they sit in.
- `ProfileSpec.validation` (`ValidationRuleRef`: id, scope, plugin, config, tool,
  on_fail) is declared and **never runs**: `pipeline_dict()` does not fold it into the
  `validate_args`/`validate_result` stages, and `agent.run_turn`'s `call_tool` never
  consults `Pipeline.validate` (`Stage.validate_args` exists; `Pipeline.validate(stage,
  ctx)` exists; nothing calls it). Gate 2's `handles_money` reads `meta` and pipeline
  plugins, never a pack's flag.
- `Database.create_all` binds the host's, `ledger_core`'s and the content plane's
  tables; it does **not** call any pack's `bind(engine)` (Phase 3 F8 promised it; the
  lunch pack has no tables of its own so nothing noticed). A pack with tables needs it.
- Skills and rules are host files (`app/agent_skills/{skills/*/SKILL.md, rules/*.mdc}`)
  read by `app.agent`; the seeded profile snapshots them as sources. `ToolPack` has no
  way to ship content (prompt, skills, rules). `ensure_seeded` is generic per business
  (`business_slug, spec, agent_slug, sources`) and boot seeds one (`lunch`).
- `app/evalworld.build_world` takes fixtures from `lunch_ledger_pack()` by name;
  `evalhost.import_lunch_suite` imports from `bench.corpus`. `ToolSelection` compares
  `member_amount_lists` entries as `(member, amount)` pairs — a poker entry is
  `{member, buy_in, cash_out}`.
- The frontend renders `expense_draft`/`payment_draft` cards; there is no `game_draft`
  card. The backend is the headless scope of this work: `commit_any` dispatches on any
  registered kind, so the API proves the pack; a card UI is the frontend's later work.

### Decisions taken for this phase

1. **The shared tools become their own pack, `ledger_tools`** (`packs/ledger_tools`,
   `handles_money: True`): the nine tools above, the `payment_draft` kind, and the
   settlement/statement/summary/random-pick bodies and decision, moved verbatim from
   `packs/lunch_ledger`. `lunch_ledger` keeps `propose_meal`, `void_meal`, the
   `expense_draft` kind, the meal bodies and `contributions`. Both businesses enable
   `ledger_tools`; the seeded lunch profile's `tool_packs` becomes `[lunch_ledger,
   ledger_tools, room_members, lunch_places]`; `LEGACY_ORDER` still governs the manifest
   and the golden replays stay byte-identical (render order: lunch first for the meal
   draft, then `ledger_tools` for payment drafts and typed bodies — the same precedence
   `decide()` has today). `MONEY_TOOLS` splits accordingly.
2. **Validation rules run** (design §5.4), proven by `chips-conserved`.
   `ProfileSpec.pipeline_dict()` folds `validation` refs with scope `tool_args` /
   `tool_result` into those stages as entries `{id: plugin, version, config: {**config,
   rule: id, tool, on_fail}}`; the run plugin puts a `validate_call(name, args) ->
   error | None` hook on `ToolContext` (`TurnContext.extras["pipeline"]` is set by the
   caller — `run_bot_turn` and the eval host); `agent.run_turn`'s `call_tool` consults it
   before executing and returns the error dict instead (the `{ok: false, error}`
   convention — the model asks). Pre-built validators in `kernos/plugins/validate.py`:
   `kernos.validate.sum_equals` (`left`/`right` are paths in a tiny subset:
   `field`, `field[*].sub`; integers; `tolerance` default 0), `kernos.validate.non_negative`
   (paths), `kernos.validate.unique_members` (path to a member-id list or a list of
   objects with `member`). Each declares `handles_money = True`, so gate 2 keeps counting.
   `scope: reply` rules are **not** folded (the seeded pipeline's reply validators are
   plugins already; folding them is a later, separate change).
3. **`poker_ledger`** (`packs/poker_ledger`, `handles_money: True`): tables `games (id,
   room_id, played_on, note, house, raw_input, logged_by, source, voided, voided_by,
   voided_at, created_at)` and `game_entries (id, game_id, member_id, buy_in, cash_out)`
   on the pack's own `Base`, bound by `bind(engine)`; `Database.create_all` calls every
   host pack's `bind` (the gap above, closed). Pure money in
   `packs/poker_ledger/money.py`: `net_positions(entries, house)` (Σ buy_in = Σ cash_out +
   house **exactly**, `house ≥ 0`, amounts ≥ 0, unique members, ≥ 2 players — a
   `PokerError` otherwise) and `game_edges(game_id, played_on, nets)`: for each loser, one
   edge per winner, the loss split across winners proportionally to their wins with
   largest-remainder rounding so **each loser's edges sum exactly to their loss**; a
   winner's receipts equal their win up to `(number of losers − 1)` đồng of rounding,
   stated in the result. The edge's `meal_id` slot carries the game id and `dish` the
   label `"game #N"` (a space belongs to one business, so game and meal ids never share a
   FIFO pool; payments toward game debts are untargeted — `Payment.meal_id` cannot
   reference a game). Tools: `propose_game(entries[{member, buy_in, cash_out}], house?,
   day_word?/played_on?, note?)` → `game_draft` payload with the nets and edges preview
   (the tool enforces the invariant itself — the rule in decision 2 is the profile's
   earlier, configurable check, the tool is the floor); `void_game(game_id)`;
   `game_history(keyword?)`. Kind `game_draft` (editable entries/house/note; `prepare`
   recomputes nets; commit writes `games` + `game_entries`; card `game_result`: "#12 —
   5 players, pot 2,500,000đ • winners … / losers …"). `contributions` = the edges of
   every non-voided game of the space. Fixtures `game_recorded`, `leave_pending`,
   `confirm_pending`, `payment`, `add_member`, `settle`, through the same `world`
   contract. Graders: `poker_ledger.eval.tool_selection` (`ToolSelection` with
   `compared_args = ["entries", "house", "from", "to", "amount"]` and a `propose_game`
   equivalence hook that reduces entries to the nets map) and
   `poker_ledger.eval.game_state` (blocking: the draft's nets and the settlement's
   transfers against the case's expectation) — both in the pack; `prose` reuses the
   kernos class with the pack's outcome classifier.
4. **A pack can ship its content.** `ToolPack` gains `content() -> {prompt_body,
   skills: [{name, description, body}], rules: [{slug, content, tags}]}` (`BasePack`
   default empty). `poker_ledger.content()` ships `record-game` and `poker-balances`
   skills, the `money-safety` rule verbatim (business-agnostic) and a prompt body built
   from the lunch template's variables. The **host** builds the poker `ProfileSpec`
   (`app/poker_profile.py`: the pack's content + this host's models/caps/runtime/pipeline
   — the same pipeline as lunch minus nothing; `tool_packs = [poker_ledger, ledger_tools,
   room_members]`; `validation = [chips-conserved, non-negative buy-ins/cash-outs,
   unique members]`) and boot seeds business `poker` with agent `dealer`
   (`ensure_seeded`, managed_by boot, no default binding — a table binds to the dealer
   through the admin API). Seeding is content only: nothing runs unless a room is bound.
5. **Eval world and suite by business.** `evalworld.build_world(db, case, fixtures)` takes
   the fixtures of the business's packs (the host resolves business → packs through the
   profile's `tool_packs`); `ToolPack.eval_cases() -> list[dict]` (`BasePack` default
   `[]`) lets a pack ship its golden cases as content; `evalhost.import_pack_suite(store,
   business_id, pack)` imports them plus the pack's graders as suite `{pack.id}-golden`;
   lunch keeps the bench import. Poker's golden cases: three recorded games with known
   nets and edges (`tests/golden/poker.py`), a settle case with QR payees, an
   ambiguous cash-out case (Σ does not conserve) that must **ask** (expects no
   `propose_game` draft: `tools_ok: []`, `expect.asks: true` — the grader passes when
   the tool returned an error or was not called and the reply is prose).

### Review gate (second Fable, 2026-09-06) — findings and dispositions

| id | sev | finding | disposition |
|---|---|---|---|
| F1 | high | "One business per space, so ids never share a FIFO pool" is not enforced: edge sources are registered per kernel, not per space, and a re-bound room with `meals.id == games.id` for the same pair merges edges in `apply_payments_fifo` — a targeted meal payment could settle a game edge. | Taken, in 6a: the deferred F5 rename — `DebtEdge(debtor, creditor, ref_kind, ref_id, label, occurred_on, amount, paid)` with `meal_id`/`dish` as read-only properties (wire dicts unchanged); FIFO sorts by `(occurred_on, ref_kind, ref_id)` and targets by `(debtor, creditor, ref_kind, ref_id)`; `debt_breakdown` passes `Payment.ref_kind` through; `build_debt_edges` stamps `"meal"`. Policy stated: contributions are **not** filtered by the space's profile (money never hides on re-bind); collisions are prevented by the key. Proof: a room with meal #N and game #N for one pair and a targeted payment attributes to the meal only. |
| F2 | high | The nine "shared" tools carry lunch: `get_period_summary` reads meals only and the summary body counts "meals"; `settle_period`'s pending listing knows the meal payload shape; the QR fallback note is "Chia tien an"; `posted_body_kind`/`CARD_LABELS` encode the shared bodies in lunch's eval module. | Taken, in 6a: (a) `ToolPack.timeline(session, space_id, from, to) -> list[dict]` (default `[]`) registered like `contributions`; `period_timeline` sums the registered sources (lunch = today's meal rows, byte-identical) and `_summary_body` counts by `kind` generically; (b) `DraftKind.summary(payload) -> dict` (default `{"kind": kind}`) used by `settle_period`'s pending listing and `_settle_blocked_body`; (c) `ledger_tools` takes `fallback_note(date) -> str` at registration (lunch passes today's string); (d) the shared bodies' outcome classification moves to `packs/ledger_tools/eval.py` and lunch/poker compose it. |
| F3 | high | Rounding "exact per loser, winners off by up to losers−1 đồng" leaves edges that do not sum to the nets on the card. | Taken. Sequential remaining-proportional allocation: losers in member-id order, each loss split across winners proportionally to their **remaining** unreceived win with largest-remainder rounding; because Σ losses = Σ wins exactly, the last loser's shares equal the remaining wins — both sides exact. Tested for exactness on both sides and determinism under reordering. `PokerError` carries the signed delta and says `house` is where rake/tips go; the `sum_equals` error carries the same delta. |
| F4 | high | Poker `ToolSelection`: `_item_key` reduces entries to `(member, amount)` → `{member, buy_in, cash_out}` compares as `(id, None)` (vacuous pass when the equivalence hook returns `None`); `tools: []` grades nothing, so a "must ask" case has no grader. | Taken. `item_fields` config per list (default today's pair, so the Phase 4 identity holds; poker: `entries: [member, buy_in, cash_out]`); a generic `expect.forbidden_tools`: fail when a named tool returned `ok: true`, a graded **True** when `tools` is empty and none did. `propose_game`'s result echoes `house` so an omitted `house` passes via `_recorded`. |
| F5 | med | Validation plumbing gaps: the pipeline handle on `extras` set by every caller; `block` verdicts in per-call stages would set `stopped`/replace `outcome`; one plugin cannot serve two scopes; no tool name in the trace row; `rule.tool` unchecked; validators' `config_schema` must accept `rule`/`tool`/`on_fail`; reflexivity fences only `on_fail == "block"` rules. | Taken. `Pipeline.run` sets `ctx.extras["pipeline"] = self`; `Pipeline.validate` runs per-call plugins with `ctx.extras["tool_call"]` and never touches `stopped`/`outcome`; `return_error` maps the first failing verdict to `{ok: False, error}`; records carry `tool`. Validators register once per stage (`kernos.validate.sum_equals` at `validate_args`, `….result` at `validate_result`); the fold checks `plugin.stage` against the scope. Config schemas declare `rule`/`tool`/`on_fail` and paths with a pattern; `right` is a list; a missing path reads 0, a non-integer fails closed. Gate 1 checks `rule.tool` against the enabled packs' static tool names. Blacklist any `validation` entry with a tool scope or a money-handling plugin. `bench.run` runs no rules (its profile has none) — stated. |
| F6 | med | "The same pipeline as lunch" carries lunch into the dealer: `fabricated_commit` checks meal ids and says "This meal was not recorded"; `COMMIT_TOOLS` is lunch's; the bot handle/label is global. | Taken. The poker profile keeps the host's handle and name (one bot identity per host; per-space handles are a host change, deferred; the proof calls `run_bot_turn` directly). `FabricatedCommit` takes its commit tools from the enabled packs (`ToolPack.commit_tools`, default = `money_tools`) and `record_exists` from the enabled packs' `DraftKind.exists(session, space_id, id)` (default: unknown → treat as forged), with a neutral body ("nothing was recorded"); the lunch body text is kept for lunch through the same hook so the golden stays byte-identical. Proof: a poker turn saying "Đã ghi #1" with no tool call is blocked and the body names no meal. |
| F7 | med | Wrong fact: `money-safety.mdc` is lunch-specific (14 tools, `items`, the meal card format, bill photos). | Taken. The rule splits into a generic core shipped by `ledger_tools.content()` (tagged `money`) and a lunch addendum that stays in the host's `rules/`; the seeded lunch profile's rules content stays byte-identical (core + addendum concatenated in today's order is not required — the lunch profile keeps today's single file; the split is for poker, which ships core + its own addendum). |
| F8 | med | Gate 2 never reads a pack's `handles_money` (design §9.2 says any enabled pack or plugin); the fact omitted `meta`. | Taken. Gate 2 ORs `pack.handles_money` over `tool_packs` through the registry it already holds; test added; fact fixed. |
| F9 | med | `build_world(db, case, fixtures)` breaks two-argument callers; `world_factory(case)` has no spec; fixture names overlap across packs. | Taken. `build_world(db, case, fixtures=None)` (`None` = lunch's); `run_suite` builds `world_factory` with `fixtures_for(spec)` = the union of the enabled packs' fixtures, refusing a step two packs provide; `payment`/`add_member`/`settle` move to `ledger_tools.fixtures()`; poker's pending step is `game_pending`. |
| F10 | low | A pack-owned `Base` is invisible to the debug export. | Taken. `ToolPack.metadata` (default `None`; `bind` uses it); `_all_tables` includes every registered pack's metadata. |
| F11 | low | No import cycle for `create_all` → pack `bind` (lazy import); `sync_additive_columns` is reusable. | Taken; negative proof: a lunch DB has the same `meals`/`payments` columns before and after 6c. |
| F12 | low | The frontend renders unknown card kinds as a blank human bubble and drops status flips for non-lunch drafts. | Scope kept (headless); the degradation is stated in the plan and README, and the generic `DraftCard` + `use-room` fix is ticketed in `TODO.md` as the frontend's first Phase-6 follow-up. |

Positions confirmed by the gate: the F5 rename now; `house ≥ 0`, tolerance 0; seed `poker` unconditionally (content only); reply-scope rules not folded; `bench.run` without rules.

### Task 6.1 (PR 6a): `ledger_tools` out of `lunch_ledger` — done

- [x] `ledger_core`: the F5 rename (`DebtEdge.ref_kind/ref_id/label`, `meal_id`/`dish`
      properties; FIFO keys include `ref_kind`; `Payment.ref_kind` passed through — F1);
      `period_timeline` over registered **timeline sources** (`ToolPack.timeline`, lunch =
      today's rows — F2a).
- [x] `packs/ledger_tools/{__init__.py,tools.py,render.py,eval.py,fixtures.py}` — the nine
      shared tools, `payment_draft`, the typed bodies (summary counts by `kind`), the
      shared outcome classification, `payment`/`add_member`/`settle` fixtures; takes
      `qr` and `fallback_note` at registration (F2c); `settle_period`'s pending listing and
      `_settle_blocked_body` use `DraftKind.summary` (F2b). `lunch_ledger` keeps
      `propose_meal`, `void_meal`, `expense_draft`, the meal bodies, `contributions`,
      `timeline`, its fixtures; `app/packs` registers both and the seeded profile enables
      both; `MONEY_TOOLS` → `LUNCH_TOOLS` + `LEDGER_TOOLS`; branch-added tests updated;
      `chat.py` re-exports follow the bodies.
- [x] Proof: golden 9/9; regrade identity 0/0; full suite unedited; `tool_manifest()`
      byte-identical (`test_tools_manifest.py`); a room with meal #N and game-shaped
      edge #N (a stub source) for one pair and a targeted payment attributes to the meal
      only (F1); layering green.
- [x] Commit: `ledger_core/packs: ref-keyed debt edges, timeline sources; ledger_tools — the tools two ledger businesses share`

Notes: `DebtEdge` keeps its field names (`meal_id`, `dish` are on the wire and in the
pre-existing `test_money.py` constructor calls) and gains `ref_kind` plus `ref_id`/`label`
properties — the key change is what F1 asked for; every FIFO key includes `ref_kind`.
Same-day edges of different kinds pool in `(ref_kind, id)` order — deterministic. The
blocked-settle body treats a `kind`-less pending entry as a meal (the shape before kinds
carried a name; `test_chat.py` pins it). `ledger_core.notes` still carries the lunch memo
wording (`"bua trua"`) — a 6c item alongside the rule split. 1158 tests, 1 skipped.

### Task 6.2 (PR 6b): validation rules run — done

- [x] `pipeline_dict()` folds `validation` (decision 2, F5): entries carry `rule`, `tool`,
      `on_fail` in config; the fold checks the plugin's stage matches the scope.
      `Pipeline.run` sets `ctx.extras["pipeline"]`; `Pipeline.validate` runs the per-call
      plugins with `ctx.extras["tool_call"]`, never touches `stopped`/`outcome`, records
      `tool`; `LegacyRunTurn` sets `tool_ctx.validate_call`; `agent.run_turn` honours it.
- [x] `kernos.validate.{sum_equals,non_negative,unique_members}` at `validate_args` and
      `….result` twins at `validate_result`; `config_schema` with the path pattern;
      `right` a list; missing path = 0; non-integer fails closed; `on_fail: return_error`
      (a `warn` rule records a `warn` verdict and lets the call through). Gate 1 checks
      `rule.tool` against the enabled packs' static tool names; gate 5 blacklists
      tool-scope and money-handling rule changes. `bench.run` runs no rules (stated).
- [x] Proof: a lunch profile with a `sum_equals` rule on `propose_meal` (`total` vs
      `items[*].amount`) refuses a mismatched call with the rule's error and the trace
      shows the `validate_args` verdict; without the rule the seeded pipeline is unchanged
      (golden 9/9); gate 1 refuses a rule naming an unknown validator or a bad path.
- [x] Commit: `kernos: validation rules run at validate_args/validate_result; sum_equals, non_negative, unique_members`

Notes: gate 5 fences every tool-scope rule (and blocking reply rules, as before) by
comparing the fenced list — the registry-based "money-handling plugin" check was not
needed, since every tool-scope rule is fenced anyway. `Pipeline.validate` refuses the
call when a rule *raises* (fail closed, recorded as `error`). The end-to-end proof drives
`agent.run_turn` with the fake sidecar through `run_bot_turn`, so the executor hook is the
one production uses. 1165 tests, 1 skipped; sidecar 69.

### Task 6.3 (PR 6c): the poker pack and business — done

- [x] `packs/poker_ledger/{__init__.py,models.py,money.py,tools.py,render.py,fixtures.py,
      eval.py,content/}` per decisions 3–5 as amended (F3 allocation, F4 grader config and
      `forbidden_tools`, `game_pending`); `ToolPack.metadata`/`bind` and `Database.create_all`
      binding host packs lazily (F10/F11); `ToolPack.content()`/`eval_cases()`/`commit_tools`;
      `FabricatedCommit` over the enabled packs (F6); the money-safety rule split (F7);
      gate 2 over packs (F8); `app/poker_profile.py`; boot seeds `poker`/`dealer`;
      `build_world(db, case, fixtures=None)` and `fixtures_for(spec)` (F9);
      `import_pack_suite`; admin import for a non-lunch business; `TODO.md` frontend ticket
      (F12).
- [x] Proof: `tests/test_poker_money.py` (nets, edges exact per loser, rounding bound,
      every invariant refused with a clear error); `tests/golden/poker.py` +
      `tests/test_poker_pack.py` (a table bound to the dealer: `propose_game` through
      `run_bot_turn` with a fake engine → `game_draft` card → `commit_any` writes the game
      → `settle_period` shows losers→winners transfers with QR → `member_statement` rows name
      `game #1` → `void_game` clears them; `chips-conserved` refuses a short table before
      the tool runs and the tool refuses it too without the rule; `game_history`); the poker
      suite green under an oracle engine (`import_pack_suite` + `run_suite`), the lunch
      suite unchanged (golden 9/9, regrade identity, `typical` import count unchanged);
      a lunch room sees no poker tools and a poker table no meal tools; layering green.
- [x] Commit: `packs: poker_ledger — games, chips conserved, edges from losers to winners; the poker business`

Notes: the **house** cut is borne by the losers in proportion to their losses (exactly,
largest remainder) and is a debt to nobody in the ledger — the table kept that cash; the
rest of each loss goes to the winners, exact on both sides. The golden cases ship with the
pack (`packs/poker_ledger/golden.py`, imported as suite `poker_ledger-golden`) rather than
under `tests/golden/`, because a pack's golden cases are its content (decision 5). The
`game_state` grader compares settlement transfers as a set (netting order is the core's).
The `bench.graders` shim keeps its lunch import path; `compare_settlement` and
`balances_by_member` now live in `packs/ledger_tools/eval.py`. 1222 tests, 1 skipped.

### Task 6.4: Docs and state of play

- [x] Design §7.2 as built; README (second business); plan state of play.

**Proof for the phase:** the poker suite green; the lunch suite unchanged.

**Phase 6 — state of play (2026-09-06):** Tasks 6.1–6.4 done as three commits (0cb75c5 6a,
96a3521 6b, 70c2f78 6c). The second money business exists as `packs/poker_ledger` on the
shared `ledger_core` + `packs/ledger_tools`: debt edges keyed by what they reference,
timeline sources, the shared tools injected with the host's QR builder, memo wording and
draft-kind registry; validation rules run at the per-call stages through the frozen
executor; the poker pack ships tables, exact money, tools, a draft kind, content, fixtures,
graders and golden cases, and boot seeds business `poker`/agent `dealer` as content. Gate 1
checks pack ids, override names, rule tools and stage/scope; gate 2 counts pack money
flags; gate 5 fences tool-scope rules. The frontend has no `game_draft` card yet
(`TODO.md`). Baseline 986 → 1222 tests (+1 skipped), sidecar 69; golden 9/9; regrade
identity 0/0. Review findings F1–F12 all landed.

## Phase 7 — Agents and sub-agents

### Facts that shape this phase (from the code, 2026-09-06)

- `kn_agents` already carries `role` (`manager` | `sub`), `delegates_to` (a JSON list, never
  validated), `max_depth` (default 2) and `capabilities`; a space binds to a manager only
  (`store.bind_space`); there is `get_agent(id)`, `list_agents(business_id)`,
  `default_agent(business)` and no lookup by slug.
- `TurnContext.depth` (default 0) and `ToolInvocation.from_agent` exist and nothing sets
  or reads them; `TurnResult.last_result/all_results` do not filter on `from_agent`.
  `TurnEvent` types `sub.started`/`sub.finished` exist and map to `agent.sub.started/
  finished`, which the frontend ignores (additive).
- The run stage is `LegacyRunTurn` → the frozen `agent.run_turn(text, tool_ctx, images,
  emit, memory, history)` → `PiEngine.run(spec, tools=tool_manifest(ctx), call_tool)`. The
  sidecar caps a run by `EngineSpec.max_tools`/`max_seconds`, which come from
  `ctx.engine_spec` (the profile's `caps`). `PiEngine` records the executor's payload
  verbatim as the invocation's `result` and sends the same payload to the model.
- `chat.run_bot_turn` resolves a **spec**; the agent behind it is
  `kernel.resolver.describe(space_id)["agent"]`. The whole pipeline runs under
  `chat._agent_lock`, an `asyncio.Lock` that is **not reentrant** — a nested run must not
  take it. `Pipeline.run` puts itself on `ctx.extras["pipeline"]` (Task 6.2).
- `moneyguard.backed_amounts` counts numbers in every invocation's `args` and `result`;
  the reply validators read `ctx.result.tools`; the packs' `render` reads
  `result.last_result/all_results`; `EvalCapture` records the enabled packs'
  `money_tools` calls; the trace row's `tools` already carry `from_agent`.
- Golden fixtures compare persisted messages, events and what the fake engine was handed;
  the seeded agents have `delegates_to = []`, so nothing below runs for them.

### Decisions taken for this phase

1. **Delegation is a kernos pack, `kernos.agents.DelegationPack`** (id `delegation`,
   `handles_money: False`, no draft kinds), implicitly enabled for a turn whose agent has
   a non-empty `delegates_to`: the run plugin appends `{pack: "delegation"}` to the
   turn's `tool_config` and puts the agent record and `depth` on `ToolContext`.
   `tools(ctx)` generates `ask_<sub_slug>(task: string)` for each `delegates_to` entry
   that names a `sub` agent of the same business (anything else is logged and skipped;
   `store.create_agent/update_agent` refuse a `delegates_to` entry that is not a `sub` of
   the business, and a `sub` that is bound or default). The tool's description is the
   sub's `name` plus `capabilities.description` when set. A sub at `depth ≥
   manager.max_depth` gets no `ask_*` tools — recursion stops there; a sub's own
   `delegates_to` is honoured within that limit.
2. **Executing `ask_<sub>` is a nested pipeline run inside the executor** (`Kernel.run_sub`):
   the sub's profile (its agent's published version, with runtime) builds its pipeline;
   stages `context → validate` run (no `persist`, no `after`: a sub posts nothing and is
   traced as a **span** of the manager — its trace rows join the manager's `ctx.trace`
   with `span=<slug>` and `depth`), same space and principal, `text=task`, `depth+1`, a
   fresh `ToolContext` for the sub (same db/room/sender, `tool_config` from the sub's
   `tool_packs`, `agent=sub`). Caps are the **minimum** of the manager's remaining budget
   and the sub's own: `max_tools − calls made so far` (the executor counts on
   `ToolContext.calls_made`) and `max_seconds − elapsed` (`Pipeline.run` stamps
   `ctx.extras["started_at"]`); the sub's `ToolContext.caps_override` is applied to its
   `EngineSpec` by `agent.run_turn`. The nested run never touches `_agent_lock` (held by
   the manager's turn) and never posts a message.
3. **Results merge, text does not.** The tool returns to the model `{ok, text, results}`
   (the sub's final text and its structured tool results); the **recorded** invocation
   is `{ok, agent, results}` — `PiEngine` records `payload["_record"]` when present and
   sends the payload without it (a documented executor contract), so a sub's prose can
   never launder a number into the allow-set. Every tool invocation the sub made is
   appended to the manager's `TurnResult.tools` tagged `from_agent=<slug>` (collected on
   `ToolContext.sub_invocations`, merged by `agent.run_turn` after the engine returns).
   `TurnResult.last_result/all_results` consider **own** invocations only (`from_agent is
   None`), so a sub's `propose_*` result is data for the manager and never a card; the
   reply validators read `.tools` — the union — so every number any tool produced backs
   the manager's reply and a number that appears only in the sub's text does not.
4. **Events**: `sub.started` (`agent`, `task`) and `sub.finished` (`agent`, `elapsed_ms`,
   `tools`, `error`) on the manager's sink with the manager's `turn_id`; the sub's own
   `agent.*` events are forwarded through the manager's sink with `parent_turn_id` and
   `agent` added (the frontend ignores the extra keys; an AG-UI sink can nest them).
5. **Out of scope, stated**: the eval host runs a profile, not an agent — `ask_*` tools
   do not appear in an eval run until Phase 8 gives the runner an agent; `EvalCapture`
   records the manager's own money calls only (`from_agent is None`); a sub's turn is
   not separately traced (it is a span).

### Review gate (second Fable, 2026-09-06) — findings and dispositions

| id | sev | finding | disposition |
|---|---|---|---|
| F1 | high | The sidecar's `max_seconds` timer on the manager keeps running while a nested run consumes wall-clock inside `call_tool`; when it fires the manager's session is disposed, the sub keeps running (and may write), and the room gets a capped reply. `max_tools` is counted by the sidecar on the manager's own calls only. | Taken. `sub.max_seconds = min(sub.caps.max_seconds, manager.max_seconds − elapsed − MARGIN)` (`MARGIN` 15 s, config of the delegation pack); below a 5 s floor the executor refuses without running (`{ok: false, error: "no time budget left to delegate"}`); `sub.max_tools = min(sub.caps.max_tools, manager.max_tools − own calls − sub calls so far)`, floor 1; the tool payload carries the sub's `capped`. Residual stated: a manager cut mid-sub still merges the sub's invocations, so the trace shows the writes. |
| F2 | high | A sub's successful `propose_*` (`from_agent`) counts as commit evidence for the manager's "đã ghi" claim although it creates no card. | Taken. `FabricatedCommit` admits **own** invocations only as commit evidence; `unbacked_amounts` keeps the union; the `ask_*` description says a sub's proposals are data and the manager must call `propose_*` itself for a card. Proof: "Đã ghi #1" with only a sub's `propose_meal` is blocked. |
| F3 | high | Forwarding the sub's `agent.*` events under the sub's `turn_id` makes the frontend stream the sub's prose as the active timeline and end the manager's "working" state early. | Taken. The legacy sink drops the sub's `run.started/finished/text.delta`; `sub.started/finished` replace them; the sub's `tool.start/tool.result` are forwarded under the **manager's** `turn_id` with `agent=<slug>`. Proof asserts no `agent.text.delta` and no second `agent.run.started`. |
| F4 | high | Blanket own-only `last_result/all_results` breaks `Cards`' republish of a sub's `cancel_draft` and contradicts spec §6's wording. | Taken. Render stays own-only (a deliberate deviation, spec §6 fixed in 7.2); `TurnResult.all_results(name, *, include_sub=False)`; `Cards` republishes over `include_sub=True`. Stated: immediate-write tools a sub calls take effect with no pack body; the manager's prose reports them. Proof: a sub's `cancel_draft` yields the republish event. |
| F5 | med | "Stages context → validate" has no API; `Pipeline.run` always runs everything and `after` in a `finally` — the sub would write a second trace row and be captured as a case; `text` is undefined when the sub's validate blocks or its render yields a Draft. | Taken. `Pipeline.run(ctx, *, through=None)` runs stages up to and including `through` (no `after` unless included; default unchanged); `run_sub` uses `through=Stage.validate`; `text = outcome.text if outcome is a Body else result.final_text` — a blocked sub hands the manager the replacement body, never the forged prose; a Draft outcome yields the proposal in `results`. |
| F6 | med | The sub ctx must carry `images`, `before_id`, `principal`; `Rollover` must not run mid-turn at depth > 0; joining the sub's trace rows double-counts `elapsed_ms` and leaks a sub plugin error into the manager's summary. | Taken. The sub ctx copies them; `run_sub` skips `kernos.context.rollover` at depth > 0; `summarize()` sums `ms` and picks `errors` over rows with no `span`; verdicts keep `span`. |
| F7 | med | `update_agent` validates nothing (any `role`, `is_default` on a sub, role flips on referenced/bound agents); self-reference in `delegates_to`. | Taken. `update_agent` validates `role`, refuses `is_default` on a sub, refuses role changes for an agent that is bound, default or referenced, and refuses `delegates_to` entries that are not `sub` of the business, are bound/default, or the agent itself; `referrers(agent_id)` helper. |
| F8 | med | Depth semantics ambiguous at depth 2; cycles legal. | Taken. The **root** (bound/default) agent's `max_depth` is the tree's limit, carried on `ToolContext.max_depth` from depth 0 to every sub ctx (a sub's own column is ignored, documented); `ask_*` tools exist iff `depth + 1 < max_depth` (default 2: the manager delegates, its subs do not); cycles are legal and bounded by depth. |
| F9 | med | Same bridge with `req_id` multiplexing works, but the sidecar's `pendingToolCalls` is keyed by `call_id` alone — concurrent sessions can collide. | Taken. Position: same bridge, a second `run` with `req_id=run-<sub turn_id>`; `main.js` keys `pendingToolCalls` by `${req_id}:${call_id}`; a sidecar test interleaves two runs with the same `call_id`. |
| F10 | med | A fake `run_turn` branching on depth proves none of the mechanism. | Taken. The proof runs the real `run_turn` with a scripted `FakeBridge` branching on `req_id`: the sub's `run` command carries the clamped caps, the manager's `tool_result` content carries `text` while the recorded invocation does not, and the merged order; one pipeline-level test keeps the golden angle. |
| F11 | low | Residual laundering: a number the manager copies from the sub's text into a tool's args is backed; the summary's `tools` list cannot tell a sub's call. | Taken. (a) recorded as an accepted residual (the arg reaches a card a human confirms); (b) summary `tools` entries are `<slug>:<name>` for sub calls. |
| F12 | low | `capabilities.description` collides with §8.3's permission object; `EvalRun` has no agent. | Taken. `kn_agents.description` column (additive) for the `ask_*` description; Phase 8 adds `agent_id` to eval runs and the runner context (noted, not built). |

Confirmed by the gate: zero behaviour change with `delegates_to = []`; the facts block; `settle_period` is read-only; the `_record` contract keeps the sub's prose out of the record; nothing in the nested path takes `_agent_lock`; the sub's own validation rules apply through its pipeline handle.

### Task 7.1 (PR 7a): delegation

**Files:** `kernos/agents.py` (new), `kernos/engine/base.py` (own-only reads, the `_record`
contract), `kernos/engine/pi/engine.py`, `kernos/kernel/pipeline.py` (`started_at`),
`kernos/content/store.py` (delegates_to validation), `app/kernel.py` (`run_sub`,
`agent_for`), `app/chat.py` (agent on `extras`), `app/plugins/run.py`, `app/tools.py`,
`app/agent.py` (caps override, merge, `calls_made`), tests.

- [x] `TurnResult.last_result` own-only and `all_results(name, *, include_sub=False)`;
      `Cards` republishes with `include_sub=True` (F4); `PiEngine` records `_record`;
      `Pipeline.run(ctx, *, through=None)` and `started_at` (F5); `kn_agents.description`
      (F12); `store.create_agent/update_agent` validation and `referrers` (F7); the sidecar
      keys `pendingToolCalls` by `req_id:call_id` with a test (F9).
- [x] `DelegationPack(agents_of, run_sub, margin_seconds=15)` per decisions 1–2 as amended
      (F1 caps rule and refusal, F8 depth on `ToolContext.max_depth`, the description text
      of F2); `Kernel.run_sub(parent_ctx, sub_agent, task) -> {text, results, capped,
      invocations}` per decisions 2–4 as amended (F3 event shape, F5 `through=validate` and
      the text rule, F6 sub ctx fields and no rollover); `Kernel.agent_for(space_id)`;
      `run_bot_turn` puts the agent on `ctx.extras["agent"]`; `LegacyRunTurn` passes
      agent/depth/max_depth/tool_config; `agent.run_turn` applies `caps_override`, counts
      calls, merges `sub_invocations`; `FabricatedCommit` admits own invocations only (F2);
      `summarize()` sums own rows and names sub calls `<slug>:<name>` (F6, F11).
- [x] Proof (`tests/test_delegation.py`, a manager + a `sub` "auditor" on a lunch room,
      the real `run_turn` with a scripted `FakeBridge` branching on `req_id` — F10): the
      manager's manifest carries `ask_auditor` and the sub's does not; the sub's `run`
      command carries the clamped caps (manager with 40 tools and 3 already made, sub with
      40 → 37; seconds clamped by the margin) and a manager with no time budget left gets
      a refusal without a nested run; the manager's `tool_result` content for `ask_auditor`
      carries `text` while the recorded invocation does not; the merged `TurnResult.tools`
      is `[ask_auditor (own), settle_period (from_agent=auditor)]`; a manager reply quoting
      an amount from the sub's **result** passes `unbacked_amounts`, one quoting an amount
      that appears only in the sub's **text** is flagged; "Đã ghi #1" with only a sub's
      `propose_meal` is blocked (F2); a sub's `propose_payment` creates no card and a sub's
      `cancel_draft` yields the republish event (F4); a sub whose prose is a fabricated
      commit hands the manager the replacement text (F5); at the depth limit the sub gets
      no `ask_*` tools and a B→C→B cycle terminates (F8); the emitted events have exactly
      one `agent.run.started`, no `agent.text.delta` from the sub, `agent.sub.started/
      finished` and the sub's tool events under the manager's `turn_id` with `agent` (F3);
      the trace has the sub's rows as `span=auditor`, the summary's `elapsed_ms` counts
      own rows and lists `auditor:settle_period` (F6, F11); `create_agent`/`update_agent`
      refusals (F7); the sidecar interleaves two runs with the same `call_id` (F9); golden
      9/9; full suite unedited; layering green.
- [x] Commit: `kernos.agents: ask_<sub> delegation — nested run with min caps, results merged as from_agent, text never backed`

      _As built (2026-09-06), where the build refined the plan:_ `delegates_to` entries are
      **agent ids** (design §5.2 already said so); a pack tool may return an awaitable and
      the executor awaits it (the nested run is async, pack tools stay sync otherwise); the
      sub's `TurnContext` reaches the pack through `ToolContext.turn`, the manager's
      `turn_id` through `ToolContext.turn_id` (set by `run_turn` before the engine runs), so
      the sub's forwarded events carry it; `Rollover` itself returns early at `depth > 0`
      (the same effect as skipping it in `run_sub`, robust to any nested runner); sub
      invocations are merged **in order** — each sub's calls right after the `ask_*` that
      made them (`(own_call_index, invocation)` pairs) — not appended at the end; a
      deeper sub's trace rows keep their own `span`, so a B→C chain shows both; the
      manager's budget counts the calls made *before* the in-flight `ask_*` (40 tools, 3
      made → 37, as the proof states); `EvalCapture` records own money calls only (decision
      5, now enforced); the `text` a sub hands back is its **outcome**: with a pack body
      (a settlement) it is that body, not the model's prose. Suite 1235 passed, 1 skipped;
      sidecar 70/70; golden 9/9; no pre-existing test edited.

### Task 7.2: Docs and state of play

- [ ] Design §6 as built; README (agents); plan state of play.

**Proof for the phase:** merged results pass `unbacked_amounts`; a sub's text never backs a number.

**Phase 7 — state of play:** _(filled as tasks complete)_

## Phase 8 — AI-ready

- `os_admin` pack (§8.2); `capabilities`, `self_change_scope`, blacklist; `kn_change_proposals`
  and the proposal card; in-turn loop bounds; steward brief as a prompt template.
  **Proof:** the §8.5 steward scenario end to end.

## Phase 9 — Portability

- `examples/minimal_host`; `kernos.api.agui` sink; Pi-package export/import; sidecar
  extension registry; move the sidecar to `backend/kernos_sidecar/` and update
  Dockerfile/CI; packaging metadata for PyPI/npm; `git subtree split` rehearsal.
  **Proof:** the example host runs a "hello" business with no `app`/`packs` on its path.
