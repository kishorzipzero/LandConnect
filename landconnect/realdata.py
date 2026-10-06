"""Real-world workspace builder.

Downloads REAL open geospatial data for any bounding box in India and builds a test workspace:

  real   osm_buildings.geojson               OpenStreetMap building footprints (ODbL)
  real   ms_ai_building_footprints.geojsonl  Microsoft Global ML Building Footprints - AI-extracted from
                                             satellite imagery (ODbL)
  real   osm_roads.geojson, osm_landmarks.geojson   road network, amenities and land use (ODbL)

Open cadastral maps and land records do not exist for Indian cities (Bhuvan's SISDP cadastral layers
are view-only, WFS disabled), so the land-record layers are SIMULATED on top of the real geography:
plots are drawn around the real buildings inside the real city blocks, and each department file is
written in the format and CRS a real department would use:

  sim    cadastral_sheet.shp     Shapefile in Kalianpur 1975 / India zone IVa (EPSG:24383) with a
                                 sheet-registration error, digitising noise, a bow-tie, a duplicate
  sim    rtc_bhoomi_extract.csv  Bhoomi-style RTC: Survey No + Hissa No, owner names in KANNADA script,
                                 extent in GUNTAS, stale owners, missing and orphan rows
  sim    bbmp_property_tax.kml   municipal layer (WGS 84) with English owner names, sq ft, merged sites
  sim    gnss_dgps_control.csv   DGPS/CORS corner fixes (lat/lon)
  sim    field_verification.gpx  ground-truth visits as GPS waypoints (owner in the comment field)

The hidden truth for the simulated layers is saved in _truth/ (buildings have no truth: the two real
building layers are compared with each other instead).

    python -m landconnect.realdata jayanagar 12.9270 77.5800 12.9320 77.5870 "Jayanagar, Bengaluru"
"""
import csv
import gzip
import io
import json
import math
import random
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyogrio
import shapely
from indic_transliteration import sanscript
from shapely import affinity
from shapely.geometry import LineString, MultiPoint, Point, Polygon, box, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from . import config, coreg, geo
from .synth import name_variant

UA = "LandConnect-SIH2026-prototype/0.2 (research demo)"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
MS_LINKS = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
ROAD_WIDTH = {"motorway": 16, "trunk": 14, "primary": 12, "secondary": 10, "tertiary": 8, "residential": 6,
              "unclassified": 6, "living_street": 5, "service": 3.5}
LEGACY_EPSG = 24383   # Kalianpur 1975 / India zone IVa (southern India legacy survey maps)

# (English spelling used by the municipality, ITRANS spelling -> Kannada script in the RTC)
FIRST = [("Ramesh", "ramesh"), ("Suresh", "suresh"), ("Lakshmi", "lakShmI"), ("Priya", "priyA"),
         ("Anil", "anil"), ("Sunitha", "sunItA"), ("Mahesh", "mahesh"), ("Kavitha", "kavitA"),
         ("Venkatesh", "ve~NkaTesh"), ("Shobha", "shobhA"), ("Rajesh", "rAjesh"), ("Geetha", "gItA"),
         ("Manjunath", "ma~njunAtha"), ("Deepa", "dIpA"), ("Srinivas", "shrInivAs"), ("Anitha", "anitA"),
         ("Prakash", "prakAsh"), ("Rekha", "rekhA"), ("Naveen", "navIn"), ("Pooja", "pUjA"),
         ("Harish", "harIsh"), ("Nagaraj", "nAgarAj"), ("Divya", "divyA"), ("Chandrashekar", "cha.ndrashekhar"),
         ("Shanthala", "shA.ntalA"), ("Basavaraj", "basavarAj"), ("Nandini", "na.ndinI"), ("Girish", "girIsh"),
         ("Vani", "vANI"), ("Keshava", "keshava"), ("Mohan", "mohan"), ("Asha", "AshA")]
LAST = [("Gowda", "gauDa"), ("Rao", "rAv"), ("Hegde", "hegDe"), ("Shetty", "sheTTi"), ("Bhat", "bhaT"),
        ("Murthy", "mUrti"), ("Prasad", "prasAd"), ("Kulkarni", "kulakarNi"), ("Swamy", "svAmi"),
        ("Reddy", "reDDi"), ("Nayak", "nAyak"), ("Patil", "pATIl"), ("Kumar", "kumAr"), ("Joshi", "joshi")]


