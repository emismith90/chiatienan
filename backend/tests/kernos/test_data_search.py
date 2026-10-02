"""Journals (append-only collections) and hybrid search over collections."""
import pytest
from sqlalchemy import text

from kernos.content import ContentStore
from kernos.content import bind as bind_content
from kernos.content.errors import Conflict, Invalid
from kernos.data import DataStore, EmbeddingError, OpenRouterEmbedder
from kernos.data import embed as embed_mod
from kernos.data import store as store_mod
from kernos.data.search import default_searchable, fold, fts_query, labelled_text, rrf

PLACES = {"type": "object", "required": ["id", "name"],
          "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "note": {"type": "string"},
                         "tags": {"type": "array", "items": {"type": "string"}}, "price": {"type": "integer"}}}
LOG = {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}, "who": {"type": "string"}}}


class FakeEmbedder:
    """Meaning as a bag of concepts: each concept is one axis, and a text points along
    every axis whose words it contains. Counts what it was asked to embed."""
    model, min_similarity = "fake-1", 0.5
    CONCEPTS = [("cay", "spicy", "ot"), ("chay", "vegetarian", "rau"), ("hop", "meeting", "offsite"),
                ("noodle", "pho", "bun")]

    def __init__(self):
        self.calls: list[list[str]] = []
        self.fail = False

    def embed(self, texts):
        if self.fail:
            raise EmbeddingError("fake-1: down")
        self.calls.append(list(texts))
        out = []
        for t in texts:
            words = set(fold(t).replace(":", " ").replace(",", " ").split())
            out.append([1.0 if words & set(c) else 0.0 for c in self.CONCEPTS] + [0.01])
        return out


def _setup(db, embedder=None):
    store = ContentStore(db.session)
    bid = store.create_business("acme", "Acme")["id"]
    data = DataStore(db.session, audit=store.log, embedder=embedder)
    places = data.put_collection(bid, "places", name="Places", schema=PLACES, key="id", actor="admin")
    return data, store, bid, places


# ------------------------------------------------------------------ helpers

def test_folding_query_and_merge_helpers():
    assert fold("Đậu Phở BÚN") == "dau pho bun"
    assert fts_query('Phở "bò" OR -x') == '"pho" OR "bo" OR "or" OR "x"'      # quoted: no FTS syntax leaks
    assert fts_query("?!") is None
    assert default_searchable(PLACES) == ["id", "name", "note", "tags"]
    assert labelled_text(["name", "tags", "note"], {"id": "pho-ha", "name": "Phở Hà", "tags": ["nước", "bò"], "price": 5}) == \
        "name: Phở Hà\ntags: nước, bò"
    # in both lists beats the top of one; each id remembers which lists it came from
    assert rrf([1, 2, 3], [3, 4]) == [(3, [0, 1]), (1, [0]), (2, [0]), (4, [1])]


# ------------------------------------------------------------------ journal

def test_journal_definition_rules(db):
    data, store, bid, places = _setup(db)
    with pytest.raises(Invalid, match="a journal has no key"):
        data.put_collection(bid, "log", name="Log", schema=LOG, key="text", mode="journal", actor="admin")
    with pytest.raises(Invalid, match="mode 'ledger'"):
        data.put_collection(bid, "log", name="Log", schema=LOG, key="", mode="ledger", actor="admin")
    with pytest.raises(Invalid, match="searchable field 'price' must be a string"):
        data.put_collection(bid, "p2", name="P", schema=PLACES, key="id", searchable=["price"], actor="admin")
    log = data.put_collection(bid, "log", name="Log", schema=LOG, key="", mode="journal", actor="admin")
    assert log["mode"] == "journal" and log["searchable"] == ["text", "who"] and places["mode"] == "table"
    assert store.audit(limit=1)[0]["after"]["mode"] == "journal"
    data.append_entry(log, 1, {"text": "x"}, actor="a")
    with pytest.raises(Conflict, match="mode cannot change"):
        data.put_collection(bid, "log", name="Log", schema=LOG, key="text", mode="table", actor="admin")
    # an empty table may still become a journal
    assert data.put_collection(bid, "places", name="P", schema=PLACES, key="", mode="journal", actor="admin")["mode"] == "journal"


