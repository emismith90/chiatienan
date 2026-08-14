import pytest

from app import drafts, ledger
from app.db import Database
from app.models import Meal, Member, Place, Room
from app.tools import ToolContext, build_tools


@pytest.fixture()
def env():
    db = Database("sqlite:///:memory:")
    db.create_all()
    with db.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.flush()
        s.add(Member(id=1, room_id=1, display_name="An", nickname="an"))
        s.add(Member(id=2, room_id=1, display_name="Bình", nickname="binh"))
        s.add(Place(room_id=1, slug="bun-cha-rua-xe", name="Bún chả rửa xe Nam Đồng",
                    aliases=["bún chả rửa xe", "rửa xe"]))
        s.add(Place(room_id=1, slug="banh-cuon-ba-hoanh", name="Bánh cuốn Bà Hoành"))
        s.add(Place(room_id=1, slug="banh-cuon-ba-xuan", name="Bánh cuốn Bà Xuân"))
    return db, build_tools(ToolContext(db=db, room_id=1, sender_member_id=1))


def test_confident_dish_links_the_place(env):
    _db, tools = env
    res = tools["propose_meal"].execute(
        {"participants": [1, 2], "total": 100000, "payer": 1, "dish": "bún chả rửa xe"})
    assert res["ok"] and res["place_id"] is not None
    assert res["place_guess"]["name"] == "Bún chả rửa xe Nam Đồng"


def test_ambiguous_dish_leaves_place_null_but_still_proposes(env):
    _db, tools = env
    res = tools["propose_meal"].execute(
        {"participants": [1, 2], "total": 100000, "payer": 1, "dish": "bánh cuốn"})
    assert res["ok"], "an ambiguous place must never block a meal (D2)"
    assert res["place_id"] is None
    assert res["dish"] == "bánh cuốn", "the raw text is kept verbatim"


def test_unknown_dish_never_blocks_the_meal(env):
    _db, tools = env
    res = tools["propose_meal"].execute(
        {"participants": [1, 2], "total": 100000, "payer": 1, "dish": "sushi bar"})
    assert res["ok"] and res["place_id"] is None
    assert res["shares_preview"], "the split is unaffected by place resolution"


def test_meal_with_no_dish_at_all_is_fine(env):
    _db, tools = env
    res = tools["propose_meal"].execute({"participants": [1, 2], "total": 100000, "payer": 1})
    assert res["ok"] and res["place_id"] is None


def test_token_tier_offers_a_guess_but_does_not_link(env):
    _db, tools = env
    res = tools["propose_meal"].execute(
        {"participants": [1, 2], "total": 100000, "payer": 1,
         "dish": "rửa xe bún chả nam đồng"})
    assert res["ok"]
    assert res["place_id"] is None, "token tier is a guess, not a link"
    assert res["place_guess"]["name"] == "Bún chả rửa xe Nam Đồng"


def test_record_meal_persists_place_id(env):
    db, _tools = env
    with db.session() as s:
        place = s.query(Place).filter_by(slug="bun-cha-rua-xe").one()
        res = ledger.record_meal(
            s, room_id=1, payer_member_id=1, participants=[1, 2],
            total_amount=100000, dish="bún chả rửa xe", place_id=place.id)
        assert res["place_id"] == place.id
        assert s.get(Meal, res["meal_id"]).place_id == place.id


def test_place_id_is_editable_on_the_draft_card(env):
    assert "place_id" in drafts._EDITABLE
