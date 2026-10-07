"""Every path a proposed record change travels, through the real routes: a turn proposes
→ one card (or a card beside another pack's card) → Confirm / Dismiss over HTTP → the
store changes (or does not) → the room is told. Plus the review's dead-card cases
(two changes to one record, one place created twice) and the guards around them."""
import app.agent as agent_mod
from app import chat, places, store
from app.agent import ToolInvocation, TurnResult
from app.db import get_db
from app.realtime import hub
from app.tools import build_tools
from kernos.data import actions
from tests.places_util import add_place, place_by_slug


def _turn(calls, reply="Bấm Xác nhận nhé."):
    """A fake engine turn that makes ``calls`` (``[(tool, args)]``) with the real tools."""
    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        tools = build_tools(ctx)
        return TurnResult(final_text=reply, turn_id="t-1",
                          tools=[ToolInvocation(n, a, tools[n].execute(a)) for n, a in calls])
    return fake


def _seed(room_id):
    with get_db().session() as s:
        add_place(s, room_id=room_id, slug="pho-ga", name="Phở gà", address="1 Lý Quốc Sư")
        add_place(s, room_id=room_id, slug="bun-bo-hue-1992", name="Bún bò Huế 1992")
        return {p.slug: p.id for p in places.list_places(s, room_id)}


async def _card(monkeypatch, room_id, member, calls, reply="Bấm Xác nhận nhé."):
    monkeypatch.setattr(agent_mod, "run_turn", _turn(calls, reply))
    return await chat.run_bot_turn(get_db(), room_id, member, "Linh", "@phoenix …")


def _place(room_id, pid):
    with get_db().session() as s:
        return places.get_place(s, room_id, pid)


async def test_confirm_over_http_applies_two_changes_to_one_place_and_refreshes_the_panel(api_client_room, monkeypatch):
    client, h, room_id, m = api_client_room
    ids = _seed(room_id)
    pid = str(ids["pho-ga"])
    card = await _card(monkeypatch, room_id, m["Linh"], [
        ("places_update", {"doc_id": pid, "changes": {"phone": "0901"}}),
        ("places_update", {"doc_id": pid, "changes": {"address": "9 Hàng Bông"}}),
        ("places_delete", {"doc_id": pid})])
    acts = card.attachments["actions"]
    assert card.kind == "record_draft" and len(acts) == 2                      # the two updates are one item
    assert sorted(c["field"] for c in acts[0]["changes"]) == ["address", "phone"]
    events = []
    orig = hub.publish

    async def spy(rid, ev):
        events.append(ev["type"])
        await orig(rid, ev)

    monkeypatch.setattr(hub, "publish", spy)
    r = client.post(f"/api/rooms/{room_id}/drafts/{card.id}/commit", headers=h)
    assert r.status_code == 200, r.text
    p = _place(room_id, int(pid))
    assert (p.phone, p.address, p.active) == ("0901", "9 Hàng Bông", False)      # all three, none undone
    assert "knowledge:changed" in events
    assert client.post(f"/api/rooms/{room_id}/drafts/{card.id}/commit", headers=h).status_code == 409   # once


async def test_a_stale_card_is_refused_with_409_and_writes_nothing(api_client_room, monkeypatch):
    client, h, room_id, m = api_client_room
    ids = _seed(room_id)
    card = await _card(monkeypatch, room_id, m["Linh"], [
        ("places_create", {"data": {"name": "Cơm rang Tuấn"}}),
        ("places_update", {"doc_id": str(ids["pho-ga"]), "changes": {"phone": "0901"}})])
    with get_db().session() as s:                                             # the panel, meanwhile
        places.edit_place(s, places.get_place(s, room_id, ids["pho-ga"]), {"phone": "0999"})
    r = client.post(f"/api/rooms/{room_id}/drafts/{card.id}/commit", headers=h)
    assert r.status_code == 409 and "changed since" in r.json()["detail"]
    with get_db().session() as s:
        assert [p.name for p in places.list_places(s, room_id)] == ["Bún bò Huế 1992", "Phở gà"]   # no create
    assert _place(room_id, ids["pho-ga"]).phone == "0999"


