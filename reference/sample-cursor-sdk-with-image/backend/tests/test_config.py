import os
from app.config import Settings


def test_defaults_when_env_absent(monkeypatch):
    for k in ("CURSOR_SDK_MODEL", "CURSOR_AGENT_MAX_TOOLS", "CURSOR_AGENT_MAX_SECONDS", "CORS_ORIGINS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CURSOR_API_KEY", "k")
    s = Settings.from_env()
    assert s.cursor_api_key == "k"
    assert s.cursor_model == "composer-2.5"
    assert s.max_tools == 500
    assert s.max_seconds == 1800
    assert s.cors_origins == ["http://localhost:3000"]


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "k")
    monkeypatch.setenv("CURSOR_SDK_MODEL", "gemini-2.5-pro")
    monkeypatch.setenv("CURSOR_AGENT_MAX_TOOLS", "0")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.com,http://b.com")
    s = Settings.from_env()
    assert s.cursor_model == "gemini-2.5-pro"
    assert s.max_tools == 0
    assert s.cors_origins == ["http://a.com", "http://b.com"]
