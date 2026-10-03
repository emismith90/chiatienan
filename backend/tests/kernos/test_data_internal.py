"""Internal collections: the data plane a host builds its own stores on (plan 2026-10-02, S1)."""
import pytest
from sqlalchemy import text

from kernos.content import ContentStore
from kernos.content.errors import Conflict, Invalid
from kernos.data import (FIND_LIMIT, CollectionsPack, DataStore, ensure_internal, internal_collection,
                         system_business_id)

PLACES = {"slug": "places", "name": "Places", "key": "id", "searchable": ["name"],
          "schema": {"type": "object", "required": ["id", "name"],
                     "properties": {"id": {"type": "string"}, "name": {"type": "string"},
                                    "tags": {"type": "array", "items": {"type": "string"}}}}}


def _setup(db):
    store = ContentStore(db.session)
    data = DataStore(db.session, audit=store.log)
    cols = ensure_internal(data, db.session, [PLACES])
    return data, store, cols["places"]


def test_ensure_internal_is_idempotent_and_owned_by_system(db):
    data, store, col = _setup(db)
    assert col["internal"] is True
    assert internal_collection(data, db.session, "places")["id"] == col["id"]
    again = ensure_internal(data, db.session, [PLACES])["places"]
    assert again["id"] == col["id"] and again["updated_at"] == col["updated_at"]     # unchanged: no write
    changed = ensure_internal(data, db.session, [{**PLACES, "searchable": ["name", "tags"]}])["places"]
    assert changed["searchable"] == ["name", "tags"]                                # the code owns it


def test_internal_collections_refuse_admin_writes_and_generate_no_tools(db):
    data, store, col = _setup(db)
    bid = system_business_id(db.session)
    with pytest.raises(Invalid, match="internal"):
        data.put_collection(bid, "places", name="x", schema=PLACES["schema"], key="id", actor="admin")
    with pytest.raises(Invalid, match="internal"):
        data.delete_collection(bid, "places", actor="admin")
    pack = CollectionsPack(data, lambda _space: bid)
    assert pack.tools(type("Ctx", (), {"space_id": "1", "sender_member_id": None})()) == {}


def test_a_space_cannot_bind_to_the_system_business(db):
    store = ContentStore(db.session)
    bid = system_business_id(db.session)
    with db.session() as s:                    # an agent nobody should ever be able to create there
        from kernos.content import models as m
        prof = m.Profile(business_id=bid, name="p")
        s.add(prof); s.flush()
        agent = m.Agent(business_id=bid, slug="x", name="x", role="manager", profile_id=prof.id)
        s.add(agent); s.flush()
        agent_id = agent.id
    with pytest.raises(Invalid, match="_system"):
        store.bind_space("1", agent_id)


def test_internal_documents_need_the_callers_session(db):
    data, store, col = _setup(db)
    with pytest.raises(Invalid, match="caller's session"):
        data.upsert_document(col, 1, {"id": "1", "name": "A"}, actor="t")
    with pytest.raises(Invalid, match="caller's session"):
        data.read_all(col, 1)
    with db.session() as s:
        data.upsert_document(col, 1, {"id": "1", "name": "A"}, actor="t", session=s)
        assert [d["doc_id"] for d in data.read_all(col, 1, session=s)] == ["1"]


def test_a_write_rolls_back_with_the_callers_transaction(db):
    data, store, col = _setup(db)
    with pytest.raises(RuntimeError):
        with db.session() as s:
            data.upsert_document(col, 1, {"id": "1", "name": "A"}, actor="t", session=s)
            raise RuntimeError("the card failed to post")
    with db.session() as s:
        assert data.read_all(col, 1, session=s) == []


def test_insert_never_overwrites_and_a_unique_key_clash_leaves_the_transaction_usable(db):
    data, store, col = _setup(db)
    with db.session() as s:
        data.insert_document(col, 1, {"id": "1", "name": "Phở Hà"}, actor="t", session=s, unique_key="pho-ha")
        with pytest.raises(Conflict, match="already has '1'"):
            data.insert_document(col, 1, {"id": "1", "name": "Other"}, actor="t", session=s)
        with pytest.raises(Conflict, match="already taken"):
            data.insert_document(col, 1, {"id": "2", "name": "Phở Hà 2"}, actor="t", session=s, unique_key="pho-ha")
        data.insert_document(col, 1, {"id": "3", "name": "Bún"}, actor="t", session=s, unique_key="bun")
        data.insert_document(col, 2, {"id": "4", "name": "Phở Hà"}, actor="t", session=s, unique_key="pho-ha")
    with db.session() as s:          # the clash rolled back alone; the rest committed
        assert [d["data"]["name"] for d in data.read_all(col, 1, session=s)] == ["Phở Hà", "Bún"]
        assert len(data.read_all(col, 2, session=s)) == 1


def test_editing_a_returned_dict_and_writing_it_back_is_saved(db):
    data, store, col = _setup(db)
    with db.session() as s:
        data.upsert_document(col, 1, {"id": "1", "name": "A", "tags": ["x"]}, actor="t", session=s)
    with db.session() as s:
        doc = data.get_document(col, 1, "1", session=s)
        doc["data"]["tags"].append("y")
        data.upsert_document(col, 1, doc["data"], actor="t", session=s)
    with db.session() as s:
        assert data.get_document(col, 1, "1", session=s)["data"]["tags"] == ["x", "y"]


def test_read_all_is_not_capped_like_find(db):
    data, store, col = _setup(db)
    with db.session() as s:
        for i in range(FIND_LIMIT + 5):
            data.insert_document(col, 1, {"id": f"{i:03d}", "name": f"P{i}"}, actor="t", session=s)
        assert len(data.read_all(col, 1, session=s)) == FIND_LIMIT + 5


def test_next_id_is_monotonic_and_respects_the_floor(db):
    with db.session() as s:
        assert DataStore.next_id("place", session=s, floor=101) == 102
        assert DataStore.next_id("place", session=s, floor=101) == 103
        assert DataStore.next_id("place", session=s, floor=500) == 501       # a floor that moved up wins
        assert DataStore.next_id("meal", session=s) == 1                     # one counter per name
    with db.session() as s:
        assert s.execute(text("SELECT value FROM kn_sequences WHERE name='place'")).scalar_one() == 501


def test_the_unique_index_is_the_last_guard(db):
    """A writer racing past the lookup (another process) still cannot store a duplicate."""
    import sqlalchemy.exc
    data, store, col = _setup(db)
    with db.session() as s:
        data.insert_document(col, 1, {"id": "1", "name": "A"}, actor="t", session=s, unique_key="a")
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        with db.session() as s:
            s.execute(text("INSERT INTO kn_documents (collection_id, space_id, doc_id, data, created_at, created_by, "
                           "updated_at, updated_by, unique_key) VALUES (:c, '1', '9', '{}', '', 't', '', 't', 'a')"),
                      {"c": col["id"]})
