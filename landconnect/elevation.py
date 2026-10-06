"""Elevation models for a real-world area: building heights, floors and change detection.

Open elevation data for India (Bhuvan CartoDEM, SRTM, Copernicus) is 10-30 m per pixel: it shows the
terrain but cannot resolve a house. Building-level DSMs come from drone or LiDAR surveys, which are not
public. For the real-world workspaces this module therefore SIMULATES, and labels as simulated:

  * a DTM (bare earth: gentle slope and undulation),
  * a DSM for the current date (DTM + every real building footprint at a plausible height + trees),
  * a DSM for an earlier survey date with planted changes: buildings that did not exist yet (new),
    a building that has since been demolished, smaller footprints (extensions) and lower roofs (added floors).

The pipeline then uses them like real survey rasters:
  * sample()          height of each fused building = median nDSM (DSM - DTM) inside its footprint
  * detect_changes()  compares the two DSMs inside each current footprint (new / extension / added floor)
                      and finds raised areas that have gone to ground (demolished)
Comparing elevation inside one footprint, instead of comparing two differently-traced outlines, is what
keeps outline noise from turning into false "extensions".
"""
import json
import random
from pathlib import Path

import cv2
import numpy as np
from shapely import affinity, wkt
from shapely.geometry import Polygon, box, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from . import geo

RES = 0.5              # m per pixel
FLOOR_M = 3.2
MIN_H = 2.5            # anything lower is not a building storey
T1_DATE, T2_DATE = "2021-11-20", "2026-02-14"


# ----------------------------------------------------------------------------- raster helpers
def _px(geom, grid):
    x0, y_top, res = grid["x0"], grid["y_top"], grid["res"]
    polys = getattr(geom, "geoms", [geom])
    out = []
    for p in polys:
        if p.geom_type != "Polygon" or p.is_empty:
            continue
        a = np.asarray(p.exterior.coords)
        out.append(np.stack([(a[:, 0] - x0) / res, (y_top - a[:, 1]) / res], axis=1).round().astype(np.int32))
    return out


def mask_of(geom, grid):
    m = np.zeros((grid["height"], grid["width"]), np.uint8)
    pts = _px(geom, grid)
    if pts:
        cv2.fillPoly(m, pts, 1)
    return m.astype(bool)


def _read(path):
    if str(path).lower().endswith((".tif", ".tiff")):
        from . import geotiff
        return geotiff.read(path)[0].astype(np.float32)
    return np.load(path)


def window(geom, grid, pad=3):
    """(row slice, col slice, local mask) of one footprint: rasterise only the pixels around it."""
    x0, y_top, res = grid["x0"], grid["y_top"], grid["res"]
    minx, miny, maxx, maxy = geom.bounds
    c0, c1 = max(0, int((minx - x0) / res) - pad), min(grid["width"], int((maxx - x0) / res) + pad + 1)
    r0, r1 = max(0, int((y_top - maxy) / res) - pad), min(grid["height"], int((y_top - miny) / res) + pad + 1)
    if c1 <= c0 or r1 <= r0:
        return None
    local = dict(grid, x0=x0 + c0 * res, y_top=y_top - r0 * res, width=c1 - c0, height=r1 - r0)
    return slice(r0, r1), slice(c0, c1), mask_of(geom, local)


