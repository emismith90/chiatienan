"""``DataStore``: collections (content) and documents (data) — design §5.3, plan Phase 5.

Definitions are audited content and refuse ``agent:*`` actors (an agent may not
rewrite its own tools outside a proposal — review F2); documents are what an agent
remembers on a space's behalf and stay agent-writable, audited with the actor.

A collection is a ``table`` (documents upserted and deleted by their ``key``) or a
``journal`` (append-only: the server numbers the entries ``000001``, ``000002``, …, an
entry is never replaced or deleted, and a fix is a new entry that ``corrects`` an old
one). Both are searchable by words and by meaning (:meth:`DataStore.search_documents`).

An **internal** collection (``internal=True``, defined in code under the ``_system``
business — :mod:`kernos.data.system`) is a store the host builds on: notes, places,
later the ledger. Its document methods *require* the caller's ``session`` so every read
and write lands in the caller's transaction (one connection, one snapshot — review R10),
it is never audited per document, and it is capped at ``INTERNAL_CAP``.
"""
from __future__ import annotations

import copy
import logging
from contextlib import contextmanager
from typing import Any, Callable, Iterable

import jsonschema
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from kernos.content import models as m
from kernos.content.errors import Conflict, Invalid, NotFound
from kernos.data.embed import EmbeddingError
from kernos.data.schema import DOC_ID_RE, SLUG_RE, SchemaError, check_collection_schema
from kernos.data.search import (
    CANDIDATES, content_hash, default_searchable, dot, fts_query, labelled_text, pack_vector, query_tokens,
    rrf, search_text, searchable_fields, unpack_vector,
)

log = logging.getLogger("kernos.data")

#: Documents per (space, collection). `find` loads a space's rows of one collection into
#: Python; this is what makes that bounded (review F7).
MAX_DOCUMENTS = 1000
#: Entries per (space, journal). Higher than a table's: a journal only grows, and the
#: entry number is six digits.
MAX_JOURNAL_ENTRIES = 10000
#: Documents per (space, internal collection). The host reads them all (`read_all`);
#: this bounds that, far above any room's real size.
INTERNAL_CAP = 100_000
#: The most rows a generated `find` returns.
FIND_LIMIT = 50
#: The most rows a generated `search` returns.
SEARCH_LIMIT = 20
#: Documents one search may embed, so a backlog (a new model, a bulk import) is worked off
#: over several searches instead of stalling one turn; the rest is reported as ``partial``.
EMBED_PER_SEARCH = 256
#: Word matches read before they are re-ranked by how many of the query's words they hold.
TEXT_SCAN = 500


def _row(obj) -> dict:
    # Deep-copied: a caller that edits the returned `data` and writes it back must not be
    # editing the ORM's own dict, which SQLAlchemy would then see as unchanged (review R11).
    return {c.name: copy.deepcopy(getattr(obj, c.name)) for c in obj.__table__.columns if c.name != "search_text"}


def _cap(collection: dict) -> int:
    if collection.get("internal"):
        return INTERNAL_CAP
    return MAX_JOURNAL_ENTRIES if collection.get("mode") == "journal" else MAX_DOCUMENTS


def generated_tool_names(slug: str, mode: str = "table") -> tuple[str, ...]:
    if mode == "journal":
        return f"{slug}_find", f"{slug}_append", f"{slug}_search"
    return f"{slug}_find", f"{slug}_create", f"{slug}_update", f"{slug}_delete", f"{slug}_search"


def _hit(row: m.Document) -> dict:
    out = {"doc_id": row.doc_id, "data": copy.deepcopy(row.data), "updated_at": row.updated_at}
    if row.corrects:
        out["corrects"] = row.corrects
    return out


