"""The knowledge panel's HTTP surface: read the three stores, edit two of them.

Every write route takes ``chat._agent_lock`` and — for the two file-backed stores —
an ``etag``, because the turn loop writes the same files. The tests here pin the
refusals as hard as the successes: a stale write that silently wins is the failure
mode this design exists to prevent.
"""
from datetime import date, timedelta

import pytest

from app import observations as obs
from app.realtime import hub


@pytest.fixture(autouse=True)
def room_files(tmp_path, monkeypatch):
    """Point room memory at a per-test directory.

    ``conftest`` sets one process-wide ``DATA_DIR``, and every test's first room is
    ``room_id=1`` — so without this, one test's observations.md is the next test's
    starting state.
    """
    from app import memory as mem
    monkeypatch.setattr(mem, "_base_dir", lambda: tmp_path)
    return tmp_path


def _seed_place(room_id, name, **kw):
    from app.db import get_db
    from app import places
    with get_db().session() as s:
        p = places.create_place(s, room_id, name=name, **kw)
        return {"id": p.id, "slug": p.slug, "name": p.name}


def _seed_meal(room_id, payer, participants, *, total, on, place_id=None):
    from app.db import get_db
    from app import ledger
    with get_db().session() as s:
        return ledger.record_meal(s, room_id=room_id, payer_member_id=payer,
                                  participants=participants, total_amount=total,
                                  dish="bun cha", occurred_on=on, place_id=place_id)


def _write_obs(room_id, text):
    from app.memory import room_memory_dir
    (room_memory_dir(room_id) / "observations.md").write_text(text, encoding="utf-8")


def _read_obs(room_id):
    from app.memory import room_memory_dir
    return (room_memory_dir(room_id) / "observations.md").read_text(encoding="utf-8")


# ============================================================== GET /knowledge

def test_get_knowledge_returns_all_three_stores(api_client_room):
    client, headers, room_id, m = api_client_room
    be_bu = _seed_place(room_id, "Quán Bé Bự", tags=["com"], phone="0912345678")
    _seed_place(room_id, "Cơm gà Thịnh Lơ", tags=["chưa-thử"])
    _write_obs(room_id,
               "# tay\n"
               "- always | place:quan-be-bu | order-by@11:30 | Phải gọi trước.\n"
               "- 2026-08-10 | place:quan-be-bu | - | Hôm nay hết gà.\n"
               "- always | member:giang | - | Thích bún riêu.\n")
    from app import memory as mem
    mem.append_summary(room_id, summary_text="- Ăn bún chả", through_id=9,
                       through_at="2026-08-01T12:00:00", header="Tóm tắt")

    data = client.get(f"/api/rooms/{room_id}/knowledge", headers=headers).json()

    assert set(data) == {"etags", "places", "observations", "memory", "subjects"}
    assert set(data["etags"]) == {"observations", "memory"}

    place = next(p for p in data["places"] if p["id"] == be_bu["id"])
    assert place["slug"] == "quan-be-bu"
    assert place["phone"] == "0912345678"
    assert place["note_count"] == 2          # the rule and the observation
    assert place["stats"]["times"] == 0      # nobody has eaten there yet

    # Subjects come back resolved: a panel must never have to turn
    # "member:giang" into a person by itself.
    labels = {(o["subject_kind"], o["subject_label"]) for o in data["observations"]}
    assert ("place", "Quán Bé Bự") in labels
    assert ("member", "Giang") in labels

    # Rules first, then dated observations.
    assert data["observations"][0]["standing"] is True
    assert data["observations"][0]["gate_label"] == "Order by 11:30"

    assert [s["date"] for s in data["memory"]["sections"]] == ["2026-08-01"]
    assert data["memory"]["watermark"]["through_id"] == 9


def test_derived_place_stats_come_from_the_ledger(api_client_room):
    client, headers, room_id, m = api_client_room
    p = _seed_place(room_id, "Bún chả rửa xe")
    from app.clock import today_ict
    _seed_meal(room_id, m["Linh"], [m["Linh"], m["Giang"]], total=120_000,
               on=today_ict() - timedelta(days=3), place_id=p["id"])

    data = client.get(f"/api/rooms/{room_id}/knowledge", headers=headers).json()
    stats = next(x for x in data["places"] if x["id"] == p["id"])["stats"]
    assert stats["times"] == 1
    assert stats["days_since"] == 3
    assert stats["avg_per_head"] == 60_000
    assert stats["band"] is None            # one price cannot make a tertile