# ----------------------------------------------------------------------------- downloads
def _get(url, data=None, timeout=180):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_osm(bbox, cache):
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    s, w, n, e = bbox
    b = f"{s},{w},{n},{e}"
    q = (f"[out:json][timeout:90];(way[\"building\"]({b});relation[\"building\"]({b});way[\"highway\"]({b});"
         f"way[\"landuse\"]({b});way[\"leisure\"]({b});way[\"amenity\"]({b});node[\"amenity\"][\"name\"]({b});"
         f"node[\"shop\"][\"name\"]({b});node[\"leisure\"][\"name\"]({b});node[\"tourism\"][\"name\"]({b}););out geom;")
    last = None
    for ep in OVERPASS:
        try:
            data = json.loads(_get(ep, urllib.parse.urlencode({"data": q}).encode()))
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
            return data
        except Exception as ex:          # try the mirror
            last = ex
    raise RuntimeError(f"Overpass download failed: {last}")


def _quadkey(lat, lon, z=9):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return "".join(str((1 if x >> i & 1 else 0) + (2 if y >> i & 1 else 0)) for i in range(z - 1, -1, -1))


def fetch_ms_buildings(bbox, cache):
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    s, w, n, e = bbox
    qk = _quadkey((s + n) / 2, (w + e) / 2)
    links = _get(MS_LINKS).decode("utf-8").splitlines()
    urls = [r.split(",")[2] for r in links[1:] if r.split(",")[1] == qk and r.startswith("India,")]
    if not urls:
        raise RuntimeError(f"No Microsoft building tile for quadkey {qk}")
    area = box(w, s, e, n)
    feats = []
    for u in urls:
        raw = gzip.decompress(_get(u, timeout=900))
        for line in io.StringIO(raw.decode("utf-8")):
            if not line.strip():
                continue
            f = json.loads(line)
            g = shape(f["geometry"])
            if g.intersects(area):
                feats.append(f)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(feats), encoding="utf-8")
    return feats


# ----------------------------------------------------------------------------- OSM parsing
def _osm_geoms(osm):
    buildings, roads, pois = [], [], []
    for el in osm["elements"]:
        t = el.get("tags", {})
        if el["type"] == "node":
            pois.append((Point(el["lon"], el["lat"]), {"name": t.get("name"), "kind": t.get("amenity") or
                         t.get("shop") or t.get("leisure") or t.get("tourism"), "osm_id": f"node/{el['id']}"}))
            continue
        if el["type"] == "way" and "geometry" in el:
            pts = [(p["lon"], p["lat"]) for p in el["geometry"]]
            closed = len(pts) >= 4 and pts[0] == pts[-1]
            if "building" in t and closed:
                buildings.append((Polygon(pts), {"osm_id": f"way/{el['id']}", "building": t.get("building"),
                                                 "building:levels": t.get("building:levels"), "name": t.get("name")}))
            elif "highway" in t and not closed and t["highway"] in ROAD_WIDTH:
                roads.append((LineString(pts), {"osm_id": f"way/{el['id']}", "highway": t["highway"],
                                                "name": t.get("name")}))
            elif closed and any(k in t for k in ("landuse", "leisure", "amenity")):
                pois.append((Polygon(pts), {"name": t.get("name"), "kind": t.get("amenity") or t.get("leisure")
                                            or t.get("landuse"), "osm_id": f"way/{el['id']}"}))
        elif el["type"] == "relation" and "building" in t:
            outers = [Polygon([(p["lon"], p["lat"]) for p in m["geometry"]]) for m in el.get("members", [])
                      if m.get("role") == "outer" and m.get("geometry") and len(m["geometry"]) >= 4
                      and m["geometry"][0] == m["geometry"][-1]]
            if outers:
                buildings.append((geo.largest_polygon(unary_union(outers)),
                                  {"osm_id": f"relation/{el['id']}", "building": t.get("building")}))
    return buildings, roads, pois


