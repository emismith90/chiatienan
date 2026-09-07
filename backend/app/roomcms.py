"""The room's own view of the agent it runs (plan Phase 11).

A door into the content plane for people who are not operators. Everything here is
about what that door does **not** open, so the rules are stated once, at the top:

* **Editing needs a binding, not membership** (review F1). ``POST /api/rooms/create`` is
  public by design, and a room with no ``kn_space_bindings`` row falls back to the
  business's *default* agent — the same profile the real room runs. So "is a member of
  the room in the URL" would let anyone on the internet republish the real bot. Only a
  space with a binding of its own may edit; every other space is view-only.
* **Editing is scoped** (D1). ``ROOM_EDITABLE`` names what a member may change, enforced
  through :func:`kernos.content.gates.outside_scope`, which also refuses everything in
  ``NEVER_IN_SCOPE`` — the models, the pipeline, the packs, the caps, blocking
  validators. The operator's admin password keeps all of that; a room member never has it.
* **A rule's identity is its slug** (review F2). ``kernos.content.sources`` holds that
  guard because the proposal path needs it too.
* **Publish first, sources second** (review F6), the order ``approve_proposal`` uses: a
  gate failure must not leave a rewritten source behind for the next draft to pick up.
* **The concurrency check is inside the publish transaction** (review F7). Comparing
  version ids in a route is a race, not a check.

What a member *cannot* be stopped from doing is written down in the README and in the
plan's D9: with ``bash`` enabled, prose can still talk the model into arithmetic, and
``backed_amounts`` counts a builtin's output as evidence. The size cap here is a floor
under that, not a fix for it.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

from kernos.content import Conflict, ContentError, GateError, Invalid, NotFound, PreconditionFailed
from kernos.content.gates import changed_paths, outside_scope
from kernos.content.sources import duplicate_keys, protected_changes, source_changes
from kernos.osadmin import _unified

#: What a room member may change. Everything else — models, caps, pipeline, tool packs,
#: builtin tools, blocking validators, money-tagged rules — stays with the operator.
ROOM_EDITABLE = ("prompt.body", "prompt.append", "skills", "rules")
#: A ceiling on what one room can make every turn carry (review F9).
MAX_CONTENT_BYTES = 32_768
#: Rule slugs and skill names become `/virtual/<slug>` context-file paths in the sidecar
#: and `kn_sources.slug` rows (review F14).
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
#: The revision list a room reads; older versions stay reachable through the admin API.
VERSION_LIMIT = 50


def _wrap(fn):
    """Content-plane errors as HTTP, the way the admin router maps them."""
    try:
        return fn()
    except GateError as exc:
        raise HTTPException(422, {"gates": [{"gate": g, "message": m} for g, m in exc.failures]}) from exc
    except ContentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


def _space(kernel, room_id: int) -> dict:
    info = kernel.agent_space(room_id)
    if not info or not info.get("agent") or not info.get("profile_id"):
        raise HTTPException(404, "this room has no agent")
    return info


def _editable_space(kernel, room_id: int) -> dict:
    """The space, or 403 when it may only look (F1)."""
    info = _space(kernel, room_id)
    if not info.get("bound"):
        raise HTTPException(403, "this room runs the shared default bot; ask an operator to "
                                 "bind it to an agent before editing")
    return info


def _actor(member_id: int) -> str:
    return f"member:{member_id}"


def _editable(spec: dict, protected: set[str]) -> dict:
    return {
        "prompt_body": (spec.get("prompt") or {}).get("body") or "",
        "prompt_append": list((spec.get("prompt") or {}).get("append") or []),
        "skills": [{"name": s["name"], "description": s.get("description", ""), "body": s.get("body", "")}
                   for s in spec.get("skills") or []],
        "rules": [{"slug": r["slug"], "content": r.get("content", ""), "tags": list(r.get("tags") or []),
                   "editable": r["slug"] not in protected}
                  for r in spec.get("rules") or []],
    }


def view(kernel, room_id: int) -> dict:
    """What the room runs, split into what a member may change and what they may not."""
    info = _space(kernel, room_id)
    pid = info["profile_id"]
    spec = kernel.store.published_spec(pid) or {}
    profile = kernel.store.get_profile(pid)
    business = kernel.store.get_business(profile["business_id"])
    version = kernel.store.get_version(profile["published_version_id"]) if profile["published_version_id"] else None
    protected = {r["slug"] for r in spec.get("rules") or [] if "money" in (r.get("tags") or [])}

    etags = {}
    for kind, slug in ([("skill", s["name"]) for s in spec.get("skills") or []]
                       + [("rule", r["slug"]) for r in spec.get("rules") or []] + [("prompt", "system")]):
        try:
            etags[f"{kind}/{slug}"] = kernel.store.get_source(profile["business_id"], kind, slug)["etag"]
        except NotFound:
            pass

    return {
        "agent": {"slug": info["agent"]["slug"], "name": info["agent"]["name"],
                  "persona": spec.get("persona") or {}},
        "profile": {"id": pid, "name": profile["name"], "business": business["slug"],
                    # once a person publishes, boot stops refreshing this profile from
                    # code — the panel says so out loud (review F8)
                    "managed_by": profile["managed_by"]},
        "version": {"id": version["id"], "version": version["version"], "actor": version["actor"],
                    "note": version["note"], "published_at": version["published_at"]} if version else None,
        "editable": _editable(spec, protected),
        "readonly": {
            "models": spec.get("models") or {},
            "caps": spec.get("caps") or {},
            "builtin_tools": list(spec.get("builtin_tools") or []),
            "tool_packs": [t["pack"] for t in spec.get("tool_packs") or []],
            # stage names only: a plugin's config can carry prose (review F16)
            "pipeline_stages": sorted((spec.get("pipeline") or {}).keys()),
        },
        "source_etags": etags,
        "can_edit": bool(info.get("bound")),
        "shared": info.get("source") == "default",
        "scope": list(ROOM_EDITABLE),
    }


def versions(kernel, room_id: int) -> list[dict]:
    """The revision log, newest first, with what each version changed."""
    info = _space(kernel, room_id)
    rows = kernel.store.list_versions(info["profile_id"])
    by_version = {r["version"]: r for r in rows}
    out = []
    for row in sorted(rows, key=lambda r: r["version"], reverse=True)[:VERSION_LIMIT]:
        previous = by_version.get(row["version"] - 1)
        spec = kernel.store.get_version(row["id"])["spec"]
        prev_spec = kernel.store.get_version(previous["id"])["spec"] if previous else None
        out.append({"id": row["id"], "version": row["version"], "status": row["status"],
                    "actor": row["actor"], "note": row["note"], "created_at": row["created_at"],
                    "published_at": row["published_at"],
                    "paths": changed_paths(prev_spec, spec)})
    return out


def version_detail(kernel, room_id: int, version: int) -> dict:
    """One version's content and a diff against the version before it."""
    info = _space(kernel, room_id)
    row = _wrap(lambda: kernel.store.find_version(info["profile_id"], version))
    spec = kernel.store.get_version(row["id"])["spec"]
    previous = next((v for v in kernel.store.list_versions(info["profile_id"])
                     if v["version"] == version - 1), None)
    prev_spec = kernel.store.get_version(previous["id"])["spec"] if previous else None
    protected = {r["slug"] for r in spec.get("rules") or [] if "money" in (r.get("tags") or [])}
    return {"version": row["version"], "status": row["status"], "actor": row["actor"],
            "note": row["note"], "created_at": row["created_at"], "published_at": row["published_at"],
            "editable": _editable(spec, protected),
            "paths": changed_paths(prev_spec, spec),
            "diff": _unified(prev_spec or {}, spec, f"v{version}")}


