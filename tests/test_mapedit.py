"""Mapping on the map: draw a building, remove one, reshape a boundary. All go through maker-checker approval."""
import json

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import mapping, shape

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


def propose(c, plot, field, value, reason="Field inspection on 2026-09-20", evidence=False):
    files = {"evidence": ("sketch.png", bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]) + b"0" * 64, "image/png")} if evidence else None
    return c.post("/api/change-requests?ws=demo", files=files,
                  data={"plot_id": plot, "field": field, "reason": reason,
                        "new_value": value if isinstance(value, str) else json.dumps(value)})


def empty_plot(c):
    r = c.get("/api/result?ws=demo").json()
    for f in r["layers"]["parcels"]["features"]:
        g = shape(f["geometry"])
        used = [shape(b["geometry"]) for b in r["layers"]["buildings"]["features"]
                if b["properties"]["plot_id"] == f["properties"]["plot_id"]]
        if g.area > 0 and not any(u.intersects(g.centroid.buffer(0.00004)) for u in used):
            return f["properties"]["plot_id"], g, r
    pytest.skip("no plot without a building")


def test_admin_maps_a_building_and_an_approver_publishes_it(client):
    as_user(client, "admin")
    plot, g, _ = empty_plot(client)
    house = g.centroid.buffer(0.00004, cap_style=3)                  # ~9 m square in the middle of the plot
    r = propose(client, plot, "building_add", {"footprint": mapping(house), "floors": 2})
    assert r.status_code == 200, r.text
    cr = r.json()
    assert cr["department"] == "survey" and cr["new_value"]["floors"] == 2 and cr["new_display"].startswith("new building")
    # an outline outside the plot, or a crossed outline, is refused
    from shapely import affinity
    far = affinity.translate(house, 0.01, 0.01)
    assert "inside plot" in propose(client, plot, "building_add", {"footprint": mapping(far)}).json()["detail"]
    bow = {"type": "Polygon", "coordinates": [[[77.58, 12.93], [77.5801, 12.9301], [77.5801, 12.93], [77.58, 12.9301], [77.58, 12.93]]]}
    assert propose(client, plot, "building_add", {"footprint": bow}).status_code == 400
    assert client.post(f"/api/change-requests/{cr['id']}/approve?ws=demo", json={}).status_code == 403   # four eyes
    as_user(client, "survey.approver")
    assert client.post(f"/api/change-requests/{cr['id']}/approve?ws=demo", json={}).status_code == 200
    res = client.get("/api/result?ws=demo").json()
    b = next(b for b in res["layers"]["buildings"]["features"] if b["properties"]["id"] == f"BLD-{cr['code']}")
    assert b["properties"]["plot_id"] == plot and b["properties"]["floors"] == 2
    p = next(f for f in res["layers"]["parcels"]["features"] if f["properties"]["plot_id"] == plot)["properties"]
    assert b["properties"]["id"] in p["buildings"]
    # the same house cannot be mapped twice; removing it works and is matched by shape
    as_user(client, "admin")
    assert "already mapped" in propose(client, plot, "building_add", {"footprint": mapping(house)}).json()["detail"]
    rm = propose(client, plot, "building_remove", b["properties"]["id"]).json()
    as_user(client, "survey.approver")
    assert client.post(f"/api/change-requests/{rm['id']}/approve?ws=demo", json={}).status_code == 200
    res = client.get("/api/result?ws=demo").json()
    assert not any(x["properties"]["id"] == b["properties"]["id"] for x in res["layers"]["buildings"]["features"])
    kinds = [e["kind"] for e in client.get(f"/api/audit?ws=demo&subject={plot}").json()["entries"]]
    assert kinds.count("change.approved") == 2


def test_boundary_edit_cannot_swallow_a_neighbour(client):
    as_user(client, "survey.officer")
    plot, g, _ = empty_plot(client)
    grown = g.buffer(0.0003, join_style=2)                           # ~30 m bigger in every direction
    r = propose(client, plot, "geometry", mapping(grown), evidence=True)
    assert r.status_code == 400 and "overlaps plot" in r.json()["detail"]
    as_user(client, "water.officer")                                  # not the survey department
    assert propose(client, plot, "building_add", {"footprint": mapping(g.centroid.buffer(0.00004, cap_style=3))}).status_code == 403


