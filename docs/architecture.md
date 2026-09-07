# Architecture

How chiatienan is put together, and how it sits on the `kernos` agent framework. This is
the structural view: what the pieces are, where the boundaries run, and what crosses them.
For *how to extend it* read the [developer guide](developer-guide.md); for *why* it is
shaped this way, the [design spec](superpowers/specs/2026-09-05-agent-cms-design.md) and
the [phase plan](superpowers/plans/2026-09-05-agent-os-framework.md). Every statement here
was checked against the code on the day of writing (2026-09-07); where a test pins a
claim, the test is named.

---

## 1. Runtime topology

Three containers, one process each, on one droplet (`docker-compose.yml`).

```
Phone / browser — installable PWA (Next.js 16, React 19)
   │
   ▼
Caddy (auto-TLS)
   ├── /api/*, /internal/*  ──▶  FastAPI backend — ONE uvicorn process
   └── everything else      ──▶  Next.js standalone server
                                    │
      backend process               │
      ┌─────────────────────────────┴────────────────────────────────────────┐
      │  app/           the host: rooms, chat, SSE hub, ledger, drafts, CMS  │
      │  packs/         business tool packs: lunch_ledger, ledger_tools,     │
      │                 poker_ledger                                          │
      │  ledger_core/   the money domain both ledgers share                  │
      │  kernos/        the agent framework                                  │
      │        │ JSONL over stdin/stdout                                     │
      │        ▼                                                             │
      │  agent_sidecar/ Node child process — the Pi harness, one per backend │
      └──────────────────────────────────────────────────────────────────────┘
                                    │
                            SQLite (WAL) on the /data volume
                            app tables + kn_* content tables
```

Two facts about this topology decide a lot of the code:

- **Single writer.** Agent turns are serialized by one in-process `asyncio.Lock`
  (`chat._agent_lock`) and SQLite runs in WAL mode. This is correct only with one
  backend process. No `--workers`, no replicas.
- **Single sidecar.** The backend spawns one Node child and multiplexes every turn over
  its stdio. Every command carries a `req_id` and every reply echoes it, because a
  liveness `ping` from `/internal/bridge-smoke` can legitimately interleave with a turn.

## 2. Four layers, one repository

The backend is four Python packages with a strict import direction. The rule is a test
(`tests/test_layering.py`), so a violation is a red build rather than a review comment.

| layer | what it is | may import |
|---|---|---|
| `kernos/` | the agent framework: pipeline, plugins, engine boundary, content plane, agents, data plane, eval, admin API | `kernos` only |
| `ledger_core/` | the money domain: members, meals, payments, FIFO debt edges, netting, periods, VietQR, `moneyguard` | `kernos`, itself |
| `packs/` | business tool packs: `lunch_ledger`, `ledger_tools`, `poker_ledger` | `kernos`, `ledger_core`, itself |
| `app/` | the host application: FastAPI routes, rooms, chat, SSE, drafts, the room CMS, the kernos composition root | everything |

`bench/` (the benchmark) may import everything; `examples/minimal_host/` may import
`kernos` only and its test proves it boots with no chiatienan module on the path. The two
tolerated lazy exceptions (`app/modelprobe.py` and `app/evalhost.py` reaching into
`bench`) are listed in the test with a reason.

The direction matters for one reason: **`kernos` knows spaces, principals, turns,
profiles and plugins. It does not know rooms, meals, VND or Phoenix.** Everything
chiatienan-specific reaches the framework through one of three doors described next:
the composition root, the host adapters, and the packs.

## 3. The kernos integration

### 3.1 The composition root — `app/kernel.py`

`kernos.host.BaseKernel` is what every host's composition root has in common: the
framework plugins and packs, pipeline caching, delegation, proposals, eval jobs and the
gate helpers. chiatienan's `Kernel` subclasses it and, in its constructor, does all of the
wiring in one place:

1. builds a `ContentStore` and a `DataStore` over the app's `Database`, and the ten host
   adapters (`app/hostadapters.py`);
2. registers **this host's packs** (`app/packs/`: the `lunch_ledger` registration with the
   QR builder and place resolver injected, `lunch_places`, `room_members`);
3. registers the **framework packs** every host gets (`collections`, `delegation`,
   `os_admin`) and the **framework plugins**;
