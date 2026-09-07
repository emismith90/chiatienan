"""``DataStore``: collections (content) and documents (data) — design §5.3, plan Phase 5.

Definitions are audited content and refuse ``agent:*`` actors (an agent may not
rewrite its own tools outside a proposal — review F2); documents are what an agent
remembers on a space's behalf and stay agent-writable, audited with the actor.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

import jsonschema
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kernos.content import models as m
from kernos.content.errors import Conflict, Invalid, NotFound
from kernos.data.schema import DOC_ID_RE, SLUG_RE, SchemaError, check_collection_schema

#: Documents per (space, collection). `find` loads a space's rows of one collection into
#: Python; this is what makes that bounded (review F7).
MAX_DOCUMENTS = 1000
#: The most rows a generated `find` returns.
FIND_LIMIT = 50


def _row(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def generated_tool_names(slug: str) -> tuple[str, str, str]:
    return f"{slug}_find", f"{slug}_upsert", f"{slug}_delete"


class DataStore:
    def __init__(self, session_factory: Callable[[], Any], *, audit: Callable | None = None) -> None:
        self._session = session_factory
        self._audit = audit            # ContentStore.log(session, actor, action, entity, entity_id, before, after)

    def _log(self, s: Session, actor: str, action: str, entity: str, entity_id: Any, before=None, after=None) -> None:
        if self._audit is not None:
            self._audit(s, actor, action, entity, entity_id, before=before, after=after)

    # ---------------------------------------------------------------- collections

    def put_collection(self, business_id: int, slug: str, *, name: str, schema: dict, key: str,
                       indexed: Iterable[str] = (), description: str = "", actor: str,
                       reserved: Iterable[str] = (), force: bool = False) -> dict:
        """Create or update a definition. Refused for an ``agent:*`` actor, a slug outside
        ``[a-z][a-z0-9_]{0,56}``, a schema outside the sidecar-safe subset, a generated
        tool name another pack owns, or — with documents — a schema an existing document
        no longer satisfies (unless ``force``)."""
        if actor.startswith("agent:"):
            raise Invalid("an agent may not define or change a collection; propose it instead")
        if not SLUG_RE.match(slug or ""):
            raise Invalid(f"collection slug {slug!r} must match [a-z][a-z0-9_]{{0,56}}")
        indexed = list(indexed)
        try:
            check_collection_schema(schema, key=key, indexed=indexed)
        except SchemaError as exc:
            raise Invalid(str(exc)) from exc
        clash = sorted(set(generated_tool_names(slug)) & set(reserved))
        if clash:
            raise Conflict(f"collection {slug!r} would generate tool names another pack owns: {clash}")
        with self._session() as s:
            if s.get(m.Business, business_id) is None:
                raise NotFound(f"no business #{business_id}")
            row = s.scalar(select(m.Collection).where(m.Collection.business_id == business_id, m.Collection.slug == slug))
            before = _row(row) if row else None
            if row is None:
                row = m.Collection(business_id=business_id, slug=slug, schema=schema, key=key, name=name)
                s.add(row)
            else:
                bad = []
                for doc in s.scalars(select(m.Document).where(m.Document.collection_id == row.id)).all():
                    try:
                        jsonschema.validate(doc.data, schema)
                    except jsonschema.ValidationError:
                        bad.append(doc.doc_id)
                if bad and not force:
                    raise Conflict(f"{len(bad)} existing document(s) no longer satisfy the schema: "
                                   f"{bad[:10]} — fix them or pass force")
            row.name, row.description, row.schema, row.key, row.indexed = name, description, schema, key, indexed
            row.updated_at = m.utcnow()
            s.flush()
            self._log(s, actor, "put", "collection", f"{business_id}/{slug}",
                      before={"schema": before["schema"], "key": before["key"], "indexed": before["indexed"]} if before else None,
                      after={"schema": schema, "key": key, "indexed": indexed})
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

    def upsert_document(self, collection: dict, space_id: Any, data: dict, *, actor: str) -> dict:
        """Validate ``data`` and write it; the document id is ``data[key]``. Raises
        :class:`Invalid` on a schema error or a bad id, :class:`Conflict` when the space's
        collection is full."""
        problem = self.validate(collection, data)
        if problem:
            raise Invalid(f"document does not match the {collection['slug']} schema: {problem}")
        doc_id = str(data[collection["key"]])
        if not DOC_ID_RE.match(doc_id):
            raise Invalid(f"{collection['key']}={doc_id!r} must match {DOC_ID_RE.pattern}")
        space_id = str(space_id)
        with self._session() as s:
            row = self._doc(s, collection["id"], space_id, doc_id)
            before = row.data if row else None
            if row is None:
                n = s.scalar(select(func.count()).select_from(m.Document).where(
                    m.Document.collection_id == collection["id"], m.Document.space_id == space_id))
                if n >= MAX_DOCUMENTS:
                    raise Conflict(f"{collection['slug']} already holds {MAX_DOCUMENTS} documents in this space; delete some first")
                row = m.Document(collection_id=collection["id"], space_id=space_id, doc_id=doc_id, data=data,
                                 created_by=actor, updated_by=actor)
                s.add(row)
            else:
                row.data, row.updated_by, row.updated_at = data, actor, m.utcnow()
            s.flush()
            self._log(s, actor, "upsert", "document", f"{collection['slug']}/{space_id}/{doc_id}", before=before, after=data)
            return _row(row)

    def get_document(self, collection: dict, space_id: Any, doc_id: str) -> dict | None:
        with self._session() as s:
            row = self._doc(s, collection["id"], str(space_id), str(doc_id))
            return _row(row) if row else None

    def find_documents(self, collection: dict, space_id: Any, *, where: dict | None = None,
                       limit: int = FIND_LIMIT) -> dict:
        """``{"documents": [...], "more": bool}`` — equality on ``indexed`` fields only, in
        ``doc_id`` order, at most ``limit`` (≤ FIND_LIMIT). Never a count."""
        where = dict(where or {})
        unknown = sorted(set(where) - set(collection["indexed"]))
        if unknown:
            raise Invalid(f"{collection['slug']}_find can only filter by {collection['indexed']}; not {unknown}")
        limit = max(1, min(int(limit or FIND_LIMIT), FIND_LIMIT))
        with self._session() as s:
            rows = s.scalars(select(m.Document).where(m.Document.collection_id == collection["id"],
                                                      m.Document.space_id == str(space_id))
                             .order_by(m.Document.doc_id)).all()
        hits = [r for r in rows if all(r.data.get(k) == v for k, v in where.items())]
        return {"documents": [{"doc_id": r.doc_id, "data": r.data, "updated_at": r.updated_at} for r in hits[:limit]],
                "more": len(hits) > limit}

    def list_documents(self, collection: dict, space_id: Any, *, limit: int = 100, after: str | None = None) -> dict:
        """The admin listing: paginated by ``doc_id``."""
        limit = max(1, min(int(limit), 500))
        with self._session() as s:
            q = select(m.Document).where(m.Document.collection_id == collection["id"], m.Document.space_id == str(space_id))
            if after:
                q = q.where(m.Document.doc_id > after)
            rows = s.scalars(q.order_by(m.Document.doc_id).limit(limit + 1)).all()
        return {"documents": [_row(r) for r in rows[:limit]], "more": len(rows) > limit}

    def delete_document(self, collection: dict, space_id: Any, doc_id: str, *, actor: str) -> dict:
        """Delete and return the document (the audit row's ``before`` carries it — F9)."""
        with self._session() as s:
            row = self._doc(s, collection["id"], str(space_id), str(doc_id))
            if row is None:
                raise NotFound(f"no document {doc_id!r} in {collection['slug']} for this space")
            out = _row(row)
            s.delete(row)
            self._log(s, actor, "delete", "document", f"{collection['slug']}/{space_id}/{doc_id}", before=out["data"])
            return out
