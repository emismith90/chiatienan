"""Booting this branch over a database that predates it (plan Phase 10.4).

Production runs `main`, which has no content plane at all: no ``kn_`` tables, no
businesses, no profiles, no agents. This is the deploy rehearsal — build a database the
way `main` leaves it, with real rows in it, then start the branch on top and check the
two things that decide whether a deploy is safe: **nothing that was there is disturbed**,
and **nothing new is switched on**.
"""
from datetime import date

import pytest
from sqlalchemy import create_engine, inspect

from app import chat, ledger
from app.db import Database
from app.kernel import Kernel, kernel_for
from app.models import Base, Member, Room, RoomMessage
from app.tools import ToolContext, build_tools
from tests.test_delegation import _install, _tool_names, _turn_done


@pytest.fixture
def prod_shaped(tmp_path):
    """A database as `main` leaves it: the app's own tables, real data, no ``kn_``."""
    url = f"sqlite:///{tmp_path}/prod.db"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    from ledger_core import bind as bind_ledger
    bind_ledger(engine)
    assert not [t for t in inspect(engine).get_table_names() if t.startswith("kn_")]

    db = Database(url)
    with db.session() as s:
        room = Room(name="Lunch", invite_token="tok")
        s.add(room)
        s.flush()
        members = [Member(room_id=room.id, display_name=f"M{i}", nickname=f"m{i}", pin=str(i)) for i in (1, 2, 3)]
        s.add_all(members)
        s.flush()
        ids = [m.id for m in members]
        ledger.record_meal(s, room_id=room.id, payer_member_id=ids[0], participants=ids,
                           total_amount=300_000, occurred_on=date(2026, 9, 1))
        chat.post_message(s, room.id, ids[0], "@phoenix ghi bữa trưa 300k", kind="text")
        chat.post_message(s, room.id, None, "Đã ghi #1.", kind="bot")
        room_id = room.id
    engine.dispose()
    return db, room_id, ids


def test_the_deploy_adds_the_content_plane_without_touching_what_was_there(prod_shaped):
    db, room_id, ids = prod_shaped
    with db.session() as s:
        before_messages = s.query(RoomMessage).count()
        before_edges = sorted((e.debtor, e.creditor, e.amount) for e in ledger.meal_edges(s, room_id))

    db.create_all()                                     # what the container does on start
    kernel = kernel_for(db)

    kn = {t for t in inspect(db.engine).get_table_names() if t.startswith("kn_")}
    assert len(kn) == 16 and "kn_agents" in kn and "kn_change_proposals" in kn
    # the room's own history and money are exactly as they were
    with db.session() as s:
        assert s.query(RoomMessage).count() == before_messages
        assert sorted((e.debtor, e.creditor, e.amount) for e in ledger.meal_edges(s, room_id)) == before_edges
        assert before_edges
        assert s.query(Member).filter(Member.room_id == room_id).count() == 3

    # the content plane seeded itself: two businesses, their agents, and the steward
    assert kernel.seed_report["actions"] and kernel.poker_report["actions"]
    agents = {a["slug"]: a for a in kernel.store.list_agents()}
    assert set(agents) == {"phoenix", "dealer", "steward"}
    assert agents["phoenix"]["is_default"] and agents["phoenix"]["delegates_to"] == []
    assert agents["steward"]["role"] == "sub" and agents["steward"]["capabilities"]["cms"] == ["read", "draft"]


async def test_after_the_deploy_the_room_runs_exactly_as_before(prod_shaped, monkeypatch):
    db, room_id, ids = prod_shaped
    db.create_all()
    kernel_for(db)

    # the tools the model is handed: the same nineteen, in the same order
    legacy = list(build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=ids[0])))
    fake = _install(monkeypatch, [_turn_done("Cả nhóm chia đều 100,000đ mỗi người.")])

    async def emit(e):
        pass
    reply = await chat.run_bot_turn(db, room_id, ids[0], "M1", "@phoenix ai nợ ai", emit=emit)
    assert _tool_names(fake.runs[0]) == legacy and len(legacy) == 19
    assert not any(n.startswith(("cms_", "ask_")) for n in legacy)
    assert reply.kind == "bot" and reply.body == "Cả nhóm chia đều 100,000đ mỗi người."
    # and the turn is now traced, which is the one thing that is new and additive
    traces = kernel_for(db).store.list_traces(str(room_id))
    assert len(traces) == 1 and traces[0]["summary"]["tools"] == []


def test_a_second_deploy_changes_nothing(prod_shaped):
    db, room_id, _ids = prod_shaped
    db.create_all()
    first = Kernel(db)
    published = first.store.published_spec(first.seed_report["profile_id"])
    versions = len(first.store.list_versions(first.seed_report["profile_id"]))

    db.create_all()                                     # redeploy: same image, same env
    second = Kernel(db)
    assert second.seed_report["actions"] == [] and second.poker_report["actions"] == []
    assert second.steward_report["actions"] == []
    assert second.store.published_spec(second.seed_report["profile_id"]) == published
    # a boot that finds nothing changed still writes one draft and retires it, so the row
    # count grows by one per restart; the published version — what a room runs — does not
    rows = second.store.list_versions(second.seed_report["profile_id"])
    assert len(rows) == versions + 1 and rows[-1]["status"] == "retired"
    assert [r for r in rows if r["status"] == "published"] == [
        r for r in first.store.list_versions(first.seed_report["profile_id"]) if r["status"] == "published"]
