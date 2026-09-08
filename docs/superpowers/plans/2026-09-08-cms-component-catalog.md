# Phase 13 — the component catalogue, and assembling an agent from it

> Reviewed before implementation; §"Review" at the end records what the review changed
> and why. Findings are cited as **R1…R13** where the body acts on them.

## Why

Phase 12 gave the operator four tabs. The one question an operator actually opens the
screen with — **"what is this agent made of, and how do I change it?"** — none of them
answer:

| Gap | Evidence |
|---|---|
| Tools are invisible, everywhere | There is no `/api/admin/packs` route. `PackRegistry.describe()` (`kernos/packs.py:204`) returns `{id, version, handles_money, draft_kinds, fixtures}` and **never the tools**. No API caller can list the tools a pack provides, their descriptions or their schemas. |
| Plugins are exposed but unused | `GET /api/admin/registry` (`kernos/api/admin.py:140`) returns every plugin with `stage`, `config_schema` and `schema_hash`. `frontend/src/lib/admin-api.ts` has no client for it; nothing renders it. |
| The prompt has no provenance | `_snapshot_sources` (`kernos/content/store.py:242`) builds `spec.skills/rules/templates/prompt.body` from `kn_sources`. Nothing tells an operator which source row produced which part of what the bot runs. |
| Sources can be edited, never added | `components/admin/content.tsx` lists sources and edits the one you pick. There is no *new* and no *delete* — though `PUT …/sources/{kind}/{slug}` already upserts (`store.py:158`) and `DELETE` exists (`store.py:193`). |
| Assembly is API-only | `PATCH /profiles/{id}/versions/{v}` accepts any spec patch, but the UI edits `prompt.body` and nothing else — a deliberate Phase 12 decision ("the API keeps them; the UI does not offer them"). |

This is not a hole in the content model. The design settled the model already:

* §2.7 — the CMS-native list includes *"tool enablement + description overrides + guidelines"*.
* §4.5 — *"What an editor can still not do: write a tool body, change a tool's parameter
  schema, add a stage. Everything else … is content."*
* §5.0 — *"The admin API exposes each plugin's schema so a generic editor can render it;
  the framework does not ship an editor."*

Phase 13 ships the editor, the catalogue it renders from, and the four server-side
guards that make an editor safe to hand an operator (R1–R4).

## Vocabulary: why there is no `ToolContentType`

The request that opened this phase was framed in Optimizely CMS terms: scan the code for
tools and skills, surface them as `ToolContentType` / `SkillContentType` /
`AgentContentType`, extend by subclassing (`PhoenixAgentContentType`).

The instinct is right and the mapping is close, which is worth writing down because it
decides what we build:

| Optimizely | kernos |
|---|---|
| `[ContentType] class ArticlePage : PageData`, scanned by reflection at startup | `BasePack` subclass, `Plugin` object, registered at boot (`BaseKernel.register_packs` / `register_framework_plugins`) |
| `tblContentType` / `tblPropertyDefinition` — the scanner's persisted output | **missing for packs; unexposed for plugins** — this phase |
| `PropertyDefinition` | `Plugin.config_schema`, `PackTool.schema` (JSON Schema) |
| a content instance row | `kn_sources`, `kn_agents`, `kn_collections`, a `spec` in `kn_profile_versions` |
| ContentArea + blocks with per-block settings | `spec.tool_packs[].tools{enabled, description}`, `spec.pipeline[{id, version, config}]` |
| `[AllowedTypes]` | `Business.tool_packs`, `Business.plugins_allowed` |
| draft / publish / approval sequence | `kn_profile_versions` + `PublishGates` + `kn_change_proposals` |

So the code classes **are** the content types and the `kn_` rows **are** the instances.
Adding a `*ContentType` layer would be a third name for both. Three consequences,
decided here:

1. **No new class hierarchy.** The catalogue is the *scanner's output*, computed from the
   registries that already exist — the `tblContentType` step, not a new model.
2. **No `PhoenixAgentContentType`.** In Optimizely, inheritance adds *properties*.
   `phoenix` and `dealer` differ by **values** of the same properties, and are already two
   `kn_agents` rows over two profiles. Subclassing per agent is a page type per page. The
   extension axes stay compositional and already exist: new tools → a **pack**
   (`class LunchLedgerPack(BasePack)`), new turn behaviour → a **plugin** (`id@version`),
   new document type → a **collection** (authored in the CMS), new prompt/skill/rule → a
   **source row**. An agent-specific field with nowhere else to live is `settings` /
   `extensions`.
