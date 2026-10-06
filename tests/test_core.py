"""Unit tests for the harmonisation rules (see docs/RULES_AND_STANDARDS.md)."""
import json

import numpy as np
import pytest
from shapely.geometry import Polygon, box

from landconnect import audit, crs_engine, geo, query, schema_mapper as sm, topology


# ----------------------------------------------------------------------------- B2 plot IDs & units
@pytest.mark.parametrize("raw,norm", [
    ("KH-045/2", "45/2"), ("Sy.No. 45 Hissa 2A", "45/2A"), ("Plot 45/A", "45/A"), ("45-2a", "45/2A"),
    ("Survey No. 45", "45"), ("101-3", "101/3"), ("Sy.No 101/3", "101/3"), (None, None), ("no digits", None)])
def test_plot_id_normalisation(raw, norm):
    assert sm.normalize_plot_id(raw) == norm


def test_plot_id_similarity_hierarchy():
    assert sm.id_similarity("45/2", "45/2") == 1.0
    assert sm.id_similarity("45/2", "45/2A") == 0.8
    assert sm.id_similarity("45", "45/A") == 0.7
    assert sm.id_similarity("45/2", "45/3") == 0.3
    assert sm.id_similarity("45/2", "46/2") < 0.5


@pytest.mark.parametrize("col,unit,factor", [
    ("Extent (Sq.Yd)", "sq yd", 0.836127), ("prop_area_sqft", "sq ft", 0.092903),
    ("Extent (Gunta)", "gunta (Karnataka/Telangana)", 101.17141), ("EXTENT_GU", "gunta (Karnataka/Telangana)", 101.17141),
    ("Area_cents", "cent (Tamil Nadu/Kerala)", 40.468564), ("area_in_meters", "sq m", 1.0)])
def test_area_units(col, unit, factor):
    assert sm.detect_unit(col) == (unit, factor)


# ----------------------------------------------------------------------------- B3 owner names
@pytest.mark.parametrize("a,b", [
    ("Sri Ramesh Kumar", "R. KUMAR"), ("Kumar Ramesh", "ramesh kumar S"), ("Soonita Rao", "SUNEETA RAO"),
    ("ರಮೇಶ್ ಕುಮಾರ್", "Ramesh Kumar"), ("ಶ್ರೀನಿವಾಸ್ ಗೌಡ", "Srinivas Gowda"), ("ನಂದಿನೀ ರಾವ್", "Nandini Rao"),
    ("Lakshmi Kumar N", "Lakshmi Kumar R")])
def test_same_person(a, b):
    assert sm.owner_similarity(a, b) >= 75


@pytest.mark.parametrize("a,b", [("R. Kumar", "S. Kumar"), ("RAMESH KUMAR", "Suresh Kumar"),
                                 ("ರಮೇಶ್ ಕುಮಾರ್", "Suresh Kumar"), ("Ravi Nair", "Rajesh Kumar")])
def test_different_people(a, b):
    assert sm.owner_similarity(a, b) < 75


def test_initial_only_match_is_capped():
    assert sm.owner_similarity("Sri Ramesh Kumar", "R. KUMAR") <= 85


# ----------------------------------------------------------------------------- B2 column mapping
def test_column_mapping_by_name_and_values():
    rows = [{"props": {"Khasra No.": f"KH-{100 + i:03d}/{i % 4 + 1}", "Pattadar Name": n, "OCCUPANT": n,
                       "Father/Husband Name": f, "Extent (Sq.Yd)": 300 + i}}
            for i, (n, f) in enumerate([("Ramesh Kumar", "Anil Rao"), ("Priya Shetty", "Mohan Das"),
                                         ("Kavitha Rao", "Girish Bhat"), ("Naveen Gowda", "Ravi Nair")])]
    m = sm.map_columns(["Khasra No.", "Pattadar Name", "Father/Husband Name", "Extent (Sq.Yd)"], rows)
    got = {x["field"]: x["column"] for x in m["mappings"]}
    assert got == {"plot_id": "Khasra No.", "owner_name": "Pattadar Name", "area": "Extent (Sq.Yd)"}
    assert "Father/Husband Name" in m["unmapped"]
    only_values = sm.map_columns(["OCCUPANT"], rows)["mappings"]
    assert only_values and only_values[0]["field"] == "owner_name"


