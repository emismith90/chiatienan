import pytest

from kernos.content import Models, ProfileSpec, ToolPackRef
from kernos.kernel import Body, Draft, Principal, TurnContext
from kernos.packs import BasePack, DraftKind, PackError, PackRegistry, PackTool, apply_tool_overrides, compose_tools
from kernos.plugins import PackRender, empty_turn_body
from kernos.engine import TurnResult, ToolInvocation


class Stub(BasePack):
    id = "stub"
    cancel_tools = frozenset({"cancel_thing"})

    def tools(self, ctx):
        return {"a": PackTool("a", "tool a", {"type": "object"}, lambda args: {"ok": True}),
                "b": PackTool("b", "tool b", {"type": "object"}, lambda args: {"ok": True})}

    def draft_kinds(self):
        return {"thing_draft": DraftKind("thing_draft", lambda *a: None, stamps=frozenset({"raw_input", "logged_by", "turn_id"}))}

    def render(self, result):
        if result.last_result("propose_thing"):
            return Draft("thing_draft", {"x": 1})
        if result.last_result("typed"):
            return Body("typed body", {"type": "typed"}, claimed_by_pack=True)
        return None


def test_registry_overrides_and_composition():
    reg = PackRegistry()
    reg.register(Stub())
    assert reg.describe()[0]["draft_kinds"] == ["thing_draft"]
    with pytest.raises(PackError):
        reg.get("nope")
    tools = Stub().tools(None)
    out = apply_tool_overrides(tools, {"a": {"enabled": False}, "b": {"description": "renamed"}})
    assert list(out) == ["b"] and out["b"].description == "renamed"
    with pytest.raises(PackError):
        apply_tool_overrides(tools, {"zzz": {"enabled": False}})
    composed = compose_tools(reg, [ToolPackRef(pack="stub", tools={"a": {"enabled": False}})], None)
    assert list(composed) == ["b"]
    composed = compose_tools(reg, [{"pack": "stub"}], None)
    assert list(composed) == ["a", "b"]


async def test_pack_render_stamps_drafts_and_falls_back_to_prose_or_empty_body():
    reg = PackRegistry(); reg.register(Stub())
    spec = ProfileSpec(models=Models(text="m"), tool_packs=[ToolPackRef(pack="stub")])
    render = PackRender(reg)
    ctx = TurnContext(space_id="s", principal=Principal(7, "An"), text="@bot do", profile=spec)
    ctx.result = TurnResult(final_text="ignored", turn_id="t1", tools=[ToolInvocation("propose_thing", {}, {"ok": True})])
    await render.run(ctx, {})
    assert ctx.outcome == Draft("thing_draft", {"x": 1, "raw_input": "@bot do", "logged_by": "7", "turn_id": "t1"})
    ctx.result = TurnResult(final_text="999", tools=[ToolInvocation("typed", {}, {"ok": True})])
    await render.run(ctx, {})
    assert ctx.outcome.claimed_by_pack and ctx.outcome.text == "typed body"
    ctx.result = TurnResult(final_text="hello")
    await render.run(ctx, {})
    assert ctx.outcome == Body("hello", None, claimed_by_pack=False)
    ctx.result = TurnResult(final_text="", capped=True)
    await render.run(ctx, {"empty": {"capped": "Hết giờ rồi."}})
    assert ctx.outcome == Body("Hết giờ rồi.", None, claimed_by_pack=True)
    assert empty_turn_body(TurnResult(error="boom")) == "⚠️ boom"
    assert "nothing" in empty_turn_body(TurnResult())
