"""Collections as content, one documents table (plan Task 5.1)."""
import pytest

from kernos.content import ContentStore
from kernos.content.errors import Conflict, Invalid, NotFound
from kernos.data import MAX_DOCUMENTS, DataStore, SchemaError, check_schema

ROTA = {"type": "object", "required": ["week", "who"],
        "properties": {"week": {"type": "string", "description": "ISO week, e.g. 2026-W36"},
                       "who": {"type": "string"}, "brings": {"type": "string", "enum": ["cards", "chips", "both"]},
                       "players": {"type": "integer"}, "tags": {"type": "array", "items": {"type": "string"}}}}


def _stores(db):
    store = ContentStore(db.session)
    bid = store.create_business("acme", "Acme")["id"]
    return DataStore(db.session, audit=store.log), store, bid


@pytest.mark.parametrize("schema, keyword", [
    ({"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": False}, "additionalProperties"),
    ({"type": "object", "properties": {"n": {"type": "integer", "minimum": 0}}}, "minimum"),
    ({"type": "object", "properties": {"r": {"$ref": "#/x"}}}, "$ref"),
    ({"type": "object", "properties": {"s": {"type": "string", "format": "date"}}}, "format"),
])
def test_only_the_sidecar_safe_subset_is_accepted(schema, keyword):
    with pytest.raises(SchemaError, match=keyword.replace("$", r"\$")):
        check_schema(schema)
    with pytest.raises(SchemaError, match="enum is only supported on strings"):
        check_schema({"type": "integer", "enum": [1, 2]})
    with pytest.raises(SchemaError, match="must declare items"):
        check_schema({"type": "array"})
    with pytest.raises(SchemaError, match="not a scalar type"):
        check_schema({"type": ["string", "object"]})
    check_schema(ROTA)                       # the six keywords, a string enum, an array with items


def test_put_collection_rules(db):
    data, store, bid = _stores(db)
    def put(**kw):
        kw.setdefault("actor", "admin")
        return data.put_collection(bid, kw.pop("slug", "rota"), name="Rota", schema=kw.pop("schema", ROTA),
                                   key=kw.pop("key", "week"), indexed=kw.pop("indexed", ["who"]), **kw)
    with pytest.raises(Invalid, match="slug"):
        put(slug="Rota")
    with pytest.raises(Invalid, match="slug"):
        put(slug="a" * 58)
    with pytest.raises(Invalid, match="must be required"):
        put(key="brings")
    with pytest.raises(Invalid, match="must be a string"):
        put(schema={**ROTA, "required": ["week", "who", "players"]}, key="players")
    with pytest.raises(Invalid, match="indexed field 'nope'"):
        put(indexed=["nope"])
    with pytest.raises(Invalid, match="additionalProperties"):
        put(schema={**ROTA, "additionalProperties": False})
    with pytest.raises(Conflict, match="rota_find"):
        put(reserved={"rota_find"})
    with pytest.raises(Invalid, match="an agent may not"):
        put(actor="agent:phoenix")
    with pytest.raises(NotFound):
        data.put_collection(999, "rota", name="x", schema=ROTA, key="week", actor="admin")
    col = put()
    assert col["slug"] == "rota" and col["indexed"] == ["who"] and data.get_collection(bid, "rota")["key"] == "week"
    assert [c["slug"] for c in data.list_collections(bid)] == ["rota"]
    assert store.audit(limit=1)[0]["entity"] == "collection"


def test_documents_validate_key_cap_find_and_delete(db):
    data, store, bid = _stores(db)
    col = data.put_collection(bid, "rota", name="Rota", schema=ROTA, key="week", indexed=["who"], actor="admin")
    with pytest.raises(Invalid, match="does not match the rota schema"):
        data.upsert_document(col, 7, {"week": "2026-W36"}, actor="3")           # who missing
    with pytest.raises(Invalid, match="brings"):
        data.upsert_document(col, 7, {"week": "2026-W36", "who": "An", "brings": "beer"}, actor="3")
    with pytest.raises(Invalid, match="must match"):
        data.upsert_document(col, 7, {"week": "2026 W36", "who": "An"}, actor="3")
    a = data.upsert_document(col, 7, {"week": "2026-W36", "who": "An", "players": 6}, actor="3")
    b = data.upsert_document(col, 7, {"week": "2026-W37", "who": "Binh"}, actor="3")
    again = data.upsert_document(col, "7", {"week": "2026-W36", "who": "An", "players": 8}, actor="4")
    assert again["id"] == a["id"] and again["data"]["players"] == 8 and again["updated_by"] == "4"
    assert data.get_document(col, 7, "2026-W37")["data"]["who"] == "Binh" and data.get_document(col, 8, "2026-W37") is None
    found = data.find_documents(col, 7, where={"who": "An"})
    assert [d["doc_id"] for d in found["documents"]] == ["2026-W36"] and found["more"] is False
    assert [d["doc_id"] for d in data.find_documents(col, 7)["documents"]] == ["2026-W36", "2026-W37"]
    assert data.find_documents(col, 7, limit=1) == {"documents": [{"doc_id": "2026-W36", "data": again["data"], "updated_at": again["updated_at"]}], "more": True}
    with pytest.raises(Invalid, match="can only filter by \\['who'\\]"):
        data.find_documents(col, 7, where={"players": 8})
    # a schema edit that invalidates an existing document is refused, unless forced
    tighter = {**ROTA, "required": ["week", "who", "players"]}
    with pytest.raises(Conflict, match="2026-W37"):
        data.put_collection(bid, "rota", name="Rota", schema=tighter, key="week", indexed=["who"], actor="admin")
    data.put_collection(bid, "rota", name="Rota", schema=tighter, key="week", indexed=["who"], actor="admin", force=True)
    # delete returns the row; the collection cannot go while documents exist
    with pytest.raises(Conflict, match="2 document"):
        data.delete_collection(bid, "rota", actor="admin")
    gone = data.delete_document(col, 7, "2026-W37", actor="3")
    assert gone["data"]["who"] == "Binh" and store.audit(limit=1)[0]["before"] == gone["data"]
    with pytest.raises(NotFound):
        data.delete_document(col, 7, "2026-W37", actor="3")
    data.delete_document(col, 7, "2026-W36", actor="3")
    data.delete_collection(bid, "rota", actor="admin")
    assert data.list_collections(bid) == [] and b["doc_id"] == "2026-W37"


def test_the_document_cap_and_pagination(db, monkeypatch):
    data, store, bid = _stores(db)
    monkeypatch.setattr("kernos.data.store.MAX_DOCUMENTS", 3)
    col = data.put_collection(bid, "notes", name="Notes", schema={"type": "object", "required": ["id"],
                              "properties": {"id": {"type": "string"}}}, key="id", actor="admin")
    for i in range(3):
        data.upsert_document(col, 1, {"id": f"n{i}"}, actor="a")
    with pytest.raises(Conflict, match="already holds 3"):
        data.upsert_document(col, 1, {"id": "n9"}, actor="a")
    data.upsert_document(col, 2, {"id": "n9"}, actor="a")                  # another space has its own cap
    page = data.list_documents(col, 1, limit=2)
    assert [d["doc_id"] for d in page["documents"]] == ["n0", "n1"] and page["more"] is True
    page = data.list_documents(col, 1, limit=2, after="n1")
    assert [d["doc_id"] for d in page["documents"]] == ["n2"] and page["more"] is False
    assert MAX_DOCUMENTS == 1000
