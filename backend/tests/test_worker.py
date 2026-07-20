import app.agent as agent_mod
from app import ledger
from app.agent import ToolInvocation, TurnResult
from app.reply import Reply
from app.worker import TurnJob, Worker


class _Capture:
    def __init__(self):
        self.sent = []

    async def send(self, reference, reply: Reply):
        self.sent.append((reference, reply))


def _fake_turn_result(record_meal_result):
    tr = TurnResult(final_text="")
    tr.tools = [ToolInvocation("record_meal", {}, record_meal_result)]
    return tr


async def test_worker_processes_and_sends_reply(monkeypatch, db):
    async def _fake_run(user_text, ctx, images=None):
        return _fake_turn_result(
            {"ok": True, "meal_id": 1, "total_amount": 200_000,
             "payer": {"id": 1, "name": "An"},
             "shares": [{"id": 1, "name": "An", "amount": 200_000}]}
        )

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run)
    cap = _Capture()
    w = Worker(db, send=cap.send)
    await w.process(TurnJob(activity_id="a1", user_text="200k", conversation_reference="ref"))

    assert len(cap.sent) == 1
    ref, reply = cap.sent[0]
    assert ref == "ref"
    assert "#1" in reply.text


async def test_worker_dedupes_duplicate_activity(monkeypatch, db):
    calls = {"n": 0}

    async def _fake_run(user_text, ctx, images=None):
        calls["n"] += 1
        return TurnResult(final_text="ok")

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run)
    cap = _Capture()
    w = Worker(db, send=cap.send)
    await w.process(TurnJob(activity_id="dup", user_text="hi", conversation_reference="r"))
    await w.process(TurnJob(activity_id="dup", user_text="hi", conversation_reference="r"))

    assert calls["n"] == 1  # second is dropped before running
    assert len(cap.sent) == 1
    with db.session() as s:
        assert ledger.already_processed(s, "dup")


async def test_worker_file_attachment_hint(monkeypatch, db):
    async def _fake_run(*a, **k):
        raise AssertionError("agent should not run for a file-only attachment")

    monkeypatch.setattr(agent_mod, "run_turn", _fake_run)
    cap = _Capture()
    w = Worker(db, send=cap.send)
    await w.process(
        TurnJob(activity_id="f1", user_text="", has_file_attachment=True, conversation_reference="r")
    )
    assert "dán ảnh" in cap.sent[0][1].text


async def test_worker_agent_failure_sends_fallback(monkeypatch, db):
    async def _boom(*a, **k):
        raise RuntimeError("bridge died")

    monkeypatch.setattr(agent_mod, "run_turn", _boom)
    cap = _Capture()
    w = Worker(db, send=cap.send)
    await w.process(TurnJob(activity_id="e1", user_text="hi", conversation_reference="r"))
    assert "lỗi" in cap.sent[0][1].text.lower()
