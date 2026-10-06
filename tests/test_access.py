"""Delegated access (who may grant which role), one-time passwords, and the PDF property report."""
import pytest
from fastapi.testclient import TestClient

PW = "dept-test-password"


@pytest.fixture()
def client(isolated_output, monkeypatch):
    if not (isolated_output / "demo" / "result.json").exists():
        pytest.skip("run the demo pipeline first")
    for u in ("admin", "reviewer", "viewer"):
        monkeypatch.setenv(f"LANDCONNECT_{u.upper()}_PASSWORD", f"test-{u}-password")
    monkeypatch.setenv("LANDCONNECT_DEPT_PASSWORD", PW)
    from landconnect import api
    api._results.clear()
    api._runs.clear()
    api._failures.clear()
    with TestClient(api.app) as c:
        yield c


def as_user(c, user, pw=None):
    c.cookies.clear()
    pw = pw or (PW if "." in user else f"test-{user}-password")
    r = c.post("/api/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def test_department_approver_grants_officers_only_in_own_department(client):
    as_user(client, "revenue.approver")
    r = client.post("/api/access/users", json={"username": "rev.clerk", "role": "officer", "department": "revenue",
                                               "full_name": "Revenue clerk"})
    assert r.status_code == 200, r.text
    temp = r.json()["one_time_password"]
    assert client.post("/api/access/users", json={"username": "x1", "role": "reviewer", "department": "revenue"}).status_code == 403
    assert client.post("/api/access/users", json={"username": "x2", "role": "officer", "department": "water"}).status_code == 403
    assert client.post("/api/access/users", json={"username": "x3", "role": "admin"}).status_code == 403
    names = {u["username"] for u in client.get("/api/access/users").json()}
    assert "rev.clerk" in names and "water.officer" not in names            # sees only own department
    # role changes within the delegation
    assert client.post("/api/access/users/rev.clerk/role", json={"role": "viewer"}).status_code == 200
    assert client.post("/api/access/users/rev.clerk/role", json={"role": "reviewer"}).status_code == 403
    assert client.post("/api/access/users/water.officer/role", json={"role": "viewer"}).status_code == 403
    assert client.post("/api/access/users/revenue.approver/role", json={"role": "viewer"}).status_code == 403  # self
    # one-time password must be changed before anything else
    user = as_user(client, "rev.clerk", temp)
    assert user["must_change"] is True
    assert client.get("/api/result?ws=demo").status_code == 403
    assert client.post("/api/me/password", json={"current": temp, "new": "my-own-password-1"}).status_code == 200
    user = as_user(client, "rev.clerk", "my-own-password-1")
    assert user["must_change"] is False and user["role"] == "viewer"
    assert client.get("/api/result?ws=demo").status_code == 200


def test_state_admin_grants_approvers_and_protects_last_admin(client):
    as_user(client, "admin")
    r = client.post("/api/access/users", json={"username": "tp.head", "role": "reviewer", "department": "town_planning"})
    assert r.status_code == 200
    assert client.post("/api/access/users/admin/active?active=false").status_code == 403          # self
    assert client.post("/api/access/users/admin/role", json={"role": "viewer"}).status_code == 403  # self
    temp = client.post("/api/access/users/revenue.officer/reset-password").json()["one_time_password"]
    assert as_user(client, "revenue.officer", temp)["must_change"] is True
    as_user(client, "admin")
    log = client.get("/api/access/audit").json()
    kinds = [e["kind"] for e in log["entries"]]
    assert log["verify"]["ok"] and "access.user_created" in kinds and "access.password_reset" in kinds
    assert all(e["actor"] for e in log["entries"])


def test_viewer_and_officer_cannot_manage_access(client):
    for u in ("viewer", "water.officer"):
        as_user(client, u)
        assert client.get("/api/access/users").status_code == 403
        assert client.post("/api/access/users", json={"username": "zz", "role": "viewer"}).status_code == 403


def test_property_report_pdf_for_approvers_and_admins(client):
    as_user(client, "viewer")
    plot = client.get("/api/result?ws=demo").json()["layers"]["parcels"]["features"][0]["properties"]["plot_id"]
    url = f"/api/parcels/{plot}/report.pdf?ws=demo"
    assert client.get(url).status_code == 403
    as_user(client, "water.officer")
    assert client.get(url).status_code == 403
    for u in ("revenue.approver", "admin"):
        as_user(client, u)
        r = client.get(url)
        assert r.status_code == 200 and r.content.startswith(b"%PDF") and len(r.content) > 20000
        assert "attachment" in r.headers["content-disposition"]
    entries = client.get(f"/api/audit?ws=demo&kind=report&subject={plot}").json()["entries"]
    assert len(entries) == 2 and len(entries[0]["detail"]["sha256"]) == 64
    assert client.get("/api/parcels/999/9/report.pdf?ws=demo").status_code == 404
