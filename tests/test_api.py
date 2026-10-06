"""API tests: sign-in, roles, API keys, review workflow, exports, path safety."""
import pytest
from fastapi.testclient import TestClient

from landconnect import auth, config


@pytest.fixture()
def client(isolated_output, monkeypatch):
    if not (isolated_output / "demo" / "result.json").exists():
        pytest.skip("run the demo pipeline first")
    for u, r in (("admin", "admin"), ("reviewer", "reviewer"), ("viewer", "viewer")):
        monkeypatch.setenv(f"LANDCONNECT_{u.upper()}_PASSWORD", f"test-{r}-password")
    from landconnect import api
    api._results.clear()
    api._runs.clear()
    api._failures.clear()
    with TestClient(api.app) as c:
        yield c


def login(c, user):
    r = c.post("/api/login", json={"username": user, "password": f"test-{user}-password"})
    assert r.status_code == 200, r.text
    return r


def test_everything_needs_sign_in(client):
    for url in ("/api/result?ws=demo", "/api/workspaces", "/ogc/collections?ws=demo", "/api/audit?ws=demo",
                "/api/export/parcels.geojson?ws=demo", "/output/demo/web/ortho_t2.jpg"):
        assert client.get(url).status_code == 401, url
    assert client.get("/").status_code == 200            # the sign-in page itself is public


def test_bootstrap_writes_credentials_file(client, isolated_output):
    assert (isolated_output / "initial_credentials.txt").exists()


def test_wrong_password_and_rate_limit(client):
    for _ in range(5):
        assert client.post("/api/login", json={"username": "admin", "password": "nope-nope-nope"}).status_code == 401
    assert client.post("/api/login", json={"username": "admin", "password": "test-admin-password"}).status_code == 429


def test_viewer_reads_but_cannot_decide_or_run(client):
    login(client, "viewer")
    assert client.get("/api/me").json()["role"] == "viewer"
    r = client.get("/api/result?ws=demo")
    assert r.status_code == 200 and r.json()["layers"]["parcels"]["features"]
    cid = next(c["id"] for c in r.json()["conflicts"] if c["status"] == "open")
    assert client.post("/api/decisions?ws=demo", json={"conflict_id": cid, "choice": None}).status_code == 403
    assert client.post("/api/run?ws=demo").status_code == 403
    assert client.get("/api/admin/users").status_code == 403


def test_reviewer_decision_is_attributed_persisted_and_undoable(client):
    login(client, "reviewer")
    res = client.get("/api/result?ws=demo").json()
    c = next(c for c in res["conflicts"] if c["status"] == "open" and len(c["versions"]) >= 2)
    r = client.post("/api/decisions?ws=demo", json={"conflict_id": c["id"], "choice": 0, "note": "sale deed seen"})
    assert r.status_code == 200
    body = r.json()
    assert body["conflict"]["status"] == "resolved" and body["conflict"]["resolved_by"] == "reviewer"
    assert body["audit"]["ok"]
    from landconnect import db
    assert db.load_decisions("demo")[c["id"]]["by"] == "reviewer"
    entries = client.get(f"/api/audit?ws=demo&kind=review&subject={c['plot_id']}").json()["entries"]
    assert entries[0]["actor"] == "reviewer" and entries[0]["kind"] == "review.decision"
    assert client.post("/api/decisions?ws=demo", json={"conflict_id": c["id"], "choice": 7}).status_code == 400
    pid = c["id"].split("/")
    r = client.post(f"/api/decisions/{c['id']}/reopen?ws=demo")
    assert r.status_code == 200 and r.json()["conflict"]["status"] == "open"
    assert c["id"] not in db.load_decisions("demo")


def test_admin_manages_users_and_api_keys(client):
    login(client, "admin")
    assert client.post("/api/admin/users", json={"username": "officer.1", "password": "short", "role": "reviewer"}).status_code == 400
    assert client.post("/api/admin/users", json={"username": "officer.1", "password": "long-enough-pass",
                                                   "role": "reviewer"}).status_code == 200
    key = client.post("/api/admin/api-keys", json={"name": "BBMP GIS", "role": "viewer"}).json()["key"]
    client.post("/api/logout")
    client.cookies.clear()
    r = client.get("/ogc/collections/parcels/items?ws=demo&limit=2", headers={"X-API-Key": key})
    assert r.status_code == 200 and r.json()["numberReturned"] == 2
    assert client.post("/api/decisions?ws=demo", json={"conflict_id": "x"}, headers={"X-API-Key": key}).status_code == 403
    assert client.get("/ogc/collections?ws=demo", headers={"X-API-Key": "lc_wrong"}).status_code == 401
    login(client, "admin")
    prefix = key[:10]
    assert client.post(f"/api/admin/api-keys/{prefix}/revoke").status_code == 200
    client.cookies.clear()
    assert client.get("/ogc/collections?ws=demo", headers={"X-API-Key": key}).status_code == 401


def test_exports_and_search(client):
    login(client, "viewer")
    for fmt in ("geojson", "csv", "kml", "gpkg", "shp"):
        r = client.get(f"/api/export/parcels.{fmt}?ws=demo")
        assert r.status_code == 200 and len(r.content) > 1000, fmt
    assert client.get("/api/export/ladm.gpkg?ws=demo").status_code == 200
    s = client.get("/api/search/parcels?ws=demo&status=review").json()
    assert s["total"] > 0 and all(i["status"] == "review" for i in s["items"])
    assert client.get("/api/stats?ws=demo").json()["parcels_by_status"]


def test_path_traversal_and_bad_input(client):
    login(client, "admin")
    assert client.get("/output/demo/web/..%2F..%2Flandconnect.db").status_code == 404
    assert client.get("/output/demo/web/audit.jsonl").status_code == 404
    assert client.get("/api/result?ws=../../etc").status_code == 404
    r = client.post("/api/upload?ws=demo", files={"files": ("evil.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400
    assert client.get("/ogc/collections/parcels/items?ws=demo&bbox=1,2,3").status_code == 400
