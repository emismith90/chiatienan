"""Plugin discovery and schema-validated pipeline construction (design §4.3).

Two rules from the review are enforced here rather than documented:

* **Versions are mandatory.** A profile names ``id@version``; there is no
  "latest". A published spec therefore keeps running the plugin it was published
  with until someone republishes it — the same snapshot discipline as skill bodies.
* **A schema change is a new version.** The registry hashes each plugin's
  ``config_schema``; registering the same ``id@version`` with a different hash is
  refused, so a deploy cannot change what a live profile's config means.
"""
from __future__ import annotations

import hashlib
import json
from importlib.metadata import entry_points
from typing import Iterable

from jsonschema import Draft202012Validator

from kernos.kernel.context import Stage
from kernos.kernel.pipeline import Pipeline
from kernos.kernel.plugin import Plugin, PluginRef, key


class RegistryError(ValueError):
    pass


class ConfigError(RegistryError):
    """One or more plugin configs failed validation. ``problems`` lists them all."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def schema_hash(schema: dict) -> str:
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class Registry:
    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._hashes: dict[str, str] = {}
        self._validators: dict[str, Draft202012Validator] = {}

    # ------------------------------------------------------------ registration

    def register(self, plugin: Plugin) -> Plugin:
        if not plugin.id or not plugin.version:
            raise RegistryError(f"plugin {plugin!r} needs a non-empty id and version")
        k = key(plugin)
        h = schema_hash(plugin.config_schema)
        if k in self._plugins:
            if self._hashes[k] != h:
                raise RegistryError(
                    f"{k} is already registered with a different config_schema "
                    f"({self._hashes[k]} != {h}); a schema change is a new version"
                )
            return self._plugins[k]          # idempotent re-registration
        Draft202012Validator.check_schema(plugin.config_schema)
        self._plugins[k] = plugin
        self._hashes[k] = h
        self._validators[k] = Draft202012Validator(plugin.config_schema)
        return plugin

    def register_all(self, plugins: Iterable[Plugin]) -> None:
        for p in plugins:
            self.register(p)

    def load_entry_points(self, group: str = "kernos.plugins") -> int:
        """Register every plugin object exposed under an entry-point group.

        An entry point may resolve to a plugin or to an iterable of plugins.
        """
        n = 0
        for ep in entry_points(group=group):
            obj = ep.load()
            items = obj if isinstance(obj, (list, tuple)) else [obj]
            for item in items:
                self.register(item() if isinstance(item, type) else item)
                n += 1
        return n

    # ------------------------------------------------------------------ lookup

    def get(self, id: str, version: str) -> Plugin:
        k = f"{id}@{version}"
        try:
            return self._plugins[k]
        except KeyError:
            known = sorted(v for v in self._plugins if v.startswith(id + "@"))
            hint = f" (known: {', '.join(known)})" if known else ""
            raise RegistryError(f"no plugin {k}{hint}") from None

    def list(self) -> list[Plugin]:
        return [self._plugins[k] for k in sorted(self._plugins)]

    def describe(self) -> list[dict]:
        """The admin API's registry payload: one row per ``id@version``."""
        return [
            {
                "id": p.id, "version": p.version, "stage": str(p.stage),
                "config_schema": p.config_schema, "schema_hash": self._hashes[key(p)],
                "handles_money": bool(getattr(p, "handles_money", False)),
            }
            for p in self.list()
        ]

    # -------------------------------------------------------------- validation

    def validate_config(self, id: str, version: str, config: dict) -> list[str]:
        """Human-readable problems, each with the JSON-pointer path of the bad value."""
        plugin = self.get(id, version)
        validator = self._validators[key(plugin)]
        problems = []
        for err in sorted(validator.iter_errors(config), key=lambda e: list(e.absolute_path)):
            path = "/" + "/".join(str(p) for p in err.absolute_path)
            problems.append(f"{key(plugin)} config at {path}: {err.message}")
        return problems

    def build_pipeline(self, spec_pipeline: dict[str, list[dict | PluginRef]]) -> Pipeline:
        """``{stage: [{id, version, config}]}`` → a runnable :class:`Pipeline`.

        Every triple is resolved and validated before anything is built, and every
        problem is reported at once — a publish that fails should say everything
        that is wrong, not the first thing.
        """
        problems: list[str] = []
        stages: dict[Stage, list] = {}
        for stage_name, entries in spec_pipeline.items():
            try:
                stage = Stage(stage_name)
            except ValueError:
                problems.append(f"unknown stage '{stage_name}'")
                continue
            for entry in entries:
                ref = entry if isinstance(entry, PluginRef) else PluginRef(
                    id=entry.get("id", ""), version=str(entry.get("version", "")),
                    config=entry.get("config") or {})
                if not ref.version:
                    problems.append(f"{ref.id}: version is mandatory in a pipeline entry")
                    continue
                try:
                    plugin = self.get(ref.id, ref.version)
                except RegistryError as exc:
                    problems.append(str(exc))
                    continue
                problems.extend(self.validate_config(ref.id, ref.version, ref.config))
                stages.setdefault(stage, []).append((plugin, dict(ref.config)))
        if problems:
            raise ConfigError(problems)
        return Pipeline(stages)