def test_stale_observations_are_flagged_not_hidden(api_client_room):
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    from app.clock import today_ict
    old = today_ict() - timedelta(days=obs.DEFAULT_SINCE_DAYS + 5)
    fresh = today_ict() - timedelta(days=2)
    _write_obs(room_id,
               f"- {old.isoformat()} | place:quan-be-bu | - | Chuyện cũ.\n"
               f"- {fresh.isoformat()} | place:quan-be-bu | - | Chuyện mới.\n")
    rows = client.get(f"/api/rooms/{room_id}/knowledge", headers=headers).json()["observations"]
    by_text = {r["text"]: r for r in rows}
    assert by_text["Chuyện cũ."]["stale"] is True
    assert by_text["Chuyện mới."]["stale"] is False


def test_an_orphaned_subject_is_still_shown(api_client_room):
    """A note about a place that no longer exists is exactly the note someone
    needs to see in order to clean it up."""
    client, headers, room_id, _m = api_client_room
    _write_obs(room_id, "- always | place:quan-da-bien-mat | - | Không còn quán này.\n")
    rows = client.get(f"/api/rooms/{room_id}/knowledge", headers=headers).json()["observations"]
    assert rows[0]["subject_kind"] == "unknown"
    assert rows[0]["subject_label"] == "place:quan-da-bien-mat"


def test_both_member_subject_spellings_group_onto_one_person(api_client_room):
    """The seed installer writes the nickname; `tools._memo_subject` writes the
    folded display name. Both are in the wild and both are the same person."""
    client, headers, room_id, _m = api_client_room
    from app.db import get_db
    from app import accounts
    from app.models import Room
    with get_db().session() as s:
        accounts.add_unclaimed(s, s.get(Room, room_id),
                               display_name="Giang Hoàng", nickname="nhim")
    _write_obs(room_id,
               "- always | member:nhim | - | Thích bún riêu.\n"
               "- always | member:giang-hoang | - | Hay đổi ý.\n")
    rows = client.get(f"/api/rooms/{room_id}/knowledge", headers=headers).json()["observations"]
    assert {r["subject_key"] for r in rows} == {"member:nhim"}
    assert {r["subject_label"] for r in rows} == {"Giang Hoàng"}


def test_knowledge_is_room_scoped(api_client_room, db, monkeypatch):
    client, headers, room_id, _m = api_client_room
    other = client.post("/api/rooms/create", json={
        "room_name": "Khác", "display_name": "Ngoài", "nickname": "ngoai", "pin": "1234"}).json()
    r = client.get(f"/api/rooms/{room_id}/knowledge",
                   headers={"Authorization": f"Bearer {other['token']}"})
    assert r.status_code == 403


# ================================================================ places: write

def test_create_place_derives_the_slug(api_client_room):
    client, headers, room_id, _m = api_client_room
    r = client.post(f"/api/rooms/{room_id}/places", headers=headers,
                    json={"name": "Cơm gà Thịnh Lơ", "tags": ["com"], "walkable": True})
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "com-ga-thinh-lo"


def test_create_place_reports_a_collision_rather_than_a_second_row(api_client_room):
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Cơm gà Thịnh Lơ")
    r = client.post(f"/api/rooms/{room_id}/places", headers=headers,
                    json={"name": "cơm gà thịnh lơ"})
    assert r.status_code == 409
    assert "Cơm gà Thịnh Lơ" in r.json()["detail"]


def test_renaming_a_place_keeps_its_slug_and_its_notes(api_client_room):
    """The slug is the `place:` subject in observations.md. Recomputing it on
    rename would silently detach every note about the restaurant."""
    client, headers, room_id, _m = api_client_room
    p = _seed_place(room_id, "Quán Bé Bự")
    _write_obs(room_id, "- always | place:quan-be-bu | - | Đông lắm.\n")
    r = client.patch(f"/api/rooms/{room_id}/places/{p['id']}", headers=headers,
                     json={"name": "Bé Bự (mới)"})
    assert r.status_code == 200 and r.json()["changed"] is True
    data = client.get(f"/api/rooms/{room_id}/knowledge", headers=headers).json()
    row = next(x for x in data["places"] if x["id"] == p["id"])
    assert (row["name"], row["slug"]) == ("Bé Bự (mới)", "quan-be-bu")
    assert row["note_count"] == 1
    assert data["observations"][0]["subject_label"] == "Bé Bự (mới)"


