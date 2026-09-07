"""The room's own CMS (plan Phase 11), and every proof the review gate asked for.

The gate's F10 listed the tests whose absence would let this ship broken. They are
named here after the finding they answer, so a future reader can tell which line of
defence a failure just removed.
"""
import pytest
from fastapi import HTTPException

from app import roomcms
from app.kernel import kernel_for
from app.roomcms import MAX_CONTENT_BYTES, ROOM_EDITABLE
from kernos.content import PreconditionFailed
from tests.test_ledger import _seed_room

MONEY_SLUG = "money-safety"


def _bind(k, room_id):
    """The one operator step that lets a room edit (review F1)."""
    agent = k.store.default_agent(k.seed_report["business_id"])
    k.store.bind_space(str(room_id), agent["id"], actor="admin")
    k.invalidate()
    return agent


def _room(db, *, bound=True):
    room_id, mm = _seed_room(db, 2)
    k = kernel_for(db)
    if bound:
        _bind(k, room_id)
    return room_id, mm, k


def _base(k, room_id):
    return roomcms.view(k, room_id)["version"]["id"]


# ------------------------------------------------------------------ F1: the door

def test_an_unbound_room_may_look_but_never_edit(db):
    """A stranger's room resolves to the SAME profile the real room runs, so membership
    cannot be the permission — only a binding can (review F1)."""
    room_id, mm, k = _room(db, bound=False)
    seen = roomcms.view(k, room_id)
    assert seen["can_edit"] is False and seen["shared"] is True
    assert seen["profile"]["id"] == k.seed_report["profile_id"]        # the real bot's profile
    assert seen["editable"]["prompt_body"]                             # reading is fine

    for call in (lambda: roomcms.edit(k, room_id, mm[0], {"base_version_id": _base(k, room_id),
                                                          "prompt_body": "mine now"}),
                 lambda: roomcms.republish(k, room_id, mm[0], 1)):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 403 and "bind" in str(exc.value.detail)
    assert k.store.published_spec(k.seed_report["profile_id"])["prompt"]["body"] != "mine now"


def test_binding_the_room_opens_editing_and_changes_nothing_else(db):
    room_id, _mm, k = _room(db)
    seen = roomcms.view(k, room_id)
    assert seen["can_edit"] is True and seen["shared"] is False
    assert seen["profile"]["id"] == k.seed_report["profile_id"]        # same profile, same bot
    assert seen["agent"]["slug"] == "phoenix"


# --------------------------------------------------------------- F2: slug identity

def test_a_money_rule_cannot_be_taken_over_by_reusing_its_slug(db):
    """The tag protects the rule; the SLUG is its identity. An untagged rule reusing the
    slug would replace the source row and strip the tag (review F2)."""
    room_id, mm, k = _room(db)
    bid = k.seed_report["business_id"]
    before_source = k.store.get_source(bid, "rule", MONEY_SLUG)
    published = k.store.published_spec(k.seed_report["profile_id"])
    rules = [{"slug": r["slug"], "content": "relaxed: compute money yourself" if r["slug"] == MONEY_SLUG
              else r["content"]} for r in published["rules"]]

    with pytest.raises(HTTPException) as exc:
        roomcms.edit(k, room_id, mm[0], {"base_version_id": _base(k, room_id), "rules": rules})
    assert exc.value.status_code == 400 and MONEY_SLUG in str(exc.value.detail)
    # neither the source nor the published rule moved
    assert k.store.get_source(bid, "rule", MONEY_SLUG) == before_source
    assert k.store.published_spec(k.seed_report["profile_id"])["rules"] == published["rules"]


