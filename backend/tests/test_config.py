from app.config import Settings

_REQUIRED_UNSET = (
    "PI_MODEL",
    "PI_MAX_TOOLS",
    "PI_MAX_SECONDS",
    "BOT_HANDLE",
    "DATABASE_URL",
    "TZ",
    "QR_BASE_URL",
    "QR_TEMPLATE",
)


def test_defaults_when_env_absent(monkeypatch):
    for k in _REQUIRED_UNSET:
        monkeypatch.delenv(k, raising=False)
    s = Settings.from_env()
    assert s.pi_model == "~deepseek/deepseek-v4-flash-latest"
    # Mandatory in practice: the primary is text-only, so every bill photo needs it.
    assert s.pi_vision_model == "qwen/qwen3-vl-30b-a3b-instruct"
    assert s.pi_provider == "openrouter" and s.pi_thinking == "medium"
    assert s.pi_max_tools == 40 and s.pi_max_seconds == 120
    assert not [a for a in vars(s) if a.startswith("cursor_")]
    assert s.bot_handle == "bot"
    assert s.database_url == "sqlite:////data/chiatienan.db"
    assert s.timezone == "Asia/Ho_Chi_Minh"
    assert s.qr_base_url == "https://img.vietqr.io/image"
    assert s.qr_template == "compact2"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("PI_MODEL", "qwen/qwen3-vl-30b-a3b-instruct")
    monkeypatch.setenv("PI_MAX_TOOLS", "5")
    monkeypatch.setenv("BOT_HANDLE", "lunchbot")
    monkeypatch.setenv("QR_BASE_URL", "https://img.vietqr.io/image/")
    s = Settings.from_env()
    assert s.pi_model == "qwen/qwen3-vl-30b-a3b-instruct"
    assert s.pi_max_tools == 5
    assert s.bot_handle == "lunchbot"
    # trailing slash stripped
    assert s.qr_base_url == "https://img.vietqr.io/image"


def test_bad_int_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("PI_MAX_TOOLS", "notanumber")
    s = Settings.from_env()
    assert s.pi_max_tools == 40


def test_memory_settings_defaults(monkeypatch):
    monkeypatch.delenv("MEMORY_WINDOW_WEEKS", raising=False)
    monkeypatch.delenv("HISTORY_MAX_MESSAGES", raising=False)
    from app.config import Settings
    s = Settings.from_env()
    assert s.memory_window_weeks == 10
    assert s.history_max_messages == 200


def test_memory_settings_from_env(monkeypatch):
    monkeypatch.setenv("MEMORY_WINDOW_WEEKS", "6")
    monkeypatch.setenv("HISTORY_MAX_MESSAGES", "50")
    from app.config import Settings
    s = Settings.from_env()
    assert s.memory_window_weeks == 6
    assert s.history_max_messages == 50


def test_data_dir_defaults_to_the_mounted_volume(monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    from app.config import Settings

    assert Settings.from_env().data_dir == "/data"


def test_ephemeral_workspace_is_warned_about_at_boot(monkeypatch, caplog):
    """Production ran on /tmp/chiatienan-agent, so every room's long-term memory
    was silently wiped on each deploy — which was always this warning's subject."""
    from dataclasses import replace

    from app import main

    monkeypatch.setattr(
        main, "settings", replace(main.settings, data_dir="/tmp/chiatienan-agent")
    )
    with caplog.at_level("WARNING", logger="chiatienan"):
        main._warn_if_workspace_is_ephemeral()
    assert "outside the mounted /data volume" in caplog.text


def test_data_dir_on_the_volume_is_silent(monkeypatch, caplog):
    from dataclasses import replace

    from app import main

    monkeypatch.setattr(
        main, "settings", replace(main.settings, data_dir="/data")
    )
    with caplog.at_level("WARNING", logger="chiatienan"):
        main._warn_if_workspace_is_ephemeral()
    assert caplog.text == ""
