"""Env-var settings for the chiatienan backend.

Deployment skeleton: only the vars needed to stand up the service + run the
Cursor SDK bridge smoke test. Teams / ledger / roster vars are added in the
implementation phase.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    cursor_api_key: str
    cursor_model: str
    cursor_workspace: str
    admin_password: str
    database_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            cursor_api_key=(os.environ.get("CURSOR_API_KEY") or "").strip(),
            cursor_model=(os.environ.get("CURSOR_SDK_MODEL") or "").strip() or "composer-2.5",
            cursor_workspace=(os.environ.get("CURSOR_SDK_WORKSPACE") or "").strip() or "/tmp/chiatienan-agent",
            admin_password=(os.environ.get("ADMIN_PASSWORD") or "").strip(),
            database_url=(os.environ.get("DATABASE_URL") or "").strip() or "sqlite:////data/chiatienan.db",
        )


settings = Settings.from_env()
