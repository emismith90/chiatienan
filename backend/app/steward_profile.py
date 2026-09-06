"""The steward: an agent that reviews this bot's own recent turns (plan Phase 10.2).

A **sub**-agent of the lunch business with a profile of its own. It enables exactly one
pack — ``os_admin`` — so its whole vocabulary is the CMS: read what went wrong
(``cms_get_friction``), read the turns behind it, draft one change to the lunch profile,
and open a proposal for a person to approve. It has no money tools, cannot write the
ledger, and cannot publish (the ``publish`` capability is refused for a profile that
names no ``eval.suites`` — Phase 8 review F3, and that is deliberate here).

**Seeded, and off.** ``ensure_sub_agent`` creates it on boot; nothing wires
``phoenix.delegates_to``, so no live room's manifest changes. Turning it on is one admin
call, documented in the README — because it adds ``ask_steward`` to every space the
default agent runs, and that is the operator's decision to make, not boot's.
"""
from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.config import settings as _settings
from kernos.content import Caps, Models, Persona, PipelineEntry, ProfileSpec, Prompt, Runtime, ToolPackRef
from kernos.osadmin import STEWARD_BRIEF

SLUG = "steward"
NAME = "Steward"

#: What a manager is told ``ask_steward`` does. It says "proposes", never "changes":
#: the tool returns a proposal id, and a person approves it through the admin API.
DESCRIPTION = (
    "Reviews this bot's own recent turns for mistakes it made — forged confirmations, money it "
    "could not account for, calls a rule refused, turns that timed out — and, when there is a "
    "clear pattern, drafts one change to a skill or the prompt and opens a proposal for a person "
    "to approve. It changes nothing by itself and never touches the ledger."
)

#: Read and draft only. `eval` costs real model calls and `publish` is refused without
#: eval suites, so both stay an operator's explicit grant.
CAPABILITIES = {"cms": ["read", "draft"]}

_PERSONA_BODY = """You are {{persona.name}}, the steward of this assistant's own configuration.
You are not talking to the room about lunch; you are reporting to the person who runs the bot.
Be short and concrete. Name turn ids. Never invent an amount — every number you write must come
from a tool result you were handed.

"""


def build_steward_spec(settings: Settings | None = None) -> ProfileSpec:
    s = settings or _settings
    e = PipelineEntry
    return ProfileSpec(
        persona=Persona(handle=SLUG, name=NAME, language="en"),
        prompt=Prompt(body=_PERSONA_BODY + STEWARD_BRIEF),
        models=Models(text=s.pi_model, vision=None, thinking=s.pi_thinking),
        # Its own ceiling; as a sub-agent it is clamped again to whatever the manager has
        # left (Phase 7 review F1), so this is the cap for a directly-bound ops room.
        caps=Caps(max_tools=20, max_seconds=180),
        builtin_tools=[],
        runtime=Runtime(cwd=str(Path(s.data_dir) / "pi-cwd"),
                        agent_dir=str(Path(s.data_dir) / "pi-agent")),
        pipeline={
            # No memory or history: the steward's input is the friction report, not the
            # room's conversation, and loading a lunch thread would only cost tokens.
            "prompt": [e(id="kernos.prompt.template", version="1"),
                       e(id="kernos.prompt.sections", version="1")],
            "model": [e(id="kernos.model.passthrough", version="1")],
            "run": [e(id="app.run.legacy", version="1")],
            "render": [e(id="kernos.render.packs", version="1")],
            # It writes prose about money it read from tools; both guards apply to it
            # exactly as they do to the lunch bot.
            "validate": [e(id="app.validate.fabricated_commit", version="1"),
                         e(id="app.validate.unbacked_amounts", version="1")],
            # Neither runs for a sub-agent (its nested run stops at `validate`); they are
            # here so the profile is also valid for a room bound straight to the steward.
            "persist": [e(id="kernos.persist.cards", version="1")],
            "after": [e(id="kernos.after.trace", version="1", config={"keep_days": 30})],
        },
        tool_packs=[ToolPackRef(pack="os_admin")],
    )