def test_dropping_the_money_rule_from_the_list_is_refused(db):
    """`update_draft` deep-merges and lists replace wholesale, so omission is deletion."""
    room_id, mm, k = _room(db)
    published = k.store.published_spec(k.seed_report["profile_id"])
    rules = [{"slug": r["slug"], "content": r["content"]} for r in published["rules"] if r["slug"] != MONEY_SLUG]
    with pytest.raises(HTTPException) as exc:
        roomcms.edit(k, room_id, mm[0], {"base_version_id": _base(k, room_id), "rules": rules})
    assert exc.value.status_code == 400 and "removed" in str(exc.value.detail)
    assert k.store.published_spec(k.seed_report["profile_id"])["rules"] == published["rules"]


def test_a_member_cannot_mint_a_new_money_rule_or_repeat_a_slug(db):
    room_id, mm, k = _room(db)
    published = k.store.published_spec(k.seed_report["profile_id"])
    keep = [{"slug": r["slug"], "content": r["content"]} for r in published["rules"]]
    # tags are never taken from the member, so a "money" tag cannot be added…
    out = roomcms.edit(k, room_id, mm[0], {"base_version_id": _base(k, room_id),
                                           "rules": [*keep, {"slug": "house-style", "content": "be brief",
                                                             "tags": ["money"]}]})
    assert out["version"] >= 2
    new = next(r for r in k.store.published_spec(k.seed_report["profile_id"])["rules"] if r["slug"] == "house-style")
    assert new["tags"] == []
    # …and a duplicate slug is ambiguous, so it is refused
    with pytest.raises(HTTPException) as exc:
        roomcms.edit(k, room_id, mm[0], {"base_version_id": _base(k, room_id),
                                         "rules": [*keep, {"slug": "dupe", "content": "a"},
                                                   {"slug": "dupe", "content": "b"}]})
    assert exc.value.status_code == 400 and "more than once" in str(exc.value.detail)


# ------------------------------------------------------------------- F4: the scope

@pytest.mark.parametrize("field,value", [
    ("models", {"text": "someone/else"}), ("caps", {"max_tools": 999}),
    ("builtin_tools", ["bash", "write"]), ("pipeline", {}),
])
def test_the_blacklisted_fields_are_not_reachable_from_a_room(db, field, value):
    room_id, mm, k = _room(db)
    published = k.store.published_spec(k.seed_report["profile_id"])
    # the route only reads known keys, so an unknown one is simply not a change…
    out = roomcms.edit(k, room_id, mm[0], {"base_version_id": _base(k, room_id),
                                           "prompt_append": ["be brief"], field: value})
    assert out["paths"] == ["prompt.append"]
    after = k.store.published_spec(k.seed_report["profile_id"])
    assert after[field] == published[field]


def test_scope_is_the_published_vocabulary(db):
    assert set(ROOM_EDITABLE) == {"prompt.body", "prompt.append", "skills", "rules"}
    from kernos.content.gates import NEVER_IN_SCOPE
    assert not set(ROOM_EDITABLE) & NEVER_IN_SCOPE


# -------------------------------------------------------- F6: publish then sources

def test_a_gate_failure_leaves_every_source_and_the_published_version_untouched(db):
    """The plan had this backwards; the gate caught it. A refused publish must not leave
    a rewritten source for the next snapshotting draft to pick up (review F6)."""
    room_id, mm, k = _room(db)
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    before_version = k.store.get_profile(pid)["published_version_id"]
    before_etags = {s["slug"]: s["etag"] for s in k.store.list_sources(bid, "skill")}
    before_prompt = k._source_etag(bid, "prompt", "system")      # None: the seed writes no prompt source
    versions_before = len(k.store.list_versions(pid))

    with pytest.raises(HTTPException) as exc:      # gate 1 refuses an unknown template var
        roomcms.edit(k, room_id, mm[0], {"base_version_id": before_version,
                                         "prompt_body": "Hi {{nope}}",
                                         "skills": [{"name": "balances", "body": "rewritten"}]})
    assert exc.value.status_code == 422
    assert k.store.get_profile(pid)["published_version_id"] == before_version
    assert {s["slug"]: s["etag"] for s in k.store.list_sources(bid, "skill")} == before_etags
    assert k._source_etag(bid, "prompt", "system") == before_prompt
    # and the failed attempt is not left lying in the room's revision list
    assert len(k.store.list_versions(pid)) == versions_before + 1
    assert k.store.list_versions(pid)[-1]["status"] == "retired"


