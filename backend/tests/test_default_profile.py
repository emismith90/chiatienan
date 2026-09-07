"""The seeded profile IS today's configuration (plan Task 1.7)."""
from app import agent
from app.config import settings
from app.default_profile import build_default_spec


def test_engine_half_equals_todays_engine_spec():
    assert build_default_spec(settings).to_engine_spec() == agent.default_engine_spec()


def test_the_pipeline_lists_todays_steps_in_order_with_env_values():
    spec = build_default_spec(settings)
    ids = {stage: [e.id for e in entries] for stage, entries in spec.pipeline.items()}
    assert ids["context"] == ["kernos.context.rollover", "kernos.context.memory",
                              "kernos.context.history", "kernos.context.images"]
    assert ids["render"] == ["kernos.render.packs"] and ids["run"] == ["app.run.legacy"]
    assert ids["validate"] == ["app.validate.fabricated_commit", "app.validate.unbacked_amounts"]
    hist = next(e for e in spec.pipeline["context"] if e.id == "kernos.context.history")
    assert hist.config["max_messages"] == settings.history_max_messages
    assert all(e.version for entries in spec.pipeline.values() for e in entries)


def test_money_safety_is_tagged_and_the_profile_says_it_handles_money():
    spec = build_default_spec(settings)
    assert any(r.slug == "money-safety" and "money" in r.tags for r in spec.rules)
    assert spec.meta["handles_money"] is True
    assert spec.persona.handle == settings.bot_handle and "bot" in spec.persona.aliases
