"""Internal collections: stores a host builds on the data plane (notes, places, the ledger).

They belong to a reserved business, ``_system``, never to the business a room is bound
to: re-binding a room hides the documents of its old business's collections (by design,
for user collections), and a room's notes, places and money must never vanish with a
binding (review of 2026-10-02, D1). ``_system`` has no agents, so no room can bind to it,
and an internal collection generates no agent tools (D2).

A host declares its internal collections in code and calls :func:`ensure_internal` from
its schema setup; it is idempotent, and a changed declaration replaces the stored one
(the code owns it).
"""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select

from kernos.content import models as m
from kernos.content.errors import NotFound

SYSTEM_BUSINESS = "_system"
SYSTEM_ACTOR = "system"


def system_business_id(session_factory: Callable[[], Any]) -> int:
    """The ``_system`` business, created on first use."""
    with session_factory() as s:
        b = s.scalar(select(m.Business).where(m.Business.slug == SYSTEM_BUSINESS))
        if b is None:
            b = m.Business(slug=SYSTEM_BUSINESS, name="System",
                           description="Internal stores (notes, places, ledger). Not bindable.")
            s.add(b)
            s.flush()
        return b.id


def ensure_internal(data, session_factory: Callable[[], Any], specs: list[dict]) -> dict[str, dict]:
    """Create or update each declared internal collection; ``{slug: collection}``.

    A spec is ``{slug, name, schema, key, mode?, searchable?, indexed?, description?,
    options?}`` (``options``: :func:`kernos.data.actions.check_options`).
    """
    from kernos.data.search import default_searchable

    bid = system_business_id(session_factory)
    out = {}
    for spec in specs:
        want = {"name": spec["name"], "schema": spec["schema"], "key": spec["key"],
                "indexed": list(spec.get("indexed", ())), "description": spec.get("description", ""),
                "mode": spec.get("mode", "table"),
                "searchable": (default_searchable(spec["schema"]) if spec.get("searchable") is None
                               else list(spec["searchable"])),
                "options": spec.get("options")}
        try:
            have = data.get_collection(bid, spec["slug"])
        except NotFound:
            have = None
        # Unchanged: skip. A put re-validates every document and clears the search text,
        # which every boot would otherwise pay for.
        if have is not None and have.get("internal") and all(have.get(k) == v for k, v in want.items()):
            out[spec["slug"]] = have
            continue
        out[spec["slug"]] = data.put_collection(
            bid, spec["slug"], name=spec["name"], schema=spec["schema"], key=spec["key"],
            indexed=spec.get("indexed", ()), description=spec.get("description", ""),
            mode=spec.get("mode", "table"), searchable=spec.get("searchable"),
            internal=True, force=True, options=spec.get("options"), actor=SYSTEM_ACTOR)
    return out


def internal_collection(data, session_factory: Callable[[], Any], slug: str) -> dict:
    """One internal collection by slug. Raises :class:`NotFound` before
    :func:`ensure_internal` has declared it."""
    col = data.get_collection(system_business_id(session_factory), slug)
    if not col.get("internal"):
        raise NotFound(f"{slug!r} is not an internal collection")
    return col
