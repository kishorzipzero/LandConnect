"""Real-world Tamil Nadu area: REAL cadastral parcels from two government sources, plus simulated records.

Real (unchanged):
  tngis_cadastral.geojson          TNGIS town-survey parcels (survey number / subdivision), Tamil Nadu GIS portal
  ncscm_coastal_survey.geojson     NCSCM coastal-zone cadastral parcels of the same land, drawn independently
  osm_buildings / roads / landmarks, Microsoft AI building footprints
Both cadastral layers come from datameet's collection of state portals (CC0 1.0, attribute datameet and the
source) and are cut out of the state-wide PMTiles (data/_statewide/tamil-nadu, zoom 14, about ±0.6 m).
Because the two are independent government surveys of the same parcels, comparing them is a real test with no
simulated answer key: the pipeline reports how far they agree and raises a conflict where they do not.

Simulated on the real parcels (land and utility records are not open data):
  chitta_patta_register.csv        Chitta / Patta extract: Tamil pattadar names, patta no., extent in hectares
  gcc_property_tax.kml             Greater Chennai Corporation property tax (English names, assessment no.)
  dgps_control.csv, field_verification.gpx, department records (TANGEDCO, CMWSSB, gas, CMDA, TNREGINET),
  elevation models (DSM/DTM 2021 and 2026)

    python -m landconnect.tndata [ws south west north east "title"]
"""
import csv
import json
import random
import shutil
import sys

import geopandas as gpd
import numpy as np
import pyogrio
import shapely
from pyproj import Transformer
from shapely import affinity
from shapely.geometry import box, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from . import config, coreg, geo, statewide
from . import realdata as rd
from .synth import name_variant

MEN = [("Murugan", "முருகன்"), ("Senthil", "செந்தில்"), ("Karthik", "கார்த்திக்"), ("Suresh", "சுரேஷ்"),
       ("Ramesh", "ரமேஷ்"), ("Rajesh", "ராஜேஷ்"), ("Kumar", "குமார்"), ("Arun", "அருண்"), ("Vijay", "விஜய்"),
       ("Anand", "ஆனந்த்"), ("Prakash", "பிரகாஷ்"), ("Ganesh", "கணேஷ்"), ("Saravanan", "சரவணன்"),
       ("Selvam", "செல்வம்"), ("Balaji", "பாலாஜி"), ("Venkatesh", "வெங்கடேஷ்"), ("Mani", "மணி"), ("Raja", "ராஜா"),
       ("Krishnan", "கிருஷ்ணன்"), ("Sundar", "சுந்தர்"), ("Dinesh", "தினேஷ்"), ("Mohan", "மோகன்"), ("Ravi", "ரவி"),
       ("Siva", "சிவா")]
WOMEN = [("Lakshmi", "லட்சுமி"), ("Meena", "மீனா"), ("Priya", "பிரியா"), ("Kavitha", "கவிதா"), ("Divya", "திவ்யா"),
         ("Revathi", "ரேவதி"), ("Saranya", "சரண்யா"), ("Anitha", "அனிதா"), ("Geetha", "கீதா"), ("Malathi", "மாலதி"),
         ("Vasanthi", "வசந்தி"), ("Deepa", "தீபா"), ("Janaki", "ஜானகி"), ("Kalpana", "கல்பனா"), ("Padma", "பத்மா"),
         ("Uma", "உமா"), ("Radha", "ராதா"), ("Sumathi", "சுமதி"), ("Nirmala", "நிர்மலா"), ("Tamilselvi", "தமிழ்செல்வி")]
BBOX = (12.992, 80.262, 13.002, 80.272)           # south, west, north, east: Thiruvanmiyur, Chennai (coast)


def _person(rng):
    """Tamil naming: father's initial + given name. English "S. Murugan", Tamil "ச. முருகன்"."""
    father = rng.choice(MEN)
    woman = rng.random() < 0.4
    given = rng.choice(WOMEN if woman else MEN)
    en = f"{father[0][0]}. {given[0]}"
    ta = f"{father[1][0]}. {given[1]}"
    title = ("திருமதி " if woman else "திரு. ") if rng.random() < 0.3 else ""
    return en, ta, title


