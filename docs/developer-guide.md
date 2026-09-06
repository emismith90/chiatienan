# Developer guide — architecture, and how to extend it

For using the thing, see the [user guide](user-guide.md). For shipping it, the
[deploy runsheet](superpowers/plans/2026-09-06-deploy-runbook.md). The reasoning behind
every decision is in the [design spec](superpowers/specs/2026-09-05-agent-cms-design.md)
and the [phase plan](superpowers/plans/2026-09-05-agent-os-framework.md).

---

## 1. The shape of it

There are two things in this repository:

- **`kernos`** — a host-agnostic agent framework. A turn pipeline, a plugin registry, an
  engine boundary, host adapter protocols, and a versioned content plane. It knows nothing
  about lunch, money or rooms.
- **chiatienan** — a lunch-splitting PWA that is `kernos`'s first host. It supplies a
  database, a chat UI, a ledger and a set of tool packs.

```
┌── app/ ──────────── the host: FastAPI, rooms, chat, ledger, drafts, the composition root
│   ┌── packs/ ────── tool packs: lunch_ledger, ledger_tools, poker_ledger
│   │   ┌── ledger_core/ ── money primitives shared by two ledgers
│   │   │   ┌── kernos/ ─── the framework
```

**The layering rule is a test** (`tests/test_layering.py`), not a convention:

| layer | may import |
|---|---|
| `kernos` | `kernos` only |
| `ledger_core` | `kernos`, itself |
| `packs` | `kernos`, `ledger_core`, itself |
| `app` | everything |
| `bench` | everything |
| `examples` | `kernos`, itself — a host with no chiatienan on its path |

Two documented lazy exceptions (`app/modelprobe.py`, `app/evalhost.py` → `bench`). If you
need a new one, it goes in `EXCEPTIONS` with a reason, or the design is wrong.

## 2. A turn

`app/chat.py::run_bot_turn` is the entry point. It resolves what the room runs, builds a
pipeline from that, and runs it.

```
resolve   ── which agent/profile/version does this space run?   (before the pipeline exists)
  ↓
context   ── memory, recent history, image lookback
prompt    ── render the system prompt from the profile's template + sections
model     ── choose the model (vision vs text)
run       ── the engine: the model, its tool calls, the results
   ├ validate_args    ── per tool call, before it executes
   └ validate_result  ── per tool call, on what it returned
render    ── a pack turns the result into a Body or a Draft card
validate  ── reply-level guards (unbacked amounts, forged commits)
persist   ── write the message or the card
after     ── the trace row, summaries
```

`Stage` is an enum in `kernos/kernel/context.py`; `PIPELINE_ORDER` is the sequence.
`SINGLE_OWNER` stages (`model`, `run`, `render`) must have exactly one plugin — the schema
gate refuses otherwise.

A **sub-agent** runs the same pipeline `through=validate`: it never persists, so a
delegated turn cannot make a card. That is deliberate.

`TurnContext` carries everything a stage needs and is passed down the chain. `ctx.extras`
is the scratchpad plugins use to hand each other per-turn state.

## 3. The content plane

The bot's configuration is data, not code.

```
kn_businesses ─┬─ kn_sources          prompt / rule / skill / template, ETag'd
               └─ kn_profiles ── kn_profile_versions   draft → published → superseded → retired
                                   ▲
kn_agents ─────────────────────────┘     kn_space_bindings   space → agent
kn_model_catalogue   kn_audit_log   kn_turn_traces   kn_change_proposals
kn_eval_cases / _suites / _rubrics / _runs      kn_collections / kn_documents
```

`ProfileSpec` (`kernos/content/spec.py`) is the whole configuration of one agent: persona,
prompt, rules, skills, templates, models, caps, builtin tools, memory, pipeline, tool packs,
validation, eval, extensions. It is **frozen** — a resolved profile is shared between turns,
so overrides go through `model_copy`.

Two things to internalise:

**Sources are upstream of versions.** `create_draft(snapshot=True)` pulls the business's
current sources into the new version. A change written only into a version's `spec` is
silently reverted by the next snapshotting draft. Anything that publishes a changed spec
must write the matching sources — use `kernos.content.sources.source_changes`, which both
the proposal path and the room editor go through.

**`stored()` excludes `runtime`.** Paths (`cwd`, `agent_dir`) are boot-layer and injected by
the host at resolve time. Never compare specs without excluding it.

### The five gates

`kernos/content/gates.py::PublishGates.check`:

1. **schema** — the spec validates, the pipeline builds, plugin configs match their
   schemas, every `{{variable}}` is known, a discoverable skill has the `read` builtin.
2. **money safety** — a `handles_money` profile with a risky builtin (`bash`/`write`/`edit`)
   needs an `override_reason`.