def test_patch_place_distinguishes_omitted_from_cleared(api_client_room):
    client, headers, room_id, _m = api_client_room
    p = _seed_place(room_id, "Quán Bé Bự", phone="0912345678", walk_minutes=9,
                    closed_until=date(2026, 12, 1))
    # Omitting phone leaves it; an explicit null clears walk_minutes.
    r = client.patch(f"/api/rooms/{room_id}/places/{p['id']}", headers=headers,
                     json={"walk_minutes": None, "closed_until": None})
    assert r.status_code == 200
    row = next(x for x in client.get(f"/api/rooms/{room_id}/knowledge",
                                     headers=headers).json()["places"] if x["id"] == p["id"])
    assert row["phone"] == "0912345678"
    assert row["walk_minutes"] is None
    assert row["closed_until"] is None


def test_patch_place_rejects_an_empty_name(api_client_room):
    client, headers, room_id, _m = api_client_room
    p = _seed_place(room_id, "Quán Bé Bự")
    r = client.patch(f"/api/rooms/{room_id}/places/{p['id']}", headers=headers,
                     json={"name": "   "})
    assert r.status_code == 422


def test_deleting_a_place_hides_it_and_keeps_the_meal_link(api_client_room):
    client, headers, room_id, m = api_client_room
    p = _seed_place(room_id, "Quán Bé Bự")
    from app.db import get_db
    from app.models import Meal
    meal_id = _seed_meal(room_id, m["Linh"], [m["Linh"], m["Giang"]], total=100_000,
                         on=date(2026, 8, 1), place_id=p["id"])["meal_id"]

    assert client.delete(f"/api/rooms/{room_id}/places/{p['id']}",
                         headers=headers).status_code == 200

    row = next(x for x in client.get(f"/api/rooms/{room_id}/knowledge",
                                     headers=headers).json()["places"] if x["id"] == p["id"])
    assert row["active"] is False
    with get_db().session() as s:
        assert s.get(Meal, meal_id).place_id == p["id"]


def test_place_routes_are_room_scoped(api_client_room):
    client, headers, room_id, _m = api_client_room
    p = _seed_place(room_id, "Quán Bé Bự")
    other = client.post("/api/rooms/create", json={
        "room_name": "Khác", "display_name": "Ngoài", "nickname": "ngoai", "pin": "1234"}).json()
    h = {"Authorization": f"Bearer {other['token']}"}
    assert client.patch(f"/api/rooms/{room_id}/places/{p['id']}", headers=h,
                        json={"name": "x"}).status_code == 403
    # And a place id from another room is a 404, not a cross-room edit.
    assert client.patch(f"/api/rooms/{other['room_id']}/places/{p['id']}", headers=h,
                        json={"name": "x"}).status_code == 404


# ========================================================== observations: write

def test_create_observation_appends_a_well_formed_line(api_client_room):
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    r = client.post(f"/api/rooms/{room_id}/observations", headers=headers, json={
        "subject": "place:quan-be-bu", "text": "Phải gọi trước.",
        "standing": True, "gate_kind": "order-by", "gate_at": "11:30"})
    assert r.status_code == 200, r.text
    assert _read_obs(room_id).splitlines()[-1] == (
        "- always | place:quan-be-bu | order-by@11:30 | Phải gọi trước.")
    assert r.json()["etag"] == obs.file_etag(room_id)


def test_a_dated_observation_defaults_to_today(api_client_room):
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    client.post(f"/api/rooms/{room_id}/observations", headers=headers, json={
        "subject": "place:quan-be-bu", "text": "Hôm nay hết gà."})
    from app.clock import today_ict
    assert obs.load(room_id)[0].when == today_ict()


