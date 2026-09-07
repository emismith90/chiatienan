# Operations — the Agent OS in production

Phases 1–11 (PR #50) **merged and deployed on 2026-09-07** as `e218d1d`. This was the
one-time ship sheet; it is now the standing operations doc. Three parts:

1. [What is live, and what is still off](#1-what-is-live-and-what-is-still-off) — read this first.
2. [The two remaining opt-ins](#2-the-two-remaining-opt-ins) — the live decisions still to make.
3. [Standing procedures](#3-standing-procedures) — deploy, verify, roll back. Applies to every deploy.

The record of the 2026-09-07 deploy itself is in [§4](#4-the-2026-09-07-deploy-as-it-happened).

---

## 1. What is live, and what is still off

| | State in production |
|---|---|
| Content plane | **live** — 16 `kn_` tables, created additively on container start |
| Turn traces | **live** — every turn writes a row (`kn_turn_traces`, 30-day retention) |
| Seeded businesses | **live** — `lunch` (profile 1) and `poker` (profile 2), both `managed_by: boot` |
| The **Bot** tab | **live, read-only in every room** — no room has a binding |
| Room editing | **off** — needs a binding ([§2.1](#21-turn-on-room-editing)) |
| The steward | **seeded, unreachable** — agent 3, `delegates_to: []` ([§2.2](#22-turn-on-the-steward)) |
| Collections, poker room, delegation, CMS tools | **off** — no profile lists the packs |
| The bot's behaviour | **unchanged** — 19 tools, same order, same prompt |

Verified on the live box after the deploy:

```
kn_agents:  phoenix  manager default caps={}                          delegates=[]
            dealer   manager default caps={}                          delegates=[]
            steward  sub             caps={"cms":["read","draft"], "manages_profiles":[1]}  delegates=[]
kn_space_bindings: (empty)
kn_profiles: 1 lunch/default boot · 2 poker/default boot · 3 lunch/steward boot
```

Nothing points at the steward and no room is bound, so every new surface is inert until
somebody in §2 decides otherwise.

---

## 2. The two remaining opt-ins

Independent of each other, both reversible. **Neither is done.**

Both are switches on the **Live** tab of `/admin` (sign in with `ADMIN_PASSWORD`), which
says what each one does before it does it. The `curl` form of each is below, and is what
the tab calls.

### 2.1 Turn on room editing

Until a room has its own binding, the Bot tab is a reader. Editing is gated on a binding
rather than on membership because `POST /api/rooms/create` is public — an unbound room
resolves to the same default agent the real room runs, so a stranger's room would
otherwise be a way in.

`/admin` → **Live** → *Bindings*: space id `3`, agent `phoenix`, **Bind**. Or:

```bash
export A="X-Admin-Password: $ADMIN_PASSWORD"; export H="X-Actor: hung"
export B=https://chiatienan.duckdns.org/api/admin

curl -sS -H "$A" $B/agents                       # phoenix is agent 1
curl -sS -X PUT $B/spaces/3/binding -H "$A" -H "$H" \
  -H "Content-Type: application/json" -d '{"agent_id": 1}'
```

Room 3 is the real room. Binding it to `phoenix` — the agent it already runs — changes no
behaviour; it is an authorisation fact.

After it: any member of room 3 can edit the system prompt, the skills and the non-money
rules, and republish any earlier version. They **cannot** touch the model, the caps, the
pipeline, the tool packs, the builtin tools or any money-tagged rule.

**Undo:** `curl -X DELETE $B/spaces/3/binding -H "$A" -H "$H"`

**Know before you do it:** the first member edit flips profile 1's `managed_by` from
`boot` to `human`, and from then on **a deploy stops refreshing that profile's prompt,
skills and rules from code**. The Bot tab says so on screen. To re-sync later, take a draft,
patch it with what `build_default_spec` produces, and publish.

### 2.2 Turn on the steward

`/admin` → **Live** → *Agents* → tick **ask_steward** under `phoenix`. Or:

```bash
curl -sS -X PATCH $B/agents/1 -H "$A" -H "$H" \
  -H "Content-Type: application/json" -d '{"delegates_to": [3]}'
```

Phoenix's manifest goes 19 → **20 tools** (`ask_steward`). That is a real change to what the
model sees, so **run the benchmark and compare before leaving it on** ([§3.1](#31-before-a-deploy)).
Undo with `{"delegates_to": []}`.

Then `@phoenix nhờ steward xem lại` makes it read the friction detectors and, when a pattern
is clear, draft one change and open a proposal. It cannot publish: a person approves at
`POST $B/proposals/<id>/approve`.

---

## 3. Standing procedures

### 3.1 Before a deploy

```bash
cd backend && .venv/bin/python -m pytest tests -q      # expect: 1318 passed, 1 skipped
cd agent_sidecar && node --test                         # expect: 69/69
cd ../../frontend && npx tsc --noEmit && npx vitest run  # expect: 301 passed, tsc clean
```

The one skip is `test_scenario_week_llm.py` — a real-model scenario test, opt-in behind
`RUN_LLM_EVAL=1`. Included in the above: the golden fixtures (9/9 byte-identical replies),
the layering test, the 19-tool manifest, and `test_prod_migration.py`, which boots the tree
over a production-shaped database.

**Benchmark** — needed whenever you change what the model sees (prompt, skills, tool
manifest) or touch code a money turn executes:

```bash
cd backend
.venv/bin/python -m bench.run --corpus typical --engine pi --repeat 3 --out /tmp/now.json
.venv/bin/python -m bench.report --compare bench/results/pi-typical-phase11-2026-09-06.json /tmp/now.json
```

Ship criterion: no case down more than 1/3 on `tool_selection` or `ledger_state`. Each
blocker reports a Fisher exact p-value and says whether it is distinguishable from sampling
noise, with the re-run command when it is not. **A blocker marked `WITHIN NOISE` means
re-run that case at `--repeat 15`, not stop.** `bills` cases go through the vision model and
are genuinely flaky (`B3`'s measured rate is 0.60); `week` and `meals` cases are close to
deterministic, so a drop there is real. Background:
[`backend/bench/results/agent-os-2026-09-06.md`](../../../backend/bench/results/agent-os-2026-09-06.md).

If you are shipping a branch, check you have not edited a test that predates it:

```bash
comm -12 <(git diff --name-only $(git merge-base HEAD origin/main) -- backend/tests | sort) \
         <(git ls-tree -r $(git merge-base HEAD origin/main) --name-only backend/tests | sort)
```

### 3.2 Back up first, for anything that touches the schema

```bash
ssh -i ~/.ssh/digitalocean-openclaw root@chiatienan.duckdns.org
cd /opt/chiatienan
docker compose exec backend python -c "import sqlite3,datetime,os; os.makedirs('/data/backups',exist_ok=True); sqlite3.connect('/data/chiatienan.db').backup(sqlite3.connect(f'/data/backups/backup-{datetime.date.today()}.db'))"
df -h /var/lib/docker && docker image prune -af    # a full disk is how a deploy silently ships stale code
```

SSH timing out with no banner is the network, not the key — phone hotspot, or the
DigitalOcean web console. See the `deploy-chiatienan` skill.

### 3.3 Deploy

Merge to `main`, or **Actions → Deploy → Run workflow**. CI builds images, pushes to GHCR,
the droplet pulls. Never `up -d --build` on the 512 MB host. The workflow's last step fails
the job if the running tag ≠ `github.sha`.

### 3.4 Verify — take a baseline *before*, compare *after*

This is what caught nothing on 2026-09-07 and is exactly why it is worth doing. Capture the
"before" while the old code is still live:

```bash
export D="X-Debug-Key: $DEBUG_API_KEY"; export X=https://chiatienan.duckdns.org/internal/debug
curl -sS -H "$D" $X/ping > /tmp/before-ping.json
for t in meals members payments meal_shares settlements places; do
  curl -sS -H "$D" "$X/tables/$t.csv" -o "/tmp/before-$t.csv"; done
```

After the deploy, re-fetch and diff the row counts, and fingerprint the ledger:

```bash
python3 - <<'PY'
import csv, hashlib
rows = [r for r in csv.DictReader(open('/tmp/after-meals.csv'))
        if r['room_id'] == '3' and r['voided'] == 'False']
print(len(rows), sum(float(r['total_amount']) for r in rows),
      hashlib.sha256(''.join(sorted(r['id'] + r['total_amount'] for r in rows)).encode()).hexdigest()[:16])
PY
```

Then the log, and the app:

```bash
curl -sS -H "$D" "$X/logs?lines=400" | grep -E "(ERROR|CRITICAL)[: ]|Traceback \(most recent"
```

> Grep for `ERROR` with a word boundary. `uvicorn.error` is a *logger name* that appears on
> ordinary INFO lines ("Application startup complete"), and a loose `-i error` matches all
> of them — it looks like two dozen errors when there are none.

In the app: send a normal question in the room and confirm a normal answer; open the **Bot**
tab and confirm it renders read-only with the shared-bot notice; open `/admin`, sign in,
and confirm the **Live** tab lists the businesses, profiles and agents. After a frontend deploy,
unregister the service worker and clear caches before deciding a UI change did not work —
the SW serves stale chunks.

### 3.5 Roll back

- **App:** re-run Deploy on the previous commit SHA. The schema is additive, so older code
  runs against the newer tables without complaint.
- **A bad bot edit:** the Bot tab's **Republish** on any earlier version, or `/admin` →
  **Content** → the version → **New draft from vN**, then **Publish**. Both write a new
  version rather than rewriting history, which `POST $B/profiles/1/rollback` does not.
- **Data:** restore `/data/backups/backup-<date>.db` with the stack stopped.

---

## 4. The 2026-09-07 deploy, as it happened

Merged `e218d1d` at 10:01Z; CI green (run 105); Deploy run 35 green in both jobs, including
*Verify the running images match this commit*. Prod picked it up at 10:06:50Z.

Startup log, in full:

```
17:06:49 INFO uvicorn.error: Started server process [1]
17:06:49 INFO kernos.content: schema: added payments.ref_kind
17:06:50 INFO chiatienan: [kernos] boot: business created; source skill/balances created;
         source skill/pick-random created; source skill/record-meal created;
         source skill/record-payment created; source skill/suggest-lunch created;
         source rule/money-safety created; profile created; version 1 published;
         default agent created; catalogue …deepseek… added; catalogue …qwen… added
17:06:50 INFO uvicorn.error: Application startup complete.
```

One additive column, the seed, no errors, no tracebacks.

Data integrity, baseline captured before the deploy and compared after:

| table | before | after |
|---|---|---|
| meals | 32 | 32 |
| meal_shares | 121 | 121 |
| payments | 74 | 74 |
| members | 11 | 11 |
| places | 100 | 100 |
| settlements | 0 | 0 |
| rooms / messages | 4 / 467 | 4 / 467 |

Room 3's live ledger: **23 meals, 8,229,960đ, sha `2f300cff1293decc` — identical before and
after.** Nothing was disturbed.