3. **probe** — a *changed* model needs a recent successful probe in the catalogue.
4. **eval** — a profile naming `eval.suites` needs a finished run of that exact content
   (`spec_sha`) passing every blocking grader.
5. **reflexivity** — an `agent:` actor may not publish outside its `self_change_scope`, and
   never touches `BLACKLIST_FIELDS`. Does not apply to human actors.

`NEVER_IN_SCOPE` is the blacklist plus `rules[tag=money]`, blocking validators, `persona`,
`meta`, `memory`, `retry`, `templates` — paths no scope can name, however it is written.

## 4. The standing rules

These are why the code looks the way it does. Break them and the tests will tell you.

**Tools own every number.** The model never computes or re-types money. A tool result is
*evidence* on purpose; anything else records a reference only (the `_record` contract in
`kernos/engine/pi/engine.py`) and non-evidence packs set `evidence = False`.
`ledger_core/moneyguard.py::backed_amounts` decides what a reply is allowed to say.

*Known hole, documented:* builtin tools (`bash`, `read`, `write`) are not packs, so their
output counts as backing. A bash-computed amount in prose passes as "backed". Fixing it
changes live validator behaviour and is its own phase (`TODO.md`).

**Zero behaviour change unless deliberately enabled.** A pack only produces tools when a
profile lists it; capabilities default to none; a new agent delegates to nobody.
`tests/test_run_bot_turn_golden.py` (9 byte-identical fixtures) is the regression proof.

**Pre-existing tests are never edited.** Check with:

```bash
comm -12 <(git diff --name-only origin/main -- backend/tests | sort) \
         <(git ls-tree -r origin/main --name-only backend/tests | sort)
```

If a framework change breaks a pre-existing test's duck-typed fake, adapt the framework.

**No secrets, no real names, no bank details** in fixtures or docs.

---

# How to extend it

## Add a tool pack

A pack is a bundle of tools, their rendering, and any cards they create.
`kernos/packs.py::BasePack` is the base; everything is optional.

```python
# packs/my_pack/__init__.py
from kernos.kernel.context import Body
from kernos.packs import BasePack, PackTool, err

class MyPack(BasePack):
    id, version, handles_money = "my_pack", "1", False
    evidence = True                      # False = its results can never back a number

    def tools(self, ctx) -> dict[str, PackTool]:
        def do_thing(args: dict | None) -> dict:
            args = args or {}
            if not args.get("name"):
                return err("Missing name.")
            return {"ok": True, "greeting": f"Hello, {args['name']}!"}

        schema = {"type": "object", "properties": {"name": {"type": "string"}},
                  "required": ["name"], "additionalProperties": False}
        return {"do_thing": PackTool("do_thing", "Greet someone by name.", schema, do_thing)}

    def render(self, result) -> Body | None:
        rec = result.last_result("do_thing")
        return Body(rec["greeting"], None, claimed_by_pack=True) if rec else None
```

Then register it (`app/packs/__init__.py::host_packs`) and **enable it in a profile** —
registration alone does nothing:

```json
{"tool_packs": [{"pack": "my_pack"}]}
```

Other hooks worth knowing: `draft_kinds()` (cards the room confirms), `contributions()`
(debt edges into the ledger), `timeline()`, `graders()`, `eval_cases()`, `content()`
(prompt/skills/rules a business can seed from), `bind(engine)` (tables of its own),
`all_tool_names` (so gate 1 can check names without a live context).

### If your pack writes money

- Set `handles_money = True`.
- Never return a number the model can re-type unless a tool computed it.
- Give it a `DraftKind` so a person confirms before the ledger moves. Set
  `blocks_settlement=False` only if the card is not about money.

## Add a pipeline plugin

```python
from kernos.kernel.context import Stage, TurnContext
from kernos.kernel.plugin import BasePlugin

class MyContext(BasePlugin):
    id, version, stage = "my.context.thing", "1", Stage.context
    config_schema = {"type": "object", "properties": {"limit": {"type": "integer"}},
                     "additionalProperties": False}

    def __init__(self, adapters) -> None:
        self._a = adapters                      # the host's protocols, if you need them

    async def run(self, ctx: TurnContext, config: dict) -> None:
        # TurnContext is mutable and plugins mutate it in place, returning None.
        # `ctx.extras` is the scratchpad stages use to hand each other per-turn state.
        ctx.extras["thing"] = config.get("limit", 10)
```

