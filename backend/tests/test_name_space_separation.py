"""A place name must never resolve to a person, nor a person to a place (D18).

The hazard is concrete, not theoretical: ``roster._NameIndex`` indexes bank
account holders, so this room's Nhím (``DINH HONG TRANG``) is reachable as
"Trang", and the room eats at "Bún riêu cô Trang". If a place name is read as a
member, that member gets added to a split as an eater — the failure mode here is
money, not a bad suggestion.
"""
import json
from pathlib import Path

import pytest

from app import places, roster
from app.db import Database
from app.models import Member, Place, Room
from app.seed_places import lint
from app.tools import ToolContext, build_tools

SEEDS = Path(__file__).resolve().parents[1] / "seeds"
ROOM_NAMES = ["Giang", "Linh", "Nhím",
              "HOANG MINH GIANG", "NGUYEN ANH LINH", "DINH HONG TRANG"]


@pytest.fixture()
def env():
    db = Database("sqlite:///:memory:")
    db.create_all()
    with db.session() as s:
        s.add(Room(id=1, name="12B", invite_token="t"))
        s.flush()
        s.add(Member(id=1, room_id=1, display_name="Nhím", nickname="Nhím",
                     account_holder="DINH HONG TRANG"))
        s.add(Member(id=2, room_id=1, display_name="Linh Nguyen", nickname="Linh",
                     account_holder="NGUYEN ANH LINH"))
        # The real room has a SECOND person reachable as "Trang" (BUI THU TRANG,
        # alias "Bùi Trang"). Modelling only Nhím made the residual test assert
        # behaviour production never had.
        s.add(Member(id=3, room_id=1, display_name="Tabu", nickname="Tabu",
                     account_holder="BUI THU TRANG", aliases=["Bùi Trang"]))
        s.add(Place(room_id=1, slug="bun-rieu-co-trang", name="Bún riêu cô Trang",
                    aliases=["cô trang", "co trang"]))
        s.add(Place(room_id=1, slug="bun-dau-met-tran-huu-tuoc",
                    name="Bún đậu mẹt Trần Hữu Tước", aliases=["bún đậu mẹt", "bún đậu"]))
    return db, build_tools(ToolContext(db=db, room_id=1, sender_member_id=1))


def test_member_lookup_never_returns_a_place(env):
    db, _tools = env
    with db.session() as s:
        res = roster.resolve(s, 1, names=["bún riêu cô Trang", "bún đậu mẹt"])
    assert res["matched"] == [], f"roster resolved a restaurant: {res['matched']}"


def test_place_lookup_never_returns_a_member(env):
    db, _tools = env
    with db.session() as s:
        assert places.resolve_one(s, 1, "Nhím") == (None, "none")
        assert places.resolve_one(s, 1, "Linh") == (None, "none")


def test_a_place_named_like_a_member_does_not_join_the_split(env):
    """The money case. 'cô Trang' is the venue; Nhím must not become an eater."""
    _db, tools = env
    res = tools["propose_meal"].execute(
        {"participants": [1, 2], "total": 100000, "payer": 2, "dish": "bún riêu cô Trang"})
    assert res["ok"], "a place that looks like a member must not block the meal"
    eaters = {s["member"] for s in res["shares_preview"]}
    assert eaters == {1, 2}, f"split membership changed: {eaters}"
    assert res["place_guess"]["name"] == "Bún riêu cô Trang"


def test_shipped_seeds_contain_no_bare_member_name_alias():
    """Regression lock on the dropped 'anh linh' alias."""
    rows = []
    for name in ("places-local.json", "places-nearby.json", "places-online.json"):
        rows += json.loads((SEEDS / name).read_text(encoding="utf-8"))
    assert lint(rows, member_names=ROOM_NAMES) == []


def test_known_residual_the_bare_honorific_form_still_reaches_the_members(env):
    """Characterisation, not an endorsement — this is the limit of the guard.

    "cô Trang" strips its kinship term to "trang", which is a name token of TWO
    people in the real room: Nhím (bank holder `DINH HONG TRANG`, alias "Trang
    Dinh") and Tabu (`BUI THU TRANG`, alias "Bùi Trang"). So `roster` returns
    them as **ambiguous** rather than picking one — which is the safe outcome, and
    safer than a single match would be.

    The fixture models both on purpose. An earlier version had only Nhím and
    asserted a lone match, which was never true of production: it would have gone
    green while quietly describing behaviour the real room never exhibits.

    Meanwhile the place index answers the same string with the restaurant. Neither
    is wrong — the ambiguity is real in the language. What keeps it safe is that
    the indexes are separate and the model picks the tool, enforced above by the
    split-membership test. Pinned so a change to `_HONORIFICS` or the given-name
    tier surfaces here instead of in a wrong bill.
    """
    db, _tools = env
    with db.session() as s:
        res = roster.resolve(s, 1, names=["cô Trang"])
    assert res["matched"] == [], "two people answer to Trang — must not pick one"
    assert [a["name"] for a in res["ambiguous"]] == ["cô Trang"]
    assert {c["display_name"] for c in res["ambiguous"][0]["candidates"]} == {"Nhím", "Tabu"}

    # ...and the place index answers the same string with the restaurant.
    with db.session() as s:
        place, tier = places.resolve_one(s, 1, "cô Trang")
    assert place.slug == "bun-rieu-co-trang" and tier == "exact"
