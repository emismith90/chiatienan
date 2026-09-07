"""Deriving source rows from two specs (plan Phase 11.0; Phase 8 review F10, Phase 11 F2/F12).

``kn_sources`` is upstream of every draft: ``create_draft(snapshot=True)`` pulls the
business's current sources into the new version. So a change written only into a
version's ``spec`` is silently reverted by the next snapshotting draft — the Phase 8
lesson. Anything that publishes a changed spec must therefore write the matching
sources too, and both the proposal path (``BaseKernel.approve_proposal``) and the room
editor (Phase 11) do it through :func:`source_changes` so the guard below lives in one
place.

**The guard (Phase 11 review F2).** A rule's identity is its **slug**, not its tags.
``kn_sources`` is unique on ``(business_id, kind, slug)``, so writing a rule
``{slug: "money-safety", tags: []}`` replaces the money-tagged source's body *and*
strips its tag — after which the next snapshotting draft carries the untagged rewrite
and the money rule is gone from every future version. Filtering on the *draft's* tags
cannot catch that, because the submitted rule is untagged by construction. So the
protected slugs come from the **published** spec, and a change to one is refused.
"""
from __future__ import annotations

from typing import Any, Callable


def money_slugs(published: dict) -> frozenset[str]:
    """The rule slugs the published spec protects. Identity is the slug: a submitted
    rule reusing one of these is refused however it is tagged."""
    return frozenset(r["slug"] for r in (published.get("rules") or [])
                     if "money" in (r.get("tags") or []))


def money_rules(spec: dict) -> dict[str, dict]:
    """The money-tagged rules of a spec, by slug — for asserting they came through an
    edit byte-identical."""
    return {r["slug"]: r for r in (spec.get("rules") or []) if "money" in (r.get("tags") or [])}


def protected_changes(published: dict, draft: dict) -> list[str]:
    """Why ``draft`` may not be published by a non-operator, in money terms: a protected
    rule reused by slug, altered, or dropped. Empty means the money rules survived."""
    protected, out = money_slugs(published), []
    by_slug = {r["slug"]: r for r in (draft.get("rules") or [])}
    before = money_rules(published)
    for slug in sorted(protected):
        rule = by_slug.get(slug)
        if rule is None:
            out.append(f"rules/{slug} (a money rule) was removed")
        elif rule != before[slug]:
            out.append(f"rules/{slug} is a money rule and cannot be changed here")
    return out


def duplicate_keys(draft: dict) -> list[str]:
    """Skill names or rule slugs submitted twice in one spec — ambiguous, and a way to
    smuggle a second write to one source row."""
    out = []
    for label, items, key in (("skills", draft.get("skills") or [], "name"),
                              ("rules", draft.get("rules") or [], "slug")):
        seen: set[str] = set()
        for item in items:
            k = item.get(key)
            if k in seen:
                out.append(f"{label}/{k} appears more than once")
            seen.add(k)
    return out


def source_changes(published: dict, draft: dict, *, etag_of: Callable[[str, str], Any]) -> list[dict]:
    """The source rows that must follow ``draft``: every skill, non-money rule and the
    system prompt that differs from ``published``, each with the etag its source has now
    (``None`` when it does not exist) so the write can be applied with ``if_match``.

    Derived from the two specs rather than from what a caller edited, so a proposal made
    turns after its draft still carries them. A rule whose slug is protected by
    ``published`` is never emitted — callers must have refused it already
    (``protected_changes``); this is the second lock on the same door.
    """
    protected = money_slugs(published)
    out: list[dict] = []

    old_skills = {s["name"]: s for s in published.get("skills", [])}
    for sk in draft.get("skills", []):
        if old_skills.get(sk["name"]) != sk:
            out.append({"kind": "skill", "slug": sk["name"], "body": sk["body"], "title": sk["name"],
                        "frontmatter": {"description": sk.get("description", ""),
                                        "delivery": sk.get("delivery", "inline")},
                        "if_match": etag_of("skill", sk["name"])})

    old_rules = {r["slug"]: r for r in published.get("rules", [])}
    for r in draft.get("rules", []):
        if r["slug"] in protected or "money" in (r.get("tags") or []):
            continue
        if old_rules.get(r["slug"]) != r:
            out.append({"kind": "rule", "slug": r["slug"], "body": r["content"], "title": r["slug"],
                        "frontmatter": {"tags": list(r.get("tags") or [])},
                        "if_match": etag_of("rule", r["slug"])})

    new_body = (draft.get("prompt") or {}).get("body")
    if new_body != (published.get("prompt") or {}).get("body"):
        out.append({"kind": "prompt", "slug": "system", "body": new_body or "", "title": "system",
                    "frontmatter": {}, "if_match": etag_of("prompt", "system")})
    return out