# ----------------------------------------------------------------------------- simulated plots
def _plots(bbox_u, roads_u, anchors, rng):
    """City blocks from the real road network, cut into plots around the real buildings."""
    road_poly = unary_union([g.buffer(ROAD_WIDTH[h] / 2, cap_style="flat") for g, h in roads_u])
    # modelled carriageway widths must not cut through real buildings
    road_poly = road_poly.difference(unary_union(anchors).buffer(0.5))
    blocks = bbox_u.difference(road_poly)
    blocks = [b for b in getattr(blocks, "geoms", [blocks]) if b.geom_type == "Polygon" and b.area > 150]
    atree = STRtree([a for a in anchors])
    plots = []
    for bi, blk in enumerate(sorted(blocks, key=lambda b: (-round(b.centroid.y, -1), b.centroid.x))):
        inside = [int(i) for i in atree.query(blk) if blk.contains(anchors[int(i)].centroid)]
        if not inside:
            plots.append({"block": bi, "geom": blk, "anchor": None})
            continue
        if len(inside) == 1:
            plots.append({"block": bi, "geom": blk, "anchor": inside[0]})
            continue
        pts, owner = [], []
        for k in inside:
            ring = anchors[k].exterior
            for d in np.arange(0, ring.length, 1.0):
                p = ring.interpolate(d)
                pts.append(p)
                owner.append(k)
        cells = shapely.voronoi_polygons(MultiPoint(pts), extend_to=blk.buffer(5), ordered=True)
        by = {}
        for cell, k in zip(cells.geoms, owner):
            by.setdefault(k, []).append(cell)
        for k, cs in by.items():
            g = unary_union(cs).intersection(blk)
            g = geo.largest_polygon(unary_union([g, anchors[k].buffer(0.3).intersection(blk.buffer(2))]))
            plots.append({"block": bi, "geom": g, "anchor": k})
    # resolve overlaps from the building buffers, simplify like a surveyed boundary
    out, done = [], []
    for p in plots:
        g = p["geom"]
        for q in done:
            if g.intersects(q):
                g = g.difference(q)
        g = geo.largest_polygon(shapely.make_valid(g.simplify(0.4)))
        if g.is_empty or g.area < 25:
            continue
        p["geom"] = g
        done.append(g)
        out.append(p)
    return out, blocks


def _names(rng, n):
    return [(f"{f[0]} {l[0]}", f"{f[1]} {l[1]}") for f, l in
            ((rng.choice(FIRST), rng.choice(LAST)) for _ in range(n))]


def _kannada(itrans, rng):
    k = sanscript.transliterate(itrans, sanscript.ITRANS, sanscript.KANNADA)
    return ("ಶ್ರೀ " + k) if rng.random() < 0.3 else k


def _jitter(poly, nrng, sigma, shift=(0.0, 0.0)):
    pts = geo.ring_points(poly)
    shell = pts + nrng.normal(0, sigma, pts.shape) + np.asarray(shift)
    holes = [np.asarray(r.coords)[:-1] + np.asarray(shift) for r in poly.interiors]   # keep courtyards / enclosed plots
    return Polygon(shell, holes)


