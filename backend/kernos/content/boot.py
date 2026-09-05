"""Boot seeding (design §0.3, plan Task 2.4; review findings 1, 2, 4).

On every start a host calls :func:`ensure_seeded` with today's configuration as
a ``ProfileSpec`` and the source files it came from. First run: business, its
sources, a ``managed_by="boot"`` profile with version 1 published (gates
bypassed — the seeded profile *is* today's behaviour), and the default manager
agent. Later runs: sources that changed on disk are re-put, and the profile is
republished only while it is still boot-managed and the stored spec differs. A
human publish flips ``managed_by`` and boot never touches the profile again.
"""
from __future__ import annotations

from kernos.content.spec import ProfileSpec
from kernos.content.store import ContentStore, source_etag

DEFAULT_PROFILE_NAME = "default"


def ensure_seeded(store: ContentStore, *, business_slug: str, business_name: str, spec: ProfileSpec,
                  agent_slug: str, agent_name: str, sources: list[dict] | None = None,
                  catalogue_rows: list[dict] | None = None, actor: str = "boot") -> dict:
    stored = spec.stored()
    out: dict = {"actions": []}

    try:
        business = store.get_business(business_slug)
    except Exception:
        business = store.create_business(business_slug, business_name, actor=actor, seed={"spec": stored})
        out["actions"].append("business created")
    bid = business["id"]

    # Sources first: a draft snapshots them, so they must exist before version 1
    # (finding 1) and track the files afterwards.
    existing = {(r["kind"], r["slug"]): r for r in store.list_sources(bid)}
    for src in sources or []:
        fm = src.get("frontmatter") or {}
        etag = source_etag(src["kind"], src["slug"], src.get("title", ""), src["body"], fm)
        row = existing.get((src["kind"], src["slug"]))
        if row is None or row["etag"] != etag:
            store.put_source(bid, src["kind"], src["slug"], body=src["body"], title=src.get("title", ""),
                             frontmatter=fm, actor=actor)
            out["actions"].append(f"source {src['kind']}/{src['slug']} {'created' if row is None else 'updated'}")

    profile = next((p for p in store.list_profiles(bid) if p["name"] == DEFAULT_PROFILE_NAME), None)
    if profile is None:
        profile = store.create_profile(bid, DEFAULT_PROFILE_NAME, actor=actor, managed_by="boot")
        out["actions"].append("profile created")
    pid = profile["id"]

    published = store.published_spec(pid)
    if published is None:
        draft = store.create_draft(pid, actor=actor, base_spec=stored, note="boot: seeded from code and env")
        store.publish(draft["id"], actor=actor, bypass_gates=True)
        out["actions"].append("version 1 published")
    elif profile["managed_by"] == "boot":
        draft = store.create_draft(pid, actor=actor, base_spec=stored, note="boot: code or env changed")
        if store.get_version(draft["id"])["spec"] == published:
            store.retire(draft["id"], actor=actor)            # nothing changed; no churn
        else:
            store.publish(draft["id"], actor=actor, bypass_gates=True)
            out["actions"].append("republished (code or env changed)")

    agent = store.default_agent(bid)
    if agent is None:
        agent = store.create_agent(bid, agent_slug, agent_name, profile_id=pid, actor=actor,
                                   role="manager", is_default=True)
        out["actions"].append("default agent created")

    for row in catalogue_rows or []:
        if store.get_model(row["model_id"]) is None:
            store.upsert_model(**row)
            out["actions"].append(f"catalogue {row['model_id']} added")

    profile = store.get_profile(pid)
    out.update(business_id=bid, profile_id=pid, agent_id=agent["id"],
               version_id=profile["published_version_id"], managed_by=profile["managed_by"])
    return out
