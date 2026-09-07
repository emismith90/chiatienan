"""The prompt is content now (plan Task 2.7): the template in ``app.prompt`` renders
exactly what the pre-Phase-2 code produced, for every sender case, from code and
through the ``kernos.prompt.template`` plugin alike. ``tests/legacy_prompt.py`` is
the pre-change module, kept verbatim as the oracle."""
from datetime import date, datetime, timezone

import pytest

from app import prompt
from kernos.adapters import HostAdapters
from kernos.adapters.memory import FixedClock, InMemoryHistory, InMemoryMemory, InMemoryMessages
from kernos.content import Models, Persona, ProfileSpec, Prompt
from kernos.kernel import Principal, TurnContext
from kernos.plugins import TemplatePrompt
from kernos.template import TemplateError, render, validate
from tests import legacy_prompt

CASES = [(None, None), ("An", None), ("An", 7), (None, 7)]


@pytest.mark.parametrize("name,sid", CASES)
def test_template_from_code_equals_the_legacy_prompt(name, sid):
    for day in (date(2026, 9, 5), date(2000, 1, 2)):
        assert prompt.build_system_prompt(sender_name=name, sender_id=sid, today=day) == \
            legacy_prompt.build_system_prompt(sender_name=name, sender_id=sid, today=day)


@pytest.mark.parametrize("name,sid", CASES)
async def test_template_plugin_equals_the_legacy_prompt(name, sid):
    h = InMemoryHistory()
    adapters = HostAdapters(history=h, memory=InMemoryMemory(), messages=InMemoryMessages(h),
                            clock=FixedClock(datetime(2026, 9, 5, 8, tzinfo=timezone.utc)))
    spec = ProfileSpec(models=Models(text="m"), persona=Persona(name="Phoenix", handle="phoenix"),
                       prompt=Prompt(body=prompt.SYSTEM_PROMPT_TEMPLATE))
    ctx = TurnContext(space_id="1", principal=Principal(sid, name), text="x", profile=spec)
    await TemplatePrompt(adapters).run(ctx, {})
    assert ctx.system == legacy_prompt.build_system_prompt(sender_name=name, sender_id=sid, today=date(2026, 9, 5))


async def test_append_sections_are_rendered_after_the_body():
    h = InMemoryHistory()
    adapters = HostAdapters(history=h, memory=InMemoryMemory(), messages=InMemoryMessages(h),
                            clock=FixedClock(datetime(2026, 9, 5, tzinfo=timezone.utc)))
    spec = ProfileSpec(models=Models(text="m"), prompt=Prompt(body="Hi {{persona.name}}", append=["Room {{space.id}} speaks {{persona.language}}"]))
    ctx = TurnContext(space_id="7", principal=Principal(1, "A"), text="x", profile=spec)
    await TemplatePrompt(adapters).run(ctx, {})
    assert ctx.system == "Hi Assistant\n\nRoom 7 speaks en"


def test_template_language_rules():
    v = {"a": {"b": "x", "n": None, "l": ["p", "q"]}, "today": "t"}
    allowed = frozenset({"a.b", "a.n", "a.l", "today"})
    assert render("{{a.b}}-{{a.n}}-{{a.l}}", v, allowed=allowed) == "x--p, q"
    assert render("{{#if a.n}}yes{{else}}no{{/if}}", v, allowed=allowed) == "no"
    assert render("{{#if a.b}}[{{#if a.n}}1{{else}}2{{/if}}]{{/if}}", v, allowed=allowed) == "[2]"
    assert validate("{{nope}}", allowed) == ["unknown variable 'nope'"]
    assert validate("{{#if a.b}}x", allowed) == ["1 unclosed '{{#if}}' block(s)"]
    assert validate("x{{/if}}", allowed) == ["'{{/if}}' without '{{#if}}'"]
    with pytest.raises(TemplateError):
        render("{{sender.bogus}}", v)