def build(ws, bbox, title, seed=7):
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

    # ---- real layers -------------------------------------------------------------
    print("Downloading OpenStreetMap ...", flush=True)
    osm = fetch_osm(bbox, cache / "osm.json")
    b_osm, roads, pois = _osm_geoms(osm)
    print("Downloading Microsoft ML building footprints ...", flush=True)
    ms = fetch_ms_buildings(bbox, cache / "ms_buildings.json")
    area = box(w, s, e, n)
    b_osm = [(g, p) for g, p in b_osm if g.is_valid and area.contains(g.centroid)]
    ms = [f for f in ms if area.contains(shape(f["geometry"]).centroid)]

    geo.write_feature_collection(raw / "osm_buildings.geojson", b_osm, decimals=8)
    with open(raw / "ms_ai_building_footprints.geojsonl", "w", encoding="utf-8") as f:
        for ft in ms:
            f.write(json.dumps({"type": "Feature", "geometry": ft["geometry"],
                                "properties": {"confidence": ft["properties"].get("confidence"),
                                               "height": ft["properties"].get("height")}}) + "\n")
    geo.write_feature_collection(raw / "osm_roads.geojson", [(g, p) for g, p in roads], decimals=8)
    geo.write_feature_collection(raw / "osm_landmarks.geojson", [(g, p) for g, p in pois if p.get("name") or
                                                                  p.get("kind")], decimals=8)

    # ---- simulated land records on the real geography -----------------------------
    bbox_u = to_u(area)
    roads_u = [(to_u(g), p["highway"]) for g, p in roads]
    # anchors = real buildings the simulated plots are drawn around. Overlapping OSM polygons are merged;
    # AI-only buildings are added after removing the AI layer's systematic offset against OSM.
    osm_u = [to_u(g) for g, _ in b_osm]
    anchors = [geo.largest_polygon(g) for g in getattr(unary_union(osm_u), "geoms", [unary_union(osm_u)])]
    ms_u = [to_u(geo.largest_polygon(shape(ft["geometry"]))) for ft in ms]
    otree = STRtree(osm_u)
    pairs = []
    for g in ms_u:
        near = [osm_u[int(i)] for i in otree.query(g.centroid.buffer(6))]
        if near:
            pairs.append((min(near, key=lambda o: o.centroid.distance(g.centroid)), g))
    mdx, mdy, _ = coreg.offset_from_pairs([a for a, _ in pairs], [b for _, b in pairs])
    atree = STRtree(anchors)
    for g in ms_u:
        g = affinity.translate(g, mdx, mdy)
        if not any(anchors[int(i)].intersects(g.buffer(0.3)) for i in atree.query(g.buffer(0.3))):
            anchors.append(g)
    anchors = [a for a in anchors if a.is_valid and a.area > 12]
    plots, blocks = _plots(bbox_u, roads_u, anchors, rng)

    # ids: survey number per block, hissa per plot (some sub-divided "3A", "3B")
    by_block = {}
    for p in plots:
        by_block.setdefault(p["block"], []).append(p)
    parcels = []
    poi_u = [(to_u(g), pr) for g, pr in pois]
    for bi, (blk, ps) in enumerate(sorted(by_block.items())):
        survey = 101 + bi
        ps.sort(key=lambda p: (round(-p["geom"].centroid.y / 15), p["geom"].centroid.x))
        lettered = rng.random() < 0.35 and len(ps) >= 4
        for k, p in enumerate(ps):
            hissa = f"{k // 2 + 1}{'AB'[k % 2]}" if lettered else str(k + 1)
            use = "vacant" if p["anchor"] is None else "residential"
            c = p["geom"].representative_point()
            for g, pr in poi_u:
                if g.geom_type == "Polygon" and g.contains(c):
                    kind = (pr.get("kind") or "").lower()
                    use = ("commercial" if kind in ("commercial", "retail", "marketplace", "bank", "restaurant")
                           else "institutional" if kind in ("school", "college", "hospital", "place_of_worship",
                                                             "clinic", "community_centre", "library") else use)
            parcels.append({"plot_id": f"{survey}/{hissa}", "survey": survey, "hissa": hissa, "geom": p["geom"],
                            "use": use, "anchor": p["anchor"]})
    names = _names(rng, 2 * len(parcels))
    for i, p in enumerate(parcels):
        p["owner"], p["owner_kn"] = names[2 * i]
        prev = names[2 * i + 1]
        if prev[0].split()[0] == p["owner"].split()[0]:
            prev = ("Keshava Joshi", "keshava joshi")
        p["prev_owner"], p["prev_owner_kn"] = prev
    by_id = {p["plot_id"]: p for p in parcels}

    # planted encroachments: legal plot drawn 2.5 m short of a real building's road-side wall
    enc = []
    road_lines = unary_union([g for g, _ in roads_u])
    for p in rng.sample([p for p in parcels if p["anchor"] is not None and p["use"] == "residential"], 40):
        a = anchors[p["anchor"]]
        rect = geo.ring_points(a.minimum_rotated_rectangle)
        edges = [(rect[i], rect[(i + 1) % 4]) for i in range(4)]
        e0 = min(edges, key=lambda ed: LineString(ed).distance(road_lines))   # road-facing wall
        mid = (e0[0] + e0[1]) / 2
        inward = np.asarray(a.centroid.coords[0]) - mid
        inward = inward / (np.linalg.norm(inward) or 1) * 2.5
        outward = -inward / 2.5 * 40.0
        strip = Polygon([e0[0] + outward, e0[1] + outward, e0[1] + inward, e0[0] + inward]).buffer(0.05)
        g = geo.largest_polygon(p["geom"].difference(strip))
        if len(g.interiors):
            continue
        if g.area > 0.85 * p["geom"].area and a.intersection(strip).area > 4:
            p["geom"] = g
            enc.append(p["plot_id"])
        if len(enc) == 3:
            break

    # ---- cadastral sheet: Shapefile in Kalianpur 1975 with sheet-registration error --------
    all_ids = [p["plot_id"] for p in parcels]
    cad_missing = rng.sample(all_ids, 2)
    bowties = rng.sample([i for i in all_ids if i not in cad_missing], 2)
    dup = rng.choice([i for i in all_ids if i not in cad_missing + bowties])
    cx, cy = bbox_u.centroid.x, bbox_u.centroid.y
    cad_rows, invalid = [], []
    for p in parcels:
        if p["plot_id"] in cad_missing:
            continue
        g = p["geom"].simplify(0.2)
        g = _jitter(g, nrng, 0.35)
        g = affinity.rotate(g, 0.25, origin=(cx, cy))
        g = affinity.translate(g, 3.1, -2.4)
        if p["plot_id"] in bowties and len(geo.ring_points(g)) >= 4:
            pts = geo.ring_points(g)
            pts[[1, 2]] = pts[[2, 1]]
            g = Polygon(pts)
        if not g.is_valid:
            invalid.append(p["plot_id"])
        row = {"SURVEY_NO": p["survey"], "HISSA_NO": p["hissa"], "EXTENT_GU": round(p["geom"].area / 101.17141, 2),
               "VILLAGE": "Byrasandra", "TALUK": "Bengaluru South", "geometry": geo.reproject(g, epsg, LEGACY_EPSG)}
        cad_rows.append(row)
        if p["plot_id"] == dup:
            cad_rows.append(dict(row, geometry=affinity.translate(row["geometry"], 0.4, -0.3)))
    gpd.GeoDataFrame(cad_rows, geometry="geometry", crs=f"EPSG:{LEGACY_EPSG}").to_file(
        raw / "cadastral_sheet.shp", engine="pyogrio", encoding="utf-8")

    # ---- RTC (Bhoomi-style revenue record): Kannada owner names, extent in guntas ---------
    ror_missing = rng.sample([i for i in all_ids if i not in cad_missing], 3)
    ror_ids = [i for i in all_ids if i not in ror_missing]
    stale = rng.sample(ror_ids, max(1, round(0.06 * len(ror_ids))))
    area_mm = rng.sample([i for i in ror_ids if i not in stale], 5)
    rows = []
    for pid in ror_ids:
        p = by_id[pid]
        kn = p["prev_owner_kn"] if pid in stale else p["owner_kn"]
        err = rng.uniform(0.15, 0.25) * rng.choice([-1, 1]) if pid in area_mm else nrng.normal(0, 0.015)
        rows.append({"Survey No": p["survey"], "Hissa No": p["hissa"], "Khatha No": f"{rng.randint(100, 9999)}",
                     "Owner Name": _kannada(kn, rng), "Extent (Gunta)": round(p["geom"].area * (1 + err) / 101.17141, 2),
                     "Land Type": {"vacant": "Vacant", "commercial": "Commercial", "institutional": "Institutional"}
                     .get(p["use"], "Residential")})
    last_survey = max(p["survey"] for p in parcels)
    orphans = [f"{last_survey + 4}/1", f"{last_survey + 9}/2"]
    for o in orphans:
        sv, hs = o.split("/")
        en, it = _names(rng, 1)[0]
        rows.append({"Survey No": int(sv), "Hissa No": hs, "Khatha No": f"{rng.randint(100, 9999)}",
                     "Owner Name": _kannada(it, rng), "Extent (Gunta)": round(rng.uniform(2, 6), 2),
                     "Land Type": "Residential"})
    rng.shuffle(rows)
    with open(raw / "rtc_bhoomi_extract.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)

    # ---- BBMP property tax (KML, WGS 84) ----------------------------------------------
    muni_missing = set(rng.sample(all_ids, round(0.08 * len(all_ids))))
    muni_stale = set(rng.sample([i for i in all_ids if i not in stale], 3))
    merges = []
    for p in rng.sample(parcels, min(60, len(parcels))):
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
        g = geo.largest_polygon(shapely.make_valid(_jitter(g.simplify(0.3), nrng, 0.5, (1.1, -0.7))))
        spid = f"BBMP-171-{k:05d}"
        muni_map[spid] = members
        owner = p["prev_owner"] if pid in muni_stale else p["owner"]
        muni_rows.append({"Name": spid, "PID": spid, "Site_No": f"{p['survey']}/{p['hissa']}",
                          "Owner": name_variant(rng, owner),
                          "Site_Area_sqft": round(g.area / 0.092903), "Usage": p["use"].title(),
                          "Ward": "171 Jayanagar", "geometry": to_ll(g)})
    gpd.GeoDataFrame(muni_rows, geometry="geometry", crs="EPSG:4326").to_file(
        raw / "bbmp_property_tax.kml", driver="KML", engine="pyogrio")

    # ---- DGPS / CORS control (CSV lat/lon) -----------------------------------------------
    gnss_parcels = rng.sample([p for p in parcels if p["plot_id"] not in cad_missing], round(0.15 * len(parcels)))
    with open(raw / "gnss_dgps_control.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["Point", "Survey Ref", "Latitude", "Longitude", "Ellipsoidal_Ht", "Fix", "HRMS_m"])
        np_ = 0
        for p in gnss_parcels:
            for x, y in geo.ring_points(p["geom"].simplify(0.2)):
                np_ += 1
                lon, lat = geo.transformer(epsg, 4326).transform(x + nrng.normal(0, 0.02), y + nrng.normal(0, 0.02))
                wr.writerow([f"DGPS-{np_:04d}", f"Sy.No. {p['survey']} Hissa {p['hissa']}", f"{lat:.8f}",
                             f"{lon:.8f}", round(rng.uniform(885, 900), 3), "RTK Fixed", 0.02])

    # ---- field verification (GPX waypoints) -------------------------------------------
    gt_ids = rng.sample(stale, max(1, 2 * len(stale) // 3))
    gt_ids += rng.sample([i for i in all_ids if i not in gt_ids], max(0, round(0.08 * len(all_ids)) - len(gt_ids)))
    gt_rows = [{"name": f"{by_id[i]['survey']}/{by_id[i]['hissa']}", "cmt": by_id[i]["owner"],
                "desc": by_id[i]["use"].title(), "geometry": to_ll(by_id[i]["geom"].representative_point())}
               for i in gt_ids]
    gpd.GeoDataFrame(gt_rows, geometry="geometry", crs="EPSG:4326").to_file(
        raw / "field_verification.gpx", driver="GPX", engine="pyogrio", layer="waypoints")

    # ---- truth + workspace metadata ---------------------------------------------------------
    truth = {
        "epsg": epsg,
        "parcels": [{"plot_id": p["plot_id"], "wkt": p["geom"].wkt, "owner": p["owner"],
                     "prev_owner": p["prev_owner"], "use": p["use"]} for p in parcels],
        "encroachments": enc, "municipal_map": muni_map, "municipal_merges": merges,
        "municipal_missing": sorted(muni_missing), "municipal_stale_owner": sorted(muni_stale),
        "ror_missing": ror_missing, "ror_orphans": orphans, "ror_stale_owner": stale, "area_mismatch": area_mm,
        "cadastral_missing": cad_missing, "cadastral_bowties": invalid, "cadastral_duplicate": dup,
        "gnss_parcels": [p["plot_id"] for p in gnss_parcels], "gt_parcels": gt_ids,
        "sheet_error": {"rotation_deg": 0.25, "shift_m": [3.1, -2.4], "legacy_epsg": LEGACY_EPSG},
        "schema": {
            "cadastral_sheet.shp": {"plot_id": "SURVEY_NO", "plot_sub": "HISSA_NO", "area": "EXTENT_GU"},
            "rtc_bhoomi_extract.csv": {"plot_id": "Survey No", "plot_sub": "Hissa No", "owner_name": "Owner Name",
                                       "area": "Extent (Gunta)", "land_use": "Land Type", "record_id": "Khatha No"},
            "bbmp_property_tax.kml": {"plot_id": "Site_No", "owner_name": "Owner", "area": "Site_Area_sqft",
                                      "land_use": "Usage", "record_id": "PID"},
            "gnss_dgps_control.csv": {"plot_id": "Survey Ref", "record_id": "Point"},
            "field_verification.gpx": {"plot_id": "name", "owner_name": "cmt", "land_use": "desc"},
        },
    }
    # the true encroachment list comes from the final geometry: a real building > 1 m beyond its plot
    true_enc = []
    for p in parcels:
        if p["anchor"] is not None and p["geom"].area > 0:
            a = anchors[p["anchor"]]
            if a.difference(p["geom"]).area > 4 and max(Point(xy).distance(p["geom"]) for xy in a.exterior.coords) > 1.0:
                true_enc.append(p["plot_id"])
    truth["encroachments"] = true_enc
    truth["encroachments_planted"] = enc
    truth["ai_vs_osm_offset_m"] = [round(mdx, 2), round(mdy, 2)]
    geo.write_json(root / "_truth" / "truth.json", truth)
    geo.write_json(paths["meta"], {
        "title": title, "kind": "real-world", "bbox": bbox,
        "description": "Real OpenStreetMap and Microsoft AI building footprints, real road network and landmarks. "
                       "Land records (cadastral sheet, RTC, BBMP tax, DGPS, field visits) are simulated on the "
                       "real geography because Indian cadastral data is not openly downloadable.",
        "sources": [
            {"name": "OpenStreetMap contributors", "license": "ODbL 1.0", "url": "https://www.openstreetmap.org/copyright",
             "files": ["osm_buildings.geojson", "osm_roads.geojson", "osm_landmarks.geojson"]},
            {"name": "Microsoft Global ML Building Footprints", "license": "ODbL 1.0",
             "url": "https://github.com/microsoft/GlobalMLBuildingFootprints", "files": ["ms_ai_building_footprints.geojsonl"]},
            {"name": "Simulated land records (this project)", "license": "synthetic",
             "files": ["cadastral_sheet.shp", "rtc_bhoomi_extract.csv", "bbmp_property_tax.kml",
                       "gnss_dgps_control.csv", "field_verification.gpx"]},
            {"name": "Simulated department records (this project)", "license": "synthetic",
             "files": ["bescom_consumers.csv", "bwssb_water_connections.csv", "png_gas_connections.csv",
                       "building_permissions.csv", "igr_registered_deeds.csv", "sewer_network.geojson"]},
            {"name": "Simulated elevation models and 2021 survey (this project)", "license": "synthetic",
             "files": ["elevation/dtm.tif", "elevation/dsm_2021.tif", "elevation/dsm_2026.tif"]}],
        "real_notes": "The satellite basemap is Esri World Imagery (real, shown for reference only, not processed).",
        "simulated_notes": [
            "Owners, deeds, tax, revenue, utility and permission records: simulated on the real plots, because Indian land and utility records are not openly downloadable.",
            "Plot boundaries: drawn around the real buildings and real roads, then given survey-style errors.",
            "Elevation (DSM, DTM) and the 2021 survey: simulated on the real footprints. Open DEMs are 10-30 m and cannot measure a building's height.",
        ],
        "counts": {"osm_buildings": len(b_osm), "ms_buildings": len(ms), "roads": len(roads), "plots": len(parcels)},
    }, indent=2)
    from . import deptdata, elevation
    elevation.simulate(root, seed=seed + 14)
    truth = deptdata.add(root, seed=seed + 1)
    print(f"{title}: {len(b_osm)} OSM buildings, {len(ms)} Microsoft AI footprints, {len(roads)} roads -> "
          f"{len(parcels)} simulated plots in {len(by_block)} blocks. Encroachments planted on {enc}")
    return truth


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) >= 6:
        ws, s, w, n, e = sys.argv[1], *map(float, sys.argv[2:6])
        title = sys.argv[6] if len(sys.argv) > 6 else ws
    else:
        ws, (s, w, n, e), title = "jayanagar", (12.9270, 77.5800, 12.9320, 77.5870), "Jayanagar, Bengaluru — real-world"
    build(ws, (s, w, n, e), title)