def test_newlines_in_a_note_cannot_split_the_line(api_client_room):
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    client.post(f"/api/rooms/{room_id}/observations", headers=headers, json={
        "subject": "place:quan-be-bu", "text": "Dòng một\nDòng hai"})
    assert len(obs.load(room_id)) == 1
    assert obs.load(room_id)[0].text == "Dòng một Dòng hai"


def test_an_identical_note_is_a_no_op_not_a_second_line(api_client_room):
    """Two byte-identical lines share a content-derived `line_id`, so neither could
    be addressed again — and the second one says nothing new anyway."""
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    body = {"subject": "place:quan-be-bu", "text": "Phải gọi trước.", "standing": True}
    first = client.post(f"/api/rooms/{room_id}/observations", headers=headers, json=body)
    second = client.post(f"/api/rooms/{room_id}/observations", headers=headers, json=body)
    assert first.json()["already_existed"] is False
    assert second.json()["already_existed"] is True
    assert first.json()["id"] == second.json()["id"]
    assert len(obs.load(room_id)) == 1
    # And the room is not told twice about one fact.
    msgs = client.get(f"/api/rooms/{room_id}/messages", headers=headers).json()["messages"]
    assert len([m for m in msgs if m["body"].startswith("📓")]) == 1


def test_editing_a_note_into_a_copy_of_another_is_refused(api_client_room):
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    _write_obs(room_id,
               "- always | place:quan-be-bu | - | Ghi nhớ A.\n"
               "- always | place:quan-be-bu | - | Ghi nhớ B.\n")
    target = next(o for o in obs.load(room_id) if o.text == "Ghi nhớ B.")
    r = client.patch(f"/api/rooms/{room_id}/observations/{target.line_id}", headers=headers,
                     json={"etag": obs.file_etag(room_id), "text": "Ghi nhớ A."})
    assert r.status_code == 422
    assert {o.text for o in obs.load(room_id)} == {"Ghi nhớ A.", "Ghi nhớ B."}


def test_an_unresolvable_subject_is_refused(api_client_room):
    client, headers, room_id, _m = api_client_room
    r = client.post(f"/api/rooms/{room_id}/observations", headers=headers, json={
        "subject": "place:khong-ton-tai", "text": "x"})
    assert r.status_code == 422
    assert obs.load(room_id) == []


def test_a_bad_gate_is_refused_not_silently_dropped(api_client_room):
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    r = client.post(f"/api/rooms/{room_id}/observations", headers=headers, json={
        "subject": "place:quan-be-bu", "text": "x", "gate_kind": "order-by",
        "gate_at": "25:00"})
    assert r.status_code == 422
    assert obs.load(room_id) == []


def test_an_empty_note_is_refused(api_client_room):
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    r = client.post(f"/api/rooms/{room_id}/observations", headers=headers, json={
        "subject": "place:quan-be-bu", "text": "   "})
    assert r.status_code == 422


def test_patch_observation_edits_one_line(api_client_room):
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    _write_obs(room_id, "# tay\n- always | place:quan-be-bu | busy@12:00 | Đông lúc 12h.\n")
    line_id = obs.load(room_id)[0].line_id
    r = client.patch(f"/api/rooms/{room_id}/observations/{line_id}", headers=headers, json={
        "etag": obs.file_etag(room_id), "text": "Đông từ trưa.",
        "gate_kind": "order-by", "gate_at": "11:30"})
    assert r.status_code == 200, r.text
    lines = _read_obs(room_id).splitlines()
    assert lines[0] == "# tay"          # the comment survives the edit
    assert lines[1] == "- always | place:quan-be-bu | order-by@11:30 | Đông từ trưa."


def test_patch_observation_can_clear_a_gate_and_flip_to_a_dated_line(api_client_room):
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    _write_obs(room_id, "- always | place:quan-be-bu | busy@12:00 | Đông lúc 12h.\n")
    line_id = obs.load(room_id)[0].line_id
    r = client.patch(f"/api/rooms/{room_id}/observations/{line_id}", headers=headers, json={
        "etag": obs.file_etag(room_id), "standing": False, "when": "2026-08-10",
        "gate_kind": None})
    assert r.status_code == 200, r.text
    assert _read_obs(room_id).strip() == (
        "- 2026-08-10 | place:quan-be-bu | - | Đông lúc 12h.")


