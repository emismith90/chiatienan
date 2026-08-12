# Cursor SDK → Pi harness + equivalence benchmark — Implementation Plan

> **For agentic workers:** implement task-by-task, in order. Steps use checkbox
> (`- [ ]`) syntax for tracking. Every task ends in a commit. Do not batch tasks.

**Design:** [`../specs/2026-08-12-cursor-to-pi-harness-design.md`](../specs/2026-08-12-cursor-to-pi-harness-design.md)

**Goal:** Replace the Cursor SDK engine with Pi (`@earendil-works/pi-coding-agent`)
on OpenRouter, with **no behavior change**, and prove it with a benchmark that
replays the golden datasets and real production conversations.

**Architecture:** A Node sidecar (`backend/agent_sidecar/`) owns the whole
harness — provider, session, event stream, turn caps, answer assembly. Python
keeps the data and content: SQLite, the ledger, the 14 money tools, the
Vietnamese prompt. They speak JSONL over stdio; tool calls round-trip back into
Python so no arithmetic ever crosses to the model's side (design D3).

**Tech Stack:** Python 3.11/3.12 · FastAPI · SQLAlchemy · SQLite (WAL) · pytest.
Node ≥22.19 · plain ESM JavaScript (no build step) · `node --test`.

---

## Global Constraints

- All money is **integer VND**. Tools own every number; the LLM never computes or
  re-types a tool-produced amount (**design D3**). Nothing in this plan may move
  arithmetic into `agent_sidecar/`.
- `run_turn`'s signature and `TurnResult`'s shape are **frozen**:
  `run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None) -> TurnResult`
  with `final_text`, `tools`, `error`, `turn_id`, `last_result()`, `all_results()`.
  ~30 tests monkeypatch it; `chat.py` is the only caller. Keeping this contract is
  what makes the port small.
- The `agent.*` SSE event names are **frozen** — the frontend
  (`frontend/src/hooks/use-room.ts:45-90`, `components/chat/agent-timeline.tsx`)
  consumes them. The sidecar emits them in final form.
- Backend tests run from `backend/` with `pytest`; sidecar tests from
  `backend/agent_sidecar/` with `node --test`; frontend from `frontend/` with
  `npm test`.
- Dates are **ICT**. Freeze time by monkeypatching `app.clock.now_ict`, **never**
  `today_ict` (it is import-bound in `ledger`/`drafts`/`tools`).
- The backend stays a **single process** — `chat._agent_lock` and `realtime.hub`
  are in-process state. Do not add `--workers`.
- **No secret, real name, bank account, or `qr_url` may be committed.** The prod
  corpus is sanitized and the sanitizer is CI-tested.
- Plain ESM JS in the sidecar, no TypeScript, no bundler: the source that ships is
  the source that runs.

---

## Phase 0 — Resolve the vision question

### Task 0: Verify `deepseek/deepseek-v4-flash` modality

**Blocking.** If this model cannot accept images, bill-photo reading breaks, and
that is a headline feature. Resolve it before writing any sidecar code.

- [ ] **Step 1: Confirm the key resolves**

```bash
test -n "$OPENROUTER_API_KEY" && echo "key present" || echo "KEY MISSING"
```

If missing, stop and report — Phases 1–5 can still proceed (the Cursor baseline
uses `CURSOR_API_KEY`), but note it and revisit before Phase 6.

- [ ] **Step 2: Query the catalogue**

```bash
curl -sS https://openrouter.ai/api/v1/models \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
| python3 -c '
import json,sys
for m in json.load(sys.stdin)["data"]:
    if "deepseek-v4" in m["id"]:
        a = m.get("architecture", {})
        print(m["id"], a.get("input_modalities"), a.get("output_modalities"),
              "tools" in (m.get("supported_parameters") or []), m.get("pricing"))'
```

- [ ] **Step 3: Record the finding and decide `PI_VISION_MODEL`**

Write the actual output into this plan under this task. Then:

- `input_modalities` includes `image` → set `PI_VISION_MODEL` = `PI_MODEL`; the
  vision branch stays but is inert.
- text-only → pick a vision-capable OpenRouter model for `PI_VISION_MODEL` and
  say which, with its cost. Image turns route there.
- `tools` **not** in `supported_parameters` → **stop**. Tool calling is
  non-negotiable; the model choice has to change and that is a user decision.

- [ ] **Step 4: Commit the finding**

```bash
git add docs/superpowers/plans/2026-08-12-cursor-to-pi-harness.md
git commit -m "Record the deepseek-v4-flash modality finding for the Pi port"
```

---

## Phase 1 — Benchmark harness on Cursor, and the baseline

> **Why this comes first:** the cutover is hard — Cursor gets deleted, not kept
> behind a flag. Once it is gone there is nothing to compare against, so the
> baseline must be recorded while it still runs. Phase 2 does not start until
> `bench/results/baseline-cursor.json` exists.

### Task 1: `bench` package + corpus loaders

**Files:**
- Create: `backend/bench/__init__.py`, `backend/bench/corpus.py`
- Create: `backend/tests/test_bench_corpus.py`

**Interfaces:**
- Produces: `bench.corpus.load(name) -> list[Case]`, where `name` ∈
  `{"meals", "week", "prod", "all"}`.
- `Case` is a dataclass: `id, source, day, actor, history, message, had_images, expect, seed`.