def load(raw_dir, datasets=()):
    """Return (meta, dtm, {"t1": dsm, "t2": dsm}) or None if the area has no elevation models.
    Either a raw/elevation/ folder described by elevation_meta.json, or DSM / DTM GeoTIFFs uploaded with the
    other files (one DSM = heights only; two DSMs with a year in the name = heights and change detection)."""
    d = Path(raw_dir) / "elevation"
    mp = d / "elevation_meta.json"
    if mp.exists():
        meta = geo.read_json(mp)
        return meta, _read(d / meta["dtm"]), {k: _read(d / ep["dsm"]) for k, ep in meta["epochs"].items()}
    tifs = [x for x in datasets if x.format == "GeoTIFF" and x.role in ("dsm", "dtm") and x.raster.get("kind") == "elevation"]
    dtms = [x for x in tifs if x.role == "dtm"]
    dsms = sorted([x for x in tifs if x.role == "dsm"], key=lambda x: x.name)
    if not dtms or not dsms:
        return None
    from . import geotiff
    dtm, grid, epsg = geotiff.read(dtms[0].path)
    same = [x for x in dsms if (x.raster["width"], x.raster["height"]) == (grid["width"], grid["height"])]
    if not same:
        return None
    import re
    year = lambda x: (re.search(r"(19|20)\d\d", x.name) or [None])[0]   # noqa: E731
    epochs = {"t2": same[-1]} if len(same) == 1 else {"t1": same[-2], "t2": same[-1]}
    meta = {"epsg": epsg, "grid": grid, "simulated": False, "dtm": dtms[0].name,
            "epochs": {k: {"date": year(x) or k, "dsm": x.name} for k, x in epochs.items()},
            "note": "Uploaded GeoTIFF elevation models."}
    return meta, dtm.astype(np.float32), {k: geotiff.read(x.path)[0].astype(np.float32) for k, x in epochs.items()}


def sample(geom, dsm, dtm, grid):
    """(height_m, elevated_area_m2) of one footprint: median nDSM of its raised pixels."""
    w = window(geom, grid)
    if w is None or not w[2].any():
        return None, 0.0
    rs, cs, m = w
    n = (dsm[rs, cs] - dtm[rs, cs])[m]
    up = n[n > MIN_H]
    if up.size < 8:
        return None, float(up.size * grid["res"] ** 2)
    return round(float(np.median(up)), 1), float(up.size * grid["res"] ** 2)


def floors_from(h):
    return max(1, int(round((h - 0.6) / FLOOR_M))) if h else None


