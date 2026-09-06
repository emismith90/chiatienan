"""``ToolPack``: how a business contributes code to the kernel (design §4.2, §5.4; plan 3.1).

A pack is the unit a developer ships for one domain: tools, the decision of what a
turn's outcome is (a draft card, a typed body, or nothing), the draft kinds it can
commit, the debt edges it contributes to balances, fixtures for building eval
worlds, seed data, and its own tables. The content plane decides *which* packs a
profile enables and which of their tools are on; the pack decides nothing about
that.

Tools are **sync** callables (the host's executor already runs them off the event
loop) and return a payload dict; ``{"ok": False, "error": …}`` is a clarifying
question, not a failure (the tools.py convention).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from kernos.kernel.context import Outcome


def err(message: str) -> dict:
    """The clarifying-question result every pack tool returns instead of raising."""
    return {"ok": False, "error": message}


@dataclass
class PackTool:
    name: str
    description: str
    schema: dict
    execute: Callable[[dict], Any]

    def manifest(self) -> dict:
        return {"name": self.name, "description": self.description, "schema": self.schema}


@dataclass(frozen=True)
class DraftKind:
    """A card kind a pack can commit — everything the host's draft store needs to
    know about it without knowing the business (plan Task 3.4).

    ``commit(session, space_id, payload, *, logged_by) -> result`` writes the
    domain rows for a confirmed card; ``card(session, space_id, payload, result) ->
    (body, attachments)`` is the confirmation the room sees afterwards, built from
    the result dict, never from prose; ``prepare(payload) -> payload`` normalises a
    payload on create and on every edit (lunch re-derives the itemised split);
    ``signature(payload) -> hashable`` identifies a re-proposal so the store can
    retire the older pending card (``None`` = never supersede); ``editable`` is the
    field list a card edit may patch; ``stamps`` names the kernel-owned fields the
    render stage adds to the payload (``raw_input``, ``logged_by``, ``turn_id``) — a
    pack never sees principals.
    """

    kind: str
    commit: Callable[..., Any]
    editable: frozenset[str] = frozenset()
    stamps: frozenset[str] = frozenset({"turn_id"})
    card: Callable[..., tuple[str, dict]] | None = None
    prepare: Callable[[dict], dict] | None = None
    signature: Callable[[dict], Any] | None = None


@runtime_checkable
class ToolPack(Protocol):
    id: str
    version: str
    handles_money: bool
    #: tools whose successful result names a card to republish (`draft_id`)
    cancel_tools: frozenset[str]
    #: tools whose calls carry money — what eval capture records (plan Task 4.3)
    money_tools: frozenset[str]

    def tools(self, ctx: Any) -> dict[str, PackTool]: ...
    def draft_kinds(self) -> dict[str, DraftKind]: ...
    def render(self, result: Any) -> Outcome | None: ...
    def contributions(self, session: Any, space_id: str) -> list: ...
    def fixtures(self) -> dict[str, Callable[..., Any]]: ...
    def seed(self, session: Any, space_id: str) -> None: ...
    def bind(self, engine: Any) -> None: ...
    def graders(self) -> dict[str, Callable[..., Any]]: ...      # eval grader factories by plugin id


class BasePack:
    """Defaults so a pack implements only what it has."""

    id: str = ""
    version: str = "1"
    handles_money: bool = False
    cancel_tools: frozenset[str] = frozenset()
    money_tools: frozenset[str] = frozenset()

    def tools(self, ctx: Any) -> dict[str, PackTool]:
        return {}

    def draft_kinds(self) -> dict[str, DraftKind]:
        return {}

    def render(self, result: Any) -> Outcome | None:
        return None

    def contributions(self, session: Any, space_id: str) -> list:
        return []

    def fixtures(self) -> dict[str, Callable[..., Any]]:
        return {}

    def seed(self, session: Any, space_id: str) -> None:
        return None

    def bind(self, engine: Any) -> None:
        return None

    def graders(self) -> dict[str, Callable[..., Any]]:
        return {}

    def __repr__(self) -> str:
        return f"<pack {self.id}@{self.version}>"


class PackError(ValueError):
    pass


class PackRegistry:
    def __init__(self) -> None:
        self._packs: dict[str, ToolPack] = {}

    def register(self, pack: ToolPack) -> ToolPack:
        if not pack.id:
            raise PackError("a pack needs an id")
        self._packs[pack.id] = pack
        return pack

    def register_all(self, packs) -> None:
        for p in packs:
            self.register(p)

    def get(self, pack_id: str) -> ToolPack:
        try:
            return self._packs[pack_id]
        except KeyError:
            raise PackError(f"no pack {pack_id!r} (known: {sorted(self._packs)})") from None

    def list(self) -> list[ToolPack]:
        return [self._packs[k] for k in sorted(self._packs)]

    def describe(self) -> list[dict]:
        return [{"id": p.id, "version": p.version, "handles_money": p.handles_money,
                 "draft_kinds": sorted(p.draft_kinds()), "fixtures": sorted(p.fixtures())}
                for p in self.list()]

    def enabled(self, tool_packs: list) -> list[tuple[ToolPack, dict]]:
        """``(pack, per-tool overrides)`` in profile order for a spec's ``tool_packs``
        (each a ``ToolPackRef`` or its dict)."""
        out = []
        for ref in tool_packs or []:
            pack_id = ref["pack"] if isinstance(ref, dict) else ref.pack
            overrides = (ref.get("tools") if isinstance(ref, dict) else ref.tools) or {}
            out.append((self.get(pack_id), dict(overrides)))
        return out


def apply_tool_overrides(tools: dict[str, PackTool], overrides: dict[str, dict]) -> dict[str, PackTool]:
    """Per-tool content: ``{"enabled": bool, "description": str}``. Unknown tool names
    in the overrides are an error — a profile that names a tool the pack does not
    have is misconfigured, not harmless."""
    unknown = set(overrides) - set(tools)
    if unknown:
        raise PackError(f"overrides name tools the pack does not have: {sorted(unknown)}")
    out: dict[str, PackTool] = {}
    for name, tool in tools.items():
        ov = overrides.get(name, {})
        if ov.get("enabled", True) is False:
            continue
        if ov.get("description"):
            tool = PackTool(tool.name, ov["description"], tool.schema, tool.execute)
        out[name] = tool
    return out


def compose_tools(registry: PackRegistry, tool_packs: list, ctx: Any) -> dict[str, PackTool]:
    """Every enabled pack's tools with overrides applied, in profile order; a name
    provided by two packs is an error rather than a silent shadow."""
    out: dict[str, PackTool] = {}
    for pack, overrides in registry.enabled(tool_packs):
        for name, tool in apply_tool_overrides(pack.tools(ctx), overrides).items():
            if name in out:
                raise PackError(f"tool {name!r} is provided by two enabled packs")
            out[name] = tool
    return out
