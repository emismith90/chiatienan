# Phase 13 — the component catalogue, and assembling an agent from it

## Why

Phase 12 gave the operator four tabs. The one question an operator actually opens the
screen with — **"what is this agent made of, and how do I change it?"** — none of them
answer:

| Gap | Evidence |
|---|---|
| Tools are invisible, everywhere | There is no `/api/admin/packs` route. `PackRegistry.describe()` (`kernos/packs.py:204`) returns `{id, version, handles_money, draft_kinds, fixtures}` and **never the tools**. No API caller — human or agent — can list the tools a pack provides, their descriptions or their schemas. |
| Plugins are exposed but unused | `GET /api/admin/registry` (`kernos/api/admin.py:140`) returns every plugin with `stage`, `config_schema` and `schema_hash`. `frontend/src/lib/admin-api.ts` has no client for it; nothing renders it. |
| The prompt has no provenance | `_snapshot_sources` (`kernos/content/store.py:242`) builds `spec.skills/rules/templates/prompt.body` from `kn_sources`, then the prompt plugins assemble the system prompt. Nothing tells an operator which source row produced which part of what the bot runs. |
| Sources can be edited, never added | `components/admin/content.tsx` lists sources and edits the one you pick. There is no *new* and no *delete* — even though `PUT …/sources/{kind}/{slug}` already upserts (`store.py:158`) and `DELETE` exists (`store.py:193`). |
| Assembly is API-only | `PATCH /profiles/{id}/versions/{v}` accepts any spec patch, but the UI edits `prompt.body` and nothing else — a deliberate Phase 12 decision ("the API keeps them; the UI does not offer them"). |

This is not a hole in the content model. The design settled the model already:

* §2.7 — the CMS-native list includes *"tool enablement + description overrides + guidelines"*.
* §4.5 — *"What an editor can still not do: write a tool body, change a tool's parameter
  schema, add a stage. Everything else … is content."*
* §5.0 — *"The admin API exposes each plugin's schema so a generic editor can render it;
  the framework does not ship an editor."*

Phase 13 ships the editor, and the catalogue it renders from.

## Vocabulary: why there is no `ToolContentType`

The request that opened this phase was framed in Optimizely CMS terms: scan the code for
tools and skills, surface them as `ToolContentType` / `SkillContentType` /
`AgentContentType`, extend by subclassing (`PhoenixAgentContentType`).

The instinct is right and the mapping is close, which is worth writing down because it
decides what we build:

| Optimizely | kernos |
|---|---|
| `[ContentType] class ArticlePage : PageData`, scanned by reflection at startup | `BasePack` subclass, `Plugin` object, registered at boot (`Registry.load_entry_points`, `BaseKernel.register_packs`) |
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
   What the CMS creates is a *reference with overrides* — which `apply_tool_overrides`
   (`packs.py:220`) already implements. The one place where a person really does create a
   component in the CMS and the agent gains tools is `Collection` (`kernos/data/pack.py`):
   a JSON Schema in the safe subset generates `{slug}_find|_upsert|_delete`. It has no
   screen. Phase 13.4 gives it one; inventing a *second* declarative tool kind is
   deliberately not in this phase (see "Out").

## Scope

In:

| Sub-phase | What | Why it earns the work |
|---|---|---|
| **13.1** | `GET /api/admin/catalogue` — packs with **every tool** (name, description, schema, flags), plugins by stage with config schemas, graders, builtin tools, source kinds, draft kinds | the missing scanner output; nothing else in the phase is possible without it |
| **13.2** | A **Components** tab: the catalogue, and for a chosen space which of it is actually on; plus prompt provenance (resolved prompt → the source row each part came from) | answers "what is this agent made of" — read-only, cannot break a live bot |
| **13.3** | Assembly on a **draft**: packs on/off, per-tool enable + description override, builtin tools, model from the catalogue, caps; **new/delete source**; gate 2 extended to pack money tools | the actual "assemble it in the CMS" ask |
| **13.4** | A **Collections** editor: create/edit/delete a collection, see the three tools it generates | the one genuine "create a component in the CMS, get tools" path, already built server-side |

Out, on purpose:

* **A new declarative tool kind** (HTTP call, templated reply). It needs a table, a
  sandbox story and its own gate; §4.4 already parked the neighbouring idea (sandboxed
  expressions for `ValidationRule`) for the same reasons. 13.4 proves the pattern with the
  one that exists.
* **Editing `pipeline` and `extensions` in the UI.** Shown read-only with schemas, as
  today. A stage list is not weekly work and a wrong entry breaks a turn in a way no form
  explains. The API keeps them.