def test_a_stale_etag_refuses_the_write_and_changes_nothing(api_client_room):
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    _write_obs(room_id, "- always | place:quan-be-bu | - | Ghi nhớ gốc.\n")
    line_id = obs.load(room_id)[0].line_id
    before = _read_obs(room_id)
    r = client.patch(f"/api/rooms/{room_id}/observations/{line_id}", headers=headers,
                     json={"etag": "stale-etag-000", "text": "Bị ghi đè"})
    assert r.status_code == 409
    assert _read_obs(room_id) == before

    d = client.delete(f"/api/rooms/{room_id}/observations/{line_id}",
                      headers=headers, params={"etag": "stale-etag-000"})
    assert d.status_code == 409
    assert _read_obs(room_id) == before


def test_delete_observation_removes_one_line_and_keeps_the_rest(api_client_room):
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    _write_obs(room_id,
               "# tay\n"
               "- rác không đọc được\n"
               "- always | place:quan-be-bu | - | Xoá cái này.\n"
               "- always | place:quan-be-bu | - | Giữ cái này.\n")
    target = next(o for o in obs.load(room_id) if o.text == "Xoá cái này.")
    r = client.delete(f"/api/rooms/{room_id}/observations/{target.line_id}",
                      headers=headers, params={"etag": obs.file_etag(room_id)})
    assert r.status_code == 200
    after = _read_obs(room_id)
    assert "# tay" in after and "- rác không đọc được" in after
    assert "Xoá cái này." not in after and "Giữ cái này." in after


def test_patching_a_vanished_line_is_a_404(api_client_room):
    client, headers, room_id, _m = api_client_room
    _write_obs(room_id, "")
    r = client.patch(f"/api/rooms/{room_id}/observations/deadbeef1234", headers=headers,
                     json={"etag": obs.file_etag(room_id), "text": "x"})
    assert r.status_code == 404


# =============================================================== memory: write

def _two_memory_sections(room_id):
    from app import memory as mem
    mem.append_summary(room_id, summary_text="- Mục cũ", through_id=10,
                       through_at="2026-07-01T12:00:00", header="Tóm tắt")
    mem.append_summary(room_id, summary_text="- Mục mới", through_id=20,
                       through_at="2026-08-01T12:00:00", header="Tóm tắt")


def test_patch_memory_section(api_client_room):
    client, headers, room_id, _m = api_client_room
    _two_memory_sections(room_id)
    from app import memory as mem
    r = client.patch(f"/api/rooms/{room_id}/memory/sections/0", headers=headers,
                     json={"etag": mem.file_etag(room_id), "body": "- Đã sửa"})
    assert r.status_code == 200, r.text
    secs = client.get(f"/api/rooms/{room_id}/knowledge",
                      headers=headers).json()["memory"]["sections"]
    assert [s["body"] for s in secs] == ["- Đã sửa", "- Mục mới"]


def test_memory_section_write_refuses_a_stale_etag(api_client_room):
    client, headers, room_id, _m = api_client_room
    _two_memory_sections(room_id)
    r = client.patch(f"/api/rooms/{room_id}/memory/sections/0", headers=headers,
                     json={"etag": "nope", "body": "- Đã sửa"})
    assert r.status_code == 409
    from app import memory as mem
    assert mem.parse_sections(room_id)[0]["body"] == "- Mục cũ"


def test_memory_section_write_refuses_a_smuggled_heading(api_client_room):
    client, headers, room_id, _m = api_client_room
    _two_memory_sections(room_id)
    from app import memory as mem
    r = client.patch(f"/api/rooms/{room_id}/memory/sections/0", headers=headers,
                     json={"etag": mem.file_etag(room_id), "body": "- a\n## Mục lạ\n- b"})
    assert r.status_code == 422
    assert len(mem.parse_sections(room_id)) == 2


def test_delete_memory_section_keeps_the_watermark(api_client_room):
    client, headers, room_id, _m = api_client_room
    _two_memory_sections(room_id)
    from app import memory as mem
    r = client.delete(f"/api/rooms/{room_id}/memory/sections/0", headers=headers,
                      params={"etag": mem.file_etag(room_id)})
    assert r.status_code == 200
    body = client.get(f"/api/rooms/{room_id}/knowledge", headers=headers).json()["memory"]
    assert [s["body"] for s in body["sections"]] == ["- Mục mới"]
    assert body["watermark"]["through_id"] == 20


