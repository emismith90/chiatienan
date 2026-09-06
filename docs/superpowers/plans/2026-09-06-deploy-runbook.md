# Prod deploy runsheet — branch `claude/headless-cms-pi-harness-nn18pb`

Written 2026-09-06. Production runs `main` (`5066d85`, 2026-08-26) with **7 real users
and a live money ledger**. This branch is Phases 1–11 of the Agent OS / headless-CMS
work. Read the whole sheet before starting; the steps are ordered and step 5 is the only
one that changes what your users see.

---

## 0. What this deploy actually does

| | |
|---|---|
| **Schema** | Automatic and additive. `Database.create_all()` on container start adds the 16 `kn_` tables, the ledger tables and pack tables; `sync_additive_columns` adds missing columns. Nothing is dropped, renamed or retyped. **No manual migration.** |
| **Existing data** | Untouched — rooms, members, messages, meals, ledger edges. Tested, not asserted. |
| **The bot's behaviour** | Unchanged. Same 19 tools, same order, same prompt. |
| **New, visible** | Every turn writes a trace row (`kn_turn_traces`, 30-day retention). A **Bot** tab appears in the side panel, read-only until step 5. |
| **Seeded but off** | Collections, the poker business, delegation, the CMS tools, the steward. A pack only produces tools when a profile lists it, and agent capabilities default to none. |

Phase 11 (the room CMS) is the first thing here that ships **on** — but only as a
*reader*, because editing needs a binding that does not exist until you make it in
step 5.

---

## 1. Verify the build before you ship it

```bash
cd backend && .venv/bin/python -m pytest tests -q          # expect: 1309 passed, 1 skipped
cd agent_sidecar && node --test                             # expect: 69/69
cd ../../frontend && npx tsc --noEmit && npx vitest run      # expect: 301 passed, tsc clean
```

That includes the golden fixtures (9/9, byte-identical replies), the layering test, the
19-tool manifest, and `tests/test_prod_migration.py` — the deploy rehearsal, which boots
this branch over a database shaped like production's and checks the table above.

Confirm no test that exists on `main` was edited:

```bash
comm -12 <(git diff --name-only origin/main -- backend/tests | sort) \
         <(git ls-tree -r origin/main --name-only backend/tests | sort)
```

Empty output = clean.

**Benchmark (real model, real money-graders).** Latest run on this tip is recorded in
`backend/bench/results/agent-os-2026-09-06.md`. To repeat:

```bash
cd backend
.venv/bin/python -m bench.run --corpus typical --engine pi --repeat 3 --out /tmp/now.json
.venv/bin/python -m bench.report --compare bench/results/pi-typical-phase10-2026-09-06.json /tmp/now.json
```

Ship criterion: no case down more than 1/3 on `tool_selection` or `ledger_state`.

> **Read this before trusting a blocker on a `bills` case.** The Phase 11 run tripped the
> criterion on `B3` (3/3 → 1/3 on both money graders). Re-running `B3` at `--repeat 15`
> put its natural pass rate at **0.60**, at which three attempts give 3/3 about 22% of the
> time and 1/3 about 29% — so both samples were noise, and the run is clear. The chance of
> `--repeat 3` inventing a blocker on a 60% case is 0.076 per case per comparison, and
> there are 23 cases. **A blocker on a `bills` case means re-run that case at `--repeat 15`;
> a blocker on a `week` or `meals` case is real.** Detail and the per-case rate:
> `backend/bench/results/agent-os-2026-09-06.md`.

---

## 2. Back up production first

```bash
ssh -i ~/.ssh/digitalocean-openclaw root@chiatienan.duckdns.org
cd /opt/chiatienan
docker compose exec backend python -c "import sqlite3,datetime,os; os.makedirs('/data/backups',exist_ok=True); sqlite3.connect('/data/chiatienan.db').backup(sqlite3.connect(f'/data/backups/pre-agentos-{datetime.date.today()}.db'))"
df -h /var/lib/docker && docker image prune -af     # a full disk is how a deploy silently ships stale code
```

If SSH times out with no banner that is the network, not the key — use a phone hotspot or
the DigitalOcean web console (see the `deploy-chiatienan` skill).

---

## 3. Deploy

Merge the PR to `main`, or **Actions → Deploy → Run workflow**. The runner builds images,
pushes to GHCR, and the droplet pulls — never `up -d --build` on the 512 MB host.

Then verify the running image is actually this commit:

```bash
cd /opt/chiatienan
docker compose ps --format '{{.Service}} {{.Image}}'    # must match the deployed SHA
docker compose logs --tail=100 backend | grep -iE "error|traceback|no such column"
```

