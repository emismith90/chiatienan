"""``lunch_places``: the restaurant/knowledge tools as a pack (plan Task 3.1).

Stays in the host through Phase 3 because it is welded to the host's memory files
and knowledge panel (decision 5); the framework gets a home for it in Phase 5.
"""
from __future__ import annotations

from kernos.packs import BasePack, PackTool

PLACES_TOOLS = frozenset({"find_places", "suggest_lunch", "remember", "forget", "add_place"})


class LunchPlacesPack(BasePack):
    id, version, handles_money = "lunch_places", "1", False

    def tools(self, ctx) -> dict[str, PackTool]:
        from app.tools import _legacy_build_tools
        return {name: PackTool(name, t.description, t.input_schema, t.execute)
                for name, t in _legacy_build_tools(ctx).items() if name in PLACES_TOOLS}

    # `seed()` stays the no-op default: `seed_places.load_file` needs a seed file path,
    # which is a deployment decision, not a pack default. Phase 5 decides where it lives.
