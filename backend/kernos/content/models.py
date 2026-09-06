"""The content plane's tables (design §5.0, §5.1) — its own ``Base``, ``kn_`` prefix.

Created next to the host's tables by :func:`kernos.content.schema.bind`. Nothing
here references a host table; the join to a host tenant is the opaque
``space_id`` string. Timestamps are UTC ``isoformat(timespec="seconds")`` strings
the framework writes itself. Specs and overrides are JSON columns.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


class Business(Base):
    __tablename__ = "kn_businesses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tool_packs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    plugins_allowed: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    seed: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow, nullable=False)


class Profile(Base):
    __tablename__ = "kn_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("kn_businesses.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: "boot" while the seeded profile tracks code/env; "human" after any human publish.
    managed_by: Mapped[str] = mapped_column(String(20), default="human", nullable=False)
    published_version_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow, nullable=False)


class ProfileVersion(Base):
    __tablename__ = "kn_profile_versions"
    __table_args__ = (UniqueConstraint("profile_id", "version", name="uq_kn_profile_version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("kn_profiles.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: draft | published | superseded | retired
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow, nullable=False)
    published_at: Mapped[str | None] = mapped_column(String(32))


class Source(Base):
    """An editable source: kind ∈ prompt | rule | skill | template."""

    __tablename__ = "kn_sources"
    __table_args__ = (UniqueConstraint("business_id", "kind", "slug", name="uq_kn_source"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("kn_businesses.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    frontmatter: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    etag: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(120), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow, nullable=False)


class Agent(Base):
    __tablename__ = "kn_agents"
    __table_args__ = (UniqueConstraint("business_id", "slug", name="uq_kn_agent_slug"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("kn_businesses.id"), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: manager | sub
    role: Mapped[str] = mapped_column(String(20), default="manager", nullable=False)
    #: the business's default manager — the one an unbound space runs. One per business.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey("kn_profiles.id"), nullable=False)
    delegates_to: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    max_depth: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow, nullable=False)


class SpaceBinding(Base):
    """One manager agent per space; framework-owned so a host needs no schema change."""

    __tablename__ = "kn_space_bindings"
    space_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("kn_agents.id"), nullable=False)
    overrides: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow, nullable=False)


class ModelCatalogue(Base):
    __tablename__ = "kn_model_catalogue"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    input: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    context_window: Mapped[int | None] = mapped_column(Integer)
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reasoning: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: {"ok": bool, "checked_at": iso, "schemas": [...], "notes": str, "source": str}
    probe: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow, nullable=False)


class AuditLog(Base):
    __tablename__ = "kn_audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(120), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    at: Mapped[str] = mapped_column(String(32), default=utcnow, nullable=False)


class TurnTrace(Base):
    """One row per turn (design §8.6; plan Task 4.1): the plugin trace, the tool
    calls with args and results, and a summary the admin timeline and eval capture
    read. ``turn_id`` is null when the turn failed before the engine ran."""

    __tablename__ = "kn_turn_traces"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    space_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    turn_id: Mapped[str | None] = mapped_column(String(80), index=True)
    profile_version_id: Mapped[int | None] = mapped_column(Integer)
    started: Mapped[str] = mapped_column(String(32), nullable=False)
    finished: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    tools: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    trace: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