4. registers **this app's plugins**: the legacy run stage, the two money validators, and
   the Phase-1 Phoenix system-prompt plugin (still registered, no longer in the seeded
   pipeline since the prompt became content rendered by `kernos.prompt.template`);
5. **seeds content**: the `lunch` business with agent `phoenix`, the `poker` business with
   agent `dealer`, and the `steward` sub-agent under lunch;
6. builds the publish gates, the database-backed `DbResolver` (falling back to the
   code-built default spec), and the model probe.

Kernels are cached per `Database` object in a weak dictionary, so production has one and
each test gets its own. Four hooks are the only places `BaseKernel` calls back into the
host: how packs' draft kinds and debt-edge contributions are handed to `app.drafts` and
`ledger_core`, how a tool context is built with no room, how a sub-agent's tool context is
derived from its manager's, and the command line that runs an eval job.

### 3.2 The host adapters — `app/hostadapters.py`

Ten small protocols (`kernos/adapters/protocols.py`) are what the kernel needs from a
host to run a turn. chiatienan implements each as pure delegation, one call into the
module that already owned the behaviour:

| protocol | over |
|---|---|
| `HistorySource` | `chat.build_history`, `chat.recent_images`, `memory.messages_to_summarize` |
| `MemoryStore` | `memory.py` (the room's `memory.md` and its watermark) |
| `KnowledgeSource` | `knowledge.py` |
| `EventSink` | the SSE `RoomHub` (`realtime.py`) |
| `MessageStore` | `chat.post_message` |
| `CardStore` | `drafts.create_*` |
| `PrincipalDirectory` | `roster.py` |
| `TraceStore` | `kn_turn_traces` via `kernos.content.traces.StoreTraces` |
| `Completion` | `summarize.summarize_messages` |
| `Clock` | `clock.py` (ICT) |

`space_id` is the room id as a string. Everything the kernel hands back to a host, such
as message and card refs, is opaque to the kernel.

### 3.3 The engine boundary — `kernos.engine` and the sidecar

`kernos/engine/base.py::Engine` is the protocol for "the thing that runs the model loop
for one turn". `PiEngine` is the production engine: it sends one `run` command to the
sidecar through `PiBridge`, forwards `agent.*` events untouched to the host's sink, hands
every `tool_call` to the host's executor and posts the `tool_result` back, and hydrates
the `turn_done` reply into a frozen `TurnResult`. `ScriptedEngine` replays a script and is
what most tests use.

The sidecar (`backend/agent_sidecar/`, Node) owns the whole Pi harness: provider and
model resolution, session and context files, event normalization, the `max_tools` and
`max_seconds` caps, narration stripping and answer assembly. Python never imports Pi.

Two app modules remain as deliberately thin shims over the framework, because their
signatures are frozen by test fakes: `app/agent.py::run_turn` builds an `EngineSpec` and
calls `PiEngine`, and `app/pi_bridge.py` configures the framework bridge with the sidecar
path, the credential name (`OPEN_ROUTER_KEY`, translated to the name Pi reads) and the
per-process singleton the chat lock and the smoke route share.

### 3.4 A turn, end to end

`app/chat.py::run_bot_turn` is the only production entry point. Since the kernel landed
its body is five lines of orchestration:

```
kernel = kernel_for(db)
spec   = kernel.resolve(room_id)                 # binding → agent → profile → published version
ctx    = TurnContext(space_id, principal, text, images, profile=spec,
                     tool_ctx=ToolContext(...), sink=LegacyAgentEventSink(emit),
                     extras={"agent": kernel.agent_for(room_id)})
async with _agent_lock:
    await kernel.pipeline_for(spec).run(ctx)     # the stages below, as plugins
await flush(ctx.pending_events, ctx.sink)        # republished cards, outside the lock
return ctx.persisted
```

The pipeline is built from the resolved profile and cached per spec. Stages run in
`PIPELINE_ORDER`; `model`, `run` and `render` must have exactly one plugin each (the
schema gate refuses a profile that breaks this). The seeded lunch profile
(`app/default_profile.py`) lists today's behaviour in today's order:

| stage | plugin | owner | what it does |
|---|---|---|---|
| context | `kernos.context.rollover` | framework | fold aged messages into memory, advance the watermark |
| context | `kernos.context.memory` | framework | load the room's long-term memory |
| context | `kernos.context.history` | framework | render the recent conversation |
| context | `kernos.context.images` | framework | carry a recently pasted bill into this turn |
| prompt | `kernos.prompt.template` | framework | render the profile's prompt template |
| prompt | `kernos.prompt.sections` | framework | assemble the user-message sections |
| model | `kernos.model.passthrough` | framework | text vs vision model from the profile |
| run | `app.run.legacy` | host | the engine turn through the frozen `agent.run_turn` |
| ↳ validate_args / validate_result | `kernos.validate.*` | framework | per tool call: sum equals total, non-negative, unique members |
| render | `kernos.render.packs` | framework | the enabled packs turn tool results into a `Body` or a draft card |
| validate | `app.validate.fabricated_commit` | host | block a reply that claims a commit no tool made |
| validate | `app.validate.unbacked_amounts` | host | warn on an amount no tool result backs |
| persist | `kernos.persist.cards` | framework | write the message or the card |
| after | `kernos.after.trace` | framework | one `kn_turn_traces` row per turn, kept 30 days |

`TurnContext` is the one mutable record every stage reads and writes; `ctx.extras` is the
scratchpad plugins use to pass per-turn state. A validator returns a `Verdict`; the runner
stops on `block` and re-raises exceptions so the host decides what an error means.

A **sub-agent** turn (an `ask_<slug>` tool executed by a manager) runs the sub's own
profile through the same runner but only `through=validate`. It never persists, so a
delegated turn cannot make a card, and it runs inside the manager's remaining time and
tool budget.

`tests/test_run_bot_turn_golden.py` pins nine byte-identical replies across this
refactor: moving the turn into plugins changed nothing a room can see.

### 3.5 Live events

The kernel emits typed `TurnEvent`s. chiatienan chooses `LegacyAgentEventSink`, which
maps them to the `agent.*` names the frontend's timeline already consumed before the
framework existed, and publishes them into the in-process `RoomHub` feeding each room's
SSE stream. Events that must wait for the writer lock, such as a superseded card whose
buttons must disappear, go to `ctx.pending_events` and are flushed after the lock is
released. A new host can pick `AguiEventSink` instead, which turns the same events into
an ordered AG-UI stream.

## 4. The content plane

The bot's configuration is data, not code. It lives in framework-owned `kn_*` tables next
to the app's own, created additively on boot (`tests/test_prod_migration.py` boots the
current code over a production-shaped database with no `kn_` tables and proves the
existing data untouched).

