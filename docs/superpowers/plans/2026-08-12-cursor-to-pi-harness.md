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
  **14 `monkeypatch.setattr` sites across 4 test files** depend on it —
  `test_chat.py` (7), `test_chat_payment_turn.py` (3),
  `test_bill_image_carryover.py` (2), `test_api.py` (2, on `run_bot_turn`) — and
  `chat.py:503` is the only production caller. Keeping this contract is what makes
  the port small.
- The `agent.*` SSE event names are **frozen** — the frontend
  (`frontend/src/hooks/use-room.ts:50-89`, `components/chat/agent-timeline.tsx`)
  consumes them. The sidecar emits them in final form.
- **Five** files import `cursor_sdk`: `agent.py`, `cursor_runner.py`, `tools.py`,
  `summarize.py`, `bridge_smoke.py`. The last is easy to forget — it is only
  touched via a rename (Task 18).
- Backend tests run from `backend/` with `pytest`; sidecar tests from
  `backend/agent_sidecar/` with `node --test`; frontend from `frontend/` with
  `npm test`.
- Dates are **ICT**. Freeze time by monkeypatching `app.clock.now_ict`, **never**
  `today_ict` (it is import-bound in `ledger`/`drafts`/`tools`).
- The backend stays a **single process** — `chat._agent_lock` and `realtime.hub`
  are in-process state. Do not add `--workers`.
- **No secret, real name, bank account, or `qr_url` may be committed.** See Task 7
  — the sanitizer is CI-tested and the corpus file is gitignored by default.
- Plain ESM JS in the sidecar, no TypeScript, no bundler: the source that ships is
  the source that runs.

---

## Phase 0 — Resolve the vision question

### Task 0: Verify `deepseek/deepseek-v4-flash` modality

**Blocking.** If this model cannot accept images, bill-photo reading breaks, and
that is a headline feature. Resolve it before writing any sidecar code.

> **Where to run this.** `openrouter.ai` is **egress-blocked from the dev
> container** (design §12). Run Step 2 from a machine with open egress and the key
> present — a local shell, or the droplet via the `deploy-chiatienan` skill. If
> neither is available, record that Step 2 could not be executed and treat the
> web-sourced "text-only" report as the working assumption, which makes
> `PI_VISION_MODEL` mandatory rather than optional.

- [ ] **Step 1: Confirm the key resolves**

```bash
test -n "$OPENROUTER_API_KEY" && echo "key present" || echo "KEY MISSING"
```

- [ ] **Step 2: Query the catalogue**

```bash
curl -sS https://openrouter.ai/api/v1/models \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
| python3 -c '
import json,sys
for m in json.load(sys.stdin)["data"]:
    if "deepseek-v4" in m["id"] or "vision" in m["id"]:
        a = m.get("architecture", {})
        print(m["id"], a.get("input_modalities"), a.get("output_modalities"),
              "tools" in (m.get("supported_parameters") or []), m.get("pricing"))'
```

- [ ] **Step 3: Record the finding and decide `PI_VISION_MODEL`**

Write the actual output into this plan under this task. Then:

- `input_modalities` includes `image` → set `PI_VISION_MODEL` = `PI_MODEL`; the
  vision branch stays but is inert.
- text-only → pick a vision-capable OpenRouter model for `PI_VISION_MODEL`, and
  **check `tools` is in its `supported_parameters` too**. A bill-photo turn ends
  in `propose_meal`; a vision model that cannot call tools breaks the money path
  as thoroughly as one that cannot see. Record the model and its cost.
- `tools` **not** in `supported_parameters` for the primary → **stop**. Tool
  calling is non-negotiable; the model choice has to change and that is a user
  decision.

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
- Create: `backend/bench/corpus/meal_messages.py`
- Create: `backend/tests/test_bench_corpus.py`

**Interfaces:**
- `bench.corpus.load(name) -> list[Case]`, `name` ∈ `{"meals","week","prod","bills","all"}`
- `Case` dataclass: `id, source, day, actor, members, prior_steps, message, images, had_images, expect`

`bench/` sits outside `testpaths` (which is `["tests"]`) so it never runs in CI by
accident.

> **The two corpora are not symmetric, and neither is replayable as-is.**
> `tests/golden/meals.py` has **no messages** — its 9 cases (`G1`–`G8`, `G12`) are
> draft payloads addressed by 1-based member index. `tests/golden/scenario_week.py`
> has **21 steps**, of which only **11** carry a `message` (`s1`–`s8`, `s9b`,
> `s10b`, `s12`); the other 10 are two `confirm_pending` button presses and the
> eight `s11a`–`s11h` payments. Both facts drive Task 6.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_bench_corpus.py`:

```python
def test_meals_corpus_pairs_every_golden_case_with_a_message():
    from bench.corpus import load
    cases = load("meals")
    assert [c.id for c in cases] == ["G1","G2","G3","G4","G5","G6","G7","G8","G12"]
    # meals.py has no messages — corpus.py must supply one per case
    assert all(c.message and c.message.startswith("@bot") for c in cases)


def test_week_corpus_keeps_only_the_eleven_llm_replayable_steps():
    from bench.corpus import load
    ids = [c.id for c in load("week")]
    assert ids == ["s1","s2","s3","s4","s5","s6","s7","s8","s9b","s10b","s12"]


