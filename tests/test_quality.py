"""Elevation / change detection, GeoTIFF, ULPIN and ownership chain, privacy masking, bulk and batch decisions,
column-mapping confirmation, and learning from reviewers."""
import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box

PW = "dept-test-password"


# ----------------------------------------------------------------------------- pure functions
def test_geotiff_round_trip_and_ingest_role(tmp_path):
    from landconnect import geotiff, ingest
    a = (np.random.default_rng(1).random((30, 40)) * 5 + 880).astype(np.float32)
    grid = {"x0": 484380.0, "y_top": 1423020.0, "res": 0.5}
    geotiff.write(tmp_path / "dsm_2026.tif", a, grid, 32643)
    geotiff.write(tmp_path / "dtm.tif", a - 3, grid, 32643)
    b, g, epsg = geotiff.read(tmp_path / "dsm_2026.tif")
    assert np.allclose(a, b) and epsg == 32643 and g["x0"] == grid["x0"] and g["res"] == 0.5
    roles = {d.name: (d.format, d.role) for d in ingest.load_folder(tmp_path)}
    assert roles == {"dsm_2026.tif": ("GeoTIFF", "dsm"), "dtm.tif": ("GeoTIFF", "dtm")}


def test_elevation_change_detection_on_a_tiny_scene():
    from landconnect import elevation
    grid = {"x0": 0.0, "y_top": 100.0, "res": 0.5, "width": 200, "height": 200}
    dtm = np.full((200, 200), 900.0, np.float32)
    d1, d2 = dtm.copy(), dtm.copy()

    def put(z, g, h):
        z[elevation.mask_of(g, grid)] = 900.0 + h
    same, grown, raised, new, gone = (box(5, 80, 15, 90), box(30, 80, 40, 90), box(55, 80, 65, 90),
                                      box(5, 50, 15, 60), box(30, 50, 40, 60))
    for g, h1, h2 in ((same, 6.8, 6.8), (raised, 6.8, 10.0), (new, None, 6.8), (gone, 6.8, None)):
        if h1:
            put(d1, g, h1)
        if h2:
            put(d2, g, h2)
    put(d1, box(30, 80, 36, 90), 6.8)                   # extension: smaller footprint before
    put(d2, grown, 6.8)
    blds = [{"geom": g, "id": f"B{i}", "plot_id": f"P{i}"} for i, g in enumerate((same, grown, raised, new))]
    found = {(c["type"], c.get("plot_id")) for c in elevation.detect_changes(blds, d1, d2, dtm, grid, lambda g: "PX",
                                                                             ["2021-01-01", "2026-01-01"])}
    assert found == {("extension", "P1"), ("new_floor", "P2"), ("new", "P3"), ("demolished", "PX")}
    h, _ = elevation.sample(raised, d2, dtm, grid)
    assert h == 10.0 and elevation.floors_from(h) == 3


def test_ulpin_and_ownership_chain():
    from landconnect import insights
    assert insights.geohash(57.64911, 10.40744, 11) == "u4pruydqqvj"          # the reference example
    from shapely.geometry import Point
    u = insights.ulpin(Point(484400, 1423000).buffer(10), "KA")
    assert len(u) == 14 and u.startswith("KA") and u == u.upper()
    deeds = [{"id": "D1", "date": "2019-01-01", "seller": "Ramesh Rao", "buyer": "Sita Nayak"},
             {"id": "D2", "date": "2024-01-01", "seller": "Sita Nayak", "buyer": "Anil Joshi"}]
    assert insights.ownership_chain("Anil Joshi", deeds)["ok"]
    assert "mutation pending" in insights.ownership_chain("Sita Nayak", deeds)["verdict"]
    broken = insights.ownership_chain("Anil Joshi", [deeds[0], dict(deeds[1], seller="Someone Else")])
    assert not broken["ok"] and broken["steps"][1]["issue"]


def test_schema_overrides_fix_a_mapping():
    from landconnect import schema_mapper
    recs = [{"geom": None, "props": {"Ref": f"{i}/1", "Holder": "Anil Joshi"}} for i in range(20)]
    auto = schema_mapper.map_columns(["Ref", "Holder"], recs)
    fixed = schema_mapper.map_columns(["Ref", "Holder"], recs, {"Holder": {"field": "owner_name", "by": "approver"},
                                                                 "Ref": {"field": None, "by": "approver"}})
    got = {m["column"]: m for m in fixed["mappings"]}
    assert got["Holder"]["field"] == "owner_name" and got["Holder"]["confidence"] == 100 and "Ref" in fixed["unmapped"]
    assert all("needs_confirmation" in m for m in auto["mappings"])