* **`prompt_guidelines` / `execution_mode` per tool** (design §2.3). `apply_tool_overrides`
  supports `enabled` and `description` only; adding two more override keys means touching
  the sidecar's manifest builder. Its own phase.
* **Persisting the catalogue** (a real `tblContentType`). Nothing needs it: gate 1 already
  refuses a spec naming an unknown pack, an unknown plugin, or a per-tool override for a
  tool the pack does not have (`gates.py:181-193`), and the registry refuses a changed
  `config_schema` under a known `id@version` (`registry/registry.py:56`). A table would add
  a migration and a drift-detection problem to buy an error we already raise loudly.

## Migration

**Nothing in 13.1–13.4 adds a table or a column.** That is a design constraint of this
phase, not an accident:

* The catalogue is **computed** at request time from `kernel.packs`, `kernel.registry`,
  `kernel.graders` and `BaseKernel.null_tool_context()` (`app/kernel.py:81`, which exists
  for exactly this). Nothing is stored.
* New/delete source, assembly and collections all use routes and tables that shipped in
  earlier phases (`kn_sources`, `kn_profile_versions`, `kn_collections`, `kn_documents`).
* `tests/test_prod_migration.py` asserts `len(kn) == 16`. It must still pass **unchanged** —
  that is the phase's migration test. If a sub-phase wants a table, it leaves the phase.
* `kernos.content.schema.bind()` is additive-only (`create_all` + `sync_additive_columns`)
  and idempotent, so even a later table lands without a script — but this phase does not
  exercise that.

The four migration risks that are **not** about schema:

1. **Gate 2 becomes stricter (13.3).** Today `MONEY_TOOLS = {"bash", "write", "edit"}`
   (`gates.py:22`) — builtins only. Disabling `propose_meal` or `settle_period` (a pack's
   `commit_tools`) is ungated, which is invisible today only because no UI offers it and
   13.3 puts it one checkbox away. Extending gate 2 to refuse a publish that disables a
   `commit_tools` member without an `override_reason` **can only reject a publish, never a
   resolve** — no running bot is affected, and gates do not run on the read path. Verified:
   no seeded profile overrides any tool (`default_profile.py:71`, `poker_profile.py:57`,
   `steward_profile.py:78` all use bare `ToolPackRef(pack=…)`), so no existing published
   spec becomes unpublishable. A rollback also skips nothing here: `publish` still takes
   `override_reason`.
2. **The boot one-way door.** `store.publish` flips `managed_by` from `boot` to `human`
   permanently for anything but boot seeding, after which a deploy no longer refreshes the
   prompt, skills and rules. `content.tsx` already warns before publishing a boot profile;
   **every new publish button in 13.3/13.4 carries the same confirm**, and the Components
   tab says which profiles are still boot-managed.
3. **Delete-a-source is a spec change one draft later.** `_snapshot_sources` replaces
   *per kind*, so deleting a skill row removes that skill from the **next snapshotting
   draft**, not from what is published now. The delete confirm must say that in words, and
   must refuse a rule the published spec tags `money` (`sources.money_slugs`) rather than
   let it be deleted and silently reappear as an untagged rewrite (the Phase 11 F2 lesson).
4. **The PWA cache is not a risk here.** `public/sw.js` serves navigations network-first
   and `/_next/static/*` cache-first on content-hashed URLs, and never touches `/api/*`.
   A new admin route ships as new hashed chunks; **no `CACHE` bump is needed.**

Deploy order: backend first (the catalogue route is additive and unused until the UI
ships), then the frontend. No downtime step, no data backfill, no env var.

## 13.1 — `GET /api/admin/catalogue`

One payload, the scanner's output:

```jsonc
{
  "packs": [{
    "id": "lunch_ledger", "version": "3", "handles_money": true, "evidence": true,
    "draft_kinds": ["expense_draft", "payment_draft"],
    "tools": [{"name": "propose_meal", "description": "…", "schema": {…},
               "money": true, "commit": true, "cancel": false}],
    "dynamic": false            // true = tools depend on the space (collections)
  }],
  "plugins": [{"id": "…", "version": "1", "stage": "prompt", "config_schema": {…},
               "schema_hash": "…", "handles_money": false}],
  "graders": ["…"], "builtin_tools": ["read", "write", "bash", "edit", "grep", "find", "ls"],
  "risky_builtin_tools": ["bash", "write", "edit"],
  "source_kinds": ["prompt", "rule", "skill", "template"],
  "model_ids": ["…"]           // from kn_model_catalogue, with probe state
}
```

