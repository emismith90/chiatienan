from app.config import Settings

_REQUIRED_UNSET = (
    "CURSOR_SDK_MODEL",
    "CURSOR_AGENT_MAX_TOOLS",
    "CURSOR_AGENT_MAX_SECONDS",
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
    assert s.cursor_model == "grok-4.5-fast"
    assert s.max_tools == 40
    assert s.max_seconds == 120
    assert s.bot_handle == "bot"
    assert s.database_url == "sqlite:////data/chiatienan.db"
    assert s.timezone == "Asia/Ho_Chi_Minh"
    assert s.qr_base_url == "https://img.vietqr.io/image"
    assert s.qr_template == "compact2"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("CURSOR_SDK_MODEL", "gemini-2.5-pro")
    monkeypatch.setenv("CURSOR_AGENT_MAX_TOOLS", "5")
    monkeypatch.setenv("BOT_HANDLE", "lunchbot")
    monkeypatch.setenv("QR_BASE_URL", "https://img.vietqr.io/image/")
    s = Settings.from_env()
    assert s.cursor_model == "gemini-2.5-pro"
    assert s.max_tools == 5
    assert s.bot_handle == "lunchbot"
    # trailing slash stripped
    assert s.qr_base_url == "https://img.vietqr.io/image"


def test_bad_int_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("CURSOR_AGENT_MAX_TOOLS", "notanumber")
    s = Settings.from_env()
    assert s.max_tools == 40


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