def test_correcting_a_building_outline_and_floors(client):
    as_user(client, "admin")
    res = client.get("/api/result?ws=demo").json()
    b = next(b for b in res["layers"]["buildings"]["features"] if b["properties"]["plot_id"])
    plot, bid = b["properties"]["plot_id"], b["properties"]["id"]
    g = shape(b["geometry"])
    assert "Nothing changed" in propose(client, plot, "building_edit", {"footprint": b["geometry"], "target": bid,
                                                                      "floors": b["properties"]["floors"]}).json()["detail"]
    smaller = g.buffer(-0.00001, join_style=2)
    r = propose(client, plot, "building_edit", {"footprint": mapping(smaller), "target": bid, "floors": 3})
    assert r.status_code == 200, r.text
    cr = r.json()
    assert cr["new_value"]["old"] == b["geometry"] and bid in cr["new_display"]
    assert "already awaiting" in propose(client, plot, "building_edit", {"footprint": mapping(smaller), "target": bid, "floors": 4}).json()["detail"]
    as_user(client, "survey.approver")
    assert client.post(f"/api/change-requests/{cr['id']}/approve?ws=demo", json={}).status_code == 200
    nb = next(x for x in client.get("/api/result?ws=demo").json()["layers"]["buildings"]["features"] if x["properties"]["id"] == bid)
    assert nb["properties"]["floors"] == 3 and shape(nb["geometry"]).area < g.area
    assert any(cr["code"] in s for s in nb["properties"]["sources"])


def test_new_plot_outside_the_mapped_area_then_a_building_on_it(client):
    from shapely import affinity
    as_user(client, "survey.officer")
    res = client.get("/api/result?ws=demo").json()
    bb = [shape(f["geometry"]).bounds for f in res["layers"]["parcels"]["features"]]
    miny, maxx = min(b[1] for b in bb), max(b[2] for b in bb)
    lot = affinity.translate(shape({"type": "Polygon", "coordinates": [[[0, 0], [0.0002, 0], [0.0002, 0.00015], [0, 0.00015], [0, 0]]]}),
                             maxx + 0.001, miny)                                   # ~20 x 17 m, beyond the area
    assert propose(client, "999/1", "parcel_add", mapping(lot)).status_code == 400          # needs the survey sketch
    assert propose(client, "bad id!", "parcel_add", mapping(lot), evidence=True).status_code == 400
    taken = res["layers"]["parcels"]["features"][0]["properties"]["plot_id"]
    assert "already exists" in propose(client, taken, "parcel_add", mapping(lot), evidence=True).json()["detail"]
    over = shape(res["layers"]["parcels"]["features"][0]["geometry"]).buffer(0.00005)
    assert "overlaps plot" in propose(client, "999/2", "parcel_add", mapping(over.envelope), evidence=True).json()["detail"]
    cr = propose(client, "999/1", "parcel_add", mapping(lot), evidence=True).json()
    assert cr["new_display"].startswith("new plot")
    as_user(client, "survey.approver")
    assert client.post(f"/api/change-requests/{cr['id']}/approve?ws=demo", json={}).status_code == 200
    res = client.get("/api/result?ws=demo").json()
    p = next(f["properties"] for f in res["layers"]["parcels"]["features"] if f["properties"]["plot_id"] == "999/1")
    assert p["status"] == "review" and p["new_plot"] and len(p["ulpin"]) == 14
    assert any(c["id"] == "C-NO_REVENUE_RECORD-999/1" and c["departments"] == ["revenue"] for c in res["conflicts"])
    # now a house can be mapped on it
    as_user(client, "survey.officer")
    house = lot.centroid.buffer(0.00003, cap_style=3)
    assert propose(client, "999/1", "building_add", {"footprint": mapping(house), "floors": 1}).status_code == 200
    # an approved new plot survives a pipeline re-run
    from landconnect import api, records
    r = api._result("demo")
    r["layers"]["parcels"]["features"] = [f for f in r["layers"]["parcels"]["features"] if f["properties"]["plot_id"] != "999/1"]
    assert records.apply_approved("demo", r) >= 1
    assert any(f["properties"]["plot_id"] == "999/1" for f in r["layers"]["parcels"]["features"])