`bench/` sits outside `testpaths` (which is `["tests"]`) so it never runs in CI by
accident.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_bench_corpus.py`:

```python
def test_meals_corpus_loads_all_golden_cases():
    from bench.corpus import load
    cases = load("meals")
    assert [c.id for c in cases] == ["G1","G2","G3","G4","G5","G6","G7","G8","G12"]
    assert all(c.source == "meals" for c in cases)


def test_week_corpus_skips_ui_only_steps():
    from bench.corpus import load
    cases = load("week")
    # confirm_pending is a button press, not an LLM turn
    assert "s9a" not in {c.id for c in cases}
    assert "s1" in {c.id for c in cases}
    assert next(c for c in cases if c.id == "s1").message == "@bot tôi trả 300k cả nhóm"


def test_prod_corpus_is_empty_until_exported():
    from bench.corpus import load
    assert load("prod") == []          # no fixture committed yet
```

- [ ] **Step 2: Run to verify it fails** — `cd backend && pytest tests/test_bench_corpus.py -v`
      → `ModuleNotFoundError: bench`.

- [ ] **Step 3: Implement**

`bench/corpus.py` imports `tests.golden.meals.CASES` and
`tests.golden.scenario_week.{MEMBERS, STEPS}` **directly** — they stay the single
source of truth, no copies. Skip `kind == "confirm_pending"` steps and any step
without a `message`. `load("prod")` reads
`bench/corpus/prod_conversations.json` if present, else returns `[]`.

- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `bench: corpus loaders over the existing golden datasets`

---

### Task 2: `tool_selection` grader

**Files:** Create `backend/bench/graders.py`; add to `backend/tests/test_bench_graders.py`

**Interfaces:** `grade_tool_selection(case, record) -> Verdict(passed: bool, reason: str)`

- [ ] **Step 1: Write the failing test**

```python
MONEY_ARGS = ("total", "payer", "participants", "from", "to", "amount", "items")

def test_extra_scaffolding_tools_do_not_fail_a_case():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"]})
    rec  = _record(tools=[("find_members", {}), ("propose_meal", {"total": 300000})])
    assert grade_tool_selection(case, rec).passed


def test_wrong_money_arg_fails_even_when_the_tool_is_right():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"], "args": {"propose_meal": {"total": 300000}}})
    rec  = _record(tools=[("propose_meal", {"total": 30000})])
    v = grade_tool_selection(case, rec)
    assert not v.passed and "total" in v.reason


def test_non_money_args_are_ignored():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"], "args": {"propose_meal": {"total": 300000}}})
    rec  = _record(tools=[("propose_meal", {"total": 300000, "dish": "bún bò", "note": "x"})])
    assert grade_tool_selection(case, rec).passed
```

- [ ] **Step 2: Verify it fails.**

- [ ] **Step 3: Implement.** Expected tool ∈ called (superset-tolerant, matching
      `test_scenario_week_llm.py`'s current behavior), plus an arg-subset check
      restricted to `MONEY_ARGS`. Participant lists compare as **sets** — order is
      not meaningful. Nothing else is compared: prose, dish names, and notes are
      the model's business.

- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `bench: tool-selection grader that checks the money args`

---

### Task 3: `ledger_state` grader — extract, don't duplicate

**Files:** Modify `backend/bench/graders.py`, `backend/tests/test_scenario_week.py`

**Interfaces:** `grade_ledger_state(case, record, db, ids) -> Verdict`

The transfer/balance/QR comparison already exists inside
`tests/test_scenario_week.py`. **Move it into `bench/graders.py` and have the test
import it back**, so there is exactly one implementation. If the extraction is
correct, `test_scenario_week.py` still passes unchanged in behavior.

- [ ] **Step 1: Write the failing test** — assert `grade_ledger_state` rejects a
      transfer list that differs in order, rejects a missing `qr_url`, and accepts
      the real `s5` expectation from `scenario_week.STEPS`.

- [ ] **Step 2: Verify it fails.**

- [ ] **Step 3: Implement.** Extract `_balances`, the ordered-transfer comparison,
      the `f'{amount:,}' in body` render check against `chat._settlement_body`, and
      the `qr_payees` check. Keep the `settle_blocked` / `blocked_pending` branch.

- [ ] **Step 4: Verify both pass** —
      `pytest tests/test_bench_graders.py tests/test_scenario_week.py -v`.
      `test_scenario_week` passing after the extraction is the real assertion here.

- [ ] **Step 5: Commit** — `bench: extract the ledger-state comparison out of the week scenario test`

---

### Task 4: `prose_quality` grader

**Files:** Modify `backend/bench/graders.py`; add to `backend/tests/test_bench_graders.py`

**Interfaces:** `grade_prose(case, record, judge=None) -> Verdict`

Two stages, cheap first. Stage 1 is deterministic and reuses **existing**
production code: `app.moneyguard.unbacked_amounts(body, user_text, tools)`. It is
wired today as a report-only warning at `chat.py:562`; here it becomes a grade.
Stage 2 is an LLM judge, only reached if stage 1 passes.

- [ ] **Step 1: Write the failing test**

```python
def test_unbacked_amount_in_the_reply_fails_without_calling_the_judge():
    from bench.graders import grade_prose
    called = []
    case = _case(message="@bot 300k cả nhóm")
    rec  = _record(final_text="Đã ghi, mỗi người 75.000đ",
                   tools=[("propose_meal", {"total": 300000}, {"ok": True})])
    v = grade_prose(case, rec, judge=lambda *_: called.append(1))
    assert not v.passed
    assert called == []          # stage 1 short-circuits; no judge spend