def _validate_names(payload: dict) -> None:
    for skill in payload.get("skills") or []:
        if not SLUG_RE.match(str(skill.get("name") or "")):
            raise HTTPException(400, f"skill name {skill.get('name')!r} must match {SLUG_RE.pattern}")
    for rule in payload.get("rules") or []:
        if not SLUG_RE.match(str(rule.get("slug") or "")):
            raise HTTPException(400, f"rule slug {rule.get('slug')!r} must match {SLUG_RE.pattern}")


def _patch_from(payload: dict, published: dict) -> dict:
    """Only the fields the member actually sent. ``update_draft`` deep-merges and lists
    replace wholesale, so a submitted `rules` list is the complete new list."""
    patch: dict[str, Any] = {}
    prompt = {}
    if payload.get("prompt_body") is not None:
        prompt["body"] = payload["prompt_body"]
    if payload.get("prompt_append") is not None:
        prompt["append"] = list(payload["prompt_append"])
    if prompt:
        patch["prompt"] = {**(published.get("prompt") or {}), **prompt}
    if payload.get("skills") is not None:
        old = {s["name"]: s for s in published.get("skills") or []}
        patch["skills"] = [{"name": s["name"], "description": s.get("description", ""),
                            "body": s.get("body", ""),
                            "delivery": old.get(s["name"], {}).get("delivery", "inline")}
                           for s in payload["skills"]]
    if payload.get("rules") is not None:
        old = {r["slug"]: r for r in published.get("rules") or []}
        patch["rules"] = [{"slug": r["slug"], "content": r.get("content", ""),
                           # a member never sets tags: they carry over from the published
                           # rule, so `money` can be neither added nor dropped here
                           "tags": list(old.get(r["slug"], {}).get("tags") or [])}
                          for r in payload["rules"]]
    if not patch:
        raise HTTPException(400, "nothing to change")
    return patch


def _size(patch: dict) -> int:
    body = (patch.get("prompt") or {}).get("body") or ""
    parts = [body, *((patch.get("prompt") or {}).get("append") or [])]
    parts += [s.get("body", "") for s in patch.get("skills") or []]
    parts += [r.get("content", "") for r in patch.get("rules") or []]
    return sum(len(p.encode("utf-8")) for p in parts)