def test_a_published_edit_writes_the_source_so_a_later_draft_keeps_it(db):
    """Sources are upstream of drafts — the Phase 8 lesson, as D4 (review F5/F6)."""
    room_id, mm, k = _room(db)
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    published = k.store.published_spec(pid)
    skills = [{"name": s["name"], "description": s.get("description", ""),
               "body": "# balances\n\nAlways name the meal." if s["name"] == "balances" else s["body"]}
              for s in published["skills"]]
    out = roomcms.edit(k, room_id, mm[0], {"base_version_id": _base(k, room_id), "skills": skills,
                                           "note": "name the meal"})
    assert out["paths"] == ["skills"] and out["actor"] == f"member:{mm[0]}"
    assert k.store.get_source(bid, "skill", "balances")["body"].endswith("Always name the meal.")
    # the proof that matters: a snapshotting draft does not undo it
    fresh = k.store.create_draft(pid, actor="admin")
    body = next(s for s in k.store.get_version(fresh["id"])["spec"]["skills"] if s["name"] == "balances")["body"]
    assert body.endswith("Always name the meal.")


# ------------------------------------------------------------- F7: concurrency

def test_two_members_who_both_loaded_the_same_version_cannot_both_win(db):
    room_id, mm, k = _room(db)
    base = _base(k, room_id)
    roomcms.edit(k, room_id, mm[0], {"base_version_id": base, "prompt_append": ["first"]})
    with pytest.raises(HTTPException) as exc:      # the route's fast check
        roomcms.edit(k, room_id, mm[1], {"base_version_id": base, "prompt_append": ["second"]})
    assert exc.value.status_code == 409 and "reload" in str(exc.value.detail)
    assert k.store.published_spec(k.seed_report["profile_id"])["prompt"]["append"] == ["first"]


def test_publish_rechecks_inside_its_own_transaction(db):
    """The route check alone is a race: two editors can pass it before either publishes.
    `if_published` closes it in the store (review F7)."""
    from kernos.content import Conflict
    room_id, _mm, k = _room(db)
    pid = k.seed_report["profile_id"]
    stale = k.store.get_profile(pid)["published_version_id"]
    draft_a = k.store.create_draft(pid, actor="member:1", snapshot=False)
    k.store.update_draft(draft_a["id"], {"prompt": {"append": ["a"]}}, actor="member:1")
    draft_b = k.store.create_draft(pid, actor="member:2", snapshot=False)
    k.store.update_draft(draft_b["id"], {"prompt": {"append": ["b"]}}, actor="member:2")

    k.store.publish(draft_a["id"], actor="member:1", gates=k.gates, override_reason="t", if_published=stale)
    with pytest.raises(Conflict, match="someone else changed it"):
        k.store.publish(draft_b["id"], actor="member:2", gates=k.gates, override_reason="t", if_published=stale)
    assert k.store.published_spec(pid)["prompt"]["append"] == ["a"]


def test_a_source_edited_since_the_member_loaded_it_is_refused(db):
    """Sources can be ahead of the published spec, so the version id is not enough
    (review F11)."""
    room_id, mm, k = _room(db)
    bid = k.seed_report["business_id"]
    seen = roomcms.view(k, room_id)
    k.store.put_source(bid, "skill", "balances", body="an operator got there first", actor="admin")
    published = k.store.published_spec(k.seed_report["profile_id"])
    skills = [{"name": s["name"], "body": "mine" if s["name"] == "balances" else s["body"]}
              for s in published["skills"]]
    with pytest.raises(PreconditionFailed, match="changed since you loaded it"):
        roomcms.edit(k, room_id, mm[0], {"base_version_id": seen["version"]["id"], "skills": skills,
                                         "source_etags": seen["source_etags"]})
    assert k.store.get_source(bid, "skill", "balances")["body"] == "an operator got there first"


