"""Env-var settings for the chiatienan PWA lunch-splitting bot.

One frozen ``Settings`` dataclass, hydrated from the environment. A module-level
singleton ``settings`` is created on import; tests build fresh instances via
``Settings.from_env()`` under ``monkeypatch.setenv``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    """Comma-separated env var → tuple. An explicitly empty value means empty."""
    raw = os.environ.get(name)
    if raw is None:
        raw = default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


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
    data_dir: str
    pi_model: str
    pi_vision_model: str
    pi_provider: str
    pi_thinking: str
    pi_max_tools: int
    pi_max_seconds: int
    pi_builtin_tools: tuple[str, ...]
    memory_window_weeks: int
    history_max_messages: int
    # How far back to look for a bill image when the @phoenix message itself has
    # none (people paste the bill, then say "@phoenix log đi" in a second message).
    image_lookback_messages: int
    image_lookback_minutes: int
    # Bot
    bot_handle: str
    # Storage
    database_url: str
    timezone: str
    # Admin
    admin_password: str
    # Debug/export API (deploy/DEBUGGING.md §6). Separate credential from
    # ADMIN_PASSWORD on purpose: this surface exports the whole chatlog and
    # ledger, so it must be rotatable on its own and revocable by unsetting it
    # without taking the admin routes down with it. Unset = endpoints disabled.
    debug_api_key: str
    # Mirror of the stdout log to a file on the mounted volume, so the export
    # API can serve it (`docker compose logs` is unreadable from inside the
    # container). Empty path disables the mirror; stdout logging is unaffected.
    log_file: str
    log_max_bytes: int
    log_backup_count: int
    # VietQR
    qr_base_url: str
    qr_template: str
    # Deploy
    caddy_domain: str
    # Eval: the LLM judge for prose grading (`bench.judge`); unset = prose not graded.
    bench_judge_model: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=(os.environ.get("DATA_DIR") or "").strip() or "/data",
            # Probed against the real tool schemas, not taken from a catalogue's
            # `supported_parameters` — see bench/probe_models.py.
            pi_model=(os.environ.get("PI_MODEL") or "").strip()
            or "~deepseek/deepseek-v4-flash-latest",
            # Mandatory in practice: the primary is text-only, so every bill photo
            # routes here. An image turn with this unset fails loudly rather than
            # dropping the photo.
            pi_vision_model=(os.environ.get("PI_VISION_MODEL") or "").strip()
            or "qwen/qwen3-vl-30b-a3b-instruct",
            pi_provider=(os.environ.get("PI_PROVIDER") or "").strip() or "openrouter",
            pi_thinking=(os.environ.get("PI_THINKING") or "").strip() or "medium",
            pi_max_tools=_int_env("PI_MAX_TOOLS", 40),
            pi_max_seconds=_int_env("PI_MAX_SECONDS", 600),
            # Built-in pi tools available alongside the 14 money tools. Empty makes
            # money-safety structural (no `bash` = the model cannot compute money);
            # non-empty lets it work things out itself and re-enables the mechanism
            # behind a known prod defect. `grade_prose`'s moneyguard stage measures
            # the cost — see agent_sidecar/session.js::toolOptionsFor.
            # `is None` rather than `or`: an explicitly empty PI_BUILTIN_TOOLS
            # means "none", and falling back to the default there would silently
            # re-enable bash for anyone trying to turn it off.
            pi_builtin_tools=_csv_env("PI_BUILTIN_TOOLS", "read,write,bash"),
            memory_window_weeks=_int_env("MEMORY_WINDOW_WEEKS", 10),
            history_max_messages=_int_env("HISTORY_MAX_MESSAGES", 200),
            image_lookback_messages=_int_env("IMAGE_LOOKBACK_MESSAGES", 10),
            image_lookback_minutes=_int_env("IMAGE_LOOKBACK_MINUTES", 120),
            bot_handle=(os.environ.get("BOT_HANDLE") or "").strip() or "phoenix",
            database_url=(os.environ.get("DATABASE_URL") or "").strip() or "sqlite:////data/chiatienan.db",
            timezone=(os.environ.get("TZ") or "").strip() or "Asia/Ho_Chi_Minh",
            admin_password=(os.environ.get("ADMIN_PASSWORD") or "").strip(),
            bench_judge_model=(os.environ.get("BENCH_JUDGE_MODEL") or "").strip() or None,
            debug_api_key=(os.environ.get("DEBUG_API_KEY") or "").strip(),
            log_file=(os.environ.get("LOG_FILE") or "").strip() or "/data/logs/app.log",
            log_max_bytes=_int_env("LOG_MAX_BYTES", 5_000_000),
            log_backup_count=_int_env("LOG_BACKUP_COUNT", 3),
            qr_base_url=((os.environ.get("QR_BASE_URL") or "").strip() or "https://img.vietqr.io/image").rstrip("/"),
            qr_template=(os.environ.get("QR_TEMPLATE") or "").strip() or "compact2",
            caddy_domain=(os.environ.get("CADDY_DOMAIN") or "").strip(),
        )


settings = Settings.from_env()
