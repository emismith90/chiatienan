import pytest

from app.db import Database


@pytest.fixture
def db(tmp_path):
    """A fresh file-backed SQLite database per test (WAL, real single-writer)."""
    database = Database(f"sqlite:///{tmp_path}/test.db")
    database.create_all()
    return database
