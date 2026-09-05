"""``ContentStore``: sources, drafts, publish, rollback, bindings, catalogue, audit
(design §5; review findings 1, 2, 4, 7, 10, 11).

Every method returns plain dicts, never ORM rows, so the API layer and the
resolver need no session of their own and nothing detached leaks out. Writes are
audited. ``on_change`` callbacks fire after anything that changes what a space
runs (publish, rollback, bind, unbind) so a resolver can drop its cache.
"""
from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from kernos.content import models as m
from kernos.content.errors import Conflict, GateError, Invalid, NotFound, PreconditionFailed
from kernos.content.spec import BindingOverrides, ProfileSpec

SOURCE_KINDS = ("prompt", "rule", "skill", "template")
SYSTEM_PROMPT_SLUG = "system"


def sessions_for(engine: Engine) -> Callable[[], Any]:
    """A session factory over an engine: commit on success, rollback on error."""
    maker = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def factory() -> Iterator[Session]:
        s = maker()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    return factory


def source_etag(kind: str, slug: str, title: str, body: str, frontmatter: dict) -> str:
    raw = json.dumps([kind, slug, title, body, frontmatter], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def deep_merge(base: dict, patch: dict) -> dict:
    """Dicts merge recursively; lists and scalars replace."""
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _row(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _validate(spec: dict) -> ProfileSpec:
    try:
        return ProfileSpec.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise Invalid(f"spec does not validate: {first['msg']} at /{'/'.join(str(p) for p in first['loc'])}") from exc


class ContentStore:
    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._session = session_factory
        self.on_change: list[Callable[[], None]] = []

    def _changed(self) -> None:
        for cb in self.on_change:
            cb()

    # --------------------------------------------------------------------- audit

    def log(self, s: Session, actor: str, action: str, entity: str, entity_id: Any,
            before: Any = None, after: Any = None) -> None:
        s.add(m.AuditLog(actor=actor, action=action, entity=entity, entity_id=str(entity_id),
                         before=before, after=after))

    def audit(self, *, limit: int = 100) -> list[dict]:
        with self._session() as s:
            rows = s.scalars(select(m.AuditLog).order_by(m.AuditLog.id.desc()).limit(limit)).all()
            return [_row(r) for r in rows]

    # ---------------------------------------------------------------- businesses

    def create_business(self, slug: str, name: str, *, actor: str = "admin", description: str = "",
                        tool_packs: list | None = None, plugins_allowed: list | None = None,
                        seed: dict | None = None) -> dict:
        with self._session() as s:
            if s.scalar(select(m.Business).where(m.Business.slug == slug)):
                raise Conflict(f"business {slug!r} exists")
            b = m.Business(slug=slug, name=name, description=description, tool_packs=tool_packs or [],
                           plugins_allowed=plugins_allowed or [], seed=seed or {})
            s.add(b); s.flush()
            self.log(s, actor, "create", "business", b.id, after=_row(b))
            return _row(b)

    def get_business(self, ref: int | str) -> dict:
        with self._session() as s:
            return _row(self._business(s, ref))

    def _business(self, s: Session, ref: int | str) -> m.Business:
        b = s.get(m.Business, ref) if isinstance(ref, int) else s.scalar(
            select(m.Business).where(m.Business.slug == ref))
        if b is None:
            raise NotFound(f"no business {ref!r}")
        return b

    def list_businesses(self) -> list[dict]:
        with self._session() as s:
            return [_row(b) for b in s.scalars(select(m.Business).order_by(m.Business.id)).all()]

    def update_business(self, ref: int | str, patch: dict, *, actor: str = "admin") -> dict:
        allowed = {"name", "description", "tool_packs", "plugins_allowed", "seed"}
        bad = set(patch) - allowed
        if bad:
            raise Invalid(f"business fields not editable: {sorted(bad)}")
        with self._session() as s:
            b = self._business(s, ref)
            before = _row(b)
            for k, v in patch.items():
                setattr(b, k, v)
            s.flush()
            self.log(s, actor, "update", "business", b.id, before=before, after=_row(b))
            return _row(b)

    # ------------------------------------------------------------------- sources

    def put_source(self, business_id: int, kind: str, slug: str, *, body: str, actor: str,
                   title: str = "", frontmatter: dict | None = None, if_match: str | None = None) -> dict:
        if kind not in SOURCE_KINDS:
            raise Invalid(f"unknown source kind {kind!r}; one of {SOURCE_KINDS}")
        frontmatter = frontmatter or {}
        etag = source_etag(kind, slug, title, body, frontmatter)
        with self._session() as s:
            self._business(s, business_id)
            row = s.scalar(select(m.Source).where(m.Source.business_id == business_id,
                                                  m.Source.kind == kind, m.Source.slug == slug))
            if row is not None and if_match is not None and if_match != row.etag:
                raise PreconditionFailed(f"etag mismatch for {kind}/{slug}: have {row.etag}")
            before = _row(row) if row is not None else None
            if row is None:
                row = m.Source(business_id=business_id, kind=kind, slug=slug)
                s.add(row)
            row.title, row.body, row.frontmatter, row.etag = title, body, frontmatter, etag
            row.updated_by, row.updated_at = actor, m.utcnow()
            s.flush()
            self.log(s, actor, "put", f"source:{kind}", f"{business_id}/{slug}", before=before, after=_row(row))
            return _row(row)

    def get_source(self, business_id: int, kind: str, slug: str) -> dict:
        with self._session() as s:
            row = s.scalar(select(m.Source).where(m.Source.business_id == business_id,
                                                  m.Source.kind == kind, m.Source.slug == slug))
            if row is None:
                raise NotFound(f"no source {kind}/{slug}")
            return _row(row)

    def list_sources(self, business_id: int, kind: str | None = None) -> list[dict]:
        with self._session() as s:
            q = select(m.Source).where(m.Source.business_id == business_id)
            if kind:
                q = q.where(m.Source.kind == kind)
            return [_row(r) for r in s.scalars(q.order_by(m.Source.kind, m.Source.slug)).all()]

    def delete_source(self, business_id: int, kind: str, slug: str, *, actor: str,
                      if_match: str | None = None) -> None:
        with self._session() as s:
            row = s.scalar(select(m.Source).where(m.Source.business_id == business_id,
                                                  m.Source.kind == kind, m.Source.slug == slug))
            if row is None:
                raise NotFound(f"no source {kind}/{slug}")
            if if_match is not None and if_match != row.etag:
                raise PreconditionFailed(f"etag mismatch for {kind}/{slug}: have {row.etag}")
            self.log(s, actor, "delete", f"source:{kind}", f"{business_id}/{slug}", before=_row(row))
            s.delete(row)

    # ------------------------------------------------------------------ profiles

    def create_profile(self, business_id: int, name: str, *, actor: str = "admin",
                       managed_by: str = "human") -> dict:
        with self._session() as s:
            self._business(s, business_id)
            p = m.Profile(business_id=business_id, name=name, managed_by=managed_by)
            s.add(p); s.flush()
            self.log(s, actor, "create", "profile", p.id, after=_row(p))
            return _row(p)

    def get_profile(self, profile_id: int) -> dict:
        with self._session() as s:
            return _row(self._profile(s, profile_id))

    def _profile(self, s: Session, profile_id: int) -> m.Profile:
        p = s.get(m.Profile, profile_id)
        if p is None:
            raise NotFound(f"no profile {profile_id}")
        return p

    def list_profiles(self, business_id: int | None = None) -> list[dict]:
        with self._session() as s:
            q = select(m.Profile)
            if business_id is not None:
                q = q.where(m.Profile.business_id == business_id)
            return [_row(p) for p in s.scalars(q.order_by(m.Profile.id)).all()]

    def published_spec(self, profile_id: int) -> dict | None:
        with self._session() as s:
            p = self._profile(s, profile_id)
            if p.published_version_id is None:
                return None
            return s.get(m.ProfileVersion, p.published_version_id).spec

    # ------------------------------------------------------------------ versions

    def _snapshot_sources(self, s: Session, business_id: int, base: dict) -> dict:
        """Replace, per kind, the source-backed parts of ``base`` with the business's
        current sources (finding 1: replace-per-kind is the documented semantics, and
        boot seeds sources so an untouched seeded profile snapshots to itself)."""
        rows = s.scalars(select(m.Source).where(m.Source.business_id == business_id)
                         .order_by(m.Source.slug)).all()
        by_kind: dict[str, list[m.Source]] = {k: [] for k in SOURCE_KINDS}
        for r in rows:
            by_kind[r.kind].append(r)
        spec = copy.deepcopy(base)
        spec["rules"] = [{"slug": r.slug, "content": r.body, "tags": list(r.frontmatter.get("tags", []))}
                         for r in by_kind["rule"]]
        spec["skills"] = [{"name": r.slug, "description": r.frontmatter.get("description", ""),
                           "body": r.body, "delivery": r.frontmatter.get("delivery", "inline")}
                          for r in by_kind["skill"]]
        spec["templates"] = [{"name": r.slug, "kind": r.frontmatter.get("kind", "template"),
                              "content": r.body, "description": r.frontmatter.get("description", "")}
                             for r in by_kind["template"]]
        system = next((r for r in by_kind["prompt"] if r.slug == SYSTEM_PROMPT_SLUG), None)
        if system is not None:
            spec.setdefault("prompt", {})
            spec["prompt"] = {**spec["prompt"], "body": system.body}
        return spec

    def create_draft(self, profile_id: int, *, actor: str, from_version: int | None = None,
                     note: str | None = None, base_spec: dict | None = None) -> dict:
        with self._session() as s:
            p = self._profile(s, profile_id)
            if from_version is not None:
                v = s.scalar(select(m.ProfileVersion).where(m.ProfileVersion.profile_id == profile_id,
                                                            m.ProfileVersion.version == from_version))
                if v is None:
                    raise NotFound(f"profile {profile_id} has no version {from_version}")
                base = v.spec
            elif p.published_version_id is not None:
                base = s.get(m.ProfileVersion, p.published_version_id).spec
            elif base_spec is not None:
                base = base_spec
            else:
                business = s.get(m.Business, p.business_id)
                base = (business.seed or {}).get("spec")
                if not base:
                    raise Invalid("no base spec: publish a version, pass base_spec, or set business.seed.spec")
            spec = _validate(self._snapshot_sources(s, p.business_id, base)).stored()
            return _row(self._add_version(s, p, spec, actor=actor, note=note))

    def _add_version(self, s: Session, p: m.Profile, spec: dict, *, actor: str,
                     note: str | None, status: str = "draft") -> m.ProfileVersion:
        last = s.scalar(select(m.ProfileVersion.version).where(m.ProfileVersion.profile_id == p.id)
                        .order_by(m.ProfileVersion.version.desc()).limit(1)) or 0
        v = m.ProfileVersion(profile_id=p.id, version=last + 1, status=status, spec=spec,
                             actor=actor, note=note)
        s.add(v); s.flush()
        self.log(s, actor, "create_version", "version", v.id, after={"profile_id": p.id, "version": v.version})
        return v

    def get_version(self, version_id: int) -> dict:
        with self._session() as s:
            return _row(self._version(s, version_id))

    def _version(self, s: Session, version_id: int) -> m.ProfileVersion:
        v = s.get(m.ProfileVersion, version_id)
        if v is None:
            raise NotFound(f"no version {version_id}")
        return v

    def find_version(self, profile_id: int, version: int) -> dict:
        with self._session() as s:
            v = s.scalar(select(m.ProfileVersion).where(m.ProfileVersion.profile_id == profile_id,
                                                        m.ProfileVersion.version == version))
            if v is None:
                raise NotFound(f"profile {profile_id} has no version {version}")
            return _row(v)

    def list_versions(self, profile_id: int) -> list[dict]:
        with self._session() as s:
            rows = s.scalars(select(m.ProfileVersion).where(m.ProfileVersion.profile_id == profile_id)
                             .order_by(m.ProfileVersion.version)).all()
            return [{**_row(v), "spec": None} for v in rows]     # list is light; get_version has the spec

    def update_draft(self, version_id: int, patch: dict, *, actor: str) -> dict:
        if "runtime" in patch:
            raise Invalid("runtime is boot-layer configuration and cannot be stored in a profile")
        with self._session() as s:
            v = self._version(s, version_id)
            if v.status != "draft":
                raise Conflict(f"version {v.version} is {v.status}; only drafts are editable")
            before = v.spec
            v.spec = _validate(deep_merge(v.spec, patch)).stored()
            s.flush()
            self.log(s, actor, "update_draft", "version", v.id, before=before, after=v.spec)
            return _row(v)

    def publish(self, version_id: int, *, actor: str, gates=None, override_reason: str | None = None,
                bypass_gates: bool = False, skip_probe: bool = False, note: str | None = None) -> dict:
        """Make ``version_id`` what its profile runs.

        ``bypass_gates`` exists for boot seeding only — the seeded profile *is* today's
        behaviour — and it is the one path that keeps ``managed_by = "boot"``. Any
        other publish makes the profile human-managed (finding 4).
        """
        with self._session() as s:
            v = self._version(s, version_id)
            if v.status not in ("draft", "superseded"):
                raise Conflict(f"version {v.version} is {v.status}; only a draft or superseded version can be published")
            p = self._profile(s, v.profile_id)
            previous = s.get(m.ProfileVersion, p.published_version_id) if p.published_version_id else None
            if not bypass_gates:
                if gates is None:
                    raise Invalid("publish needs gates (or bypass_gates for boot seeding)")
                failures = gates.check(v.spec, previous=previous.spec if previous else None, actor=actor,
                                       override_reason=override_reason, skip_probe=skip_probe)
                if failures:
                    raise GateError(f.as_tuple() for f in failures)
            if previous is not None and previous.id != v.id:
                previous.status = "superseded"
            v.status, v.published_at = "published", m.utcnow()
            if note:
                v.note = note
            p.published_version_id = v.id
            if not bypass_gates:
                p.managed_by = "human"
            s.flush()
            self.log(s, actor, "publish", "profile", p.id,
                     before={"published_version_id": previous.id if previous else None},
                     after={"published_version_id": v.id, "version": v.version,
                            "override_reason": override_reason, "bypass_gates": bypass_gates})
            out = _row(v)
        self._changed()
        return out

    def rollback(self, profile_id: int, version: int, *, actor: str, gates,
                 override_reason: str | None = None) -> dict:
        """Republish a superseded version. Gate 3 is skipped: the version passed its
        probe when it was published, and rollback is the incident path (finding 7)."""
        target = self.find_version(profile_id, version)
        if target["status"] != "superseded":
            raise Conflict(f"version {version} is {target['status']}; only a superseded version can be rolled back to")
        return self.publish(target["id"], actor=actor, gates=gates, override_reason=override_reason,
                            skip_probe=True, note=f"rollback by {actor}")

    def retire(self, version_id: int, *, actor: str) -> dict:
        with self._session() as s:
            v = self._version(s, version_id)
            if v.status == "published":
                raise Conflict("the published version cannot be retired; publish another first")
            v.status = "retired"
            s.flush()
            self.log(s, actor, "retire", "version", v.id)
            return _row(v)

    # -------------------------------------------------------------------- agents

    def create_agent(self, business_id: int, slug: str, name: str, *, profile_id: int, actor: str = "admin",
                     role: str = "manager", is_default: bool = False, delegates_to: list | None = None,
                     capabilities: dict | None = None, max_depth: int = 2) -> dict:
        if role not in ("manager", "sub"):
            raise Invalid("role must be manager or sub")
        with self._session() as s:
            self._business(s, business_id)
            self._profile(s, profile_id)
            if s.scalar(select(m.Agent).where(m.Agent.business_id == business_id, m.Agent.slug == slug)):
                raise Conflict(f"agent {slug!r} exists in business {business_id}")
            if is_default:
                if role != "manager":
                    raise Invalid("only a manager can be the default agent")
                for other in s.scalars(select(m.Agent).where(m.Agent.business_id == business_id,
                                                             m.Agent.is_default.is_(True))).all():
                    other.is_default = False
            a = m.Agent(business_id=business_id, slug=slug, name=name, role=role, is_default=is_default,
                        profile_id=profile_id, delegates_to=delegates_to or [], capabilities=capabilities or {},
                        max_depth=max_depth)
            s.add(a); s.flush()
            self.log(s, actor, "create", "agent", a.id, after=_row(a))
            return _row(a)

    def get_agent(self, agent_id: int) -> dict:
        with self._session() as s:
            a = s.get(m.Agent, agent_id)
            if a is None:
                raise NotFound(f"no agent {agent_id}")
            return _row(a)

    def list_agents(self, business_id: int | None = None) -> list[dict]:
        with self._session() as s:
            q = select(m.Agent)
            if business_id is not None:
                q = q.where(m.Agent.business_id == business_id)
            return [_row(a) for a in s.scalars(q.order_by(m.Agent.id)).all()]

    def default_agent(self, business_ref: int | str) -> dict | None:
        with self._session() as s:
            b = self._business(s, business_ref)
            a = s.scalar(select(m.Agent).where(m.Agent.business_id == b.id, m.Agent.is_default.is_(True)))
            return _row(a) if a else None

    def update_agent(self, agent_id: int, patch: dict, *, actor: str = "admin") -> dict:
        allowed = {"name", "role", "profile_id", "delegates_to", "capabilities", "max_depth", "is_default"}
        bad = set(patch) - allowed
        if bad:
            raise Invalid(f"agent fields not editable: {sorted(bad)}")
        with self._session() as s:
            a = s.get(m.Agent, agent_id)
            if a is None:
                raise NotFound(f"no agent {agent_id}")
            before = _row(a)
            if patch.get("is_default"):
                for other in s.scalars(select(m.Agent).where(m.Agent.business_id == a.business_id,
                                                             m.Agent.is_default.is_(True))).all():
                    other.is_default = False
            if "profile_id" in patch:
                self._profile(s, patch["profile_id"])
            for k, v in patch.items():
                setattr(a, k, v)
            s.flush()
            self.log(s, actor, "update", "agent", a.id, before=before, after=_row(a))
            out = _row(a)
        self._changed()
        return out

    # ------------------------------------------------------------------ bindings

    def bind_space(self, space_id: str, agent_id: int, *, actor: str = "admin",
                   overrides: dict | None = None) -> dict:
        try:
            ov = BindingOverrides.model_validate(overrides or {})
        except ValidationError as exc:
            raise Invalid(f"invalid overrides: {exc.errors()[0]['msg']}") from exc
        with self._session() as s:
            a = s.get(m.Agent, agent_id)
            if a is None:
                raise NotFound(f"no agent {agent_id}")
            if a.role != "manager":
                raise Invalid(f"agent {a.slug!r} is a {a.role}; a space binds to a manager")
            row = s.get(m.SpaceBinding, space_id)
            before = _row(row) if row else None
            if row is None:
                row = m.SpaceBinding(space_id=space_id, agent_id=agent_id)
                s.add(row)
            row.agent_id, row.overrides, row.updated_at = agent_id, ov.model_dump(), m.utcnow()
            s.flush()
            self.log(s, actor, "bind", "space", space_id, before=before, after=_row(row))
            out = _row(row)
        self._changed()
        return out

    def get_binding(self, space_id: str) -> dict | None:
        with self._session() as s:
            row = s.get(m.SpaceBinding, space_id)
            return _row(row) if row else None

    def unbind_space(self, space_id: str, *, actor: str = "admin") -> None:
        with self._session() as s:
            row = s.get(m.SpaceBinding, space_id)
            if row is None:
                raise NotFound(f"space {space_id} is not bound")
            self.log(s, actor, "unbind", "space", space_id, before=_row(row))
            s.delete(row)
        self._changed()

    # ----------------------------------------------------------------- catalogue

    def upsert_model(self, model_id: str, *, provider: str, name: str = "", input: list | None = None,
                     context_window: int | None = None, max_tokens: int | None = None,
                     cost: dict | None = None, reasoning: bool = False, probe: dict | None = None) -> dict:
        with self._session() as s:
            row = s.scalar(select(m.ModelCatalogue).where(m.ModelCatalogue.model_id == model_id))
            if row is None:
                row = m.ModelCatalogue(model_id=model_id, provider=provider)
                s.add(row)
            row.provider, row.name, row.input = provider, name, input or ["text"]
            row.context_window, row.max_tokens, row.cost, row.reasoning = context_window, max_tokens, cost or {}, reasoning
            if probe is not None:
                row.probe = probe
            row.updated_at = m.utcnow()
            s.flush()
            return _row(row)

    def set_probe(self, model_id: str, probe: dict, *, actor: str = "admin") -> dict:
        with self._session() as s:
            row = s.scalar(select(m.ModelCatalogue).where(m.ModelCatalogue.model_id == model_id))
            if row is None:
                raise NotFound(f"no model {model_id!r} in the catalogue")
            before = row.probe
            row.probe, row.updated_at = probe, m.utcnow()
            s.flush()
            self.log(s, actor, "probe", "model", model_id, before=before, after=probe)
            return _row(row)

    def get_model(self, model_id: str) -> dict | None:
        with self._session() as s:
            row = s.scalar(select(m.ModelCatalogue).where(m.ModelCatalogue.model_id == model_id))
            return _row(row) if row else None

    def list_models(self) -> list[dict]:
        with self._session() as s:
            return [_row(r) for r in s.scalars(select(m.ModelCatalogue).order_by(m.ModelCatalogue.model_id)).all()]