async def test_dismiss_over_http_writes_nothing_and_the_card_cannot_be_confirmed_after(api_client_room, monkeypatch):
    client, h, room_id, m = api_client_room
    ids = _seed(room_id)
    card = await _card(monkeypatch, room_id, m["Linh"], [("places_delete", {"doc_id": str(ids["bun-bo-hue-1992"])})])
    r = client.patch(f"/api/rooms/{room_id}/drafts/{card.id}", json={"status": "cancelled"}, headers=h)
    assert r.status_code == 200
    assert _place(room_id, ids["bun-bo-hue-1992"]).active
    assert client.post(f"/api/rooms/{room_id}/drafts/{card.id}/commit", headers=h).status_code == 409


async def test_the_actions_cannot_be_edited_and_another_room_cannot_confirm(api_client_room, monkeypatch):
    client, h, room_id, m = api_client_room
    ids = _seed(room_id)
    card = await _card(monkeypatch, room_id, m["Linh"], [("places_delete", {"doc_id": str(ids["pho-ga"])})])
    client.patch(f"/api/rooms/{room_id}/drafts/{card.id}", json={"actions": []}, headers=h)
    with get_db().session() as s:
        assert len(s.get(type(card), card.id).attachments["actions"]) == 1        # payload is not editable
    other = client.post("/api/rooms/create", json={"room_name": "B", "display_name": "X", "nickname": "x", "pin": "1234"}).json()
    hb = {"Authorization": f"Bearer {other['token']}"}
    assert client.post(f"/api/rooms/{other['room_id']}/drafts/{card.id}/commit", headers=hb).status_code in (404, 409)
    assert client.post(f"/api/rooms/{room_id}/drafts/{card.id}/commit", headers=hb).status_code in (401, 403)
    assert _place(room_id, ids["pho-ga"]).active                                # untouched


async def test_a_place_change_beside_a_meal_gets_its_own_card(api_client_room, monkeypatch):
    client, h, room_id, m = api_client_room
    ids = _seed(room_id)
    meal = {"participants": [m["Linh"], m["Giang"]], "total": 120000}
    reply = await _card(monkeypatch, room_id, m["Linh"], [
        ("propose_meal", meal), ("places_delete", {"doc_id": str(ids["bun-bo-hue-1992"])})])
    assert reply.kind == "expense_draft"
    msgs = client.get(f"/api/rooms/{room_id}/messages?days=1", headers=h).json()
    kinds = [x["kind"] for x in (msgs["messages"] if isinstance(msgs, dict) else msgs)]
    assert "expense_draft" in kinds and "record_draft" in kinds                  # neither dropped


async def test_the_reply_stays_on_the_card_and_one_place_is_created_once(api_client_room, monkeypatch):
    client, h, room_id, m = api_client_room
    _seed(room_id)
    card = await _card(monkeypatch, room_id, m["Linh"], [
        ("suggest_lunch", {}),
        ("places_create", {"data": {"name": "Cơm Tấm"}}),
        ("places_create", {"data": {"name": "Com tam", "phone": "1"}})],
        reply="Hôm nay ăn Phở gà nhé! Mình thêm Cơm Tấm vào danh sách.")
    assert card.body.startswith("Hôm nay ăn Phở gà nhé!") and card.attachments["reply"]
    assert [a["op"] for a in card.attachments["actions"]] == ["create"]          # "Com tam" is the same place
    assert client.post(f"/api/rooms/{room_id}/drafts/{card.id}/commit", headers=h).status_code == 200
    with get_db().session() as s:
        p = place_by_slug(s, room_id, "com-tam")
    assert p.walkable and p.active                                                # defaults, never guessed


def test_a_card_never_applies_to_a_collection_that_reused_its_id(db):
    with db.session() as s:
        col = store.collection(s, "places")
    action = {"collection_id": col["id"], "collection": "rota", "internal": False, "space_id": "1",
              "op": "delete", "doc_id": "1", "payload": {}, "before": {}, "label": "x"}
    with db.session() as s:
        try:
            actions.apply(store.DATA, action, actor="member:1", session=s)
        except actions.ActionRefused as exc:
            assert "no longer exists" in str(exc)
        else:
            raise AssertionError("applied to the wrong collection")


