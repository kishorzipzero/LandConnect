"""The profile page: own details, preferences, password rules, sign-in sessions, activity."""
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


def login(c, user, pw=None, ua="Mozilla/5.0 (Windows NT 10.0) Chrome/130"):
    c.cookies.clear()
    pw = pw or (PW if "." in user else f"test-{user}-password")
    r = c.post("/api/login", json={"username": user, "password": pw}, headers={"User-Agent": ua})
    assert r.status_code == 200, r.text
    return r.cookies


def test_profile_shows_account_access_and_devices(client):
    login(client, "revenue.officer")
    p = client.get("/api/me/profile").json()
    assert p["username"] == "revenue.officer" and p["role_label"] == "Officer" and p["department"] == "revenue"
    assert p["last_login_at"] and p["can_do"] and "_session" not in client.get("/api/me").json()
    assert len(p["sessions"]) == 1 and p["sessions"][0]["current"] and p["sessions"][0]["device"] == "Chrome on Windows"


def test_editing_own_details_is_validated_and_audited(client):
    login(client, "viewer")
    ok = client.put("/api/me/profile", json={"full_name": "  Asha   Rao ", "designation": "Clerk", "email": "asha@tn.gov.in",
                                              "phone": "+91 98450 12345", "language": "ta"})
    assert ok.status_code == 200, ok.text
    p = ok.json()["profile"]
    assert p["full_name"] == "Asha Rao" and p["phone"] == "+919845012345" and p["language"] == "ta"
    for bad in ({"email": "asha@"}, {"phone": "12345"}, {"full_name": "A"}, {"language": "xx"}, {"employee_id": "a b<c>"}):
        assert client.put("/api/me/profile", json=bad).status_code == 400, bad
    assert client.put("/api/me/profile", json={"prefs": {"basemap": "streets", "default_ws": "demo", "start_tab": "review"}}).status_code == 200
    me = client.get("/api/me").json()
    assert me["prefs"] == {"basemap": "streets", "default_ws": "demo", "start_tab": "review"}
    assert client.put("/api/me/profile", json={"prefs": {"default_ws": "nowhere"}}).status_code == 400
    kinds = [a["kind"] for a in client.get("/api/me/profile").json()["activity"]]
    assert "access.profile_updated" in kinds


def test_password_rules_and_it_signs_out_everywhere(client):
    login(client, "revenue.officer")
    for bad in ("short", "revenue.officer-2026", "aaaaaaaaaaaa", "onlyletterspassword", PW):
        r = client.post("/api/me/password", json={"current": PW, "new": bad})
        assert r.status_code == 400, bad
    assert client.post("/api/me/password", json={"current": "wrong-password-1", "new": "Tidal-River-42"}).status_code == 400
    assert client.post("/api/me/password", json={"current": PW, "new": "Tidal-River-42"}).status_code == 200
    assert client.get("/api/me/profile").status_code == 401                       # every session ended
    login(client, "revenue.officer", "Tidal-River-42")
    assert client.get("/api/me/profile").json()["password_changed_at"]


def test_signing_out_other_devices(client):
    phone = login(client, "survey.officer", ua="Mozilla/5.0 (Linux; Android 14) Chrome/130 Mobile")
    login(client, "survey.officer")
    p = client.get("/api/me/profile").json()
    assert len(p["sessions"]) == 2 and sum(s["current"] for s in p["sessions"]) == 1
    other = next(s for s in p["sessions"] if not s["current"])
    assert other["device"] == "Chrome on Android"
    assert client.post(f"/api/me/sessions/{other['id']}/end").status_code == 200
    assert len(client.get("/api/me/profile").json()["sessions"]) == 1
    client.cookies.clear()
    client.cookies.update(phone)
    assert client.get("/api/me").status_code == 401                                 # the phone is signed out
    login(client, "survey.officer")
    login(client, "survey.officer")
    assert client.post("/api/me/sessions/end-others").json()["ended"] >= 1
    assert client.post("/api/me/sessions/zz/end").status_code == 400
