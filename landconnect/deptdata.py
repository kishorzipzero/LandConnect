"""Simulated records of the other government bodies, written in the formats they typically export:

  bescom_consumers.csv         electricity DISCOM consumer ledger (RR no., tariff, sanctioned load, GPS)
  bwssb_water_connections.csv  water board connections (consumer no., Domestic / Non-Domestic, GPS)
  png_gas_connections.csv      city-gas customers (BP no., GPS)
  sewer_network.geojson        underground drainage lines (two legacy lines run under buildings)
  building_permissions.csv     town-planning approvals (permitted floors, built-up area)
  igr_registered_deeds.csv     registered sale deeds (document no., date, buyer, seller)

Planted cross-department problems (saved in the hidden truth): unregistered mutations, commercial use
on residential tax assessments, live services on untaxed properties, floors beyond the permission.
About 5 % of plot references are wrong on purpose, so linking must also use GPS.

    python -m landconnect.deptdata <workspace>        # add to an existing workspace
"""
import csv
import json
import random
import sys
from pathlib import Path

import numpy as np
from shapely import affinity, wkt
from shapely.geometry import LineString

from . import config, geo
from .synth import FIRST, LAST, name_variant

def _name(rng):
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def _ref(p, rng, wrong_pool):
    """Survey reference as a department clerk would type it; ~5 % point at the wrong hissa."""
    pid = p["plot_id"]
    if rng.random() < 0.05 and wrong_pool:
        pid = rng.choice(wrong_pool)
    s, h = pid.split("/")
    return rng.choice([f"Sy No {s}/{h}", f"{s}/{h}", f"SY.{s} H.{h}", f"{s}-{h}"])


