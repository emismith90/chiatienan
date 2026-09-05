"""chiatienan's packs (plan Tasks 3.1, 3.3).

``lunch_ledger`` is the framework's (``packs/lunch_ledger``), registered here with
this host's QR builder and place resolver; ``lunch_places`` and ``room_members`` are
host packs — the first is welded to the host's memory files and knowledge panel
until Phase 5 gives the framework a home for knowledge, the second administers
sign-in accounts, which are the host's.
"""
from app.packs.lunch import lunch_ledger_pack
from app.packs.members import MEMBER_TOOLS, RoomMembersPack
from app.packs.places import PLACES_TOOLS, LunchPlacesPack
from packs.lunch_ledger import MONEY_TOOLS, LunchLedgerPack  # noqa: F401

#: The 19 tools in the order `app.tools` has always listed them — pinned by
#: `test_tools_manifest.py` and the sidecar's schema fixture (review F7).
LEGACY_ORDER = (
    "find_members", "propose_meal", "void_meal", "cancel_draft", "pick_random",
    "resolve_period", "resolve_date", "member_statement", "get_period_summary",
    "settle_period", "add_member", "update_member", "delete_member",
    "find_places", "suggest_lunch", "remember", "forget", "add_place", "propose_payment",
)
assert MONEY_TOOLS | PLACES_TOOLS | MEMBER_TOOLS == set(LEGACY_ORDER)


def host_packs() -> list:
    """Every pack this host registers with its kernel, fresh instances."""
    return [lunch_ledger_pack(), LunchPlacesPack(), RoomMembersPack()]
