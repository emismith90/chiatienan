"""The mountable admin API of the content plane (design §5.2, plan Task 2.5).

``admin_router(get_kernel)`` returns a FastAPI ``APIRouter``; a host mounts it
under whatever prefix and guard it likes. ``get_kernel()`` must return an object
with ``store`` (ContentStore), ``registry`` (Registry), ``gates`` (PublishGates),
``resolver`` (with ``describe``/``resolve``), ``pipeline_for(spec)``, and optionally
``probe`` (a ``ModelProbe``). Everything else is data in, data out.

Statuses: 404 unknown, 409 state conflict, 412 ``If-Match`` mismatch, 422
validation or gate failures (with the failure list), 501 no probe configured.
Actors default to ``admin``; ``boot*`` is refused (review finding 4).
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from fastapi import APIRouter, Body, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from kernos.content.errors import ContentError, GateError
from kernos.registry.registry import ConfigError, RegistryError
from kernos.content.spec import BindingOverrides, ProfileSpec



class EvalCaseIn(BaseModel):
    case: dict
    tags: list[str] = []
    source: str = "manual"
    review: bool = False


class EvalSuiteIn(BaseModel):
    case_slugs: list[str] = []
    graders: list[dict] = []
    judge: dict = {}
    repeat: int = 1


class RubricIn(BaseModel):
    body: str

class BusinessIn(BaseModel):
    slug: str
    name: str
    description: str = ""
    tool_packs: list = Field(default_factory=list)
    plugins_allowed: list = Field(default_factory=list)
    seed: dict = Field(default_factory=dict)


class SourceIn(BaseModel):
    title: str = ""
    body: str
    frontmatter: dict = Field(default_factory=dict)


class ProfileIn(BaseModel):
    business_id: int
    name: str


class DraftIn(BaseModel):
    from_version: int | None = None
    note: str | None = None
    base_spec: dict | None = None


class PublishIn(BaseModel):
    actor: str = "admin"
    override_reason: str | None = None
    note: str | None = None


class RollbackIn(BaseModel):
    version: int
    actor: str = "admin"
    override_reason: str | None = None


class AgentIn(BaseModel):
    business_id: int
    slug: str
    name: str
    profile_id: int
    role: str = "manager"
    is_default: bool = False
    delegates_to: list = Field(default_factory=list)
    capabilities: dict = Field(default_factory=dict)
    max_depth: int = 2


class BindingIn(BaseModel):
    agent_id: int
    overrides: BindingOverrides = Field(default_factory=BindingOverrides)


def _actor(name: str | None) -> str:
    actor = (name or "admin").strip() or "admin"
    if actor.lower().startswith("boot"):
        raise HTTPException(422, "actor 'boot' is reserved for seeding")
    return actor


def _wrap(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except GateError as exc:
        raise HTTPException(422, {"gates": [{"gate": g, "message": m} for g, m in exc.failures]}) from exc
    except ContentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except ConfigError as exc:
        raise HTTPException(422, {"config": exc.problems}) from exc
    except RegistryError as exc:
        raise HTTPException(404, str(exc)) from exc


def admin_router(get_kernel: Callable[[], Any], *, dependencies=()) -> APIRouter:
    r = APIRouter(dependencies=list(dependencies))

    # ------------------------------------------------------------- registry
    @r.get("/registry")
    def registry():
        return get_kernel().registry.describe()

    @r.get("/plugins/{plugin_id}/{version}/schema")
    def plugin_schema(plugin_id: str, version: str):
        return _wrap(lambda: get_kernel().registry.get(plugin_id, version).config_schema)

    # ----------------------------------------------------------- businesses
    @r.get("/businesses")
    def businesses():
        return get_kernel().store.list_businesses()

    @r.post("/businesses", status_code=201)
    def create_business(body: BusinessIn, x_actor: str | None = Header(default=None)):
        return _wrap(lambda: get_kernel().store.create_business(
            body.slug, body.name, actor=_actor(x_actor), description=body.description,
            tool_packs=body.tool_packs, plugins_allowed=body.plugins_allowed, seed=body.seed))

    @r.get("/businesses/{business_id}")
    def business(business_id: int):
        return _wrap(lambda: get_kernel().store.get_business(business_id))

    @r.patch("/businesses/{business_id}")
    def patch_business(business_id: int, patch: dict = Body(...), x_actor: str | None = Header(default=None)):
        return _wrap(lambda: get_kernel().store.update_business(business_id, patch, actor=_actor(x_actor)))

    # -------------------------------------------------------------- sources
    @r.get("/businesses/{business_id}/sources")
    def sources(business_id: int, kind: str | None = Query(default=None)):
        return _wrap(lambda: get_kernel().store.list_sources(business_id, kind))

    @r.get("/businesses/{business_id}/sources/{kind}/{slug}")
    def source(business_id: int, kind: str, slug: str, response: Response):
        row = _wrap(lambda: get_kernel().store.get_source(business_id, kind, slug))
        response.headers["ETag"] = row["etag"]
        return row

    @r.put("/businesses/{business_id}/sources/{kind}/{slug}")
    def put_source(business_id: int, kind: str, slug: str, body: SourceIn, response: Response,
                   if_match: str | None = Header(default=None), x_actor: str | None = Header(default=None)):
        row = _wrap(lambda: get_kernel().store.put_source(
            business_id, kind, slug, body=body.body, title=body.title, frontmatter=body.frontmatter,
            actor=_actor(x_actor), if_match=if_match))
        response.headers["ETag"] = row["etag"]
        return row

    @r.delete("/businesses/{business_id}/sources/{kind}/{slug}", status_code=204)
    def delete_source(business_id: int, kind: str, slug: str, if_match: str | None = Header(default=None),
                      x_actor: str | None = Header(default=None)):
        _wrap(lambda: get_kernel().store.delete_source(business_id, kind, slug, actor=_actor(x_actor), if_match=if_match))
        return Response(status_code=204)

    # ------------------------------------------------------------- profiles
    @r.get("/profiles")
    def profiles(business_id: int | None = Query(default=None)):
        return get_kernel().store.list_profiles(business_id)

    @r.post("/profiles", status_code=201)
    def create_profile(body: ProfileIn, x_actor: str | None = Header(default=None)):
        return _wrap(lambda: get_kernel().store.create_profile(body.business_id, body.name, actor=_actor(x_actor)))

    @r.get("/profiles/{profile_id}")
    def profile(profile_id: int):
        return _wrap(lambda: get_kernel().store.get_profile(profile_id))

    @r.get("/profiles/{profile_id}/versions")
    def versions(profile_id: int):
        return _wrap(lambda: get_kernel().store.list_versions(profile_id))

    @r.post("/profiles/{profile_id}/versions", status_code=201)
    def create_draft(profile_id: int, body: DraftIn | None = None, x_actor: str | None = Header(default=None)):
        body = body or DraftIn()
        return _wrap(lambda: get_kernel().store.create_draft(
            profile_id, actor=_actor(x_actor), from_version=body.from_version, note=body.note, base_spec=body.base_spec))

    @r.get("/profiles/{profile_id}/versions/{version}")
    def version(profile_id: int, version: int):
        return _wrap(lambda: get_kernel().store.find_version(profile_id, version))

    @r.patch("/profiles/{profile_id}/versions/{version}")
    def patch_version(profile_id: int, version: int, patch: dict = Body(...), x_actor: str | None = Header(default=None)):
        def go():
            store = get_kernel().store
            v = store.find_version(profile_id, version)
            return store.update_draft(v["id"], patch, actor=_actor(x_actor))
        return _wrap(go)

    @r.post("/profiles/{profile_id}/versions/{version}/publish")
    def publish(profile_id: int, version: int, body: PublishIn | None = None):
        body = body or PublishIn()
        actor = _actor(body.actor)

        def go():
            k = get_kernel()
            v = k.store.find_version(profile_id, version)
            return k.store.publish(v["id"], actor=actor, gates=k.gates, override_reason=body.override_reason, note=body.note)
        return _wrap(go)

    @r.post("/profiles/{profile_id}/versions/{version}/retire")
    def retire(profile_id: int, version: int, x_actor: str | None = Header(default=None)):
        def go():
            store = get_kernel().store
            return store.retire(store.find_version(profile_id, version)["id"], actor=_actor(x_actor))
        return _wrap(go)

    @r.post("/profiles/{profile_id}/rollback")
    def rollback(profile_id: int, body: RollbackIn):
        actor = _actor(body.actor)
        return _wrap(lambda: get_kernel().store.rollback(
            profile_id, body.version, actor=actor, gates=get_kernel().gates, override_reason=body.override_reason))

    # --------------------------------------------------------------- agents
    @r.get("/agents")
    def agents(business_id: int | None = Query(default=None)):
        return get_kernel().store.list_agents(business_id)

    @r.post("/agents", status_code=201)
    def create_agent(body: AgentIn, x_actor: str | None = Header(default=None)):
        return _wrap(lambda: get_kernel().store.create_agent(
            body.business_id, body.slug, body.name, profile_id=body.profile_id, actor=_actor(x_actor), role=body.role,
            is_default=body.is_default, delegates_to=body.delegates_to, capabilities=body.capabilities,
            max_depth=body.max_depth))

    @r.get("/agents/{agent_id}")
    def agent(agent_id: int):
        return _wrap(lambda: get_kernel().store.get_agent(agent_id))

    @r.patch("/agents/{agent_id}")
    def patch_agent(agent_id: int, patch: dict = Body(...), x_actor: str | None = Header(default=None)):
        return _wrap(lambda: get_kernel().store.update_agent(agent_id, patch, actor=_actor(x_actor)))

    # --------------------------------------------------------------- spaces
    @r.get("/spaces/{space_id}/binding")
    def binding(space_id: str):
        row = get_kernel().store.get_binding(space_id)
        if row is None:
            raise HTTPException(404, f"space {space_id} is not bound")
        return row

    @r.put("/spaces/{space_id}/binding")
    def bind(space_id: str, body: BindingIn, x_actor: str | None = Header(default=None)):
        return _wrap(lambda: get_kernel().store.bind_space(
            space_id, body.agent_id, actor=_actor(x_actor), overrides=body.overrides.model_dump()))

    @r.delete("/spaces/{space_id}/binding", status_code=204)
    def unbind(space_id: str, x_actor: str | None = Header(default=None)):
        _wrap(lambda: get_kernel().store.unbind_space(space_id, actor=_actor(x_actor)))
        return Response(status_code=204)

    @r.get("/spaces/{space_id}/resolved")
    def resolved(space_id: str):
        k = get_kernel()
        spec: ProfileSpec = k.resolve(space_id)
        return {
            "space_id": space_id,
            "resolution": k.resolver.describe(space_id) if hasattr(k.resolver, "describe") else None,
            "spec": spec.model_dump(),
            "engine_spec": asdict(spec.to_engine_spec()),
            "pipeline": k.pipeline_for(spec).describe(),
        }

    # ----------------------------------------------------------------- eval
    @r.get("/businesses/{business_id}/eval/cases")
    def eval_cases(business_id: int, review: bool | None = None, source: str | None = None):
        return _wrap(lambda: get_kernel().store.list_cases(business_id, review=review, source=source))

    @r.get("/businesses/{business_id}/eval/cases/{slug}")
    def eval_case(business_id: int, slug: str):
        return _wrap(lambda: get_kernel().store.get_case(business_id, slug))

    @r.put("/businesses/{business_id}/eval/cases/{slug}")
    def put_eval_case(business_id: int, slug: str, body: EvalCaseIn, x_actor: str | None = Header(default=None)):
        return _wrap(lambda: get_kernel().store.put_case(
            business_id, slug, body.case, actor=_actor(x_actor), tags=body.tags, source=body.source, review=body.review))

    @r.delete("/businesses/{business_id}/eval/cases/{slug}", status_code=204)
    def delete_eval_case(business_id: int, slug: str, x_actor: str | None = Header(default=None)):
        _wrap(lambda: get_kernel().store.delete_case(business_id, slug, actor=_actor(x_actor)))

    @r.get("/businesses/{business_id}/eval/suites")
    def eval_suites(business_id: int):
        return _wrap(lambda: get_kernel().store.list_suites(business_id))

    @r.get("/businesses/{business_id}/eval/suites/{slug}")
    def eval_suite(business_id: int, slug: str):
        return _wrap(lambda: get_kernel().store.get_suite(business_id, slug))

    @r.put("/businesses/{business_id}/eval/suites/{slug}")
    def put_eval_suite(business_id: int, slug: str, body: EvalSuiteIn, x_actor: str | None = Header(default=None)):
        return _wrap(lambda: get_kernel().store.put_suite(
            business_id, slug, actor=_actor(x_actor), case_slugs=body.case_slugs, graders=body.graders,
            judge=body.judge, repeat=body.repeat))

    @r.delete("/businesses/{business_id}/eval/suites/{slug}", status_code=204)
    def delete_eval_suite(business_id: int, slug: str, x_actor: str | None = Header(default=None)):
        _wrap(lambda: get_kernel().store.delete_suite(business_id, slug, actor=_actor(x_actor)))

    @r.get("/businesses/{business_id}/eval/rubrics")
    def rubrics(business_id: int):
        return _wrap(lambda: get_kernel().store.list_rubrics(business_id))

    @r.put("/businesses/{business_id}/eval/rubrics/{slug}")
    def put_rubric(business_id: int, slug: str, body: RubricIn, x_actor: str | None = Header(default=None)):
        return _wrap(lambda: get_kernel().store.put_rubric(business_id, slug, body.body, actor=_actor(x_actor)))

    @r.post("/profiles/{profile_id}/versions/{version}/eval", status_code=202)
    def start_eval(profile_id: int, version: int, suite: str = Query(...), x_actor: str | None = Header(default=None)):
        """Create the run and spawn the job; poll `GET /eval/runs/{id}`."""
        k = get_kernel()
        start = getattr(k, "start_eval_run", None)
        if start is None:
            raise HTTPException(501, "this host cannot run evals")

        def go():
            v = k.store.find_version(profile_id, version)
            return start(suite, v["id"], actor=_actor(x_actor))
        return _wrap(go)

    @r.post("/businesses/{business_id}/eval/import")
    def import_suite(business_id: int, x_actor: str | None = Header(default=None)):
        k = get_kernel()
        importer = getattr(k, "import_eval_suite", None)
        if importer is None:
            raise HTTPException(501, "this host has no eval suite to import")
        return _wrap(lambda: importer(business_id, actor=_actor(x_actor)))

    @r.get("/eval/graders")
    def eval_graders():
        k = get_kernel()
        registry = getattr(k, "graders", None)
        return registry.ids() if registry is not None else []

    @r.get("/eval/runs")
    def eval_runs(suite_id: int | None = None, profile_version_id: int | None = None,
                  limit: int = Query(default=50, le=500)):
        return get_kernel().store.list_runs(suite_id=suite_id, profile_version_id=profile_version_id, limit=limit)

    @r.get("/eval/runs/{run_id}")
    def eval_run(run_id: int):
        return _wrap(lambda: get_kernel().store.get_run(run_id))

    # ---------------------------------------------------------------- turns
    @r.get("/spaces/{space_id}/turns")
    def turns(space_id: str, limit: int = Query(default=50, le=500)):
        return get_kernel().store.list_traces(space_id, limit=limit)

    @r.get("/spaces/{space_id}/turns/{ref}")
    def turn(space_id: str, ref: str):
        row = get_kernel().store.get_trace(space_id, ref)
        if row is None:
            raise HTTPException(404, f"no trace {ref!r} in space {space_id!r}")
        return row

    # ------------------------------------------------------------ catalogue
    @r.get("/catalogue/models")
    def models():
        return get_kernel().store.list_models()

    @r.post("/catalogue/models/{model_id:path}/probe")
    async def probe(model_id: str, x_actor: str | None = Header(default=None)):
        k = get_kernel()
        probe = getattr(k, "probe", None)
        if probe is None:
            raise HTTPException(501, "no model probe configured on this host")
        try:
            result = await probe.probe(model_id)
        except NotImplementedError as exc:
            raise HTTPException(501, str(exc)) from exc
        return _wrap(lambda: k.store.set_probe(model_id, result, actor=_actor(x_actor)))

    # ---------------------------------------------------------------- audit
    @r.get("/audit")
    def audit(limit: int = Query(default=100, le=1000)):
        return get_kernel().store.audit(limit=limit)

    return r
