import os
import tempfile

import pytest

# Point the module-level Settings at writable temp paths BEFORE any app import
# (config.settings is frozen at import time). Real env, if present, wins.
_TMP = tempfile.mkdtemp(prefix="chiatienan-test-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/default.db")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pw")
os.environ.setdefault("CURSOR_SDK_WORKSPACE", f"{_TMP}/ws")

from app.db import Database  # noqa: E402


@pytest.fixture
def db(tmp_path):
    """A fresh file-backed SQLite database per test (WAL, real single-writer)."""
    database = Database(f"sqlite:///{tmp_path}/test.db")
    database.create_all()
    return database