def test_hidden_places_do_not_crowd_out_search_or_find(db):
    from app.models import Room
    from app.tools import ToolContext
    with db.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.flush()
        for i in range(25):
            add_place(s, room_id=1, slug=f"bun-an-{i}", name=f"Bún ẩn {i}", active=False)
        # long, so word ranking (BM25) puts it after every short hidden one
        add_place(s, room_id=1, slug="bun-thang", name="Quán bún thang bà Đức ngõ chợ Hàng Hành phố cổ")
    tools = build_tools(ToolContext(db=db, room_id=1))
    found = [d["data"]["name"] for d in tools["places_search"].execute({"query": "bún"})["documents"]]
    assert found == ["Quán bún thang bà Đức ngõ chợ Hàng Hành phố cổ"]
    assert [p["name"] for p in tools["find_places"].execute({"all": True})["places"]] == [
        "Quán bún thang bà Đức ngõ chợ Hàng Hành phố cổ"]


# prod 2026-10-07, "update sdt quán gà koko": the model sent every editable field, empty
# where it had nothing to say, then corrected itself with the whole record in the same turn
_KOKO = dict(aliases=["koko", "chicken", "gà rán"], tags=["gần", "gà"], phone="0865869862",
             walk_minutes=5, price_hint=100000)


def _koko(room_id):
    with get_db().session() as s:
        return str(add_place(s, room_id=room_id, slug="koko-chicken", name="koko chicken", **_KOKO).id)


def _everything(**over):
    return {"name": "koko chicken", "aliases": _KOKO["aliases"], "tags": _KOKO["tags"], "delivery": [],
            "address": "", "phone": "0564434567", "walkable": True, "walk_minutes": 5,
            "price_hint": 100000, "closed_until": "", "active": True, **over}


def test_an_update_that_would_erase_a_field_is_refused_and_says_how_to_clear(api_client_room):
    from app.tools import ToolContext
    _, _, room_id, _ = api_client_room
    pid = _koko(room_id)
    tools = build_tools(ToolContext(db=get_db(), room_id=room_id))
    out = tools["places_update"].execute({"doc_id": pid, "changes": _everything(tags=[])})
    assert not out.get("ok") and "['tags']" in out["error"] and "clear" in out["error"]
    # empty over empty (delivery, address, closed_until) is no change, not an erase
    ok = tools["places_update"].execute({"doc_id": pid, "changes": _everything()})
    assert [c["field"] for c in ok["action"]["changes"]] == ["phone"]
    cleared = tools["places_update"].execute({"doc_id": pid, "clear": ["tags"]})
    assert cleared["action"]["changes"] == [{"field": "tags", "before": ["gần", "gà"], "after": []}]
    assert "cannot be cleared" in tools["places_update"].execute({"doc_id": pid, "clear": ["walk_minutes"]})["error"]


async def test_a_corrected_update_in_one_turn_is_one_card_item_and_confirms_right(api_client_room, monkeypatch):
    client, h, room_id, m = api_client_room
    pid = _koko(room_id)
    card = await _card(monkeypatch, room_id, m["Linh"], [
        ("places_update", {"doc_id": pid, "changes": _everything(aliases=["gà koko"], walk_minutes=0, price_hint=0)}),
        ("places_update", {"doc_id": pid, "changes": _everything()})])
    acts = card.attachments["actions"]
    assert len(acts) == 1 and acts[0]["changes"] == [{"field": "phone", "before": "0865869862", "after": "0564434567"}]
    assert client.post(f"/api/rooms/{room_id}/drafts/{card.id}/commit", headers=h).status_code == 200
    p = _place(room_id, int(pid))
    assert (p.phone, p.aliases, p.walk_minutes, p.price_hint) == ("0564434567", _KOKO["aliases"], 5, 100000)

