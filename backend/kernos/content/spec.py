"""``ProfileSpec`` — the published, resolved configuration of one agent (design §5.1).

This is the content plane's output and the kernel's input. It is the ``run``
command of the Pi sidecar minus the per-turn parts, plus the pipeline, packs,
validation and eval blocks kernos adds. ``to_engine_spec()`` produces exactly
the engine's half; ``pipeline`` is handed to :meth:`kernos.registry.Registry.build_pipeline`.

Everything optional has a default equal to "nothing", so a host's seeded default
profile can list only what it needs. Field names are the public surface (§12.5).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kernos.engine.base import EngineSpec


class _Strict(BaseModel):
    """Every spec model is strict and **frozen**: a resolved profile is shared between
    turns and spaces, so nothing may mutate it in place — overrides go through
    ``model_copy`` (review finding 8)."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Persona(_Strict):
    handle: str = "assistant"
    aliases: list[str] = Field(default_factory=list)
    name: str = "Assistant"
    language: str = "en"


class Prompt(_Strict):
    body: str = ""
    append: list[str] = Field(default_factory=list)


class Rule(_Strict):
    slug: str
    content: str
    tags: list[str] = Field(default_factory=list)


class Skill(_Strict):
    name: str
    description: str = ""
    body: str = ""
    delivery: str = "inline"          # inline | discoverable


class PromptTemplate(_Strict):
    name: str
    kind: str = "template"            # template | builtin
    content: str = ""
    description: str = ""


class Models(_Strict):
    text: str
    vision: str | None = None
    thinking: str = "medium"
    thinking_budgets: dict[str, int] | None = None


class Retry(_Strict):
    enabled: bool = True
    maxRetries: int = 3
    baseDelayMs: int = 2000


class Caps(_Strict):
    max_tools: int = 40
    max_seconds: int = 120


class Memory(_Strict):
    history_max_messages: int = 200
    window_weeks: int = 10
    image_lookback_messages: int = 10
    image_lookback_minutes: int = 120
    vision_history_max_messages: int | None = None
    summary_prompt: str | None = None


class Runtime(_Strict):
    """Boot-layer paths the engine needs; a host fills them, content never does."""

    cwd: str = ""
    agent_dir: str = ""


class PipelineEntry(_Strict):
    id: str
    version: str
    config: dict[str, Any] = Field(default_factory=dict)


class ToolPackRef(_Strict):
    pack: str
    tools: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ValidationRuleRef(_Strict):
    id: str
    scope: str                        # tool_args | tool_result | reply | content
    plugin: str
    version: str = "1"
    config: dict[str, Any] = Field(default_factory=dict)
    tool: str | None = None
    on_fail: str = "warn"


class Eval(_Strict):
    suites: list[str] = Field(default_factory=list)
    gate: dict[str, float] = Field(default_factory=dict)


class ProfileSpec(_Strict):
    persona: Persona = Field(default_factory=Persona)
    prompt: Prompt = Field(default_factory=Prompt)
    rules: list[Rule] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    templates: list[PromptTemplate] = Field(default_factory=list)
    models: Models
    retry: Retry = Field(default_factory=Retry)
    caps: Caps = Field(default_factory=Caps)
    builtin_tools: list[str] = Field(default_factory=list)
    memory: Memory = Field(default_factory=Memory)
    settings: dict[str, Any] = Field(default_factory=dict)
    runtime: Runtime = Field(default_factory=Runtime)
    pipeline: dict[str, list[PipelineEntry]] = Field(default_factory=dict)
    tool_packs: list[ToolPackRef] = Field(default_factory=list)
    validation: list[ValidationRuleRef] = Field(default_factory=list)
    eval: Eval = Field(default_factory=Eval)
    extensions: list[dict[str, Any]] = Field(default_factory=list)
    #: Free-form host metadata (e.g. ``handles_money`` until packs carry it, Phase 3).
    meta: dict[str, Any] = Field(default_factory=dict)

    def to_engine_spec(self, *, system: str | None = None) -> EngineSpec:
        """The engine's half of this profile. Skills go out as ``{name, description,
        body}`` and rules as ``{path, content}`` — the exact shapes the sidecar reads
        today, so the seeded default profile round-trips byte for byte."""
        return EngineSpec(
            model=self.models.text,
            vision_model=self.models.vision,
            thinking=self.models.thinking,
            builtin_tools=list(self.builtin_tools),
            max_tools=self.caps.max_tools,
            max_seconds=self.caps.max_seconds,
            cwd=self.runtime.cwd,
            agent_dir=self.runtime.agent_dir,
            system=system,
            skills=[{"name": s.name, "description": s.description, "body": s.body}
                    for s in self.skills if s.delivery == "inline"],
            context_files=[{"path": r.slug, "content": r.content} for r in self.rules],
            settings=dict(self.settings),
            extensions=list(self.extensions),
        )

    def pipeline_dict(self) -> dict[str, list[dict]]:
        return {stage: [e.model_dump() for e in entries] for stage, entries in self.pipeline.items()}

    def stored(self) -> dict:
        """The JSON the content plane persists: everything but ``runtime``, which is
        boot-layer and injected by the host at resolve time (review finding 2)."""
        return self.model_dump(exclude={"runtime"})

    def with_runtime(self, runtime: "Runtime") -> "ProfileSpec":
        return self.model_copy(update={"runtime": runtime})


class BindingOverrides(_Strict):
    """What a space may change about the profile it is bound to (design §3):
    add-only prompt sections, and the persona's handle/language."""

    append_sections: list[str] = Field(default_factory=list)
    handle: str | None = None
    language: str | None = None

    def apply(self, spec: ProfileSpec) -> ProfileSpec:
        if not (self.append_sections or self.handle or self.language):
            return spec
        prompt = spec.prompt.model_copy(update={"append": [*spec.prompt.append, *self.append_sections]})
        persona_update = {k: v for k, v in (("handle", self.handle), ("language", self.language)) if v}
        persona = spec.persona.model_copy(update=persona_update) if persona_update else spec.persona
        return spec.model_copy(update={"prompt": prompt, "persona": persona})
