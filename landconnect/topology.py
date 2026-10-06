"""Automated topology correction for the parcel fabric.

Order of operations:
  1. repair invalid polygons (self-intersecting "bow-ties" from vertex-order digitising errors)
  2. drop duplicate parcels (same plot ID digitised twice)
  3. snap corners to GNSS control points (centimetre-accurate, highest reliability)
  4. merge corners shared by neighbouring parcels (the same corner digitised slightly differently)
  5. resolve overlaps (the overlap goes to the more reliable parcel)
  6. fill thin gaps between neighbours (a gap goes to the neighbour with the longest shared edge)
  7. remove slivers
"""
import numpy as np
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree
from sklearn.neighbors import NearestNeighbors

from . import geo

SNAP_SAME_PLOT_M = 2.5
SNAP_OTHER_PLOT_M = 0.8
CORNER_MERGE_M = 1.2
MAX_GAP_M2 = 25.0


def _fix_bowtie(poly):
    """Simple 4/5-corner plots: re-order vertices around the centroid (a swapped-vertex digitising
    error). Anything more complex: OGC make_valid, keeping the largest valid part."""
    pts = geo.ring_points(poly)
    if len(pts) <= 5:
        c = pts.mean(axis=0)
        order = np.argsort(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))
        cand = Polygon(pts[order])
        if cand.is_valid and cand.area > 0.9 * cand.convex_hull.area:
            return cand, "vertex order repaired"
    fixed = geo.largest_polygon(shapely.make_valid(poly))
    if fixed.area < 0.8 * abs(Polygon(pts).buffer(0).area or fixed.area):
        fixed = geo.largest_polygon(poly.buffer(0))
    return fixed, "make_valid"


