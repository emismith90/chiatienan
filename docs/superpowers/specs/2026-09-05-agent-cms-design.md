# A headless CMS for Pi-harness agents, hosted in chiatienan — Design

**Date:** 2026-09-05 · **Status:** draft for review — decisions in §10 are open
**Builds on:** [`2026-08-12-cursor-to-pi-harness-design.md`](2026-08-12-cursor-to-pi-harness-design.md)
(the sidecar boundary) · `TODO.md` "BIG: agent engine export/import"

## 0. What is being asked, restated

Two questions, in the order the ask put them:

**(a)** Take *everything* the Pi harness can be configured with, understand what each
knob *is*, and decide what a CMS field for it would look like — or why there is none.

**(b)** Evaluate this repository as the host for that CMS: an admin side that can
configure everything about a **room**, up to and including standing up a *new
business* (a bot that is not about lunch money) without touching the code paths
that exist today.

One pushback before anything else, because it shapes the whole design:

> **"All configuration Pi supports" is the wrong bar for a CMS, and hitting it would
> make the CMS worse.** Roughly a third of Pi's surface configures an interactive
> terminal (themes, keybindings, cursor, scrollbar, changelog collapse). A server bot
> has no terminal, so those fields would be dead UI. Another third is *code*
> (extensions in TypeScript, tool bodies) — a CMS that lets an editor type code and
> ship it to a process that owns a money ledger is not a CMS, it is remote code
> execution with a nicer form. So the inventory in §2 covers **all of it**, but each
> knob gets one of three fates, and only the first becomes a CMS field.

| Fate | Meaning | Examples |
|---|---|---|
| **CMS-native** | Text or a value an editor can own safely. Becomes a content field. | system prompt, skills, rules, model choice, thinking level, caps, compaction, bot handle |
| **Catalogue** | Code that developers ship; the CMS only *enables, orders and parameterises* it. | tools / tool packs, extension hooks, providers |
| **Not applicable** | Only meaningful with a human at a terminal. Documented, deliberately absent. | themes, keybindings, TUI mode, steering/follow-up queues, `/share` |

## 1. Where this repo already is

The ask says "look at this repo as candidate". The audit is favourable, and the
reason is one design decision already taken in the Pi port: **the sidecar takes the
whole agent configuration as data, per turn.** `agent.py:148-171` builds the `run`
command and `session.js:91-124` constructs a fresh Pi session from it:

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
instead of from code*, per room. That is a much smaller project than "build a CMS
for Pi", and it is the thing that makes this repo a good candidate.

What produces each field today, and how hard it is to move behind content:

| `run` field | Source today | Room-scoped? | Moves to content? |
|---|---|---|---|
| `system` | `prompt.py` — one hard-coded Vietnamese string with two variables (`sender`, `today`) | no | **yes** — a template with named variables |
| `skills` | five `SKILL.md` files under `app/agent_skills/skills/` | no | **yes** — already the right shape (frontmatter `name`/`description` + body) |
| `context_files` | `agent_skills/rules/money-safety.mdc` | no | **yes** — "always-on rule" content type |
| `tools` | `tools.build_tools()` — 19 `CustomTool`s, one flat dict | scoped at *execution* (`ToolContext.room_id`), not at *selection* | **catalogue** — enable/disable + description override per profile; bodies stay code |
| `model`, `vision_model`, `thinking` | env `PI_MODEL`, `PI_VISION_MODEL`, `PI_THINKING` | no | **yes** — from a probed model catalogue |
| `builtin_tools` | env `PI_BUILTIN_TOOLS` (default `read,write,bash`) | no | **yes, governed** — see §7 |
| `max_tools`, `max_seconds` | env | no | **yes** |
| `message` assembly | `agent._render_prompt` — section headers are hard-coded Vietnamese | no | **yes** — the section titles are content too |
| memory policy | env `MEMORY_WINDOW_WEEKS`, `HISTORY_MAX_MESSAGES`, `IMAGE_LOOKBACK_*` | no | **yes** |
| summariser prompt | `summarize._SUMMARY_PROMPT` constant | no | **yes** |
| bot identity | env `BOT_HANDLE`; the name "Phoenix" and its origin story are inside `prompt.py` | no | **yes** |
| post-turn rendering | `chat.py` `_settlement_body` etc. — server-side bodies per tool-result type | n/a | **catalogue** — a tool pack ships its renderers |
| knowledge stores | `memory.md`, `observations.md`, `places` — per room, already editable in the UI | **yes** | already content; the CMS reuses `knowledge.py` |