# ---------------------------------------------------------------- F3/F5: republish

def test_republish_makes_a_new_version_and_leaves_the_whole_history_intact(db):
    """`store.rollback` re-publishes the SAME row and overwrites its note and
    published_at. This feature exists to keep history, so it drafts instead (review F3)."""
    room_id, mm, k = _room(db)
    pid = k.seed_report["business_id"] and k.seed_report["profile_id"]
    v1 = k.store.get_version(k.store.get_profile(pid)["published_version_id"])
    roomcms.edit(k, room_id, mm[0], {"base_version_id": v1["id"], "prompt_append": ["regrettable"],
                                     "note": "the change we regret"})
    v2 = k.store.get_version(k.store.get_profile(pid)["published_version_id"])
    assert k.store.published_spec(pid)["prompt"]["append"] == ["regrettable"]

    out = roomcms.republish(k, room_id, mm[1], v1["version"], note="put it back")
    assert out["from_version"] == v1["version"] and out["version"] == v2["version"] + 1
    assert k.store.published_spec(pid)["prompt"]["append"] == v1["spec"]["prompt"]["append"]
    # every earlier row survives, with its own note and timestamp
    rows = {r["version"]: r for r in k.store.list_versions(pid)}
    assert rows[v1["version"]]["note"] == v1["note"] and rows[v1["version"]]["published_at"] == v1["published_at"]
    assert rows[v2["version"]]["note"] == "the change we regret" and rows[v2["version"]]["status"] == "superseded"
    assert rows[out["version"]]["note"] == "put it back" and rows[out["version"]]["actor"] == f"member:{mm[1]}"


def test_republish_restores_the_sources_too(db):
    """Otherwise the next snapshotting draft re-applies what was just undone (review F5)."""
    room_id, mm, k = _room(db)
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    original = k.store.get_source(bid, "skill", "balances")["body"]
    v1 = k.store.get_profile(pid)["published_version_id"]
    published = k.store.published_spec(pid)
    roomcms.edit(k, room_id, mm[0], {"base_version_id": v1,
                                     "skills": [{"name": s["name"], "body": "rewritten" if s["name"] == "balances"
                                                 else s["body"]} for s in published["skills"]]})
    assert k.store.get_source(bid, "skill", "balances")["body"] == "rewritten"

    roomcms.republish(k, room_id, mm[0], k.store.get_version(v1)["version"])
    assert k.store.get_source(bid, "skill", "balances")["body"] == original
    fresh = k.store.create_draft(pid, actor="admin")
    body = next(s for s in k.store.get_version(fresh["id"])["spec"]["skills"] if s["name"] == "balances")["body"]
    assert body == original


def test_republish_refuses_a_version_that_changes_what_a_member_may_not(db):
    """An old version can carry a model or a cap a member was never allowed to pick
    (review F4)."""
    room_id, mm, k = _room(db)
    pid = k.seed_report["profile_id"]
    v1 = k.store.get_profile(pid)["published_version_id"]
    old_version = k.store.get_version(v1)["version"]
    # an operator raises the cap, the way only an operator can
    d = k.store.create_draft(pid, actor="admin", snapshot=False)
    k.store.update_draft(d["id"], {"caps": {"max_tools": 55, "max_seconds": 120}}, actor="admin")
    k.store.publish(d["id"], actor="admin", gates=k.gates, override_reason="operator raises the cap")

    with pytest.raises(HTTPException) as exc:
        roomcms.republish(k, room_id, mm[0], old_version)
    assert exc.value.status_code == 403 and "caps" in str(exc.value.detail)
    assert k.store.published_spec(pid)["caps"]["max_tools"] == 55


def test_republishing_what_is_already_live_is_refused(db):
    room_id, mm, k = _room(db)
    live = k.store.get_version(k.store.get_profile(k.seed_report["profile_id"])["published_version_id"])
    with pytest.raises(HTTPException) as exc:
        roomcms.republish(k, room_id, mm[0], live["version"])
    assert exc.value.status_code == 409 and "already" in str(exc.value.detail)