A plugin may instead return a `Verdict` to warn or block — that is how the validators work.
`ctx.depth > 0` means this is a sub-agent's nested run; several plugins deliberately do
nothing there (folding the room's history mid-turn, for instance).

Register it on the kernel's registry, then name it in a profile's pipeline:

```json
{"pipeline": {"context": [{"id": "my.context.thing", "version": "1", "config": {"limit": 5}}]}}
```

Framework plugins are `kernos.*`; host plugins are `app.*`. Gate 1 validates the config
against `config_schema` at publish time, so a bad config is refused before it can run.

## Add a business

A business is a tenant: its own sources, profiles and agents.

```python
from kernos.content import ensure_seeded

report = ensure_seeded(
    store, business_slug="cafe", business_name="Cafe ledger",
    spec=build_cafe_spec(settings), agent_slug="barista", agent_name="Barista",
    sources=cafe_sources(),
)
```

Call it from the composition root (`app/kernel.py`). It is idempotent: it seeds once, and
re-publishes from code only while the profile is still `managed_by="boot"`. `packs/poker_ledger/`
is the worked example of a second business that shares `ledger_core` but nothing else.

## Add a sub-agent

```python
from kernos.content import ensure_sub_agent

ensure_sub_agent(store, business_id, slug="helper", name="Helper",
                 spec=build_helper_spec(settings),
                 description="What a manager is told this sub does.",
                 capabilities={"cms": ["read"]})
```

Seeded once and never overwritten afterwards, so an operator's capability grant survives a
redeploy. It deliberately does **not** wire any manager's `delegates_to` — that adds a tool
to every space the manager runs, which is an operator's decision. `app/steward_profile.py`
is the worked example.

## Add a collection

A schema-validated document type, defined through the admin API rather than in code:

```bash
curl -X PUT $B/businesses/1/collections/places -H "$A" \
  -d '{"schema": {"type": "object", "properties": {"name": {"type": "string"}}, ...}}'
```

A profile that enables the `collections` pack gets `places_find` / `places_upsert` /
`places_delete` generated from it. The schema must stay in the sidecar-safe JSON Schema
subset (`kernos/data/`).

## Add an engine

`kernos/engine/base.py::Engine` is the boundary — the thing that actually talks to a model.

```python
class MyEngine:
    async def run(self, spec, *, turn_id, message, images, tools, call_tool, emit) -> TurnResult:
        ...
```

`PiEngine` (over the Node sidecar) is the production one; `ScriptedEngine`
(`kernos/engine/fake.py`) replays a script and is what most tests use. Honour the `_record`
contract: when a tool result carries `_record`, record *that* and send the payload without
it.

## Add a host

`kernos.host.BaseKernel` is the framework's composition root. A host subclasses it and
supplies four hooks: `on_packs_registered`, `null_tool_context`, `sub_tool_context`,
`eval_runner_argv`, plus a `tool_context_factory` and the ten adapter protocols
(`kernos/adapters/protocols.py`: history, memory, knowledge, events, messages, cards,
principals, traces, completion, clock).

`examples/minimal_host/host.py` is ~120 lines and is a complete second host — its test
installs a `sys.meta_path` guard that raises on any import of `app`, `packs`,
`ledger_core` or `bench`, so it cannot accidentally depend on chiatienan.

---

## Testing

```bash
cd backend && .venv/bin/python -m pytest tests -q        # 1309 passed, 1 skipped
cd agent_sidecar && node --test                           # 69/69
cd ../../frontend && npx tsc --noEmit && npx vitest run    # 301 passed
```

The ones that matter most when you change the framework:

| test | what it protects |
|---|---|
| `test_run_bot_turn_golden.py` | 9 byte-identical replies — the zero-behaviour-change guarantee |
| `test_layering.py` | the import rules above |
| `test_tools_manifest.py` | the 19 legacy tools, in order |
| `test_prod_migration.py` | booting over a production-shaped database |
| `test_room_cms.py` | every guard on the room editor, named after the review finding it answers |

**The benchmark** is the other half — the golden fixtures use a fake engine, this uses the
real model:

```bash
.venv/bin/python -m bench.run --corpus typical --engine pi --repeat 3 --out /tmp/now.json
.venv/bin/python -m bench.report --compare bench/results/pi-typical-phase10-2026-09-06.json /tmp/now.json
```

Needs `OPEN_ROUTER_KEY`. Ship criterion: no case down more than 1/3 on `tool_selection` or
`ledger_state`. Run it whenever you change what the model sees — the prompt, the skills, the
tool manifest — and whenever you touch code a money turn executes.

## How work is done here

Every phase in this repository followed the same loop, and it is worth keeping:

1. Write the plan section: facts *verified from the code*, decisions with their reasoning,
   tasks each with a proof list.
2. **Review gate** — a second model reviews the plan adversarially before any code is
   written. Findings go in the plan as a table with a disposition per row.
3. Fold the dispositions into the tasks, then implement with the tests named in the proofs.
4. Update the design "as built", the README, and the plan's state of play.

The gate has repeatedly caught things that would have shipped: an unauthenticated path to
editing the production bot, a money rule that could be overwritten by slug collision, a
"rollback" that quietly rewrote the history it was supposed to preserve. It is not
ceremony.
