"""Publish gates (design §9; review findings 3, 4, 7).

Numbering follows the design: 1 schema, 2 money-safety, 3 model probe, 4 eval
(a hook, wired in Phase 4), 5 reflexivity. There is no actor-based bypass — the
only way around the gates is ``ContentStore.publish(bypass_gates=True)``, which
boot seeding alone uses.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from pydantic import ValidationError

from kernos.content.spec import ProfileSpec
from kernos.registry.registry import ConfigError, Registry, RegistryError
from kernos.template import ALLOWED_VARS, validate as validate_template

MONEY_TOOLS = frozenset({"bash", "write", "edit"})

#: Spec paths an agent may never publish a change to (design §8.3 + finding 3).
BLACKLIST_FIELDS = ("builtin_tools", "models", "tool_packs", "pipeline", "eval",
                    "extensions", "settings", "runtime", "caps")


@dataclass(frozen=True)
class GateFailure:
    gate: str
    message: str

    def as_tuple(self) -> tuple[str, str]:
        return self.gate, self.message


class _UtcClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def _dump(spec: ProfileSpec | dict | None) -> dict | None:
    if spec is None:
        return None
    return spec.model_dump() if isinstance(spec, ProfileSpec) else dict(spec)


def blacklisted_changes(previous: ProfileSpec | dict | None, spec: ProfileSpec | dict) -> list[str]:
    """Paths of ``spec`` that differ from ``previous`` and are off-limits to an agent.

    A missing ``previous`` means everything blacklisted that is non-default counts —
    an agent cannot create a profile from nothing either.
    """
    # both sides as stored (no `runtime`): a resolved ProfileSpec carries the host's runtime,
    # a stored dict never does, and that difference is not a change an agent made
    new, old = _stored(spec), _stored(previous)
    changed: list[str] = []
    for field in BLACKLIST_FIELDS:
        if new.get(field) != old.get(field):
            changed.append(field)
    def fenced(v: dict) -> bool:
        # a blocking reply rule, or any rule that guards a tool call — those are the
        # money invariants (plan Task 6.2, review F5)
        return v.get("on_fail") == "block" or v.get("scope") in ("tool_args", "tool_result")

    new_block = [v for v in new.get("validation", []) if fenced(v)]
    old_block = [v for v in old.get("validation", []) if fenced(v)]
    if new_block != old_block:
        changed.append("validation[on_fail=block|scope=tool_*]")
    new_money = [r for r in new.get("rules", []) if "money" in (r.get("tags") or [])]
    old_money = [r for r in old.get("rules", []) if "money" in (r.get("tags") or [])]
    if new_money != old_money:
        changed.append("rules[tag=money]")
    # A removed key is a change too (finding 3): compare presence and value.
    if ("handles_money" in new.get("meta", {})) != ("handles_money" in old.get("meta", {})) \
            or new.get("meta", {}).get("handles_money") != old.get("meta", {}).get("handles_money"):
        changed.append("meta.handles_money")
    return changed


def _stored(spec: ProfileSpec | dict | None) -> dict:
    """A spec as the content plane stores it (no ``runtime``), whichever form came in."""
    if spec is None:
        return {}
    d = spec.stored() if isinstance(spec, ProfileSpec) else dict(spec)
    d.pop("runtime", None)
    return d


def _fenced(v: dict) -> bool:
    return v.get("on_fail") == "block" or v.get("scope") in ("tool_args", "tool_result")


def _money(r: dict) -> bool:
    return "money" in (r.get("tags") or [])


#: Paths an agent can never self-publish, whatever its scope says (Phase 8 review F7, F13).
NEVER_IN_SCOPE = frozenset(BLACKLIST_FIELDS) | frozenset({
    "rules[tag=money]", "validation[on_fail=block|scope=tool_*]", "meta", "retry", "persona", "memory", "templates",
})


def changed_paths(previous: ProfileSpec | dict | None, spec: ProfileSpec | dict) -> list[str]:
    """The coarse paths of ``spec`` that differ from ``previous`` — **every** top-level
    field of ``ProfileSpec`` by name, refined only where the scope vocabulary splits a
    field: ``prompt.body`` / ``prompt.append``, ``rules`` / ``rules[tag=money]``,
    ``validation.warn`` / ``validation[on_fail=block|scope=tool_*]``. A field added to the
    spec later is a changed path by construction (Phase 8 review F7)."""
    new, old = _stored(spec), _stored(previous)
    out: list[str] = []
    for field in ProfileSpec.model_fields:
        if field == "runtime":
            continue
        n, o = new.get(field), old.get(field)
        if field == "prompt":
            n, o = n or {}, o or {}
            if n.get("body") != o.get("body"):
                out.append("prompt.body")
            if n.get("append") != o.get("append"):
                out.append("prompt.append")
        elif field == "rules":
            n, o = n or [], o or []
            if [r for r in n if not _money(r)] != [r for r in o if not _money(r)]:
                out.append("rules")
            if [r for r in n if _money(r)] != [r for r in o if _money(r)]:
                out.append("rules[tag=money]")
        elif field == "validation":
            n, o = n or [], o or []
            if [v for v in n if not _fenced(v)] != [v for v in o if not _fenced(v)]:
                out.append("validation.warn")
            if [v for v in n if _fenced(v)] != [v for v in o if _fenced(v)]:
                out.append("validation[on_fail=block|scope=tool_*]")
        elif n != o:
            out.append(field)
    return out


def outside_scope(previous: ProfileSpec | dict | None, spec: ProfileSpec | dict, scope: list[str]) -> list[str]:
    """The changed paths an agent with ``self_change_scope = scope`` may **not** publish:
    everything changed that the scope does not name, plus :data:`NEVER_IN_SCOPE` even
    when named."""
    allowed = set(scope or []) - NEVER_IN_SCOPE
    return [path for path in changed_paths(previous, spec) if path not in allowed]


class PublishGates:
    def __init__(self, registry: Registry, catalogue: Any, *, clock: Any | None = None,
                 probe_max_age_days: int = 30, money_tools: frozenset[str] = MONEY_TOOLS,
                 eval_gate: Callable[..., list[GateFailure]] | None = None,
                 packs: Any | None = None, tool_names_of: Callable[[Any], set | None] | None = None) -> None:
        self._registry = registry
        self._packs = packs                  # a PackRegistry, or None to skip the pack checks
        self._tool_names_of = tool_names_of  # pack -> its tool names, or None when they are dynamic
        self._catalogue = catalogue          # anything with get_model(model_id) -> dict | None
        self._clock = clock or _UtcClock()
        self._max_age = timedelta(days=probe_max_age_days)
        self._money_tools = money_tools
        self._eval_gate = eval_gate

    def check(self, spec: ProfileSpec | dict, *, previous: ProfileSpec | dict | None,
              actor: str, override_reason: str | None = None,
              skip_probe: bool = False, skip_eval: bool = False,
              profile_id: int | None = None, version_id: int | None = None) -> list[GateFailure]:
        failures: list[GateFailure] = []
        # 1 — schema
        try:
            parsed = spec if isinstance(spec, ProfileSpec) else ProfileSpec.model_validate(spec)
        except ValidationError as exc:
            return [GateFailure("schema", f"spec does not validate: {exc.errors()[0]['msg']} at "
                                          f"/{'/'.join(str(p) for p in exc.errors()[0]['loc'])}")]
        try:
            pipeline = self._registry.build_pipeline(parsed.pipeline_dict())
        except (ConfigError, RegistryError, ValueError) as exc:
            failures.append(GateFailure("schema", str(exc)))
            pipeline = None
        if any(s.delivery == "discoverable" for s in parsed.skills) and "read" not in parsed.builtin_tools:
            failures.append(GateFailure("schema", "a skill with delivery=discoverable needs 'read' in builtin_tools"))
        for where, body in (("prompt.body", parsed.prompt.body), *((f"prompt.append[{i}]", a) for i, a in enumerate(parsed.prompt.append))):
            for problem in validate_template(body, ALLOWED_VARS):
                failures.append(GateFailure("schema", f"{where}: {problem}"))
        if self._packs is not None:
            for ref in parsed.tool_packs:
                try:
                    pack = self._packs.get(ref.pack)
                except Exception as exc:  # noqa: BLE001 — PackError, reported as a schema failure
                    failures.append(GateFailure("schema", str(exc)))
                    continue
                names = self._tool_names_of(pack) if self._tool_names_of is not None else None
                if names is not None:
                    unknown = sorted(set(ref.tools) - set(names))
                    if unknown:
                        failures.append(GateFailure(
                            "schema", f"tool_packs[{ref.pack}].tools names tools the pack does not have: {unknown}"))
            if self._tool_names_of is not None:
                known: set[str] = set()
                dynamic = False
                for ref in parsed.tool_packs:
                    try:
                        names = self._tool_names_of(self._packs.get(ref.pack))
                    except Exception:  # noqa: BLE001 — reported above
                        continue
                    if names is None:
                        dynamic = True
                    else:
                        known |= set(names)
                for rule in parsed.validation:
                    if rule.scope in ("tool_args", "tool_result") and rule.tool and rule.tool not in known and not dynamic:
                        failures.append(GateFailure(
                            "schema", f"validation[{rule.id}] guards tool {rule.tool!r}, which no enabled pack provides"))
        # 2 — money safety
        handles_money = bool(parsed.meta.get("handles_money"))
        if pipeline is not None:
            handles_money = handles_money or any(
                getattr(plugin, "handles_money", False)
                for entries in pipeline._stages.values() for plugin, _ in entries)
        if self._packs is not None:
            for ref in parsed.tool_packs:
                try:
                    handles_money = handles_money or bool(getattr(self._packs.get(ref.pack), "handles_money", False))
                except Exception:  # noqa: BLE001 — an unknown pack is already a schema failure
                    pass
        risky = sorted(set(parsed.builtin_tools) & self._money_tools)
        if handles_money and risky and not override_reason:
            failures.append(GateFailure(
                "money", f"a money-handling profile enables {risky}; publish needs an override_reason"))
        # 3 — model probe, for models that changed
        if not skip_probe:
            prev = _dump(previous) or {}
            prev_models = prev.get("models", {})
            for key in ("text", "vision"):
                model_id = getattr(parsed.models, key)
                if not model_id or model_id == prev_models.get(key):
                    continue
                failure = self._probe_failure(model_id)
                if failure:
                    failures.append(GateFailure("probe", f"models.{key}={model_id!r}: {failure}"))
        # 4 — eval: `eval_gate(spec, *, profile_id, version_id)` (kernos.eval.gate); a
        # rollback skips it — the version passed when it was published (review F8)
        if self._eval_gate is not None and not skip_eval:
            failures.extend(self._eval_gate(parsed, profile_id=profile_id, version_id=version_id))
        # 5 — reflexivity
        if actor.startswith("agent:"):
            changed = blacklisted_changes(previous, parsed)
            if changed:
                failures.append(GateFailure(
                    "reflexivity", f"an agent may only propose changes to {changed}; publish refused"))
        return failures

    def _probe_failure(self, model_id: str) -> str | None:
        row = self._catalogue.get_model(model_id) if self._catalogue is not None else None
        probe = (row or {}).get("probe") or {}
        if not probe.get("ok"):
            return "no passing probe on record (run POST /catalogue/models/{id}/probe)"
        checked_at = probe.get("checked_at")
        try:
            when = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return f"probe has no parseable checked_at ({checked_at!r})"
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if self._clock.now() - when > self._max_age:
            return f"probe from {checked_at} is older than {self._max_age.days} days"
        return None