def test_deleting_a_missing_memory_section_is_a_404(api_client_room):
    client, headers, room_id, _m = api_client_room
    from app import memory as mem
    r = client.delete(f"/api/rooms/{room_id}/memory/sections/3", headers=headers,
                      params={"etag": mem.file_etag(room_id)})
    assert r.status_code == 404


# ============================================================== trail + events

def test_every_edit_leaves_a_trail_in_the_room(api_client_room):
    """Memory steers suggestions, so a member changing it is not a private act."""
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    client.post(f"/api/rooms/{room_id}/observations", headers=headers, json={
        "subject": "place:quan-be-bu", "text": "Phải gọi trước.", "standing": True})
    msgs = client.get(f"/api/rooms/{room_id}/messages", headers=headers).json()["messages"]
    trail = [m for m in msgs if m["body"].startswith("📓")]
    assert len(trail) == 1
    assert "Linh" in trail[0]["body"] and "Quán Bé Bự" in trail[0]["body"]
    assert "Phải gọi trước." in trail[0]["body"]


def test_a_no_op_place_save_says_nothing(api_client_room):
    client, headers, room_id, _m = api_client_room
    p = _seed_place(room_id, "Quán Bé Bự", phone="0912345678")
    r = client.patch(f"/api/rooms/{room_id}/places/{p['id']}", headers=headers,
                     json={"phone": "0912345678"})
    assert r.status_code == 200 and r.json()["changed"] is False
    msgs = client.get(f"/api/rooms/{room_id}/messages", headers=headers).json()["messages"]
    assert not [m for m in msgs if m["body"].startswith("📓")]


@pytest.mark.asyncio
async def test_writes_publish_knowledge_changed(api_client_room, monkeypatch):
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    seen = []
    orig = hub.publish

    async def spy(rid, ev):
        if rid == room_id and ev.get("type") == "knowledge:changed":
            seen.append(ev)
        await orig(rid, ev)

    monkeypatch.setattr(hub, "publish", spy)
    client.post(f"/api/rooms/{room_id}/observations", headers=headers, json={
        "subject": "place:quan-be-bu", "text": "x", "standing": True})
    assert seen, "expected knowledge:changed after an observation write"


@pytest.mark.asyncio
async def test_committing_a_memo_publishes_knowledge_changed(api_client_room, monkeypatch):
    """The memo card writes observations.md too — an open panel must notice."""
    client, headers, room_id, _m = api_client_room
    _seed_place(room_id, "Quán Bé Bự")
    from app.db import get_db
    from app import memos
    with get_db().session() as s:
        memo = memos.create(s, room_id, action="add", subject="place:quan-be-bu",
                            subject_label="Quán Bé Bự", text="Chậm.")
        memo_id = memo.id

    seen = []
    orig = hub.publish

    async def spy(rid, ev):
        if rid == room_id and ev.get("type") == "knowledge:changed":
            seen.append(ev)
        await orig(rid, ev)

    monkeypatch.setattr(hub, "publish", spy)
    r = client.post(f"/api/rooms/{room_id}/memos/{memo_id}/commit", headers=headers)
    assert r.status_code == 200, r.text
    assert seen


# ======================================================== renaming a place's slug
#
# `slug` is a foreign key held by things that are not the database, so this route
# is a small migration rather than a field edit. `PlacePatchIn` still has no
# `slug`, which is what keeps `places.EDITABLE` literally true.

def _rename(client, headers, room_id, place_id, slug):
    return client.post(f"/api/rooms/{room_id}/places/{place_id}/slug",
                       json={"slug": slug}, headers=headers)