Two observations from that table:

1. **Everything agent-shaped is global today; only the *data* is per room.** Every
   room runs the same bot with the same tools. "Configure a room" therefore means
   introducing the first per-room *configuration*, not extending an existing one.
2. **The repo already has half a CMS.** The knowledge panel (`knowledge.py`, the
   `PATCH /api/rooms/{id}/observations|memory` routes, `knowledge-panel.tsx`) is an
   editor for per-room content with etag concurrency. The admin side proposed here
   is the same pattern applied to *behaviour* instead of *facts*.

What is **not** favourable, stated plainly:

- The admin surface is one password header (`require_admin`) guarding room creation.
  There is no admin identity, no audit trail, no roles. A CMS that can change which
  model handles money needs at least "who changed what, when" (§7).
- `chat.py` renders tool results into room bodies by `if/elif` on result type
  (`chat.py:611-622`). A "new business" with different tools has nowhere to plug in
  its own renderers without editing that chain. §5.4 fixes this.
- `tools.build_tools` returns all 19 tools as one unit. There is no notion of a
  *pack*, so "enable only member tools" is not expressible today.

## 2. Pi's configuration surface, classified

Verified against the installed `@earendil-works/pi-coding-agent@0.84.1`
(`dist/**/*.d.ts` and `docs/`), not from memory. Where a claim comes from a type or
a doc file it is named.

### 2.1 Model, provider, credentials

| Pi concept | Where it lives in Pi | Fate | CMS equivalent |
|---|---|---|---|
| Provider (`KnownProvider` + custom) | `models.json` `providers.{name}` · `pi.registerProvider()` · `ProviderConfig` {`baseUrl`, `apiKey`, `api`, `headers`, `authHeader`, `models[]`, `oauth`} | **Catalogue** | `Provider` record: name, api type, base URL, header template. **The key never enters the CMS.** It is an env-var *reference* (`$OPEN_ROUTER_KEY`), exactly Pi's own `apiKey: "$VAR"` convention. |
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
| Custom tool (`ToolDefinition`: `name`, `label`, `description`, `promptSnippet`, `promptGuidelines[]`, `parameters` (TypeBox), `executionMode`, `execute()`, renderers) | `customTools[]` / `pi.registerTool()` | **Catalogue** | `ToolPack` (code) exposes tools; the profile stores per tool: `enabled`, `description_override`, `prompt_guidelines[]`, `execution_mode`. **Bodies and schemas never come from content** (D3: the tool owns the numbers; a content-edited schema would let an editor silently remove `required: ["total"]`). |
| Tool result shape (`content[]` blocks + `details`) | `AgentToolResult` | Catalogue | fixed by the pack |
| `tool_call` / `tool_result` extension events (block, rewrite, approve) | `ExtensionAPI.on("tool_call")` | **Catalogue** (policy hooks) | a small set of sidecar-shipped policies the CMS can switch on per profile: e.g. *deny-list by name*, *max calls per tool per turn*, *require confirmation* — parameterised, never authored |
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
| Extension (`.pi/extensions/*.ts`, `extensionFactories`, `ExtensionAPI`: 30+ events, `registerTool`, `registerCommand`, `registerShortcut`, `registerFlag`, `registerMessageRenderer`, `registerProvider`, `sendMessage`, `appendEntry`, `exec`, `setActiveTools`, `setModel`) | `dist/core/extensions/types.d.ts` | **Catalogue** | An `Extension` in this system is a **sidecar-shipped factory with a JSON config schema**. The CMS lists the ones the sidecar exposes, and stores `{enabled, config}` per profile. Authoring TypeScript in the CMS is explicitly rejected (§0). Candidates worth shipping first: *tool-call policy* (`tool_call` → `{block, reason}` — Pi has **no built-in permission or sandbox layer**, `docs/security.md`; this hook is the only gate), *provider header injection* (`before_provider_headers`), *per-turn prompt/message injection* (`before_agent_start` → `{systemPrompt?, message?}`), *turn telemetry* (`agent_end` stats to our log line). |
| Package (`package.json` `pi` manifest: `extensions[]`, `skills[]`, `prompts[]`, `themes[]`; sources `npm:`/`git:`/path; `autoload`, filters; `pi install`) | `docs/packages.md`, `PackageSource` | **Catalogue + export format** | Two uses. (1) *Import*: a Pi package is a legitimate way for a developer to hand the CMS a bundle of skills/prompts — the CMS reads `skills/**/SKILL.md` and `prompts/*.md` from a package and creates content from them. (2) *Export*: a published profile exports **as a Pi package** (`SKILL.md` files, `prompts/*.md`, `AGENTS.md`, `settings.json` with model/thinking/compaction), so the same agent can be run under stock `pi` for debugging or moved between deployments. This is the "engine export/import" item in `TODO.md`, given a concrete file format. |
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