def test_week_case_carries_every_prior_step_including_message_less_ones():
    from bench.corpus import load
    s12 = next(c for c in load("week") if c.id == "s12")
    prior = [s["id"] for s in s12.prior_steps]
    # the s11* payments are what make s12's `empty: True` true
    assert "s11a" in prior and "s11h" in prior and "s9a" in prior
    assert prior[-1] == "s11h"


def test_week_members_carry_bank_details():
    from bench.corpus import load
    # a1/a2/a4 have banks "so QR builds succeed" — without them settle_period raises
    s5 = next(c for c in load("week") if c.id == "s5")
    banked = {m["key"] for m in s5.members if m.get("bank")}
    assert banked == {"a1", "a2", "a4"}


def test_prod_corpus_is_empty_when_the_fixture_is_absent(tmp_path, monkeypatch):
    from bench import corpus
    monkeypatch.setattr(corpus, "PROD_PATH", tmp_path / "nope.json")
    assert corpus.load("prod") == []
```

Note the last test: it asserts behavior **when the file is absent**, against a
tmp path. Asserting `load("prod") == []` unconditionally would start failing the
moment Task 7 produces a fixture, and nothing in Task 7 would fix it.

- [ ] **Step 2: Run to verify it fails** — `cd backend && pytest tests/test_bench_corpus.py -v`
      → `ModuleNotFoundError: bench`.

- [ ] **Step 3: Implement.** `bench/corpus.py` imports
      `tests.golden.meals.CASES` and `tests.golden.scenario_week.{MEMBERS, STEPS}`
      **directly** — they stay the source of truth for payloads and expectations.
      On top of that:
  - `bench/corpus/meal_messages.py` maps each golden case id to a canonical
    Vietnamese message the bot should be able to act on, e.g.
    `"G3": "@bot tôi trả 200k, Bình với Cường ăn, tôi không ăn"`. Write these to
    match the case's payload semantics (`G3` = payer not a participant).
    `load("meals")` pairs them by id and fails loudly on a missing id, so adding a
    golden case forces adding a message.
  - Each `Case` carries `prior_steps` — **every** earlier step in list order,
    message-less ones included — and `members` (the corpus's own member specs,
    with bank details for the week corpus).
  - `load("prod")` reads `PROD_PATH` if present, else `[]`, and skips cases
    flagged `"review": true`.

- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `bench: corpus loaders over the existing golden datasets`

---

### Task 2: `tool_selection` grader

**Files:** Create `backend/bench/graders.py`; add `backend/tests/test_bench_graders.py`

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


def test_participants_compare_as_a_set():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"], "args": {"propose_meal": {"participants": [1, 2, 3]}}})
    rec  = _record(tools=[("propose_meal", {"participants": [3, 1, 2]})])
    assert grade_tool_selection(case, rec).passed


def test_non_money_args_are_ignored():
    from bench.graders import grade_tool_selection
    case = _case(expect={"tools": ["propose_meal"], "args": {"propose_meal": {"total": 300000}}})
    rec  = _record(tools=[("propose_meal", {"total": 300000, "dish": "bún bò", "note": "x"})])
    assert grade_tool_selection(case, rec).passed
```

