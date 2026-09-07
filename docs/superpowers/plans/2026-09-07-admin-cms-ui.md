# Phase 12 — the admin CMS UI

## Why

There are 63 routes under `/api/admin/*` and **no screen for any of them**. The whole
content plane — sources, profiles, versions, publish, agents, bindings, proposals, audit —
is reachable only by `curl` with `X-Admin-Password`. The two remaining production
opt-ins in [Operations §2](2026-09-06-deploy-runbook.md#2-the-two-remaining-opt-ins) are
literally curl commands pasted into a doc. Room *members* got a UI in Phase 11; the
operator did not.

This phase gives the operator the day-to-day subset as a page, and nothing more.

## What is in scope, and what deliberately is not

The test for "in scope" is: **does an operator do this in a normal week, and does doing it
wrong break the bot in a way the UI can explain?**

In:

| Screen | Wraps | Why it earns a screen |
|---|---|---|
| **Overview** | `/businesses`, `/profiles`, `/agents`, `/bindings`, `/spaces/{id}/resolved` | answers "what does this room run, and who may edit it" in one place |
| | `PUT`/`DELETE /spaces/{id}/binding` | Operations §2.1, as a switch |
| | `PATCH /agents/{id}` (`delegates_to`, `capabilities`) | Operations §2.2, as a switch |
| **Content** | `/businesses/{id}/sources/**` with `If-Match` | the prompt, skills and rules actually live here |
| | `/profiles/{id}/versions`, `…/{v}`, `…/{v}/diff` | the revision log with a real diff |
| | `POST …/versions`, `…/publish`, `…/retire`, `/rollback` | the publish loop, with gate failures on screen |
| **Proposals** | `/proposals`, `…/approve`, `…/reject` | the human half of the steward loop |
| **Audit** | `/audit` | who changed what, when |

Out, on purpose:

* **Editing `models`, `caps`, `pipeline`, `tool_packs`, `builtin_tools`, `extensions`** —
  shown read-only as JSON. These are not weekly work, a wrong keystroke here breaks the
  bot in ways a form cannot explain, and they are exactly `NEVER_IN_SCOPE`/`BLACKLIST_FIELDS`
  for good reason. The API keeps them; the UI does not offer them.
* **Collections/documents, eval cases/suites/rubrics, packages (import/export), the model
  catalogue and probe, turn traces.** Real surfaces, not day-to-day, and each is its own
  screen's worth of work. `curl` still reaches them.
* **A rooms list.** There is no admin route that lists rooms, and adding one is a host
  concern in a `kernos` router. The binding form takes a space id; chiatienan's space id
  *is* the room id, and the copy says so.

## Backend gaps (the only new backend code)

Two routes, both generic enough to live in `kernos`:

1. `store.list_bindings()` → `GET /api/admin/bindings`. Without it the Overview cannot
   answer "which spaces are bound", which is the one authorisation fact that decides
   whether room members can edit. `get_binding(space_id)` needs an id you already know.
2. `GET /api/admin/profiles/{id}/versions/{v}/diff?against=<v>` → `{paths, diff}`.
   Reuses `kernos.content.gates.changed_paths` and `kernos.osadmin._unified` — the same
   implementation the room panel and the proposal card render, rather than a second
   diff written in TypeScript. Defaults to the previous version; the first version
   reports `paths: []` (the fix in `dc4adcd` — an origin changed nothing).

Nothing else changes on the backend. No new auth, no new permission model.

## Credential handling

`ADMIN_PASSWORD` is a **single shared secret with full power over every business,
profile and version**, and `/admin` is a public URL. Three decisions:

* **`sessionStorage`, not `localStorage`.** Cleared when the tab closes; a "Sign out"
  button clears it now. `localStorage` would leave a full-power secret on the device
  indefinitely.
* **Its own client.** `src/lib/admin-api.ts` is separate from `src/lib/api.ts` so the
  room bearer is never sent to `/api/admin/*` and the admin password is never sent to a
  room route. A test asserts both directions.
* **Never in a URL, never in a log, never rendered.** The service worker already treats
  `/api/*` as network-only, so no admin response is cached.

Known and **not** fixed here: `require_admin` has no rate limiting, so the password is
guessable at network speed. That was already true of `curl` — this UI does not widen the
surface, it just makes the target obvious. Follow-up, not this PR.

## Self-review against the Phase 11 findings

The findings that shaped the room panel apply here too, and the answers differ because
the operator's authority differs:

* **F1 (membership is not permission).** Unchanged: this page is gated on the admin
  password, and the *room* editing path still requires a binding. Turning a binding on
  from this page is the operator granting that, deliberately, with the consequence on
  screen.
* **F2 (a rule's identity is its slug).** The admin publish path already goes through
  `store.publish` and the gates; nothing here bypasses `protected_changes`. Money rules
  are editable *by the operator* — that is the point of the operator — but a source edit
  is `If-Match`-guarded so two operators cannot silently overwrite each other.
* **F6 (publish first, sources second).** Not re-implemented: the UI calls the existing
  routes, which already own that order.
* **F7 (concurrency inside the transaction).** Source edits carry the ETag; publish uses
  the existing route. The UI shows 409/412 as "someone else changed this — reload".
* **F8 (`managed_by` flips to `human`).** The Overview shows `managed_by` per profile,
  and the publish button warns before the *first* human publish of a `boot` profile that
  deploys will stop refreshing it.

## Steps, each with its check

1. `list_bindings` + the two routes → `pytest tests/test_admin_ui_routes.py` (new file:
   bindings list shape, diff of v1 is empty, diff against a chosen version, 404 on a
   version that does not exist).
2. `src/lib/admin-api.ts` → vitest asserts the header split in both directions.
3. `/admin` page + screens → vitest: sign-in gates the tabs, a bad password shows the
   error and does not store, a gate failure renders each failing gate, `managed_by: boot`
   shows the warning, approve/reject call the right route.
4. Full suites (`pytest`, `node --test`, `tsc --noEmit`, `vitest`), then docs: user guide
   part 2 gains the screen, Operations §2 gains "or click it", README's table gains
   nothing (the guides already point there).

No benchmark run: this phase changes no prompt, no skill, no tool manifest, and no code a
money turn executes.
