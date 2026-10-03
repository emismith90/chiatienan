import pytest

from app.db import Database
from app.models import Room
from app.tools import ToolContext, build_tools
from tests.places_util import add_place


@pytest.fixture()
def tools():
    db = Database("sqlite:///:memory:")
    db.create_all()
    with db.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.flush()
        add_place(s, room_id=1, slug="pho-vui", name="Phở Vui", aliases=["vui"])
    return build_tools(ToolContext(db=db, room_id=1))


def test_find_places_resolves_a_casual_spelling(tools):
    res = tools["find_places"].execute({"names": ["vui"]})
    assert res["ok"] and [m["slug"] for m in res["matched"]] == ["pho-vui"]


def test_find_places_lists_all_when_asked(tools):
    res = tools["find_places"].execute({"all": True})
    assert res["ok"] and len(res["places"]) == 1