def test_tool_backed_amounts_reach_the_judge():
    from bench.graders import grade_prose
    rec = _record(final_text="Đã ghi bữa trưa nhé",
                  tools=[("propose_meal", {"total": 300000}, {"ok": True, "per_head": 75000})])
    v = grade_prose(_case(), rec, judge=lambda *_: {"ok": True, "reason": "fine"})
    assert v.passed
```

- [ ] **Step 2: Verify it fails.**

- [ ] **Step 3: Implement.** Judge is injected, never constructed inside the
      grader — that keeps the test offline and lets Phase 6 swap the model via
      `BENCH_JUDGE_MODEL`. The judge rubric: replies in Vietnamese, answers what
      was asked, no narration of skill/tool selection, does not restate amounts the
      card already shows.

- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `bench: prose grader — moneyguard pre-check, then an LLM judge`

---

### Task 5: `cost_latency` grader + aggregation

**Files:** Modify `backend/bench/graders.py`; add to `backend/tests/test_bench_graders.py`

**Interfaces:** `summarize_cost_latency(records) -> dict` with
`{p50_s, p95_s, mean_tool_calls, total_tokens, total_cost_usd, n}`

Reported, **never** pass/fail — a slower engine that is correct is a business
decision, not a test failure.

- [ ] **Step 1: Write the failing test** — assert p50/p95 on a known list, and
      that missing `stats` (Cursor exposes no cost) yields `None` rather than `0`.
      Conflating "unknown" with "free" would make the comparison lie.

- [ ] **Step 2: Verify it fails.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `bench: aggregate latency and cost`

---

### Task 6: `bench/run.py` — the runner

**Files:** Create `backend/bench/run.py`; add `backend/tests/test_bench_run.py`

**Interfaces:** CLI
`python -m bench.run --corpus {meals,week,prod,all} --engine {cursor,pi} --out PATH`

Per case: fresh temp SQLite via `app.db.Database`, room seeded with the same shape
as `tests/test_ledger._seed_room`, clock frozen to noon ICT of `case.day` by
patching `app.clock.now_ict`, `case.history` replayed into `room_messages` so
`chat.build_history` renders it exactly as prod did, then `agent.run_turn(...)`.

- [ ] **Step 1: Write the failing test** — with `run_turn` monkeypatched to a stub,
      assert: one record per case; the record carries `tools`, `args`, `results`,
      `final_text`, `error`, `elapsed_s`; a case that raises is recorded as an
      error rather than aborting the run; and the frozen clock reaches the tool
      (assert a stub tool observes `case.day`).

- [ ] **Step 2: Verify it fails.**

- [ ] **Step 3: Implement.** One case failing must never kill the run — a
      benchmark that stops at the first error cannot report honestly. Write
      `bench/results/<engine>-<timestamp>.json`.

- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `bench: case runner with a frozen clock and replayed history`

---

### Task 7: `bench/export_prod.py` — prod fixtures, sanitized

**Files:** Create `backend/bench/export_prod.py`; add `backend/tests/test_bench_sanitize.py`

**Interfaces:**
- `sanitize(rows, name_map) -> list[dict]` (pure, the tested part)
- CLI `python -m bench.export_prod --room 1 --days 90 --out bench/corpus/prod_conversations.json`

Source is the existing read-only debug API (`app/debug_api.py`, guarded by
`X-Debug-Key`; see `deploy/DEBUGGING.md` §6):

```
GET /internal/debug/conversation.csv?room_id=1&days=90
  → id, room_id, created_at, kind, author_member_id, author, body, attachments
```

**This is the highest-risk task in the plan.** A mistake here commits real names
and bank accounts to a git history that cannot be rewritten. The sanitizer is
pure and tested first; the network fetch is a thin wrapper around it.

- [ ] **Step 1: Write the failing test** — every one of these is a redaction
      requirement, not a nice-to-have:

```python
def test_bank_details_are_stripped_from_attachments():
    out = sanitize([_row(attachments={"transfers": [
        {"to": "Linh", "amount": 100000, "account_number": "0123456789",
         "account_holder": "NGUYEN VAN A", "bank_code": "VCB",
         "qr_url": "https://img.vietqr.io/image/VCB-0123456789-compact2.png?amount=100000"}]})])
    blob = json.dumps(out)
    for secret in ("0123456789", "NGUYEN VAN A", "VCB", "img.vietqr.io"):
        assert secret not in blob


def test_names_are_pseudonymized_in_the_body_too_not_just_the_author():
    out = sanitize([_row(author="Linh", body="@bot Linh trả 300k, trừ Emi")],
                   name_map={"Linh": "A1", "Emi": "A2"})
    assert out[0]["author"] == "A1"
    assert "Linh" not in out[0]["body"] and "Emi" not in out[0]["body"]
    assert out[0]["body"] == "@bot A1 trả 300k, trừ A2"


def test_base64_images_become_the_anh_marker():
    out = sanitize([_row(attachments={"images": [{"data": "iVBORw0KGgo…"}]})])
    assert "iVBORw" not in json.dumps(out)
    assert out[0]["had_images"] == 1


def test_amounts_survive_because_they_are_what_is_graded():
    out = sanitize([_row(body="@bot 324200 grab")])
    assert "324200" in out[0]["body"]


