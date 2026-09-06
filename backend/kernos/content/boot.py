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
        # a source a human or an agent has edited is theirs now — boot never reverts it
        # (Phase 8 review F2; mirrors `managed_by`)
        if row is None or (row["etag"] != etag and row["updated_by"] == actor):
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


def ensure_sub_agent(store: ContentStore, business_id: int, *, slug: str, name: str, spec: ProfileSpec,
                     description: str = "", capabilities: dict | None = None,
                     profile_name: str | None = None, actor: str = "boot") -> dict:
    """Seed a **sub**-agent with a profile of its own, once (plan Phase 10.2).

    Idempotent and non-destructive in the way boot has to be: an agent that already
    exists keeps everything an operator has changed about it — capabilities above all —
    and its profile is republished only while it is still ``managed_by="boot"`` and the
    stored spec differs, the same rule :func:`ensure_seeded` uses for the default one.

    It deliberately does **not** wire any manager's ``delegates_to``. Delegation adds an
    ``ask_<slug>`` tool to every space that manager runs, which is a change to what a
    live room's bot can do; boot creates the parts, an operator connects them.
    """
    out: dict = {"actions": []}
    stored = spec.stored()
    pname = profile_name or slug

    profile = next((p for p in store.list_profiles(business_id) if p["name"] == pname), None)
    if profile is None:
        profile = store.create_profile(business_id, pname, actor=actor, managed_by="boot")
        out["actions"].append(f"profile {pname} created")
    pid = profile["id"]

    published = store.published_spec(pid)
    if published is None:
        draft = store.create_draft(pid, actor=actor, base_spec=stored, snapshot=False,
                                   note=f"boot: seeded {pname} from code")
        store.publish(draft["id"], actor=actor, bypass_gates=True)
        out["actions"].append(f"{pname} version 1 published")
    elif profile["managed_by"] == "boot":
        draft = store.create_draft(pid, actor=actor, base_spec=stored, snapshot=False,
                                   note=f"boot: {pname} changed in code")
        if store.get_version(draft["id"])["spec"] == published:
            store.retire(draft["id"], actor=actor)            # nothing changed; no churn
        else:
            store.publish(draft["id"], actor=actor, bypass_gates=True)
            out["actions"].append(f"{pname} republished (code changed)")

    agent = next((a for a in store.list_agents(business_id) if a["slug"] == slug), None)
    if agent is None:
        agent = store.create_agent(business_id, slug, name, profile_id=pid, actor=actor, role="sub",
                                   description=description, capabilities=capabilities or {})
        out["actions"].append(f"sub-agent {slug} created")

    profile = store.get_profile(pid)
    out.update(agent_id=agent["id"], profile_id=pid, version_id=profile["published_version_id"],
               slug=slug, managed_by=profile["managed_by"])
    return out
