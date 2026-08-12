# Cursor → Pi: what the harness actually measured

**Status: the port is complete and runs; the full acceptance benchmark is NOT.**
This file reports what was measured, what was not, and the one open bug that
stopped it — because a benchmark that reports only its wins is worse than no
benchmark, since it will be believed.

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

## ⛔ Not measured, and why

**No full-corpus Pi run completed.** Two independent attempts stopped after case
`G1`. `G2` (`@bot tôi trả 300k, An không ăn`) ran **>230s against a configured
`max_seconds=120`** and had to be killed.

The cap is implemented and unit-tested: `turn.js` races `session.prompt()` against
the deadline, so a session whose `prompt()` never settles still returns at the cap
(the test asserts exactly that, including that `abort()` was called). **That fix is
not sufficient for a real turn**, so something else is holding it — the likely
candidates, in order:

1. **pi's own retry loop.** `auto_retry_*` events exist in its event vocabulary; a
   provider retry with backoff may restart work the deadline already cancelled.
2. **The tool round-trip.** A `tool_call` whose `tool_result` never arrives blocks
   inside the tool's `execute`, which the cap does not interrupt — the race only
   covers `prompt()`.
3. **The per-case sidecar spawn** now that the bridge closes per case, if a spawn
   itself stalls.

**This matters more than the benchmark it blocked.** `chat.py` holds
`_agent_lock` for the whole turn, so one unbounded turn freezes *every* room, not
just the one that triggered it. Treat it as the top item before any deploy:
reproduce with `--case G2`, and instrument which of the three it is.

Also unrun: `docker compose build` (no Docker daemon in the environment that
produced this file), and the prod-corpus Pi run that a like-for-like `--compare`
against the baseline needs.

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