class DataStore:
    def __init__(self, session_factory: Callable[[], Any], *, audit: Callable | None = None,
                 embedder: Any = None) -> None:
        self._session = session_factory
        self._audit = audit            # ContentStore.log(session, actor, action, entity, entity_id, before, after)
        #: ``embed(texts) -> vectors`` plus ``model`` and ``min_similarity`` (see
        #: :class:`kernos.data.embed.OpenRouterEmbedder`); ``None`` = search by words only.
        self._embedder = embedder

    def _log(self, s: Session, actor: str, action: str, entity: str, entity_id: Any, before=None, after=None) -> None:
        if self._audit is not None:
            self._audit(s, actor, action, entity, entity_id, before=before, after=after)

    @contextmanager
    def _use(self, collection: dict, session: Session | None):
        """The caller's session, or a fresh one of our own. An internal collection must
        be handed the caller's: a second connection inside a write would read a snapshot
        without the caller's uncommitted rows, or wait out the busy timeout (review R10)."""
        if session is not None:
            yield session
            return
        if collection.get("internal"):
            raise Invalid(f"{collection['slug']} is internal: pass the caller's session")
        with self._session() as s:
            yield s

    def _log_doc(self, collection: dict, s: Session, actor: str, action: str, entity_id: str,
                 before=None, after=None) -> None:
        if not collection.get("internal"):          # an internal store keeps its own history
            self._log(s, actor, action, "document", entity_id, before=before, after=after)

    # ---------------------------------------------------------------- collections

    def put_collection(self, business_id: int, slug: str, *, name: str, schema: dict, key: str,
                       indexed: Iterable[str] = (), description: str = "", actor: str,
                       reserved: Iterable[str] = (), force: bool = False, mode: str = "table",
                       searchable: Iterable[str] | None = None, internal: bool = False,
                       options: dict | None = None) -> dict:
        """Create or update a definition. Refused for an ``agent:*`` actor, a slug outside
        ``[a-z][a-z0-9_]{0,56}``, a schema outside the sidecar-safe subset, a generated
        tool name another pack owns, a ``mode`` change once documents exist, or — with
        documents — a schema an existing document no longer satisfies (unless ``force``).
        ``searchable=None`` stores the default: every top-level string field."""
        if actor.startswith("agent:"):
            raise Invalid("an agent may not define or change a collection; propose it instead")
        if not SLUG_RE.match(slug or ""):
            raise Invalid(f"collection slug {slug!r} must match [a-z][a-z0-9_]{{0,56}}")
        indexed = list(indexed)
        searchable = default_searchable(schema) if searchable is None else list(searchable)
        try:
            check_collection_schema(schema, key=key, indexed=indexed, mode=mode, searchable=searchable)
        except SchemaError as exc:
            raise Invalid(str(exc)) from exc
        from kernos.data.actions import check_options
        options = check_options(options, schema=schema, key=key, mode=mode)
        clash = sorted(set(generated_tool_names(slug, mode)) & set(reserved))
        if clash:
            raise Conflict(f"collection {slug!r} would generate tool names another pack owns: {clash}")
        with self._session() as s:
            if s.get(m.Business, business_id) is None:
                raise NotFound(f"no business #{business_id}")
            row = s.scalar(select(m.Collection).where(m.Collection.business_id == business_id, m.Collection.slug == slug))
            if row is not None and row.internal and not internal:
                raise Invalid(f"collection {slug!r} is internal: it is defined in code, not here")
            before = _row(row) if row else None
            if row is None:
                row = m.Collection(business_id=business_id, slug=slug, schema=schema, key=key, name=name)
                s.add(row)
            else:
                if mode != row.mode and s.scalar(select(func.count()).select_from(m.Document)
                                                 .where(m.Document.collection_id == row.id)):
                    raise Conflict(f"collection {slug!r} is a {row.mode} with documents; its mode cannot change")
                bad = []
                for doc in s.scalars(select(m.Document).where(m.Document.collection_id == row.id)).all():
                    try:
                        jsonschema.validate(doc.data, schema)
                    except jsonschema.ValidationError:
                        bad.append(doc.doc_id)
                if bad and not force:
                    raise Conflict(f"{len(bad)} existing document(s) no longer satisfy the schema: "
                                   f"{bad[:10]} — fix them or pass force")
                # the searchable text may have changed: the next search recomputes it
                s.execute(m.Document.__table__.update().where(m.Document.collection_id == row.id)
                          .values(search_text=None))
            row.name, row.description, row.schema, row.key, row.indexed = name, description, schema, key, indexed
            row.mode, row.searchable, row.internal, row.options = mode, searchable, internal, options
            row.updated_at = m.utcnow()
            s.flush()
            self._log(s, actor, "put", "collection", f"{business_id}/{slug}",
                      before={k: before[k] for k in ("schema", "key", "indexed", "mode", "searchable")} if before else None,
                      after={"schema": schema, "key": key, "indexed": indexed, "mode": mode, "searchable": searchable,
                             "options": options})
            return _row(row)

    def _collection(self, s: Session, business_id: int, slug: str) -> m.Collection:
        row = s.scalar(select(m.Collection).where(m.Collection.business_id == business_id, m.Collection.slug == slug))
        if row is None:
            raise NotFound(f"no collection {slug!r} in business #{business_id}")
        return row

    def get_collection(self, business_id: int, slug: str) -> dict:
        with self._session() as s:
            return _row(self._collection(s, business_id, slug))

    def list_collections(self, business_id: int) -> list[dict]:
        with self._session() as s:
            return [_row(r) for r in s.scalars(select(m.Collection).where(m.Collection.business_id == business_id)
                                                .order_by(m.Collection.slug)).all()]

    def delete_collection(self, business_id: int, slug: str, *, actor: str) -> None:
        if actor.startswith("agent:"):
            raise Invalid("an agent may not delete a collection; propose it instead")
        with self._session() as s:
            row = self._collection(s, business_id, slug)
            if row.internal:
                raise Invalid(f"collection {slug!r} is internal: it is defined in code, not here")
            n = s.scalar(select(func.count()).select_from(m.Document).where(m.Document.collection_id == row.id))
            if n:
                raise Conflict(f"collection {slug!r} has {n} document(s); delete them first")
            s.delete(row)
            self._log(s, actor, "delete", "collection", f"{business_id}/{slug}")

    # ----------------------------------------------------------------- documents

    @staticmethod
    def validate(collection: dict, data: Any) -> str | None:
        """The schema error for ``data``, or ``None`` when it validates."""
        try:
            jsonschema.validate(data, collection["schema"])
        except jsonschema.ValidationError as exc:
            path = "/".join(str(p) for p in exc.absolute_path)
            return f"{exc.message}" + (f" (at {path})" if path else "")
        return None

    def _doc(self, s: Session, collection_id: int, space_id: str, doc_id: str) -> m.Document | None:
        return s.scalar(select(m.Document).where(m.Document.collection_id == collection_id,
                                                 m.Document.space_id == space_id, m.Document.doc_id == doc_id))

    def upsert_document(self, collection: dict, space_id: Any, data: dict, *, actor: str,
                        session: Session | None = None, unique_key: str | None = None) -> dict:
        """Validate ``data`` and write it; the document id is ``data[key]``. Raises
        :class:`Invalid` on a schema error, a bad id or a journal, :class:`Conflict` when
        the space's collection is full or ``unique_key`` is taken by another document."""
        return self._write(collection, space_id, data, actor=actor, session=session,
                           unique_key=unique_key, create_only=False)

    def insert_document(self, collection: dict, space_id: Any, data: dict, *, actor: str,
                        session: Session | None = None, unique_key: str | None = None) -> dict:
        """Like :meth:`upsert_document`, but :class:`Conflict` when the document exists:
        a create must never silently overwrite another record (review R4)."""
        return self._write(collection, space_id, data, actor=actor, session=session,
                           unique_key=unique_key, create_only=True)

    def _write(self, collection: dict, space_id: Any, data: dict, *, actor: str, session: Session | None,
               unique_key: str | None, create_only: bool) -> dict:
        if collection.get("mode") == "journal":
            raise Invalid(f"{collection['slug']} is a journal: append entries, never replace them")
        problem = self.validate(collection, data)
        if problem:
            raise Invalid(f"document does not match the {collection['slug']} schema: {problem}")
        doc_id = str(data[collection["key"]])
        if not DOC_ID_RE.match(doc_id):
            raise Invalid(f"{collection['key']}={doc_id!r} must match {DOC_ID_RE.pattern}")
        space_id = str(space_id)
        data = copy.deepcopy(data)
        with self._use(collection, session) as s:
            row = self._doc(s, collection["id"], space_id, doc_id)
            if row is not None and create_only:
                raise Conflict(f"{collection['slug']} already has {doc_id!r} in this space")
            before = copy.deepcopy(row.data) if row else None
            if row is None:
                n = s.scalar(select(func.count()).select_from(m.Document).where(
                    m.Document.collection_id == collection["id"], m.Document.space_id == space_id))
                cap = _cap(collection)
                if n >= cap:
                    raise Conflict(f"{collection['slug']} already holds {cap} documents in this space; delete some first")
            # A plain lookup, then the write. Not a savepoint: with the stdlib sqlite driver
            # a SAVEPOINT opened before the transaction's first write starts that
            # transaction, and RELEASE then *commits* it — the caller's rollback would no
            # longer undo this write. The unique index stays the last guard: a true race
            # fails the whole transaction loudly instead of writing a duplicate.
            if unique_key is not None and s.scalar(select(m.Document.doc_id).where(
                    m.Document.collection_id == collection["id"], m.Document.space_id == space_id,
                    m.Document.unique_key == unique_key, m.Document.doc_id != doc_id)) is not None:
                raise Conflict(f"{collection['slug']}: {unique_key!r} is already taken in this space")
            if row is None:
                row = m.Document(collection_id=collection["id"], space_id=space_id, doc_id=doc_id,
                                 data=data, created_by=actor, updated_by=actor)
                s.add(row)
            else:
                row.data, row.updated_by, row.updated_at = data, actor, m.utcnow()
                flag_modified(row, "data")
            row.unique_key = unique_key
            row.search_text = search_text(searchable_fields(collection), data)
            s.flush()
            self._log_doc(collection, s, actor, "upsert", f"{collection['slug']}/{space_id}/{doc_id}",
                          before=before, after=data)
            return _row(row)

    def append_entry(self, collection: dict, space_id: Any, data: dict, *, actor: str,
                     corrects: str | None = None, session: Session | None = None) -> dict:
        """Add one entry to a journal; the server picks its ``doc_id`` (the next number).
        ``corrects`` names an earlier entry of the same journal and space that this one
        fixes — the earlier one stays, unchanged. Raises :class:`Invalid` on a table, a
        schema error or an unknown ``corrects``; :class:`Conflict` when the journal is full."""
        if collection.get("mode") != "journal":
            raise Invalid(f"{collection['slug']} is a table: upsert its documents by {collection['key']!r}")
        problem = self.validate(collection, data)
        if problem:
            raise Invalid(f"entry does not match the {collection['slug']} schema: {problem}")
        space_id = str(space_id)
        data = copy.deepcopy(data)
        with self._use(collection, session) as s:
            if corrects is not None and self._doc(s, collection["id"], space_id, str(corrects)) is None:
                raise Invalid(f"corrects={corrects!r}: no such entry in {collection['slug']} for this space")
            n = s.scalar(select(func.count()).select_from(m.Document).where(
                m.Document.collection_id == collection["id"], m.Document.space_id == space_id))
            cap = _cap(collection)
            if n >= cap:
                raise Conflict(f"{collection['slug']} already holds {cap} entries in this space")
            # never deleted, so the count is the last number
            doc_id = f"{n + 1:06d}"
            row = m.Document(collection_id=collection["id"], space_id=space_id, doc_id=doc_id, data=data,
                             created_by=actor, updated_by=actor, corrects=None if corrects is None else str(corrects),
                             search_text=search_text(searchable_fields(collection), data))
            s.add(row)
            s.flush()
            self._log_doc(collection, s, actor, "append", f"{collection['slug']}/{space_id}/{doc_id}", after=data)
            return _row(row)

    def get_document(self, collection: dict, space_id: Any, doc_id: str, *,
                     session: Session | None = None) -> dict | None:
        with self._use(collection, session) as s:
            row = self._doc(s, collection["id"], str(space_id), str(doc_id))
            return _row(row) if row else None

    def find_documents(self, collection: dict, space_id: Any, *, where: dict | None = None,
                       limit: int = FIND_LIMIT, session: Session | None = None,
                       hide: str | None = None) -> dict:
        """``{"documents": [...], "more": bool}`` — equality on ``indexed`` fields only, in
        ``doc_id`` order (a journal's newest first), at most ``limit`` (≤ FIND_LIMIT). Never
        a count."""
        where = dict(where or {})
        unknown = sorted(set(where) - set(collection["indexed"]))
        if unknown:
            raise Invalid(f"{collection['slug']}_find can only filter by {collection['indexed']}; not {unknown}")
        limit = max(1, min(int(limit or FIND_LIMIT), FIND_LIMIT))
        with self._use(collection, session) as s:
            order = m.Document.doc_id.desc() if collection.get("mode") == "journal" else m.Document.doc_id
            rows = s.scalars(select(m.Document).where(m.Document.collection_id == collection["id"],
                                                      m.Document.space_id == str(space_id))
                             .order_by(order)).all()
        hits = [r for r in rows if all(r.data.get(k) == v for k, v in where.items())
                and not (hide and r.data.get(hide) is False)]
        return {"documents": [_hit(r) for r in hits[:limit]], "more": len(hits) > limit}

    def list_documents(self, collection: dict, space_id: Any, *, limit: int = 100, after: str | None = None,
                       session: Session | None = None) -> dict:
        """The admin listing: paginated by ``doc_id``."""
        limit = max(1, min(int(limit), 500))
        with self._use(collection, session) as s:
            q = select(m.Document).where(m.Document.collection_id == collection["id"], m.Document.space_id == str(space_id))
            if after:
                q = q.where(m.Document.doc_id > after)
            rows = s.scalars(q.order_by(m.Document.doc_id).limit(limit + 1)).all()
        return {"documents": [_row(r) for r in rows[:limit]], "more": len(rows) > limit}

    def delete_document(self, collection: dict, space_id: Any, doc_id: str, *, actor: str,
                        session: Session | None = None) -> dict:
        """Delete and return the document (the audit row's ``before`` carries it — F9).
        A journal's entries are never deleted."""
        if collection.get("mode") == "journal":
            raise Invalid(f"{collection['slug']} is a journal: entries are never deleted; append one that corrects it")
        with self._use(collection, session) as s:
            row = self._doc(s, collection["id"], str(space_id), str(doc_id))
            if row is None:
                raise NotFound(f"no document {doc_id!r} in {collection['slug']} for this space")
            out = _row(row)
            s.execute(delete(m.DocumentVector).where(m.DocumentVector.document_id == row.id))
            s.delete(row)
            s.flush()
            self._log_doc(collection, s, actor, "delete", f"{collection['slug']}/{space_id}/{doc_id}", before=out["data"])
            return out

    def read_all(self, collection: dict, space_id: Any, *, session: Session | None = None) -> list[dict]:
        """Every document of one space, in ``doc_id`` order, **uncapped** — what a host
        folds or indexes. ``find`` and ``list`` page and cap; a store built on them would
        silently lose rows once it outgrew the cap (review R6)."""
        with self._use(collection, session) as s:
            rows = s.scalars(select(m.Document).where(m.Document.collection_id == collection["id"],
                                                      m.Document.space_id == str(space_id))
                             .order_by(m.Document.doc_id)).all()
            return [_row(r) for r in rows]

    @staticmethod
    def next_id(name: str, *, session: Session, floor: int = 0) -> int:
        """The next value of the counter ``name``, never at or below ``floor`` — one
        statement, so two writers (the app and the seed CLI, which does not share the
        app's lock) can never draw the same number (review R4)."""
        return int(session.execute(text(
            "INSERT INTO kn_sequences (name, value) VALUES (:n, :f + 1) "
            "ON CONFLICT(name) DO UPDATE SET value = max(kn_sequences.value, :f) + 1 "
            "RETURNING value"), {"n": name, "f": int(floor)}).scalar_one())

    # ------------------------------------------------------------------ search

    def search_documents(self, collection: dict, space_id: Any, query: str, *, limit: int = SEARCH_LIMIT,
                         hide: str | None = None) -> dict:
        """Hybrid search of one space's documents: words (FTS5, BM25) and meaning
        (embeddings, cosine ≥ the embedder's ``min_similarity``), merged by rank (RRF).

        ``{"documents": [{…, "match": ["text"|"semantic", …]}], "semantic": state}`` where
        state is ``on``, ``partial`` (some documents still wait to be embedded), ``off``
        (no embedder) or ``unavailable`` (the embedder failed; the word hits still come
        back). No score is returned: it is not a fact about the document, and a number in a
        tool result is evidence the reply validators would accept.
        """
        if not isinstance(query, str) or not query.strip():
            raise Invalid("query must be non-empty text")
        fields = searchable_fields(collection)
        if not fields:
            raise Invalid(f"{collection['slug']} has no searchable fields")
        limit = max(1, min(int(limit or SEARCH_LIMIT), SEARCH_LIMIT))
        space_id = str(space_id)
        with self._session() as s:
            self._fill_search_text(s, collection, space_id, fields)
            by_text = self._text_hits(s, collection, space_id, query, hide)
        state, by_meaning = self._semantic_hits(collection, space_id, query, fields, hide)
        merged = rrf(by_text, by_meaning)[:limit]
        with self._session() as s:
            rows = {r.id: r for r in s.scalars(select(m.Document).where(m.Document.id.in_([i for i, _ in merged])))}
        sources = ("text", "semantic")
        return {"documents": [{**_hit(rows[i]), "match": [sources[w] for w in which]}
                              for i, which in merged if i in rows],
                "semantic": state}

    def _scope(self, collection: dict, space_id: str):
        return (m.Document.collection_id == collection["id"], m.Document.space_id == space_id)

    def _fill_search_text(self, s: Session, collection: dict, space_id: str, fields: list[str]) -> None:
        """Compute the text of rows that have none yet: older than the index, or after a
        definition change. The update trigger indexes them."""
        for row in s.scalars(select(m.Document).where(*self._scope(collection, space_id),
                                                      m.Document.search_text.is_(None))):
            row.search_text = search_text(fields, row.data)
        s.flush()

    def _text_hits(self, s: Session, collection: dict, space_id: str, query: str,
                   hide: str | None = None) -> list[int]:
        """Rows with any of the words, those with more of the distinct words first, then by
        BM25 — which alone can put a short row repeating one word above a row with all of them."""
        tokens = query_tokens(query)
        if not tokens:
            return []
        rows = s.execute(text(
            "SELECT d.id, d.search_text FROM kn_documents_fts JOIN kn_documents d ON d.id = kn_documents_fts.rowid "
            "WHERE kn_documents_fts MATCH :q AND d.collection_id = :c AND d.space_id = :s "
            # a soft-deleted record is skipped here, before the cut — never after it
            + ("AND coalesce(json_extract(d.data, :hide), 1) != 0 " if hide else "")
            + "ORDER BY bm25(kn_documents_fts) LIMIT :n"),
            {"q": fts_query(query), "c": collection["id"], "s": space_id, "n": TEXT_SCAN,
             "hide": f"$.{hide}" if hide else None}).all()
        covered = [(-len(tokens & query_tokens(row.search_text)), rank, row.id) for rank, row in enumerate(rows)]
        return [doc for _, _, doc in sorted(covered)[:CANDIDATES]]

    def _semantic_hits(self, collection: dict, space_id: str, query: str, fields: list[str],
                       hide: str | None = None) -> tuple[str, list[int]]:
        """Embed what is missing or stale (at most ``EMBED_PER_SEARCH``), then rank by
        cosine. The provider is called with no database session open."""
        embedder = self._embedder
        if embedder is None:
            return "off", []
        with self._session() as s:
            texts = {r.id: t for r in s.scalars(select(m.Document).where(*self._scope(collection, space_id)))
                     if (t := labelled_text(fields, r.data)) and not (hide and r.data.get(hide) is False)}
            cached = {v.document_id: v for v in s.scalars(select(m.DocumentVector).where(
                m.DocumentVector.document_id.in_(list(texts))))}
            hashes = {i: content_hash(embedder.model, t) for i, t in texts.items()}
            vectors = {i: unpack_vector(cached[i].vector) for i in texts
                       if i in cached and cached[i].content_hash == hashes[i]}
        stale = [i for i in texts if i not in vectors]
        todo = stale[:EMBED_PER_SEARCH]
        try:
            embedded = embedder.embed([query] + [texts[i] for i in todo])
        except EmbeddingError as exc:
            log.warning("search %s: semantic unavailable: %s", collection["slug"], exc)
            return "unavailable", []
        query_vector = unpack_vector(pack_vector(embedded[0]))
        if todo:
            with self._session() as s:
                for i, vector in zip(todo, embedded[1:]):
                    blob = pack_vector(vector)
                    s.merge(m.DocumentVector(document_id=i, content_hash=hashes[i], vector=blob))
                    vectors[i] = unpack_vector(blob)
        scored = sorted(((dot(query_vector, v), i) for i, v in vectors.items()), reverse=True)
        hits = [i for score, i in scored if score >= embedder.min_similarity][:CANDIDATES]
        return ("partial" if len(stale) > len(todo) else "on"), hits
