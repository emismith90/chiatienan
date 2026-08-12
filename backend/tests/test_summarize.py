"""Summarize: send text, get text — and never crash a turn."""
import pytest

from app.summarize import summarize_messages


class FakeBridge:
    def __init__(self, script):
        self._script = script
        self.command = None

    async def request(self, command):
        self.command = command
        for message in self._script:
            yield dict(message, req_id=command["req_id"])

    async def send(self, message):  # pragma: no cover - summarize sends nothing
        raise AssertionError("summarize must not send follow-ups")


@pytest.fixture
def bridge(monkeypatch):
    def install(script):
        fake = FakeBridge(script)
        monkeypatch.setattr("app.pi_bridge.get_bridge", lambda: fake)
        return fake
    return install


async def test_it_returns_the_text_the_sidecar_sent(bridge):
    bridge([{"type": "summarize_done", "text": "- An trả 300k\n- Bình nợ 75k"}])
    assert "An trả 300k" in await summarize_messages("An: tôi trả 300k")


async def test_the_summary_prompt_wraps_the_history(bridge):
    fake = bridge([{"type": "summarize_done", "text": "x"}])
    await summarize_messages("An: chào")
    assert "tóm tắt" in fake.command["text"].lower()
    assert fake.command["text"].endswith("An: chào")
    assert fake.command["type"] == "summarize"


async def test_blank_history_never_reaches_the_model(bridge):
    fake = bridge([{"type": "summarize_done", "text": "should not be used"}])
    assert await summarize_messages("   ") == ""
    assert fake.command is None


@pytest.mark.parametrize("script", [
    [{"type": "summarize_done", "text": ""}],
    [{"type": "summarize_done", "text": "", "error": "provider down"}],
    [{"type": "fatal", "message": "sidecar exited"}],
])
async def test_any_failure_returns_empty_string(bridge, script):
    # chat._maybe_rollover leaves the watermark untouched on a blank result, so the
    # aged messages are retried next turn rather than silently dropped.
    bridge(script)
    assert await summarize_messages("An: chào") == ""


async def test_a_raising_bridge_returns_empty_rather_than_crashing_the_turn(monkeypatch):
    class Exploding:
        async def request(self, command):
            raise RuntimeError("no node")
            yield  # pragma: no cover
    monkeypatch.setattr("app.pi_bridge.get_bridge", lambda: Exploding())
    assert await summarize_messages("An: chào") == ""