def test_invite_tokens_and_pins_are_stripped():
    out = sanitize([_row(attachments={"invite_token": "abc123", "pin": "4321"})])
    blob = json.dumps(out)
    assert "abc123" not in blob and "4321" not in blob
```

- [ ] **Step 2: Verify they fail.**

- [ ] **Step 3: Implement `sanitize`.** Denylist keys recursively:
      `account_number`, `account_holder`, `bank_code`, `invite_token`, `pin`,
      `qr_url`, `data`. Replace name occurrences longest-first so "An" inside
      "Anh" is not corrupted. Emit `had_images` and drop the bytes.

- [ ] **Step 4: Verify they pass.**

- [ ] **Step 5: Add the fetch + expectation bootstrap.** Derive `expect` from what
      prod actually did — the bot's own reply row and its `attachments` record the
      tool and the numbers. Any case whose expectation cannot be derived gets
      `"review": true`; `bench/corpus.py` skips those until a human clears them.
      `had_images: 1` cases are graded on tool selection only (the photo is gone,
      so the reply cannot be faithfully reproduced).

- [ ] **Step 6: Export, then read the output with your own eyes** before adding it
      to git. Grep the file for every real member name and for `vietqr` as a final
      check. The test suite is a net, not a substitute for looking.

- [ ] **Step 7: Commit** — `bench: sanitized production conversation corpus`

---

### Task 8: `bench/report.py`

**Files:** Create `backend/bench/report.py`; add `backend/tests/test_bench_report.py`

**Interfaces:**
- `python -m bench.report RESULTS.json` → markdown
- `python -m bench.report --compare BASE.json NEW.json` → equivalence table

- [ ] **Step 1: Write the failing test** — the compare mode must surface a case
      that regressed, a case that *improved*, and must not hide a case present in
      one run but missing from the other. A silently dropped case reads as "no
      change" and that is the failure mode this whole harness exists to prevent.

- [ ] **Step 2: Verify it fails.**

- [ ] **Step 3: Implement.** Per-case grid (one column per grader), per-grader
      aggregate, latency percentiles, cost total. Compare mode: verdict flips in
      both directions, tool-selection diffs, latency/cost delta, and an explicit
      `MISSING` row for asymmetric cases.

- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `bench: markdown report and run-vs-run comparison`

---

### Task 9: GATE — capture the Cursor baseline

**Files:** Create `backend/bench/results/baseline-cursor.json`, `backend/bench/results/baseline-cursor.md`

- [ ] **Step 1: Full CI run first** — `cd backend && pytest -q`. The harness must
      be green before its output means anything.

- [ ] **Step 2: Run the corpus against Cursor**

```bash
cd backend
RUN_LLM_EVAL=1 python -m bench.run --corpus all --engine cursor \
  --out bench/results/baseline-cursor.json
python -m bench.report bench/results/baseline-cursor.json > bench/results/baseline-cursor.md
```

- [ ] **Step 3: Read the report.** Cases that fail *on Cursor* are expected — this
      is a baseline, not a target. Record them, because a Pi run that fails the
      same cases is equivalent, not broken.

- [ ] **Step 4: Commit** — `bench: record the Cursor baseline over both corpora`

> **Do not proceed to Phase 2 until this file is committed.**

---

## Phase 2 — The TypeScript harness

### Task 10: Sidecar skeleton + schema fixture

**Files:**
- Create: `backend/agent_sidecar/{package.json,.gitignore}`, `backend/agent_sidecar/test/.gitkeep`
- Create: `backend/bench/dump_schemas.py`
- Create: `backend/agent_sidecar/test/fixtures/tool-schemas.json`
- Create: `backend/tests/test_tool_schemas_fixture.py`

**Interfaces:** the fixture is the contract between the runtimes — a JSON object
mapping all 14 tool names to their resolved `input_schema`.

- [ ] **Step 1: Write the failing test** — assert the committed fixture matches
      what `build_tools` produces *right now*, so the two runtimes cannot drift:

```python
def test_committed_schema_fixture_matches_the_live_tools(db):
    from app.tools import build_tools, ToolContext
    live = {n: t.input_schema for n, t in build_tools(ToolContext(db=db, room_id=1)).items()}
    fixture = json.loads(Path("agent_sidecar/test/fixtures/tool-schemas.json").read_text())
    assert fixture == live, "regenerate with: python -m bench.dump_schemas"
```

- [ ] **Step 2: Verify it fails** (no fixture yet).

- [ ] **Step 3: Implement** `bench/dump_schemas.py` (resolves the subscript
      references like `_PERIOD_SCHEMA["properties"]["keyword"]` by importing the
      real module) and generate the fixture. `package.json`: `type: "module"`,
      deps `@earendil-works/pi-coding-agent`, `@earendil-works/pi-ai`, `typebox`;
      `"test": "node --test"`. Commit `package-lock.json` — deploys must be
      reproducible.

- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `sidecar: package skeleton and committed tool-schema fixture`

---

### Task 11: `schema.js` — JSON Schema → TypeBox

**Files:** Create `backend/agent_sidecar/schema.js`, `backend/agent_sidecar/test/schema.test.js`

**Interfaces:** `toTypeBox(jsonSchema) -> TSchema`

Only six keywords need support — `type`, `properties`, `required`, `items`,
`description`, `enum` — verified by dumping all 14 schemas. See design §6 for the
mapping table.

- [ ] **Step 1: Write the failing test** — cover each mapping, and give the two
      genuinely dangerous cases their own assertions:

```js
test("string enum uses StringEnum, not Type.Union", () => {
  const s = toTypeBox({type:"object", properties:{
    keyword:{type:"string", enum:["since_last","this_week"]}}});
  // Type.Union/Type.Literal breaks Google's API — pi docs are explicit
  assert.deepEqual(s.properties.keyword.enum, ["since_last","this_week"]);
  assert.equal(s.properties.keyword.anyOf, undefined);
});

