"""Synthetic ward generator.

Real NAKSHA data is not public, so the prototype builds one hidden "true" ward and
derives from it the messy datasets each department would actually hand over:

  * cadastral map      - old digitised sheet in an unknown local grid (rotated, scaled,
                         shifted), digitising noise, a bow-tie polygon, a duplicate
  * revenue records    - RoR CSV, khasra IDs in another format, misspelt / stale owner
                         names, extent in square yards, missing and orphan rows
  * municipal parcels  - property-tax layer in WGS84, offset, merged plots, stale usage
  * building footprints- open-buildings style layer from the older date
  * utility network    - water mains in Web Mercator, two legacy pipes under buildings
  * GNSS control points- RTK corners for ~15% of parcels
  * ground truth survey- field verification CSV with lat/lon
  * drone ORI + DSM    - rendered orthophotos and surface models for two survey dates

The truth is written to data/_truth and is only used to score the pipeline.
"""
import csv
import math
import random
import shutil
from pathlib import Path

import cv2
import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon

from . import config, geo

FIRST = ["Ramesh", "Suresh", "Lakshmi", "Priya", "Anil", "Sunita", "Mahesh", "Kavitha", "Venkatesh",
         "Shobha", "Rajesh", "Geetha", "Manjunath", "Deepa", "Srinivas", "Anitha", "Prakash", "Rekha",
         "Naveen", "Pooja", "Harish", "Meena", "Ganesh", "Asha", "Mohan", "Latha", "Ravi", "Uma",
         "Kiran", "Savitha", "Nagaraj", "Divya", "Vinay", "Bharathi", "Dinesh", "Jyothi"]
LAST = ["Kumar", "Reddy", "Sharma", "Rao", "Gowda", "Naidu", "Iyer", "Patil", "Shetty", "Hegde",
        "Nair", "Murthy", "Prasad", "Joshi", "Kulkarni", "Pillai", "Menon", "Bhat", "Swamy", "Das"]
HONORIFICS = ["Sri", "Smt", "Shri", "Kum"]
ROOF_COLOURS = [(176, 84, 62), (204, 202, 196), (74, 112, 158), (150, 150, 152), (212, 192, 160), (120, 60, 50)]
SYN_EPSG = 32643   # the synthetic ward always lives in UTM 43N
USE_CODES = {"residential": "RES", "commercial": "COM", "institutional": "INS", "vacant": "VAC"}
ROR_CLASS = {"residential": "Residential", "commercial": "Commercial",
             "institutional": "Institutional", "vacant": "Vacant land"}


# ----------------------------------------------------------------------------- names
def _vowel_variant(rng, word):
    subs = [("a", "aa"), ("e", "ee"), ("i", "ee"), ("u", "oo"), ("sh", "s"), ("th", "t")]
    rng.shuffle(subs)
    for a, b in subs:
        idx = word.lower().find(a, 1)
        if idx > 0:
            return word[:idx] + b + word[idx + len(a):]
    return word


def name_variant(rng, name):
    """The same person written the way a different office would write them."""
    first, last = name.split(" ", 1)
    r = rng.random()
    if r < 0.25:
        out = name
    elif r < 0.45:
        out = f"{rng.choice(HONORIFICS)} {name}"
    elif r < 0.62:
        out = f"{_vowel_variant(rng, first)} {last}"
    elif r < 0.78:
        out = f"{first[0]}. {last}"
    elif r < 0.90:
        out = f"{last} {first}"
    else:
        out = f"{name} {rng.choice('SRKMN')}"
    return out.upper() if rng.random() < 0.5 else out


def _random_name(rng, exclude_first=None):
    while True:
        f = rng.choice(FIRST)
        if f != exclude_first:
            return f"{f} {rng.choice(LAST)}"


# ----------------------------------------------------------------------------- geometry
def _jitter(poly, nrng, sigma, shift=(0.0, 0.0)):
    pts = geo.ring_points(poly)
    pts = pts + nrng.normal(0, sigma, pts.shape) + np.asarray(shift)
    return Polygon(pts)


def _to_local(poly, T):
    th, s = math.radians(T["theta_deg"]), T["scale"]
    c, si = math.cos(th), math.sin(th)
    pts = geo.ring_points(poly) - [T["e0"], T["n0"]]
    x = s * (c * pts[:, 0] - si * pts[:, 1]) + T["tx"]
    y = s * (si * pts[:, 0] + c * pts[:, 1]) + T["ty"]
    return Polygon(np.column_stack([x, y]))


