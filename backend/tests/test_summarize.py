import types

import pytest

import app.summarize as summarize_mod
from app.summarize import summarize_messages
from tests.test_agent import _FakeClient, _FakeRun, _text_msg


@pytest.mark.asyncio
async def test_summarize_empty_input_skips_llm():
    assert await summarize_messages("   ", kind="clear") == ""


@pytest.mark.asyncio
async def test_summarize_returns_assistant_text(monkeypatch):
    fake_run = _FakeRun([_text_msg("- An trả 100k\n- Còn nợ Bình 50k")])

    monkeypatch.setattr(summarize_mod, "_ensure_workspace", lambda: "/tmp/chiatienan-test")
    monkeypatch.setattr("app.cursor_runner.resolve_cursor_api_key", lambda *a, **k: "k", raising=False)
    monkeypatch.setattr(
        "app.cursor_runner.resolve_model_selection",
        lambda *a, **k: types.SimpleNamespace(id="composer-2.5", params=None), raising=False,
    )

    async def _fake_launch(AsyncClient, workspace, local):
        return _FakeClient(fake_run)

    monkeypatch.setattr(summarize_mod, "_launch_bridge_resilient", _fake_launch)

    out = await summarize_messages("«An»: 100k cả nhóm", kind="clear")
    assert "An trả 100k" in out


@pytest.mark.asyncio
async def test_summarize_returns_blank_on_failure(monkeypatch):
    monkeypatch.setattr(summarize_mod, "_ensure_workspace", lambda: "/tmp/chiatienan-test")
    monkeypatch.setattr("app.cursor_runner.resolve_cursor_api_key", lambda *a, **k: "k", raising=False)
    monkeypatch.setattr(
        "app.cursor_runner.resolve_model_selection",
        lambda *a, **k: types.SimpleNamespace(id="composer-2.5", params=None), raising=False,
    )

    async def _boom(*a, **k):
        raise RuntimeError("bridge dead")

    monkeypatch.setattr(summarize_mod, "_launch_bridge_resilient", _boom)
    assert await summarize_messages("«An»: 100k", kind="rollover") == ""