## 3. Vocabulary: room, profile, business

Three words the design uses precisely, because the ask uses "room" and "business"
interchangeably and they must not be:

- **Room** — what exists today: a group of members, a ledger, a chat, its own
  memory files. A *tenant*. Rooms hold **data**.
- **Agent profile** — a published, versioned configuration that says how the bot
  behaves: prompt, skills, rules, models, tools, caps, memory policy. Profiles hold
  **behaviour**. Many rooms can share one profile.
- **Business** — a profile *plus* the tool packs and renderers it depends on, plus a
  set of defaults for the data a new room of that kind starts with (seed places,
  seed rules). "Lunch ledger" is one business; "office equipment lending" would be
  another. A business is a **template a room is created from**, not something a room
  can be switched to later (its tools write different tables).

So "configure everything of a room" decomposes into: pick the room's profile (or
give it a private one), add room-level *append sections* and knowledge, and manage
its members — the last two already exist.

```
Business (template)  ──creates──▶  Room (tenant, data)
     │                                  │  agent_profile_id
     └── default AgentProfile ◀─────────┘  + room-level append sections
             │ versions (draft → published)
             ├── Prompt, Rules[], Skills[], PromptTemplates[]
             ├── models, thinking, caps, retry, memory policy
             ├── ToolPack refs + per-tool overrides   (code, catalogued)
             └── Extension refs + config              (code, catalogued)
```

## 4. Content model

Concrete enough to implement, small enough to fit the single-process SQLite the repo
runs. Storage is SQLite tables with JSON columns; not files. Reason: the profile has
to be versioned, published atomically, and joined to `rooms` — three things the
existing `memory.md`-style file stores are bad at and a table is good at. Skill and
rule *bodies* are text columns; on export they become files (§6).

```
businesses            id, slug, name, description, tool_packs JSON[], default_profile_id,
                      seed JSON {places[], observations[]}, created_at
agent_profiles        id, business_id, name, published_version_id NULL, created_at
agent_profile_versions
                      id, profile_id, version INT, status draft|published|retired,
                      spec JSON  (the whole resolved spec, §4.1),
                      created_by, created_at, published_at, note
prompts / rules / skills / prompt_templates
                      id, business_id, slug, title, body TEXT, frontmatter JSON,
                      updated_by, updated_at          — the *editable* source
rooms                 + agent_profile_id NULL (NULL → business default)
                      + agent_overrides JSON  {append_sections[], handle?, language?}
admin_users           id, email, role owner|editor|viewer, password_hash, created_at
audit_log             id, actor, action, entity, entity_id, before JSON, after JSON, at
model_catalogue       provider, model_id, name, input[], context_window, max_tokens,
                      cost JSON, reasoning, probe JSON {ok, checked_at, schemas[], notes}
```

A `version.spec` is a *snapshot*: publishing copies the referenced prompt/rule/skill
bodies into the spec so a later edit to a skill does not change what a published room
runs until someone publishes again. Editors edit sources; rooms run snapshots.

### 4.1 The spec — what one published version contains

This is the CMS's output and the sidecar's input. It is the `run` command from §1
with the per-turn parts (`message`, `images`, `cwd`) removed and the per-room parts
resolved at dispatch time.