def _front_offset(parcel):
    """Unit vector pointing from the parcel toward its road frontage."""
    return (0.0, -1.0) if parcel["row"] == 0 else (0.0, 1.0)


# ----------------------------------------------------------------------------- rendering
class _Grid:
    def __init__(self, x0, y_top, res, width, height):
        self.x0, self.y_top, self.res, self.w, self.h = x0, y_top, res, width, height

    def px(self, pts):
        pts = np.asarray(pts, dtype=float)
        col = (pts[:, 0] - self.x0) / self.res
        row = (self.y_top - pts[:, 1]) / self.res
        return np.round(np.column_stack([col, row]) * 8).astype(np.int32)  # 3-bit sub-pixel shift

    def fill(self, img, poly, value):
        cv2.fillPoly(img, [self.px(geo.ring_points(poly))], value, lineType=cv2.LINE_AA, shift=3)

    def mask(self, poly):
        m = np.zeros((self.h, self.w), np.uint8)
        cv2.fillPoly(m, [self.px(geo.ring_points(poly))], 1, shift=3)
        return m.astype(bool)


def _render_ortho(grid, blocks, parcels, buildings, trees, nrng, tint):
    img = np.empty((grid.h, grid.w, 3), np.float32)
    img[:] = (98, 98, 102)                                   # asphalt roads
    for b in blocks:
        grid.fill(img, b, (176, 164, 136))
    for p in parcels:                                        # yards, each a slightly different tone
        tone = p["tone"]
        if p["_vacant_now"]:
            tone = (118 + tone[0] - 176, 136 + tone[1] - 164, 88)
        grid.fill(img, p["geom"], tone)
    low = cv2.resize(cv2.GaussianBlur(nrng.normal(0, 1, (grid.h // 8 + 1, grid.w // 8 + 1)).astype(np.float32),
                                      (0, 0), 2), (grid.w, grid.h))
    img += low[..., None] * 9
    for b in buildings:                                      # shadows cast to the south-east
        h = b["height"]
        sh = affinity.translate(b["geom"], 0.12 * h, -0.12 * h)
        m = grid.mask(sh)
        img[m] *= 0.55
    for b in buildings:
        grid.fill(img, b["geom"], ROOF_COLOURS[b["roof"]])
        cv2.polylines(img, [grid.px(geo.ring_points(b["geom"]))], True, (60, 60, 60), 1, cv2.LINE_AA, 3)
    for t in trees:
        c = grid.px([[t["x"], t["y"]]])[0]
        cv2.circle(img, (int(c[0]), int(c[1])), int(t["r"] / grid.res * 8), (52, 94, 46), -1, cv2.LINE_AA, 3)
        cv2.circle(img, (int(c[0] - 6), int(c[1] - 6)), int(t["r"] * 0.55 / grid.res * 8), (72, 118, 58), -1,
                   cv2.LINE_AA, 3)
    img += nrng.normal(0, 6, img.shape).astype(np.float32)
    img = cv2.GaussianBlur(img, (0, 0), 0.6) * np.asarray(tint, np.float32)
    return np.clip(img, 0, 255).astype(np.uint8)


def _render_dsm(grid, dtm, buildings, trees, nrng):
    heights = np.zeros((grid.h, grid.w), np.float32)
    for b in buildings:
        cv2.fillPoly(heights, [grid.px(geo.ring_points(b["geom"]))], float(b["height"]), shift=3)
    canopy = np.zeros_like(heights)
    for t in trees:
        c = grid.px([[t["x"], t["y"]]])[0]
        cv2.circle(canopy, (int(c[0]), int(c[1])), int(t["r"] / grid.res * 8), float(t["h"]), -1, shift=3)
    return (dtm + np.maximum(heights, canopy) + nrng.normal(0, 0.12, dtm.shape)).astype(np.float32)


# ----------------------------------------------------------------------------- main
def generate(out_root=None, seed=47, rasters=True, n_cols=6, n_rows=5, muni_noise=0.5, muni_shift=(0.6, -0.4),
             departments=True):
    """Create a synthetic ward. Writes <out_root>/raw/* and <out_root>/_truth/truth.json."""
    out_root = Path(out_root) if out_root else config.DATA_DIR / config.DEFAULT_WS
    raw, truth_dir = out_root / "raw", out_root / "_truth"
    for d in (raw, truth_dir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    rng, nrng = random.Random(seed), np.random.default_rng(seed)
    to_utm = geo.transformer(config.WGS84, SYN_EPSG)
    e0, n0 = to_utm.transform(*config.WARD_ORIGIN_LONLAT)
    e0, n0 = round(e0, -1), round(n0, -1)
    BW, BH, ROAD = 60.0, 40.0, 10.0
    W, H = n_cols * (BW + ROAD) + ROAD, n_rows * (BH + ROAD) + ROAD

    # ---- true parcels ---------------------------------------------------------
    blocks, parcels = [], []
    for j in range(n_rows):
        for i in range(n_cols):
            bx, by = e0 + ROAD + i * (BW + ROAD), n0 + ROAD + j * (BH + ROAD)
            blocks.append(Polygon([(bx, by), (bx + BW, by), (bx + BW, by + BH), (bx, by + BH)]))
            survey = 101 + j * n_cols + i
            ym = by + BH / 2 + rng.uniform(-2.5, 2.5)
            sub = 1
            for row in (0, 1):
                y_lo, y_hi = (by, ym) if row == 0 else (ym, by + BH)
                n = rng.choice([3, 4, 4, 5])
                ws = [rng.uniform(0.75, 1.25) for _ in range(n)]
                xs = [bx]
                for w in ws:
                    xs.append(xs[-1] + w * BW / sum(ws))
                lo_x = [bx] + [x + rng.uniform(-1.5, 1.5) for x in xs[1:-1]] + [bx + BW]
                hi_x = [bx] + [x + rng.uniform(-1.5, 1.5) for x in xs[1:-1]] + [bx + BW]
                for k in range(n):
                    poly = Polygon([(lo_x[k], y_lo), (lo_x[k + 1], y_lo), (hi_x[k + 1], y_hi), (hi_x[k], y_hi)])
                    r = rng.random()
                    use = "vacant" if r < 0.12 else "commercial" if r < 0.27 else \
                        "institutional" if r < 0.30 else "residential"
                    owner = _random_name(rng)
                    parcels.append(dict(
                        plot_id=f"{survey}/{sub}", survey=survey, sub=sub, geom=poly, block=(i, j), row=row,
                        k=k, n_in_row=n, use=use, owner=owner, prev_owner=_random_name(rng, owner.split()[0]),
                        tone=(rng.uniform(168, 186), rng.uniform(156, 170), rng.uniform(126, 142))))
                    sub += 1
    by_id = {p["plot_id"]: p for p in parcels}

    # ---- buildings at T1 (older survey) and T2 (current drone survey) -----------
    b_t1 = {}
    for p in parcels:
        if p["use"] == "vacant":
            continue
        sb = rng.uniform(1.0, 2.0)
        floors = rng.randint(2, 5) if p["use"] == "commercial" else rng.randint(1, 4)
        b_t1[p["plot_id"]] = dict(id=f"B-{p['plot_id']}", plot_id=p["plot_id"],
                                  geom=p["geom"].buffer(-sb, join_style="mitre"),
                                  height=round(floors * 3.2 + rng.uniform(0.3, 0.8), 2),
                                  roof=rng.randrange(len(ROOF_COLOURS)), setback=sb)

    built = [pid for pid in b_t1 if by_id[pid]["use"] == "residential"]
    rng.shuffle(built)
    encroach = built[:3]
    for pid in encroach:                      # building pushed 1.6 m past the front boundary into the road
        b = b_t1[pid]
        dx, dy = _front_offset(by_id[pid])
        d = b["setback"] + 1.6
        b["geom"] = affinity.translate(b["geom"], dx * d, dy * d)

    b_t2 = {pid: dict(b) for pid, b in b_t1.items()}
    changes = []
    demolished = built[3:6]
    for pid in demolished:
        del b_t2[pid]
        changes.append({"type": "demolished", "plot_id": pid, "building_id": b_t1[pid]["id"]})
    ext_candidates = [pid for pid in built[6:] if b_t1[pid]["setback"] > 1.6]
    extended = ext_candidates[:4]
    for pid in extended:
        g = by_id[pid]["geom"].buffer(-0.7, join_style="mitre")
        b_t2[pid] = dict(b_t1[pid], geom=g)
        changes.append({"type": "extension", "plot_id": pid, "building_id": b_t1[pid]["id"]})
    floor_add = [pid for pid in built[6:] if pid not in extended][:4]
    for pid in floor_add:
        b_t2[pid] = dict(b_t1[pid], height=round(b_t1[pid]["height"] + 3.2, 2))
        changes.append({"type": "new_floor", "plot_id": pid, "building_id": b_t1[pid]["id"]})
    vacant = [p["plot_id"] for p in parcels if p["use"] == "vacant"]
    rng.shuffle(vacant)
    new_built = vacant[:6]
    for pid in new_built:
        sb = rng.uniform(1.0, 2.0)
        b_t2[pid] = dict(id=f"B-{pid}", plot_id=pid, geom=by_id[pid]["geom"].buffer(-sb, join_style="mitre"),
                         height=round(rng.randint(1, 3) * 3.2 + 0.5, 2), roof=rng.randrange(len(ROOF_COLOURS)),
                         setback=sb)
        changes.append({"type": "new", "plot_id": pid, "building_id": f"B-{pid}"})

    # ---- trees ---------------------------------------------------------------
    trees = []
    for pid in vacant[6:]:
        g = by_id[pid]["geom"]
        for _ in range(rng.randint(1, 2)):
            c = g.representative_point()
            trees.append(dict(x=c.x + rng.uniform(-2, 2), y=c.y + rng.uniform(-2, 2),
                              r=rng.uniform(2.0, 3.2), h=rng.uniform(6, 9)))
    for _ in range(18):                        # street trees on the road verges
        j = rng.randrange(n_rows + 1)
        trees.append(dict(x=e0 + rng.uniform(15, W - 15), y=n0 + j * (BH + ROAD) + rng.choice([2.2, 7.8]),
                          r=rng.uniform(1.8, 2.6), h=rng.uniform(5, 8)))

    # ---- utilities -------------------------------------------------------------
    utilities = []
    for j in range(n_rows + 1):
        y = n0 + ROAD / 2 + j * (BH + ROAD)
        utilities.append(dict(id=f"WM-{j + 1:02d}", type="water_main", diameter=rng.choice([150, 200, 250]),
                              geom=LineString([(e0, y), (e0 + W, y)]), legacy=False))
    for i in range(0, n_cols + 1, 2):
        x = e0 + ROAD / 2 + i * (BW + ROAD)
        utilities.append(dict(id=f"WV-{i + 1:02d}", type="water_main", diameter=150,
                              geom=LineString([(x, n0), (x, n0 + H)]), legacy=False))
    legacy_ids = []
    for n_, blk in enumerate(rng.sample(range(len(blocks)), 2)):
        minx, miny, maxx, maxy = blocks[blk].bounds
        uid = f"LP-{n_ + 1:02d}"
        utilities.append(dict(id=uid, type="legacy_pipe", diameter=100, legacy=True,
                              geom=LineString([(minx - 5, miny + 6), (maxx + 5, maxy - 6)])))
        legacy_ids.append(uid)

    # ---- source 1: cadastral map (unknown local grid) ---------------------------
    T = dict(theta_deg=1.2, scale=1.0035, tx=1000.0, ty=2000.0, e0=e0, n0=n0)
    cad_ids = [p["plot_id"] for p in parcels]
    cad_missing = rng.sample(cad_ids, 2)
    bowties = rng.sample([c for c in cad_ids if c not in cad_missing], 2)
    duplicate = rng.choice([c for c in cad_ids if c not in cad_missing + bowties])
    cad_feats = []
    for p in parcels:
        if p["plot_id"] in cad_missing:
            continue
        noisy = _jitter(p["geom"], nrng, 0.3)
        local = _to_local(noisy, T)
        if p["plot_id"] in bowties:
            pts = geo.ring_points(local)
            local = Polygon([pts[0], pts[2], pts[1], pts[3]])
        props = {"SURVEY_NO": p["survey"], "SUB_DIV": str(p["sub"]),
                 "EXTENT_SQM": round(p["geom"].area * (1 + nrng.normal(0, 0.02)), 1),
                 "VILLAGE": "Ward-47", "SHEET": f"CS-{p['block'][1] + 1:02d}"}
        cad_feats.append((local, props))
        if p["plot_id"] == duplicate:
            cad_feats.append((affinity.translate(local, 0.4, -0.3), dict(props)))
    geo.write_feature_collection(raw / "cadastral_map_ward47.geojson", cad_feats, crs_name=None, decimals=3)

    # ---- source 2: revenue records (RoR) --------------------------------------
    all_ids = [p["plot_id"] for p in parcels]
    ror_missing = rng.sample([i for i in all_ids if i not in cad_missing], 3)
    ror_ids = [i for i in all_ids if i not in ror_missing]
    ror_stale = rng.sample(ror_ids, max(1, round(0.06 * len(ror_ids))))
    area_mismatch = rng.sample([i for i in ror_ids if i not in ror_stale], 5)
    rows = []
    for pid in ror_ids:
        p = by_id[pid]
        owner = p["prev_owner"] if pid in ror_stale else p["owner"]
        err = rng.uniform(0.15, 0.25) * rng.choice([-1, 1]) if pid in area_mismatch else nrng.normal(0, 0.015)
        rows.append({"Khata No": f"KT{rng.randint(1000, 9999)}",
                     "Khasra No.": f"KH-{p['survey']:03d}/{p['sub']}",
                     "Pattadar Name": name_variant(rng, owner),
                     "Father/Husband Name": _random_name(rng),
                     "Extent (Sq.Yd)": round(p["geom"].area * (1 + err) / 0.836127, 1),
                     "Land Classification": ROR_CLASS[p["use"]]})
    orphans = [f"{101 + n_cols * n_rows + 3}/1", f"{101 + n_cols * n_rows + 7}/2"]
    for oid in orphans:
        s, sub = oid.split("/")
        rows.append({"Khata No": f"KT{rng.randint(1000, 9999)}", "Khasra No.": f"KH-{int(s):03d}/{sub}",
                     "Pattadar Name": name_variant(rng, _random_name(rng)),
                     "Father/Husband Name": _random_name(rng),
                     "Extent (Sq.Yd)": round(rng.uniform(250, 500), 1), "Land Classification": "Residential"})
    rng.shuffle(rows)
    with open(raw / "revenue_records_RoR.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---- source 3: municipal property-tax parcels (WGS84) -------------------------
    muni_missing = set(rng.sample(all_ids, round(0.08 * len(all_ids))))
    merges = []
    for p in rng.sample(parcels, 12):
        nxt = f"{p['survey']}/{p['sub'] + 1}"
        if p["k"] < p["n_in_row"] - 1 and nxt in by_id and len(merges) < 3 and \
                not ({p["plot_id"], nxt} & (muni_missing | {x for m in merges for x in m})):
            merges.append((p["plot_id"], nxt))
    merged_second = {b for _, b in merges}
    muni_stale = set(rng.sample([i for i in all_ids if i not in ror_stale], 3))
    muni_feats, muni_map, shift = [], {}, muni_shift
    count = 0
    for p in parcels:
        pid = p["plot_id"]
        if pid in muni_missing or pid in merged_second:
            continue
        count += 1
        geom, members = p["geom"], [pid]
        for a, b in merges:
            if a == pid:
                geom = geom.union(by_id[b]["geom"]).buffer(0.01).buffer(-0.01)
                members.append(b)
        noisy = geo.largest_polygon(_jitter(geom.simplify(0.05), nrng, muni_noise, shift))
        pid_m = f"MCC-W47-{count:04d}"
        muni_map[pid_m] = members
        owner = p["prev_owner"] if pid in muni_stale else p["owner"]
        muni_feats.append((geo.reproject(noisy, SYN_EPSG, config.WGS84), {
            "PID": pid_m, "plot_no": f"{p['survey']}-{p['sub']}", "OCCUPANT": name_variant(rng, owner),
            "prop_area_sqft": round(geom.area / 0.092903 * (1 + nrng.normal(0, 0.03))),
            "usage": USE_CODES[p["use"]], "ward": 47}))
    geo.write_feature_collection(raw / "municipal_property_tax.geojson", muni_feats, decimals=8)

    # ---- source 4: open building footprints (older date, WGS84) ----------------------
    fp_feats = []
    for b in b_t1.values():
        if rng.random() < 0.12:
            continue
        g = _jitter(b["geom"], nrng, 0.35, (0.5, 0.3)).minimum_rotated_rectangle
        fp_feats.append((geo.reproject(g, SYN_EPSG, config.WGS84),
                         {"confidence": round(rng.uniform(0.65, 0.95), 2), "area_in_meters": round(g.area, 1)}))
    for t in rng.sample(trees, 4):             # false positives on tree crowns
        g = Polygon([(t["x"] - t["r"], t["y"] - t["r"]), (t["x"] + t["r"], t["y"] - t["r"]),
                     (t["x"] + t["r"], t["y"] + t["r"]), (t["x"] - t["r"], t["y"] + t["r"])])
        fp_feats.append((geo.reproject(g, SYN_EPSG, config.WGS84),
                         {"confidence": round(rng.uniform(0.55, 0.7), 2), "area_in_meters": round(g.area, 1)}))
    geo.write_feature_collection(raw / "open_building_footprints.geojson", fp_feats, decimals=8)

    # ---- source 5: utility network (Web Mercator) ----------------------------------
    geo.write_feature_collection(
        raw / "water_utility_network.geojson",
        [(geo.reproject(u["geom"], SYN_EPSG, 3857),
          {"asset_id": u["id"], "asset_type": u["type"], "diameter_mm": u["diameter"]}) for u in utilities],
        crs_name="urn:ogc:def:crs:EPSG::3857", decimals=3)

    # ---- source 6: GNSS / CORS control points -----------------------------------
    gnss_pool = [p for p in parcels if p["plot_id"] not in cad_missing]
    gnss_parcels = rng.sample(gnss_pool, round(0.15 * len(parcels)))
    gnss_feats, n_pt = [], 0
    to_ll = geo.transformer(SYN_EPSG, config.WGS84)
    for p in gnss_parcels:
        for ci, (x, y) in enumerate(geo.ring_points(p["geom"])):
            n_pt += 1
            ll = to_ll.transform(x + nrng.normal(0, 0.015), y + nrng.normal(0, 0.015))
            gnss_feats.append((Point(ll), {"point_id": f"CP-{n_pt:03d}", "plot_ref": f"Sy.No {p['plot_id']}",
                                           "corner": ci + 1, "fix_type": "RTK-FIXED", "h_accuracy_m": 0.02}))
    geo.write_feature_collection(raw / "gnss_control_points.geojson", gnss_feats, decimals=9)

    # ---- source 7: ground-truth field survey -------------------------------------
    gt_ids = rng.sample(ror_stale, max(1, len(ror_stale) * 2 // 3))
    gt_ids += rng.sample([i for i in all_ids if i not in gt_ids], 25 - len(gt_ids))
    with open(raw / "ground_truth_survey.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["visit_id", "plot", "verified_owner", "verified_use", "remarks", "lat", "lon"])
        for n_, pid in enumerate(gt_ids):
            p = by_id[pid]
            c = p["geom"].centroid
            lon, lat = geo.transformer(SYN_EPSG, config.WGS84).transform(c.x, c.y)
            w.writerow([f"GT-{n_ + 1:03d}", f"{p['survey']}/{p['sub']}", p["owner"],
                        ROR_CLASS[p["use"]], rng.choice(["Owner present", "Neighbour confirmed", "Documents seen"]),
                        f"{lat:.7f}", f"{lon:.7f}"])

    # ---- source 8: drone orthophotos + DSM/DTM for two dates ---------------------
    meta = {"epsg": SYN_EPSG,
            "ortho": {"x0": e0, "y_top": n0 + H, "res": config.ORTHO_RES,
                      "width": int(W / config.ORTHO_RES), "height": int(H / config.ORTHO_RES)},
            "dsm": {"x0": e0, "y_top": n0 + H, "res": config.DSM_RES,
                    "width": int(W / config.DSM_RES), "height": int(H / config.DSM_RES)},
            "epochs": {"t1": {"date": "2024-02-10", "ortho": "drone_ortho_2024.png", "dsm": "dsm_2024.npy"},
                       "t2": {"date": "2026-03-18", "ortho": "drone_ortho_2026.png", "dsm": "dsm_2026.npy"}},
            "dtm": "dtm_2026.npy"}
    if rasters:
        og = _Grid(e0, n0 + H, config.ORTHO_RES, meta["ortho"]["width"], meta["ortho"]["height"])
        dg = _Grid(e0, n0 + H, config.DSM_RES, meta["dsm"]["width"], meta["dsm"]["height"])
        cols, rows_ = np.meshgrid(np.arange(dg.w) + 0.5, np.arange(dg.h) + 0.5)
        dtm = (18.0 + 0.012 * cols * dg.res + 0.006 * (dg.h - rows_) * dg.res).astype(np.float32)
        np.save(raw / meta["dtm"], dtm)
        for key, bset, tint in (("t1", b_t1, (1.0, 1.0, 1.0)), ("t2", b_t2, (1.03, 1.0, 0.96))):
            built_now = set(bset)
            for p in parcels:
                p["_vacant_now"] = p["plot_id"] not in built_now and p["use"] == "vacant"
            img = _render_ortho(og, blocks, parcels, list(bset.values()), trees, nrng, tint)
            cv2.imwrite(str(raw / meta["epochs"][key]["ortho"]), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
            np.save(raw / meta["epochs"][key]["dsm"], _render_dsm(dg, dtm, list(bset.values()), trees, nrng))
    geo.write_json(raw / "raster_meta.json", meta, indent=2)

    # ---- hidden truth ------------------------------------------------------------
    truth = {
        "seed": seed, "epsg": SYN_EPSG, "transform_utm_to_local": T,
        "parcels": [{"plot_id": p["plot_id"], "wkt": p["geom"].wkt, "owner": p["owner"],
                     "prev_owner": p["prev_owner"], "use": p["use"]} for p in parcels],
        "buildings_t1": [{"id": b["id"], "plot_id": b["plot_id"], "wkt": b["geom"].wkt, "height": b["height"]}
                         for b in b_t1.values()],
        "buildings_t2": [{"id": b["id"], "plot_id": b["plot_id"], "wkt": b["geom"].wkt, "height": b["height"]}
                         for b in b_t2.values()],
        "changes": changes, "encroachments": encroach, "legacy_pipes": legacy_ids,
        "municipal_map": muni_map, "municipal_merges": merges, "municipal_missing": sorted(muni_missing),
        "municipal_stale_owner": sorted(muni_stale),
        "ror_missing": ror_missing, "ror_orphans": orphans, "ror_stale_owner": ror_stale,
        "area_mismatch": area_mismatch, "cadastral_missing": cad_missing, "cadastral_bowties": bowties,
        "cadastral_duplicate": duplicate, "gnss_parcels": [p["plot_id"] for p in gnss_parcels],
        "gt_parcels": gt_ids,
        "schema": {
            "cadastral_map_ward47.geojson": {"plot_id": "SURVEY_NO", "plot_sub": "SUB_DIV", "area": "EXTENT_SQM"},
            "revenue_records_RoR.csv": {"plot_id": "Khasra No.", "owner_name": "Pattadar Name",
                                        "area": "Extent (Sq.Yd)", "land_use": "Land Classification",
                                        "record_id": "Khata No"},
            "municipal_property_tax.geojson": {"plot_id": "plot_no", "owner_name": "OCCUPANT",
                                               "area": "prop_area_sqft", "land_use": "usage", "record_id": "PID"},
            "gnss_control_points.geojson": {"plot_id": "plot_ref", "record_id": "point_id"},
            "ground_truth_survey.csv": {"plot_id": "plot", "owner_name": "verified_owner",
                                        "land_use": "verified_use", "record_id": "visit_id"},
            "open_building_footprints.geojson": {"area": "area_in_meters"},
            "water_utility_network.geojson": {"record_id": "asset_id"},
        },
    }
    geo.write_json(truth_dir / "truth.json", truth)
    geo.write_json(out_root / "workspace.json", {
        "title": "Ward 47, Mangaluru — synthetic benchmark", "kind": "synthetic", "seed": seed,
        "description": "Fully synthetic ward with a known true layout. Every department file has planted "
                       "errors, so accuracy can be measured exactly.",
        "sources": []}, indent=2)
    if departments:
        from . import deptdata
        truth = deptdata.add(out_root, seed=seed + 1)
    return truth


if __name__ == "__main__":
    t = generate()
    print(f"Generated {len(t['parcels'])} parcels, {len(t['buildings_t2'])} buildings (current), "
          f"{len(t['changes'])} planted changes -> {config.ws_paths(config.DEFAULT_WS)['raw']}")