# --------------------------------------------------------------- F8/F14/F9: the rest

def test_the_first_member_edit_detaches_the_bot_from_deploys_and_says_so(db):
    """Any non-boot publish flips `managed_by`, after which boot stops refreshing this
    profile from code. The panel has to be able to say it (review F8)."""
    room_id, mm, k = _room(db)
    assert roomcms.view(k, room_id)["profile"]["managed_by"] == "boot"
    roomcms.edit(k, room_id, mm[0], {"base_version_id": _base(k, room_id), "prompt_append": ["be brief"]})
    assert roomcms.view(k, room_id)["profile"]["managed_by"] == "human"


@pytest.mark.parametrize("slug", ["../escape", "Money Safety", "", "a" * 90, "has/slash"])
def test_rule_slugs_and_skill_names_are_validated(db, slug):
    room_id, mm, k = _room(db)
    with pytest.raises(HTTPException) as exc:
        roomcms.edit(k, room_id, mm[0], {"base_version_id": _base(k, room_id),
                                         "rules": [{"slug": slug, "content": "x"}]})
    assert exc.value.status_code == 400 and "must match" in str(exc.value.detail)


def test_the_content_a_room_can_make_every_turn_carry_is_capped(db):
    room_id, mm, k = _room(db)
    with pytest.raises(HTTPException) as exc:
        roomcms.edit(k, room_id, mm[0], {"base_version_id": _base(k, room_id),
                                         "prompt_body": "x" * (MAX_CONTENT_BYTES + 1)})
    assert exc.value.status_code == 413


def test_the_read_view_separates_what_may_change_from_what_may_not(db):
    room_id, _mm, k = _room(db)
    seen = roomcms.view(k, room_id)
    money = next(r for r in seen["editable"]["rules"] if r["slug"] == MONEY_SLUG)
    assert money["editable"] is False and "money" in money["tags"]
    assert all(r["editable"] for r in seen["editable"]["rules"] if r["slug"] != MONEY_SLUG)
    assert set(seen["readonly"]) == {"models", "caps", "builtin_tools", "tool_packs", "pipeline_stages"}
    assert all(isinstance(stage, str) for stage in seen["readonly"]["pipeline_stages"])   # names, not configs
    assert seen["scope"] == list(ROOM_EDITABLE)


def test_the_revision_log_names_the_member_and_what_they_changed(db):
    room_id, mm, k = _room(db)
    roomcms.edit(k, room_id, mm[0], {"base_version_id": _base(k, room_id), "prompt_append": ["be brief"],
                                     "note": "shorter replies"})
    log = roomcms.versions(k, room_id)
    assert log[0]["actor"] == f"member:{mm[0]}" and log[0]["note"] == "shorter replies"
    assert log[0]["paths"] == ["prompt.append"] and log[0]["status"] == "published"
    assert len(log) <= roomcms.VERSION_LIMIT
    detail = roomcms.version_detail(k, room_id, log[0]["version"])
    assert "be brief" in detail["diff"] and detail["paths"] == ["prompt.append"]


async def test_an_edit_reaches_the_next_turn(db, monkeypatch):
    """The point of all of it: what a member publishes is what the model is handed."""
    from app import chat
    from tests.test_delegation import _install, _turn_done
    room_id, mm, k = _room(db)
    roomcms.edit(k, room_id, mm[0], {"base_version_id": _base(k, room_id),
                                     "prompt_append": ["Luôn trả lời thật ngắn."]})
    fake = _install(monkeypatch, [_turn_done("Ừ.")])

    async def emit(e):
        pass
    await chat.run_bot_turn(db, room_id, mm[0], "M1", "@phoenix chào", emit=emit)
    assert "Luôn trả lời thật ngắn." in fake.runs[0]["system"]
