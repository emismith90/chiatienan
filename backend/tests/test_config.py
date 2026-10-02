from app.config import Settings

_REQUIRED_UNSET = (
    "PI_MODEL",
    "PI_BUILTIN_TOOLS",
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
    assert s.pi_model == "openai/gpt-6.1-sol-pro"
    # Every bill photo routes here; by default the same multimodal model.
    assert s.pi_vision_model == "openai/gpt-6.1-sol-pro"
    assert s.pi_provider == "openrouter" and s.pi_thinking == "medium"
    assert s.pi_max_tools == 40 and s.pi_max_seconds == 600
    assert s.pi_builtin_tools == ("read", "write", "bash")
    assert not [a for a in vars(s) if a.startswith("cursor_")]
    assert s.bot_handle == "phoenix"
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


def test_builtin_tools_can_be_turned_off_entirely(monkeypatch):
    # Empty restores the structural guarantee: no bash means the model cannot
    # compute money, whatever money-safety.mdc does or does not persuade it to do.
    monkeypatch.setenv("PI_BUILTIN_TOOLS", "")
    assert Settings.from_env().pi_builtin_tools == ()


def test_builtin_tools_ignore_whitespace_and_empties(monkeypatch):
    monkeypatch.setenv("PI_BUILTIN_TOOLS", " read , bash ,,")
    assert Settings.from_env().pi_builtin_tools == ("read", "bash")


def test_embedding_settings(monkeypatch):
    from app.kernel import build_embedder
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_MIN_SIMILARITY", raising=False)
    s = Settings.from_env()
    assert s.embedding_model == "google/gemini-embedding-001" and s.embedding_min_similarity == 0.65
    emb = build_embedder(s)
    assert emb.model == "google/gemini-embedding-001" and emb.min_similarity == 0.65
    monkeypatch.setenv("EMBEDDING_MODEL", " baai/bge-m3 ")
    monkeypatch.setenv("EMBEDDING_MIN_SIMILARITY", "0.45")
    s = Settings.from_env()
    assert (s.embedding_model, s.embedding_min_similarity) == ("baai/bge-m3", 0.45)
    monkeypatch.setenv("EMBEDDING_MIN_SIMILARITY", "high")
    assert Settings.from_env().embedding_min_similarity == 0.65
    # explicitly empty = words only; so is a missing key
    monkeypatch.setenv("EMBEDDING_MODEL", "")
    assert Settings.from_env().embedding_model == "" and build_embedder(Settings.from_env()) is None
    monkeypatch.delenv("EMBEDDING_MODEL")
    monkeypatch.setenv("OPEN_ROUTER_KEY", "")
    assert build_embedder(Settings.from_env()) is None
