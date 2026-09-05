# Agent OS — a portable framework for Pi-harness agents, with the CMS as its configuration plane

**Date:** 2026-09-05 · **Status:** draft v3 — reframed as a framework after operator
feedback; §11 records decisions taken and open
**Implementation plan:** [`../plans/2026-09-05-agent-os-framework.md`](../plans/2026-09-05-agent-os-framework.md)
**Builds on:** [`2026-08-12-cursor-to-pi-harness-design.md`](2026-08-12-cursor-to-pi-harness-design.md)
(the sidecar boundary) · `TODO.md` "BIG: agent engine export/import"

## 0. What this is

A **framework** for running LLM agents on the Pi harness inside any application, with
three properties the operator asked for in this order:

1. **Every behaviour is configured by content**, and the same content is editable by
   humans and, under permission, by the agents themselves.
2. **Code is first-class**: developers extend the system through a defined turn
   pipeline with typed injection points, never by editing the core.
3. **Portable**: chiatienan is the *first host application*, not the framework. A
   second application mounts the same framework, writes its own packs, and gets the
   CMS, the data plane, the observability plane and the eval plane for free.

The name "Agent OS" is a framing, not a promise of a scheduler or isolation. It is
used because the mapping is real and it keeps the layering honest (§0.1): a small
kernel, components that each publish a configuration schema, and a content plane (the
CMS) that instantiates those schemas per business, profile and space. "The CMS
controls the setup of the OS" is exactly right, in the sense that manifests control a
cluster or `/etc` controls a Unix box: **content configures a component; it never
implements one** (§0.2).

