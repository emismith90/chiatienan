# Before you deploy this branch — what is verified, and what you must run

Branch `claude/headless-cms-pi-harness-nn18pb`, written 2026-09-06. Prod runs `main` with
7 real users. Read this before shipping.

## 1. What deploying actually changes for the room

**Nothing.** That is the point, and it is tested rather than asserted
(`backend/tests/test_prod_migration.py`, three tests):

- The container's normal start (`Database.create_all()`) adds the 16 `kn_` tables, the
  ledger tables and any pack tables. Everything is additive — `create_all` only creates
  missing tables, `sync_additive_columns` only adds missing columns, and neither drops,
  renames or retypes anything. **There is no manual migration step.**
- Existing rooms, members, messages and ledger edges are untouched, byte for byte.
- The bot is handed the same 19 tools in the same order. No `cms_*`, no `ask_*`.
- The one new visible behaviour is that every turn now writes a trace row
  (`kn_turn_traces`, 30-day retention), which is what the steward reads later.
- A redeploy is a no-op: the seeds report no actions and the published version does not
  move. (One cosmetic wart: each restart writes a draft, finds it identical and retires
  it, so `kn_profile_versions` grows by one retired row per boot per profile. Harmless.)

Everything built in Phases 5–10 — collections, the poker business, delegation, the CMS
tools, the steward — is **seeded but off**. A pack only produces tools when a profile
lists it, and agent capabilities default to none.

## 2. Deterministic verification (done here, re-run before you ship)

```bash
cd backend && .venv/bin/python -m pytest tests -q          # 1280 passed, 1 skipped
cd backend/agent_sidecar && node --test                     # 69/69
```

Included in that: golden fixtures byte-identical (9/9), the layering test, the
19-tool manifest, and the deploy rehearsal above. No test that exists on `main` has been
edited — check it yourself with:

```bash
comm -12 <(git diff --name-only origin/main -- backend/tests | sort) \
         <(git ls-tree -r origin/main --name-only backend/tests | sort)
```

Empty output means clean.

## 3. The LLM eval — run here, numbers below

`OPEN_ROUTER_KEY` **is** present in this environment (an earlier draft of this file said
otherwise; it was wrong). The benchmark has been run twice on this branch:

- `40d671f`, after the nine phases: 69 turns of `typical` at `--repeat 3`, 0 errored,
  `tool_selection` 69/69 = 1.00, `ledger_state` 55/60 = 0.92, p50 3.8s, $0.137. Compared
  against `origin/main` run the same day, no blocker attributable to this branch.
- After Phase 10, run on this branch tip: 69 turns, 0 errored, `tool_selection`
  69/69 = 1.00, `ledger_state` 58/60 = 0.97, p50 4.5s / p95 32.2s, $0.145.
  `--compare` against the run above reports **no blockers and no case dropped on either
  money grader**. Phase 10 was worth re-running for because, although the steward is off,
  three of its changes are executed by a live money turn: the settle guard now filters
  pending cards by `blocks_settlement`, `create_card` fills in a body when the kind
  provides one, and `drafts._commit` translates a kind's refused commit into a
  `LedgerError`. Evidence and the caveats (one case moved on non-determinism, one 33s
  turn moved p95) are in `backend/bench/results/agent-os-2026-09-06.md`.

To repeat it yourself:

```bash
cd backend
.venv/bin/python -m bench.run --corpus typical --engine pi --repeat 3 --out bench-out.json
.venv/bin/python -m bench.run --corpus typical --engine pi --repeat 3 --out main.json --compare bench-out.json
```

The in-CMS eval (the one the publish gates read) is a different thing and still worth
loading, because it is what makes gate 4 non-vacuous:

```bash
.venv/bin/python -m app.evalhost import       # offline: 23 cases, 3 graders — verified
.venv/bin/python -m app.evalhost run --suite lunch-typical --version <published version id>
```

## 4. Turning the steward on — the one thing that changes the bot

The steward is seeded on every boot as a `sub` agent with its own profile (one pack,
`os_admin`; read + draft capabilities; it manages the lunch profile). Nothing points at
it, so today it is unreachable. Connecting it is one call:

```bash
curl -X PATCH https://<host>/api/admin/agents/<phoenix agent id> \
  -H "X-Admin-Password: $ADMIN_PASSWORD" -H "X-Actor: hung" \
  -H "Content-Type: application/json" \
  -d '{"delegates_to": [<steward agent id>]}'
```

Ids: `GET /api/admin/agents`. After that call, Phoenix's manifest is **20 tools** — the
19 plus `ask_steward`. That is a real change to what the model sees, so:

- **run the eval in step 3 again afterwards** and compare against the baseline, and
- undo it by setting `delegates_to` back to `[]` if the numbers move.

Asking the steward (`@phoenix nhờ steward xem lại`) makes it read the friction report,
optionally draft one change to a skill/rule/prompt, and open a proposal. It cannot
publish anything: `cms_publish` is refused for a profile it merely manages, and it has
no `publish` verb anyway. A proposal is approved by a person at
`POST /api/admin/proposals/<id>/approve`.

## 5. Known gaps, stated plainly

- **The Confirm button is not in the UI yet.** A proposal renders as a card only in a
  space whose own agent authored it; through `ask_steward` it comes back as a body with
  the admin URL. Where a card is produced, the frontend shows its text (rationale and
  diff) but no buttons until the generic `DraftCard` ticket in `TODO.md` lands.
- **The eval is a baseline, not a gate**, until a profile names `eval.suites`. Granting
  an agent the `publish` capability is refused without it, by design.
- The `prod` corpus cannot be benchmarked in a checkout: it is real conversation and is
  `.gitignore`d, so 14 of the comparison's blockers are `MISSING` by construction.
