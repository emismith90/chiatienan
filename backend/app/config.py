"""Env-var settings for the chiatienan PWA lunch-splitting bot.

One frozen ``Settings`` dataclass, hydrated from the environment. A module-level
singleton ``settings`` is created on import; tests build fresh instances via
``Settings.from_env()`` under ``monkeypatch.setenv``.
"""
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
    # Cursor SDK
    cursor_api_key: str
    cursor_model: str
    cursor_workspace: str
    cursor_api_base: str
    max_tools: int
    max_seconds: int
    memory_window_weeks: int
    history_max_messages: int
    # Bot
    bot_handle: str
    # Storage
    database_url: str
    timezone: str
    # Admin
    admin_password: str
    # VietQR
    qr_base_url: str
    qr_template: str
    # Deploy
    caddy_domain: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            cursor_api_key=(os.environ.get("CURSOR_API_KEY") or "").strip(),
            cursor_model=(os.environ.get("CURSOR_SDK_MODEL") or "").strip() or "grok-4.5-fast",
            cursor_workspace=(os.environ.get("CURSOR_SDK_WORKSPACE") or "").strip()
            or "/data/cursor-agent",
            cursor_api_base=((os.environ.get("CURSOR_API_BASE") or "").strip() or "https://api.cursor.com").rstrip("/"),
            max_tools=_int_env("CURSOR_AGENT_MAX_TOOLS", 40),
            max_seconds=_int_env("CURSOR_AGENT_MAX_SECONDS", 120),
            memory_window_weeks=_int_env("MEMORY_WINDOW_WEEKS", 10),
            history_max_messages=_int_env("HISTORY_MAX_MESSAGES", 200),
            bot_handle=(os.environ.get("BOT_HANDLE") or "").strip() or "bot",
            database_url=(os.environ.get("DATABASE_URL") or "").strip() or "sqlite:////data/chiatienan.db",
            timezone=(os.environ.get("TZ") or "").strip() or "Asia/Ho_Chi_Minh",
            admin_password=(os.environ.get("ADMIN_PASSWORD") or "").strip(),
            qr_base_url=((os.environ.get("QR_BASE_URL") or "").strip() or "https://img.vietqr.io/image").rstrip("/"),
            qr_template=(os.environ.get("QR_TEMPLATE") or "").strip() or "compact2",
            caddy_domain=(os.environ.get("CADDY_DOMAIN") or "").strip(),
        )


settings = Settings.from_env()