def test_renaming_a_slug_keeps_every_note_attached_to_the_place(api_client_room):
    """The load-bearing one: after a rename the notes still resolve to the place,
    the count is unchanged, and nothing became an orphan."""
    client, headers, room_id, m = api_client_room
    p = _seed_place(room_id, "Bún chả rửa xe Nam Đồng")
    _write_obs(room_id,
               "- always | place:bun-cha-rua-xe-nam-dong | busy@12:00 | Đông lúc 12h.\n"
               "- 2026-08-10 | place:bun-cha-rua-xe-nam-dong | - | Hết chả.\n")

    before = client.get(f"/api/rooms/{room_id}/knowledge", headers=headers).json()
    assert next(x for x in before["places"] if x["id"] == p["id"])["note_count"] == 2

    r = _rename(client, headers, room_id, p["id"], "bun-cha-huong-lien")
    assert r.status_code == 200, r.text
    assert r.json()["notes_moved"] == 2

    after = client.get(f"/api/rooms/{room_id}/knowledge", headers=headers).json()
    place = next(x for x in after["places"] if x["id"] == p["id"])
    assert place["slug"] == "bun-cha-huong-lien"
    assert place["former_slugs"] == ["bun-cha-rua-xe-nam-dong"]
    assert place["note_count"] == 2
    labels = {o["subject_label"] for o in after["observations"]}
    assert labels == {"Bún chả rửa xe Nam Đồng"}          # still resolved, not ⚠️
    assert not [o for o in after["observations"] if o["subject_kind"] == "unknown"]


def test_a_comment_and_a_malformed_line_survive_the_rename(api_client_room):
    client, headers, room_id, m = api_client_room
    p = _seed_place(room_id, "Quán Bé Bự")
    _write_obs(room_id, "# đừng xoá\n"
                        "- always | place:quan-be-bu | - | Ăn được.\n"
                        "- rubbish that does not parse\n")

    _rename(client, headers, room_id, p["id"], "be-bu")

    lines = _read_obs(room_id).splitlines()
    assert lines[0] == "# đừng xoá"
    assert lines[2] == "- rubbish that does not parse"
    assert lines[1] == "- always | place:be-bu | - | Ăn được."


def test_a_memo_drafted_before_the_rename_commits_onto_the_new_slug(api_client_room):
    """Modelled on the card that was sitting pending in production."""
    client, headers, room_id, m = api_client_room
    from app import memos
    from app.db import get_db

    p = _seed_place(room_id, "Bún riêu cô Trang")
    with get_db().session() as s:
        memo = memos.create(s, room_id, action="add", subject="place:bun-rieu-co-trang",
                            subject_label="Bún riêu cô Trang",
                            text="Gọi giá tính tiền", when=date(2026, 8, 14))
        memo_id = memo.id

    assert _rename(client, headers, room_id, p["id"],
                   "bun-rieu-truong-sa").json()["memos_moved"] == 1

    r = client.post(f"/api/rooms/{room_id}/memos/{memo_id}/commit", headers=headers)
    assert r.status_code == 200, r.text

    data = client.get(f"/api/rooms/{room_id}/knowledge", headers=headers).json()
    note = next(o for o in data["observations"] if o["text"] == "Gọi giá tính tiền")
    assert note["subject"] == "place:bun-rieu-truong-sa"
    assert note["subject_kind"] == "place"                 # attached, not an orphan


def test_renaming_onto_a_taken_slug_is_a_409_naming_the_other_place(api_client_room):
    client, headers, room_id, m = api_client_room
    p = _seed_place(room_id, "Bún chả rửa xe")
    _seed_place(room_id, "Bún chả Hương Liên")

    r = _rename(client, headers, room_id, p["id"], "bun-cha-huong-lien")
    assert r.status_code == 409
    assert "Bún chả Hương Liên" in r.json()["detail"]


def test_an_unusable_slug_is_a_422_and_a_missing_place_a_404(api_client_room):
    client, headers, room_id, m = api_client_room
    p = _seed_place(room_id, "Quán Bé Bự")
    assert _rename(client, headers, room_id, p["id"], "!!!").status_code == 422
    assert _rename(client, headers, room_id, 9999, "x").status_code == 404


def test_renaming_to_the_same_slug_changes_nothing_and_says_nothing(api_client_room):
    client, headers, room_id, m = api_client_room
    p = _seed_place(room_id, "Quán Bé Bự")
    _write_obs(room_id, "- always | place:quan-be-bu | - | Ăn được.\n")
    before = _read_obs(room_id)

    r = _rename(client, headers, room_id, p["id"], "Quán Bé Bự")
    assert r.status_code == 200 and r.json()["changed"] is False
    assert _read_obs(room_id) == before

    msgs = client.get(f"/api/rooms/{room_id}/messages", headers=headers).json()
    assert not [x for x in msgs["messages"] if "identifier" in (x["body"] or "")]