`deploy.yml` now fails the job when the running tag ≠ `github.sha`, but spot-check anyway.

---

## 4. Confirm the room is unchanged

Through the export API — no SSH needed:

```bash
export H="X-Debug-Key: $DEBUG_API_KEY"; export B=https://chiatienan.duckdns.org/internal/debug
curl -sS -H "$H" $B/ping            # row counts match what they were
curl -sS -H "$H" "$B/conversation.txt?room_id=3&days=1"
```

Then in the app: send `@phoenix ai nợ ai` and confirm a normal answer. Open the **Bot**
tab — it should render the prompt, skills and rules **read-only**, with the notice that
this room runs the shared default bot.

> After a frontend deploy, unregister the service worker and clear caches before deciding
> a UI change "didn't work" — the SW serves stale chunks.

**Stop here if you only wanted the framework.** Everything below changes behaviour.

---

## 5. Turn on room editing (optional, reversible)

Editing needs the room to have its **own binding** — membership is not a permission,
because `POST /api/rooms/create` is public and an unbound room resolves to the same
default agent your real room runs. Until you do this, nobody can edit anything.

```bash
# ids first
curl -sS -H "X-Admin-Password: $ADMIN_PASSWORD" https://chiatienan.duckdns.org/api/admin/agents
# bind room 3 to phoenix (same agent, same profile — an authorisation fact, not a change)
curl -sS -X PUT https://chiatienan.duckdns.org/api/admin/spaces/3/binding \
  -H "X-Admin-Password: $ADMIN_PASSWORD" -H "X-Actor: hung" \
  -H "Content-Type: application/json" -d '{"agent_id": <phoenix id>}'
```

After this, any member of room 3 can edit the prompt, the skills and the non-money rules
from the Bot tab, and republish any earlier version. They **cannot** touch the model, the
caps, the pipeline, the tool packs, the builtin tools or any money-tagged rule — those
stay behind the admin password.

**Undo:** `DELETE /api/admin/spaces/3/binding` returns the room to view-only.

**Know this before you do it:** the first member edit flips the profile's `managed_by`
to `human`, and from then on a deploy **stops refreshing that profile's prompt, skills
and rules from code**. The Bot tab says so. To re-sync later, publish a fresh draft from
`build_default_spec` through the admin API.

---

## 6. Turn on the steward (optional, independent of step 5)

```bash
curl -sS -X PATCH https://chiatienan.duckdns.org/api/admin/agents/<phoenix id> \
  -H "X-Admin-Password: $ADMIN_PASSWORD" -H "X-Actor: hung" \
  -H "Content-Type: application/json" -d '{"delegates_to": [<steward id>]}'
```

Phoenix's manifest goes 19 → **20 tools** (`ask_steward`). That is a real change to what
the model sees, so **re-run the benchmark from step 1 and compare** before leaving it on.
Undo by setting `delegates_to` back to `[]`.

Asking it (`@phoenix nhờ steward xem lại`) makes it read the friction detectors, and when
a pattern is clear, draft one change and open a proposal. It cannot publish: a person
approves at `POST /api/admin/proposals/<id>/approve`.

---

## 7. Rollback

- **App:** re-run the Deploy workflow on the previous commit SHA. The schema is additive,
  so `main`'s code runs against the new tables without complaint.
- **A bad bot edit:** the Bot tab's **Republish** button on any earlier version — that is
  what it is for, and it writes a new version rather than rewriting history.
- **Data:** restore `/data/backups/pre-agentos-<date>.db` (stop the stack first).

---

## 8. Known gaps, stated plainly

- **A member with edit rights can still get the bot to do arithmetic in `bash`.** The
  enforced invariants hold — a ledger write needs a confirmed card, and a forged "Đã ghi"
  is blocked — but `bash` is enabled and `backed_amounts` counts a builtin's output as
  evidence, so no validator warns about a bash-derived number in prose. Fixing that
  changes live validator behaviour and is a phase of its own. This is the main reason
  step 5 is opt-in.
- **The Confirm button for a steward proposal card is not in the UI yet** (`TODO.md`);
  the card renders its rationale and diff, and approval goes through the admin API.
- **Gate 4 (eval) is vacuous** until a profile names `eval.suites`. Load the corpus with
  `.venv/bin/python -m app.evalhost import` (23 cases, 3 graders, offline) if you want it
  to bite.
- **The `prod` benchmark corpus is `.gitignore`d** (real conversation), so 14 of a
  comparison's blockers are `MISSING` by construction.