def detect_changes(buildings, dsm1, dsm2, dtm, grid, assign_parcel, dates):
    """Changes between two surveys, measured on the elevation models inside each current footprint."""
    a_px = grid["res"] ** 2
    n1, n2 = dsm1 - dtm, dsm2 - dtm
    covered = np.zeros(dtm.shape, bool)
    out = []
    for b in buildings:
        w = window(b["geom"], grid)
        if w is None or not w[2].any():
            continue
        rs, cs, m = w
        covered[rs, cs] |= cv2.dilate(m.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        l1, l2 = n1[rs, cs], n2[rs, cs]
        e1, e2 = (l1 > MIN_H) & m, (l2 > MIN_H) & m
        A1, A2 = e1.sum() * a_px, e2.sum() * a_px
        if A2 < 12:
            continue
        pid = b.get("plot_id") or assign_parcel(b["geom"])
        h2 = float(np.median(l2[e2]))
        base = dict(geom=b["geom"], plot_id=pid, building=b.get("id"), area_t2=round(A2, 1), height_t2=round(h2, 1))
        if A1 < 0.2 * A2:
            out.append(dict(base, type="new", area_t1=0, height_t1=0,
                            detail=f"New building since {dates[0][:4]}: {A2:.0f} m², {floors_from(h2)} floor(s)"))
            continue
        h1 = float(np.median(l1[e1]))
        if A2 - A1 > 12 and A1 / A2 < 0.85:
            out.append(dict(base, type="extension", area_t1=round(A1, 1), height_t1=round(h1, 1),
                            detail=f"Footprint grew {A2 - A1:.0f} m² ({(A2 / A1 - 1) * 100:.0f}%) since {dates[0][:4]}"))
        both = e1 & e2
        if both.sum() * a_px >= 8:
            dh = float(np.median(l2[both]) - np.median(l1[both]))
            if dh > 0.7 * FLOOR_M:
                k = max(1, round(dh / FLOOR_M))
                out.append(dict(base, type="new_floor", area_t1=round(A1, 1), height_t1=round(h1, 1),
                                detail=f"Roof {h1:.1f} m → {h2:.1f} m (+{k} floor{'s' * (k > 1)}) since {dates[0][:4]}"))
    # demolished: raised in the earlier survey, bare ground now, and not under any current building
    gone = ((n1 > MIN_H) & (n2 < 1.0) & ~covered).astype(np.uint8)
    gone = cv2.morphologyEx(gone, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    k, lab, stats, _ = cv2.connectedComponentsWithStats(gone, 8)
    for i in range(1, k):
        if stats[i, cv2.CC_STAT_AREA] * a_px < 20:
            continue
        cs, _ = cv2.findContours((lab == i).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(cs, key=cv2.contourArea)[:, 0, :].astype(float)
        if len(c) < 3:
            continue
        g = Polygon(np.stack([grid["x0"] + (c[:, 0] + 0.5) * grid["res"], grid["y_top"] - (c[:, 1] + 0.5) * grid["res"]], 1))
        g = g.buffer(0) if not g.is_valid else g
        h1 = float(np.median(n1[lab == i]))
        area = stats[i, cv2.CC_STAT_AREA] * a_px
        out.append(dict(type="demolished", geom=g, plot_id=assign_parcel(g), building=None, area_t1=round(area, 1),
                        area_t2=0, height_t1=round(h1, 1), height_t2=0,
                        detail=f"Building of {area:.0f} m² ({floors_from(h1)} floor(s)) present in {dates[0][:4]}, gone now"))
    for n, c in enumerate(out):
        c["id"] = f"CH-{n + 1:03d}"
    return out


def hillshade_png(z, path, alpha_mask=None):
    """Grey hillshade (sun from the north-west) for showing a DSM or DTM on the map."""
    gy, gx = np.gradient(z.astype(np.float32), RES)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az, alt = np.radians(315), np.radians(45)
    hs = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    zn = (z - np.percentile(z, 1)) / max(np.percentile(z, 99) - np.percentile(z, 1), 1e-6)
    col = cv2.applyColorMap((np.clip(zn, 0, 1) * 255).astype(np.uint8), cv2.COLORMAP_CIVIDIS)
    shade = np.clip(hs, 0, 1)[..., None]
    img = (col * (0.35 + 0.65 * shade)).astype(np.uint8)
    alpha = np.full(z.shape, 215, np.uint8) if alpha_mask is None else np.where(alpha_mask, 215, 0).astype(np.uint8)
    cv2.imwrite(str(path), np.dstack([img, alpha]))


# ----------------------------------------------------------------------------- simulation
def simulate(root, seed=21):
    """Write raw/elevation/ (DTM, DSM 2021 and 2026, metadata) for a real-world workspace and record the
    planted changes and building heights in the hidden truth. Returns the updated truth."""
    root = Path(root)
    raw, tpath = root / "raw", root / "_truth" / "truth.json"
    truth = geo.read_json(tpath)
    epsg = truth["epsg"]
    rng, nrng = random.Random(seed), np.random.default_rng(seed)
    parcels = [(t["plot_id"], wkt.loads(t["wkt"]), t.get("use")) for t in truth["parcels"]]
    pgeoms = [g for _, g, _ in parcels]
    ptree = STRtree(pgeoms)

    # the buildings standing today: OSM footprints, plus AI-only footprints with the AI layer's offset removed
    osm = [geo.largest_polygon(geo.reproject(g, 4326, epsg)) for g, _ in geo.feature_geoms(geo.read_json(raw / "osm_buildings.geojson"))]
    osm = [g for g in osm if g.is_valid and g.area > 6]
    mdx, mdy = truth.get("ai_vs_osm_offset_m", [0.0, 0.0])
    ms = []
    with open(raw / "ms_ai_building_footprints.geojsonl", encoding="utf-8") as f:
        for line in f:
            g = geo.largest_polygon(geo.reproject(shape(json.loads(line)["geometry"]), 4326, epsg))
            ms.append(affinity.translate(g, mdx, mdy))
    otree = STRtree(osm)
    blds = list(osm) + [g for g in ms if g.is_valid and g.area > 12
                        and not any(osm[int(i)].intersects(g.buffer(0.3)) for i in otree.query(g.buffer(0.3)))]

    def plot_of(g):
        best, bi = 0.0, None
        for i in ptree.query(g):
            a = pgeoms[int(i)].intersection(g).area
            if a > best:
                best, bi = a, int(i)
        return parcels[bi][0] if bi is not None and best > 0.5 * g.area else None

    B = []
    for g in blds:
        a = g.area
        fl = rng.choice([1, 1, 2, 2]) if a < 60 else rng.choice([1, 2, 2, 3]) if a < 150 else \
            rng.choice([2, 2, 3, 3, 4]) if a < 400 else rng.choice([2, 3, 4, 5, 6])
        B.append({"geom": g, "plot": plot_of(g), "floors": fl, "h": round(fl * FLOOR_M + rng.uniform(0.3, 1.2), 2)})

    # grid over the area
    allb = unary_union(pgeoms + [b["geom"] for b in B]).bounds      # buildings may lie beyond the mapped parcels
    x0, y0, x1, y1 = allb[0] - 20, allb[1] - 20, allb[2] + 20, allb[3] + 20
    W, H = int(np.ceil((x1 - x0) / RES)), int(np.ceil((y1 - y0) / RES))
    grid = {"x0": round(x0, 2), "y_top": round(y1, 2), "res": RES, "width": W, "height": H}
    X, Y = np.meshgrid(np.arange(W) * RES, np.arange(H) * RES)
    dtm = (888.0 + 0.004 * X - 0.006 * Y + 1.2 * np.sin(X / 140.0) * np.cos(Y / 170.0)).astype(np.float32)
    dtm += cv2.GaussianBlur(nrng.normal(0, 0.25, dtm.shape).astype(np.float32), (0, 0), 6)

    # trees: canopy discs in open ground, identical in both surveys
    trees = np.zeros(dtm.shape, np.float32)
    built = cv2.dilate(mask_of(unary_union([b["geom"] for b in B]), grid).astype(np.uint8), np.ones((5, 5), np.uint8))
    for _ in range(int(W * H * RES * RES / 700)):
        cx, cy, r = rng.uniform(0, W), rng.uniform(0, H), rng.uniform(2.0, 4.5) / RES
        if built[int(min(cy, H - 1)), int(min(cx, W - 1))]:
            continue
        disc = np.zeros(dtm.shape, np.uint8)
        cv2.circle(disc, (int(cx), int(cy)), int(r), 1, -1)
        trees = np.maximum(trees, disc * rng.uniform(5, 12))
    trees *= ~built.astype(bool)
    trees = cv2.GaussianBlur(trees, (0, 0), 1.2)

    # planted changes between the two surveys
    per_plot = {}
    for b in B:
        if b["plot"]:
            per_plot.setdefault(b["plot"], []).append(b)
    single = [bs[0] for pid, bs in sorted(per_plot.items()) if len(bs) == 1 and 60 <= bs[0]["geom"].area <= 450]
    rng.shuffle(single)
    muni_missing = set(truth.get("municipal_missing", []))
    first = [b for b in single if b["plot"] in muni_missing][:2]                 # built, never taxed
    pool = [b for b in single if b not in first]
    new = first + pool[:6 - len(first)]
    ext = pool[6 - len(first):10 - len(first)]
    flr = [b for b in pool[10 - len(first):] if b["floors"] >= 2][:4]
    empty = [(pid, g) for pid, g, use in parcels if pid not in per_plot and g.area >= 120 and pid not in muni_missing]
    rng.shuffle(empty)
    gone = []
    for pid, g in empty[:3]:
        r = g.minimum_rotated_rectangle
        c = g.representative_point()
        ang = np.degrees(np.arctan2(*(np.diff(np.asarray(r.exterior.coords)[:2], axis=0)[0][::-1])))
        rect = affinity.rotate(box(c.x - 5, c.y - 4, c.x + 5, c.y + 4), ang, origin=c).intersection(g.buffer(-0.5))
        if rect.area > 30:
            gone.append({"geom": rect, "plot": pid, "floors": 2, "h": 2 * FLOOR_M + 0.6})

    def surface(epoch):
        z = dtm + trees
        roof = np.zeros(dtm.shape, np.float32)
        for b in B:
            g, h = b["geom"], b["h"]
            if epoch == 1:
                if b in new:
                    continue
                if b in ext:
                    g = _shrink(g, 0.7)
                if b in flr:
                    h -= FLOOR_M
            m = mask_of(g, grid)
            roof[m] = np.maximum(roof[m], h)
        if epoch == 1:
            for b in gone:
                m = mask_of(b["geom"], grid)
                roof[m] = np.maximum(roof[m], b["h"])
        z = np.where(roof > 0, dtm + roof, z) + nrng.normal(0, 0.08, dtm.shape).astype(np.float32)
        return z.astype(np.float32)

    from . import geotiff
    d = raw / "elevation"
    if d.exists():
        for f in d.iterdir():
            f.unlink()
    d.mkdir(parents=True, exist_ok=True)
    geotiff.write(d / "dtm.tif", dtm.astype(np.float32), grid, epsg)          # GeoTIFF, as surveys deliver them
    geotiff.write(d / f"dsm_{T1_DATE[:4]}.tif", surface(1), grid, epsg)
    geotiff.write(d / f"dsm_{T2_DATE[:4]}.tif", surface(2), grid, epsg)
    geo.write_json(d / "elevation_meta.json", {
        "epsg": epsg, "grid": grid, "dtm": "dtm.tif", "simulated": True,
        "epochs": {"t1": {"date": T1_DATE, "dsm": f"dsm_{T1_DATE[:4]}.tif"}, "t2": {"date": T2_DATE, "dsm": f"dsm_{T2_DATE[:4]}.tif"}},
        "note": "SIMULATED for this prototype. Building-level DSMs come from drone or LiDAR surveys, which are not public; "
                "open DEMs (Bhuvan CartoDEM, SRTM) are 10-30 m and cannot resolve buildings. Footprints are the real ones; "
                "heights, trees, terrain and the earlier survey are generated."}, indent=2)

    truth["changes"] = ([{"type": "new", "plot_id": b["plot"]} for b in new] +
                        [{"type": "extension", "plot_id": b["plot"]} for b in ext] +
                        [{"type": "new_floor", "plot_id": b["plot"]} for b in flr] +
                        [{"type": "demolished", "plot_id": b["plot"]} for b in gone])
    truth["building_heights"] = [{"plot_id": b["plot"], "height": b["h"], "floors": b["floors"], "wkt": b["geom"].wkt}
                                 for b in B if b["plot"]]
    truth["unassessed_new"] = [b["plot"] for b in first]
    geo.write_json(tpath, truth)
    print(f"Elevation (simulated): {W}x{H} px at {RES} m; {len(B)} buildings; planted {len(new)} new, {len(ext)} extensions, "
          f"{len(flr)} added floors, {len(gone)} demolitions between {T1_DATE} and {T2_DATE}")
    return truth


def _shrink(g, keep):
    """The same building before an extension: cut back along its long axis to `keep` of its area."""
    r = np.asarray(g.minimum_rotated_rectangle.exterior.coords)[:4]
    e = [np.linalg.norm(r[i + 1] - r[i]) for i in range(3)]
    i = int(np.argmax(e[:2]))
    a, b, c = r[i], r[i + 1], r[(i + 2) % 4]
    cut = Polygon([a, a + (b - a) * keep, a + (b - a) * keep + (c - b), c - (b - a)])
    part = g.intersection(cut)
    return geo.largest_polygon(part) if not part.is_empty else g


if __name__ == "__main__":
    import sys
    from . import config, deptdata
    ws = sys.argv[1] if len(sys.argv) > 1 else "jayanagar"
    root = config.ws_paths(ws)["root"]
    simulate(root)
    deptdata.add(root, seed=8)