test("union type target accepts both string and integer", () => {
  // tools.py:193 and :216 — {"type": ["string","integer"]}
  const s = toTypeBox({type:"object", properties:{
    target:{type:["string","integer"]}}, required:["target"]});
  assert.ok(Value.Check(s, {target: "linh"}));
  assert.ok(Value.Check(s, {target: 7}));
  assert.ok(!Value.Check(s, {target: true}));
});

test("non-required properties are Optional", () => {
  const s = toTypeBox({type:"object", properties:{a:{type:"string"},b:{type:"string"}},
                       required:["a"]});
  assert.ok(Value.Check(s, {a:"x"}));
  assert.ok(!Value.Check(s, {b:"x"}));
});

test("every one of the 14 real schemas converts and validates", () => {
  const fixture = JSON.parse(readFileSync("./test/fixtures/tool-schemas.json"));
  assert.equal(Object.keys(fixture).length, 14);
  for (const [name, s] of Object.entries(fixture)) {
    assert.doesNotThrow(() => toTypeBox(s), name);
  }
});
```

Also assert a realistic `propose_meal` payload validates end to end — nested
`items` objects with `{member, amount, label}`, an `adjustments` array, and
`discount_split: "proportional"`.

- [ ] **Step 2: Verify it fails** — `cd backend/agent_sidecar && npm test`.
- [ ] **Step 3: Implement** the recursive converter. Throw loudly on an
      unsupported keyword rather than silently dropping it — a dropped constraint
      means the model sends garbage the tool then rejects.
- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `sidecar: JSON Schema to TypeBox converter`

---

### Task 12: `main.js` — the JSONL RPC loop

**Files:** Create `backend/agent_sidecar/main.js`, `backend/agent_sidecar/test/rpc.test.js`

**Interfaces:** stdin/stdout JSONL. Commands `run`, `tool_result`, `summarize`,
`ping`. See design §4 for every payload.

- [ ] **Step 1: Write the failing test** — drive the process with a stubbed
      session so no API key is needed. This is the cheapest possible test of the
      protocol and it must exist before `session.js`:
  - `ping` → a response line
  - a `run` whose stub session calls one tool → emits `agent.tool.start`, then
    `tool_call`, blocks, and only after a `tool_result` line arrives emits
    `agent.tool.result` and finally `turn_done`
  - **framing:** a line split across two stdin chunks is handled; a `\r\n` line
    ending is tolerated (pi's docs warn generic Unicode line readers are
    incompatible, so read bytes and split on `\n` yourself)
  - a malformed line is reported as an error and does **not** kill the process —
    one bad turn must not take the bot down until the next deploy

- [ ] **Step 2: Verify it fails.**
- [ ] **Step 3: Implement.** Correlate `tool_call`/`tool_result` by `call_id` with
      a pending-promise map.
- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `sidecar: JSONL RPC loop with a tool-call round-trip`

---

### Task 13: `session.js` + `proxyTool`

**Files:** Create `backend/agent_sidecar/session.js`, `backend/agent_sidecar/test/proxy-tool.test.js`

**Interfaces:** `buildSession(req, { onEvent, callTool }) -> { session, dispose }`

- [ ] **Step 1: Write the failing test** — the `ok:false` semantics get first
      billing, because getting them backwards is a corpus-wide regression that no
      other test catches:

```js
test("ok:false is a content block, NOT a throw", async () => {
  // tools.py:8 — a validation failure is a clarifying question, not an error.
  // Throwing would make the model apologize instead of asking.
  const tool = proxyTool({name:"propose_meal", description:"d", schema:{type:"object"}},
                         async () => ({ok:false, error:"Ngày nào?"}));
  const r = await tool.execute("call-1", {});
  assert.equal(r.content[0].type, "text");
  assert.match(r.content[0].text, /Ngày nào\?/);
});

test("transport death does throw", async () => {
  const tool = proxyTool({name:"x", description:"d", schema:{type:"object"}},
                         async () => { throw new Error("bridge died"); });
  await assert.rejects(() => tool.execute("call-2", {}));
});

test("the tool result dict is preserved in details", async () => {
  const payload = {ok:true, transfers:[{amount:100000}]};
  const tool = proxyTool({name:"settle_period", description:"d", schema:{type:"object"}},
                         async () => payload);
  assert.deepEqual((await tool.execute("c", {})).details, payload);
});
```

- [ ] **Step 2: Verify it fails.**

- [ ] **Step 3: Implement.** Per design §5:
  - `const OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1";` — a module
    constant, no env override.
  - `ResourceLoader` with `systemPromptOverride`, `agentsFilesOverride`,
    `skillsOverride`, `extensionFactories`.
  - `tools: []` — no `bash`/`read`/`write`/`edit`/`grep`.
  - `SessionManager.inMemory(cwd)`, fresh session per `run`.
  - Images route to `PI_VISION_MODEL` per Task 0's finding; if a turn has images
    and no vision model is configured, **fail the turn loudly** — never silently
    drop the photo.

- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `sidecar: session construction and the Python tool proxy`

---

### Task 14: `turn.js` — event normalization and answer assembly

**Files:** Create `backend/agent_sidecar/turn.js`, `backend/agent_sidecar/test/turn.test.js`

**Interfaces:** `runTurn(session, req, emit) -> {final_text, tools, error, stats}`

This is where the Python logic being deleted lands. Port `_final_answer`,
`_split_at_seams`, `_is_narration`, `_strip_narration` from `app/agent.py`
**including their comments** — they document production incidents, and a comment
that explains a bug is worth more than the code it sits above.

- [ ] **Step 1: Write the failing test** — port the assertions from
      `backend/tests/test_agent.py`, plus:

```js
test("text fragments join with empty string, never a separator", () => {
  // Joining with a separator put a blank line between every token and shredded
  // Vietnamese diacritics into "V ẫn không được đâu Kun" in production.
  assert.equal(finalAnswer(["Không", "—", "hiện", " tại"], 0), "Không—hiện tại");
});

test("narration before the last tool call is dropped", () => {
  assert.equal(finalAnswer(["Mình đọc skill record-meal.", "Đã ghi bữa trưa."], 1),
               "Đã ghi bữa trưa.");
});

test("an all-narration reply is kept rather than emptied", () => {
  // A bad reply still beats an empty bubble.
  assert.notEqual(stripNarration("Mình đang kiểm tra."), "");
});

test("max_tools breach aborts the session", async () => { /* … */ });
test("max_seconds breach aborts the session", async () => { /* … */ });
```

- [ ] **Step 2: Verify it fails.**

- [ ] **Step 3: Implement.** Normalize pi's `message_update` / `tool_execution_*`
      / `agent_*` / `auto_retry_*` events to the frozen `agent.*` names (design
      §4.2). Enforce caps → `session.abort()`. Collect `stats` from
      `get_session_stats`. Format any failure into the single `error` string
      `chat.py` already expects.

- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `sidecar: event normalization, turn caps, answer assembly`

---

## Phase 3 — The Python shim

### Task 15: `app/pi_bridge.py`

**Files:** Create `backend/app/pi_bridge.py`, `backend/tests/test_pi_bridge.py`

**Interfaces:** `PiBridge.send(cmd) -> None`, `PiBridge.events() -> AsyncIterator[dict]`,
`PiBridge.ensure_started()`

Subprocess and framing **only**. No model logic, no event logic, no pi vocabulary
beyond the message `type` strings. If this file starts branching on pi semantics,
that logic belongs in `turn.js`.

- [ ] **Step 1: Write the failing test** — with a fake `node` that echoes canned
      JSONL: startup retries 3× with `1.5**attempt` backoff (the shape
      `_launch_bridge_resilient` uses today, because a child dying at startup is
      not a Cursor-specific problem); a missing `OPENROUTER_API_KEY` raises a clear
      error at spawn naming the variable; the parent env reaches the child; a dead
      child is restarted on the next `ensure_started`.

- [ ] **Step 2: Verify it fails.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `Bridge to the Pi sidecar subprocess`

---

### Task 16: `tools.py` — drop the `cursor_sdk` import

**Files:** Modify `backend/app/tools.py`; modify `backend/tests/test_tools.py`

**Interfaces:** `app.tools.CustomTool` (local dataclass: `execute`, `description`,
`input_schema`) and a new `tool_manifest() -> list[dict]`.

All 14 registrations and **every executor body stay byte-identical**. This task
must not touch a single line of arithmetic.

- [ ] **Step 1: Write the failing test**

```python
def test_tool_manifest_covers_every_tool_with_a_schema():
    from app.tools import build_tools, tool_manifest, ToolContext
    names = set(build_tools(ToolContext(db=db, room_id=1)))
    manifest = {t["name"] for t in tool_manifest()}
    assert manifest == names
    assert all(t["description"] and t["schema"]["type"] == "object" for t in tool_manifest())


def test_tools_module_no_longer_imports_cursor_sdk():
    assert "cursor_sdk" not in Path("app/tools.py").read_text()
```

- [ ] **Step 2: Verify it fails.**
- [ ] **Step 3: Implement.** Replace the import with the dataclass; add
      `tool_manifest()`. Nothing else changes.
- [ ] **Step 4: Verify the whole tools suite passes** —
      `pytest tests/test_tools.py tests/test_tools_payment.py tests/test_itemized_split.py -q`.
      All of it must be green with zero edits to the assertions; that is the proof
      the tools were untouched.
- [ ] **Step 5: Commit** — `Drop cursor_sdk from tools for a local CustomTool dataclass`

---

### Task 17: `app/agent.py` — rewrite the body, freeze the surface

**Files:** Rewrite `backend/app/agent.py`; rewrite `backend/tests/test_agent.py`

**Interfaces:** unchanged — `run_turn`, `TurnResult`, `ToolInvocation`,
`last_result`, `all_results`.

- [ ] **Step 1: Write the failing test** — rewrite `test_agent.py`'s fakes as a
      fake JSONL bridge (feed it lines; simpler than the old
      `_FakeClient/_FakeAgents/_FakeAgent/_FakeRun` stack). Keep the existing
      assertions about prompt assembly order (`# Bộ nhớ dài hạn` →
      `# Lịch sử hội thoại (gần đây)` → `# Tin nhắn người dùng`) and the
      setup-failure case that must still emit a finish event. Delete the MCP
      envelope tests — that envelope no longer exists.

