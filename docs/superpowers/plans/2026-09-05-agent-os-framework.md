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
