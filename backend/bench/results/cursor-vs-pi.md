# Cursor → Pi: what the harness actually measured

**Status: the port is complete, the harness runs, and the blocker below is fixed.**
This file reports what was measured and what was not — a benchmark that reports only
its wins is worse than no benchmark, since it will be believed.

## Measured

### The port works end to end, on a real turn

`@bot tôi trả 400k cả nhóm` through the real sidecar to
`~deepseek/deepseek-v4-flash-latest`:

```
tools:  find_members(all_active=True)
        propose_meal(participants=[1,2,3,4], total=400000)
text:   Đã tạo thẻ đề xuất: bạn trả 400k, cả nhóm 4 người chia đều mỗi người 100k.
error:  None
```

Golden case `G1` graded through the harness: **`tool_selection` PASS,
`ledger_state` PASS** (the draft's shares and balances match the golden
expectation exactly), `prose_quality` n/a — correctly, because a draft turn's
prose is never posted. 16.7s.

`/internal/bridge-smoke` against the real process:
`{"ok": true, "elapsed_s": 1.1, "messages_seen": 1, "text": "pong"}`.

### Both models were probed against the real tool schemas

Not taken from a catalogue's `supported_parameters` — see `bench/probe_models.py`.

| model | 3 schemas | bill image |
|---|---|---|
| `~deepseek/deepseek-v4-flash-latest` | 3/3 | — (text-only) |
| `qwen/qwen3-vl-30b-a3b-instruct` | 3/3 | PASS — read `154000` and all three dish names |

### The baseline

`baseline-prod-cursor.json`, derived from the production log rather than a Cursor
re-run: 107 cases, 81 tool-graded (1.00 by construction) and 26 prose-graded at
**3/26**, judged by reading every reply. See the plan's Task 9 for why 1.00 is not
a measurement and why 3/26 is not a target.

## ✅ The hang that blocked this, and what it actually was

Two early attempts stopped after case `G1`, with `G2` apparently running past a
configured `max_seconds=120`. **The diagnosis was wrong, and the experiment that
corrected it is the point:** run `G2` *alone* and it finishes in **11.7s**, passing
both money graders. It only hung as the **second case of a run**.

So the fault was the runner's lifecycle, not pi and not the cap. A subprocess's
pipes belong to the event loop that created them, and `run_corpus` used one
`asyncio.run` **per case** — leaving two bad options: keep the bridge and have case
2 wait on streams belonging to a closed loop, or respawn per case, which hung too.
`run_corpus_async` now runs the whole corpus in **one loop with one sidecar**, and
closes the bridge once at the end.

Verified after the fix — three cases in one process, where two used to hang:

```
G1#0 ++. 15.3s     G2#0 ++. 34.8s     G3#0 ++. 13.0s
```

`++` is `tool_selection` and `ledger_state` both passing.

The `max_seconds` race added while chasing this **stays**: it is correct on its own
terms (its test drives a session whose `prompt()` never settles and asserts the turn
still returns, keeps its text, and reports `capped` without an error). It simply was
not this bug.

## Still not measured

- **The 130-case `--corpus all` run** was launched and takes ~45 minutes of real
  model time; it had not finished when this was written. Re-run:
  `python -m bench.run --corpus all --engine pi --repeat 3 --out bench/results/pi-<ts>.json`
  and then `python -m bench.report --compare bench/results/baseline-prod-cursor.json
  bench/results/pi-<ts>.json`.
- **`docker compose build`** — no Docker daemon in the environment that produced
  this file. `npm ci --omit=dev --ignore-scripts` was verified against the committed
  lockfile; the NodeSource install line is what to watch on the first real build.
- **`--repeat 3`.** Everything above is `--repeat 1`, so no pass *rate* exists yet
  and a single verdict cannot be separated from sampling noise. The design's ship
  criterion is expressed over rates for that reason.

## What the harness found while being built

Five defects, none of which a stubbed test caught, all now covered by one:

1. `DefaultResourceLoader` needs both `cwd` and `agentDir`, or session
   construction throws.
2. Pi reads `OPENROUTER_API_KEY`; our environment provides `OPEN_ROUTER_KEY`. The
   bridge translates.
3. Forwarded `agent.*` events carried no `req_id`, so the bridge dropped every
   one: the turn worked and the room's live timeline stayed empty.
4. `run_corpus` uses one event loop per case, and a subprocess's pipes belong to
   the loop that created them — so a bridge left open hung the next case.
5. `payer` may be omitted when it is the sender (the schema says so), and grading
   its absence as a failure marked a *correct* turn wrong.

The pattern worth keeping: (5) surfaced because `ledger_state` passed while
`tool_selection` failed. When the two money graders disagree, suspect the
expectation before the engine.

---

## Iteration 1: enabling `bash` broke the arithmetic-heavy cases

`PI_BUILTIN_TOOLS=read,write,bash`, golden meals, `--repeat 1`, *before* the prompt
was hardened:

```
G1 ++  9.5s      G2 ++ 57.4s     G3 ++ 17.3s     G6 ++ 45.0s
G4 xxx 120.0s    ← hit the max_seconds cap and failed all three graders
G5 xx. 13.6s     ← failed tool_selection AND ledger_state
```

**G4 is the adjustment case (`+50k` on one member) and G5 the remainder case
(100k over three people).** They are the two most arithmetic-heavy cases in the
golden set, and they are the two that broke — G4 burning the entire 120s cap. The
cases that need no arithmetic beyond a plain division all passed.

That is the predicted cost of the trade, measured rather than argued: given `bash`,
the model works the split out itself instead of handing the numbers to
`propose_meal`, and the result never reaches the ledger. `ledger_state` failing
alongside `tool_selection` is what distinguishes this from the grader artifacts
found earlier — here the money itself is wrong, not the expectation.

Note also the latency: 45–57s on cases that took 13–17s without the builtins.

**Response:** the prompt now ranks the tools explicitly — the 14 room tools first,
`read`/`write`/`bash` as a last resort for work no tool covers and that is not about
money, with the forbidden operations named individually (split a bill, compute a
balance, work out a discount, decide who owes whom, build a QR) and the reason
stated: computing it yourself is wrong even when the arithmetic is right, because
that number never reaches the books. Both `money-safety.mdc` and
`build_system_prompt` carry it.

**Re-run to close the loop:**

```bash
python -m bench.run --corpus meals --engine pi --repeat 3 --case G4 --case G5 \
  --out bench/results/pi-G4G5-ranked.json
```

G4 and G5 recovering to `++` is the test of whether a prompt can hold this line. If
they do not, the honest options are `PI_BUILTIN_TOOLS=""` (which makes the guarantee
structural again, at the cost of the flexibility bash was enabled for) or an
allowlist without `bash` — `read,write` alone cannot do arithmetic.