def test_journal_entries_are_numbered_permanent_and_corrected_by_new_entries(db, monkeypatch):
    data, store, bid, places = _setup(db)
    log = data.put_collection(bid, "log", name="Log", schema=LOG, key="", mode="journal", indexed=["who"], actor="admin")
    first = data.append_entry(log, 7, {"text": "An paid the deposit", "who": "An"}, actor="3")
    second = data.append_entry(log, 7, {"text": "Binh booked the room", "who": "Binh"}, actor="3")
    assert (first["doc_id"], second["doc_id"]) == ("000001", "000002")
    assert data.append_entry(log, 8, {"text": "other room"}, actor="3")["doc_id"] == "000001"   # numbered per space
    fix = data.append_entry(log, 7, {"text": "Chi paid the deposit, not An", "who": "Chi"}, actor="3", corrects="000001")
    assert fix["doc_id"] == "000003" and fix["corrects"] == "000001"
    assert store.audit(limit=1)[0]["action"] == "append"
    with pytest.raises(Invalid, match="no such entry"):
        data.append_entry(log, 7, {"text": "?"}, actor="3", corrects="000099")
    with pytest.raises(Invalid, match="does not match"):
        data.append_entry(log, 7, {"who": "An"}, actor="3")
    with pytest.raises(Invalid, match="is a journal"):
        data.upsert_document(log, 7, {"text": "x"}, actor="3")
    with pytest.raises(Invalid, match="never deleted"):
        data.delete_document(log, 7, "000001", actor="3")
    with pytest.raises(Invalid, match="is a table"):
        data.append_entry(places, 7, {"id": "x", "name": "x"}, actor="3")
    found = data.find_documents(log, 7)["documents"]
    assert [d["doc_id"] for d in found] == ["000003", "000002", "000001"]        # newest first
    assert found[0]["corrects"] == "000001" and "corrects" not in found[1]
    assert [d["doc_id"] for d in data.find_documents(log, 7, where={"who": "An"})["documents"]] == ["000001"]
    assert data.get_document(log, 7, "000001")["data"]["who"] == "An"                # the wrong entry stays
    monkeypatch.setattr(store_mod, "MAX_JOURNAL_ENTRIES", 3)
    with pytest.raises(Conflict, match="already holds 3 entries"):
        data.append_entry(log, 7, {"text": "full"}, actor="3")


# ------------------------------------------------------------------ words

def test_word_search_folds_vietnamese_and_stays_in_its_space_and_collection(db):
    data, store, bid, places = _setup(db)
    data.upsert_document(places, 1, {"id": "pho-ha", "name": "Phở Hà", "note": "phở bò tái, gần văn phòng"}, actor="a")
    data.upsert_document(places, 1, {"id": "che-dau", "name": "Chè Đậu", "note": "đậu xanh", "tags": ["tráng miệng"]}, actor="a")
    data.upsert_document(places, 2, {"id": "pho-thin", "name": "Phở Thìn"}, actor="a")
    other = data.put_collection(bid, "dishes", name="Dishes", schema=PLACES, key="id", actor="admin")
    data.upsert_document(other, 1, {"id": "pho-cuon", "name": "phở cuốn"}, actor="a")

    out = data.search_documents(places, 1, "pho")
    assert [d["doc_id"] for d in out["documents"]] == ["pho-ha"] and out["semantic"] == "off"
    assert out["documents"][0]["match"] == ["text"] and "score" not in out["documents"][0]
    assert [d["doc_id"] for d in data.search_documents(places, 1, "dau")["documents"]] == ["che-dau"]   # đ → d
    assert [d["doc_id"] for d in data.search_documents(places, 1, "TRÁNG")["documents"]] == ["che-dau"]  # an array field
    # more of the query's words ranks first
    data.upsert_document(places, 1, {"id": "bo-ne", "name": "Bò Né", "note": "bò"}, actor="a")
    assert [d["doc_id"] for d in data.search_documents(places, 1, "phở bò")["documents"]] == ["pho-ha", "bo-ne"]
    with pytest.raises(Invalid, match="non-empty"):
        data.search_documents(places, 1, "  ")
    assert data.search_documents(places, 1, "?!")["documents"] == []


