"""The Lucky Draw button and the saved draw list it shares with the bot.

The button runs the bot's draw (`random_pick`) with no LLM turn, and posts the same
`random_pick` card to the room. The draw list is the members' `default_participant`
flag; the dialog replaces it, the bot's `edit_draw_list` adjusts it.
"""


def test_the_button_draws_and_posts_the_result_to_the_room(api_client_room, monkeypatch):
    client, headers, room_id, m = api_client_room
    monkeypatch.setattr("random.choice", lambda pool: pool[-1])
    r = client.post(f"/api/rooms/{room_id}/draw", json={"label": "trả tiền"}, headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["type"] == "random_pick" and out["label"] == "trả tiền"
    assert {c["id"] for c in out["candidates"]} == {m["Linh"], m["Giang"]}
    assert out["drawn_by"] == {"id": m["Linh"], "name": "Linh"}

    msgs = client.get(f"/api/rooms/{room_id}/messages", headers=headers).json()
    msgs = msgs["messages"] if isinstance(msgs, dict) else msgs
    card = next(x for x in msgs if x["id"] == out["message_id"])
    assert card["kind"] == "bot" and card["attachments"]["chosen"] == out["chosen"]
    assert out["chosen"]["name"] in card["body"]          # body rendered from the dict


def test_the_dialog_replaces_the_list_and_the_draw_uses_it(api_client_room, monkeypatch):
    client, headers, room_id, m = api_client_room
    r = client.put(f"/api/rooms/{room_id}/draw/pool",
                   json={"member_ids": [m["Giang"]]}, headers=headers)
    assert r.status_code == 200 and r.json()["in_draw"] == [m["Giang"]]
    out = client.post(f"/api/rooms/{room_id}/draw", json={}, headers=headers).json()
    assert out["chosen"]["id"] == m["Giang"] and len(out["candidates"]) == 1
    members = client.get(f"/api/rooms/{room_id}/members", headers=headers).json()
    assert {x["id"]: x["default_participant"] for x in members} == {m["Linh"]: False, m["Giang"]: True}


def test_an_empty_list_is_refused_not_drawn(api_client_room):
    client, headers, room_id, _ = api_client_room
    client.put(f"/api/rooms/{room_id}/draw/pool", json={"member_ids": []}, headers=headers)
    assert client.post(f"/api/rooms/{room_id}/draw", json={}, headers=headers).status_code == 409


def test_the_list_refuses_a_member_of_another_room(api_client_room):
    client, headers, room_id, m = api_client_room
    r = client.put(f"/api/rooms/{room_id}/draw/pool",
                   json={"member_ids": [m["Linh"], 999_999]}, headers=headers)
    assert r.status_code == 400
    members = client.get(f"/api/rooms/{room_id}/members", headers=headers).json()
    assert all(x["default_participant"] for x in members)      # nothing half-applied


def test_the_routes_are_room_scoped(api_client_room):
    client, headers, room_id, m = api_client_room
    assert client.post(f"/api/rooms/{room_id + 1}/draw", json={}, headers=headers).status_code == 403
    assert client.put(f"/api/rooms/{room_id + 1}/draw/pool", json={"member_ids": []},
                      headers=headers).status_code == 403


def test_the_bot_views_and_edits_the_same_list(db):
    from app.models import Member, Room
    from app.tools import ToolContext, build_tools
    with db.session() as s:
        room = Room(name="Room", invite_token="tok")
        s.add(room)
        s.flush()
        ms = [Member(room_id=room.id, display_name=n, nickname=n.lower(), pin="1")
              for n in ("An", "Bình", "Chi")]
        s.add_all(ms)
        s.flush()
        room_id, (a, b, c) = room.id, [x.id for x in ms]
    tools = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=a))

    view = tools["edit_draw_list"].execute({})
    assert [x["id"] for x in view["in_draw"]] == [a, b, c] and view["not_in_draw"] == []

    out = tools["edit_draw_list"].execute({"remove": [a]})
    assert [x["id"] for x in out["in_draw"]] == [b, c]
    assert {x["id"] for x in tools["pick_random"].execute({})["candidates"]} == {b, c}

    back = tools["edit_draw_list"].execute({"add": [a]})
    assert [x["id"] for x in back["in_draw"]] == [a, b, c]

    assert "error" in tools["edit_draw_list"].execute({"remove": ["An"]})   # ids, not names
    assert "error" in tools["edit_draw_list"].execute({"add": [999_999]})
