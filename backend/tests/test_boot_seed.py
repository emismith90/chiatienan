"""Boot seeding is idempotent, tracks code/env while boot-managed, and stops after a
human publish (plan Task 2.4; review findings 1, 2)."""
from app import agent
from app.config import settings
from app.default_profile import build_default_spec
from app.kernel import BUSINESS_SLUG, Kernel, catalogue_rows, default_sources, kernel_for
import pytest

from kernos.content import GateError, Models, ensure_seeded


def test_first_boot_seeds_business_sources_profile_version_agent_and_catalogue(db):
    k = kernel_for(db)
    r = k.seed_report
    assert "business created" in r["actions"] and "version 1 published" in r["actions"]
    assert r["managed_by"] == "boot" and r["version_id"] is not None
    kinds = {(s["kind"], s["slug"]) for s in k.store.list_sources(r["business_id"])}
    assert ("rule", "money-safety") in kinds and ("skill", "record-meal") in kinds
    assert k.store.get_source(r["business_id"], "rule", "money-safety")["frontmatter"] == {"tags": ["money"]}
    assert k.store.default_agent(BUSINESS_SLUG)["slug"] == "phoenix"
    assert {m["model_id"] for m in k.store.list_models()} == {settings.pi_model, settings.pi_vision_model}
    # the resolved spec IS today's configuration, runtime included
    resolved = k.resolve("1")
    assert resolved == build_default_spec(settings)
    assert resolved.to_engine_spec() == agent.default_engine_spec()


def test_second_boot_is_a_no_op_and_snapshot_equals_seed(db):
    k = kernel_for(db)
    store, r = k.store, k.seed_report
    again = ensure_seeded(store, business_slug=BUSINESS_SLUG, business_name="x", spec=build_default_spec(settings),
                          agent_slug="phoenix", agent_name="Phoenix", sources=default_sources(),
                          catalogue_rows=catalogue_rows(settings))
    assert again["actions"] == [] and again["version_id"] == r["version_id"]
    # a human draft from the untouched seeded profile equals the published spec (finding 1)
    d = store.create_draft(r["profile_id"], actor="admin")
    assert store.get_version(d["id"])["spec"] == store.published_spec(r["profile_id"])
    assert "runtime" not in store.published_spec(r["profile_id"])


def test_env_change_republishes_while_boot_managed_then_stops_after_a_human_publish(db):
    k = kernel_for(db)
    store, r = k.store, k.seed_report
    changed = build_default_spec(settings).model_copy(update={"models": Models(text="new/model", vision=None, thinking="low")})
    rep = ensure_seeded(store, business_slug=BUSINESS_SLUG, business_name="x", spec=changed,
                        agent_slug="phoenix", agent_name="Phoenix", sources=default_sources())
    assert "republished (code or env changed)" in rep["actions"] and rep["version_id"] != r["version_id"]
    assert k.resolve("1").models.text == "new/model"
    # a human publish takes over
    d = store.create_draft(r["profile_id"], actor="admin")
    store.publish(d["id"], actor="admin", gates=k.gates, override_reason="taking over")
    assert store.get_profile(r["profile_id"])["managed_by"] == "human"
    rep2 = ensure_seeded(store, business_slug=BUSINESS_SLUG, business_name="x", spec=build_default_spec(settings),
                         agent_slug="phoenix", agent_name="Phoenix", sources=default_sources())
    assert rep2["actions"] == [] and store.get_profile(r["profile_id"])["published_version_id"] == d["id"]


def test_publishing_through_the_kernel_gates_passes_for_the_seeded_models(db):
    k = kernel_for(db)
    r = k.seed_report
    d = k.store.create_draft(r["profile_id"], actor="admin")
    k.store.update_draft(d["id"], {"caps": {"max_tools": 12}}, actor="admin")
    # Today's env enables bash on a money-handling profile, so gate 2 asks for a reason
    # (design §9). Models are unchanged, so gate 3 needs no fresh probe.
    with pytest.raises(GateError) as ei:
        k.store.publish(d["id"], actor="admin", gates=k.gates)
    assert [g for g, _ in ei.value.failures] == ["money"]
    out = k.store.publish(d["id"], actor="admin", gates=k.gates, override_reason="benchmarked with bash on")
    assert out["status"] == "published" and k.resolve("1").caps.max_tools == 12
    assert isinstance(kernel_for(db), Kernel) and kernel_for(db) is k
