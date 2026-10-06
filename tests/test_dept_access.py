"""Department need-to-know: each department sees its own records in full, others as the access matrix allows,
while the plot's common information stays shared."""
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
    with TestClient(api.app) as c:
        yield c


def login(c, user):
    c.cookies.clear()
    r = c.post("/api/login", json={"username": user, "password": PW if "." in user else f"test-{user}-password"})
    assert r.status_code == 200, r.text


def _plot_with(res, *depts):
    for f in res["layers"]["parcels"]["features"]:
        p = f["properties"]
        if all(d in p["dept_records"] for d in depts):
            return p
    pytest.skip(f"no plot with {depts}")


def test_admin_sees_every_department_in_full(client):
    login(client, "admin")
    me = client.get("/api/me").json()
    assert me["dept_access"] is None and me["title"] == "Administrator"
    p = _plot_with(client.get("/api/result").json(), "electricity", "water", "town_planning")
    assert p["dept_records"]["water"][0]["fields"] and p["dept_records"]["water"][0]["name"]
    assert {"revenue", "municipal", "survey"} <= set(p["registers"])


def test_officer_sees_own_department_in_full_and_others_by_need(client):
    login(client, "admin")
    pid = _plot_with(client.get("/api/result").json(), "electricity", "water", "town_planning")["plot_id"]
    login(client, "electricity.officer")
    me = client.get("/api/me").json()
    assert me["title"] == "Section Officer (AE)" and me["dept_access"]["electricity"] == "full"
    res = client.get("/api/result").json()
    p = next(f["properties"] for f in res["layers"]["parcels"]["features"] if f["properties"]["plot_id"] == pid)
    elec, water = p["dept_records"]["electricity"][0], p["dept_records"]["water"][0]
    assert elec["name"] and elec["fields"]                              # own department: everything
    assert water["id"] and "name" not in water and "fields" not in water  # another utility: summary only
    assert "registration" not in p["dept_records"]                      # deeds are not shared with utilities
    assert p["resolved_owner"] is not None or p["owner_source"]         # common information unchanged
    assert "owner" not in p["registers"].get("revenue", {}) and "fields" not in p["registers"].get("municipal", {})
    one = client.get(f"/api/parcels/{pid}").json()["properties"]        # one-plot lookup follows the same rule
    assert "name" not in one["dept_records"]["water"][0]
    names = {f["properties"].get("name") for f in res["layers"]["connections"]["features"] if f["properties"]["dept"] == "water"}
    assert names == {None}


def test_admin_can_change_the_matrix_and_it_is_applied(client):
    login(client, "admin")
    pid = _plot_with(client.get("/api/result").json(), "water")["plot_id"]
    m = client.get("/api/admin/dept-access").json()
    assert m["matrix"]["water"]["water"] == "full" and m["matrix"]["electricity"]["water"] == "summary"
    assert client.put("/api/admin/dept-access", json={"viewer": "electricity", "target": "water", "level": "none"}).status_code == 200
    assert client.put("/api/admin/dept-access", json={"viewer": "water", "target": "water", "level": "none"}).status_code == 400
    login(client, "electricity.officer")
    p = client.get(f"/api/parcels/{pid}").json()["properties"]
    assert "water" not in p["dept_records"]
    login(client, "admin")
    client.put("/api/admin/dept-access", json={"viewer": "electricity", "target": "water", "level": "full"})
    login(client, "electricity.officer")
    assert client.get(f"/api/parcels/{pid}").json()["properties"]["dept_records"]["water"][0].get("fields")


def test_department_profile_lists_titles_and_access(client):
    login(client, "town_planning.approver")
    p = client.get("/api/me/profile").json()
    assert p["title"] == "Assistant Director of Town Planning"
    assert any("Approve Town planning changes" in s for s in p["can_do"])
    lv = {r["department"]: r["level"] for r in p["dept_access"]}
    assert lv["town_planning"] == "full" and lv["survey"] == "full"
    login(client, "gas.staff")
    assert client.get("/api/me").json()["role"] == "viewer"