```
kn_businesses ─┬─ kn_sources              prompt / rule / skill / template, ETag'd
               ├─ kn_profiles ── kn_profile_versions   draft → published → superseded → retired
               │                    ▲
               └─ kn_agents ────────┘   role manager|sub, delegates_to, capabilities, max_depth
                       ▲
kn_space_bindings ─────┘   space (room) → agent, with per-binding overrides
kn_model_catalogue · kn_audit_log · kn_turn_traces · kn_change_proposals
kn_eval_cases · kn_eval_suites · kn_rubrics · kn_eval_runs
kn_collections · kn_documents                     (the data plane, §6)
```

**`ProfileSpec`** (`kernos/content/spec.py`) is the whole configuration of one agent:
persona, prompt, rules, skills, templates, models, retry, caps, builtin tools, memory,
settings, runtime, pipeline, tool packs, validation rules, eval, extensions, meta. It is
frozen and shared between turns. `stored()` excludes `runtime` (paths the host injects at
resolve time).

**Resolution.** `DbResolver` maps a room to the profile it runs: the room's binding names
an agent, the agent names a profile, the profile's *published* version is the spec. A room
with no binding runs the default business's default agent. A store with no content falls
back to the spec built from code. Results are cached by `(version_id, space_id)` and
invalidated on any store change.

**Boot seeding.** On every start `ensure_seeded` writes the businesses, their sources and
a `managed_by="boot"` profile with version 1 published, and afterwards re-publishes from
code only while the profile is still boot-managed and the stored spec differs. The first
human publish flips `managed_by` and boot never touches that profile again. Sources a
human or an agent has edited are theirs; boot never reverts them.