def edit(kernel, room_id: int, member_id: int, payload: dict) -> dict:
    """Publish a member's change to the profile this room runs."""
    info = _editable_space(kernel, room_id)
    pid, bid = info["profile_id"], info["agent"]["business_id"]
    actor = _actor(member_id)
    profile = kernel.store.get_profile(pid)
    published = kernel.store.published_spec(pid) or {}
    base = payload.get("base_version_id")
    if base != profile["published_version_id"]:
        raise HTTPException(409, f"this room now runs version id {profile['published_version_id']}, "
                                 f"not {base}; reload and try again")

    _validate_names(payload)
    patch = _patch_from(payload, published)
    if _size(patch) > MAX_CONTENT_BYTES:
        raise HTTPException(413, f"the prompt, skills and rules together must stay under "
                                 f"{MAX_CONTENT_BYTES // 1024} KB — every turn carries them")

    note = (payload.get("note") or "").strip() or f"room edit by {actor}"
    draft = kernel.store.create_draft(pid, actor=actor, snapshot=False, note=note)
    try:
        version = _wrap(lambda: kernel.store.update_draft(draft["id"], patch, actor=actor))
        spec = version["spec"]
        # protected_changes first: it names the slug, where outside_scope only reports
        # the generic `rules[tag=money]` path
        for problem in (duplicate_keys(spec), protected_changes(published, spec),
                        outside_scope(published, spec, list(ROOM_EDITABLE))):
            if problem:
                raise HTTPException(400, {"refused": problem, "scope": list(ROOM_EDITABLE)})

        changes = source_changes(published, spec, etag_of=lambda k, sl: kernel._source_etag(bid, k, sl))
        sent = payload.get("source_etags") or {}
        for change in changes:
            key = f"{change['kind']}/{change['slug']}"
            if key in sent and sent[key] != change["if_match"]:
                raise PreconditionFailed(f"source {key} changed since you loaded it; reload and try again")

        _wrap(lambda: kernel.store.publish(version["id"], actor=actor, gates=kernel.gates, note=note,
                                           override_reason=f"room edit by {actor}: {note}",
                                           if_published=base))
    except BaseException:
        # nothing published: drop the draft rather than leave it in the room's history,
        # and leave every source untouched (review F6)
        kernel.store.retire(draft["id"], actor=actor)
        raise
    kernel.store.apply_source_changes(bid, changes, actor=actor, audit={"room": room_id, "version": version["id"]})
    return {"version": version["version"], "version_id": version["id"],
            "paths": changed_paths(published, spec), "actor": actor}


def republish(kernel, room_id: int, member_id: int, version: int, note: str | None = None) -> dict:
    """Put an earlier version's content back, as a **new** version.

    Not ``store.rollback``: that re-publishes the same row and overwrites its ``note``
    and ``published_at``, which loses exactly the history this feature exists to keep
    (review F3). Drafting from the target instead leaves every earlier row intact and
    gives the member's note somewhere to live.
    """
    info = _editable_space(kernel, room_id)
    pid, bid = info["profile_id"], info["agent"]["business_id"]
    actor = _actor(member_id)
    profile = kernel.store.get_profile(pid)
    published = kernel.store.published_spec(pid) or {}
    target = _wrap(lambda: kernel.store.find_version(pid, version))
    if target["id"] == profile["published_version_id"]:
        raise HTTPException(409, f"version {version} is already what this room runs")
    target_spec = kernel.store.get_version(target["id"])["spec"]

    # the same scope that governs an edit governs going back to one (review F4): an old
    # version may carry a model or a cap a member was never allowed to choose
    for problem in (protected_changes(published, target_spec),
                    outside_scope(published, target_spec, list(ROOM_EDITABLE))):
        if problem:
            raise HTTPException(403, {"refused": problem,
                                      "message": f"version {version} changes things a room member cannot; "
                                                 "an operator can republish it from the admin API"})

    note = (note or "").strip() or f"republished v{version} by {actor}"
    draft = kernel.store.create_draft(pid, actor=actor, from_version=target["version"], snapshot=False, note=note)
    try:
        _wrap(lambda: kernel.store.publish(draft["id"], actor=actor, gates=kernel.gates, note=note,
                                           override_reason=f"republish of v{version} by {actor}",
                                           skip_probe=True, skip_eval=True,
                                           if_published=profile["published_version_id"]))
    except BaseException:
        kernel.store.retire(draft["id"], actor=actor)
        raise
    # the sources have to come back too, or the next snapshotting draft undoes this
    # (review F5)
    changes = source_changes(published, target_spec, etag_of=lambda k, sl: kernel._source_etag(bid, k, sl))
    kernel.store.apply_source_changes(bid, changes, actor=actor,
                                      audit={"room": room_id, "republished": version})
    new = kernel.store.get_version(draft["id"])
    return {"version": new["version"], "version_id": new["id"], "from_version": version, "actor": actor}
