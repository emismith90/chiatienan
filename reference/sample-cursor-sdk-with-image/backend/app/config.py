"""Env-var settings for the sample Cursor SDK agent (no DB, single API key)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    cursor_api_key: str
    cursor_model: str
    cursor_workspace: str
    cursor_api_base: str
    max_tools: int
    max_seconds: int
    cors_origins: list[str]

    @classmethod
    def from_env(cls) -> "Settings":
        origins = [o.strip() for o in (os.environ.get("CORS_ORIGINS") or "http://localhost:3000").split(",") if o.strip()]
        return cls(
            cursor_api_key=(os.environ.get("CURSOR_API_KEY") or "").strip(),
            cursor_model=(os.environ.get("CURSOR_SDK_MODEL") or "").strip() or "composer-2.5",
            cursor_workspace=(os.environ.get("CURSOR_SDK_WORKSPACE") or "").strip() or "/tmp/sample-cursor-agent",
            cursor_api_base=((os.environ.get("CURSOR_API_BASE") or "").strip() or "https://api.cursor.com").rstrip("/"),
            max_tools=_int_env("CURSOR_AGENT_MAX_TOOLS", 500),
            max_seconds=_int_env("CURSOR_AGENT_MAX_SECONDS", 1800),
            cors_origins=origins,
        )


settings = Settings.from_env()