def test_the_rename_leaves_a_trail_naming_both_slugs_and_what_moved(api_client_room):
    client, headers, room_id, m = api_client_room
    p = _seed_place(room_id, "Quán Bé Bự")
    _write_obs(room_id, "- always | place:quan-be-bu | - | Ăn được.\n")

    _rename(client, headers, room_id, p["id"], "be-bu")

    msgs = client.get(f"/api/rooms/{room_id}/messages", headers=headers).json()
    trail = [x["body"] for x in msgs["messages"] if "identifier" in (x["body"] or "")]
    assert len(trail) == 1
    assert "quan-be-bu → be-bu" in trail[0]
    assert "1 note moved" in trail[0]


@pytest.mark.asyncio
async def test_the_rename_publishes_knowledge_changed(api_client_room, monkeypatch):
    """Every affected line's content-derived id changed, so an open panel is
    holding ids that no longer exist."""
    client, headers, room_id, m = api_client_room
    seen = []
    orig = hub.publish

    async def spy(rid, ev):
        if rid == room_id and ev.get("type") == "knowledge:changed":
            seen.append(ev)
        await orig(rid, ev)

    monkeypatch.setattr(hub, "publish", spy)
    p = _seed_place(room_id, "Quán Bé Bự")
    _rename(client, headers, room_id, p["id"], "be-bu")
    assert seen, "expected knowledge:changed after a rename"


def test_slug_is_still_not_a_patchable_field(api_client_room):
    """`PATCH /places` must stay unable to rename — that is what makes
    `places.EDITABLE` a true statement rather than a comment."""
    client, headers, room_id, m = api_client_room
    p = _seed_place(room_id, "Quán Bé Bự")
    client.patch(f"/api/rooms/{room_id}/places/{p['id']}",
                 json={"name": "Bé Bự", "slug": "be-bu"}, headers=headers)
    data = client.get(f"/api/rooms/{room_id}/knowledge", headers=headers).json()
    assert next(x for x in data["places"] if x["id"] == p["id"])["slug"] == "quan-be-bu"


def test_a_note_left_under_a_former_slug_still_resolves_to_the_place(api_client_room):
    """Only a hand edit or a restored backup can produce one — `rename_slug`
    rewrites the live file — but rendering it as `⚠️ unknown` would hide which
    restaurant it belongs to from the one person who could fix it."""
    client, headers, room_id, m = api_client_room
    p = _seed_place(room_id, "Quán Bé Bự")
    _rename(client, headers, room_id, p["id"], "be-bu")
    _write_obs(room_id, "- always | place:quan-be-bu | - | Ăn được.\n")   # stale slug

    data = client.get(f"/api/rooms/{room_id}/knowledge", headers=headers).json()
    note = data["observations"][0]
    assert note["subject_kind"] == "place"
    assert note["subject_label"] == "Quán Bé Bự"
    assert note["subject_key"] == "place:be-bu"        # canonical: the current slug


def test_the_snapshot_reports_pending_cards_so_the_dialog_can_say_what_moves(api_client_room):
    client, headers, room_id, m = api_client_room
    from app import memos
    from app.db import get_db

    p = _seed_place(room_id, "Quán Bé Bự")
    with get_db().session() as s:
        kept = memos.create(s, room_id, action="add", subject="place:quan-be-bu",
                            subject_label="Quán Bé Bự", text="pending one")
        gone = memos.create(s, room_id, action="add", subject="place:quan-be-bu",
                            subject_label="Quán Bé Bự", text="cancelled one")
        memos.cancel(s, gone.id, room_id)
        assert kept.id != gone.id

    data = client.get(f"/api/rooms/{room_id}/knowledge", headers=headers).json()
    row = next(x for x in data["places"] if x["id"] == p["id"])
    assert row["pending_memo_count"] == 1              # the cancelled one does not move
    assert row["former_slugs"] == []
