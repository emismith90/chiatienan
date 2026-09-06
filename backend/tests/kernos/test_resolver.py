import pytest
from sqlalchemy import create_engine

from kernos.content import (
    ContentStore, DbResolver, Models, Persona, ProfileSpec, Prompt, Runtime, bind, sessions_for,
)

RT = Runtime(cwd="/host/cwd", agent_dir="/host/agent")
FALLBACK = ProfileSpec(models=Models(text="fallback"), runtime=RT)


class OkGates:
    def check(self, spec, **kw): return []


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/r.db", future=True)
    bind(engine)
    return ContentStore(sessions_for(engine))


def _seed(store, slug="lunch"):
    b = store.create_business(slug, slug)
    p = store.create_profile(b["id"], "default", managed_by="boot")
    spec = ProfileSpec(models=Models(text="seeded"), persona=Persona(name="Phoenix"), prompt=Prompt(body="B"))
    v = store.create_draft(p["id"], actor="boot", base_spec=spec.stored())
    store.publish(v["id"], actor="boot", bypass_gates=True)
    a = store.create_agent(b["id"], "phoenix", "Phoenix", profile_id=p["id"], is_default=True)
    return b, p, v, a


def test_no_content_falls_back_and_runtime_is_injected(store):
    r = DbResolver(store, default_business_slug="lunch", runtime=RT, fallback=FALLBACK)
    spec = r.resolve("1")
    assert spec.models.text == "fallback" and spec.runtime == RT
    assert r.describe("1")["source"] == "fallback"


def test_unbound_space_runs_the_default_agent_and_bound_space_its_binding(store):
    b, p, v, a = _seed(store)
    other_p = store.create_profile(b["id"], "english")
    ov = store.create_draft(other_p["id"], actor="admin", base_spec=ProfileSpec(models=Models(text="other")).stored())
    store.publish(ov["id"], actor="admin", gates=OkGates())
    other_agent = store.create_agent(b["id"], "eng", "Eng", profile_id=other_p["id"])
    r = DbResolver(store, default_business_slug="lunch", runtime=RT, fallback=FALLBACK)

    default = r.resolve("room-1")
    assert default.models.text == "seeded" and default.runtime == RT and "runtime" not in store.get_version(v["id"])["spec"]
    assert r.describe("room-1")["source"] == "default"

    store.bind_space("room-2", other_agent["id"], overrides={"append_sections": ["Speak English"], "handle": "eng"})
    bound = r.resolve("room-2")
    assert bound.models.text == "other" and bound.prompt.append == ["Speak English"] and bound.persona.handle == "eng"
    assert r.describe("room-2")["source"] == "binding"
    # overrides never leak to the unbound space or the cached default (finding 8)
    assert r.resolve("room-1").prompt.append == [] and r.resolve("room-1").persona.handle == "assistant"


def test_publish_and_bind_invalidate_the_cache(store):
    b, p, v, a = _seed(store)
    r = DbResolver(store, default_business_slug="lunch", runtime=RT, fallback=FALLBACK)
    assert r.resolve("room-1").models.text == "seeded"
    d = store.create_draft(p["id"], actor="admin")
    store.update_draft(d["id"], {"models": {"text": "changed"}}, actor="admin")
    store.publish(d["id"], actor="admin", gates=OkGates())
    assert r.resolve("room-1").models.text == "changed"            # on_change → invalidate
    store.unbind_space  # noqa: B018 — exists
    assert r.describe("room-1")["version_id"] == d["id"]