```jsonc
{
  "persona":   { "handle": "phoenix", "aliases": ["bot"], "name": "Phoenix", "language": "vi" },
  "prompt":    { "body": "Bạn là **{{persona.name}}** …", "append": ["…room-level…"] },
  "rules":     [ { "slug": "money-safety", "content": "…" } ],
  "skills":    [ { "name": "record-meal", "description": "…", "body": "…", "delivery": "inline" } ],
  "templates": [ { "name": "clear", "kind": "builtin" },
                 { "name": "rules", "kind": "template", "content": "Nhắc lại luật: $@" } ],
  "models":    { "text": "~deepseek/deepseek-v4-flash-latest",
                 "vision": "qwen/qwen3-vl-30b-a3b-instruct",
                 "thinking": "medium", "thinking_budgets": null },
  "retry":     { "enabled": true, "maxRetries": 3, "baseDelayMs": 2000 },
  "caps":      { "max_tools": 40, "max_seconds": 120 },
  "builtin_tools": [],
  "tool_packs": [ { "pack": "lunch_ledger", "tools": {
                     "propose_meal": { "enabled": true },
                     "suggest_lunch": { "enabled": true, "description": "…override…" },
                     "pick_random":  { "enabled": false } } } ],
  "extensions": [ { "id": "tool_call_policy", "config": { "max_per_tool": 6 } } ],
  "memory":    { "history_max_messages": 200, "window_weeks": 10,
                 "image_lookback_messages": 10, "image_lookback_minutes": 120,
                 "vision_history_max_messages": 60,
                 "summary_prompt": "Bạn đang tóm tắt …" },
  "settings":  { "compaction": { "enabled": false } }   // passthrough → SettingsManager.inMemory
}
```

**Template variables** available to `prompt.body` and `append`: `persona.*`,
`today` (ICT), `sender.name`, `sender.member_id`, `room.name`, `room.language`.
Rendering is Python-side (`prompt.py` becomes a renderer, not a string). The set is
closed and documented in the editor; unknown variables fail validation at save time,
not at 12:05 when someone asks who pays.

### 4.2 Why bodies are inline in the spec but sources are rows

Because the two have different consumers. An editor wants "the record-meal skill"
with history and a diff. The sidecar wants "the bytes this room runs right now" with
no joins and no chance that a half-saved edit lands mid-turn. Snapshot-on-publish
serves both, and it is how every headless CMS with a publish step works.

## 5. Architecture

### 5.1 Read side — the resolver (the only change on the hot path)

```
chat.run_bot_turn(room_id, …)
   └─ profiles.resolve(session, room_id) -> ResolvedProfile   # spec + room overrides, cached
        └─ agent.run_turn(text, ctx, profile=…)                # builds the run command from it
              └─ sidecar (unchanged protocol; new optional fields: settings, extensions)
```

`resolve()` returns the published spec for `rooms.agent_profile_id` (else the
business default), applies `rooms.agent_overrides`, and caches by
`(version_id, room_id)`. It is the *only* thing the turn path learns about the CMS.
`run_turn`'s frozen signature gains one keyword, `profile=None`, defaulting to a
profile built from today's code and env — so the 14 monkeypatch sites and every
existing test keep passing, and Phase 1 (§8) can ship with **zero behaviour change**
and prove it with the existing benchmark.

### 5.2 Write side — the headless API

All under `/api/admin/…`, guarded by an admin session (cookie or bearer), not the
shared `X-Admin-Password` header. JSON in, JSON out, etags on every editable entity
(the pattern `knowledge.py` already uses).

```
GET/POST        /api/admin/businesses
GET/PATCH       /api/admin/businesses/{id}
GET/POST        /api/admin/businesses/{id}/{prompts|rules|skills|templates}
GET/PATCH/DEL   /api/admin/businesses/{id}/{…}/{slug}          (etag required on write)
GET/POST        /api/admin/profiles                              (create from business)
GET             /api/admin/profiles/{id}/versions
POST            /api/admin/profiles/{id}/versions               (draft from sources, or from a version)
POST            /api/admin/profiles/{id}/versions/{v}/publish    (runs validation + gates, §7)
POST            /api/admin/profiles/{id}/versions/{v}/preview    (dry-run a turn in a sandbox room)
GET             /api/admin/catalogue/{tool-packs|extensions|models}
POST            /api/admin/catalogue/models/{id}/probe           (runs bench.probe_models)
PATCH           /api/admin/rooms/{id}                            (agent_profile_id, agent_overrides)
GET             /api/admin/rooms/{id}/resolved                   (what this room runs, verbatim)
GET             /api/admin/profiles/{id}/versions/{v}/export     (Pi package zip, §6)
POST            /api/admin/import                                (Pi package zip → draft)
GET             /api/admin/audit
```

"Headless" here means the same thing it means for a content CMS: the admin UI is
one client of this API and nothing in it is only reachable through the UI. The
benchmark runner and the export/import CLI are the other clients.

### 5.3 Admin UI

A `/admin` route group in the existing Next.js app, same component library, same
`api.ts` client. Screens: Businesses · Profile editor (tabs: Persona, Prompt,
Rules, Skills, Commands, Models, Tools, Memory, Advanced) · Version history with
diff · Rooms (bind profile, edit append sections) · Catalogue · Audit. The knowledge
panel that exists today is linked from the Rooms screen, not duplicated.

