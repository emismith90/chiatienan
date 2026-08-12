# Cursor SDK → Pi harness — Design

**Status:** approved, not yet implemented
**Supersedes the engine half of:** [`2026-07-22-agent-payments-skills-grok-design.md`](2026-07-22-agent-payments-skills-grok-design.md)
**Implementation plan:** [`../plans/2026-08-12-cursor-to-pi-harness.md`](../plans/2026-08-12-cursor-to-pi-harness.md)

## 1. Why

The bot's LLM engine is the Cursor SDK. `app/agent.py` launches a
`cursor-sdk-bridge` subprocess per turn, hands it 14 Python `CustomTool`s, and
assembles a `TurnResult` that `app/chat.py` renders into the room. Cursor is no
longer a viable option, so the engine moves to
[Pi](https://github.com/earendil-works/pi) (`@earendil-works/pi-coding-agent`),
running on OpenRouter.

The bar is **no behavior change**. This bot owns real money: the append-only
ledger, the split arithmetic, and the amounts inside VietQR codes people scan to
pay each other. Design rule **D3** — a number may flow user → LLM → tool *once*,
never tool → LLM → tool — holds today because the tools are the only place
arithmetic happens. That must survive the port untouched.

"Must survive" is not a claim you can make by inspection, so this design has a
second deliverable of equal weight: a **benchmark harness** that replays the
golden datasets *and real production conversations* through the engine and grades
the outcome four ways, so the port ships with evidence instead of confidence.

## 2. The constraint that shapes everything

`cursor-sdk` ships a **Python** package, so the 14 tools are Python closures over
the DB session. **Pi is TypeScript-only**, and its RPC mode is explicit:

> The RPC protocol does not support host-side tool registration or tool-call
> callback patterns. […] To integrate custom host-side logic, use extensions.
>
> — [pi `docs/rpc.md`](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md)

So `pi --mode rpc` cannot host our tools. Custom tools exist only inside a Node
process, registered via `resourceLoader.extensionFactories` → `pi.registerTool()`.

Three options were considered:

| Option | Verdict |
|---|---|
| Rewrite the 14 tools in TypeScript | **Rejected.** Either duplicates `money.py`/`ledger.py` in a second language, or moves arithmetic to the model's side of the wire. Both violate D3. |
| Port the whole backend to Node | **Rejected.** Out of all proportion to the change. |
| Node sidecar + Python shim, tools stay in Python | **Chosen.** |

## 3. Architecture: TypeScript is the source of truth

The Node side owns the **entire harness**. Python reimplements no part of pi's
semantics — no model resolution, no event translation, no message unwrapping, no
answer assembly. It spawns a process, writes JSONL, reads JSONL, runs a tool when
asked, and hydrates a dataclass.

```
FastAPI (Python)                          agent_sidecar (Node — source of truth)
─────────────────                         ─────────────────────────────────────
app/agent.py                              main.js     JSONL loop, command dispatch
  build run command  ───── run ─────────▶ session.js  provider, model, base URL,
  forward agent.* events                             ResourceLoader, tools:[]
  dispatch tool_call                      turn.js     event normalization, turn
  hydrate TurnResult ◀─── turn_done ─────            caps, final-answer assembly,
                                                     error formatting, stats
app/tools.py         ◀─── tool_call ─────  schema.js  JSON Schema → TypeBox
  14 tool bodies     ──── tool_result ──▶
  (DB, money, QR)
```

### 3.1 The boundary rule

| Python owns — *data and content* | TypeScript owns — *the harness* |
|---|---|
| SQLite, `ledger.py`, `money.py`, `qr.py` | Provider, model id, base URL, `thinkingLevel` |
| The 14 tool bodies (`tools.py`) | Session lifecycle, `ResourceLoader`, skills, `AGENTS.md` |
| System-prompt *text* (`prompt.py`) | pi event stream → normalized `agent.*` events |
| Memory + history *text* (`memory.py`, `chat.build_history`) | Turn caps, `session.abort()`, retries |
| **Nothing about how pi works** | Final-answer assembly, narration stripping, error formatting, stats |

Two things stay in Python deliberately, and they are the arguable edge of the rule:

- **The 14 tool bodies** are the money layer. See §2.
- **`prompt.py` and the history/memory renderers** produce Vietnamese app
  *content* read out of the DB. Passing a string across the bridge is as thin as
  a shim gets, and `test_prompt.py` already guards the content.

Operational test for the rule: if `app/pi_bridge.py` starts branching on pi
semantics, that logic belongs in `turn.js` instead.

Everything downstream of `run_turn` — `chat.py`, `drafts.py`, `ledger.py`,
`money.py`, the SSE stream, the whole frontend — is untouched.

## 4. The RPC protocol

JSONL over the sidecar's stdin/stdout: one JSON object per line, `\n`-delimited,
the same discipline pi's own RPC mode uses. **Deliberately not loopback HTTP** —
no port, no auth surface, and nothing else on the box can invoke a ledger tool.

### 4.1 Python → sidecar

```json
{"type":"run","turn_id":"…","system":"…","message":"…",
 "images":[{"data":"<base64>","mimeType":"image/png"}],
 "tools":[{"name":"propose_meal","description":"…","schema":{…}}],
 "skills":[{"name":"record-meal","description":"…","body":"…"}],
 "context_files":[{"path":"money-safety","content":"…"}],
 "max_tools":40,"max_seconds":120}
{"type":"tool_result","call_id":"…","content":"{\"ok\":true,…}"}
{"type":"summarize","turn_id":"…","text":"…"}
{"type":"ping"}
```

### 4.2 Sidecar → Python: events forwarded verbatim

These are emitted **already in the app's wire format**, so `agent.py` passes them
straight to `emit` without touching them:

```json
{"type":"agent.run.started","turn_id":"…"}
{"type":"agent.text.delta","turn_id":"…","delta":"Đã ghi"}
{"type":"agent.tool.start","turn_id":"…","call_id":"…","name":"propose_meal","args":{…}}
{"type":"agent.tool.result","turn_id":"…","call_id":"…","name":"…","status":"completed","result":{…}}
{"type":"agent.run.finished","turn_id":"…"}
{"type":"agent.run.error","turn_id":"…","message":"…"}
```

The names are **identical** to what `agui.py` emits today. That identity is what
lets `agui.py` and `test_agui.py` be deleted with zero frontend change —
`use-room.ts:45-90` and `agent-timeline.tsx` keep working untouched.

### 4.3 Sidecar → Python: control messages

```json
{"type":"tool_call","turn_id":"…","call_id":"…","name":"propose_meal","args":{…}}
{"type":"turn_done","turn_id":"…","final_text":"…","tools":[…],"error":null,
 "stats":{"tokens":…,"cost":…,"tool_calls":…,"elapsed_s":…}}
```

`tool_call` blocks the sidecar until the matching `tool_result` arrives. The
sidecar emits `agent.tool.start` before it and `agent.tool.result` after, so the
live timeline is byte-comparable to today's.

`turn_done` is a **ready-made `TurnResult`**: Python hydrates the dataclass field
for field and performs no assembly.

## 5. Session construction

```js
const OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1";  // constant, no env override
```

`CURSOR_API_BASE` was configurable; its replacement is not. One less knob to get
wrong in prod, and the provider wiring is readable in one place.

```js
const resourceLoader = new DefaultResourceLoader({
  cwd, agentDir,
  systemPromptOverride: () => req.system,               // prompt.py text, verbatim
  agentsFilesOverride:  () => ({ agentsFiles: req.context_files }),
  skillsOverride:       () => ({ skills: req.skills }), // in memory, never on disk
  extensionFactories: [(pi) => {
    for (const t of req.tools) pi.registerTool(proxyTool(t));
  }],
});
const { session } = await createAgentSession({
  resourceLoader, model, thinkingLevel,
  tools: [],                                            // no bash/read/write/edit/grep
  sessionManager: SessionManager.inMemory(cwd),
});
```

Three consequences worth stating outright:

- **`tools: []` is a security upgrade.** Today the agent has `bash`, `edit`, and
  `write` in a real workspace, and `money-safety.mdc` merely *asks* it not to
  compute money ("KHÔNG chạy python/bash để tính tiền"). Removing the tools makes
  the rule structural.
- **Skills and `AGENTS.md` arrive as strings**, so nothing is written to disk.
  This deletes `skills.py` and the entire class of bug its `_prune` exists for:
  the workspace lives on `/data` and outlives the container, so a renamed skill
  kept being loaded forever.
- **Fresh session per turn.** Continuity already comes from
  `chat.build_history` + `memory.md` injected as text; a persistent pi session
  would double it. The *process* is long-lived (spawn cost paid once); sessions
  are not.

The 4 `SKILL.md` files need **zero content change** — their frontmatter is
already `name` + `description`, exactly what pi requires. `money-safety.mdc` has
no pi always-apply equivalent, so its body ships as a `context_files` entry,
which pi loads into every system prompt.

## 6. `schema.js` — JSON Schema → TypeBox

Pi requires TypeBox for `parameters`, not raw JSON Schema. Our 14 schemas were
dumped and analysed; the converter needs to support **exactly six keywords** and
nothing more:

`type` · `properties` · `required` · `items` · `description` · `enum`

Type values in use: `object`, `string`, `integer`, `boolean`, `array`, and **one
union**: `{"type": ["string", "integer"]}` on `target` in both
`_UPDATE_MEMBER_SCHEMA` and `_DELETE_MEMBER_SCHEMA` (`tools.py:193`, `:216`).
That union is the single case a naive converter would silently mistranslate, so
it gets its own test.

Mapping:

| JSON Schema | TypeBox |
|---|---|
| `{"type":"object","properties":…,"required":[…]}` | `Type.Object({…})`, non-required members wrapped in `Type.Optional` |
| `{"type":"string"}` | `Type.String({description})` |
| `{"type":"integer"}` | `Type.Integer({description})` |
| `{"type":"boolean"}` | `Type.Boolean({description})` |
| `{"type":"array","items":…}` | `Type.Array(convert(items))` |
| `{"type":"string","enum":[…]}` | **`StringEnum([...])`** — not `Type.Union`/`Type.Literal` |
| `{"type":["string","integer"]}` | `Type.Union([Type.String(), Type.Integer()])` |

`StringEnum` comes from `@earendil-works/pi-ai`; pi's `docs/extensions.md` is
explicit that `Type.Union`/`Type.Literal` breaks Google's API. Three schemas hit
this: `discount_split` (`proportional|equal`), `keyword` (7 period values, in
both `_PERIOD_SCHEMA` and `_SETTLE_SCHEMA`), and `mode` (`gross|offset`).

Nested objects appear in two places, both inside `_PROPOSE_SCHEMA`:
`adjustments.items` (`{member, amount}`, both required) and `items.items`
(`{member, amount, label}`, first two required).

The converter is tested against **all 14 real schemas**, dumped from Python into
a committed JSON fixture, so the two runtimes cannot drift.

## 7. `proxyTool` — the semantic detail that must not slip

Pi's documented convention is *"always throw to signal failure."* Our tools
return `{"ok": false, "error": …}` **deliberately** — `tools.py:8`:

> Validation failures are returned as `{"ok": False, "error": ...}` dicts (a
> clarifying-question result) rather than raised, so the model can ask the user
> instead of guessing.

So `proxyTool` returns that dict as an ordinary `content` text block. **Only
transport death throws.** Getting this backwards would turn every "which day did
you mean?" into an error the model apologizes for instead of a question it asks —
a subtle, corpus-wide regression that no unit test would catch.

Result shape: `content: [{type:"text", text: JSON.stringify(result)}]` for the
model, `details: result` for the session record.

## 8. What this deletes rather than ports

| Python today | Fate |
|---|---|
| `cursor_runner.py` — 372 lines resolving `ModelSelection` variants, because bare parameterized ids return an opaque `RUN_LIFECYCLE_STATUS_ERROR` | **Deleted, not replaced.** Provider/model/thinking are three strings in `session.js` |
| `agui.py` — 75-line run-message → `agent.*` translator | **Deleted.** The sidecar emits the final format (§4.2) |
| `_unwrap_tool_name` / `_unwrap_tool_args` / `_unwrap_tool_result` / `_flatten_envelope` — undo Cursor's `name=="mcp"` MCP envelopes | **Deleted.** Our own envelope has no wrapping |
| `_assistant_text`, `_final_answer`, `_split_at_seams`, `_is_narration`, `_strip_narration` | **Moved to `turn.js`** |
| `skills.py` (77 lines) + `_prune` + `test_skills_materializer.py` | **Deleted.** §5 |
| `summarize.py`'s SDK wiring | One RPC command; send text, get text |
| `bridge_smoke.py`'s SDK wiring | A `ping` RPC command |
| No `instructions` field → system prompt prepended to the user message (`prompt.py:3`) | Real `getSystemPrompt()` |

`app/agent.py` goes from ~410 lines to roughly 80. Three modules disappear. **The
Python side gets smaller than it is today** — the port is mostly deletion.

## 9. Configuration

| Removed | Added |
|---|---|
| `CURSOR_API_KEY` | `OPENROUTER_API_KEY` (already in the cloud env + GitHub secrets) |
| `CURSOR_SDK_MODEL` | `PI_MODEL=deepseek/deepseek-v4-flash`, `PI_PROVIDER=openrouter` |
| `CURSOR_API_BASE` | *(nothing — hard-coded, §5)* |
| `CURSOR_SDK_WORKSPACE` | `DATA_DIR=/data`, `PI_THINKING=medium` |
| `CURSOR_AGENT_MAX_TOOLS` / `_MAX_SECONDS` | `PI_MAX_TOOLS=40`, `PI_MAX_SECONDS=120` |
| | `PI_VISION_MODEL` (§11) |

The workspace rename is **not cosmetic**. With `tools: []` and in-memory skills
the agent needs no persistent workspace at all — but `memory.py` stores
`memory.md` + `memory.meta.json` under `settings.cursor_workspace/rooms/{id}/`,
which is app data that must stay on the mounted volume. So the setting becomes
`DATA_DIR`, `memory.py` points at `DATA_DIR/rooms/{id}/`, and
`main.py`'s `_warn_if_workspace_is_ephemeral` keeps guarding it — the warning now
protects room memory, which was always its real subject.

## 10. The benchmark

Full design in the implementation plan, §Phase 1. The shape:

- **Corpus** — `tests/golden/meals.py` (G1–G12) and
  `tests/golden/scenario_week.py` (12 steps, real Vietnamese messages) imported
  as-is, plus a new sanitized production corpus.
- **Prod fixtures** — pulled from the existing read-only
  `/internal/debug/conversation.csv`, pseudonymized, and **stripped of
  `account_number`, `account_holder`, `bank_code`, `invite_token`, `pin`, and
  every `qr_url`** (a VietQR URL embeds a real account number, and
  `test_ledger_endpoint.py:53` records that prod history holds 34 live ones).
  Amounts are kept — they are the thing being graded, and harmless once the
  account numbers are gone. The sanitizer is CI-tested; a leak here is a privacy
  incident, not a test failure.
- **Four graders** — `tool_selection` (+ money-arg subset), `ledger_state`
  (final balances / ordered transfers / QR payees), `prose_quality`
  (`moneyguard.unbacked_amounts` as a deterministic pre-check, then an LLM
  judge), and `cost_latency` (reported, never pass/fail).
- **Report** — per-case grid plus a `--compare` mode that diffs two runs.

### 10.1 Sequencing is load-bearing

The cutover is a hard one: Cursor gets ripped out, not kept behind a flag. That
conflicts with proving equivalence unless the baseline is captured **first**.

1. Build the whole harness against the **current Cursor engine**; commit
   `bench/results/baseline-cursor.json`.
2. Only then replace the engine.
3. Diff Pi against that recorded baseline.

Skip step 1 and "exactly the same behavior" becomes unfalsifiable — the report
could only say "Pi passes the tests we wrote", which is a weaker claim.

## 11. Open risk: vision

OpenRouter's model catalogue is egress-blocked from the dev environment, but web
sources consistently report **text-only input modalities** for
`deepseek/deepseek-v4-flash`.

Bill photos are load-bearing here: `images.py`, `_build_message`, the
`# Ảnh kèm theo` prompt section, `test_bill_image_carryover.py`, and
`moneyguard`'s own field note — *"of the alerts that survive a tool-output
allow-set, all but one were prices the model read off a bill photo."*

Mitigation, wired from the start rather than bolted on: `PI_VISION_MODEL` is a
separate setting. A turn carrying images resolves to that model (via
`session.setModel` / `scopedModels`); text-only turns use `PI_MODEL`. If
deepseek-v4-flash does accept images, set them equal and the branch is inert.

**Verify the modality before writing the sidecar.** If it is text-only and no
vision model is configured, image turns must **fail loudly**, never silently drop
the photo — a dropped bill means the model invents the total.

## 12. Deferred, deliberately

`_strip_narration` exists because Cursor's agent narrated its skill reads ("Mình
đọc skill…") and glued that onto the answer. With skills injected in-memory and
no `read` tool, the narration may simply be gone. It moves to `turn.js` unchanged
and is only removed if the benchmark corpus shows **zero** narration hits.
Measure, don't guess.
