"""Geo-referencing and coordinate transformation engine.

1. detect()       - work out each dataset's CRS (declared, or inferred from coordinate ranges)
2. reproject      - bring every known CRS to the project CRS (UTM 43N)
3. georeference() - for maps in an UNKNOWN local grid (old cadastral sheets), estimate the
                    affine transform to UTM from GNSS control points: coarse fit on matching
                    parcels, then iterative closest-vertex refinement with outlier rejection.
"""
import math
import re

import numpy as np
from pyproj import CRS

from . import config, geo

_EPSG_RE = re.compile(r"EPSG:{1,2}(\d+)", re.I)
_KIND = {4326: "geographic (WGS 84)", 3857: "Web Mercator", 32643: "UTM zone 43N", 32644: "UTM zone 44N",
         24378: "Kalianpur 1975 / India zone I", 7755: "WGS 84 / India NSF LCC"}


def _name(code):
    if code in _KIND:
        return _KIND[code]
    try:
        return CRS.from_epsg(code).name
    except Exception:
        return f"EPSG:{code}"


def detect(declared, xy):
    """Returns {"epsg", "src" (what to reproject from), "name", "method"}; epsg/src None = unknown grid."""
    if declared:
        m = _EPSG_RE.search(declared)
        if m:
            code = int(m.group(1))
            return {"epsg": code, "src": code, "name": _name(code), "method": "declared in file"}
        try:
            crs = CRS.from_user_input(declared)
            return {"epsg": crs.to_epsg(), "src": declared, "name": crs.name, "method": "declared in file (.prj / WKT)"}
        except Exception:
            pass
    if len(xy) == 0:
        return {"epsg": None, "src": None, "name": "no geometry", "method": "-"}
    x, y = xy[:, 0], xy[:, 1]
    if np.all(np.abs(x) <= 180) and np.all(np.abs(y) <= 90):
        return {"epsg": 4326, "src": 4326, "name": _KIND[4326], "method": "inferred from coordinate range (RFC 7946 default)"}
    if np.all((x > 160_000) & (x < 840_000)) and np.all((y > 0) & (y < 9_400_000)):
        return {"epsg": config.TARGET_EPSG, "src": config.TARGET_EPSG, "name": _name(config.TARGET_EPSG),
                "method": "inferred: UTM-like range, zone taken from project area"}
    if np.all(np.abs(x) > 1e6) and np.all(np.abs(x) <= 20_037_509):
        return {"epsg": 3857, "src": 3857, "name": _KIND[3857], "method": "inferred from coordinate range"}
    return {"epsg": None, "src": None, "name": "unknown local grid", "method": "no CRS and coordinates match no known system "
            "-> georeference with GNSS control points"}


# ----------------------------------------------------------------------------- affine fitting
def fit_affine(src, dst):
    A = np.column_stack([src, np.ones(len(src))])
    params, *_ = np.linalg.lstsq(A, dst, rcond=None)
    return params.T  # 2x3


def apply(M, pts):
    return np.asarray(pts) @ M[:, :2].T + M[:, 2]


def decompose(M):
    a, b, c, d = M[0, 0], M[0, 1], M[1, 0], M[1, 1]
    sx, sy = math.hypot(a, c), math.hypot(b, d)
    return {"rotation_deg": round(math.degrees(math.atan2(c, a)), 4), "scale_x": round(sx, 6),
            "scale_y": round(sy, 6), "shear_deg": round(math.degrees(math.atan2(a * b + c * d, a * d - b * c)), 4),
            "tx": round(M[0, 2], 2), "ty": round(M[1, 2], 2)}


def _rmse(res):
    return float(np.sqrt(np.mean(np.sum(res ** 2, axis=1)))) if len(res) else float("nan")


def georeference(parcels, gnss, already_projected=False):
    """parcels: [{"plot_id", "geom_local"}]; gnss: [{"plot_id", "pt": (x, y) UTM}].

    Returns (M 2x3, naive translation, report). M maps the input coordinates to UTM. With
    already_projected=True the input is a map reprojected from a declared (legacy) CRS and the
    fit removes its residual datum / registration shift.
    """
    by_plot = {}
    for g in gnss:
        by_plot.setdefault(g["plot_id"], []).append(g["pt"])
    verts = {p["plot_id"]: geo.ring_points(p["geom_local"]) for p in parcels if p["plot_id"]}

    # 1. coarse: parcel "centres" (mean of corners) from both sources
    src, dst = [], []
    for pid, pts in by_plot.items():
        if pid in verts and len(pts) >= 3:
            src.append(verts[pid].mean(axis=0))
            dst.append(np.mean(pts, axis=0))
    src, dst = np.asarray(src), np.asarray(dst)
    if len(src) < 3:
        raise ValueError("Need GNSS control on at least 3 cadastral parcels to georeference the local grid")
    naive_t = dst.mean(axis=0) - src.mean(axis=0)              # what "drag the layer into place" gives
    M = fit_affine(src, dst)

    # 2. refine: pair every GNSS point with its nearest vertex on the same plot, refit, reject outliers
    history = []
    for it in range(6):
        s_pts, d_pts = [], []
        for pid, pts in by_plot.items():
            if pid not in verts:
                continue
            cand_local = verts[pid]
            cand = apply(M, cand_local)
            for p in pts:
                d = np.linalg.norm(cand - p, axis=1)
                k = int(np.argmin(d))
                if d[k] < 4.0:
                    s_pts.append(cand_local[k])
                    d_pts.append(p)
        s_pts, d_pts = np.asarray(s_pts), np.asarray(d_pts)
        M_new = fit_affine(s_pts, d_pts)
        res = np.linalg.norm(apply(M_new, s_pts) - d_pts, axis=1)
        keep = res < max(3 * np.median(res), 0.5)
        M_new = fit_affine(s_pts[keep], d_pts[keep])
        history.append(_rmse(apply(M_new, s_pts[keep]) - d_pts[keep]))
        converged = np.allclose(M_new, M, atol=1e-7)
        M = M_new
        if converged:
            break

    naive_res = (s_pts if already_projected else s_pts + naive_t) - d_pts
    coarse_res = apply(fit_affine(src, dst), s_pts) - d_pts
    final_res = apply(M, s_pts[keep]) - d_pts[keep]
    first = "reprojected_only" if already_projected else "translation_only"
    report = {
        "method": ("Residual datum/registration shift removed with an affine fit on GNSS control"
                   if already_projected else
                   "Affine (6-parameter) least squares on GNSS control, iterative closest-vertex refinement"),
        "control_parcels": int(len(src)), "control_points_used": int(keep.sum()),
        "outliers_rejected": int((~keep).sum()), "iterations": len(history),
        "rmse_m": {first: round(_rmse(naive_res), 3), "coarse_affine": round(_rmse(coarse_res), 3),
                   "refined_affine": round(_rmse(final_res), 3)},
        "transform": decompose(M), "matrix": M.round(9).tolist(),
    }
    return M, naive_t, report
