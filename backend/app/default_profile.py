"""chiatienan's seeded default profile: today's bot, as a ``ProfileSpec`` (plan Task 1.7).

Built from code and env on every start, so a fresh install with no content runs
exactly the behaviour the repo has today. ``test_default_profile.py`` asserts
that its engine half equals ``agent.default_engine_spec()`` field for field; the
pipeline lists today's steps in today's order with today's env values as config.
"""
from __future__ import annotations

from pathlib import Path

from app.agent import _read_context_files, _read_skills
from app.config import Settings, settings as _settings
from app.prompt import SYSTEM_PROMPT_TEMPLATE
from kernos.content import (
    Caps, Memory, Models, Persona, PipelineEntry, ProfileSpec, Prompt, Rule, Runtime, Skill, ToolPackRef,
)

#: Rules whose content is money-safety are tagged so the self-change scope can
#: blacklist them (design §8.3).
_MONEY_RULES = {"money-safety"}


def build_default_spec(settings: Settings | None = None) -> ProfileSpec:
    s = settings or _settings
    e = PipelineEntry
    return ProfileSpec(
        persona=Persona(handle=s.bot_handle, aliases=["bot"], name="Phoenix", language="vi"),
        # The system prompt is content (plan Task 2.7): the same template `app.prompt`
        # renders from code, rendered per turn by `kernos.prompt.template` with the
        # closed variable set. `test_prompt_content.py` pins the two paths equal.
        prompt=Prompt(body=SYSTEM_PROMPT_TEMPLATE, append=[]),
        rules=[Rule(slug=f["path"], content=f["content"],
                    tags=["money"] if f["path"] in _MONEY_RULES else [])
               for f in _read_context_files()],
        skills=[Skill(name=k["name"], description=k["description"], body=k["body"])
                for k in _read_skills()],
        models=Models(text=s.pi_model, vision=s.pi_vision_model, thinking=s.pi_thinking),
        caps=Caps(max_tools=s.pi_max_tools, max_seconds=s.pi_max_seconds),
        builtin_tools=list(s.pi_builtin_tools),
        memory=Memory(history_max_messages=s.history_max_messages,
                      window_weeks=s.memory_window_weeks,
                      image_lookback_messages=s.image_lookback_messages,
                      image_lookback_minutes=s.image_lookback_minutes),
        runtime=Runtime(cwd=str(Path(s.data_dir) / "pi-cwd"),
                        agent_dir=str(Path(s.data_dir) / "pi-agent")),
        pipeline={
            "context": [
                e(id="kernos.context.rollover", version="1",
                  config={"window_weeks": s.memory_window_weeks,
                          "header": "Auto-saved (older than 10 weeks)", "kind": "rollover"}),
                e(id="kernos.context.memory", version="1"),
                e(id="kernos.context.history", version="1",
                  config={"max_messages": s.history_max_messages, "bot_label": s.bot_handle}),
                e(id="kernos.context.images", version="1"),
            ],
            "prompt": [
                e(id="kernos.prompt.template", version="1"),
                e(id="kernos.prompt.sections", version="1"),
            ],
            "model": [e(id="kernos.model.passthrough", version="1")],
            "run": [e(id="app.run.legacy", version="1")],
            "render": [e(id="app.render.lunch", version="1")],
            "validate": [
                e(id="app.validate.fabricated_commit", version="1"),
                e(id="app.validate.unbacked_amounts", version="1"),
            ],
            "persist": [e(id="app.persist.cards", version="1")],
        },
        tool_packs=[ToolPackRef(pack="lunch_ledger"), ToolPackRef(pack="room_members"),
                    ToolPackRef(pack="lunch_places")],
        meta={"handles_money": True},
    )