### 5.4 Tool packs — how code plugs in without editing `chat.py`

Today `tools.build_tools` is one function and `chat.py` renders results by a chain of
`elif attachments["type"] == …`. A pack is the minimum interface that lets a second
business exist without touching either:

```python
class ToolPack(Protocol):
    id: str                                  # "lunch_ledger"
    def tools(self, ctx: ToolContext) -> dict[str, CustomTool]: ...
    def render(self, result) -> tuple[str, dict] | None:       # body, attachments
        ...                                  # the _settlement_body / _meal_body chain, moved
    def post_turn(self, db, room_id, result) -> list[RoomMessage]:
        ...                                  # the draft-card creation in run_bot_turn, moved
    seed: Callable[[db, room_id], None] | None  # seed_places for a new room
```

`lunch_ledger` is the first and only pack: **a move, not a rewrite.** Every tool body,
renderer and draft path is byte-identical, relocated behind the protocol, and the
benchmark corpus is the proof. A registry (`packs/__init__.py`) lists installed
packs; the catalogue endpoint reads it. A developer adds a business by adding a
package; an editor cannot, and that is the intended line.

### 5.5 Sidecar changes

Small and additive to the `run` command:

- `settings` → `SettingsManager.inMemory(settings)` passed to `createAgentSession`.
- `extensions[]` → looked up in a sidecar-side registry of factories; each factory
  receives its `config` and is passed via `DefaultResourceLoader.extensionFactories`.
- `tools.promptGuidelines` / `label` → forwarded into `proxyTool` (Pi already
  supports both on `ToolDefinition`).
- `skills[].delivery === "discoverable"` → written to a per-turn temp dir under
  `agent_dir` and passed via `additionalSkillPaths`, only when `read` is enabled;
  otherwise validation rejects the combination before publish.

Nothing about the boundary rule of the Pi design changes: Python still owns content
and data, the sidecar still owns everything about how Pi runs.

## 6. Export / import — the Pi package as the interchange format

`TODO.md` asks for "engine export/import: mounting point vs skeleton, what to
export, import flow with sanitize". This design answers it by adopting Pi's own
package layout rather than inventing one:

```
<profile>-v<N>/
  package.json          {"name": …, "pi": {"skills": ["./skills"], "prompts": ["./prompts"]},
                         "chiatienan": { "spec": { …the §4.1 spec minus bodies… } }}
  AGENTS.md             rules, concatenated in order (Pi reads this as context)
  SYSTEM.md             rendered prompt body with {{variables}} left intact
  skills/<name>/SKILL.md
  prompts/<name>.md
  settings.json         {"defaultProvider","defaultModel","defaultThinkingLevel","compaction",…}
```

- **Skeleton vs mounting point:** the skeleton is everything above (behaviour); the
  mounting point is the room (data). Export never includes room data — no members,
  no ledger, no `memory.md` — so a bundle is shareable by construction.
- **Sanitise on import:** a bundle is a *draft*, never auto-published. Import
  validates frontmatter, template variables, tool references against installed
  packs (unknown tool → error, not silently dropped), and strips anything under
  `extensions/` — code is not accepted through this door (§0).
- **Round trip with stock `pi`:** because the layout is a real Pi package,
  `pi -e ./<profile>-v<N>` runs the same prompt, skills and rules under the
  interactive TUI against `bash`-less tools — a debugging aid the current setup
  does not have. The money tools are absent there (they are Python); that is
  expected and the skills say so.

## 7. Governance — what publishing checks

The bot owns real money. A CMS that makes it *easier* to change what runs must make
it *harder* to change it wrongly. Publishing a version runs, in order:

1. **Schema validation** of the spec; unknown template variables; skills with
   `delivery: discoverable` but no `read` in `builtin_tools`.
2. **Money-safety gate.** If any enabled pack declares `handles_money: true`
   (`lunch_ledger` does) and `builtin_tools` contains `bash`, `write` or `edit`,
   publishing requires an explicit `override_reason` and writes it to the audit
   log. The rationale is the one already in `session.js:toolOptionsFor`: without
   `bash` the rule "never compute money" is structural rather than requested.
3. **Model probe gate.** `models.text` and `models.vision` must have a passing
   `probe` against the enabled packs' real schemas within N days
   (`bench.probe_models` — exists; the CMS calls it and stores the result). The
   Pi design §12 records why: a catalogue claiming `tools: true` shipped a model
   that emitted nothing for `propose_meal`.