def test_masking_hides_every_name():
    import json
    from landconnect import privacy
    r = {"generated_at": "x", "summary": {}, "layers": {
        "parcels": {"features": [{"properties": {"ror_owner": "Anil Joshi", "municipal_owner": "Sri Anil Joshi",
                                                 "dept_records": {"registration": [{"buyer": "Sita Nayak", "seller": "Anil Joshi"}]}}}]},
        "poi": {"features": [{"properties": {"name": "Canara Bank"}}]}},
         "conflicts": [{"type": "OWNER_MISMATCH", "detail": "RoR says 'Anil Joshi', register says 'Sita Nayak'.",
                        "versions": [{"label": "Anil Joshi"}, {"label": "Sita Nayak"}]}]}
    m = privacy.masked_result("t", r)
    txt = json.dumps(m, ensure_ascii=False)
    assert "Anil" not in txt and "Sita" not in txt and "A••• J•••" in txt
    assert m["layers"]["poi"]["features"][0]["properties"]["name"] == "Canara Bank"      # landmarks are not people
    assert r["layers"]["parcels"]["features"][0]["properties"]["ror_owner"] == "Anil Joshi"   # original untouched


# ----------------------------------------------------------------------------- API
@pytest.fixture()
def client(isolated_output, monkeypatch, tmp_path):
    if not (isolated_output / "demo" / "result.json").exists():
        pytest.skip("run the demo pipeline first")
    for u in ("admin", "reviewer", "viewer"):
        monkeypatch.setenv(f"LANDCONNECT_{u.upper()}_PASSWORD", f"test-{u}-password")
    monkeypatch.setenv("LANDCONNECT_DEPT_PASSWORD", PW)
    from landconnect import api, learning, pipeline, privacy
    monkeypatch.setattr(pipeline, "schema_overrides_path", lambda ws: tmp_path / f"{ws}_schema_overrides.json")
    monkeypatch.setattr(learning, "rules_path", lambda ws: tmp_path / f"{ws}_learned_rules.json")
    privacy._cache.clear()
    api._results.clear()
    api._runs.clear()
    api._failures.clear()
    with TestClient(api.app) as c:
        yield c


def as_user(c, user):
    c.cookies.clear()
    pw = PW if "." in user else f"test-{user}-password"
    assert c.post("/api/login", json={"username": user, "password": pw}).status_code == 200


def test_viewer_sees_masked_names_everywhere(client):
    as_user(client, "reviewer")
    full = client.get("/api/result?ws=demo").json()
    owner = next(f["properties"]["ror_owner"] for f in full["layers"]["parcels"]["features"] if f["properties"]["ror_owner"])
    assert not full["names_masked"]
    as_user(client, "viewer")
    r = client.get("/api/result?ws=demo").json()
    assert r["names_masked"] and owner not in str(r["layers"]["parcels"]) and owner not in str(r["conflicts"])
    assert owner not in client.get("/api/export/parcels.geojson?ws=demo").text
    assert owner not in client.get("/ogc/collections/parcels/items?ws=demo&limit=1000").text
    assert client.get(f"/api/search/parcels?ws=demo&owner={owner[:4]}").status_code == 403


def test_bulk_decisions_need_a_note_and_are_audited(client):
    as_user(client, "reviewer")
    r = client.get("/api/result?ws=demo").json()
    ids = [c["id"] for c in r["conflicts"] if c["status"] == "open"][:3]
    assert client.post("/api/decisions/bulk?ws=demo", json={"conflict_ids": ids}).status_code == 400
    res = client.post("/api/decisions/bulk?ws=demo", json={"conflict_ids": ids + ["C-NOPE"], "note": "Checked on site"}).json()
    assert len(res["decided"]) == 3 and res["skipped"][0]["reason"] == "unknown"
    notes = [e["detail"].get("note", "") for e in client.get("/api/audit?ws=demo&kind=review").json()["entries"]]
    assert sum("[bulk] Checked on site" in n for n in notes) >= 3
    as_user(client, "viewer")
    assert client.post("/api/decisions/bulk?ws=demo", json={"conflict_ids": ids, "note": "xxxxxx"}).status_code == 403


