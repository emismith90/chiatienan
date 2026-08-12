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

### 1.1 The bar is "no behavior change" for money, and quality everywhere else

These are different bars, and conflating them would make the harness enforce the
old engine's faults:

| | bar | why |
|---|---|---|
| `tool_selection`, `ledger_state` | **equivalence** — must not regress | D3. The tool chosen and the amounts in it decide what people pay each other. |
| `prose_quality`, latency, cost | **the rubric** — better is better | Nothing about Cursor's replies is a specification. |

**Cursor is a reference, not a ceiling.** It was never good at the prose path, and
the recorded baseline proves it rather than assuming it: on the 26 production turns
whose reply the room actually read, Cursor scores **1/26** against the rubric,
failing mostly by narrating its own machinery into the room — "mình đọc skill phù
hợp rồi xử lý", "Mình sẽ tìm thành viên A2 rồi cập nhật", and in one case a
hand-typed six-row balance table (exactly what `moneyguard`'s field note describes).

Two consequences:

- A Pi reply that clears the rubric where Cursor did not is an **improvement**, and
  `bench.report` flags it `IMPROVED` rather than as a difference to explain away.
  A case both failed is `BASELINE-FAILED-TOO`, never `BOTH-FAILING` — the latter is
  reserved for money, where agreeing on zero means the case certified nothing.
- The ship criterion (plan, Task 22) is expressed over the money graders only. A
  prose change is written down and understood, never auto-blocking.

**And `_strip_narration` is not doing its job today.** Those bodies are what
`chat.py` posted, i.e. *after* stripping — so the narration above is leaking into
the live room right now. §13's "measure, don't guess" is therefore settled in the
opposite direction from the one it anticipated: the mechanism ports, but it needs
strengthening, not deleting.

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

**Five** files import `cursor_sdk` today: `agent.py`, `cursor_runner.py`,
`tools.py`, `summarize.py`, and `bridge_smoke.py`. The last is the one that gets
forgotten — it is only touched via a rename.

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

Every command carries a `req_id`. The sidecar echoes it on every message it emits
in response, so concurrent commands can be demultiplexed on one stdout — a
`ping` from `/internal/bridge-smoke` can arrive mid-turn, because that route is
**not** under `chat._agent_lock`.

```json
{"type":"run","req_id":"…","turn_id":"…","system":"…","message":"…",
 "images":[{"data":"<base64>","mimeType":"image/png"}],
 "tools":[{"name":"propose_meal","description":"…","schema":{…}}],
 "skills":[{"name":"record-meal","description":"…","body":"…"}],
 "context_files":[{"path":"money-safety","content":"…"}],
 "max_tools":40,"max_seconds":120}
{"type":"tool_result","req_id":"…","call_id":"…","content":"{\"ok\":true,…}"}
{"type":"summarize","req_id":"…","text":"…"}
{"type":"ping","req_id":"…"}
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

The names are **identical** to what `agui.py` emits today — verified line by line
against `agui.py:48-73` and against everything `use-room.ts:50-89` consumes. That
identity is what lets `agui.py` and `test_agui.py` be deleted with zero frontend
change.

### 4.3 Sidecar → Python: control messages

```json
{"type":"tool_call","req_id":"…","turn_id":"…","call_id":"…","name":"propose_meal","args":{…}}
{"type":"turn_done","req_id":"…","turn_id":"…","final_text":"…","tools":[…],"error":null,
 "capped":false,"stats":{"tokens":…,"cost":…,"tool_calls":…,"elapsed_s":…}}
{"type":"summarize_done","req_id":"…","text":"…"}
{"type":"pong","req_id":"…","elapsed_s":0.4,"text":"pong"}
{"type":"fatal","req_id":"…","message":"…"}
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

### 5.1 ⚠️ Skill bodies must be *verified* to reach the model

`tools: []` removes `read`. Pi's documented skill mechanism puts each skill's
name and description in the system prompt and expects the agent to **read the
full `SKILL.md`** when a task matches. Under Cursor the agent demonstrably did
exactly that — `_strip_narration` exists *because* the model narrated its skill
reads ("Mình đọc skill record-meal…", `agent.py:127-155`).

If `skillsOverride` only surfaces name+description and defers the body to a
read-like tool, then with `tools: []` **the model never sees a single procedure
body** — a corpus-wide silent regression in precisely the money workflows the
skills encode, and one no unit test would catch.

So this is an assertion, not an assumption: a sidecar test must show a skill's
**body text present in the model-visible context** with the built-in toolset
empty. If pi cannot do that, the fallback is to ship the four skill bodies as
additional `context_files` entries (always in the system prompt, ~8KB total) and
drop the skill mechanism entirely.

## 6. `schema.js` — JSON Schema → TypeBox

Pi requires TypeBox for `parameters`, not raw JSON Schema. All 14 schemas were
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
explicit that `Type.Union`/`Type.Literal` breaks Google's API. There are **three
distinct enum definitions** — `discount_split` (`proportional|equal`), `keyword`
(7 period values), and `mode` (`gross|offset`) — but they appear in **six of the
14 schemas**, because `member_statement` and `get_period_summary` reuse
`_PERIOD_SCHEMA["properties"]["keyword"]` by reference (`tools.py:841`, `:847`)
alongside `resolve_period` and `settle_period`.

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

## 8. Turn caps are a partial answer, not an error

Today a cap breach is **not** a failure. `agent.py:374-384` logs a warning,
cancels the run, and `break`s the loop: `result.error` stays `None`,
`final_text` keeps everything accumulated so far, and `chat.py:558` posts it as a
normal reply.

`turn.js` must preserve that exactly. A cap breach sets `capped: true` on
`turn_done`, keeps `error: null`, and keeps the accumulated text and tool
invocations. If a cap became an error, every capped turn would flip from a
partial reply to a `⚠️` message in the room — a visible regression on the
slowest, most complex turns, which are the ones users care about most.

## 9. What this deletes rather than ports

| Python today | Fate |
|---|---|
| `cursor_runner.py` — 371 lines resolving `ModelSelection` variants, because bare parameterized ids return an opaque `RUN_LIFECYCLE_STATUS_ERROR` | **Deleted, not replaced.** Provider/model/thinking are three strings in `session.js` |
| `agui.py` — 74-line run-message → `agent.*` translator | **Deleted.** The sidecar emits the final format (§4.2) |
| `_unwrap_tool_name` / `_unwrap_tool_args` / `_unwrap_tool_result` / `_flatten_envelope` — undo Cursor's `name=="mcp"` MCP envelopes | **Deleted.** Our own envelope has no wrapping |
| `_assistant_text`, `_final_answer`, `_split_at_seams`, `_is_narration`, `_strip_narration` | **Moved to `turn.js`** |
| `skills.py` (74 lines) + `_prune` + `test_skills_materializer.py` | **Deleted.** §5 |
| `summarize.py`'s SDK wiring | One RPC command; send text, get text |
| `bridge_smoke.py`'s SDK wiring | A `ping` RPC command |
| No `instructions` field → system prompt prepended to the user message (`prompt.py:3`) | Real `getSystemPrompt()` |

`app/agent.py` goes from 408 lines to roughly 80. Three modules disappear. **The
Python side gets smaller than it is today** — the port is mostly deletion.

The `run_turn` contract is frozen because **14 `monkeypatch.setattr` sites across
4 test files** depend on it: `test_chat.py` (7), `test_chat_payment_turn.py` (3),
`test_bill_image_carryover.py` (2), and `test_api.py` (2, on `run_bot_turn`).
Those tests passing unedited is the proof the contract held.

## 10. Configuration

| Removed | Added |
|---|---|
| `CURSOR_API_KEY` | `OPEN_ROUTER_KEY` (the environment's actual name for it — an earlier draft of this table said `OPENROUTER_API_KEY`, which does not exist) |
| `CURSOR_SDK_MODEL` | `PI_MODEL=~deepseek/deepseek-v4-flash-latest`, `PI_PROVIDER=openrouter` |
| `CURSOR_API_BASE` | *(nothing — hard-coded, §5)* |
| `CURSOR_SDK_WORKSPACE` | `DATA_DIR=/data`, `PI_THINKING=medium` |
| `CURSOR_AGENT_MAX_TOOLS` / `_MAX_SECONDS` | `PI_MAX_TOOLS=40`, `PI_MAX_SECONDS=120` |
| | `PI_VISION_MODEL=qwen/qwen3-vl-30b-a3b-instruct` (§12) |

### 10.1 ⚠️ The `DATA_DIR` rename orphans production room memory

With `tools: []` and in-memory skills the agent needs no persistent workspace at
all — but `memory.py:26-34` stores `memory.md` and `memory.meta.json` under
`{cursor_workspace}/rooms/{id}/`, and **production's workspace is
`/data/cursor-agent`** (`deploy.yml:164`).

Setting `DATA_DIR=/data` therefore points memory at `/data/rooms/{id}/` — a
different directory. On the first post-deploy turn:

- `load_memory` returns `""` — every room's long-term memory is gone
- `read_watermark` returns `0`
- `_maybe_rollover` (`chat.py:603-619`) re-summarizes the room's **entire
  >10-week history** into a fresh `memory.md`, using the new model

That is a silent, expensive, user-visible data loss dressed up as a rename. So
`memory.py` gains a one-release **idempotent startup migration**: if
`DATA_DIR/rooms/{id}` is absent and the legacy `/data/cursor-agent/rooms/{id}`
exists, copy it across. The repo already does startup migrations of this shape
(commit `aa1f992`, "Add missing columns to existing tables on startup"), so this
matches house style and is testable in CI rather than living in a deploy script.

`main.py`'s `_warn_if_workspace_is_ephemeral` keeps guarding the path — the
warning now protects room memory, which was always its real subject.

## 11. The benchmark

Full design in the implementation plan, Phase 1. The shape, and the two
constraints that make it mean anything:

### 11.1 Each case runs in a deterministically reconstructed world

This is the load-bearing requirement. The corpora do **not** carry replayable
chat state:

- **`tests/golden/meals.py`** has no messages at all — its 9 cases are draft
  payloads addressed by 1-based member index, consumed by `drafts.create_draft`.
  A canonical Vietnamese message has to be *authored* per case for LLM replay.
- **`tests/golden/scenario_week.py`** has 21 steps, of which only **11** are
  LLM-replayable (`s1`–`s8`, `s9b`, `s10b`, `s12`). The other 10 carry no
  `message`: two are `confirm_pending` button presses, and **eight are the
  `s11a`–`s11h` payments that zero the ledger** — which is exactly what `s12`'s
  `expect: {empty: True}` depends on.

So replaying history as *chat text* creates zero ledger rows, and every
mid-scenario `ledger_state` expectation fails. Worse, it fails **identically on
both engines**, so `--compare` reports "no change" — the precise false
equivalence the harness exists to prevent.

The runner must therefore reconstruct each case's world the way
`tests/test_scenario_week.py:49-115` does — `drafts.create_draft` /
`commit_draft`, `ledger.record_payment`, `Member` inserts — for every prior step,
including the message-less ones, and only then run the case's own message through
the LLM.

Room seeding matters for the same reason: `tests/test_ledger._seed_room` creates
`M1..Mn` with **no bank details**, while `scenario_week.MEMBERS` gives `a1`/`a2`/
`a4` banks precisely "so QR builds succeed" (`scenario_week.py:4`). Seeded the
wrong way, `make_qr_url` raises `QRError` for every payee and `qr_payees` can
never pass.

### 11.2 One run per engine cannot separate a regression from noise

Both engines are nondeterministic, and they are different models. A single
Cursor run as reference against a single Pi run as candidate makes a verdict flip
indistinguishable from sampling variance — and the corpus is small (9 meals + 11
week + prod).

So `bench/run.py` takes `--repeat N` (default 3), the graders record per-case
**pass rates**, and the ship criterion is expressed as a pass-rate drop
threshold, not raw verdict flips.

### 11.3 The rest

- **Prod fixtures** — pulled from the existing read-only
  `/internal/debug/conversation.csv`, pseudonymized, and stripped of bank
  details. See §11.4 — the naive version of this is unsafe.
- **Four graders** — `tool_selection` (+ money-arg subset), `ledger_state`
  (final balances / ordered transfers / QR payees), `prose_quality`
  (`moneyguard.unbacked_amounts` as a deterministic pre-check, then an LLM
  judge), and `cost_latency` (reported, never pass/fail).
- **Report** — per-case grid plus a `--compare` mode that diffs two runs by pass
  rate.

### 11.4 ⚠️ Bank details reach the corpus through message bodies, not attachments

The obvious sanitizer scrubs `account_number` / `account_holder` / `bank_code`
as **attachment keys**. That is not sufficient, because bank details enter this
system *through chat*: `add_member` and `update_member` accept `bank_code`,
`account_number`, and `account_holder` as tool arguments (`tools.py:178-211`),
which means a real message body reads something like

> `@bot cập nhật stk của tôi 0071000123456 VCB NGUYEN VAN A`

and `body` is exactly what the corpus **keeps**, amounts and all. An
`account_holder` is an uppercase, de-diacriticized legal name that a
display-name map will never match.

So the sanitizer must additionally:

1. **Redact digit runs of 8 or more** in bodies. VND amounts in this corpus are
   ≤7 digits or carry a `k`/`tr`/`đ` unit, so this is nearly free.
2. Build the replacement map from `display_name` **+ `nickname` + `aliases` +
   `account_holder` variants**, matched on **word boundaries** — bare
   longest-first substring replacement mangles Vietnamese, because "An" occurs
   inside the ubiquitous pronoun "anh".
3. Have the manual pre-commit check grep for every real `account_number` and
   `account_holder` from the members table, not just display names.

**And the corpus does not have to be committed at all.** Nothing downstream needs
it in git — only on disk when `bench.run --corpus prod` executes. Committing the
case ids, the derived expectations (pseudonyms and integers), and the corpus
file's SHA-256, while gitignoring the corpus itself, turns "a leak here is an
unrecoverable privacy incident" into "a leak here cannot happen via git." That is
the recommended default; committing the bodies is the opt-in.

### 11.5 Sequencing is load-bearing

The cutover is a hard one: Cursor gets ripped out, not kept behind a flag. That
conflicts with proving equivalence unless the baseline is captured **first**.

1. Build the whole harness against the **current Cursor engine**; commit
   `bench/results/baseline-cursor.json`.
2. Only then replace the engine.
3. Diff Pi against that recorded baseline.

Skip step 1 and "exactly the same behavior" becomes unfalsifiable — the report
could only say "Pi passes the tests we wrote", which is a weaker claim.

The judge must be pinned across both runs. A baseline graded with no judge (or a
different one) against a Pi run graded with one is not a comparison, so
`BENCH_JUDGE_MODEL` and its key are a requirement of the **baseline** task, not
just the final one.

## 12. Open risk: vision

The catalogue turned out to be reachable, and it confirms what web sources
reported: every DeepSeek V4 variant, including the chosen
`~deepseek/deepseek-v4-flash-latest`, has **text-only input modalities**. See the
plan's Task 0 for the measured output.

Bill photos are load-bearing here: `images.py`, `_build_message`, the
`# Ảnh kèm theo` prompt section, `test_bill_image_carryover.py`, and
`moneyguard`'s own field note — *"of the alerts that survive a tool-output
allow-set, all but one were prices the model read off a bill photo."*

Mitigation, wired from the start rather than bolted on: `PI_VISION_MODEL` is a
separate setting. A turn carrying images resolves to that model (via
`session.setModel` / `scopedModels`); text-only turns use `PI_MODEL`. With a
text-only primary the branch is **live code**, not a dormant safeguard:
`PI_VISION_MODEL=qwen/qwen3-vl-30b-a3b-instruct` carries every bill photo.

**A vision model must be probed, not trusted.** `tools: true` in a catalogue is a
datasheet claim: the first model configured here passed it and then emitted nothing
at all for `propose_meal` — the one tool a bill turn must end in — on both text and
a real bill image. `bench/probe_models.py` sends the live schemas and the committed
bill PNG and is the gate any replacement has to clear.

Its context window is **262,144** against the primary's **1,048,576**, and an image
turn is the heaviest turn in the system — so the branch has to trim the history
window as well as swap the model. Sizing the text path against 1M and then routing
the largest turns into a 262k window is how a long-lived room starts failing only on
bill photos.

**Verify the modality before writing the sidecar — and verify tool-calling
support for the vision model too.** A bill-photo turn ends in `propose_meal`;
a vision model that cannot call tools breaks the money path just as thoroughly as
one that cannot see. If the primary is text-only and no tool-capable vision model
is configured, image turns must **fail loudly**, never silently drop the photo —
a dropped bill means the model invents the total.

The benchmark cannot cover this path from the existing corpora (no images in the
golden data; prod images are stripped), so it gains 2–3 **synthetic bill-image
cases with known totals**. Otherwise the riskiest path in the system ships on one
manual check.

## 13. Deferred, deliberately

`_strip_narration` exists because Cursor's agent narrated its skill reads ("Mình
đọc skill…") and glued that onto the answer. With skills injected in-memory and
no `read` tool, the narration may simply be gone. It moves to `turn.js` unchanged
and is only removed if the benchmark corpus shows **zero** narration hits.
Measure, don't guess.