**Sources are upstream of versions.** A new draft snapshots the business's current
sources. A change written only into a version's spec is silently reverted by the next
draft, so every path that publishes a changed spec, the proposal path and the room editor
alike, also writes the matching sources through `kernos.content.sources.source_changes`.

**The five publish gates** (`kernos/content/gates.py`): schema (the spec validates, the
pipeline builds, plugin configs match their schemas, every template variable is known);
money safety (a money-handling profile with `bash`/`write`/`edit` needs an override
reason); probe (a changed model needs a recent successful probe in the catalogue); eval (a
profile naming suites needs a finished run of that exact content passing every blocking
grader); reflexivity (an `agent:` actor may not publish outside its `self_change_scope`
and never touches the blacklist). `NEVER_IN_SCOPE` lists paths no scope can name however it
is written: the blacklist, money-tagged rules, blocking validators, persona, meta, memory,
retry, templates.

## 5. Surfaces onto the content plane

Four doors, each with a different authority.

| door | who | how | may change |
|---|---|---|---|
| **Admin API** `/api/admin/*` | operator with `ADMIN_PASSWORD` | `kernos.api.admin_router` mounted by `app/main.py` | everything: businesses, sources, profiles, versions, publish/retire/rollback, agents, bindings, collections, eval cases/suites/rubrics/runs, proposals, catalogue and probes, traces, audit, export/import |
| **Room CMS** `/api/rooms/{id}/agent*` | any room member (read); a room **with its own binding** (write) | `app/roomcms.py` | `prompt.body`, `prompt.append`, `skills`, non-money `rules`, capped at 32 KiB; republish an earlier version |
| **`os_admin` tool pack** | an agent whose profile enables it and whose `capabilities.cms` grants verbs | `kernos/osadmin.py` | `read` its own config, traces, friction and eval results; `draft` and propose a change to its own profile or one its `manages_profiles` names; `eval`; `publish` only inside its own scope, with eval evidence, after every gate |
| **Package export/import** | operator | `kernos/content/package.py` | export a published profile as a Pi package; import creates sources plus a draft, never a publish |

The room CMS is the one door open to non-operators, and its rules are stated once at the
top of `app/roomcms.py`. The one that matters most: **editing needs a binding, not
membership.** Room creation is public and an unbound room resolves to the shared default
agent, so "member of the room in the URL" would let anyone republish the real bot. The
frontend's **Bot** tab (`frontend/src/components/chat/agent-panel.tsx`) renders the same
view for everyone and derives `can_edit` from the backend, showing two things a config
editor usually hides: whether the bot is shared with other rooms, and whether a human
edit has detached the profile from deploys.

## 6. Packs, businesses and the data plane

A **pack** (`kernos/packs.py::BasePack`) bundles tools, their rendering and any cards they
create. Registration alone does nothing: a pack produces tools only when a profile lists
it in `tool_packs`. The framework composes the enabled packs' tools into the manifest the
engine sees (`tests/test_tools_manifest.py` pins the 19 legacy tools in order).

```
packs/lunch_ledger    meals, drafts, the outcome decision and reply bodies   ─┐
packs/ledger_tools    statements, settlement, payments, the random draw      ─┼─ over ledger_core
packs/poker_ledger    game nights: buy-ins, cash-outs, house, debt edges    ─┘
app/packs/            lunch_places (restaurants, memos), room_members (member CRUD)
kernos/data           collections: {slug}_find / _upsert / _delete generated from a JSON schema
kernos/agents         delegation: ask_<sub_slug> for every sub in the agent's delegates_to
kernos/osadmin        os_admin: the CMS as tools (§5)
```

A **business** is a tenant with its own sources, profiles and agents. Two are seeded:
`lunch` and `poker`. They share `ledger_core` and the `ledger_tools` pack and nothing
else, which is what proves the framework is not lunch-shaped. A room bound to the poker
agent gets a dealer that records game nights, settled with the same VietQR flow, and the
frontend renders its `game_draft` through the generic `DraftCard`.

A **collection** is a schema-validated document type defined through the admin API. A
profile that enables the `collections` pack gets three tools per collection generated from
the definition; the schema stays in the sidecar-safe JSON Schema subset. Writes are
immediate because these are facts the room asked the agent to remember, not money.

## 7. Money safety across the boundaries

