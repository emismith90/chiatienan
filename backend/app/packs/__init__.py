"""chiatienan's packs (plan Task 3.1).

Phase 3a: thin wrappers over today's modules, so the pack interface, the per-tool
content overrides and the kernel render/persist plugins are real before any domain
code moves. 3b/3c move the money domain to ``ledger_core`` and ``packs/lunch_ledger``;
``lunch_places`` stays here until the framework has a home for knowledge (Phase 5).
"""
from app.packs.lunch import LunchLedgerPack  # noqa: F401
from app.packs.places import LunchPlacesPack  # noqa: F401

#: The 19 tools in the order `app.tools` has always listed them — pinned by
#: `test_tools_manifest.py` and the sidecar's schema fixture (review F7).
LEGACY_ORDER = (
    "find_members", "propose_meal", "void_meal", "cancel_draft", "pick_random",
    "resolve_period", "resolve_date", "member_statement", "get_period_summary",
    "settle_period", "add_member", "update_member", "delete_member",
    "find_places", "suggest_lunch", "remember", "forget", "add_place", "propose_payment",
)
MONEY_TOOLS = frozenset(LEGACY_ORDER) - {"find_places", "suggest_lunch", "remember", "forget", "add_place"}
PLACES_TOOLS = frozenset(LEGACY_ORDER) - MONEY_TOOLS