def add(root, seed=11):
    root = Path(root)
    raw, tpath = root / "raw", root / "_truth" / "truth.json"
    truth = geo.read_json(tpath)
    rng, nrng = random.Random(seed), np.random.default_rng(seed)
    epsg = truth.get("epsg", 32643)
    to_ll = geo.transformer(epsg, 4326)
    parcels = [dict(t, geom=wkt.loads(t["wkt"])) for t in truth["parcels"]]
    by_id = {p["plot_id"]: p for p in parcels}
    cad_missing = set(truth.get("cadastral_missing", []))
    muni_missing = set(truth.get("municipal_missing", []))
    merged = {x for m in truth.get("municipal_merges", []) for x in m}
    new_built = {c["plot_id"] for c in truth.get("changes", []) if c["type"] == "new"}
    if truth.get("buildings_t2"):
        built = {b["plot_id"] for b in truth["buildings_t2"]}
        height = {b["plot_id"]: b["height"] for b in truth["buildings_t2"]}
    elif truth.get("building_heights"):                    # real footprints with (simulated) measured heights
        built, height = set(), {}
        for b in truth["building_heights"]:
            built.add(b["plot_id"])
            height[b["plot_id"]] = max(height.get(b["plot_id"], 0), b["height"])
    else:
        built = {p["plot_id"] for p in parcels if p["use"] != "vacant"}
        height = {}
    links, ids = {}, [p["plot_id"] for p in parcels]

    def point(p):
        c = p["geom"].representative_point()
        lon, lat = to_ll.transform(c.x + nrng.normal(0, 1.2), c.y + nrng.normal(0, 1.2))
        return f"{lat:.8f}", f"{lon:.8f}"

    def neighbours(p):
        s = p["plot_id"].split("/")[0]
        return [i for i in ids if i.split("/")[0] == s and i != p["plot_id"]]

    # --- planted: commercial activity in residential-assessed premises (shop on the ground floor)
    res_assessed = [p["plot_id"] for p in parcels if p["use"] == "residential" and p["plot_id"] in built
                    and p["plot_id"] not in muni_missing | merged | cad_missing]
    tax_use = rng.sample(res_assessed, min(5, len(res_assessed)))

    # --- electricity DISCOM ledger
    rows, n = [], 0
    for p in parcels:
        if p["plot_id"] not in built:
            continue
        for k in range(1 + (rng.random() < 0.25)):
            n += 1
            rr = f"RR{rng.randint(10, 99)}{n:05d}"
            if p["plot_id"] in tax_use:
                tariff = "LT-3 Commercial"
            elif p["use"] == "commercial":
                tariff = "LT-3 Commercial"
            elif p["use"] == "institutional":
                tariff = "LT-6 Public"
            else:
                tariff = "LT-2a Domestic"
            holder = name_variant(rng, p["owner"]) if (k == 0 and rng.random() < 0.75) else _name(rng)
            lat, lon = point(p)
            rows.append({"RR_No": rr, "Consumer_Name": holder, "Tariff": tariff,
                         "Sanctioned_Load_kW": round(rng.uniform(2, 8) if tariff.startswith("LT-2") else rng.uniform(5, 30), 1),
                         "Survey_Ref": _ref(p, rng, neighbours(p)) if rng.random() < 0.6 else "",
                         "Status": "Disconnected" if rng.random() < 0.04 else "Live", "Latitude": lat, "Longitude": lon})
            links[rr] = p["plot_id"]
    _csv(raw / "bescom_consumers.csv", rows)

    # --- water board connections
    rows = []
    for i, p in enumerate(p for p in parcels if p["plot_id"] in built):
        if rng.random() > 0.9:
            continue
        cno = f"W{rng.randint(100, 999)}-{i + 1:05d}"
        ctype = "Non-Domestic" if (p["use"] == "commercial" or (p["plot_id"] in tax_use and rng.random() < 0.6)) else "Domestic"
        lat, lon = point(p)
        rows.append({"Consumer_No": cno, "Name": name_variant(rng, p["owner"]) if rng.random() < 0.8 else _name(rng),
                     "Connection_Type": ctype, "Meter_No": f"M{rng.randint(100000, 999999)}",
                     "Plot_Ref": _ref(p, rng, neighbours(p)) if rng.random() < 0.7 else "", "Status": "Active",
                     "Latitude": lat, "Longitude": lon})
        links[cno] = p["plot_id"]
    _csv(raw / "bwssb_water_connections.csv", rows)

    # --- city gas
    rows = []
    for i, p in enumerate(p for p in parcels if p["plot_id"] in built and p["use"] == "residential"):
        if rng.random() > 0.35:
            continue
        bp = f"BP{rng.randint(1000000, 9999999)}"
        lat, lon = point(p)
        rows.append({"BP_No": bp, "Customer_Name": name_variant(rng, p["owner"]), "Category": "Domestic",
                     "Latitude": lat, "Longitude": lon})
        links[bp] = p["plot_id"]
    _csv(raw / "png_gas_connections.csv", rows)

    # --- building permissions (town planning)
    rows, beyond = [], []
    floors_now = {pid: max(1, round((h - 0.5) / 3.2)) for pid, h in height.items()}
    new_floor = {c["plot_id"] for c in truth.get("changes", []) if c["type"] == "new_floor"}
    extra = rng.sample([pid for pid in floors_now if pid not in new_floor and floors_now[pid] >= 2], 2) if floors_now else []
    for p in parcels:
        pid = p["plot_id"]
        if pid not in built or rng.random() > 0.85 and pid not in new_floor | set(extra):
            continue
        if pid in floors_now:
            seen = floors_now[pid]
            allowed = seen - 1 if pid in new_floor | set(extra) else seen + (rng.random() < 0.3)
            if allowed < seen and pid not in cad_missing:
                beyond.append(pid)
        else:
            allowed = rng.choice([2, 3, 3, 4])
        rows.append({"Permission_No": f"{truth.get('permit_prefix', 'TP')}/{rng.randint(2008, 2023)}/{rng.randint(100, 9999)}", "Survey_Ref": _ref(p, rng, []),
                     "Applicant": name_variant(rng, p["owner"]), "Approved_Floors": allowed,
                     "Approved_BUA_sqm": round(p["geom"].area * rng.uniform(0.9, 1.8), 1),
                     "Approval_Date": f"{rng.randint(2008, 2023)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"})
    _csv(raw / "building_permissions.csv", rows)

    # --- registered deeds (IGR): every stale RoR has a newer registered sale that was never mutated
    rows = []
    stale_ror = [s for s in truth.get("ror_stale_owner", []) if s not in cad_missing]
    for pid in stale_ror + [s for s in truth.get("municipal_stale_owner", []) if s in by_id]:
        p = by_id[pid]
        rows.append({"Document_No": f"{truth.get('deed_office', 'JNR-1')}-{rng.randint(10000, 99999)}-{rng.choice([2025, 2026])}",
                     "Registration_Date": f"{rng.choice([2025, 2026])}-{rng.randint(1, 8):02d}-{rng.randint(1, 28):02d}",
                     "Survey_Ref": _ref(p, rng, []), "Buyer": p["owner"], "Seller": p["prev_owner"],
                     "Consideration_INR": rng.randint(40, 400) * 100000})
    for p in rng.sample(parcels, len(parcels) // 6):
        if p["plot_id"] in stale_ror:
            continue
        rows.append({"Document_No": f"{truth.get('deed_office', 'JNR-1')}-{rng.randint(10000, 99999)}-{rng.randint(2006, 2019)}",
                     "Registration_Date": f"{rng.randint(2006, 2019)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                     "Survey_Ref": _ref(p, rng, []), "Buyer": p["owner"], "Seller": _name(rng),
                     "Consideration_INR": rng.randint(20, 300) * 100000})
    rng.shuffle(rows)
    _csv(raw / "igr_registered_deeds.csv", rows)

    # --- sewer network: under the road centre lines + two legacy lines through built plots
    lines = []
    util = raw / "water_utility_network.geojson"
    roads = raw / "osm_roads.geojson"
    if util.exists():
        for g, pr in geo.feature_geoms(geo.read_json(util)):
            if pr.get("asset_type") == "water_main":
                g = geo.reproject(g, 3857, epsg)
                (x0, y0), (x1, y1) = g.coords[0], g.coords[-1]
                lines.append((affinity.translate(g, 2.5 if abs(x1 - x0) < 1 else 0, 2.5 if abs(y1 - y0) < 1 else 0), "sewer_main"))
    elif roads.exists():
        # a real sewer never runs through a building: where an OSM road line crosses a mapped footprint
        # (covered markets, mis-traced ways) the simulated main is interrupted there
        from shapely.ops import unary_union
        bpath = raw / "osm_buildings.geojson"
        blocks = [geo.reproject(g, 4326, epsg).buffer(1.0) for g, _ in geo.feature_geoms(geo.read_json(bpath))
                  if g is not None] if bpath.exists() else []
        mspath = raw / "ms_ai_building_footprints.geojsonl"
        if mspath.exists():                                   # AI footprints too, moved onto OSM by their known offset
            from shapely import affinity as _aff
            from shapely.geometry import shape as _shape
            odx, ody = truth.get("ai_vs_osm_offset_m", [0.0, 0.0])
            with open(mspath, encoding="utf-8") as fh:
                for line in fh:
                    g = geo.largest_polygon(geo.reproject(_shape(json.loads(line)["geometry"]), 4326, epsg))
                    if not g.is_empty:
                        blocks.append(_aff.translate(g, odx, ody).buffer(1.0))
        blocked = unary_union(blocks) if blocks else None
        for g, pr in geo.feature_geoms(geo.read_json(roads)):
            if pr.get("highway") in ("residential", "tertiary", "secondary", "unclassified"):
                g = geo.reproject(g, 4326, epsg)
                g = g.difference(blocked) if blocked is not None else g
                for part in getattr(g, "geoms", [g]):
                    if part.geom_type == "LineString" and part.length > 5:
                        lines.append((part, "sewer_main"))
    legacy = []
    for k, pid in enumerate(rng.sample(sorted(built - cad_missing), 2)):
        c = by_id[pid]["geom"].centroid
        ang = rng.uniform(0, np.pi)
        ln = LineString([(c.x - 25 * np.cos(ang), c.y - 25 * np.sin(ang)), (c.x + 25 * np.cos(ang), c.y + 25 * np.sin(ang))])
        lines.append((ln, "legacy_sewer"))
        legacy.append(f"SW-L{k + 1}")
    feats, n_main = [], 0
    for g, kind in lines:
        if kind == "sewer_main":
            n_main += 1
            aid = f"SW-{n_main:03d}"
        else:
            aid = legacy[len([f for f in feats if f[1]["asset_type"] == "legacy_sewer"])]
        feats.append((geo.reproject(g, epsg, 4326), {"asset_id": aid, "asset_type": kind,
                                                     "diameter_mm": rng.choice([225, 300, 450]) if kind == "sewer_main" else 150}))
    geo.write_feature_collection(raw / "sewer_network.geojson", feats, decimals=8)

    truth.update({
        "epsg": epsg, "dept_links": links, "tax_use_mismatch": tax_use, "floors_beyond_permission": beyond,
        "mutation_pending": stale_ror, "legacy_sewers": legacy,
        "service_without_tax": sorted(pid for pid in built if (pid in muni_missing or pid in new_built)
                                      and pid not in cad_missing and pid not in merged)})
    truth["legacy_pipes"] = sorted(set(truth.get("legacy_pipes", [])) | set(legacy))
    geo.write_json(tpath, truth)
    return truth


def _csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    ws = sys.argv[1] if len(sys.argv) > 1 else config.DEFAULT_WS
    t = add(config.ws_paths(ws)["root"])
    print(f"{ws}: department data written. Planted: {len(t['mutation_pending'])} unregistered mutations, "
          f"{len(t['tax_use_mismatch'])} commercial-on-residential, {len(t['service_without_tax'])} services without tax, "
          f"{len(t['floors_beyond_permission'])} floors beyond permission, {len(t['legacy_sewers'])} legacy sewers")
