from datetime import datetime, timedelta, timezone

from kernos.adapters import HostAdapters
from kernos.adapters.memory import (
    CannedCompletion, FixedClock, InMemoryCards, InMemoryHistory, InMemoryMemory, InMemoryMessages,
)
from kernos.content import Caps, Models, ProfileSpec
from kernos.kernel import Principal, TurnContext
from kernos.plugins import (
    ImageLookback, MemoryLoad, ModelPassthrough, RecentHistory, Rollover, SectionsMessage, render_sections,
)

NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)


def _adapters(summary="- tóm tắt"):
    h = InMemoryHistory()
    return HostAdapters(history=h, memory=InMemoryMemory(), messages=InMemoryMessages(h),
                        clock=FixedClock(NOW), cards=InMemoryCards(h), completion=CannedCompletion(summary))


def _ctx(**kw):
    return TurnContext(space_id="s", principal=Principal(1, "An"), text="@bot hi", **kw)


async def test_rollover_folds_aged_messages_and_advances_the_watermark():
    a = _adapters()
    old = a.history.add("s", author="An", body="cũ", created_at=NOW - timedelta(weeks=20))
    a.history.add("s", author="An", body="mới", created_at=NOW)
    ctx = _ctx()
    await Rollover(a).run(ctx, {"window_weeks": 10, "header": "Auto", "kind": "rollover"})
    assert a.completion.calls == [("cũ", "rollover")]
    assert a.memory.watermark("s") == old.id and "tóm tắt" in a.memory.load("s")
    # nothing left to fold → completion not called again
    await Rollover(a).run(ctx, {"window_weeks": 10})
    assert len(a.completion.calls) == 1


async def test_rollover_leaves_the_watermark_alone_on_a_blank_summary():
    a = _adapters(summary="")
    a.history.add("s", author="An", body="cũ", created_at=NOW - timedelta(weeks=20))
    await Rollover(a).run(_ctx(), {"window_weeks": 10})
    assert a.memory.watermark("s") == 0 and a.memory.load("s") == ""


async def test_memory_history_and_images_plugins():
    a = _adapters()
    a.memory.append_summary("s", summary_text="- m", through_id=1, through_at="2026-09-01T00:00", header="H")
    a.history.add("s", author="An", body="one")              # id 1, below the watermark
    a.history.add("s", author="An", body="bill", attachments={"images": [{"data": "x"}]})
    a.history.add("s", author=None, body="ok", kind="bot")
    ask = a.history.add("s", author="An", body="@bot log")
    ctx = _ctx(before_id=ask.id)
    await MemoryLoad(a).run(ctx, {})
    await RecentHistory(a).run(ctx, {"max_messages": 200, "bot_label": "phoenix"})
    await ImageLookback(a).run(ctx, {})
    assert "- m" in ctx.memory
    assert ctx.history == "«An»: bill\nphoenix: ok"          # watermark 1 excluded "one"
    assert ctx.images == [{"data": "x"}]
    own = _ctx(images=[{"data": "own"}])
    await ImageLookback(a).run(own, {})
    assert own.images == [{"data": "own"}]                    # the message's own images win
    empty = _ctx()
    await MemoryLoad(_adapters()).run(empty, {})
    assert empty.memory is None                               # "" → None, as run_bot_turn passed it


def test_sections_renderer_equals_todays_render_prompt():
    from app.agent import _render_prompt
    cases = [
        dict(memory=None, history=None, image_count=0),
        dict(memory="M", history=None, image_count=0),
        dict(memory="M ", history=" H", image_count=2),
        dict(memory=None, history="H", image_count=1),
    ]
    for kw in cases:
        assert render_sections("  xin chào ", **kw) == _render_prompt("  xin chào ", sender_name="An", **kw)


async def test_sections_plugin_and_model_passthrough():
    ctx = _ctx(memory="M", history="H", images=[{"data": "x"}])
    await SectionsMessage().run(ctx, {"headers": {"user": "# User"}})
    assert ctx.message.startswith("# Bộ nhớ dài hạn\nM") and ctx.message.endswith("# User\n@bot hi")
    assert "Lượt này có 1 ảnh" in ctx.message
    ctx.profile = ProfileSpec(models=Models(text="t", vision="v", thinking="high"), caps=Caps(max_tools=5))
    await ModelPassthrough().run(ctx, {})
    assert (ctx.model, ctx.vision_model, ctx.thinking, ctx.caps) == ("t", "v", "high", {"max_tools": 5, "max_seconds": 120})
