"""What a plugin is (design §4.2, §4.3).

A plugin is a code module registered against one stage. Its ``config_schema``
is its content type: the content plane validates a profile's config against it
and a generic editor renders it. ``id`` and ``version`` together are the
contract; behaviour changes mean a new version (§4.3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from kernos.kernel.context import Stage, TurnContext, Verdict


@runtime_checkable
class Plugin(Protocol):
    id: str
    version: str
    stage: Stage
    config_schema: dict
    handles_money: bool

    async def run(self, ctx: TurnContext, config: dict) -> TurnContext | Verdict | None: ...


@dataclass(frozen=True)
class PluginRef:
    """How a profile names a plugin: id, version (mandatory) and its config."""

    id: str
    version: str
    config: dict = field(default_factory=dict)


class BasePlugin:
    """Optional convenience base: sensible defaults for the descriptive attributes."""

    id: str = ""
    version: str = "1"
    stage: Stage = Stage.after
    config_schema: dict = {"type": "object", "additionalProperties": False}
    handles_money: bool = False

    async def run(self, ctx: TurnContext, config: dict) -> TurnContext | Verdict | None:  # pragma: no cover
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.id}@{self.version} [{self.stage}]>"


def key(plugin_or_ref: Any) -> str:
    """``id@version`` — the string a trace and a registry index by."""
    return f"{plugin_or_ref.id}@{plugin_or_ref.version}"