def _pmtiles_parcels(name, bbox, key):
    """Parcels inside bbox from a state-wide PMTiles file at its deepest zoom, pieces re-joined by feature id."""
    s, w, n, e = bbox
    path = statewide.ROOT / "tamil-nadu" / name
    if not path.exists():
        raise SystemExit(f"{path} is missing: run  python -m landconnect.statewide fetch tamil-nadu")
    t = Transformer.from_crs(4326, 3857, always_xy=True)
    (x0, y0), (x1, y1) = t.transform(w - 0.002, s - 0.002), t.transform(e + 0.002, n + 0.002)
    zoom = statewide.header(path)["max_zoom"]
    df = pyogrio.read_dataframe(path, bbox=(x0, y0, x1, y1), ZOOM_LEVEL=str(zoom))
    df = df[[c for c in df.columns if c == "geometry" or df[c].notna().any()]]
    df = df.to_crs(4326)
    df["geometry"] = shapely.make_valid(df.geometry.values)
    out = df.dissolve(by=key, as_index=False, aggfunc="first")
    area = box(w, s, e, n)
    out = out[out.geometry.representative_point().within(area)]
    return out.reset_index(drop=True)


def build(ws="thiruvanmiyur", bbox=BBOX, title="Thiruvanmiyur, Chennai — real TN cadastrals", seed=31):
    rng, nrng = random.Random(seed), np.random.default_rng(seed)
    paths = config.ws_paths(ws)
    root, raw = paths["root"], paths["raw"]
    cache = root / "_cache"
    for d in (raw, root / "_truth"):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    s, w, n, e = bbox
    epsg = config.utm_epsg_for((w + e) / 2, (s + n) / 2)
    to_u = lambda g: geo.reproject(g, 4326, epsg)      # noqa: E731
    to_ll = lambda g: geo.reproject(g, epsg, 4326)     # noqa: E731

    # ---- real cadastral layers ---------------------------------------------------------------
    print("Cutting TNGIS and NCSCM parcels out of the state-wide maps ...", flush=True)
    tg = _pmtiles_parcels("TNGIS_TN_Cadastrals.pmtiles", bbox, "id")
    tg = tg[tg["survey_number"].notna()]
    nc = _pmtiles_parcels("NCSCM_TN_Cadastrals.pmtiles", bbox, "OBJECTID")
    keep_tg = [c for c in ("id", "survey_number", "sub_division", "village_code", "taluk_code", "district_code",
                           "lgd_village_code", "lgd_taluk_code", "lgd_district_code", "is_fmb") if c in tg.columns]
    tg_out = tg[keep_tg + ["geometry"]].rename(columns={"id": "tngis_id"})
    tg_out.to_file(raw / "tngis_cadastral.geojson", driver="GeoJSON", engine="pyogrio")
    keep_nc = [c for c in ("OBJECTID", "Survey_Number", "Village", "Taluk", "District") if c in nc.columns]
    nc[keep_nc + ["geometry"]].to_file(raw / "ncscm_coastal_survey.geojson", driver="GeoJSON", engine="pyogrio")

    # ---- real buildings, roads, landmarks --------------------------------------------------------
    print("Downloading OpenStreetMap ...", flush=True)
    osm = rd.fetch_osm(bbox, cache / "osm.json")
    b_osm, roads, pois = rd._osm_geoms(osm)
    area = box(w, s, e, n)
    b_osm = [(g, p) for g, p in b_osm if g.is_valid and area.contains(g.centroid)]
    geo.write_feature_collection(raw / "osm_buildings.geojson", b_osm, decimals=8)
    geo.write_feature_collection(raw / "osm_roads.geojson", [(g, p) for g, p in roads], decimals=8)
    geo.write_feature_collection(raw / "osm_landmarks.geojson", [(g, p) for g, p in pois if p.get("name") or p.get("kind")],
                                 decimals=8)
    ms = []
    try:
        print("Downloading Microsoft ML building footprints ...", flush=True)
        ms = [f for f in rd.fetch_ms_buildings(bbox, cache / "ms_buildings.json") if area.contains(shape(f["geometry"]).centroid)]
    except Exception as ex:                                   # optional second building layer
        print(f"  Microsoft footprints unavailable ({type(ex).__name__}); continuing with OSM only")
    with open(raw / "ms_ai_building_footprints.geojsonl", "w", encoding="utf-8") as f:
        for ft in ms:
            f.write(json.dumps({"type": "Feature", "geometry": ft["geometry"],
                                "properties": {"confidence": ft["properties"].get("confidence"),
                                               "height": ft["properties"].get("height")}}) + "\n")
    osm_u = [to_u(g) for g, _ in b_osm]
    ms_u = [to_u(geo.largest_polygon(shape(ft["geometry"]))) for ft in ms]
    mdx = mdy = 0.0
    if osm_u and ms_u:
        otree = STRtree(osm_u)
        pairs = [(min((osm_u[int(i)] for i in otree.query(g.centroid.buffer(6))), key=lambda o: o.centroid.distance(g.centroid)), g)
                 for g in ms_u if len(otree.query(g.centroid.buffer(6)))]
        if len(pairs) >= 10:
            mdx, mdy, _ = coreg.offset_from_pairs([a for a, _ in pairs], [b for _, b in pairs])
    bld_u = osm_u + [affinity.translate(g, mdx, mdy) for g in ms_u]
    btree = STRtree(bld_u) if bld_u else None

    # ---- where the land really is -----------------------------------------------------------------
    # Both real building layers (OSM, Microsoft) are traced from imagery. The shift that best puts them inside the
    # real TNGIS parcels measures how far the TNGIS map sits from the ground. The simulated DGPS survey and the
    # other simulated records are placed on the ground (imagery frame), as a real field survey would be; the real
    # TNGIS file itself is left exactly as published, so the pipeline has to find and correct the offset itself.
    fab = [geo.largest_polygon(to_u(g)) for g in tg.geometry]
    dx, dy, fit0, fit1 = coreg.offset_by_containment(osm_u or bld_u, [g for g in fab if not g.is_empty], search=10.0)
    print(f"TNGIS map vs imagery-traced buildings: offset {np.hypot(dx, dy):.2f} m (dx {dx:+.2f}, dy {dy:+.2f}); "
          f"buildings inside plots {fit0} -> {fit1}", flush=True)

    # ---- parcels: real TNGIS shapes, on the ground --------------------------------------------------------
    poi_u = [(to_u(g), pr) for g, pr in pois]
    parcels, seen = [], set()
    for _, r in tg.iterrows():
        sub = str(r.get("sub_division") or "").strip()
        pid = f"{int(r['survey_number']) if str(r['survey_number']).isdigit() else r['survey_number']}" + (f"/{sub}" if sub and sub != "None" else "")
        if pid in seen:
            continue
        seen.add(pid)
        g = affinity.translate(geo.largest_polygon(to_u(r.geometry)), -dx, -dy)
        if g.is_empty or g.area < 5:
            continue
        built = btree is not None and any(bld_u[int(i)].intersection(g).area > 0.4 * bld_u[int(i)].area for i in btree.query(g))
        use = "residential" if built else "vacant"
        c = g.representative_point()
        for pg, pr in poi_u:
            if pg.geom_type == "Polygon" and pg.contains(c) or pg.distance(c) < 3:
                kind = (pr.get("kind") or "").lower()
                if kind in ("commercial", "retail", "marketplace", "bank", "restaurant", "shop", "supermarket"):
                    use = "commercial"
                elif kind in ("school", "college", "hospital", "place_of_worship", "clinic", "community_centre", "library"):
                    use = "institutional"
        en, ta, tt = _person(rng)
        pen, pta, ptt = _person(rng)
        parcels.append({"plot_id": pid, "survey": str(r["survey_number"]), "hissa": sub or "1", "geom": g, "use": use,
                        "owner": en, "owner_ta": tt + ta, "prev_owner": pen, "prev_owner_ta": ptt + pta,
                        "valid": bool(r.geometry.is_valid)})
    by_id = {p["plot_id"]: p for p in parcels}
    all_ids = [p["plot_id"] for p in parcels]

    # ---- Chitta / Patta (Tamil) -----------------------------------------------------------------
    ror_missing = rng.sample(all_ids, 3)
    ror_ids = [i for i in all_ids if i not in ror_missing]
    stale = rng.sample(ror_ids, max(1, round(0.06 * len(ror_ids))))
    area_mm = rng.sample([i for i in ror_ids if i not in stale], 5)
    rows = []
    for pid in ror_ids:
        p = by_id[pid]
        err = rng.uniform(0.15, 0.25) * rng.choice([-1, 1]) if pid in area_mm else nrng.normal(0, 0.015)
        rows.append({"Survey No": p["survey"], "Subdivision No": p["hissa"], "Patta No": f"{rng.randint(100, 4999)}",
                     "Pattadar Name": p["prev_owner_ta"] if pid in stale else p["owner_ta"],
                     "Extent (Hectare)": round(p["geom"].area * (1 + err) / 1e4, 4),
                     "Classification": {"vacant": "Vacant", "commercial": "Commercial", "institutional": "Institutional"}
                     .get(p["use"], "Residential")})
    surveys = sorted({int(p["survey"]) for p in parcels if p["survey"].isdigit()})
    orphans = [f"{surveys[-1] + 3}/1", f"{surveys[-1] + 7}/2"] if surveys else ["999/1", "999/2"]
    for o in orphans:
        sv, hs = o.split("/")
        _, ta, tt = _person(rng)
        rows.append({"Survey No": sv, "Subdivision No": hs, "Patta No": f"{rng.randint(100, 4999)}", "Pattadar Name": tt + ta,
                     "Extent (Hectare)": round(rng.uniform(0.02, 0.06), 4), "Classification": "Residential"})
    rng.shuffle(rows)
    with open(raw / "chitta_patta_register.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)

    # ---- Greater Chennai Corporation property tax (KML) ---------------------------------------
    muni_missing = set(rng.sample(all_ids, round(0.08 * len(all_ids))))
    muni_stale = set(rng.sample([i for i in all_ids if i not in stale], 3))
    merges = []
    for p in rng.sample(parcels, min(80, len(parcels))):
        nb = [q for q in parcels if q["survey"] == p["survey"] and q["plot_id"] != p["plot_id"]
              and q["geom"].intersection(p["geom"].buffer(0.5)).area > 1]
        used = {x for m in merges for x in m}
        if nb and len(merges) < 3 and not ({p["plot_id"], nb[0]["plot_id"]} & (muni_missing | used)):
            merges.append((p["plot_id"], nb[0]["plot_id"]))
    second = {b for _, b in merges}
    muni_rows, muni_map, k = [], {}, 0
    for p in parcels:
        pid = p["plot_id"]
        if pid in muni_missing or pid in second:
            continue
        k += 1
        g, members = p["geom"], [pid]
        for a, b in merges:
            if a == pid:
                g = geo.largest_polygon(unary_union([g, by_id[b]["geom"].buffer(0.05)]))
                members.append(b)
        g = geo.largest_polygon(shapely.make_valid(rd._jitter(g.simplify(0.3), nrng, 0.5, (-0.9, 0.8))))
        spid = f"GCC-13-179-{k:05d}"
        muni_map[spid] = members
        owner = p["prev_owner"] if pid in muni_stale else p["owner"]
        muni_rows.append({"Name": spid, "Assessment_No": spid, "TS_No": f"{p['survey']}/{p['hissa']}",
                          "Owner": name_variant(rng, owner), "Site_Area_sqft": round(g.area / 0.092903),
                          "Usage": p["use"].title(), "Zone_Ward": "Zone 13 Adyar / Ward 179", "geometry": to_ll(g)})
    gpd.GeoDataFrame(muni_rows, geometry="geometry", crs="EPSG:4326").to_file(
        raw / "gcc_property_tax.kml", driver="KML", engine="pyogrio")

    # ---- DGPS control and field visits -----------------------------------------------------------
    gnss_parcels = rng.sample(parcels, round(0.15 * len(parcels)))
    with open(raw / "dgps_control.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["Point", "Survey Ref", "Latitude", "Longitude", "Ellipsoidal_Ht", "Fix", "HRMS_m"])
        k = 0
        for p in gnss_parcels:
            for x, y in geo.ring_points(p["geom"].simplify(0.2)):
                k += 1
                lon, lat = geo.transformer(epsg, 4326).transform(x + nrng.normal(0, 0.02), y + nrng.normal(0, 0.02))
                wr.writerow([f"DGPS-{k:04d}", f"T.S.No. {p['survey']}/{p['hissa']}", f"{lat:.8f}", f"{lon:.8f}",
                             round(rng.uniform(-80, -75), 3), "RTK Fixed", 0.02])
    gt_ids = rng.sample(stale, max(1, 2 * len(stale) // 3))
    gt_ids += rng.sample([i for i in all_ids if i not in gt_ids], max(0, round(0.08 * len(all_ids)) - len(gt_ids)))
    gt_rows = [{"name": f"{by_id[i]['survey']}/{by_id[i]['hissa']}", "cmt": by_id[i]["owner"],
                "desc": by_id[i]["use"].title(), "geometry": to_ll(by_id[i]["geom"].representative_point())} for i in gt_ids]
    gpd.GeoDataFrame(gt_rows, geometry="geometry", crs="EPSG:4326").to_file(
        raw / "field_verification.gpx", driver="GPX", engine="pyogrio", layer="waypoints")

    # ---- truth + workspace metadata ------------------------------------------------------------
    pid_count = {}
    for _, r in tg.iterrows():
        sub = str(r.get("sub_division") or "").strip()
        pid = f"{r['survey_number']}" + (f"/{sub}" if sub and sub != "None" else "")
        pid_count[pid] = pid_count.get(pid, 0) + 1
    truth = {
        "epsg": epsg,
        "parcels": [{"plot_id": p["plot_id"], "wkt": p["geom"].wkt, "owner": p["owner"], "prev_owner": p["prev_owner"],
                     "use": p["use"]} for p in parcels],
        "municipal_map": muni_map, "municipal_merges": merges, "municipal_missing": sorted(muni_missing),
        "municipal_stale_owner": sorted(muni_stale), "ror_missing": ror_missing, "ror_orphans": orphans,
        "ror_stale_owner": stale, "area_mismatch": area_mm, "cadastral_missing": [],
        "cadastral_bowties": [p["plot_id"] for p in parcels if not p["valid"]],
        "cadastral_duplicate": next((pid for pid, c in pid_count.items() if c > 1), None),
        "encroachments": [], "encroachments_planted": [], "deed_office": "ADY-1", "permit_prefix": "CMDA/PP",
        "cadastral_registration_offset_m": [round(-dx, 2), round(-dy, 2)], "ai_vs_osm_offset_m": [round(mdx, 2), round(mdy, 2)],
        "gnss_parcels": [p["plot_id"] for p in gnss_parcels], "gt_parcels": gt_ids,
        # the cadastral and building layers are real: there is no answer key for their own errors
        "unscored": ["Encroaching building", "Self-intersecting polygon", "Duplicate parcel"],
        "schema": {
            "tngis_cadastral.geojson": {"plot_id": "survey_number", "plot_sub": "sub_division"},
            "chitta_patta_register.csv": {"plot_id": "Survey No", "plot_sub": "Subdivision No", "owner_name": "Pattadar Name",
                                          "area": "Extent (Hectare)", "land_use": "Classification", "record_id": "Patta No"},
            "gcc_property_tax.kml": {"plot_id": "TS_No", "owner_name": "Owner", "area": "Site_Area_sqft",
                                     "land_use": "Usage", "record_id": "Assessment_No"},
            "dgps_control.csv": {"plot_id": "Survey Ref", "record_id": "Point"},
            "field_verification.gpx": {"plot_id": "name", "owner_name": "cmt", "land_use": "desc"},
        },
    }
    geo.write_json(root / "_truth" / "truth.json", truth)
    geo.write_json(paths["meta"], {
        "title": title, "kind": "real-world", "bbox": list(bbox), "state_code": "TN",
        "description": "Real TNGIS and NCSCM cadastral parcels (two independent government surveys of the same land), "
                       "real OpenStreetMap and Microsoft AI buildings and roads. Chitta/Patta, corporation tax, utility "
                       "and other records are simulated on the real parcels because they are not open data.",
        "real_notes": "The two cadastral layers are compared with each other: their agreement is a real measurement with no simulated "
                      f"answer key. The TNGIS map sits about {np.hypot(dx, dy):.1f} m from the imagery-traced buildings; the "
                      "simulated DGPS survey is placed on the ground, so the pipeline has to find and correct that offset.",
        "simulated_notes": [
            "Chitta / Patta (Tamil pattadar names), Greater Chennai Corporation tax, TANGEDCO, CMWSSB, gas, CMDA permissions, "
            "TNREGINET deeds, DGPS and field visits: simulated on the real parcels.",
            "Elevation (DSM, DTM) and the 2021 survey: simulated on the real footprints.",
            "Encroachment and cadastral-error scores are not reported here: the parcels are real, so there is no answer key for them."],
        "sources": [
            {"name": "TNGIS Tamil Nadu cadastrals, via datameet", "license": "CC0 1.0 (attribute datameet and TNGIS)",
             "url": "https://github.com/ramSeraph/indian_cadastrals/releases/tag/tamil-nadu", "files": ["tngis_cadastral.geojson"]},
            {"name": "NCSCM Tamil Nadu coastal cadastrals, via datameet", "license": "CC0 1.0 (attribute datameet and NCSCM)",
             "url": "https://czmp.ncscm.res.in/", "files": ["ncscm_coastal_survey.geojson"]},
            {"name": "OpenStreetMap contributors", "license": "ODbL 1.0", "url": "https://www.openstreetmap.org/copyright",
             "files": ["osm_buildings.geojson", "osm_roads.geojson", "osm_landmarks.geojson"]},
            {"name": "Microsoft Global ML Building Footprints", "license": "ODbL 1.0",
             "url": "https://github.com/microsoft/GlobalMLBuildingFootprints", "files": ["ms_ai_building_footprints.geojsonl"]},
            {"name": "Simulated records (this project)", "license": "synthetic",
             "files": ["chitta_patta_register.csv", "gcc_property_tax.kml", "dgps_control.csv", "field_verification.gpx",
                       "tangedco_consumers.csv", "cmwssb_water_connections.csv", "torrent_gas_connections.csv",
                       "cmda_building_permissions.csv", "tnreginet_registered_deeds.csv", "sewer_network.geojson"]},
            {"name": "Simulated elevation models and 2021 survey (this project)", "license": "synthetic",
             "files": ["elevation/dtm.tif", "elevation/dsm_2021.tif", "elevation/dsm_2026.tif"]}],
        "counts": {"tngis_parcels": len(tg), "ncscm_parcels": len(nc), "osm_buildings": len(b_osm), "ms_buildings": len(ms),
                   "roads": len(roads), "plots": len(parcels)},
    }, indent=2)
    from . import deptdata, elevation
    elevation.simulate(root, seed=seed + 14)
    deptdata.add(root, seed=seed + 1)
    for a, b in (("bescom_consumers.csv", "tangedco_consumers.csv"), ("bwssb_water_connections.csv", "cmwssb_water_connections.csv"),
                 ("png_gas_connections.csv", "torrent_gas_connections.csv"),
                 ("building_permissions.csv", "cmda_building_permissions.csv"),
                 ("igr_registered_deeds.csv", "tnreginet_registered_deeds.csv")):
        if (raw / a).exists():
            (raw / a).replace(raw / b)
    print(f"{title}: {len(parcels)} real TNGIS parcels, {len(nc)} real NCSCM parcels, {len(b_osm)} OSM buildings, "
          f"{len(ms)} Microsoft footprints, {len(roads)} roads")
    return truth


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) >= 6:
        ws, s, w, n, e = sys.argv[1], *map(float, sys.argv[2:6])
        build(ws, (s, w, n, e), sys.argv[6] if len(sys.argv) > 6 else ws)
    else:
        build()
