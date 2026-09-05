from datetime import datetime, timedelta, timezone

from kernos.adapters import (
    CardStore, Clock, Completion, EventSink, HistorySource, KnowledgeSource, MemoryStore, MessageStore,
)
from kernos.adapters.memory import (
    CannedCompletion, FixedClock, InMemoryCards, InMemoryHistory, InMemoryKnowledge, InMemoryMemory,
    InMemoryMessages, RecordingSink,
)
from kernos.kernel import TurnEvent


def test_in_memory_adapters_satisfy_the_protocols():
    h = InMemoryHistory()
    assert isinstance(h, HistorySource)
    assert isinstance(InMemoryMemory(), MemoryStore)
    assert isinstance(InMemoryKnowledge(), KnowledgeSource)
    assert isinstance(RecordingSink(), EventSink)
    assert isinstance(InMemoryMessages(h), MessageStore)
    assert isinstance(InMemoryCards(h), CardStore)
    assert isinstance(CannedCompletion(), Completion)
    assert isinstance(FixedClock(datetime(2026, 9, 5, tzinfo=timezone.utc)), Clock)


def test_history_render_and_images_and_aged():
    now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    h = InMemoryHistory()
    old = h.add("s", author="An", body="cũ", created_at=now - timedelta(weeks=20))
    h.add("s", author="An", body="bill", attachments={"images": [{"data": "x"}]})
    h.add("s", author=None, body="ok", kind="bot")
    ask = h.add("s", author="An", body="@bot log")
    assert h.render("s", bot_label="phoenix", since_id=0, limit=10, before_id=ask.id) == \
        "«An»: cũ\n«An»: bill\nphoenix: ok"
    assert h.render("s", bot_label="p", since_id=0, limit=1, before_id=None) == "«An»: @bot log"
    assert h.recent_images("s", before_id=ask.id) == [{"data": "x"}]
    assert h.aged("s", watermark=0, older_than=now - timedelta(weeks=10)) == ("cũ", old.id)
    assert h.aged("s", watermark=old.id, older_than=now + timedelta(days=3650))[1] == ask.id
    assert h.aged("t", watermark=0, older_than=now) == ("", None)


def test_memory_store_round_trip():
    m = InMemoryMemory()
    assert m.load("s") == "" and m.watermark("s") == 0
    m.append_summary("s", summary_text="- a", through_id=7, through_at="2026-09-05T12:00", header="Auto")
    assert "## Auto — 2026-09-05" in m.load("s") and m.watermark("s") == 7


async def test_cards_supersede_pending_of_the_same_kind_and_sink_records():
    h = InMemoryHistory()
    cards, msgs, sink = InMemoryCards(h), InMemoryMessages(h), RecordingSink()
    first, sup0 = cards.create("s", "expense_draft", {"bill_total": 1})
    second, sup1 = cards.create("s", "expense_draft", {"bill_total": 2})
    assert sup0 == [] and [c.id for c in sup1] == [first.id]
    assert cards.get("s", first.id).attachments["status"] == "superseded"
    reply = msgs.post("s", author=None, kind="bot", body="hi", attachments=None)
    assert msgs.to_payload(reply)["kind"] == "bot"
    await sink.emit(TurnEvent("run.started", "t"))
    await sink.emit_raw({"type": "message", "id": 1})
    assert sink.events == [{"type": "agent.run.started", "turn_id": "t"}, {"type": "message", "id": 1}]


async def test_canned_completion_and_fixed_clock():
    c = CannedCompletion("- summary")
    assert await c.complete("p", kind="rollover") == "- summary" and c.calls == [("p", "rollover")]
    clock = FixedClock(datetime(2026, 9, 5, 8, tzinfo=timezone.utc))
    assert clock.today().isoformat() == "2026-09-05"