4. **Benchmark smoke (optional, recommended for the money business).** Run the
   `typical` corpus once against the draft (`bench.run --profile-version …`) and
   refuse to publish on a `tool_selection` or `ledger_state` drop. Prose graders
   are reported, not blocking — the same asymmetry the Pi design set.
5. **Audit + rollback.** Every publish is a row; `published_version_id` moves; the
   previous version stays `published`-capable so rollback is one call.

Admin identity is the prerequisite: `admin_users` with a password hash and roles,
replacing the one shared header for everything under `/api/admin`. The old
`X-Admin-Password` stays for `POST /api/rooms` and `/internal/*` until migrated.

## 8. Phases

Each phase ends green on the existing suites plus its own tests; Phase 1 is
additionally gated on the benchmark showing no change.

| # | Deliverable | Behaviour change | Proof |
|---|---|---|---|
| **1** | `profiles.py` resolver + tables; a **seeded default profile built from today's `prompt.py`, skill files, rule file and env**; `run_turn(profile=)` keyword; sidecar accepts `settings`/`extensions` (ignored when absent) | **none** | 751 backend + 65 sidecar tests unchanged; `bench.run --corpus typical --repeat 3` equal to `pi-typical-r3.json`; `GET /api/admin/rooms/{id}/resolved` byte-equals the current `run` command |
| **2** | Admin identity + audit; headless API for prompts/rules/skills/templates/models/caps/memory; publish with gates 1–3; `/admin` UI for those tabs; room binding | opt-in per room | API tests; a room bound to an edited profile runs the edit, an unbound room does not |
| **3** | `ToolPack` protocol; `lunch_ledger` moved behind it; per-tool enable/override; `chat.py` renders through the pack | none for existing rooms | benchmark equality again; a test business with two stub tools runs end-to-end in a test room |
| **4** | Export/import as Pi package; `bench.run --profile-version`; publish gate 4 | none | round-trip test: export → import → spec equality; `pi -e` smoke documented |
| **5** | Extension catalogue in the sidecar (tool-call policy, provider headers, telemetry) + config UI | opt-in | sidecar tests per factory |

Phase 1 is deliberately the boring one. It is also the one that makes every later
phase safe, because from then on "what does this room run" has one answer and one
endpoint.

## 9. What this design does not do, on purpose

- **No code authoring in the CMS** — no TypeScript extensions, no Python tools, no
  schema editing. New tools are a developer deliverable (a pack). A "generic HTTP
  tool" that an editor could point at a URL is a plausible Phase 6 for
  *non-money* businesses; it is not in scope here because it reopens the D3 wire.
- **No Pi RPC mode, no Pi session files** — decided in the Pi design for reasons
  that still hold.
- **No multi-process** — the resolver cache is in-process like everything else;
  the single-writer constraint stands.
- **No terminal-only settings** — listed in §2.5 so nobody asks twice.

## 10. Decisions needed before Phase 1

1. **Profile granularity.** Recommendation: *profiles are shared; rooms bind to one
   and may add append sections.* The alternative (every room owns a full private
   profile) is simpler to reason about and worse to operate: seven rooms, one prompt
   fix, seven edits. Say if you want per-room forks anyway.
2. **Where admin identity comes from.** Recommendation: a local `admin_users` table
   with email + password, owner/editor/viewer, because the app has no IdP today and
   the README lists RBAC as out of scope. If Niteco SSO is the real target, say so
   now — it changes Phase 2, not the content model.
3. **Business #2.** The design supports a second business but does not pick one.
   Naming a concrete candidate (even a toy) before Phase 3 keeps the `ToolPack`
   protocol honest — a protocol with one implementation is a guess.
4. **Storage.** Recommendation: SQLite tables as above. The alternative — a
   `profiles/<id>/` directory of Markdown files under `DATA_DIR`, git-style — is
   attractive for diffing and matches `memory.md`, but publishing atomically and
   joining to rooms gets hand-rolled. Tables, with the Pi-package export giving the
   file view for free.
5. **Default `builtin_tools` for the seeded profile.** Today's env default is
   `read,write,bash`. Phase 1 must copy it to stay behaviour-neutral. The question
   is whether Phase 2 flips the *default* for new profiles to `[]` (recommended, and
   what the Pi design intended) while leaving the seeded one as-is.

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