- [ ] **Step 2: Verify it fails.**

- [ ] **Step 3: Implement**, ~80 lines:
  1. Build the `run` command — `build_system_prompt()` as `system`,
     `_render_prompt` for the message (memory / history / image-count sections kept
     verbatim; the system prompt is **no longer prepended**), `tool_manifest()`,
     skills read from `app/agent_skills/skills/`, `money-safety.mdc` as a context
     file.
  2. Loop over events: `agent.*` → `await emit(ev)` untouched; `tool_call` →
     `build_tools(ctx)[name].execute(args)` → reply `tool_result`; `turn_done` →
     hydrate `TurnResult`.
  3. Keep the one-line-per-turn `logger.info` summary — it is the only persisted
     record of where a 20–80s turn went, and `/internal/debug/logs` serves it.

- [ ] **Step 4: Verify the whole suite passes.** `chat.py`'s ~30 monkeypatching
      tests must pass with **zero edits** — that is the real assertion that the
      contract held.

- [ ] **Step 5: Commit** — `Run turns through the Pi sidecar`

---

### Task 18: `summarize.py` + `pi_smoke.py`

**Files:** Rewrite `backend/app/summarize.py`; rename `backend/app/bridge_smoke.py` →
`backend/app/pi_smoke.py`; modify `backend/app/main.py`, `backend/tests/test_summarize.py`

- [ ] **Step 1: Write the failing test** — `summarize_messages` returns the text
      the sidecar sent; **any** failure returns `""` (a failed summary must never
      crash a turn); `/internal/bridge-smoke` keeps its path, its admin guard, and
      its `{ok, elapsed_s, messages_seen, text}` shape, so `deploy/DEBUGGING.md`
      and the deploy skill stay accurate.

- [ ] **Step 2: Verify it fails.**
- [ ] **Step 3: Implement.** `summarize` becomes send-text/receive-text; smoke
      becomes `{"type":"ping"}`.
- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `Summarize and smoke-check through the sidecar`

---

### Task 19: `config.py` + `memory.py` — `DATA_DIR`

**Files:** Modify `backend/app/config.py`, `backend/app/memory.py`,
`backend/app/main.py`, `backend/tests/conftest.py`, `backend/tests/test_config.py`,
`backend/tests/test_memory.py`

**Interfaces:** new settings per design §9. `CURSOR_*` all removed.

The rename is not cosmetic: with `tools: []` and in-memory skills the agent needs
no persistent workspace, but `memory.py` keeps room memory on the mounted volume.

- [ ] **Step 1: Write the failing test** — defaults for every new setting;
      `memory.room_memory_dir` resolves under `DATA_DIR/rooms/{id}/`;
      `_warn_if_workspace_is_ephemeral` still warns for a path outside `/data`
      (it now protects room memory, which was always its real subject);
      `Settings` has no `cursor_*` attribute left.

- [ ] **Step 2: Verify it fails.**
- [ ] **Step 3: Implement.** `conftest.py` swaps `CURSOR_SDK_WORKSPACE` →
      `DATA_DIR` in its before-import assignment block (those are **assigned**,
      not `setdefault`-ed, on purpose — an ambient prod `.env` once made the suite
      write to the real ledger).
- [ ] **Step 4: Verify the full suite passes.**
- [ ] **Step 5: Commit** — `Rename the workspace setting to DATA_DIR, drop every CURSOR_*`

---

## Phase 4 — Delete Cursor

### Task 20: Remove the engine and every reference

**Files:** delete `backend/app/{cursor_runner,agui,skills}.py`,
`backend/tests/{test_agui,test_skills_materializer}.py`; modify
`backend/pyproject.toml`, `.env.example`, `.github/workflows/deploy.yml`,
`deploy/README.md`, `deploy/DEBUGGING.md`, `README.md`, `PRIVACY.md`,
`.gitignore`, `.claude/skills/run-chiatienan/{SKILL.md,run.sh}`

- [ ] **Step 1: Delete the three modules and their tests.** `agui.py` goes because
      the sidecar emits its output format directly; `skills.py` because
      `ResourceLoader` takes skills programmatically.

- [ ] **Step 2: Drop `cursor-sdk>=0.1.7`** from `pyproject.toml` and update the
      project description.

- [ ] **Step 3: Sweep the prose and config.** `README.md` needs real edits, not
      just find-and-replace: the architecture diagram's `agent.py Cursor SDK`
      line, and the module table rows for `agent.py / cursor_runner.py` and
      `agui.py`. Add `agent_sidecar/` to the table. `.gitignore` loses
      `.cursor-store/`, gains `agent_sidecar/node_modules/`.

- [ ] **Step 4: Verify nothing is left**

```bash
cd /home/user/chiatienan
grep -rniE "cursor_sdk|cursor-sdk|CURSOR_(API|SDK|AGENT)" \
  --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=reference . \
  && echo "STILL REFERENCED — fix before committing" || echo "clean"
cd backend && pytest -q
```

- [ ] **Step 5: Raise `reference/sample-cursor-sdk-with-image/` with the user.**
      ~40 files of dead Cursor sample whose only purpose was as this port's
      source. Recommend deleting it; **do not delete it silently** — it is
      explicitly kept as reference material and that call is the user's.