def test_batch_approval_keeps_four_eyes(client):
    as_user(client, "municipal.officer")
    r = client.get("/api/result?ws=demo").json()
    plots = [f["properties"]["plot_id"] for f in r["layers"]["parcels"]["features"]
             if f["properties"].get("municipal_pid") and f["properties"].get("municipal_use") == "residential"][:3]

    def propose(p):
        res = client.post("/api/change-requests?ws=demo", data={"plot_id": p, "field": "municipal_use",
                                                                "new_value": "commercial", "reason": "Shop found on the site visit"})
        assert res.status_code == 200, res.text
        return res.json()["id"]
    ids = [propose(p) for p in plots[:2]]
    as_user(client, "municipal.approver")
    res = client.post("/api/change-requests/batch?ws=demo", json={"ids": ids, "approve": True, "note": "ok"}).json()
    assert len(res["decided"]) == 2 and all(c["status"] == "approved" for c in res["decided"])
    as_user(client, "admin")                            # an approver's own request is skipped, never approved
    own = propose(plots[2])
    res = client.post("/api/change-requests/batch?ws=demo", json={"ids": [own], "approve": True}).json()
    assert not res["decided"] and "Four-eyes" in res["skipped"][0]["reason"]


def test_confirming_a_column_mapping(client):
    as_user(client, "reviewer")
    r = client.get("/api/result?ws=demo").json()
    ds = next(d for d in r["datasets"] if d.get("schema") and d["schema"]["mappings"])
    m = ds["schema"]["mappings"][0]
    res = client.post("/api/schema/confirm?ws=demo", json={"dataset": ds["name"], "column": m["column"], "field": m["field"]})
    assert res.status_code == 200
    got = next(x for x in res.json()["dataset"]["schema"]["mappings"] if x["column"] == m["column"])
    assert got["confirmed_by"] == "reviewer" and got["confidence"] == 100
    assert client.post("/api/schema/confirm?ws=demo", json={"dataset": "nope.csv", "column": "x"}).status_code == 404
    as_user(client, "viewer")
    assert client.post("/api/schema/confirm?ws=demo", json={"dataset": ds["name"], "column": m["column"]}).status_code == 403


def test_learning_from_reviewers(client):
    from landconnect import learning
    as_user(client, "reviewer")
    r = client.get("/api/result?ws=demo").json()
    by_type = {}
    for c in r["conflicts"]:
        if c["status"] == "open" and len(c.get("versions") or []) >= 2:
            by_type.setdefault(c["type"], []).append(c)
    ctype, items = max(by_type.items(), key=lambda kv: len(kv[1]))
    if len(items) < learning.MIN_DECISIONS + 1:
        pytest.skip("not enough conflicts of one type in the demo")
    src = items[0]["versions"][0]["source"]
    for c in items[:learning.MIN_DECISIONS]:
        assert client.post("/api/decisions?ws=demo", json={"conflict_id": c["id"], "choice": 0, "note": "doc checked"}).status_code == 200
    g = next(g for g in client.get("/api/learning?ws=demo").json()["patterns"] if g["type"] == ctype)
    assert g["eligible"] and g["top_source"] == src and g["agreement"] == 1.0
    assert client.post("/api/learning/rule?ws=demo", json={"type": ctype, "source": src}).status_code == 403   # admin only
    as_user(client, "admin")
    assert client.post("/api/learning/rule?ws=demo", json={"type": ctype, "source": src}).status_code == 200
    rest = [dict(c) for c in items[learning.MIN_DECISIONS:]]
    assert learning.apply({"ws": "demo"}, rest) == len(rest)
    assert all(c["status"] == "auto_resolved" and "Learned rule" in c["resolution"] for c in rest)


def test_statewide_maps_are_listed_served_in_ranges_and_protected(client):
    from landconnect import statewide
    if not list(statewide.ROOT.glob("*/*.pmtiles")):
        pytest.skip("no state-wide maps downloaded (python -m landconnect.statewide fetch puducherry)")
    as_user(client, "viewer")
    cat = client.get("/api/statewide").json()
    assert cat and all(s["tile_type"] == "mvt" and s["vector_layers"] and len(s["bounds"]) == 4 for s in cat)
    s = min(cat, key=lambda s: s["bytes"])
    r = client.get(s["url"], headers={"Range": "bytes=0-126"})
    assert r.status_code == 206 and r.content[:7] == b"PMTiles"
    assert s["overview"] and client.get(s["overview"]["url"]).headers["content-type"] == "image/png"
    assert client.get("/statewide/..%2F..%2Fdata/x.pmtiles").status_code == 404
    client.cookies.clear()
    assert client.get(s["url"]).status_code == 401


def test_pmtiles_hilbert_ids():
    from landconnect import statewide
    assert statewide._zxy(0) == (0, 0, 0)
    assert [statewide._zxy(i) for i in range(1, 5)] == [(1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)]
    assert statewide._zxy(19078479) == (12, 3423, 1763)            # PMTiles spec test vector