3. **"Create a tool in the CMS" stays false for pack tools, and true for collections.**
   `PackTool.execute` is Python; §4.4 rejected code-in-the-database for money reasons.
   What the CMS creates is a *reference with overrides* — `apply_tool_overrides`
   (`packs.py:220`). The one place where a person really does create a component in the
   CMS and the agent gains tools is `Collection` (`kernos/data/pack.py`): a JSON Schema in
   the safe subset generates `{slug}_find|_upsert|_delete`. It has no screen. 13.4 gives
   it one; inventing a *second* declarative tool kind is deliberately not in this phase.

## Scope

| Sub-phase | What |
|---|---|
| **13.0** | The four server-side guards the editor needs: boot no longer publishes human sources ungated (R1), `delete_source` refuses a money rule (R2), `put_source` validates the slug (R4), `update_draft` refuses an agent-authored draft (R5) |
| **13.1** | `GET /api/admin/catalogue/components` — packs with **every tool** (name, description, schema, flags), dynamic and framework-managed markers, builtin-tool names, source kinds |
| **13.2** | A **Components** tab: the catalogue, what a chosen space has *as configured*, and prompt provenance with a drift marker |
| **13.3** | Assembly on a **draft**: packs on/off, per-tool enable + description override, builtin tools, model, caps; **new/delete source** |
| **13.4** | A **Collections** editor: create/edit/delete a collection and see the three tools it generates |

Out, on purpose:

* **A new declarative tool kind** (HTTP call, templated reply). Needs a table, a sandbox
  story and its own gate; §4.4 parked the neighbouring idea (sandboxed expressions for
  `ValidationRule`) for the same reasons. 13.4 proves the pattern with the one that exists.
* **Editing `pipeline` and `extensions` in the UI.** Shown read-only with schemas. A stage
  list is not weekly work and a wrong entry breaks a turn in a way no form explains.
* **`prompt_guidelines` / `execution_mode` per tool** (design §2.3). `apply_tool_overrides`
  supports `enabled` and `description` only; two more keys means touching the sidecar's
  manifest builder. Its own phase.
* **Extending gate 2 to pack tools.** Considered and **rejected on review** — see R6; it
  gates a non-hazard and would fire on read-only tools.
* **Persisting the catalogue** (a real `tblContentType`). Gate 1 already refuses a spec
  naming an unknown pack, an unknown plugin, or an override for a tool the pack does not
  have (`gates.py:181-193`), and the registry refuses a changed `config_schema` under a
  known `id@version` (`registry.py:56`). A table buys an error we already raise loudly.
* **A route to add a model to the catalogue.** `upsert_model` is called only from
  `boot.py:73`, and `POST /catalogue/models/{id}/probe` answers 501 in production because
  the probe lives in dev-only `bench` (`app/modelprobe.py`). The 13.3 model picker is
  therefore a **select over the rows boot seeded**, and adding a model stays env work (R3).

## Migration

**No new table and no new column anywhere in the phase.** `tests/test_prod_migration.py`
asserts `len(kn) == 16`; it must still pass **unchanged**, and that is this phase's
migration test. The catalogue is computed per request from `kernel.packs`,
`kernel.registry` and `BaseKernel.null_tool_context()` (`app/kernel.py:81`, which exists
for exactly this); nothing is stored. Everything else uses tables that shipped earlier.

The migration risks are behavioural, and 13.0 exists because of them.

**R1 — boot republishes human source edits with the gates bypassed.** On every start,
for a `managed_by == "boot"` profile, `ensure_seeded` calls
`create_draft(pid, base_spec=stored)` — with the default `snapshot=True` — and publishes
the result with `bypass_gates=True` when it differs (`kernos/content/boot.py:53-63`).
`_snapshot_sources` pulls **every** source row of the business, not just boot's. So a
source a human edits in the admin screen and never publishes **goes live on the next
deploy, ungated**. That is true today (Phase 12 shipped the source editor) and 13.3 would
widen it with create and delete.

The fix is one keyword and it matches the documented contract — the developer guide
already says boot *"re-publishes **from code** only while the profile is still
`managed_by='boot'`"* (`docs/developer-guide.md:264`), and `boot.py`'s own docstring says
the same. Today's `snapshot=True` republishes from code **plus whatever anyone edited**.
13.0 passes `snapshot=False`. A boot-managed profile then tracks code, full stop; a human
source edit reaches the bot the way every other content change does — a draft and a
publish through the gates, or a room edit (`app/roomcms.py:246`, which already uses
`snapshot=False` + `apply_source_changes` and flips `managed_by` to human).

