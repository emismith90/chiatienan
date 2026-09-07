# User guide — the bot, and how to change it

Two audiences, in order:

- **[Part 1 — everyone in the room](#part-1-everyone-in-the-room).** Talking to the bot,
  and the **Bot** tab where you can read and change how it behaves.
- **[Part 2 — the operator](#part-2-the-operator).** The admin API: bindings, agents,
  gates, eval, packages. You need the admin password for all of it.

For deploying, see the
[deploy runsheet](superpowers/plans/2026-09-06-deploy-runbook.md). For the code, see the
[developer guide](developer-guide.md).

---

# Part 1: everyone in the room

## Talking to the bot

Mention it (`@phoenix`) and say what happened in your own words. It writes nothing to the
ledger on its own: anything about money comes back as a **draft card** with the numbers on
it, and it only counts once somebody presses **Confirm**.

```
@phoenix nay ăn bún riêu 245k, emi nhím tôi giang
@phoenix tôi đã trả tiền Linh
@phoenix ai nợ ai
@phoenix roll xem ai rót trà
```

If a card shows the wrong numbers, press **Cancel** and say it again. A card that is never
confirmed changes nothing, but it does block "who owes who" until you decide, so clear it.

## The Bot tab

The side panel has three tabs: **Ledger**, **Memory**, and **Bot**. The Bot tab is the
bot's own configuration — the instructions it reads before every single turn.

Everyone in the room can **read** all of it. Whether you can **change** it depends on the
room (see [Why can't I edit?](#why-cant-i-edit)).

### What you see

| Section | What it is |
|---|---|
| **System prompt** | The main instruction block. Who the bot is, how it should behave. |
| **Extra instructions** | Extra lines added after the prompt. One per line. The easiest safe place to add a habit. |
| **Skill · \<name\>** | A procedure for one job — `record-meal`, `balances`, `pick-random`… This is where the bot's actual behaviour lives. |
| **Rule · \<name\>** | A standing rule. Some are **🔒 locked** — see below. |
| **Not editable here** | The model, the time and tool limits, the tool packs, the pipeline. Facts, not fields. |
| **History** | Every version: who changed it, when, what they changed, and their note. |

### What you can change, and what you can't

**You can change:** the system prompt, the extra instructions, any skill, and any rule that
is not locked.

**You cannot change, from here:**

| | Why |
|---|---|
| The **model** | It costs money per turn and has to pass a compatibility check first. |
| The **limits** (tools, seconds) | Same reason. |
| The **tool packs** and **pipeline** | These include the guards that catch the bot claiming it wrote something it did not. |
| **🔒 Money rules** | The rule that says the *tools* own every number and the bot never does arithmetic itself. It is the reason the ledger can be trusted. |

Those need the admin password. That is deliberate: this bot moves real money, and the
things that keep it honest should not be one careless edit away.

> **What the guards do and do not catch.** The locked list above is *enforced*: the system
> refuses the change. A rule written in prose is *advice* — the bot usually follows it, but
> an instruction that contradicts it can win. So a skill saying "work the split out
> yourself" may actually get obeyed, even though a money rule says not to. Nothing can be
> written to the ledger without a card somebody confirmed, and a reply claiming "Đã ghi #14"
> when nothing was recorded is caught and replaced. But a *number in a sentence* is not
> guaranteed to have come from a tool. Treat the cards as the truth and the prose as
> commentary.

### Making a change

1. Edit the box you want.
2. Write a short note in **What changed, and why?** — it goes in the history next to your
   name, and it is what makes the log worth keeping.
3. Press **Publish**.

The change is live for the next message anyone sends. It is checked first, and refused if
it would break something — for example a `{{placeholder}}` the system does not know. If it
is refused, nothing changes at all.

**If someone else published while you were typing**, you get *"Someone else changed the bot
while you were editing."* Reload, look at what they did, and reapply your change on top.
Nothing of yours is saved in that case — it does not overwrite them, and they do not
overwrite you.

### The history, and undoing things

Every publish makes a new version. Nothing is ever overwritten or deleted.

Click a version to see its **diff** — exactly what changed, line by line. Any earlier
version has a **Republish** button: it puts that content back as a *new* version, so the
thing you are undoing stays in the log too. If a change makes the bot worse, republish the
version before it; you do not need to remember what it said.

### Two warnings you may see

**"Changes here affect every room that has not been given its own bot."** Rooms share one
default bot unless an operator has given a room its own. Your edit will change theirs too.

**"This bot has been edited by hand, so it no longer picks up prompt or skill changes from
a deploy."** The first time anyone edits, this profile stops tracking the code. That is
what makes your edits stick — but it also means improvements shipped by a deploy will not
reach this room until an operator re-syncs it. Worth knowing before the first edit.

### Why can't I edit?

If you see *"This room runs the shared default bot, so it can be read here but not
changed"*, the room has not been given its own binding.

Anyone can create a room, and a new room automatically runs the shared default bot. If
being in a room were enough to edit it, a stranger could create their own room and rewrite
*your* bot from it. So editing needs an operator to bind the room first — one call, and it
changes nothing else about how the room works.

## When the bot reviews itself

If the operator has switched on the **steward**, you can ask the bot to look at its own
recent mistakes:

```
@phoenix nhờ steward xem lại
```

It counts what actually went wrong — forged confirmations, money it could not account for,
tool calls a rule refused, turns that timed out — and if there is a clear pattern, it drafts
**one** change and opens a **proposal**. It never publishes anything itself. A person
approves it, either from the card in the room or through the admin API.

If there is nothing wrong, it says so and stops. That is the intended answer, not a
failure.

---

# Part 2: the operator

Everything here needs `X-Admin-Password`. `X-Actor` names you in the audit log; it is
self-declared, so treat the password as the real credential.

```bash
export A="X-Admin-Password: $ADMIN_PASSWORD"
export H="X-Actor: hung"
export B=https://chiatienan.duckdns.org/api/admin
```

## The shape of it

A **business** (lunch, poker) owns **sources** (prompt, rules, skills, templates) and one
or more **profiles**. A profile has **versions**; exactly one is published, and that is what
a room runs. An **agent** points at a profile. A **space** (a room) either has a *binding*
to an agent or falls back to the business's default agent.

```
business ─┬─ sources (prompt / rule / skill / template)
          └─ profile ── versions (draft → published → superseded)
                          ▲
             agent ───────┘        space (room) ──binding──▶ agent
                                   (no binding → the business's default agent)
```

Sources are **upstream of versions**: taking a draft with a snapshot pulls the current
sources in. So a change made only to a version's spec is reverted by the next snapshotting
draft. Everything in the system that publishes a change writes the matching sources too —
if you script something yourself, do the same.

## Everyday tasks

**See what a room actually runs**

```bash
curl -sS -H "$A" $B/spaces/3/resolved
```

**Let a room edit its own bot** (this is what turns the Bot tab from a reader into an editor)

```bash
curl -sS -H "$A" $B/agents                      # find the agent id
curl -sS -X PUT $B/spaces/3/binding -H "$A" -H "$H" \
  -H "Content-Type: application/json" -d '{"agent_id": 1}'
```

Undo with `DELETE $B/spaces/3/binding`. Binding to the agent the room already runs changes
no behaviour — it only says "this room is allowed to edit this".

**Edit content yourself** (the unrestricted path — you can change the model, caps and money
rules, which a room member cannot)

```bash
curl -sS -X POST $B/profiles/1/versions -H "$A" -H "$H"                 # a draft
curl -sS -X PATCH $B/profiles/1/versions/4 -H "$A" -H "$H" \
  -H "Content-Type: application/json" -d '{"models": {"text": "..."}}'
curl -sS -X POST $B/profiles/1/versions/4/publish -H "$A" -H "$H" \
  -H "Content-Type: application/json" -d '{"override_reason": "why this is safe"}'
```

**Roll back**: `POST $B/profiles/1/rollback -d '{"version": 3}'`. Note this re-publishes the
same row; the room's own Republish button drafts a new version instead and keeps more
history. Prefer the room button when either would do.

**Re-sync a profile to the code** after someone edited it by hand (`managed_by: human`):
take a draft, patch it with what `build_default_spec` produces, and publish.

## The five gates

Every publish is checked. A failure returns 422 with the gate and the reason.

| Gate | Refuses |
|---|---|
| **1 schema** | A spec that does not validate; an unknown plugin or a bad plugin config; a `{{variable}}` the renderer does not know; a discoverable skill without the `read` builtin. |
| **2 money safety** | A money-handling profile with a risky builtin tool (`bash`, `write`, `edit`) — unless you pass an `override_reason`. The lunch profile always needs one. |
| **3 model probe** | A model that changed and has no recent successful probe on record. |
| **4 eval** | A profile that names `eval.suites` without a finished run of *that exact content* passing every blocking grader. Vacuous until a profile names suites. |
| **5 reflexivity** | An **agent** publishing outside its `self_change_scope`, or touching the blacklist. Does not apply to people. |

`bypass_gates` exists for boot seeding only.

## Agents and the steward

```bash
curl -sS -X POST $B/agents -H "$A" -H "$H" -H "Content-Type: application/json" \
  -d '{"business_id": 1, "slug": "helper", "name": "Helper", "profile_id": 2, "role": "sub"}'
```

A manager whose `delegates_to` lists sub ids gets one `ask_<slug>(task)` tool per sub. The
sub runs as a nested turn in the same room, inside the manager's remaining time and tool
budget. Its structured tool results can back the manager's numbers; **its text never can**,
and only the manager's own calls make cards.

The **steward** is seeded on every boot as a sub-agent with its own profile (`os_admin`
only, read + draft) and nothing pointing at it. Turn it on:

```bash
curl -sS -X PATCH $B/agents/1 -H "$A" -H "$H" -H "Content-Type: application/json" \
  -d '{"delegates_to": [3]}'
```

Phoenix goes from 19 to 20 tools. **That changes what the model sees — run the benchmark
and compare before leaving it on** (see the runsheet). Undo with `{"delegates_to": []}`.

Its capabilities are `read` and `draft`. It cannot be granted `publish` unless its profile
names `eval.suites` — the system refuses.

## Proposals

```bash
curl -sS -H "$A" $B/proposals
curl -sS -X POST $B/proposals/7/approve -H "$A" -H "$H"
curl -sS -X POST $B/proposals/7/reject  -H "$A" -H "$H"
```

Approval publishes through every gate and then writes the sources, so the change survives
the next draft. A gate failure leaves the proposal `pending` with `last_error` — fix the
reason and approve again.

## Traces and friction

```bash
curl -sS -H "$A" "$B/spaces/3/turns?limit=20"
curl -sS -H "$A" $B/spaces/3/turns/<turn_id>
```

Every turn writes a row: the plugins that ran, each tool call with its arguments and result,
and a summary. Retention is the `keep_days` config on the `kernos.after.trace` plugin (30
days by default). A turn that raised is traced with its error.

The steward reads the same rows through `cms_get_friction`, which counts six things
deterministically: forged commit claims, run errors, tool calls a rule refused, unbacked
money, capped turns and slow turns.

## Eval

```bash
curl -sS -X POST $B/businesses/1/eval/import -H "$A" -H "$H"        # 23 cases, 3 graders
curl -sS -X POST "$B/profiles/1/versions/4/eval?suite=lunch-typical" -H "$A" -H "$H"
curl -sS -H "$A" $B/eval/runs
```

A run is a background job. Once a profile names `eval.suites`, gate 4 starts biting — which
is what you want before granting any agent the `publish` capability.

## Collections

A schema-validated document type, per business, with per-room documents:

```bash
curl -sS -X PUT $B/businesses/1/collections/places -H "$A" -H "$H" \
  -H "Content-Type: application/json" -d '{"schema": {...}}'
```

A profile that enables the `collections` pack gets `places_find`, `places_upsert` and
`places_delete` generated from the definition.

## Export and import

```bash
curl -sS -H "$A" $B/profiles/1/export -o profile.zip       # a Pi package
curl -sS -X POST $B/businesses/1/import -H "$A" -H "$H" --data-binary @profile.zip
```

Export gives skills, prompts, `AGENTS.md`, `.pi/settings.json` and `kernos.json` for a
lossless round trip. **Import never publishes** — it writes sources and a draft for you to
review. Because sources are upstream of drafts, an import changes the business's future
drafts; read the diff before publishing.

## When something looks wrong in production

Read first, through the export API — it needs no SSH:

```bash
export D="X-Debug-Key: $DEBUG_API_KEY"; export X=https://chiatienan.duckdns.org/internal/debug
curl -sS -H "$D" $X/ping
curl -sS -H "$D" "$X/conversation.txt?room_id=3&days=7"
curl -sS -H "$D" "$X/logs?lines=300"
```

"All the data got wiped" is almost always a missing column, not data loss — check
`$X/logs | grep "no such column"` and the row counts in `$X/ping` before believing
anything is gone. The `deploy-chiatienan` skill has the full detail.