def test_the_index_follows_edits_deletes_definition_changes_and_old_rows(db):
    data, store, bid, places = _setup(db)
    data.upsert_document(places, 1, {"id": "quan-a", "name": "Quán A", "note": "cơm tấm"}, actor="a")
    data.upsert_document(places, 1, {"id": "quan-a", "name": "Quán A", "note": "bún riêu"}, actor="a")
    assert data.search_documents(places, 1, "tam")["documents"] == []
    assert [d["doc_id"] for d in data.search_documents(places, 1, "rieu")["documents"]] == ["quan-a"]
    # only `name` searchable now: the note's words drop out
    places = data.put_collection(bid, "places", name="Places", schema=PLACES, key="id", searchable=["name"], actor="admin")
    assert data.search_documents(places, 1, "rieu")["documents"] == []
    assert data.search_documents(places, 1, "quan")["documents"][0]["doc_id"] == "quan-a"
    data.delete_document(places, 1, "quan-a", actor="a")
    assert data.search_documents(places, 1, "quan")["documents"] == []
    with pytest.raises(Invalid, match="no searchable fields"):
        data.search_documents(data.put_collection(bid, "nums", name="N", schema={
            "type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}, key="id",
            searchable=[], actor="admin"), 1, "x")


def test_rows_older_than_the_index_become_searchable(db):
    data, store, bid, places = _setup(db)
    data.upsert_document(places, 1, {"id": "bun-cha", "name": "Bún Chả"}, actor="a")
    with db.engine.begin() as conn:                       # a database from before this feature
        for name in ("kn_documents_fts_ai", "kn_documents_fts_ad", "kn_documents_fts_au"):
            conn.execute(text(f"DROP TRIGGER {name}"))
        conn.execute(text("DROP TABLE kn_documents_fts"))
        conn.execute(text("UPDATE kn_documents SET search_text = NULL"))
    bind_content(db.engine)                               # boot: index created, old rows taken in
    assert [d["doc_id"] for d in data.search_documents(places, 1, "bun cha")["documents"]] == ["bun-cha"]
    data.upsert_document(places, 1, {"id": "bun-cha", "name": "Bún Chả", "note": "chả nướng"}, actor="a")   # the triggers work on them
    assert data.search_documents(places, 1, "nuong")["documents"][0]["doc_id"] == "bun-cha"
    with db.engine.connect() as conn:
        assert conn.execute(text("INSERT INTO kn_documents_fts(kn_documents_fts) VALUES ('integrity-check')")) is not None


# ------------------------------------------------------------------ meaning

def test_semantic_search_finds_by_meaning_and_merges_with_words(db):
    fake = FakeEmbedder()
    data, store, bid, places = _setup(db, fake)
    data.upsert_document(places, 1, {"id": "bun-bo-hue", "name": "Bún bò Huế", "note": "cay, đậm vị"}, actor="a")
    data.upsert_document(places, 1, {"id": "quan-chay", "name": "Quán chay Hoa Sen", "note": "yên tĩnh"}, actor="a")
    data.upsert_document(places, 1, {"id": "pizza-4ps", "name": "Pizza 4P's", "note": "spicy salami"}, actor="a")
    out = data.search_documents(places, 1, "vegetarian")
    assert out["semantic"] == "on" and [(d["doc_id"], d["match"]) for d in out["documents"]] == \
        [("quan-chay", ["semantic"])]                       # no shared word, same meaning
    spicy = data.search_documents(places, 1, "spicy")["documents"]
    assert spicy[0] == {**spicy[0], "doc_id": "pizza-4ps", "match": ["text", "semantic"]}
    assert [d["doc_id"] for d in spicy] == ["pizza-4ps", "bun-bo-hue"]
    assert data.search_documents(places, 1, "parking")["documents"] == []    # below the threshold: honest "none"


def test_vectors_are_cached_until_the_text_or_model_changes(db):
    fake = FakeEmbedder()
    data, store, bid, places = _setup(db, fake)
    data.upsert_document(places, 1, {"id": "a", "name": "A", "note": "cay"}, actor="a")
    data.upsert_document(places, 1, {"id": "b", "name": "B", "note": "chay"}, actor="a")
    data.search_documents(places, 1, "spicy")
    assert len(fake.calls[-1]) == 3                                  # the query and both documents
    data.search_documents(places, 1, "spicy")
    assert fake.calls[-1] == ["spicy"]                              # cached
    data.upsert_document(places, 1, {"id": "b", "name": "B", "note": "phở"}, actor="a")
    data.search_documents(places, 1, "spicy")
    assert fake.calls[-1] == ["spicy", "id: b\nname: B\nnote: phở"]         # only the edited one
    fake.model = "fake-2"
    data.search_documents(places, 1, "spicy")
    assert len(fake.calls[-1]) == 3                                  # a new model re-embeds everything
    data.delete_document(places, 1, "a", actor="a")
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM kn_document_vectors")).scalar() == 1


def test_a_backlog_is_worked_off_across_searches(db, monkeypatch):
    fake = FakeEmbedder()
    data, store, bid, places = _setup(db, fake)
    monkeypatch.setattr(store_mod, "EMBED_PER_SEARCH", 2)
    for name in ("A", "B", "C"):
        data.upsert_document(places, 1, {"id": name.lower(), "name": name, "note": "cay"}, actor="a")
    first = data.search_documents(places, 1, "spicy")
    assert first["semantic"] == "partial" and len(first["documents"]) == 2
    second = data.search_documents(places, 1, "spicy")
    assert second["semantic"] == "on" and len(second["documents"]) == 3


def test_a_failing_embedder_leaves_the_word_hits(db):
    fake = FakeEmbedder()
    data, store, bid, places = _setup(db, fake)
    data.upsert_document(places, 1, {"id": "quan-chay", "name": "Quán chay"}, actor="a")
    fake.fail = True
    out = data.search_documents(places, 1, "chay")
    assert out["semantic"] == "unavailable" and [d["doc_id"] for d in out["documents"]] == ["quan-chay"]


# ------------------------------------------------------------------ the embedder

def test_openrouter_embedder_batches_orders_and_wraps_errors(monkeypatch):
    sent = []

    def post(url, payload, headers, timeout):
        sent.append((url, payload, headers["Authorization"]))
        n = len(payload["input"])
        return {"data": [{"index": i, "embedding": [float(i)]} for i in reversed(range(n))]}

    monkeypatch.setattr(embed_mod, "BATCH", 2)
    emb = OpenRouterEmbedder("k", "google/gemini-embedding-001", min_similarity=0.6, post=post)
    assert emb.embed(["a", "b", "c"]) == [[0.0], [1.0], [0.0]]      # by index, two requests
    assert [p["input"] for _, p, _ in sent] == [["a", "b"], ["c"]]
    assert sent[0][0].endswith("/api/v1/embeddings") and sent[0][1]["model"] == "google/gemini-embedding-001"
    assert sent[0][2] == "Bearer k"

    def broken(*_a):
        raise OSError("HTTP Error 401")
    with pytest.raises(EmbeddingError, match="401"):
        OpenRouterEmbedder("k", "m", min_similarity=0.6, post=broken).embed(["a"])
    with pytest.raises(EmbeddingError, match="1 vectors for 2"):
        OpenRouterEmbedder("k", "m", min_similarity=0.6,
                           post=lambda *_a: {"data": [{"index": 0, "embedding": [1.0]}]}).embed(["a", "b"])