def clean(parcels, gnss):
    """parcels: [{"plot_id", "geom"}] in UTM. gnss: [{"plot_id", "pt"}] in UTM. Mutates & returns parcels."""
    rep = {"invalid_repaired": [], "duplicates_removed": [], "gnss_snapped_vertices": 0,
           "corners_merged": 0, "overlaps_resolved": 0, "overlap_area_m2": 0.0,
           "gaps_filled": 0, "gap_area_m2": 0.0, "slivers_removed": 0}

    # 1. invalid geometry
    for p in parcels:
        p["topo_flags"] = []
        if not p["geom"].is_valid:
            p["geom"], how = _fix_bowtie(p["geom"])
            p["topo_flags"].append(f"self-intersection repaired ({how})")
            rep["invalid_repaired"].append(p["plot_id"])
        p["geom"] = shapely.orient_polygons(p["geom"]) if hasattr(shapely, "orient_polygons") else p["geom"]
        p["area_before"] = p["geom"].area

    # 2. duplicates (keep the first, which is the originally digitised one)
    seen, kept = set(), []
    for p in parcels:
        if p["plot_id"] in seen:
            rep["duplicates_removed"].append(p["plot_id"])
            continue
        seen.add(p["plot_id"])
        kept.append(p)
    parcels = kept
    dup = set(rep["duplicates_removed"])
    for p in parcels:
        if p["plot_id"] in dup:
            p["topo_flags"].append("duplicate record removed")

    # vertex table
    verts = [geo.ring_points(p["geom"]).copy() for p in parcels]
    fixed = [np.zeros(len(v), bool) for v in verts]

    # 3. GNSS snapping
    if gnss:
        gp = np.asarray([g["pt"] for g in gnss])
        gid = [g["plot_id"] for g in gnss]
        nn = NearestNeighbors(n_neighbors=min(4, len(gp))).fit(gp)
        for pi, p in enumerate(parcels):
            dist, idx = nn.kneighbors(verts[pi])
            for vi in range(len(verts[pi])):
                for d, k in zip(dist[vi], idx[vi]):
                    lim = SNAP_SAME_PLOT_M if gid[k] == p["plot_id"] else SNAP_OTHER_PLOT_M
                    if d <= lim:
                        verts[pi][vi] = gp[k]
                        fixed[pi][vi] = True
                        rep["gnss_snapped_vertices"] += 1
                        break
            p["gnss_vertices"] = int(fixed[pi].sum())

    # 4. merge shared corners (only one vertex per parcel per cluster)
    owner = np.concatenate([[i] * len(v) for i, v in enumerate(verts)])
    allv = np.concatenate(verts)
    allfix = np.concatenate(fixed)
    nn = NearestNeighbors(radius=CORNER_MERGE_M).fit(allv)
    graph = nn.radius_neighbors_graph(allv, mode="connectivity").tocoo()
    keep = owner[graph.row] != owner[graph.col]
    g = coo_matrix((np.ones(keep.sum()), (graph.row[keep], graph.col[keep])), shape=(len(allv), len(allv)))
    ncomp, labels = connected_components(g, directed=False)
    newv = allv.copy()
    for lab in np.unique(labels):
        members = np.where(labels == lab)[0]
        if len(members) < 2 or len(set(owner[members])) != len(members):
            continue
        centre = allv[members].mean(axis=0)
        # a chain of near-by vertices is not one corner: never move a vertex more than the merge radius
        if len(members) > 6 or np.max(np.linalg.norm(allv[members] - centre, axis=1)) > CORNER_MERGE_M:
            continue
        fx = members[allfix[members]]
        newv[members] = allv[fx].mean(axis=0) if len(fx) else centre
        rep["corners_merged"] += 1
    off = 0
    for pi, p in enumerate(parcels):
        n = len(verts[pi])
        poly = Polygon(newv[off:off + n])
        off += n
        if not poly.is_valid:
            poly = geo.largest_polygon(shapely.make_valid(poly))
        # never accept a vertex move that destroys the parcel
        p["geom"] = poly if poly.area > 0.9 * p["area_before"] else p["geom"]

    # 5. overlaps: the more reliable parcel (more GNSS corners, then larger) keeps the overlap
    def prio(i):
        return (parcels[i].get("gnss_vertices", 0), parcels[i]["geom"].area)

    geoms = [p["geom"] for p in parcels]
    tree = STRtree(geoms)
    for i in range(len(parcels)):
        for j in tree.query(geoms[i]):
            j = int(j)
            if j <= i:
                continue
            inter = geoms[i].intersection(geoms[j])
            if inter.area > 0.01:
                win, lose = (i, j) if prio(i) >= prio(j) else (j, i)
                diff = geoms[lose].difference(geoms[win])
                if diff.geom_type != "Polygon":
                    rep["slivers_removed"] += max(0, len(getattr(diff, "geoms", [])) - 1)
                geoms[lose] = geo.largest_polygon(diff)
                rep["overlaps_resolved"] += 1
                rep["overlap_area_m2"] += inter.area
                parcels[lose]["topo_flags"].append(f"overlap {inter.area:.2f} m² given to {parcels[win]['plot_id']}")

    # 6. thin gaps between neighbours
    union = unary_union(geoms)
    closed = union.buffer(1.5, join_style="mitre").buffer(-1.5, join_style="mitre")
    gaps = closed.difference(union)
    gap_parts = getattr(gaps, "geoms", [gaps]) if not gaps.is_empty else []
    tree = STRtree(geoms)
    for gap in gap_parts:
        if gap.geom_type != "Polygon" or gap.area < 0.01 or gap.area > MAX_GAP_M2:
            continue
        if gap.area / max(gap.length, 1e-6) > 0.6:      # not thin: probably a real open space
            continue
        near = [int(k) for k in tree.query(gap.buffer(0.05))]
        if not near:
            continue
        best = max(near, key=lambda k: geoms[k].buffer(0.05).intersection(gap).area +
                   geoms[k].boundary.intersection(gap.buffer(0.05)).length)
        merged = geo.largest_polygon(unary_union([geoms[best], gap]).buffer(0.001).buffer(-0.001))
        if merged.area > geoms[best].area:
            geoms[best] = merged
            rep["gaps_filled"] += 1
            rep["gap_area_m2"] += gap.area

    # 7. slivers / final validity
    for p, gm in zip(parcels, geoms):
        if not gm.is_valid:
            gm = shapely.make_valid(gm)
        if gm.geom_type != "Polygon":
            rep["slivers_removed"] += max(0, len(getattr(gm, "geoms", [])) - 1)
        p["geom"] = geo.largest_polygon(gm)
        change = abs(p["geom"].area - p["area_before"]) / max(p["area_before"], 1)
        t = 1.0 if change < 0.01 else 0.9 if change < 0.03 else 0.75
        if any("self-intersection" in f for f in p["topo_flags"]):
            t = min(t, 0.7)
        if any("duplicate" in f for f in p["topo_flags"]):
            t = min(t, 0.85)
        p["T"] = t
        p["area_change_pct"] = round(change * 100, 2)

    collapsed = [p["plot_id"] for p in parcels if p["geom"].is_empty or p["geom"].area < 1.0]
    if collapsed:
        rep["collapsed_removed"] = collapsed
        parcels = [p for p in parcels if p["plot_id"] not in collapsed]
    rep["overlap_area_m2"] = round(rep["overlap_area_m2"], 2)
    rep["gap_area_m2"] = round(rep["gap_area_m2"], 2)
    final = [p["geom"] for p in parcels]
    tree = STRtree(final)
    rep["overlaps_remaining"] = sum(1 for i in range(len(final)) for j in tree.query(final[i])
                                    if int(j) > i and final[i].intersection(final[int(j)]).area > 0.01)
    rep["invalid_remaining"] = sum(1 for g_ in final if not g_.is_valid)
    return parcels, rep
