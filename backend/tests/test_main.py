from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok():
    with TestClient(app) as c:
        r = c.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_messages_disabled_without_bot_creds():
    # test env sets no MICROSOFT_APP_ID → bot is None → 503
    with TestClient(app) as c:
        r = c.post("/api/messages", json={"type": "message", "text": "hi"})
    assert r.status_code == 503


def test_admin_shows_login_page():
    with TestClient(app) as c:
        r = c.get("/admin")
    assert r.status_code == 200
    assert "đăng nhập" in r.text.lower()


def test_admin_login_wrong_password():
    with TestClient(app) as c:
        r = c.post("/admin/login", data={"password": "nope"})
    assert r.status_code == 401


def test_admin_login_and_roster(monkeypatch):
    # https base_url so the Secure cookie (correct for prod behind TLS) is stored
    with TestClient(app, base_url="https://testserver") as c:
        r = c.post("/admin/login", data={"password": "test-admin-pw"}, follow_redirects=False)
        assert r.status_code == 303
        # cookie now set on the client; roster page should render
        r2 = c.get("/admin")
        assert r2.status_code == 200
        assert "quản lý thành viên" in r2.text