*Verify:* a fresh database seeds to a byte-identical version 1 (skills and rules are
already in the code spec — `default_profile.py:33-38` — so the snapshot was redundant
there); a human-edited source on a boot-managed profile is **not** published by the next
`ensure_seeded`; a changed code spec still republishes. If ordering differs between
`_read_skills()` and the slug order `_snapshot_sources` imposed, the first boot after
deploy republishes once with identical content — assert that it is content-identical.

**R2 — deleting a money rule is unguarded server-side.** `store.delete_source`
(`store.py:193`) and its route check nothing but `If-Match`. `money_slugs` /
`protected_changes` (`kernos/content/sources.py`) are consulted only by the room editor
and the proposal path, and `PublishGates` never checks that a money rule survived (gate 5
is agent-only). A `window.confirm` is not a guard. 13.0 puts the refusal in
`delete_source`: a slug protected by the published spec of any profile in the business is
refused with the slug named.

**R5 — a human can PATCH an agent's pending-proposal draft.** `update_draft`
(`store.py:328`) checks `status == "draft"` and nothing about the actor. 13.3's form would
offer it, and approving afterwards publishes the human's additions under the agent's
name. 13.0 refuses `update_draft` on a version whose `actor` starts with `agent:`.

**The boot one-way door.** `store.publish` flips `managed_by` from `boot` to `human`
permanently for anything but boot seeding. `content.tsx:155` already confirms before
publishing a boot profile; every new publish button in 13.3/13.4 carries the same confirm.

**Delete-a-source is a spec change one draft later.** `_snapshot_sources` replaces *per
kind*, so deleting a skill row removes it from the **next snapshotting draft**, not from
what is published now. The confirm says so.

**Gate 2 already fires on every lunch publish** (R7). `PI_BUILTIN_TOOLS` defaults to
`read,write,bash` (`app/config.py:99`) and lunch is money-handling, so `override_reason`
is already mandatory there. 13.3's builtin-tool warning must say "this is already the
case", not present it as new.

**The PWA cache is not a risk.** `public/sw.js` serves navigations network-first and
`/_next/static/*` cache-first on content-hashed URLs, and never touches `/api/*`. A new
admin route ships as new hashed chunks; **no `CACHE` bump.**

Deploy order: 13.0 + 13.1 (backend, additive and unused until the UI ships), then the
frontend. No downtime step, no backfill, no env var.

## 13.0 — the four guards

1. `kernos/content/boot.py` — `create_draft(..., snapshot=False)` in both branches (R1).
2. `kernos/content/store.py` — `delete_source` refuses a protected money slug (R2).
3. `kernos/content/store.py` — `put_source` validates the slug against the shape the rest
   of the content plane already uses (`kernos/data/schema.py:SLUG_RE`, `[a-z][a-z0-9_]…`,
   widened for the `-` and `.` the seeded slugs use: `money-safety`, `SKILL.md`-derived
   names). Today it validates `kind` only and `Source.slug` is a bare `String(80)` (R4).
4. `kernos/content/store.py` — `update_draft` refuses a version whose `actor` is `agent:*`
   (R5), with the message pointing at the proposal flow.

*Verify:* `tests/kernos/test_content_store.py` and `tests/test_boot_seed.py`. Each guard
gets a test that fails before the change.

## 13.1 — `GET /api/admin/catalogue/components`

Named under `/catalogue/` beside the existing `/catalogue/models`, because "catalogue" is
the design's own word for *code, not content* (§2.3). It carries **only what is missing**
(R11): the UI calls `/registry`, `/eval/graders` and `/catalogue/models` for the rest.

```jsonc
{
  "packs": [{
    "id": "lunch_ledger", "version": "3", "handles_money": true, "evidence": true,
    "draft_kinds": ["expense_draft", "payment_draft"],
    "tools": [{"name": "propose_meal", "description": "…", "schema": {…},
               "money": true, "commit": true, "cancel": false}],
    "dynamic": false,            // tools depend on the turn's agent or space
    "framework_managed": false,  // the kernel adds this pack itself — never a checkbox
    "error": null                // a pack whose tools() raised, degraded not fatal
  }],
  "builtin_tools": ["read", "write", "edit", "bash", "grep", "find", "ls"],
  "risky_builtin_tools": ["bash", "write", "edit"],
  "source_kinds": ["prompt", "rule", "skill", "template"]
}
```

Three things the review corrected:

* **`dynamic` is not "collections only"** (R8). Three packs return `{}` under
  `null_tool_context()` because their tools depend on the turn: `collections` (space),
  `os_admin` (`osadmin.py:131` — no `ctx.agent` → `{}`, and the set depends on
  `capabilities.cms`), `delegation` (`agents.py:82` — no `delegates_to` → `{}`, and the
  names are `ask_<sub-slug>`). All three report `dynamic: true`. For `os_admin` the
  catalogue synthesises an agent granted all four verbs so the descriptions and schemas
  are real (`_Tools.spec()` needs an agent, not a database); `delegation` and
  `collections` report names only, from `all_tool_names` where there is one.
* **`delegation` is `framework_managed`** (R9). `kernos/plugins/run.py:37` appends
  `{"pack": "delegation"}` whenever the agent delegates, so a profile that *also* lists it
  makes `compose_tools` raise `PackError("provided by two enabled packs")` — and gate 1
  does not catch it, because `static_tool_names` returns an empty set for it. The 13.3
  checkbox list excludes framework-managed packs. (`Business.tool_packs` is `[]` for both
  seeded businesses, so "restricted to `business.tool_packs`" restricts nothing today.)
* **`risky_builtin_tools` comes from `kernel.gates._money_tools`**, not the module
  constant, because `PublishGates` accepts an override (R12). `builtin_tools` is the one
  row of the catalogue that is **not** derived from code: nothing in `kernos` or the
  sidecar enumerates pi's builtins (`agent_sidecar/session.js:158` passes names straight
  through), so it is a declared constant in `catalogue()` and says so in its docstring.

Built by a new `BaseKernel.catalogue()` in `kernos/host.py` so a second host gets it free,
with the `try/except`-per-pack discipline of `reserved_tool_names()` (`host.py:167`) — one
bad pack degrades to `{"tools": [], "error": …}` rather than 500ing the request.

*Verify:* `tests/kernos/test_catalogue.py` — every registered pack appears; every tool of a
**static** pack carries a non-empty schema; `os_admin` reports its tools with schemas via
the synthesised agent and `dynamic: true`; `delegation` is `framework_managed`; a pack
whose `tools()` raises degrades. One route test in `tests/test_admin_api.py`.

## 13.2 — the Components tab (read-only)

A fifth tab. Three sections:

1. **Packs and tools** — every pack, expandable to its tools with description and schema.
   For the space chosen in the header, each tool is badged **as configured** — on, off, or
   overridden — against `GET /spaces/{id}/resolved`. The wording is deliberate (R10):
   `/resolved` returns `spec`, `engine_spec` and `pipeline.describe()` but **no tool
   manifest**, so for the three dynamic packs the spec cannot say what a turn really gets
   (os_admin depends on the agent's `capabilities.cms`, delegation on `delegates_to`).
   Those packs are labelled "per turn — depends on the agent" instead of badged.
2. **Pipeline and plugins** — the resolved pipeline by stage, and `/registry`'s plugins
   with their config schema.
3. **Prompt provenance** — the resolved `prompt.body`, `rules` and `skills`, each labelled
   with the `kn_sources` row it came from (`kind/slug`, `updated_by`, `updated_at`), or
   "no source (spec only)". Built client-side by the join `_snapshot_sources` does
   server-side, over `GET /businesses/{id}/sources`; the business id comes from
   `resolution.agent.business_id`. Where the snapshotted body differs from the source row
   as it stands now, the label says **"source changed since this version"** (R2b) — without
   that marker the provenance lies.

*Verify:* the tab lists a tool name from a mocked catalogue; badges an enabled and a
disabled tool; labels a skill with its source; shows the drift marker when bodies differ;
labels a dynamic pack "per turn".

## 13.3 — assembly

**Sources.** `New source` (kind + slug + title + body + frontmatter) and `Delete`, on the
existing routes with `If-Match`. The server validates the slug (13.0) and the server
refuses a money-rule delete (13.0); the UI shows both messages. Two things the form must
say, because the store's behaviour is surprising (R4): a `prompt`-kind source is only read
at slug `system` (`store.py:260`), so the kind picker pins it; and a `template`-kind source
reaches `spec.templates`, which nothing but package export reads today (`to_engine_spec`
omits them) — the form says so rather than implying it changes the bot.

**A draft version** gains, beside the prompt-body editor:

* pack checkboxes from the catalogue, **excluding `framework_managed`** (R9),
* per-tool `enabled` + `description` override for the enabled packs' tools,
* `builtin_tools` checkboxes, with the warning that gate 2 already demands an
  `override_reason` here (R7),
* `models.text` / `models.vision` as a **select over `/catalogue/models`** with each row's
  probe state, because gate 3 refuses a model whose probe is missing or stale, and there
  is no way to add a model from the UI (R3),
* `caps.max_tools` / `caps.max_seconds`.

All of it is one `PATCH /profiles/{id}/versions/{v}`. `deep_merge` (`store.py:56`) **merges
dicts and replaces lists**, so the contract the form follows is explicit (R13): list fields
(`tool_packs`, `builtin_tools`) are always sent **complete**, carrying every existing
per-tool override; dict fields (`models`, `caps`) may be partial; `models.vision: null`
clears vision. `ToolPackRef.tools` values are sent as booleans and strings only —
`apply_tool_overrides` tests `enabled is False`, so the string `"false"` would *enable*.

The form is not offered on a draft whose `actor` is `agent:*`; the server refuses it too
(13.0, R5).

*Verify:* a test that ticking a pack sends the complete `tool_packs` array; that a draft
authored by an agent is refused; that gate failures render. Backend: the 13.0 guards.

## 13.4 — the Collections editor

A section on the Components tab: the business's collections with name, description, key,
indexed fields, schema, and **the three tool names each generates**. Create, edit and
delete through the existing routes. `SchemaError` from the safe-subset check
(`kernos/data/schema.py`) renders as-is — it already names the path and the keyword.

The confirm text the review corrected (R14): `delete_collection` (`data/store.py:105`)
**refuses** while documents exist (`409: has N document(s); delete them first`) — nothing
is orphaned, and that is what the UI should say. The real hazard is the other way round:
`put_collection` changes the generated tools of **every profile enabling `collections`
immediately**, outside any draft, gate or probe (design §5.3 accepts this). That is the one
place in this phase where a save reaches a live bot without a publish, and the editor says
so above the save button.

*Verify:* a schema outside the subset shows the server's error rather than saving; the
generated tool names are displayed; the delete conflict renders as the server's message.

## Order

13.0 → 13.1 → 13.2 → 13.3 → 13.4. 13.0 and 13.1 are backend-only and deployable alone;
13.2 is read-only; 13.3 is the first that can change what a bot runs, and only through the
gates; 13.4 touches a surface no room uses until a collection exists.

## Review

Reviewed against the code before implementation. What it changed:

| # | Finding | Action |
|---|---|---|
| R1 | boot re-snapshots **all** sources and publishes `bypass_gates=True` — a human source edit goes live ungated on the next deploy | 13.0: `snapshot=False`; the phase's largest correction |
| R2 | `delete_source` has no money-rule guard; gates never check a money rule survived | 13.0: refuse server-side |
| R2b | provenance matched by slug can differ from the snapshotted body | 13.2: drift marker |
| R3 | no route adds a model; the probe is 501 in prod | picker is a select over seeded rows; adding a model stays env work |
| R4 | `put_source` validates `kind` only; `prompt` sources other than `system` are ignored; `template` is inert | 13.0 validates the slug; the form says both |
| R5 | `update_draft` lets a human patch an agent's proposal draft | 13.0: refuse `agent:*` drafts |
| R6 | the gate-2 extension gates a non-hazard: `commit_tools` defaults to `money_tools`, which includes read-only tools (`member_statement`, `get_period_summary`, `pick_random`), and `FabricatedCommit._from_packs` (`app/plugins/validate.py:86`) reads `commit_tools` from the **pack**, not the overridden set — so disabling a tool cannot create a forged-commit hole | **cut** |
| R7 | gate 2 already fires on every lunch publish | wording, not a change |
| R8 | `dynamic` is not collections-only; os_admin and delegation yield `{}` under the null context | 13.1: three dynamic packs, synthesised agent for os_admin |
| R9 | `delegation` is auto-appended by `run.py`; listing it too raises `PackError` and gate 1 misses it | 13.1 marks it `framework_managed`; 13.3 excludes it |
| R10 | `/resolved` carries no tool manifest, so 13.2 cannot claim "actually on" | badge says "as configured"; dynamic packs labelled per-turn |
| R11 | the catalogue duplicated `/registry`, `/eval/graders`, `/catalogue/models` | payload trimmed to what is missing |
| R12 | `risky_builtin_tools` should come from `gates._money_tools`; nothing enumerates pi's builtins | both stated |
| R13 | `deep_merge` replaces lists, merges dicts | the PATCH contract is written out |
| R14 | 13.4's confirm was wrong: delete refuses while documents exist; the live hazard is `put_collection` | rewritten |

Corrections to the first draft's own citations: `MONEY_TOOLS` is `gates.py:20`;
`GateFailure` is `admin-api.ts:66`; `Registry.load_entry_points` exists but this host does
not call it (plugins register through `register_framework_plugins`), so it is not cited as
the boot mechanism.