These invariants predate the framework and the framework was built to keep them. Each is
enforced in code, not in the prompt.

- **Tools own every number.** A number may flow user → model → tool once, as input, never
  tool → model → tool. `ledger_core/moneyguard.py::backed_amounts` decides which amounts a
  reply may contain; `app.validate.unbacked_amounts` warns on the rest.
- **Evidence is deliberate.** A tool result is evidence only if its pack says so
  (`evidence = True`). The `os_admin` pack, the delegation pack and every trace payload
  record a *reference* only (the executor's `_record` contract), so a past trace, a
  proposal id or a sub-agent's prose can never launder an amount into a manager's reply.
- **The model proposes, a person commits.** A meal turn ends as a pending draft card;
  the ledger is written only when a member confirms it, under the same lock as the model
  turn. Sub-agents cannot create cards at all. `app.validate.fabricated_commit` blocks a
  reply that claims a commit no tool made.
- **Bodies render from tool results.** Settlement and meal bodies are rendered
  server-side by the pack from the result dict, so the visible text cannot disagree with
  the QR amounts.
- **Configuration change is gated the same way.** An agent may draft and propose against
  a profile it manages, but publishing stays own-profile-only and behind the five gates;
  money-tagged rules, blocking validators, models, caps and the pipeline are outside every
  room's and every agent's scope.

## 8. Observability and eval

- **Traces.** `kernos.after.trace` writes one `kn_turn_traces` row per turn: a summary
  column (verdicts, tool names, timing, capped flag) plus the full trace. Six
  deterministic **friction detectors** (`kernos/friction.py`) read the summary column
  only, so scanning fifty turns loads no tool results.
- **The steward.** A seeded, off-by-default sub-agent of the lunch business whose whole
  vocabulary is the `os_admin` pack with `read` and `draft`. It reads friction, reads the
  turns behind it, drafts one change to the lunch profile and opens a proposal. Nothing
  wires `phoenix.delegates_to`, so no live room's manifest changes until an operator
  connects it. A proposal becomes a confirmable card in the room whose own agent wrote it.
- **Eval.** Cases, suites, rubrics and runs are content. A run is a job, not a request:
  `app/evalhost.py` rebuilds a fresh database per case, freezes the clock to the case's
  day and drives the *candidate* pipeline directly, never through the serving process's
  lock. Gate 4 reads the result. Graders are pack plugins; a grader that raises is
  counted, never skipped.
- **The benchmark** (`backend/bench/`) is the other half: the golden fixtures use a
  scripted engine, the bench uses the real model over the `typical` corpus and reports
  per-case drops with a noise estimate. Run it whenever the model's view changes.

## 9. Frontend

Next.js 16 / React 19, talking only to `/api/*` (the dev server rewrites to the backend,
mirroring Caddy). The room view (`components/chat/room-view.tsx`) subscribes to the SSE
stream through `hooks/use-room.ts`, which replays missed messages and merges optimistic
sends by id. The side panel holds the **Ledger**, **Memory** and **Bot** tabs. Cards are
per-kind components (`expense-draft-card`, `payment-draft-card`, `memo-card`,
`statement-card`, …) with `draft-card` as the generic fallback for any `*_draft` kind the
frontend has no design for. The PWA manifest and service worker make it installable;
icon filenames never change, so new art bumps the `?v=` on every reference.

## 10. Known gaps

Stated where they are owned (`TODO.md`, the phase plan), repeated here so the picture is
honest:

- **Builtin tools count as evidence.** `bash`, `read` and `write` are not packs, so their
  output backs a number and a bash-computed amount in prose passes `unbacked_amounts`.
  Fixing it changes live validator behaviour and needs its own phase and a benchmark run.
- **No copy-on-write per room.** A room-owned profile still shares `kn_sources`, which is
  per business, so it isolates nothing until sources are per profile. Today's answer is
  the binding plus a shared-bot notice in the Bot tab.
- **`kernos` is not yet its own repository.** The split waits for a second real host; the
  layering test and `examples/minimal_host/` are what keep it splittable.
- **Sub-agents do not appear in eval runs**, because the eval world has no agent tree.
- **Horizontal scaling** would need a redesign of the single-writer lock, the in-process
  SSE hub and the one-sidecar-per-process model.
