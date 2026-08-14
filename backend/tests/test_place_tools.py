import pytest

from app.db import Database
from app.models import Place, Room
from app.tools import ToolContext, build_tools


@pytest.fixture()
def tools():
    db = Database("sqlite:///:memory:")
    db.create_all()
    with db.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.flush()
        s.add(Place(room_id=1, slug="pho-vui", name="Phở Vui", aliases=["vui"]))
    return build_tools(ToolContext(db=db, room_id=1))


def test_find_places_resolves_a_casual_spelling(tools):
    res = tools["find_places"].execute({"names": ["vui"]})
    assert res["ok"] and [m["slug"] for m in res["matched"]] == ["pho-vui"]


def test_find_places_lists_all_when_asked(tools):
    res = tools["find_places"].execute({"all": True})
    assert res["ok"] and len(res["places"]) == 1


def test_add_place_creates_and_returns_the_slug(tools):
    res = tools["add_place"].execute({"name": "Bún chả Tuấn Hưng", "aliases": ["tuấn hưng"]})
    assert res["ok"] and res["slug"] == "bun-cha-tuan-hung"
    found = tools["find_places"].execute({"names": ["tuấn hưng"]})
    assert found["matched"][0]["slug"] == "bun-cha-tuan-hung"


def test_add_place_is_idempotent_on_slug(tools):
    res = tools["add_place"].execute({"name": "Phở Vui"})
    assert res["ok"] and res["already_existed"] is True
    assert len(tools["find_places"].execute({"all": True})["places"]) == 1


def test_add_place_requires_a_name(tools):
    assert tools["add_place"].execute({})["ok"] is False