### 0.1 Layers

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Host application        chiatienan (rooms, chat, SSE, PWA)  ·  your next app │
│                         mounts the framework, implements the host adapters    │
├────────────────────────────────────────────────────────────────────────────┤
│ Business packs          lunch_ledger · poker_ledger · …   (tools, renderers,  │
│                         draft kinds, fixtures, seeds, own tables)             │
├────────────────────────────────────────────────────────────────────────────┤
│ Domain libraries        ledger_core (members, payments, netting, QR, periods) │
│                         reusable across packs of one domain, not framework    │
├────────────────────────────────────────────────────────────────────────────┤
│ Framework  (kernos)                                                          │
│   kernel      turn pipeline, TurnContext, stages, plugins, caps, trace       │
│   registry    plugin discovery, config schemas → generated content types     │
│   engine      Engine protocol; PiEngine (bridge + sidecar) is the first      │
│   content     the CMS: sources, profiles, versions, publish, gates, proposals│
│   agents      Agent entity, delegation, capabilities                         │
│   data        Collections (schema-validated documents) + generated tools     │
│   observe     turn traces, logging, eval capture                             │
│   eval        cases, suites, graders, judge, runner, fixtures                │
│   api         mountable FastAPI router + os_admin tool pack                  │
├────────────────────────────────────────────────────────────────────────────┤
│ Boot layer              env: DB URL, DATA_DIR, provider key ref, sidecar path│
│                         code: gate enforcement, plugin blacklist, seeded      │
│                         default profile — exists before any content does     │
└────────────────────────────────────────────────────────────────────────────┘
```

Two rules keep the layers real, and both are tested (plan, Task 1.1):

- **The framework never imports a host or a pack.** `kernos` has no knowledge of
  rooms, meals, VND or Phoenix. It knows *spaces*, *principals*, *turns*, *profiles*.
- **A host never reaches around the framework.** chiatienan talks to Pi only through
  `kernos.engine`; it renders replies only through its packs.

### 0.2 The two rules that make "content controls everything" plausible

**Content configures; code implements.** An editor or agent may change *which* memory
plugin runs and its window, *which* validators fire and at what severity, *which*
tools are enabled, *which* model handles vision, *which* stage runs which plugins in
what order. None of them can change what a plugin does when it runs. That is the line
that makes an agent editing its own setup safe rather than reckless.

**Content types are generated from the registry, not hand-written per component.**
Every plugin declares a `config_schema`. That schema *is* its content type. The CMS is
a schema-driven editor: it reads the registry and, for each plugin, knows how to
render, validate, version and publish an instance of that schema. Add a plugin and its
content type exists with no CMS change — the same mechanism as custom resource
definitions in Kubernetes or code-first content types in a .NET CMS. Only the rich-text
types are hand-designed: prompt, rule, skill, prompt template, rubric. Rule of thumb:
one content type per component, never one per knob.

### 0.3 Boot layer and reflexivity

The CMS runs on the OS, so a small set of things must exist before any content does
and can never be content: the database URL and data directory, the provider key
*reference*, the sidecar path, the publish gates and their thresholds, the plugin
blacklist for self-change, and the **seeded default profile** (built from code on first
boot so a fresh install runs today's behaviour with zero content). Reflexivity follows:
an agent must not be able to change the thing that judges its change, so gates,
thresholds and the blacklist are outside every self-change scope, and an eval always
runs the *candidate* profile against the *current* suite and gates (§9).

### 0.4 Out of scope

| Out | Why |
|---|---|
| Agent UI | the operator drives the agent with AG-UI over SSE; the framework emits typed turn events and ships an AG-UI mapping (§12.4), the UI is someone else's |
| Admin identity / roles | not wanted now; the host guards `/api/admin/*` as it sees fit |
| A no-code tool builder | §4.4; code is supported as registered plugins, not as text in a form |
| Pi's terminal surface | §2 lists it as N/A |

## 1. Where this repo already is

The audit is favourable, and the reason is one design decision already taken in the
Pi port: **the sidecar takes the whole agent configuration as data, per turn.**
`agent.py:148-171` builds the `run` command and `session.js:91-124` constructs a
fresh Pi session from it:

```json
{"type":"run",
 "system":"…prompt.py text…",            "message":"…memory + history + user text…",
 "tools":[{name,description,schema}…],   "skills":[{name,description,body}…],
 "context_files":[{path,content}…],      "images":[…],
 "model":"~deepseek/…","vision_model":"qwen/…","thinking":"medium",
 "builtin_tools":["read","write","bash"],
 "max_tools":40,"max_seconds":120,
 "cwd":"/data/pi-cwd","agent_dir":"/data/pi-agent"}
```

That object **is** an agent profile. Nothing in the sidecar reads a file, an env var
or a constant for any of it except the OpenRouter base URL and key. So the CMS does
not need to teach Pi anything new: it needs to *produce this object from content
instead of from code*, per room, and to run the Python half of the turn through a
pipeline instead of through `chat.run_bot_turn`'s one long function.

What produces each field today, and where it goes:

| `run` field | Source today | Room-scoped? | Becomes |
|---|---|---|---|
| `system` | `prompt.py` — one hard-coded Vietnamese string with two variables | no | **content** — a template with named variables |
| `skills` | five `SKILL.md` files under `app/agent_skills/skills/` | no | **content** — already the right shape |
| `context_files` | `agent_skills/rules/money-safety.mdc` | no | **content** — "always-on rule" |
| `tools` | `tools.build_tools()` — 19 `CustomTool`s, one flat dict | scoped at *execution*, not at *selection* | **code** — a `ToolPack` plugin; per-tool enable/override is content |
| `model`, `vision_model`, `thinking` | env | no | **content** — from a probed model catalogue |
| `builtin_tools` | env (default `read,write,bash`) | no | **content, governed** — §9 |
| `max_tools`, `max_seconds` | env | no | **content** |
| `message` assembly | `agent._render_prompt` — hard-coded section headers | no | **code plugin** (`context` stage) with content-owned headers |
| memory policy | env `MEMORY_WINDOW_WEEKS`, `HISTORY_MAX_MESSAGES`, `IMAGE_LOOKBACK_*` | no | **content** consumed by the `memory` and `images` plugins |
| summariser prompt | `summarize._SUMMARY_PROMPT` | no | **content** |
| bot identity | env `BOT_HANDLE`; "Phoenix" inside `prompt.py` | no | **content** (`persona`) |
| reply checks | `moneyguard.unbacked_amounts` / `fabricated_commit`, inline in `chat.py:645-677` | n/a | **code plugins** at the `validate` stage, configured as `ValidationRule`s |
| post-turn rendering | `chat.py` `_settlement_body` etc., an `if/elif` chain on result type | n/a | **code** — the pack's `render` |
| draft cards | `drafts.create_draft` / `create_payment_draft`, two hard-coded kinds | n/a | **code** — the pack's `post_turn`, over a generalised draft kind |
| knowledge stores | `memory.md`, `observations.md`, `places` — per room, editable in the UI | **yes** | already content; unchanged |
| benchmark | `bench/` — corpus in Python files, graders, judge, world builder | n/a | **content + code** — cases and rubrics become content, graders and fixtures become plugins (§5.5) |

Two observations:

1. **Everything agent-shaped is global today; only the data is per room.** "Configure
   a room" means introducing the first per-room *configuration*.
2. **The repo already has the pieces of a pipeline; they are just inlined.**
   `run_bot_turn` does, in order: rollover, load memory, build history, carry images,
   run the sidecar, pick the draft path, render the body, run moneyguard, persist,
   publish superseded cards. That *is* the stage list in §4.1, written as one
   function. Phase 1 is a refactor that names the stages without changing what any
   of them does, and the benchmark proves it.

What is **not** favourable, plainly: `drafts.py` knows two draft kinds by name,
`ledger.period_balances` derives balances from *meals* and *payments* specifically,
and `chat.py` renders by `elif` on result type. A second money business cannot exist
until those three become pack-provided (§7.3).

## 2. Pi's configuration surface, classified

Each knob gets one of three fates, and only the first two reach the CMS:

| Fate | Meaning | Examples |
|---|---|---|
| **CMS-native** | Text or a value an editor can own safely. Becomes a content field. | system prompt, skills, rules, model choice, thinking level, caps, compaction, bot handle |
| **Code** | A plugin behind a pipeline injection point (§4). The CMS enables, orders and configures it; a developer wrote it against a declared interface. | tools / tool packs, validators, graders, extension hooks, providers |
| **Not applicable** | Only meaningful with a human at a terminal. Documented, deliberately absent. | themes, keybindings, TUI mode, steering/follow-up queues, `/share` |


Verified against the installed `@earendil-works/pi-coding-agent@0.84.1`
(`dist/**/*.d.ts` and `docs/`), not from memory. Where a claim comes from a type or
a doc file it is named.

### 2.1 Model, provider, credentials

| Pi concept | Where it lives in Pi | Fate | CMS equivalent |
|---|---|---|---|
| Provider (`KnownProvider` + custom) | `models.json` `providers.{name}` · `pi.registerProvider()` · `ProviderConfig` {`baseUrl`, `apiKey`, `api`, `headers`, `authHeader`, `models[]`, `oauth`} | **Code** | `Provider` record: name, api type, base URL, header template. **The key never enters the CMS.** It is an env-var *reference* (`$OPEN_ROUTER_KEY`), exactly Pi's own `apiKey: "$VAR"` convention. |
| Model (`Model<Api>`) | `id`, `name`, `api`, `reasoning`, `input: ["text","image"]`, `cost`, `contextWindow`, `maxTokens`, `thinkingLevelMap`, `samplingParams`, `compat` (incl. `compat.openRouterRouting` {`order`, `only`, `ignore`, `allow_fallbacks`, `require_parameters`, `data_collection`, `zdr`, `max_price`, `sort`}), `headers` | **CMS-native** | `Model` record with the same fields, **plus** `probe` (§7): last `bench.probe_models` verdict against this repo's real tool schemas. A model that has not passed the probe cannot be selected for a money profile. |
| Default model / provider / thinking | `settings.json` `defaultProvider`, `defaultModel`, `defaultThinkingLevel` | **CMS-native** | on the profile: `model`, `vision_model`, `thinking_level` (`off\|minimal\|low\|medium\|high\|xhigh\|max` per `pi-agent-core`; Pi clamps to what the model's `thinkingLevelMap` allows) |
| `thinkingBudgets` {minimal, low, medium, high} | `settings.json` | CMS-native (advanced) | optional per-profile token budgets |
| Scoped models / model cycling (`scopedModels`, `enabledModels`, `cycle_model`) | SDK + settings | **N/A** — Ctrl+P in a terminal | The vision/text pair is our own scoped-model routing (`resolveModel`); expose as two fields, not a list. |
| `auth.json`, OAuth `/login` | `~/.pi/agent/auth.json` | **N/A** | Server bot uses env-referenced keys only. OAuth-subscription providers are out. |
| `retry` {enabled, maxRetries, baseDelayMs, provider{timeoutMs, maxRetries, maxRetryDelayMs}} (defaults `true`, 3, 2000 ms; provider 0 retries, 60 s max delay) | `settings.json` | CMS-native (advanced) | profile `retry` block. Interacts with `max_seconds`: a retry budget larger than the turn cap is wasted; validate. |
| `transport`, `httpProxy`, `httpIdleTimeoutMs`, `websocketConnectTimeoutMs` | `settings.json` | **N/A** (deployment) | stays in `.env` / compose |

### 2.2 System prompt and context

| Pi concept | Where it lives | Fate | CMS equivalent |
|---|---|---|---|
| System prompt (`systemPrompt`, `systemPromptOverride`, `--system-prompt`, `.pi/SYSTEM.md`) | `DefaultResourceLoaderOptions.systemPrompt` / `systemPromptOverride(base)` | **CMS-native** | `Prompt` entity: Markdown body with `{{variables}}`. The sidecar already passes it via `systemPromptOverride` (`session.js:106`). |
| Append system prompt (`appendSystemPrompt[]`, `APPEND_SYSTEM.md`) | `appendSystemPromptOverride` | CMS-native | profile-level `append_sections[]` — the natural place for *room* overrides ("this room speaks English") so the base prompt is shared and the room adds, never forks. |
| Context files (`AGENTS.md`, `CLAUDE.md`, `.pi/AGENTS.md`, `noContextFiles`) | `agentsFilesOverride` → `[{path, content}]`, loaded into every system prompt | **CMS-native** | `Rule` entity ("always-on"). Exactly what `money-safety.mdc` is today. Ordered list per profile. |
| Skills (`SKILL.md`, frontmatter `name`, `description`, `disable-model-invocation`, plus spec fields `license`, `compatibility`, `metadata`, experimental `allowed-tools`; dirs `.pi/skills`, `~/.pi/agent/skills`, `.agents/skills`; packages; settings `skills[]`; `--skill`) | `Skill` {name, description, filePath, baseDir, disableModelInvocation}; `formatSkillsForPrompt` puts name+description in the prompt as XML and expects the model to `read` the body; `system-prompt.js` emits that XML **only when the `read` tool is active** | **CMS-native**, with one caveat | `Skill` entity: name, description, body, `always_inline: bool`. Caveat is §3.1 of the Pi design: Pi's *native* skill mechanism needs a real `filePath` and a `read` tool. This repo ships skill bodies inline as context files instead (`buildAgentsFiles`), which is what makes them reach the model with builtins off. The CMS keeps both delivery modes as a per-skill switch: **inline** (today's behaviour, costs tokens on every turn) or **discoverable** (Pi-native, needs `read` enabled — a governed choice). |
| Prompt templates (`.pi/prompts/*.md`, `/name`, `$1 $@ ${1:-x}`, `argument-hint`) | `PromptTemplate` {name, description, argumentHint, content} | **CMS-native**, repurposed | This is exactly what chat *slash commands* are. `/clear` is hard-coded in `chat.py:49`; a `PromptTemplate` entity gives each business its own `/commands` (`/summary`, `/rules`) with the same `$@` substitution Pi already implements. Commands that need *code* (like `/clear`'s watermark write) stay a catalogue item, not a template. Pi can do the expansion itself: `AgentSession.prompt(text, {expandPromptTemplates: true})`, so the sidecar needs no template engine of its own. |
| `AGENTS.md` discovery up the directory tree | `noContextFiles`, project trust | **N/A** | the sidecar runs in a synthetic `cwd`; nothing is discovered from disk on purpose |

### 2.3 Tools

| Pi concept | Where it lives | Fate | CMS equivalent |
|---|---|---|---|
| Built-in tools `read`, `bash`, `edit`, `write` (+ `grep`, `find`, `ls` via `createReadOnlyTools`) | `CreateAgentSessionOptions.tools[]` allowlist, `excludeTools[]`, `noTools: "all"\|"builtin"` | **CMS-native, governed** | profile `builtin_tools[]`. Default **empty** for any profile that has a money pack enabled (§7); the UI shows *why* when an editor turns `bash` on. Today's env default is `read,write,bash`, i.e. weaker than the Pi design's `tools: []` — the CMS is the chance to make the safe value the default. |
| Custom tool (`ToolDefinition`: `name`, `label`, `description`, `promptSnippet`, `promptGuidelines[]`, `parameters` (TypeBox), `executionMode`, `execute()`, renderers) | `customTools[]` / `pi.registerTool()` | **Code** | `ToolPack` (code) exposes tools; the profile stores per tool: `enabled`, `description_override`, `prompt_guidelines[]`, `execution_mode`. **Bodies and schemas never come from content** (D3: the tool owns the numbers; a content-edited schema would let an editor silently remove `required: ["total"]`). |
| Tool result shape (`content[]` blocks + `details`) | `AgentToolResult` | Catalogue | fixed by the pack |
| `tool_call` / `tool_result` extension events (block, rewrite, approve) | `ExtensionAPI.on("tool_call")` | **Code** (policy hooks) | a small set of sidecar-shipped policies the CMS can switch on per profile: e.g. *deny-list by name*, *max calls per tool per turn*, *require confirmation* — parameterised, never authored |
| `user_bash` event, `bash` RPC command, `shellPath`, `shellCommandPrefix` | settings / RPC | **N/A** | no human shell in a room |

### 2.4 Session, memory, compaction

| Pi concept | Where it lives | Fate | CMS equivalent |
|---|---|---|---|
| Session persistence (JSONL entries: header, message, model_change, thinking_level_change, compaction, branch_summary, custom, custom_message, label, session_info; tree/branching; `SessionManager.create\|open\|continueRecent\|inMemory`; `sessionDir`) | `docs/session-format.md`, `SessionManager` | **N/A as storage**, reused as concept | This repo deliberately uses `SessionManager.inMemory` per turn and keeps continuity in its **own** memory (`memory.md` + `build_history`). Pi's session file is the wrong store for a multi-user room (it models one operator's conversation). Keep ours; the CMS configures *our* memory policy. |
| Compaction {enabled (true), reserveTokens (16384), keepRecentTokens (20000)}; `session_before_compact` custom summariser; the summary template (Goal / Constraints / Progress / Key decisions / Next steps) | `settings.json`, `docs/compaction.md` | **CMS-native, remapped** | Our equivalent is the **rollover** (`_maybe_rollover`) and `/clear`, whose knobs are `MEMORY_WINDOW_WEEKS`, `HISTORY_MAX_MESSAGES` and the summariser prompt. A `MemoryPolicy` block on the profile: `history_max_messages`, `window_weeks`, `summary_prompt`, `image_lookback_{messages,minutes}`, and a **vision context budget** (the 262k vs 1M window problem from the Pi design §12 — the history window must shrink on image turns). |
| Branch summary {reserveTokens, skipPrompt}, fork, tree, labels, `doubleEscapeAction`, `treeFilterMode` | settings / RPC | **N/A** | one linear room history |
| Steering / follow-up queues (`steeringMode`, `followUpMode`, RPC `steer`, `follow_up`) | settings / RPC | **N/A now**, note for later | A room *does* have "user sent another message while the bot is thinking". Today that is serialised by `_agent_lock`. If a business ever wants mid-turn steering, this is the Pi feature to reach for; not a v1 field. |

### 2.5 Extensions and packages

| Pi concept | Where it lives | Fate | CMS equivalent |
|---|---|---|---|
| Extension (`.pi/extensions/*.ts`, `extensionFactories`, `ExtensionAPI`: 30+ events, `registerTool`, `registerCommand`, `registerShortcut`, `registerFlag`, `registerMessageRenderer`, `registerProvider`, `sendMessage`, `appendEntry`, `exec`, `setActiveTools`, `setModel`) | `dist/core/extensions/types.d.ts` | **Code** | An `Extension` in this system is a **sidecar-shipped factory with a JSON config schema**. The CMS lists the ones the sidecar exposes, and stores `{enabled, config}` per profile. Authoring TypeScript in the CMS is explicitly rejected (§0). Candidates worth shipping first: *tool-call policy* (`tool_call` → `{block, reason}` — Pi has **no built-in permission or sandbox layer**, `docs/security.md`; this hook is the only gate), *provider header injection* (`before_provider_headers`), *per-turn prompt/message injection* (`before_agent_start` → `{systemPrompt?, message?}`), *turn telemetry* (`agent_end` stats to our log line). |
| Package (`package.json` `pi` manifest: `extensions[]`, `skills[]`, `prompts[]`, `themes[]`; sources `npm:`/`git:`/path; `autoload`, filters; `pi install`) | `docs/packages.md`, `PackageSource` | **Code + export format** | Two uses. (1) *Import*: a Pi package is a legitimate way for a developer to hand the CMS a bundle of skills/prompts — the CMS reads `skills/**/SKILL.md` and `prompts/*.md` from a package and creates content from them. (2) *Export*: a published profile exports **as a Pi package** (`SKILL.md` files, `prompts/*.md`, `AGENTS.md`, `settings.json` with model/thinking/compaction), so the same agent can be run under stock `pi` for debugging or moved between deployments. This is the "engine export/import" item in `TODO.md`, given a concrete file format. |
| Themes (`.pi/themes/*.json`), keybindings (`.pi/keybindings.json`), `tuiMode`, `editorPaddingX`, `outputPad`, `autocompleteMaxVisible`, `showHardwareCursor`, `fullscreenScrollbar`, `markdown.mermaid`, `externalEditor`, `collapseChangelog`, `quietStartup`, `terminal.*`, `images.*` | settings | **N/A** | terminal presentation. `images.blockImages` / `autoResize` are the only ones with a server analogue, and this repo already does its own image sanitising in `images.py`. |
| Telemetry / update checks (`enableInstallTelemetry`, `enableAnalytics`, `PI_OFFLINE`, `PI_SKIP_VERSION_CHECK`) | settings / env | **N/A** (deployment) | set once in the container env |
| Project trust (`defaultProjectTrust`, `project_trust` event) | settings | **N/A** | the synthetic cwd is never "a project" |

### 2.6 Runtime modes

| Pi concept | Fate | Note |
|---|---|---|
| Interactive TUI, `-p` print mode, `--mode json` | N/A | |
| `--mode rpc` (commands: `prompt`, `steer`, `follow_up`, `abort`, `new_session`, `get_state`, `set_model`, `set_thinking_level`, `compact`, `set_auto_compaction`, `set_auto_retry`, `bash`, `get_session_stats`, `export_html`, `switch_session`, `fork`, `get_messages`, `get_commands`, …) | **N/A, by prior decision** | The Pi design §2 rejected RPC mode because it cannot host Python tools ("The RPC protocol does not support host-side tool registration"). The SDK sidecar stays. The CMS does not change the engine boundary. |
| SDK `createAgentSession` (`cwd`, `agentDir`, `modelRuntime`, `model`, `thinkingLevel`, `scopedModels`, `noTools`, `tools`, `excludeTools`, `customTools`, `resourceLoader`, `sessionManager`, `settingsManager`, `sessionStartEvent`) | **the integration point** | every CMS-native field above lands in one of these options or in the `DefaultResourceLoader` overrides. Nothing else is needed. |
| `SettingsManager.inMemory(partial)` | **the mechanism for settings** | Rather than writing `settings.json` to `agent_dir`, the sidecar builds `SettingsManager.inMemory({...profile.settings})` per turn. In-memory means no disk state to drift, same reason skills are inline. |

### 2.7 What the classification leaves as CMS-native, in one list

Persona & channel (handle, display name, language) · system prompt template · append
sections · rules (always-on) · skills (inline / discoverable) · prompt templates
(slash commands) · model + vision model + thinking level (+ budgets) · retry ·
built-in tools (governed) · tool enablement + description overrides + guidelines ·
turn caps · memory policy (history window, rollover weeks, summary prompt, image
lookback, vision budget) · extension toggles + config · knowledge seeds
(places / observations, per room, already exists).

That is the whole content model. Everything else Pi has is either code (catalogue)
or a terminal (absent).

## 3. Vocabulary

Framework words first; the chiatienan word each maps to is in the last column.

| Word | Means | Holds | In chiatienan |
|---|---|---|---|
| **Space** | a tenant and conversation scope: the unit a turn happens in and data is partitioned by | data | room |
| **Principal** | whoever sent the message: id, display name, capabilities | identity | member |
| **Agent** | a named actor with a role (`manager` or `sub`), bound to one **profile version**, with the sub-agents it may delegate to and its `capabilities` | identity + delegation | the bot (Phoenix) |
| **Profile** | versioned, published configuration: prompt, rules, skills, templates, models, caps, pipeline, packs, validation, eval suites | behaviour | today's `prompt.py` + skill files + env |
| **Business** | a template: the packs, plugins and default profiles a space of this kind is created from, plus seed data | blueprint | "lunch" |
| **Pipeline** | the ordered stages a turn passes through; each stage has a typed interface | the kernel's shape | `run_bot_turn` |
| **Plugin** | a code module registered against one stage, with an id, a version and a JSON-Schema config | code | the inlined steps of `run_bot_turn` |
| **Tool pack** | the plugin kind that contributes tools, renderers, draft kinds, fixtures and seeds for one domain | code | `tools.py` + `drafts.py` + the render chain |
| **Engine** | what actually runs the model loop for one turn given a spec, message and tools | code | the Pi sidecar |
| **Host adapters** | the interfaces a host implements so the framework can read history, memory and knowledge and emit events | code | `chat.build_history`, `memory.py`, `realtime.py` |

```
Business ──creates──▶ Space ──manager──▶ Agent ──▶ ProfileVersion (published snapshot)
                                           │ delegates_to[]        │
                                           └─▶ Agent (sub) ───────┘ (own profile version)
ProfileVersion.spec = content fields + pipeline {stage: [{plugin, config}]}
                    + tool_packs[{pack, tools{…}}] + validation[] + eval_suites[]
```

## 4. The kernel: the turn pipeline

### 4.1 Stages

This is the list that needs sign-off before Phase 1, because every plugin is written
against it. Stage names are stable identifiers; the Python protocol per stage is in
§4.2. Left column is the Python kernel; the right column is the sidecar, where Pi's
own extension events are the injection points.

```
Python kernel (one turn)                          Node sidecar (inside the Pi run)
──────────────────────────────────────────         ─────────────────────────────────────
 1 resolve   room → Agent → ProfileVersion          before_agent_start   final prompt/message tweak
 2 gate      may this message start a turn?         tool_call            allow / block / rewrite args
 3 context   memory · history · images · knowledge  tool_result          patch / annotate result
 4 prompt    render system + message from content   before_provider_*    headers, request body
 5 model     text vs vision · thinking · caps        agent_end            stats → telemetry
 6 run       sidecar turn; per tool call ⤵
      6a validate_args   JSON Schema + ValidationRules(scope=tool_args)
      6b execute         the pack's tool body
      6c validate_result ValidationRules(scope=tool_result)
 7 validate  reply-level rules (moneyguard, narration, language)
 8 render    body + attachments from structured results   ← exactly one pack owns this
 9 persist   messages, drafts, superseded cards, events
10 after     rollover · eval capture · telemetry · sub-agent bookkeeping
```

Stages 1, 5, 6 and 8 are **single-owner** (the kernel, the kernel, the engine, the
pack). Stages 2, 3, 4, 7, 9, 10 and 6a/6c are **ordered lists of plugins**; the
profile decides which and in what order. Today's behaviour is the pre-built set:

| Stage | Pre-built plugin (today's code, relocated) | Config it takes |
|---|---|---|
| gate | `mention_gate` (`chat.mentions_bot`), `slash_command` (`/clear`, prompt templates) | handle, aliases |
| context | `long_term_memory` (`memory.load_memory` + `_maybe_rollover`), `recent_history` (`build_history`), `image_lookback` (`recent_images`), `knowledge` (places/observations for the suggest path) | window weeks, max messages, lookback, vision budget |
| prompt | `template_prompt` (renders `prompt.body` + `append`), `sections_message` (`_render_prompt`) | section headers |
| validate | `unbacked_amounts`, `fabricated_commit`, `strip_narration`¹ | severity: warn / block, replacement body |
| after | `rollover_summary`, `turn_log_line`, `eval_capture` (records the turn as a candidate eval case) | summary prompt, sampling rate |

¹ `strip_narration` runs in the sidecar today (`turn.js`); it stays there as a sidecar plugin.
Listing it here is about *configuration*, not location.

### 4.2 Interfaces

Deliberately small. A plugin is a module exposing one object:

```python
class Plugin(Protocol):
    id: str                          # "chiatienan.memory.long_term", stable, namespaced
    stage: Stage                     # Literal["gate","context","prompt","validate_args", …]
    config_schema: dict              # JSON Schema; the CMS validates config against it
    handles_money: bool = False      # §9 governance
    def run(self, ctx: TurnContext, config: dict) -> TurnContext | Verdict: ...
```

`TurnContext` is one mutable dataclass carried through the stages — room, sender,
message, images, resolved profile, the `memory`/`history`/`knowledge` texts, the
rendered `system`/`message`, the chosen model, the `TurnResult` after stage 6, the
rendered `body`/`attachments`, and an append-only `trace` of which plugin did what
(this trace is what `GET /api/admin/rooms/{id}/turns/{turn_id}` shows, and what eval
capture stores). A `validate*` plugin returns a `Verdict(ok, severity, reason, patch)`
instead of a context; `block` short-circuits to a configured reply, `warn` logs.

A **tool pack** is a plugin with a wider surface:

```python
class ToolPack(Protocol):
    id: str;  handles_money: bool
    def tools(self, ctx: ToolContext) -> dict[str, CustomTool]: ...        # the bodies
    def draft_kinds(self) -> dict[str, DraftKind]: ...                     # e.g. "expense_draft" → commit(), patch schema
    def render(self, result) -> tuple[str, dict] | None: ...               # body, attachments
    def balance_contributions(self, session, room_id, window) -> list[DebtEdge]: ...
    def fixtures(self) -> dict[str, FixtureStep]: ...                      # eval world building, §5.5
    def seed(self, session, room_id) -> None: ...                          # new room of this business
    def models(self) -> list[type[Base]]: ...                              # its own tables, if any (§5.3)
```

`balance_contributions` is the one method that makes a second money business
possible: `ledger.period_balances` stops reading `meals` directly and instead sums
`DebtEdge`s from every enabled pack. `money.net_transfers`, QR building,
settlements, payments and the roster stay in the core, shared by every pack.

Sidecar plugins are Pi extension factories (`(pi, config) => void`) exported from a
registry module; the `run` command names them with their config and `session.js`
passes the resolved list as `extensionFactories`. Same shape, other language.

### 4.3 Discovery and versioning

Plugins live **on disk, in the repo or in installed Python packages**, and register
through an entry-point group (`chiatienan.plugins`) plus a module-level `PLUGIN`
object. On startup the engine builds the registry; `GET /api/admin/registry` lists
every plugin with its stage, config schema and `handles_money`. A profile version
references plugins by id; publishing fails if an id is not in the registry or its
config does not validate. A plugin id is a contract: changing behaviour means a new
id (`…long_term@2`), and old published versions keep running the old one until they
are republished — the same snapshot discipline as skill bodies.

### 4.4 Why not store code in the database

It was considered, because "code support in the CMS" can be read that way. Rejected
for this system, for three reasons that are about *money* rather than taste:

1. **No review boundary.** A plugin on disk goes through a pull request, CI and the
   benchmark; a plugin in a text field goes live on save.
2. **No type contract.** The value of a defined pipeline is that a plugin can be
   tested against `TurnContext` in isolation. Dynamically `exec`'d code has no such
   test until it runs against a real room.
3. **D3.** The one invariant this codebase protects is that the model never computes
   money. A hot-loaded plugin with DB access is a second place that invariant could
   silently break.

What *is* reasonable later, and is left as a Phase 7+ question: a small, sandboxed
**expression language** for `ValidationRule` predicates (`sum(cash_outs) == sum(buy_ins) + house`)
so a business can add an invariant without a deploy. Expressions, not statements;
no I/O; evaluated by the engine.

### 4.5 What an editor can still not do

Write a tool body, change a tool's parameter schema, add a stage. Those are pull
requests. Everything else about how a turn behaves — which plugins, in which order,
with which config, which tools are on, which invariants must hold — is content.


### 4.6 The Engine protocol and the host adapters

The kernel is host-agnostic because everything host-shaped sits behind a protocol:

```python
class Engine(Protocol):                      # stage 6
    async def run(self, spec: EngineSpec, message: str, images: list[Image],
                  tools: list[ToolSpec], call_tool: ToolExecutor,
                  emit: EventSink) -> TurnResult: ...
# PiEngine = today's pi_bridge.py + agent_sidecar/, generalised. EngineSpec is the
# `run` command of §1 minus tools/message/images. A second engine (a direct
# provider loop, another harness) implements the same protocol; nothing above
# stage 6 notices.

class HistorySource(Protocol):               # used by the recent_history plugin
    def render(self, space_id, *, since_id, limit, before_id) -> str: ...
class MemoryStore(Protocol):                 # used by long_term_memory / rollover
    def load(self, space_id) -> str; def append(self, space_id, section) -> None
    def watermark(self, space_id) -> int; def set_watermark(self, space_id, value) -> None
class KnowledgeSource(Protocol):             # optional; what `knowledge.py` provides
    def snapshot(self, space_id) -> dict: ...
class EventSink(Protocol):                   # turn events → SSE / AG-UI / logs
    async def emit(self, event: TurnEvent) -> None: ...
class MessageStore(Protocol):                # stage 9 persistence of the reply and cards
    def post(self, space_id, *, author, kind, body, attachments) -> MessageRef: ...
```

chiatienan implements each with what it has (`build_history`, `memory.py`,
`knowledge.py`, `RoomHub`, `chat.post_message`). A new host implements the same six
interfaces and nothing else to run an agent. The framework ships an in-memory
implementation of every adapter for tests and for the minimal example host (§12.3).

## 5. The content plane (the CMS)

Storage is SQLite tables with JSON columns, versioned by snapshot-on-publish: editors
edit *sources* (prompt, rule, skill rows); publishing copies the referenced bodies into
one `spec` JSON, so a room runs a snapshot that a later edit cannot change mid-turn.

### 5.0 Storage and generated types

The framework owns its tables under its own SQLAlchemy `Base` with an `kn_` prefix
(`kn_profiles`, `kn_profile_versions`, …), created by `kernos.bind(engine)` next
to the host's tables in the same database — one file for SQLite hosts, one schema for
others. Nothing in `kernos` references a host table; the join to a host's tenant is
the opaque `space_id` string the host passes in.

Plugin configuration is not a table per plugin. A profile version's `spec.pipeline`
holds `{plugin, version, config}` triples, validated at save against the plugin's
`config_schema` from the registry (§0.2). The admin API exposes each plugin's schema
so a generic editor can render it; the framework does not ship an editor.

### 5.1 Behaviour entities

```
businesses               id, slug, name, tool_packs[], plugins_allowed[], seed JSON
agents                   id, business_id, slug, name, role manager|sub, profile_id,
                         delegates_to[] (agent ids), max_depth, created_at
agent_profiles           id, business_id, name, published_version_id NULL
agent_profile_versions   id, profile_id, version, status, spec JSON, created_at, published_at, note
prompts / rules / skills / prompt_templates
                         id, business_id, slug, title, body TEXT, frontmatter JSON, etag
spaces (host-owned)      the host stores manager_agent_id + agent_overrides JSON on its own tenant row
                         and passes them to resolve(); chiatienan adds both columns to `rooms`
model_catalogue          provider, model_id, name, input[], context_window, max_tokens, cost, probe JSON
```

The published **spec** is the `run` command of §1 minus the per-turn parts, i.e.
`persona {handle, aliases, name, language}`, `prompt {body, append[]}`, `rules[]`,
`skills[] {name, description, body, delivery inline|discoverable}`, `templates[]`,
`models {text, vision, thinking, thinking_budgets}`, `retry`, `caps {max_tools,
max_seconds}`, `builtin_tools[]`, `memory {…}`, `settings {…}` (passthrough to
`SettingsManager.inMemory`) — plus four blocks v2 adds:

```jsonc
"pipeline":   { "gate": [{"plugin":"chiatienan.gate.mention","config":{}}],
                "context": [{"plugin":"chiatienan.memory.long_term","config":{"window_weeks":10}},
                            {"plugin":"chiatienan.history.recent","config":{"max_messages":200,"vision_max_messages":60}},
                            {"plugin":"chiatienan.images.lookback","config":{"messages":10,"minutes":120}}],
                "validate":[{"plugin":"chiatienan.money.unbacked_amounts","config":{"severity":"warn"}},
                            {"plugin":"chiatienan.money.fabricated_commit","config":{"severity":"block"}}],
                "after":   [{"plugin":"chiatienan.memory.rollover","config":{"summary_prompt":"…"}}] },
"tool_packs": [ {"pack":"lunch_ledger","tools":{"pick_random":{"enabled":false}}} ],
"validation": [ …ValidationRule refs, §5.4… ],
"eval":       { "suites": ["lunch-typical"], "gate": {"tool_selection":1.0,"ledger_state":1.0} }
```

### 5.2 Agent entity

`agents.role = manager` is what a room binds to. `delegates_to` lists the sub-agents
the manager may call; the engine turns each into a tool (§6). A sub-agent has its own
profile, therefore its own prompt, tools, model and pipeline — a cheap text model for
"summarise the week" next to a vision model for "read this bill" is the obvious use.

### 5.3 Database content type — `Collection`

Two ways a business gets data, and the design is honest about which is which:

| | Pack-owned tables | `Collection` (CMS-defined) |
|---|---|---|
| Defined by | code (`ToolPack.models()`, SQLAlchemy, additive migrations as today) | content: name, JSON Schema, indexed fields |
| Stored in | its own tables | one `documents` table: `room_id, collection, doc_id, data JSON, created/updated` |
| Tools | hand-written, arithmetic inside | **generated**: `{collection}_find`, `{collection}_upsert`, `{collection}_delete`, schema-validated |
| Use when | numbers are derived from rows (balances, splits, netting) | facts the agent should remember and look up (a rota, a wishlist, a set of house rules) |
| Money? | yes | **no** — `handles_money` is false and stays false; the generated tools refuse numeric aggregation by construction |

Poker's games and buy-ins are pack-owned (§7). A "who brings cards next Friday" rota
is a `Collection`. Both are content-visible: the admin API lists a pack's tables
read-only and a collection's documents read-write.

### 5.4 Validation content type — `ValidationRule`

A rule is *a validator plugin + config + scope*, attached to a profile:

```jsonc
{ "id": "chips-conserved", "scope": "tool_args", "tool": "propose_game",
  "plugin": "chiatienan.validate.sum_equals",
  "config": { "left": "$.buy_ins[*].amount", "right": ["$.cash_outs[*].amount", "$.house"], "tolerance": 0 },
  "on_fail": "return_error" }            // the tool answers {ok:false, error} so the model asks — tools.py:8 convention
```

Scopes: `tool_args` (before execute), `tool_result` (after), `reply` (stage 7),
`content` (at save time — template variables, frontmatter, tool ids). Pre-built
validators: `json_schema`, `sum_equals`, `non_negative`, `member_exists`,
`unique_members`, `unbacked_amounts`, `fabricated_commit`, `no_narration`,
`language_is`. `on_fail` for `reply` scope is `warn` or `block_with(body)`; for tool
scopes it is `return_error`, which keeps the "ask, don't guess" behaviour the model
already knows.

### 5.5 Eval content types

The benchmark under `bench/` already has every concept; v2 makes them content so a
business ships with its own regression suite and publishing can run it.

```
eval_cases    id, business_id, slug, message, images[], actor, day,
              world JSON [ {fixture: "meal_confirmed", …}, … ],     ← fixture steps a pack provides
              expect JSON { tools[], args{}, ledger{}, empty? }, tags[], source manual|captured, review bool
eval_suites   id, business_id, slug, case_ids[], graders[{plugin, config}], judge {model, rubric_id}
rubrics       id, business_id, slug, body TEXT                       ← the prose-quality rubric is content
eval_runs     id, suite_id, profile_version_id, started, finished, records JSON, summary JSON, status
```

Mapping to what exists: `bench.corpus` → `eval_cases` (the `typical` corpus is imported
on Phase 4 as the lunch suite, ids preserved); `bench.graders.grade_*` → grader plugins
(`tool_selection`, `ledger_state`, `prose_quality`, `cost_latency`); `bench.judge`
rubric string → `rubrics`; `bench.world.build_world` → `ToolPack.fixtures()` — the pack
knows how to put a room into "meal confirmed" or "game recorded" state, the engine
only sequences steps and freezes the clock. `eval_capture` (stage 10) writes real
turns as `source: captured, review: true` cases, which is how the prod corpus was
meant to be built and never was (the Pi plan, Task 7 Step 6).

## 6. Agents and sub-agents

Pi has no sub-agent facility (`docs/usage.md`, confirmed by the inventory), and the
Pi design already decided orchestration lives in Python where tools and validation
are. So delegation is **a generated tool and a nested pipeline run**:

- For each `delegates_to` entry the engine adds a tool `ask_<sub-slug>(task: string)`
  to the manager's tool list, with the sub-agent's `description` from its profile.
- Executing it runs stages 1–8 for the sub-agent in the **same room** with the same
  `ToolContext`, `depth+1`, and caps that are the *minimum* of the manager's remaining
  budget and the sub-agent's own (`max_tools`, `max_seconds`). `max_depth` on the
  agent stops recursion; a sub-agent's `delegates_to` is honoured only within it.
- The tool returns `{text, results}` — the sub-agent's final text **and** its
  structured tool results. The manager's model sees the text. The engine merges
  `results` into the manager's `TurnResult.tools` so the pack's `render` and the
  reply validators see every number a tool produced, whoever called it.

That last point is D3 applied across agents. A sub-agent's prose is a source of
"unbacked" numbers exactly like the manager's, and the same `unbacked_amounts`
validator checks the final body against the *union* of tool results. What a
sub-agent may not do is `propose_*` a draft on the manager's behalf silently: draft
creation happens in the manager's `post_turn`, from structured results, so a card is
always attributable to a tool call in the trace.

Sub-agent turns are recorded in the trace as nested spans and shown in the room's
live timeline as child events (`agent.sub.started/finished` under the parent
`turn_id`) — additive to the frozen `agent.*` set, so the existing UI ignores them
and an AG-UI mapping can nest them.

## 7. The second business: a poker / card-game ledger

### 7.1 Why this one

It shares the *core* with lunch — members, cash payments between members, "who pays
whom" netting, VietQR, periods, statements — and differs in the *domain*: money does
not flow from one payer to many eaters; it flows through a **pot**. Each game night,
every player buys in (money in) and cashes out (money out); a player's net is
`cash_out − buy_in`; the table's nets sum to zero, less whatever the house/rake took.
That is a different ledger shape with a hard invariant, which is exactly what tests
whether the pack interface is real.

### 7.2 The pack, sketched

| | |
|---|---|
| Tables (pack-owned) | `games (id, room_id, played_on, note, voided…)`, `game_entries (game_id, member_id, buy_in, cash_out)`, optional `house` amount on the game |
| Tools | shared from core: `find_members`, `propose_payment`, `settle_period`, `member_statement`, `get_period_summary`, member CRUD. New: `propose_game(entries[{member, buy_in, cash_out}], house?, day_word?)` → draft card; `void_game`; `game_history` |
| Draft kind | `game_draft` — editable per-player buy-in / cash-out, commit writes `games` + `game_entries` |
| Balance contributions | for each game, edges from net losers to net winners, proportional (a deterministic rule the pack owns, same spirit as `split_with_guests`); `money.net_transfers` then minimises transfers as it does today |
| Validation | `chips-conserved`: Σ buy_in = Σ cash_out + house (tolerance 0, `on_fail: return_error` so the model asks "who is short?"); `non_negative` on both; `unique_members` |
| Skills | `record-game`, `poker-balances`; rules: the same `money-safety` rule verbatim — it is business-agnostic |
| Renderers | `game_result` body ("#12 — 5 players, pot 2,500,000đ • winners / losers") built server-side from the result dict |
| Eval | golden games with known nets; a settle case; an ambiguous-cash-out case that must ask |
| Seed | none (no places); a `Collection` "house-rules" is a natural optional extra |

### 7.3 What poker forces out of the host and into `ledger_core`

Everything poker and lunch share is not framework and not host: members with bank
details, cash payments between members, debt edges and FIFO payment application,
`net_transfers`, VietQR, periods, statements, settlements, the two-step draft card.
That is a **domain library**, `ledger_core`, that both packs import. Extracting it is
most of Phase 3's work and is what makes the pack interface honest — a pack is what a
business *adds* to the domain, and the domain is what two businesses *share*.

### 7.4 What poker forces in the kernel and the host — the real deliverable of Phase 6

1. `drafts.py` stops knowing `expense_draft` and `payment_draft` by name; a draft
   carries `kind`, and `commit_any` dispatches to the pack's `DraftKind.commit`.
2. `ledger.period_balances` becomes `Σ pack.balance_contributions(...)` over enabled
   packs, then payments FIFO as today. `lunch_ledger` contributes what `build_debt_edges`
   builds now, byte-identical.
3. `chat.py`'s render chain becomes `pack.render(result)` — the first pack that
   claims a result type wins, in profile order.
4. The eval world builder takes fixture steps from packs.

Each of those is a refactor with a byte-identical lunch path and the benchmark as the
oracle, which is why they are scheduled *before* the poker pack exists.

## 8. AI-ready: an agent can drive the CMS

The operator's requirement: an agent may **update itself, evaluate itself, and read
its own logs** — drive the CMS — *if permitted*. Pi already gestures at this: its
docs open with "pi can create skills, ask it to build one", and `ExtensionAPI` lets
an extension `setModel`, `setThinkingLevel` and `setActiveTools` mid-session. Those
are ephemeral and ungated. This design makes the same capability **durable and gated**,
and it reuses the one pattern this codebase trusts most.

### 8.1 The rule: the agent proposes, the gate or a human commits

Every ledger write here goes through a draft card: `propose_meal` never records, a
person taps Confirm. Self-modification follows the identical rule. An agent may
create a **draft version** of a profile and run evals against it; it may not move
`published_version_id` unless its capabilities say so *and* the publish gates (§9)
pass — and the gates apply to an agent-made publish exactly as to a human one,
unconditionally. There is no "the agent is confident" path around the model probe or
the money-safety check.

### 8.2 The `os_admin` tool pack

The headless API of Phase 2 is exposed to agents as a tool pack, so the CMS is
operable from inside a turn with no second integration:

| Tool | What it does | Needs capability |
|---|---|---|
| `cms_get_profile()` | the agent's own resolved spec, with version and sources | `cms.read` |
| `cms_get_turns(since, filter)` / `cms_get_turn_trace(turn_id)` | the per-turn trace (§4.2): plugins run, validators fired, tool calls, model, cost, `capped`, moneyguard warnings | `cms.read` |
| `cms_get_eval_results(suite, version)` | latest run summaries and per-case verdicts | `cms.read` |
| `cms_draft_change(kind, slug, body, rationale)` | edit a prompt / rule / skill / template / memory setting / warn-level validation rule into a **new draft version**; returns a diff | `cms.draft` |
| `cms_run_eval(suite, version)` | run a suite against a draft; returns verdicts and cost | `cms.eval` |
| `cms_propose_publish(version, rationale)` | opens a **change proposal** for a human | `cms.draft` |
| `cms_publish(version)` | publish directly — only within the *self-change scope* (§8.3) and only after gates | `cms.publish` |
| `cms_add_eval_case(turn_id, expect, tags)` | turn a real turn (usually a failure it just saw) into a `review: true` regression case | `cms.eval` |
| `cms_log(level, message, data)` | write a structured line into the room's turn trace and the app log | `cms.read` |

The pack is `handles_money: false` and contains no arithmetic; it is HTTP-free
(direct Python calls into the same modules the admin API uses), so nothing new
listens on a port.

### 8.3 Capabilities and the self-change scope

Permission is per **agent**, not per room, because it is the agent that acts:

```jsonc
"capabilities": { "cms": ["read", "draft", "eval"] }          // default for a manager
"capabilities": { "cms": ["read", "draft", "eval", "publish"], // a steward agent
                  "self_change_scope": ["prompt.append", "skills", "rules", "templates",
                                        "memory", "validation.warn"] }
```

Inside `self_change_scope` an agent with `cms.publish` may publish **its own** profile
after the gates pass. Outside it — `builtin_tools`, `models`, `tool_packs`,
`pipeline`, any validation rule with `severity: block`, any other agent's profile —
the most an agent can ever do is open a proposal. The scope list is content, so the
operator can widen it per agent; it can never include the blacklist above, which is
enforced in code. Every agent-made change carries `actor: agent:<slug>` in the audit
log and the rationale the agent gave.

### 8.4 What a proposal looks like

A `change_proposal` row (draft version, rationale, diff, the eval run it cites,
status) — and, in this app, **a card in the room**, because the room chat is the UI
we have: "Phoenix proposes to change skill `record-meal` (+3 −1 lines): *bill photos
without names were being itemised; adds the even-split rule*. Eval: 105/105 tool
selection, 60/60 ledger. **Approve · Reject · View diff**". Approve publishes through
the gates; the card is a `RoomMessage` of a new kind, rendered by the `os_admin`
pack like any other draft. A headless client sees the same proposal at
`GET /api/admin/proposals`.

### 8.5 Self-eval and self-review loops

Two loops, both bounded:

- **In-turn**: the agent may call `cms_draft_change` → `cms_run_eval` → adjust, up to
  `max_self_iterations` (default 3) and a token/cost budget per turn, both content.
  Each eval run costs real model calls; the budget is what stops "one more try".
- **Scheduled**: a *steward* agent (role `sub`, or a dedicated manager for an ops
  room) runs on a schedule with a fixed brief: read the last N turns' traces, list
  moneyguard warnings, `capped` turns and eval failures, propose at most one change
  with a rationale and an eval run attached. This is the `eval_capture` plugin's
  natural consumer, and it is how the prod corpus finally gets built with a reviewer
  in the loop.

### 8.6 Logging, made queryable for the agent

The turn **trace** (§4.2) is the log: which plugins ran, every validator verdict, tool
calls with args and results, model, tokens, cost, elapsed, `capped`, sub-agent spans.
It is stored per turn (`turn_traces` table, retention configurable) and exposed both to
humans (`GET /api/admin/rooms/{id}/turns/{turn_id}`) and to agents (`cms_get_turn_trace`).
The existing one-line `[agent] turn … done` log stays as the human-readable summary;
`cms_log` lets an agent add structured lines of its own. Nothing here is a second
observability stack: the trace is what eval capture, proposals and the admin timeline
all read.

### 8.7 What this adds to the content model and the phases

```
agents            + capabilities JSON, + max_self_iterations, + self_change_scope[]
change_proposals  id, agent_id, profile_version_id, rationale, diff JSON, eval_run_id,
                  status pending|approved|rejected|auto_published, decided_by, decided_at
turn_traces       id, room_id, turn_id, agent_id, started, finished, trace JSON, summary JSON
```

Phase **9** (after 2, 4 and 7): the `os_admin` pack, capabilities, proposals and the
proposal card, the two loops, and a steward brief as a shipped prompt template.

## 9. Governance — what publishing checks

No admin identity, by decision. `audit_log.actor` records the caller's self-reported
name header until that changes. Publishing a version runs, in order:

1. **Schema validation** — spec shape; every plugin id in the registry and its config
   valid against `config_schema`; every tool id in an enabled pack; template variables
   known; skills with `delivery: discoverable` but no `read` in `builtin_tools`.
2. **Money-safety gate** — any enabled pack or plugin with `handles_money` **and**
   `bash`/`write`/`edit` in `builtin_tools` requires an `override_reason` in the
   publish call, written to the audit log.
3. **Model probe gate** — `models.text` and `models.vision` must have a passing
   `probe` against the enabled packs' real schemas within N days.
4. **Eval gate** — the suites named in `spec.eval.suites` run against the draft;
   publish is refused if a money grader (`tool_selection`, `ledger_state`) drops
   below `spec.eval.gate`. Prose graders report, never block.
5. **Reflexivity** — an agent-initiated publish is refused if the diff touches any
   gate threshold, any `severity: block` rule, the plugin blacklist, `builtin_tools`,
   `models`, `tool_packs` or `pipeline` (§0.3); those are proposals only, whatever the
   agent's capabilities say. The eval in gate 4 runs the *candidate* profile against
   the *current* suite.
6. **Rollback** — `published_version_id` moves; the previous version stays
   publishable so rollback is one call.

## 10. Phases

The task-by-task plan is in
[`../plans/2026-09-05-agent-os-framework.md`](../plans/2026-09-05-agent-os-framework.md).
In one table:

| # | Deliverable | Behaviour change | Proof |
|---|---|---|---|
| **1** | `kernos` package: kernel (pipeline, `TurnContext`, stages, plugins, trace), registry, `Engine` protocol with `PiEngine` (today's bridge + sidecar, generalised), host adapter protocols; chiatienan's `run_bot_turn` becomes the pipeline with today's steps as pre-built plugins; a **seeded default profile built from code**; import-rule test | **none** | 986 backend (+1 skipped) + 65 sidecar tests unchanged; benchmark equality when a key is available; the resolved profile byte-equals today's `run` command |
| **2** | Content plane: `kn_` tables, sources, versions, publish with gates 1–3 and 5, resolver space → agent → version, mountable admin router, plugin schemas exposed | opt-in per space | API tests; a bound space runs the edit, an unbound one does not |
| **3** | `ToolPack` protocol; `ledger_core` extracted; `lunch_ledger` pack; generalised drafts, balance contributions, pack render | none | benchmark equality; a stub pack runs end-to-end in a test space |
| **4** | Observe + eval: `kn_turn_traces`, eval types, `bench` imported as the lunch suite, graders and fixtures as plugins, gate 4, `eval_capture` | none | imported suite reproduces `pi-typical-r3.json`; a captured turn appears as a `review: true` case |
| **5** | Data plane: `Collection` + generated tools + `kn_documents` | opt-in | schema-validated CRUD through the model; aggregation refused |
| **6** | `poker_ledger` pack and business | new business only | its suite green; lunch suite unchanged |
| **7** | Agents + sub-agents | opt-in | merged results pass `unbacked_amounts` |
| **8** | AI-ready: `os_admin` pack, capabilities, self-change scope, proposals + card, loops | opt-in per agent | steward scenario in §8.5 passes end to end |
| **9** | Portability: minimal example host, AG-UI event mapping, Pi-package export/import, sidecar extension registry, packaging for PyPI/npm | none | the example host runs a "hello" business with no chiatienan code on its path |

Phase 1 is the refactor everything stands on and must ship with the benchmark
unchanged to the digit.

## 11. Decisions

**Decided by the operator (2026-09-05):**

- A room links to **one agent** (its manager); a profile backs **many** rooms.
- Agents may invoke **sub-agents** → §6.
- **Code is in scope**, as pipeline plugins with defined injection points → §4.
- **Database, validation and eval are content types** → §5.3–5.5.
- **No admin identity / authoring roles** for now.
- Agent UI is **out**: AG-UI over SSE, owned elsewhere.
- Second business: **poker / card-game money ledger** → §7.
- **AI-ready**: an agent may update, evaluate and log itself through the CMS when
  permitted → §8; the agent proposes, the gates or a human commit.
- **Framework, not app feature**: Agent OS is a portable framework (§0, §12); chiatienan
  is its first host; the CMS is its configuration plane.
- **Review before code**: a second reviewer passes over this design and the plan before
  Phase 1 starts (plan, Task 0).

**Still open, needed before Phase 1 starts:**

1. **The stage list in §4.1.** Names and the single-owner / list distinction are the
   contract every plugin is written against. Confirm or rename.
2. **Plugins on disk (recommended, §4.3) vs. code in the database (§4.4).** The
   recommendation is firm; say if you disagree and why.
3. **Sub-agent limits** — proposed `max_depth: 2` and caps = min(parent remaining,
   own). Enough for manager → specialist; not enough for agent trees.
4. **Poker invariant** — treat rake/tips as an explicit `house` line (recommended,
   keeps Σ exact) or allow a tolerance? Tolerance is how a wrong cash-out becomes a
   silently absorbed number.
5. **`Collection` storage** — one `documents` JSON table, room-scoped (recommended),
   vs. a table per collection. The generated tools never aggregate, so the JSON
   table costs nothing that matters.
6. **Package name** — `kernos` is the working name (free on PyPI at the time of
   writing; `agentos` and `agentkernel` are taken). Rename freely before Phase 9 publishes anything.
7. **Self-publish** — is an agent ever allowed to publish without a human? Proposed:
   yes, but only with `cms.publish`, only inside its `self_change_scope`, and only
   after every gate passes; everything else is a proposal card. The alternative
   (never) is safer and slower; say which you want as the default for managers.

## 12. Packaging and portability

### 12.1 Repository layout (Phase 1 onward)

```
backend/
  kernos/            the framework — Python package, no imports from app/ or packs/
    kernel/  registry/  engine/  content/  agents/  data/  observe/  eval/  api/
    adapters/memory.py    in-memory HistorySource/MemoryStore/… for tests and examples
  kernos_sidecar/    the Node runtime (today's agent_sidecar/, generalised; no money comments)
  ledger_core/            domain library: members, payments, netting, QR, periods, drafts
  packs/
    lunch_ledger/         business pack; imports ledger_core and kernos
    poker_ledger/
  app/                    chiatienan host: FastAPI routes, rooms, chat, SSE, host adapters, plugins/
  examples/minimal_host/  Phase 9: a 100-line host proving the framework runs without chiatienan
```

Same repo, separate packages, one dependency direction:
`app → packs → ledger_core → kernos`, and `app → kernos`. A test walks the
import graph and fails on any edge pointing the other way. Extraction to its own
repository and to PyPI/npm is a `git subtree split` when a second host exists, not
before — a framework with one user is a guess, and the plan schedules the second host
(§12.3) as the moment to split.

### 12.2 What a host implements

Six protocols (§4.6) and two decisions: how it maps its tenant to `space_id`, and
where it mounts the admin router. Everything else is content and packs. The framework
ships: the kernel, `PiEngine`, the content plane and its API, the data plane, traces,
eval, the `os_admin` pack, the AG-UI mapping, and in-memory adapters.

### 12.3 The minimal example host

A single-file FastAPI app with one space, one agent, a "hello" pack with one tool, the
in-memory adapters, and the `os_admin` pack enabled. It exists to prove the layering
(no chiatienan module on its import path) and to be the template for the operator's
next application. It is the acceptance test of Phase 9.

### 12.4 Events and AG-UI

The kernel emits typed `TurnEvent`s: `run.started`, `text.delta`, `tool.start`,
`tool.result`, `run.finished`, `run.error`, `sub.started`, `sub.finished`,
`validation.warned`, `validation.blocked`. chiatienan's `EventSink` maps them to the
frozen `agent.*` SSE names its UI consumes. The framework also ships an
`agui.EventSink` that maps the same events to AG-UI's `RUN_STARTED`,
`TEXT_MESSAGE_CONTENT`, `TOOL_CALL_START/ARGS/END`, `RUN_FINISHED`, `RUN_ERROR`, so a
new host gets an AG-UI-compatible stream by choosing that sink.

### 12.5 Versioning

`kernos` follows semver. The stage list (§4.1), the six host protocols, the
`Plugin`/`ToolPack` protocols, `TurnContext`'s public fields, the `EngineSpec` shape and
the `kn_` schema are the public surface; a breaking change to any of them is a major
version. Plugin ids carry their own version (`…long_term@2`), so a framework minor can
add a plugin without moving anything a published profile references.

## Appendix A — Pi configuration inventory (verified)

Sources: `node_modules/@earendil-works/pi-coding-agent@0.84.1` — `dist/core/sdk.d.ts`,
`dist/core/resource-loader.d.ts`, `dist/core/settings-manager.d.ts`,
`dist/core/extensions/types.d.ts`, `dist/core/skills.d.ts`,
`dist/core/prompt-templates.d.ts`, `dist/modes/rpc/rpc-types.d.ts`; `@earendil-works/pi-ai`
`dist/types.d.ts`; `docs/{settings,models,skills,prompt-templates,packages,extensions,
compaction,session-format,environment-variables}.md`.

### A.1 `Settings` (`settings-manager.d.ts:65`)

`lastChangelogVersion` · `defaultProvider` · `defaultModel` · `defaultThinkingLevel` ·
`transport` · `steeringMode: "all"|"one-at-a-time"` · `followUpMode` · `theme` ·
`compaction {enabled, reserveTokens, keepRecentTokens}` ·
`branchSummary {reserveTokens, skipPrompt}` ·
`retry {enabled, maxRetries, baseDelayMs, provider{timeoutMs, maxRetries, maxRetryDelayMs}}` ·
`hideThinkingBlock` · `showCacheMissNotices` · `externalEditor` · `shellPath` ·
`quietStartup` · `defaultProjectTrust: "ask"|"always"|"never"` · `shellCommandPrefix` ·
`npmCommand[]` · `collapseChangelog` · `enableInstallTelemetry` · `enableAnalytics` ·
`trackingId` · `packages: PackageSource[]` · `extensions[]` · `skills[]` · `prompts[]` ·
`themes[]` · `enableSkillCommands` · `terminal {showImages, imageWidthCells,
clearOnShrink, showTerminalProgress}` · `images {autoResize, blockImages}` ·
`enabledModels[]` · `doubleEscapeAction` · `treeFilterMode` ·
`thinkingBudgets {minimal, low, medium, high}` · `editorPaddingX` · `outputPad` ·
`autocompleteMaxVisible` · `showHardwareCursor` · `markdown {codeBlockIndent, mermaid}` ·
`warnings {anthropicExtraUsage}` · `sessionDir` · `httpProxy` · `httpIdleTimeoutMs` ·
`websocketConnectTimeoutMs` · `tuiMode` · `fullscreenScrollbar`.
Defaults worth knowing: `defaultThinkingLevel medium`, `compaction {true, 16384, 20000}`,
`retry {true, 3, 2000}`, `steeringMode`/`followUpMode one-at-a-time`, `transport auto`,
`images.autoResize true` (2000×2000). Scopes: global `~/.pi/agent/settings.json`, project
`.pi/settings.json` (only when trusted; `defaultProjectTrust` and `httpProxy` are global-only). `SettingsManager.create | fromStorage | inMemory(partial)`.

### A.2 `Model<Api>` (`pi-ai/dist/types.d.ts:661`) and `models.json`

`id, name, api, provider, baseUrl, reasoning, thinkingLevelMap?, input: ("text"|"image")[],
cost {input, output, cacheRead, cacheWrite}, contextWindow, maxTokens, samplingParams?,
headers?, compat?`. `ThinkingLevel = minimal|low|medium|high|xhigh|max`;
`ModelThinkingLevel = off|ThinkingLevel`. `models.json`: `providers.{name}.{baseUrl,
api, apiKey ("$ENV" | "!command" | literal), headers, authHeader, models[]}`; supported
`api`: `openai-completions`, `openai-responses`, `azure-openai-responses`,
`openai-codex-responses`, `anthropic-messages`, `bedrock-converse-stream`, `google-*`.
Per-model defaults: `contextWindow 128000`, `maxTokens 16384`, `input ["text"]`.
Providers may also carry `modelOverrides: {modelId: partial}` for built-in models, and
`cost.tiers[]` for volume pricing. Credentials: `~/.pi/agent/auth.json` =
`Record<providerId, {type:"api_key", key, env?} | {type:"oauth", …}>`, resolution order
`--api-key` → `auth.json` → env var → `models.json apiKey`; catalogue cache
`~/.pi/agent/models-store.json`.

### A.3 `CreateAgentSessionOptions` (`sdk.d.ts:10`)

`cwd, agentDir, modelRuntime, model, thinkingLevel, scopedModels[{model, thinkingLevel}],
noTools: "all"|"builtin", tools[] (allowlist), excludeTools[] (denylist), customTools[],
resourceLoader, sessionManager, settingsManager, sessionStartEvent`.

### A.4 `DefaultResourceLoaderOptions` (`resource-loader.d.ts:67`)

`cwd, agentDir, settingsManager, eventBus, additionalExtensionPaths[],
additionalSkillPaths[], additionalPromptTemplatePaths[], additionalThemePaths[],
extensionFactories[], noExtensions, noSkills, noPromptTemplates, noThemes,
noContextFiles, systemPrompt, appendSystemPrompt[], extensionsOverride(base),
skillsOverride(base), promptsOverride(base), themesOverride(base),
agentsFilesOverride(base) → {agentsFiles: [{path, content}]}, systemPromptOverride(base),
appendSystemPromptOverride(base)`.

### A.5 Skills (`skills.d.ts`, `docs/skills.md`)

Frontmatter `name` (≤64, `[a-z0-9-]`), `description` (≤1024; missing ⇒ not loaded),
`license`, `compatibility`, `metadata`, `allowed-tools` (experimental),
`disable-model-invocation` (Agent Skills standard, leniently validated; unknown keys
ignored, first name wins on collision). `Skill {name, description, filePath, baseDir, sourceInfo,
disableModelInvocation}`. Locations: `~/.pi/agent/skills`, `~/.agents/skills`,
`.pi/skills`, `.agents/skills` (trusted projects, ancestors to repo root), packages,
`settings.skills[]`, `--skill`. Injected as XML name+description
(`formatSkillsForPrompt`); body fetched by the model via `read`; `/skill:name [args]`
forces it (`enableSkillCommands`).

### A.6 Prompt templates (`prompt-templates.d.ts`, `docs/prompt-templates.md`)

`PromptTemplate {name, description, argumentHint?, content, sourceInfo, filePath}`;
frontmatter `description`, `argument-hint`; substitution `$1..$N`, `$@`/`$ARGUMENTS`,
`${N:-default}`, `${@:-default}`, `${@:N}`, `${@:N:L}`. Locations: `~/.pi/agent/prompts`,
`.pi/prompts`, packages, `settings.prompts[]`, `--prompt-template`.

### A.7 `ToolDefinition` (`extensions/types.d.ts:343`)

`name, label, description, promptSnippet?, promptGuidelines?[], parameters (TypeBox),
constrainedSampling?, renderShell?, prepareArguments?, executionMode? "sequential"|"parallel",
execute(toolCallId, params, signal, onUpdate, ctx) → {content[], details}, renderCall?,
renderResult?`. Built-ins: `read, bash, edit, write` (default), `grep, find, ls`
(`createReadOnlyTools`).

### A.8 `ExtensionAPI` (`extensions/types.d.ts:866`)

Events: `project_trust, resources_discover, session_start, session_info_changed,
session_before_switch, session_before_fork, session_before_compact, session_compact,
session_shutdown, session_before_tree, session_tree, context, before_provider_request,
before_provider_headers, after_provider_response, before_agent_start, agent_start,
agent_end, agent_settled, turn_start, turn_end, message_start, message_update,
message_end, tool_execution_start, tool_execution_update, tool_execution_end,
model_select, thinking_level_select, tool_call, tool_result, user_bash, input`.
Methods: `registerTool, registerCommand, registerShortcut, registerFlag, getFlag,
registerMessageRenderer, registerMarkdownTransformer, registerEntryRenderer, sendMessage,
sendUserMessage, appendEntry, setSessionName, getSessionName, setLabel, exec,
getActiveTools, getAllTools, setActiveTools, getCommands, setModel, getThinkingLevel,
setThinkingLevel, registerProvider(name, ProviderConfig), unregisterProvider, events`.

### A.9 Packages (`docs/packages.md`)

`package.json` `pi: {extensions[], skills[], prompts[], themes[]}` (globs, `!` excludes);
sources `npm:pkg@ver`, `git:host/user/repo@ref`, `https://…`, `ssh://…`, local paths;
`PackageSource` object form `{source, autoload, extensions[], skills[], prompts[],
themes[]}`; `pi install|remove|list|update`; installs to `~/.pi/agent/{npm,git}` or
`.pi/{npm,git}`; `-e` for a one-run trial.

### A.10 Sessions and compaction (`docs/session-format.md`, `docs/compaction.md`)

JSONL entries: `SessionHeader, SessionMessageEntry, ModelChangeEntry,
ThinkingLevelChangeEntry, CompactionEntry, BranchSummaryEntry, CustomEntry,
CustomMessageEntry, LabelEntry, SessionInfoEntry`; tree via parent ids;
`SessionManager.create|open|continueRecent|inMemory`. Compaction triggers on
`contextWindow − reserveTokens`, keeps `keepRecentTokens`, summary template with
Goal / Constraints / Progress / Key decisions / Next steps / Critical context;
`session_before_compact` may supply a custom summary.

### A.11 RPC commands (`rpc-types.d.ts:14`) — for the record, not used

`prompt {message, images, streamingBehavior}, steer, follow_up, abort, new_session,
get_state, set_model, cycle_model, get_available_models, set_thinking_level,
cycle_thinking_level, get_available_thinking_levels, set_steering_mode,
set_follow_up_mode, compact {customInstructions}, set_auto_compaction, set_auto_retry,
abort_retry, bash, abort_bash, get_session_stats, export_html, switch_session, fork,
clone, get_fork_messages, get_entries, get_tree, get_last_assistant_text,
set_session_name, get_messages, get_commands`.

### A.12 Environment (`docs/environment-variables.md`)

Read by Pi: `PI_CODING_AGENT_DIR`, `PI_CODING_AGENT_SESSION_DIR`, `PI_PACKAGE_DIR`,
`PI_OFFLINE`, `PI_SKIP_VERSION_CHECK`, `PI_TELEMETRY`, `PI_CACHE_RETENTION`,
`HTTP(S)_PROXY`, provider keys (`OPENROUTER_API_KEY` — this repo maps
`OPEN_ROUTER_KEY` to it in `pi_bridge.py`). Set by Pi for child processes:
`PI_CODING_AGENT`, `PI_SESSION_ID`, `PI_SESSION_FILE`, `PI_PROVIDER`, `PI_MODEL`,
`PI_REASONING_LEVEL`.
