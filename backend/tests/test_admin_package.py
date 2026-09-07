"""Export and import through the admin API (plan Task 9.2)."""
import io
import json
import zipfile

from app.kernel import kernel_for

ADMIN = {"X-Admin-Password": "test-admin-pw"}


def test_export_and_import_routes(api_client_room):
    client, _h, _r, _m = api_client_room
    from app.db import get_db
    k = kernel_for(get_db())
    pid = k.seed_report["profile_id"]
    assert client.get(f"/api/admin/profiles/{pid}/export").status_code == 401
    r = client.get(f"/api/admin/profiles/{pid}/export", headers=ADMIN)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        names = set(z.namelist())
        kernos = json.loads(z.read("kernos.json"))
    assert {"package.json", "kernos.json", "AGENTS.md", ".pi/settings.json", "README.md"} <= names
    assert kernos == k.store.published_spec(pid)
    assert client.get(f"/api/admin/profiles/{pid}/export?version=99", headers=ADMIN).status_code == 404
    fresh = k.store.create_business("copy", "Copy")
    r = client.post(f"/api/admin/businesses/{fresh['id']}/import", content=r.content, headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"]["status"] == "draft" and any(s["slug"] == "money-safety" for s in body["sources"])
    r2 = client.post(f"/api/admin/businesses/{fresh['id']}/import", content=r.request.content, headers=ADMIN)
    assert r2.status_code == 409
    r3 = client.post(f"/api/admin/businesses/{fresh['id']}/import?replace=true", content=r.request.content,
                     headers={**ADMIN, "X-Actor": "agent:phoenix"})
    assert r3.status_code == 422 and "agent may not import" in r3.text
    assert client.post(f"/api/admin/businesses/{fresh['id']}/import", content=b"not a zip", headers=ADMIN).status_code == 422
