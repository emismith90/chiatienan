"""``Resolver``: space → the profile it runs (design §5.1).

Phase 1 ships :class:`StaticResolver` — one profile for every space, built from
code — so the pipeline is real before the content plane exists. Phase 2 adds the
database-backed resolver (space → manager agent → published version).
"""
from __future__ import annotations

from typing import Protocol

from kernos.content.spec import ProfileSpec


class Resolver(Protocol):
    def resolve(self, space_id: str) -> ProfileSpec: ...


class StaticResolver:
    def __init__(self, spec: ProfileSpec) -> None:
        self._spec = spec

    def resolve(self, space_id: str) -> ProfileSpec:
        return self._spec