# ----------------------------------------------------------------------------- B4 CRS
def test_crs_detection():
    assert crs_engine.detect(None, np.array([[77.58, 12.93]]))["epsg"] == 4326
    assert crs_engine.detect("urn:ogc:def:crs:EPSG::3857", np.array([[8.6e6, 1.45e6]]))["epsg"] == 3857
    assert crs_engine.detect(None, np.array([[1000.0, 2000.0]]))["epsg"] is None       # unknown local grid


def test_georeference_recovers_affine():
    rng = np.random.default_rng(1)
    theta, s, t = np.radians(1.5), 0.997, np.array([500000.0, 1400000.0])
    R = s * np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    parcels, gnss = [], []
    for i in range(12):
        local = box(i * 30, (i % 3) * 30, i * 30 + 20, (i % 3) * 30 + 20)
        parcels.append({"plot_id": f"1/{i}", "geom_local": local})
        for x, y in geo.ring_points(local):
            gx, gy = R @ [x, y] + t + rng.normal(0, 0.01, 2)
            gnss.append({"plot_id": f"1/{i}", "pt": (gx, gy)})
    M, _, rep = crs_engine.georeference(parcels, gnss)
    assert rep["rmse_m"]["refined_affine"] < 0.05
    assert abs(rep["transform"]["rotation_deg"] - 1.5) < 0.01


# ----------------------------------------------------------------------------- B6 topology
def test_topology_removes_overlaps_and_repairs_bowtie():
    a = box(0, 0, 10, 10)
    b = box(9.6, 0, 20, 10)                                            # 0.4 m overlap
    bow = Polygon([(30, 0), (40, 10), (40, 0), (30, 10)])              # swapped vertices
    parcels = [{"plot_id": "1/1", "geom": a}, {"plot_id": "1/2", "geom": b}, {"plot_id": "1/3", "geom": bow},
               {"plot_id": "1/1", "geom": a}]                           # duplicate
    out, rep = topology.clean(parcels, [])
    assert rep["overlaps_remaining"] == 0 and rep["invalid_remaining"] == 0
    assert rep["duplicates_removed"] == ["1/1"] and rep["invalid_repaired"] == ["1/3"]
    assert abs(next(p for p in out if p["plot_id"] == "1/3")["geom"].area - 100) < 1e-6


# ----------------------------------------------------------------------------- B13 audit chain
def test_audit_chain_detects_tampering(tmp_path):
    p = tmp_path / "audit.jsonl"
    log = audit.AuditLog(p)
    for i in range(5):
        log.append("parcel.finalised", f"1/{i}", {"score": 90 + i})
    log.close()
    assert audit.verify(p) == {"ok": True, "entries": 5, "head": audit.verify(p)["head"], "broken_at": None}
    lines = p.read_text(encoding="utf-8").splitlines()
    e = json.loads(lines[2])
    e["detail"]["score"] = 10
    lines[2] = json.dumps(e)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = audit.verify(p)
    assert not r["ok"] and r["broken_at"] == 3


def test_audit_chain_detects_deletion(tmp_path):
    p = tmp_path / "audit.jsonl"
    log = audit.AuditLog(p)
    for i in range(4):
        log.append("x", str(i))
    log.close()
    lines = p.read_text(encoding="utf-8").splitlines()
    del lines[1]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert audit.verify(p)["ok"] is False


# ----------------------------------------------------------------------------- B14 query parser
def test_query_parser():
    f = query.parse_rules("vacant plots larger than 5 guntas")
    assert f["land_use"] == "vacant" and abs(f["min_area_sqm"] - 505.9) < 0.1
    f = query.parse_rules("buildings with more than 3 floors")
    assert f["entity"] == "buildings" and f["min_floors"] == 4
    f = query.parse_rules("parcels with owner conflicts needing review")
    assert f["conflict_types"] == ["OWNER_MISMATCH"] and f["status"] == ["review"]
    f = query.parse_rules("10 lowest confidence parcels")
    assert f["sort"] == "score_asc" and f["limit"] == 10
    assert query.parse_rules("plot 112/3A")["plot"] == "112/3A"
    assert query.parse_rules("encroachments near Canara Bank within 300 m")["near_m"] == 300
    assert query.parse_rules("a")["status"] == [] and query.parse_rules("b")["conflict_types"] == []   # no shared state