* Tool rows come from `pack.tools(kernel.null_tool_context())` via `PackTool.manifest()`,
  with `money`/`commit`/`cancel` from the pack's `money_tools` / `commit_tools` /
  `cancel_tools`. A pack whose tools need a real space (`collections`) reports
  `dynamic: true` and lists what `all_tool_names` gives, or nothing.
* It must never 500 because one pack raises: the same `try/except` discipline as
  `BaseKernel.reserved_tool_names()` (`host.py:167`), one bad pack degrades to
  `{"tools": [], "error": "…"}`.
* Lives in `kernos/api/admin.py` beside `/registry`, built by a new
  `BaseKernel.catalogue()` in `kernos/host.py` so a second host gets it for free.

Verify: `tests/kernos/test_catalogue.py` — every registered pack appears; every tool
carries a non-empty schema; the `os_admin` pack reports its `all_tool_names`; a pack that
raises in `tools()` degrades instead of failing the request. Plus one route test in
`tests/test_admin_api.py`.

## 13.2 — the Components tab (read-only)

A fifth tab. Three sections:

1. **Packs and tools** — every pack, expandable to its tools with description and schema.
   For the space chosen in the header, each tool is badged **on** / **off** / **overridden**
   against `GET /spaces/{id}/resolved`. Money tools are badged.
2. **Pipeline and plugins** — the resolved pipeline by stage, and the catalogue's plugins
   with their config schema, so an operator can see what a stage entry could take.
3. **Prompt provenance** — the resolved `prompt.body` + `rules` + `skills`, each labelled
   with the `kn_sources` row it came from (`kind/slug`, `updated_by`, `updated_at`), and
   "no source (spec only)" where there is none. Built client-side by matching the resolved
   spec against `GET /businesses/{id}/sources`, which is exactly the join
   `_snapshot_sources` does server-side.

Verify: `frontend/src/app/admin/__tests__/` — the tab lists a tool name from a mocked
catalogue, badges an enabled/disabled tool correctly, and labels a skill with its source.

## 13.3 — assembly

**Sources.** `New source` (kind + slug + title + body + frontmatter) and `Delete`, both on
the existing routes with `If-Match`. Slug validated client-side against the same shape the
store accepts. Delete refuses a money-tagged rule with the reason on screen; the confirm
explains the snapshot lag from migration risk 3.

**A draft version** gains, next to the existing prompt-body editor:

* pack checkboxes (from the catalogue, restricted to `business.tool_packs` when non-empty),
* per-tool `enabled` + `description` override, on the tools of the enabled packs,
* `builtin_tools` checkboxes, with a live warning naming the risky ones and what gate 2
  will demand,
* `models.text` / `models.vision` from the catalogue, with the probe state shown, because
  gate 3 refuses a model whose probe is missing or stale,
* `caps.max_tools` / `caps.max_seconds`.

All of it is one `PATCH /profiles/{id}/versions/{v}` of a partial spec — no new route. Gate
failures already render (`GateFailure` in `admin-api.ts:68`); this phase adds no new error
path, it just makes the gates reachable from a form.

**Gate 2 extension** (`kernos/content/gates.py`): for a money-handling profile, a publish
that sets `enabled: false` on a tool in the pack's `commit_tools` fails with
`GateFailure("money", …)` unless an `override_reason` is given. Same shape and same
override as the risky-builtin rule directly above it.

Verify: `tests/kernos/test_gates.py` — disabling `propose_meal` fails without a reason and
passes with one; disabling a non-money tool is unaffected; a spec with no overrides (every
seeded profile) is byte-for-byte unaffected. Frontend: a test that ticking a pack and
saving sends the expected patch.

## 13.4 — the Collections editor

A section on the Components tab: the business's collections, each with name, description,
key, indexed fields, the JSON Schema, and **the three tool names it generates**. Create,
edit and delete through the routes that already exist
(`/businesses/{id}/collections/{slug}`). `SchemaError` from the safe-subset check
(`kernos/data/schema.py`) is rendered as-is — it already names the path and the keyword.
The delete confirm says documents are keyed by `collection_id`, so deleting the definition
orphans the rows.

Verify: a UI test that a schema outside the subset shows the server's error rather than
saving, and that the generated tool names are displayed.

## Order and independence

13.1 → 13.2 → 13.3 → 13.4. Each is deployable alone: 13.1 is an unused additive route,
13.2 is read-only, 13.3 is the first that can change what a bot runs (and only through the
gates), 13.4 touches a surface no room uses until a collection exists.