- [ ] **Step 6: Commit** — `Remove the Cursor SDK engine and every reference to it`

---

## Phase 5 — Docker, CI, deploy

### Task 21: Ship it

**Files:** Modify `backend/Dockerfile`, `.github/workflows/ci.yml`,
`.github/workflows/deploy.yml`, `.env.example`

- [ ] **Step 1: `backend/Dockerfile`** — install Node ≥22.19 (NodeSource on
      `python:3.12-slim`), `COPY agent_sidecar`, then
      `npm ci --omit=dev --ignore-scripts` (the install flags specified for this
      package). Drop the `git`+`curl` comment about the cursor bridge toolchain.
      **Keep the single-worker `CMD` and its warning verbatim** — `_agent_lock`
      and `realtime.hub` still assume one process owns the app.

- [ ] **Step 2: `ci.yml`** — add a `sidecar` job: `node-version: 22`, `npm ci`,
      `node --test`, with `cache-dependency-path: backend/agent_sidecar/package-lock.json`.
      The LLM eval stays opt-in and out of CI, same as `RUN_LLM_EVAL` today.

- [ ] **Step 3: `deploy.yml`** — point at the **existing** `OPENROUTER_API_KEY`
      secret instead of `CURSOR_API_KEY`; add the `PI_*` vars; remove the
      `CURSOR_API_BASE` var and the `/data` workspace-fallback guard that
      referenced `CURSOR_SDK_WORKSPACE`.

- [ ] **Step 4: `.env.example`** — rewrite the LLM block: `OPENROUTER_API_KEY`
      (`(SECRET)`, empty, matching the file's convention), `PI_MODEL`,
      `PI_VISION_MODEL`, `PI_PROVIDER`, `PI_THINKING`, `PI_MAX_TOOLS`,
      `PI_MAX_SECONDS`, `DATA_DIR`. **No base-URL entry** — it is a constant.

- [ ] **Step 5: Verify**

```bash
docker compose build backend
cd backend && pytest -q
cd backend/agent_sidecar && npm test
cd frontend && npx tsc --noEmit && npm test    # SSE contract must be untouched
```

- [ ] **Step 6: Commit** — `Node runtime in the backend image, a sidecar CI job, OpenRouter env`

---

## Phase 6 — Benchmark Pi and report

### Task 22: The acceptance gate

**Files:** Create `backend/bench/results/pi-<ts>.{json,md}`,
`backend/bench/results/cursor-vs-pi.md`

- [ ] **Step 1: Confirm the key resolves** (Task 0, Step 1). Without it this task
      cannot run — say so rather than reporting a partial result as a pass.

- [ ] **Step 2: Smoke first, cheapest signal**

```bash
curl -X POST localhost:8000/internal/bridge-smoke -H "X-Admin-Password: …"
```

- [ ] **Step 3: End-to-end by hand**, via the `run-chiatienan` skill. Post
      `@bot 840k cả nhóm trừ An, Bình +50k` → a draft card with the right
      per-head split, live tool progress in `agent-timeline`, and Confirm writes
      the meal. Then `@bot ai trả tuần này` → QR amounts match the settlement
      table. Then paste a bill photo (the vision path from Task 0).

- [ ] **Step 4: Run the corpus and compare**

```bash
cd backend
RUN_LLM_EVAL=1 python -m bench.run --corpus all --engine pi \
  --out bench/results/pi-$(date +%s).json
python -m bench.report --compare bench/results/baseline-cursor.json \
  bench/results/pi-*.json > bench/results/cursor-vs-pi.md
```

- [ ] **Step 5: Write the report honestly.** State plainly: which cases changed
      verdict in each direction, which graders regressed, the latency and cost
      delta, and **what the harness could not measure** — image cases if the model
      is text-only, and any prod case still flagged `review`. A benchmark that
      reports only its wins is worse than no benchmark, because it will be
      believed.

      Ship criterion: no regression on `tool_selection` or `ledger_state`; any
      `prose_quality` or latency change understood and written down.

- [ ] **Step 6: Decide on `_strip_narration`.** It exists because Cursor's agent
      narrated its skill reads ("Mình đọc skill…") and glued that onto the answer.
      With skills injected in-memory and no `read` tool it may be gone. If the
      corpus shows **zero** narration hits, delete it and its two keyword tables
      from `turn.js`; if not, keep them. Measure, don't guess.

- [ ] **Step 7: Commit** — `bench: Pi results and the Cursor-vs-Pi equivalence report`

---

## Rollback

The cutover is hard, so rollback is `git revert` of the Phase 3–5 commits, plus
restoring `cursor-sdk` to `pyproject.toml` and `CURSOR_*` to the deploy env. The
Phase 1 benchmark commits are engine-agnostic and should **not** be reverted —
they are the reason a rollback decision can be made on evidence.

## Task dependency summary

```
0 (vision) ─────────────────────────────┐
1 → 2,3,4,5 → 6 → 7 → 8 → 9 (GATE) ─────┤
                                        ├─▶ 10 → 11,12 → 13 → 14
                                        │           (13 needs 0)
                                        └─▶ 15 → 16 → 17 → 18 → 19 → 20 → 21 → 22
```

Tasks 2–5 are independent of each other. Task 9 gates everything after it. Tasks
11 and 12 are independent. Task 22 needs 9 and 21.
