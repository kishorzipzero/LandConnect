"""Secure record updates: maker-checker, department scoping, evidence, stale protection, notifications."""
import pytest
from fastapi.testclient import TestClient

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
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


def as_user(c, user):
    c.cookies.clear()
    pw = PW if "." in user else f"test-{user}-password"
    assert c.post("/api/login", json={"username": user, "password": pw}).status_code == 200


def plot_with_ror(c):
    res = c.get("/api/result?ws=demo").json()
    f = next(f for f in res["layers"]["parcels"]["features"] if f["properties"].get("ror_owner"))
    return f["properties"]["plot_id"], f["properties"]["ror_owner"]


def propose(c, plot, value="Meena Kulkarni", evidence=True, field="owner"):
    files = {"evidence": ("sale_deed.pdf", PDF, "application/pdf")} if evidence else None
    return c.post("/api/change-requests?ws=demo", data={"plot_id": plot, "field": field, "new_value": value,
                                                        "reason": "Registered sale deed JNR-1-11111-2026 dated 2026-03-02"},
                  files=files)


def test_maker_checker_happy_path(client):
    as_user(client, "revenue.officer")
    plot, old = plot_with_ror(client)
    assert propose(client, plot, evidence=False).status_code == 400          # ownership needs a document
    r = propose(client, plot)
    assert r.status_code == 200, r.text
    cr = r.json()
    assert cr["status"] == "pending" and cr["old_value"] == old and len(cr["evidence_sha"]) == 64
    assert propose(client, plot, "Someone Else").status_code == 400         # one pending request per field
    assert client.post(f"/api/change-requests/{cr['id']}/approve?ws=demo", json={}).status_code == 403  # officer

    as_user(client, "municipal.approver")                                    # wrong department
    assert client.post(f"/api/change-requests/{cr['id']}/approve?ws=demo", json={}).status_code == 403

    as_user(client, "revenue.approver")
    r = client.post(f"/api/change-requests/{cr['id']}/approve?ws=demo", json={"note": "deed verified"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["request"]["status"] == "approved" and body["parcel"]["ror_owner"] == "Meena Kulkarni"
    assert body["parcel"]["updates"][-1]["request"] == cr["code"]
    assert client.get(f"/api/evidence/{cr['evidence_sha']}?ws=demo").content == PDF

    as_user(client, "municipal.officer")                                     # downstream department is told
    box = client.get("/api/inbox?ws=demo").json()
    note = next(n for n in box["notifications"] if n["ref"] == cr["code"])
    assert client.post(f"/api/inbox/{note['id']}/ack?ws=demo").status_code == 200

    entries = client.get(f"/api/audit?ws=demo&kind=change&subject={plot}").json()
    kinds = [e["kind"] for e in entries["entries"]]
    assert "change.proposed" in kinds and "change.approved" in kinds and entries["verify"]["ok"]
    actors = {e["kind"]: e["actor"] for e in entries["entries"]}
    assert actors["change.proposed"] == "revenue.officer" and actors["change.approved"] == "revenue.approver"


def test_department_scoping_and_four_eyes(client):
    as_user(client, "water.officer")
    plot, _ = plot_with_ror(client)
    assert propose(client, plot).status_code == 403                          # water cannot change ownership
    as_user(client, "admin")                                                  # 'all' department may propose...
    cr = propose(client, plot, "Anil Shetty").json()
    assert client.post(f"/api/change-requests/{cr['id']}/approve?ws=demo", json={}).status_code == 403  # ...not self-approve
    assert client.post(f"/api/change-requests/{cr['id']}/reject?ws=demo", json={"note": ""}).status_code == 403
    as_user(client, "revenue.approver")
    assert client.post(f"/api/change-requests/{cr['id']}/reject?ws=demo", json={"note": ""}).status_code == 400
    r = client.post(f"/api/change-requests/{cr['id']}/reject?ws=demo", json={"note": "deed not registered"})
    assert r.json()["request"]["status"] == "rejected"


def test_stale_request_is_blocked(client):
    as_user(client, "revenue.officer")
    plot, _ = plot_with_ror(client)
    cr = propose(client, plot, "Kavitha Hegde").json()
    from landconnect import api
    f = next(f for f in api._results["demo"]["layers"]["parcels"]["features"] if f["properties"]["plot_id"] == plot)
    f["properties"]["ror_owner"] = "Changed Meanwhile"                        # the record moved on
    as_user(client, "revenue.approver")
    r = client.post(f"/api/change-requests/{cr['id']}/approve?ws=demo", json={})
    assert r.status_code == 409 and "out of date" in r.json()["detail"]


def test_conflict_review_is_routed_by_department(client):
    as_user(client, "water.approver")
    res = client.get("/api/result?ws=demo").json()
    c = next(c for c in res["conflicts"] if c["status"] == "open" and "water" not in c.get("departments", []))
    assert client.post("/api/decisions?ws=demo", json={"conflict_id": c["id"]}).status_code == 403


def test_bad_evidence_rejected(client):
    as_user(client, "revenue.officer")
    plot, _ = plot_with_ror(client)
    r = client.post("/api/change-requests?ws=demo", data={"plot_id": plot, "field": "owner", "new_value": "X Y",
                                                         "reason": "Registered sale deed number 1234"},
                    files={"evidence": ("deed.pdf", b"MZ not a pdf", "application/pdf")})
    assert r.status_code == 400