- [ ] **Step 2: Verify it fails.**
- [ ] **Step 3: Implement.** Expected tool ∈ called (superset-tolerant, matching
      `test_scenario_week_llm.py`'s current behavior), plus an arg-subset check
      restricted to `MONEY_ARGS`. Nothing else is compared: prose, dish names, and
      notes are the model's business.
- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `bench: tool-selection grader that checks the money args`

---

### Task 3: `ledger_state` grader — extract, don't duplicate

**Files:** Modify `backend/bench/graders.py`, `backend/tests/test_scenario_week.py`

**Interfaces:** `grade_ledger_state(case, record, db, ids) -> Verdict`

The transfer/balance/QR comparison already exists inside
`tests/test_scenario_week.py:84-115`. **Move it into `bench/graders.py` and have
the test import it back**, so there is exactly one implementation. If the
extraction is correct, `test_scenario_week.py` still passes unchanged in behavior.

- [ ] **Step 1: Write the failing test** — assert `grade_ledger_state` rejects a
      transfer list that differs in **order**, rejects a missing `qr_url`, accepts
      the real `s5` expectation from `scenario_week.STEPS`, and handles the
      `settle_blocked` / `blocked_pending` branch (`s8`).

- [ ] **Step 2: Verify it fails.**
- [ ] **Step 3: Implement.** Extract `_balances`, the ordered-transfer comparison,
      the `f'{amount:,}' in body` render check against `chat._settlement_body`, and
      the `qr_payees` check.
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


def test_a_missing_judge_is_an_error_not_a_pass():
    from bench.graders import grade_prose
    # An unjudged run must not silently count as passing — that would make the
    # Cursor baseline and the Pi run incomparable (design §11.5).
    v = grade_prose(_case(), _record(final_text="ok"), judge=None)
    assert v.reason and "judge" in v.reason.lower()
    assert v.passed is None      # tri-state: not graded
```

- [ ] **Step 2: Verify it fails.**
- [ ] **Step 3: Implement.** Judge is injected, never constructed inside the
      grader — that keeps the test offline and lets Tasks 9/22 pin the model via
      `BENCH_JUDGE_MODEL`. `passed` is **tri-state** (`True`/`False`/`None`): a
      case with no judge is *not graded*, never *passed*. The judge rubric: replies
      in Vietnamese, answers what was asked, no narration of skill/tool selection,
      does not restate amounts the card already shows.
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

### Task 6: `bench/run.py` — the runner **(the load-bearing task)**

**Files:** Create `backend/bench/run.py`, `backend/bench/world.py`; add
`backend/tests/test_bench_run.py`

**Interfaces:** CLI
`python -m bench.run --corpus {meals,week,prod,bills,all} --engine {cursor,pi} --repeat N --out PATH`

> **Read this before writing code.** The naive runner — seed a generic room,
> replay `history` as chat text — produces a world in which the money graders
> **cannot pass on either engine**, so `--compare` reports "no change" and the
> benchmark silently certifies equivalence it never tested. Two specific reasons:
>
> 1. **Chat text creates no ledger rows.** `s5`'s expected transfers require
>    meals `s1`/`s2`/`s4` and payment `s3` *committed*; `s8` requires a pending
>    draft; `s12`'s `empty: True` requires the eight `s11a`–`s11h` payments, which
>    carry no `message` at all.
> 2. **`tests/test_ledger._seed_room` creates members with no bank details**
>    (`M1..Mn`, `display_name`/`nickname`/`pin` only). `scenario_week.MEMBERS`
>    gives `a1`/`a2`/`a4` banks precisely "so QR builds succeed". Seeded the wrong
>    way, `make_qr_url` raises `QRError` for every payee and `qr_payees` can never
>    pass.

**`bench/world.py` — deterministic world reconstruction.** Factor the dispatch out
of `tests/test_scenario_week.py:49-115` (do not re-derive it) into
`build_world(db, case) -> (room_id, ids, draft_by_step)`:

- Seed the room from `case.members`, **including `bank_code` / `account_number` /
  `account_holder`** where the spec has them.
- For each entry in `case.prior_steps`, freeze the clock to that step's `day` and
  dispatch by `kind` exactly as the test does: `add_member` → `Member` insert;
  `meal_confirmed` / `leave_pending` → `drafts.create_draft` (+ `commit_draft` for
  the former); `confirm_pending` → `commit_draft(draft_by_step[ref])`;
  `payment` → `ledger.record_payment`; `settle` → skipped (read-only, no state).
- Then freeze to `case.day` and hand back the world for the LLM turn.

For the `meals` corpus a case's world is just the 4-member seeded room — the
golden cases are independent, with no prior steps.

- [ ] **Step 1: Write the failing test**

```python
def test_world_reconstruction_puts_s5_in_the_state_its_expectation_assumes(db):
    from bench.corpus import load
    from bench.world import build_world
    from app import ledger, periods
    s5 = next(c for c in load("week") if c.id == "s5")
    room_id, ids, _ = build_world(db, s5)
    with db.session() as s:
        bal = ledger.period_balances(s, room_id, None, date(2999, 1, 1))
    # s4's expectation, i.e. the state s5 must settle from
    assert bal[ids["a2"]]["balance"] == 300_000
    assert bal[ids["a3"]]["balance"] == -225_000


def test_week_members_are_seeded_with_banks_so_qr_builds(db):
    from bench.corpus import load
    from bench.world import build_world
    from app import tools
    s5 = next(c for c in load("week") if c.id == "s5")
    room_id, ids, _ = build_world(db, s5)
    ctx = tools.ToolContext(db=db, room_id=room_id, sender_member_id=ids["a3"])
    res = tools.build_tools(ctx)["settle_period"].execute({"keyword": "since_last"})
    payee_rows = [t for t in res["transfers"] if t["to_id"] == ids["a1"]]
    assert payee_rows and all(t["qr_url"] for t in payee_rows)   # would QRError unbanked


def test_s8_world_has_the_pending_draft_that_blocks_settle(db):
    from bench.corpus import load
    from bench.world import build_world
    from app import drafts
    s8 = next(c for c in load("week") if c.id == "s8")
    room_id, _, _ = build_world(db, s8)
    with db.session() as s:
        assert len(drafts.list_pending_drafts(s, room_id)) == 1


def test_s12_world_is_a_settled_ledger(db):
    from bench.corpus import load
    from bench.world import build_world
    from app import ledger
    s12 = next(c for c in load("week") if c.id == "s12")
    room_id, ids, _ = build_world(db, s12)
    with db.session() as s:
        bal = ledger.period_balances(s, room_id, None, date(2999, 1, 1))
    assert all(v["balance"] == 0 for v in bal.values())   # the s11* payments ran


def test_the_frozen_clock_reaches_the_tool(db, monkeypatch):
    # a stub tool observes case.day, not today
    ...


def test_a_case_that_raises_is_recorded_not_fatal(monkeypatch):
    # one bad case must never kill the run
    ...


def test_repeat_produces_n_records_per_case(monkeypatch):
    from bench.run import run_corpus
    recs = run_corpus("meals", repeat=3, run_turn=_stub)
    assert len([r for r in recs if r["case_id"] == "G1"]) == 3
```

- [ ] **Step 2: Verify they fail.**

- [ ] **Step 3: Implement `bench/world.py`**, then `bench/run.py` on top of it.
      Per case per repetition: fresh temp SQLite via `app.db.Database`,
      `build_world`, clock frozen to `case.day` by patching `app.clock.now_ict`
      (**never** `today_ict`), then `agent.run_turn(case.message, ctx, images=…)`.
      Record `tools`, `args`, `results`, `final_text`, `error`, `elapsed_s`,
      `stats`, plus `case_id` and `rep`. **One case failing must never kill the
      run** — a benchmark that stops at the first error cannot report honestly.
      Write `bench/results/<engine>-<timestamp>.json`.

- [ ] **Step 4: Verify they pass.**
- [ ] **Step 5: Commit** — `bench: deterministic world reconstruction and case runner`

---

### Task 7: `bench/export_prod.py` — prod fixtures, sanitized

**Files:** Create `backend/bench/export_prod.py`; add `backend/tests/test_bench_sanitize.py`;
modify `.gitignore`

**Interfaces:**
- `sanitize(rows, name_map) -> list[dict]` (pure, the tested part)
- CLI `python -m bench.export_prod --room 1 --days 90 --out bench/corpus/prod_conversations.json`

Source is the existing read-only debug API (`app/debug_api.py:232`, guarded by
`X-Debug-Key`; see `deploy/DEBUGGING.md` §6):

```
GET /internal/debug/conversation.csv?room_id=1&days=90
  → id, room_id, created_at, kind, author_member_id, author, body, attachments
```

**This is the highest-risk task in the plan.**

> **The corpus file is gitignored by default.** Nothing downstream needs the
> bodies in git — only on disk when `bench.run --corpus prod` executes. Commit the
> case ids, the derived expectations (pseudonyms and integers only), and the
> corpus file's SHA-256; gitignore the corpus itself. That turns "a leak here is
> an unrecoverable privacy incident" into "a leak here cannot happen via git."
> Committing bodies is an explicit opt-in, and if taken, the sanitizer tests must
> also run in a **pre-commit** hook — CI runs after the push, which is too late.

> **Bank details arrive through message bodies, not just attachments.**
> `add_member` and `update_member` take `bank_code` / `account_number` /
> `account_holder` as tool arguments (`tools.py:178-211`), so a real body reads
> `@bot cập nhật stk của tôi 0071000123456 VCB NGUYEN VAN A`. A key-denylist over
> `attachments` does not touch that, and `body` is exactly what the corpus keeps.

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


def test_account_numbers_typed_into_chat_are_redacted():
    # tools.py:178-211 — bank details enter via add_member/update_member args
    out = sanitize([_row(body="@bot cập nhật stk của tôi 0071000123456 VCB NGUYEN VAN A")],
                   name_map={}, holders=["NGUYEN VAN A"])
    blob = json.dumps(out)
    assert "0071000123456" not in blob
    assert "NGUYEN VAN A" not in blob


def test_digit_runs_of_eight_or_more_go_but_amounts_stay():
    out = sanitize([_row(body="@bot 324200 grab, stk 19001234567")])
    assert "324200" in out[0]["body"]        # a VND amount — graded, must survive
    assert "19001234567" not in out[0]["body"]


def test_names_are_matched_on_word_boundaries_not_substrings():
    # bare longest-first replacement mangles Vietnamese: "An" sits inside "anh"
    out = sanitize([_row(author="An", body="anh An trả 300k, cảm ơn anh")],
                   name_map={"An": "A1"})
    assert out[0]["body"] == "anh A1 trả 300k, cảm ơn anh"


def test_the_map_covers_nicknames_aliases_and_holders_not_just_display_names():
    out = sanitize([_row(body="@bot linhle trả 100k")],
                   name_map={"Linh": "A1", "linhle": "A1"})
    assert "linhle" not in json.dumps(out)


def test_base64_images_become_the_anh_marker():
    out = sanitize([_row(attachments={"images": [{"data": "iVBORw0KGgo…"}]})])
    assert "iVBORw" not in json.dumps(out)
    assert out[0]["had_images"] == 1


def test_invite_tokens_and_pins_are_stripped():
    out = sanitize([_row(attachments={"invite_token": "abc123", "pin": "4321"})])
    blob = json.dumps(out)
    assert "abc123" not in blob and "4321" not in blob
```

- [ ] **Step 2: Verify they fail.**

- [ ] **Step 3: Implement `sanitize`.** Recursive key denylist:
      `account_number`, `account_holder`, `bank_code`, `invite_token`, `pin`,
      `qr_url`, `data`. Body-level: redact digit runs ≥8 (VND amounts here are ≤7
      digits or carry a `k`/`tr`/`đ` unit); replace names on **word boundaries**,
      with the map built from `display_name` + `nickname` + `aliases` +
      `account_holder` variants (uppercase and de-diacriticized forms). Emit
      `had_images` and drop the bytes.

- [ ] **Step 4: Verify they pass.**

- [ ] **Step 5: Add the fetch + expectation bootstrap.** Derive `expect` from what
      prod actually did — the bot's own reply row and its `attachments` record the
      tool and the numbers. Any case whose expectation cannot be derived gets
      `"review": true`; `corpus.py` skips those until a human clears them.
      **Mark a case image-tainted if any of the previous
      `IMAGE_LOOKBACK_MESSAGES` rows had images** — `chat.recent_images`
      (`chat.py:273-301`) attaches a bill from an *earlier* message, so a
      text-only row can still have been answered from a photo, and its derived
      expectation (a total read off that photo) is unreproducible. Image-tainted
      and `had_images` cases are graded on tool selection only.

- [ ] **Step 6: Export, then read the output with your own eyes** before adding
      anything to git. Grep the file for every real member name **and every
      `account_number` and `account_holder` from the prod members table**, and for
      `vietqr`. The test suite is a net, not a substitute for looking.

- [ ] **Step 7: Add 2–3 synthetic bill-image cases** as `bench/corpus/bills/` —
      small hand-made PNGs of a bill with known totals, plus their expectations.
      The golden corpora have no images and prod images are stripped, so without
      these the riskiest path in the system (design §12) has zero benchmark
      coverage.

- [ ] **Step 8: Commit** — `bench: sanitized production corpus and synthetic bill cases`

---

### Task 8: `bench/report.py`

**Files:** Create `backend/bench/report.py`; add `backend/tests/test_bench_report.py`

**Interfaces:**
- `python -m bench.report RESULTS.json` → markdown
- `python -m bench.report --compare BASE.json NEW.json` → equivalence table

Because `--repeat` produces N records per case, the unit of comparison is a
**pass rate**, not a verdict. Both engines are nondeterministic and they are
different models; a single flip is indistinguishable from sampling noise.

- [ ] **Step 1: Write the failing test** — the compare mode must surface a case
      whose pass rate **dropped**, a case that improved, a case present in one run
      but missing from the other (an explicit `MISSING` row — a silently dropped
      case reads as "no change", the exact failure this harness exists to
      prevent), and a case where both runs scored 0/3 (an explicit
      `BOTH-FAILING` row, so a vacuous grader cannot masquerade as equivalence).

- [ ] **Step 2: Verify it fails.**
- [ ] **Step 3: Implement.** Per-case grid with `passed/n` per grader, per-grader
      aggregate, latency percentiles, cost total. Compare mode: pass-rate delta
      per case per grader, `MISSING` and `BOTH-FAILING` rows, latency/cost delta.
      Ungraded (`passed is None`) is reported as `n/a`, never folded into a rate.
- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `bench: markdown report and pass-rate comparison`

---

### Task 9: GATE — capture the Cursor baseline

**Files:** Create `backend/bench/results/baseline-cursor.json`, `…/baseline-cursor.md`

- [ ] **Step 1: Full CI run first** — `cd backend && pytest -q`. The harness must
      be green before its output means anything.

- [ ] **Step 2: Pin the judge.** `BENCH_JUDGE_MODEL` and its key must be set for
      **this** run, identically to Task 22. A baseline graded without a judge
      against a Pi run graded with one is not a comparison (design §11.5). If no
      judge is available here, record that `prose_quality` is `n/a` for both runs
      and say so in the report — do not let one side be judged and the other not.

- [ ] **Step 3: Run the corpus against Cursor**

```bash
cd backend
RUN_LLM_EVAL=1 python -m bench.run --corpus all --engine cursor --repeat 3 \
  --out bench/results/baseline-cursor.json
python -m bench.report bench/results/baseline-cursor.json > bench/results/baseline-cursor.md
```

- [ ] **Step 4: Read the report.** Cases that fail *on Cursor* are expected —
      this is a baseline, not a target. Record them, because a Pi run that fails
      the same cases at the same rate is equivalent, not broken. **Any case at
      0/3 on Cursor needs a note**: either the expectation is wrong or the grader
      is vacuous, and either way it cannot certify Pi.

- [ ] **Step 5: Commit** — `bench: record the Cursor baseline over every corpus`

> **Do not proceed to Phase 2 until this file is committed.**

---

## Phase 2 — The TypeScript harness

### Task 10: Sidecar skeleton + schema fixture

**Files:**
- Create: `backend/agent_sidecar/{package.json,.gitignore}`, `backend/agent_sidecar/test/.gitkeep`
- Create: `backend/bench/dump_schemas.py`
- Create: `backend/agent_sidecar/test/fixtures/tool-schemas.json`
- Create: `backend/tests/test_tool_schemas_fixture.py`

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
- [ ] **Step 3: Implement** `bench/dump_schemas.py` (it must resolve the subscript
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
mapping table. Note **six** of the 14 schemas contain an `enum` (three distinct
definitions; `keyword` is shared by reference across four tools).

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

test("an unsupported keyword throws rather than being dropped", () => {
  assert.throws(() => toTypeBox({type:"object", properties:{a:{type:"string", minLength:3}}}));
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
`ping`. Replies `turn_done`, `summarize_done`, `pong`, `fatal`. **Every command
carries a `req_id` and every reply echoes it** — see design §4. This matters
because `/internal/bridge-smoke` (`main.py:676`) is **not** under
`chat._agent_lock`, so a `ping` can legitimately arrive mid-turn and interleave on
one stdout.

- [ ] **Step 1: Write the failing test** — drive the process with a stubbed
      session so no API key is needed. This is the cheapest possible test of the
      protocol and it must exist before `session.js`:
  - `ping` → `{"type":"pong","req_id":…}` with the same `req_id`
  - `summarize` → `{"type":"summarize_done","req_id":…,"text":…}`
  - a `run` whose stub session calls one tool → emits `agent.tool.start`, then
    `tool_call`, blocks, and only after a matching `tool_result` emits
    `agent.tool.result` and finally `turn_done`
  - **interleaving:** a `ping` sent while a `run` is blocked on `tool_call`
    returns its `pong` without disturbing the run, and both replies carry their
    own `req_id`
  - **framing:** a line split across two stdin chunks is handled; a `\r\n` line
    ending is tolerated (pi's docs warn generic Unicode line readers are
    incompatible, so read bytes and split on `\n` yourself)
  - a malformed line yields a `fatal` for that `req_id` and does **not** kill the
    process — one bad turn must not take the bot down until the next deploy

- [ ] **Step 2: Verify it fails.**
- [ ] **Step 3: Implement.** Correlate `tool_call`/`tool_result` by `call_id` and
      every command/reply by `req_id`, with a pending-promise map for each.
- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `sidecar: JSONL RPC loop with a tool-call round-trip`

---

### Task 13: `session.js` + `proxyTool`

**Files:** Create `backend/agent_sidecar/session.js`, `backend/agent_sidecar/test/proxy-tool.test.js`,
`backend/agent_sidecar/test/skills.test.js`

**Interfaces:** `buildSession(req, { onEvent, callTool }) -> { session, dispose }`

- [ ] **Step 1: Write the failing tests** — the `ok:false` semantics get first
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

And the one that de-risks design §5.1 — **skill bodies must be verified to reach
the model, because `tools: []` removes `read`**:

```js
test("a skill BODY reaches the model with the built-in toolset empty", async () => {
  // If pi surfaces only name+description and defers the body to a read-like
  // tool, tools:[] means the model never sees a single procedure. That would be
  // a silent, corpus-wide regression in the money workflows.
  const { session } = await buildSession({
    system: "sys", message: "m", tools: [],
    skills: [{name:"record-meal", description:"d", body:"UNIQUE_BODY_MARKER_42"}],
    context_files: [],
  }, stubs);
  const ctx = await dumpModelVisibleContext(session);   // stub captures the prompt
  assert.match(ctx, /UNIQUE_BODY_MARKER_42/);
});
```

- [ ] **Step 2: Verify they fail.**

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
  - **If the skill-body test cannot be made to pass**, fall back to shipping the
    four skill bodies as extra `context_files` entries (~8KB, always in the system
    prompt) and drop the skill mechanism. Record which path was taken.

- [ ] **Step 4: Verify they pass.**
- [ ] **Step 5: Commit** — `sidecar: session construction and the Python tool proxy`

---

### Task 14: `turn.js` — event normalization and answer assembly

**Files:** Create `backend/agent_sidecar/turn.js`, `backend/agent_sidecar/test/turn.test.js`

**Interfaces:** `runTurn(session, req, emit) -> {final_text, tools, error, capped, stats}`

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

test("a max_tools breach is a PARTIAL ANSWER, not an error", async () => {
  // agent.py:374-384 — today the loop cancels and breaks; result.error stays
  // None and chat.py:558 posts the accumulated text as a normal reply. If a cap
  // became an error, every capped turn would flip to a ⚠️ message in the room.
  const r = await runTurn(sessionThatCallsNTools(50), {max_tools: 3}, noop);
  assert.equal(r.error, null);
  assert.equal(r.capped, true);
  assert.equal(r.tools.length, 3);
  assert.ok(r.final_text.length > 0);
});

test("a max_seconds breach behaves the same way", async () => { /* same shape */ });
```

- [ ] **Step 2: Verify it fails.**

- [ ] **Step 3: Implement.** Normalize pi's `message_update` / `tool_execution_*`
      / `agent_*` / `auto_retry_*` events to the frozen `agent.*` names (design
      §4.2). Enforce caps → `session.abort()`, setting `capped: true` and leaving
      `error: null` (design §8). Collect `stats` from `get_session_stats`. Format
      only genuine failures into the single `error` string `chat.py` expects.

- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `sidecar: event normalization, turn caps, answer assembly`

---

## Phase 3 — The Python shim

### Task 15: `app/pi_bridge.py`

**Files:** Create `backend/app/pi_bridge.py`, `backend/tests/test_pi_bridge.py`

**Interfaces:** `PiBridge.request(cmd) -> AsyncIterator[dict]` (demultiplexed by
`req_id`), `PiBridge.send(msg)`, `PiBridge.ensure_started()`

Subprocess and framing **only**. No model logic, no event logic, no pi vocabulary
beyond the message `type` strings. If this file starts branching on pi semantics,
that logic belongs in `turn.js`.

- [ ] **Step 1: Write the failing test** — with a fake `node` that echoes canned
      JSONL: startup retries 3× with `1.5**attempt` backoff (the shape
      `_launch_bridge_resilient` uses today, because a child dying at startup is
      not a Cursor-specific problem); a missing `OPENROUTER_API_KEY` raises a clear
      error at spawn naming the variable; the parent env reaches the child; a dead
      child is restarted on the next `ensure_started`; **and two concurrent
      requests with different `req_id`s each receive only their own messages.**

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
      setup-failure case that must still emit a finish event. Add one asserting a
      `capped: true` `turn_done` yields `error is None`. Delete the MCP envelope
      tests — that envelope no longer exists.

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

- [ ] **Step 4: Verify the whole suite passes.** The **14 monkeypatch sites across
      4 files** must pass with **zero edits** — that is the real assertion that
      the contract held.

- [ ] **Step 5: Commit** — `Run turns through the Pi sidecar`

---

### Task 18: `summarize.py` + `pi_smoke.py`

**Files:** Rewrite `backend/app/summarize.py`; rename `backend/app/bridge_smoke.py` →
`backend/app/pi_smoke.py`; modify `backend/app/main.py`, `backend/tests/test_summarize.py`

**The summarize session must be specified, not inherited.** Today
`summarize.py:51-56` runs with `custom_tools=[]`, **no** `setting_sources`, and the
summary prompt as the entire message — no system prompt, no skills, no rules. The
sidecar's `summarize` command must reproduce that: **empty `tools`, empty
`skills`, empty `context_files`, and `_SUMMARY_PROMPT + text` as the whole
message.** If it silently inherited the `run` session's construction, every
room's long-term memory would change flavor with no test catching it.

- [ ] **Step 1: Write the failing test** — `summarize_messages` returns the text
      the sidecar sent; **any** failure returns `""` (a failed summary must never
      crash a turn); the sidecar's summarize session is built with no system
      prompt, no skills, and no context files; `/internal/bridge-smoke` keeps its
      path, its admin guard, and its `{ok, elapsed_s, messages_seen, text}` shape.

- [ ] **Step 2: Verify it fails.**
- [ ] **Step 3: Implement.** `summarize` becomes send-text/receive-text; smoke
      becomes `{"type":"ping"}` → `pong`. **`messages_seen` carries the count of
      JSONL messages the sidecar emitted for that `req_id`** (1 for a healthy
      ping) — the field is kept for `deploy/DEBUGGING.md` compatibility, so it has
      to mean something rather than be fabricated.
- [ ] **Step 4: Verify it passes.**
- [ ] **Step 5: Commit** — `Summarize and smoke-check through the sidecar`

---

### Task 19: `config.py` + `memory.py` — `DATA_DIR` and its migration

**Files:** Modify `backend/app/config.py`, `backend/app/memory.py`,
`backend/app/main.py`, `backend/tests/conftest.py`, `backend/tests/test_config.py`,
`backend/tests/test_memory.py`

**Interfaces:** new settings per design §10.

> ⚠️ **This rename silently destroys production room memory unless migrated.**
> Memory lives at `{cursor_workspace}/rooms/{id}/` (`memory.py:26-34`) and prod's
> workspace is `/data/cursor-agent` (`deploy.yml:164`). `DATA_DIR=/data` points it
> at `/data/rooms/{id}/` — a different directory. On the first post-deploy turn
> `load_memory` returns `""`, `read_watermark` returns `0`, and `_maybe_rollover`
> (`chat.py:603-619`) re-summarizes the room's entire >10-week history into a
> fresh `memory.md` using the new model.

- [ ] **Step 1: Write the failing test**

```python
def test_legacy_room_memory_is_migrated_on_first_access(tmp_path, monkeypatch):
    legacy = tmp_path / "cursor-agent" / "rooms" / "1"
    legacy.mkdir(parents=True)
    (legacy / "memory.md").write_text("## tuần trước\n- An trả 300k\n")
    (legacy / "memory.meta.json").write_text('{"summarized_through_id": 42}')
    monkeypatch.setattr(memory, "_LEGACY_BASE", tmp_path / "cursor-agent")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert "An trả 300k" in memory.load_memory(1)
    assert memory.read_watermark(1) == 42          # not 0 — no re-summarization


def test_migration_is_idempotent_and_never_overwrites(tmp_path, monkeypatch):
    # a second call must not clobber newer memory written after the migration
    ...


def test_defaults_and_no_cursor_attributes_remain():
    s = Settings.from_env()
    assert s.data_dir == "/data" and s.pi_model and s.pi_max_tools == 40
    assert not [a for a in vars(s) if a.startswith("cursor_")]
```

- [ ] **Step 2: Verify it fails.**
- [ ] **Step 3: Implement.** Add the `PI_*` / `DATA_DIR` settings and drop every
      `cursor_*`. Give `memory.py` an **idempotent startup migration**: if
      `DATA_DIR/rooms/{id}` is absent and `_LEGACY_BASE/rooms/{id}` exists, copy
      it. Mark it with a comment naming the release it can be deleted in — the repo
      already does startup migrations of this shape (commit `aa1f992`).
      `conftest.py` swaps `CURSOR_SDK_WORKSPACE` → `DATA_DIR` in its
      before-import assignment block (those are **assigned**, not
      `setdefault`-ed, on purpose — an ambient prod `.env` once made the suite
      write to the real ledger).
- [ ] **Step 4: Verify the full suite passes.**
- [ ] **Step 5: Commit** — `Rename the workspace setting to DATA_DIR, migrate room memory`

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
      `.cursor-store/`, gains `agent_sidecar/node_modules/` and
      `bench/corpus/prod_conversations.json`.

- [ ] **Step 4: Verify nothing is left in code or config**

```bash
cd /home/user/chiatienan
# docs/ and the committed baseline legitimately contain these strings — this
# plan, its design doc, the superseded 2026-07-22 spec, and baseline-cursor.md
# all describe the Cursor engine on purpose. Excluding them is the point.
grep -rniE "cursor_sdk|cursor-sdk|CURSOR_(API|SDK|AGENT)" \
  --exclude-dir=node_modules --exclude-dir=.git \
  --exclude-dir=reference --exclude-dir=docs \
  --exclude='baseline-cursor*' . \
  && echo "STILL REFERENCED — fix before committing" || echo "clean"
cd backend && pytest -q
```

- [ ] **Step 5: Raise `reference/sample-cursor-sdk-with-image/` with the user.**
      **58 files** of dead Cursor sample whose only purpose was as this port's
      source (it also carries 4 licensed Gilroy `.ttf` files). Recommend deleting
      it; **do not delete it silently** — it is explicitly kept as reference
      material and that call is the user's.

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
      secret instead of `CURSOR_API_KEY`; add the `PI_*` vars and `DATA_DIR`;
      remove `CURSOR_API_BASE`, `CURSOR_SDK_MODEL`, `CURSOR_SDK_WORKSPACE`, and
      the `WORKSPACE_FALLBACK` guard block at L190-197. Task 19's startup
      migration handles the memory move, so no deploy-script copy step is needed —
      but verify on the first deploy that `/data/rooms/1/memory.md` exists and is
      non-empty.

- [ ] **Step 4: `.env.example`** — rewrite the LLM block: `OPENROUTER_API_KEY`
      (`(SECRET)`, empty, matching the file's convention), `PI_MODEL`,
      `PI_VISION_MODEL`, `PI_PROVIDER`, `PI_THINKING`, `PI_MAX_TOOLS`,
      `PI_MAX_SECONDS`, `DATA_DIR`. **No base-URL entry** — it is a constant.

- [ ] **Step 5: Verify** — each command from the repo root, so the `cd`s don't
      compound:

```bash
docker compose build backend
(cd backend && pytest -q)
(cd backend/agent_sidecar && npm test)
(cd frontend && npx tsc --noEmit && npm test)    # SSE contract must be untouched
```

- [ ] **Step 6: Commit** — `Node runtime in the backend image, a sidecar CI job, OpenRouter env`

---

## Phase 6 — Benchmark Pi and report

### Task 22: The acceptance gate

**Files:** Create `backend/bench/results/pi-<ts>.{json,md}`,
`backend/bench/results/cursor-vs-pi.md`

- [ ] **Step 1: Confirm the key and the judge resolve.** `OPENROUTER_API_KEY` and
      the same `BENCH_JUDGE_MODEL` Task 9 used. Without both this task cannot run
      — say so rather than reporting a partial result as a pass.

- [ ] **Step 2: Make `--engine` a checked label, not a switch.** There is only
      ever one engine in the tree, so `--engine pi` run before Phase 3 would
      silently record Cursor results under a Pi filename. Assert the flag matches
      reality — `cursor_sdk` importable ⇒ `cursor`, `agent_sidecar/` present and
      `cursor_sdk` gone ⇒ `pi` — and fail the run on a mismatch.

- [ ] **Step 3: Smoke first, cheapest signal**

```bash
curl -X POST localhost:8000/internal/bridge-smoke -H "X-Admin-Password: …"
```

- [ ] **Step 4: End-to-end by hand**, via the `run-chiatienan` skill. Post
      `@bot 840k cả nhóm trừ An, Bình +50k` → a draft card with the right
      per-head split, live tool progress in `agent-timeline`, and Confirm writes
      the meal. Then `@bot ai trả tuần này` → QR amounts match the settlement
      table. Then paste a bill photo (the vision path from Task 0).

- [ ] **Step 5: Run the corpus and compare**

```bash
cd backend
RUN_LLM_EVAL=1 python -m bench.run --corpus all --engine pi --repeat 3 \
  --out bench/results/pi-$(date +%s).json
python -m bench.report --compare bench/results/baseline-cursor.json \
  bench/results/pi-*.json > bench/results/cursor-vs-pi.md
```

- [ ] **Step 6: Write the report honestly.** State plainly: which cases dropped
      pass rate and by how much, which graders regressed, the latency and cost
      delta, every `MISSING` and `BOTH-FAILING` row, and **what the harness could
      not measure** — image cases if the model is text-only, prod cases still
      flagged `review`, and any case `n/a` on `prose_quality`. A benchmark that
      reports only its wins is worse than no benchmark, because it will be
      believed.

      Ship criterion: **no case whose `tool_selection` or `ledger_state` pass rate
      dropped by more than 1/3**, and any `prose_quality` or latency change
      understood and written down. A `BOTH-FAILING` row is not a pass — it means
      that case certified nothing.

- [ ] **Step 7: Decide on `_strip_narration`.** It exists because Cursor's agent
      narrated its skill reads ("Mình đọc skill…") and glued that onto the answer.
      With skills injected in-memory and no `read` tool it may be gone. If the
      corpus shows **zero** narration hits across all repetitions, delete it and
      its two keyword tables from `turn.js`; if not, keep them. Measure, don't
      guess.

- [ ] **Step 8: Commit** — `bench: Pi results and the Cursor-vs-Pi equivalence report`

---

## Rollback

The cutover is hard, so rollback is `git revert` of the Phase 3–5 commits, plus
restoring `cursor-sdk` to `pyproject.toml` and `CURSOR_*` to the deploy env. Room
memory survives either direction: Task 19's migration copies rather than moves, so
`/data/cursor-agent/rooms/*` is still intact. The Phase 1 benchmark commits are
engine-agnostic and should **not** be reverted — they are the reason a rollback
decision can be made on evidence.

## Task dependency summary

```
0 (vision) ─────────────────────────────────┐
1 → 2,3,4,5 → 6 → 7 → 8 → 9 (GATE) ─────────┤
                                            ├─▶ 10 → 11,12 → 13 → 14
                                            │           (13 needs 0)
                                            └─▶ 15 → 16 → 17 → 18 → 19 → 20 → 21 → 22
```

Tasks 2–5 are independent of each other. **Task 6 is the load-bearing one** — the
money graders are vacuous without it, and Tasks 8, 9, and 22 all rest on it.
Task 9 gates everything after it. Tasks 11 and 12 are independent. Task 22 needs
9 and 21.
