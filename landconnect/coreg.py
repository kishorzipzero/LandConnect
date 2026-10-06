"""Layer co-registration: find and remove systematic shifts between layers.

Layers traced from different imagery or digitised on different control are often shifted as a
whole (e.g. Microsoft AI footprints vs OpenStreetMap in Jayanagar differ by ~3 m). Comparing them
without correcting the shift produces false encroachments and boundary disputes everywhere.

* offset_from_pairs      - median displacement of matched feature centroids (parcel <-> parcel)
* offset_by_containment  - shift of a building layer that best fits buildings inside the surveyed
                           (GNSS-controlled) parcel fabric; buildings should not straddle plot lines
Only shifts larger than MIN_SHIFT_M that also clearly improve the fit are applied.
"""
import numpy as np
import shapely
from shapely.strtree import STRtree

from . import geo

MIN_SHIFT_M = 0.3


def shift(geoms, dx, dy):
    return shapely.transform(np.asarray(geoms, dtype=object), lambda xy: xy + [dx, dy])


def offset_from_pairs(a_geoms, b_geoms):
    """Median (dx, dy) that moves b onto a, with its robust spread (MAD)."""
    if not a_geoms:
        return 0.0, 0.0, None
    ca = shapely.centroid(np.asarray(a_geoms, dtype=object))
    cb = shapely.centroid(np.asarray(b_geoms, dtype=object))
    d = np.column_stack([shapely.get_x(ca) - shapely.get_x(cb), shapely.get_y(ca) - shapely.get_y(cb)])
    med = np.median(d, axis=0)
    mad = np.median(np.linalg.norm(d - med, axis=1))
    return float(med[0]), float(med[1]), float(mad)


def _containment(pts, owner, n_pts, tree, dx, dy):
    """Mean share of each building's outline points that fall in the building's most common parcel."""
    moved = shapely.points(pts + [dx, dy])
    pi, parcel = tree.query(moved, predicate="within")
    if len(pi) == 0:
        return 0.0
    key = owner[pi].astype(np.int64) * 1_000_000 + parcel
    uk, cnt = np.unique(key, return_counts=True)
    best = np.zeros(len(n_pts))
    np.maximum.at(best, (uk // 1_000_000).astype(int), cnt)
    return float(np.mean(best / n_pts))


def offset_by_containment(buildings, parcels, search=4.0):
    """Coarse-to-fine grid search (1 m, 0.25 m, 0.1 m) for the shift that maximises how much of each
    building's outline lies inside a single parcel. Returns (dx, dy, fit_before, fit_after)."""
    bgeoms = [b for b in buildings if b is not None and not b.is_empty]
    if len(bgeoms) > 400:                     # a random 400 buildings pin down a whole-layer shift
        idx = np.random.default_rng(0).choice(len(bgeoms), 400, replace=False)
        bgeoms = [bgeoms[i] for i in sorted(idx)]
    if len(bgeoms) < 10 or len(parcels) < 5:
        return 0.0, 0.0, None, None
    shrunk = shapely.buffer(np.asarray(bgeoms, dtype=object), -0.3)      # keep samples off the wall line
    shrunk = np.where(shapely.is_empty(shrunk), np.asarray(bgeoms, dtype=object), shrunk)
    polys = np.asarray([geo.largest_polygon(g) for g in shrunk], dtype=object)
    rings = shapely.segmentize(shapely.get_exterior_ring(polys), 3.0)
    pts, owner = shapely.get_coordinates(rings, return_index=True)
    n_pts = np.bincount(owner, minlength=len(bgeoms)).astype(float)
    n_pts[n_pts == 0] = 1
    tree = STRtree(np.asarray(parcels, dtype=object))
    base = _containment(pts, owner, n_pts, tree, 0, 0)
    best = (base, 0.0, 0.0)
    for step, span in ((1.0, search), (0.25, 0.75), (0.1, 0.2)):
        cx, cy = best[1], best[2]
        for dx in np.arange(cx - span, cx + span + 1e-9, step):
            for dy in np.arange(cy - span, cy + span + 1e-9, step):
                sc = _containment(pts, owner, n_pts, tree, dx, dy)
                if sc > best[0] + 1e-9:
                    best = (sc, float(dx), float(dy))
    return round(best[1], 2), round(best[2], 2), round(base, 4), round(best[0], 4)
