from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_agui_rejects_bad_body():
    r = client.post("/agui", json={"not": "a run input"})
    assert r.status_code == 422


def test_models_shape(monkeypatch):
    # Stub the SDK catalog so the test is offline.
    import app.main as m

    class _Model:
        def __init__(self, mid):
            self.id = mid

    monkeypatch.setattr(m, "_list_catalog", lambda: [_Model("composer-2.5"), _Model("gemini-2.5-pro")])
    monkeypatch.setattr(m, "default_cursor_model", lambda: "composer-2.5")
    r = client.get("/models")
    assert r.status_code == 200
    body = r.json()
    assert body["default"] == "composer-2.5"
    assert "composer-2.5" in body["models"]
    assert "gemini-2.5-pro" in body["models"]


def test_models_degraded_on_catalog_failure(monkeypatch):
    # When the catalog fetch fails, /models should degrade to status 200 with just the default.
    import app.main as m

    def _boom():
        raise RuntimeError("SDK down")

    monkeypatch.setattr(m, "_list_catalog", _boom)
    monkeypatch.setattr(m, "default_cursor_model", lambda: "composer-2.5")
    r = client.get("/models")
    assert r.status_code == 200
    body = r.json()
    assert body["models"] == ["composer-2.5"]
    assert body["default"] == "composer-2.5"
