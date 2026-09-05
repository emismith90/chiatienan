"""``Resolver``: space → the profile it runs (design §5.1, plan Task 2.4).

:class:`StaticResolver` — one profile for every space, built from code.
:class:`DbResolver` — binding → agent → profile → published version, with the
host's ``runtime`` injected and the binding's overrides applied **by copy**
(review finding 8); an unbound space runs the default business's default agent;
a store with no content at all falls back to a static spec. Cached by
``(version_id, space_id)``; ``invalidate()`` is hooked to ``store.on_change``.
"""
from __future__ import annotations

from typing import Protocol

from kernos.content.errors import NotFound
from kernos.content.spec import BindingOverrides, ProfileSpec, Runtime


class Resolver(Protocol):
    def resolve(self, space_id: str) -> ProfileSpec: ...


class StaticResolver:
    def __init__(self, spec: ProfileSpec) -> None:
        self._spec = spec

    def resolve(self, space_id: str) -> ProfileSpec:
        return self._spec

    def invalidate(self) -> None:  # symmetry with DbResolver
        pass


class DbResolver:
    def __init__(self, store, *, default_business_slug: str, runtime: Runtime, fallback: ProfileSpec) -> None:
        self._store = store
        self._default_business = default_business_slug
        self._runtime = runtime
        self._fallback = fallback
        self._cache: dict[tuple[int, str], ProfileSpec] = {}
        store.on_change.append(self.invalidate)

    def invalidate(self) -> None:
        self._cache.clear()

    def describe(self, space_id: str) -> dict:
        """Which agent/profile/version a space resolves to (for the admin API)."""
        binding = self._store.get_binding(space_id)
        agent = None
        if binding is not None:
            agent = self._store.get_agent(binding["agent_id"])
        else:
            try:
                agent = self._store.default_agent(self._default_business)
            except NotFound:
                agent = None
        if agent is None:
            return {"space_id": space_id, "bound": binding is not None, "agent": None, "profile_id": None,
                    "version_id": None, "source": "fallback"}
        profile = self._store.get_profile(agent["profile_id"])
        return {"space_id": space_id, "bound": binding is not None, "agent": agent,
                "profile_id": profile["id"], "version_id": profile["published_version_id"],
                "overrides": (binding or {}).get("overrides") or {},
                "source": "binding" if binding else "default"}

    def resolve(self, space_id: str) -> ProfileSpec:
        info = self.describe(space_id)
        version_id = info["version_id"]
        if version_id is None:
            return self._fallback.with_runtime(self._runtime)
        key = (version_id, space_id)
        spec = self._cache.get(key)
        if spec is None:
            stored = self._store.get_version(version_id)["spec"]
            spec = ProfileSpec.model_validate(stored).with_runtime(self._runtime)
            overrides = BindingOverrides.model_validate(info.get("overrides") or {})
            spec = overrides.apply(spec)
            self._cache[key] = spec
        return spec
