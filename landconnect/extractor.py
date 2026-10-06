"""GeoAI feature extraction from drone ORI + DSM/DTM.

Buildings are found by fusing two signals:
  * elevation  - normalised DSM (DSM - DTM) above 2.5 m = something standing on the ground
  * spectral   - excess-green index on the orthophoto removes trees (tall but green)
The mask is cleaned with morphology, traced into polygons, regularised to rectangles when
the shape is rectangular (typical of Indian urban plots), and given a height from the nDSM.

This classical pipeline runs anywhere with no GPU. `extract_buildings` is the single entry
point, so a deep model (SAM / U-Net) can replace it without touching the rest of the system.
"""
import cv2
import numpy as np
from shapely.geometry import Polygon

MIN_HEIGHT_M = 2.5
MIN_AREA_M2 = 12.0
EXG_TREE = 0.06


def load_epoch(raw_dir, meta, key):
    ep = meta["epochs"][key]
    rgb = cv2.cvtColor(cv2.imread(str(raw_dir / ep["ortho"])), cv2.COLOR_BGR2RGB)
    dsm = np.load(raw_dir / ep["dsm"])
    dtm = np.load(raw_dir / meta["dtm"])
    return rgb, dsm, dtm


def ndsm_on_ortho(dsm, dtm, ortho_shape):
    ndsm = (dsm - dtm).astype(np.float32)
    return cv2.resize(ndsm, (ortho_shape[1], ortho_shape[0]), interpolation=cv2.INTER_NEAREST)


def extract_buildings(rgb, dsm, dtm, meta):
    om = meta["ortho"]
    res, x0, y_top = om["res"], om["x0"], om["y_top"]
    ndsm = ndsm_on_ortho(dsm, dtm, rgb.shape)

    f = rgb.astype(np.float32)
    s = f.sum(axis=2) + 1e-6
    exg = (2 * f[..., 1] - f[..., 0] - f[..., 2]) / s
    elevated = ndsm > MIN_HEIGHT_M
    vegetation = cv2.GaussianBlur(exg, (0, 0), 1.0) > EXG_TREE
    mask = (elevated & ~vegetation).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = []
    min_px = MIN_AREA_M2 / (res * res)
    for c in contours:
        area_px = cv2.contourArea(c)
        if area_px < min_px:
            continue
        rect = cv2.minAreaRect(c)
        rect_area = rect[1][0] * rect[1][1]
        rectangularity = area_px / rect_area if rect_area else 0
        # keep the traced outline (buildings follow slanted plot lines); only square it off when the
        # trace is noisy (many vertices) but the blob is clearly rectangular
        pts = cv2.approxPolyDP(c, 1.5, True).reshape(-1, 2).astype(np.float32)
        regularised = False
        if len(pts) > 6 and rectangularity > 0.9:
            pts = cv2.boxPoints(rect)
            regularised = True
        if len(pts) < 3:
            continue
        comp = np.zeros(mask.shape, np.uint8)
        cv2.drawContours(comp, [c], -1, 1, -1)
        heights = ndsm[comp.astype(bool)]
        height = float(np.median(heights))
        # pixel centre -> map coordinates
        xy = np.column_stack([x0 + (pts[:, 0] + 0.5) * res, y_top - (pts[:, 1] + 0.5) * res])
        poly = Polygon(xy)
        if not poly.is_valid:
            poly = poly.buffer(0)
        # model confidence: clean rectangular shape, consistent roof height, not tiny
        roof_consistency = float(np.clip(1 - np.std(heights) / max(height, 1), 0, 1))
        score = 0.45 * min(rectangularity, 1) + 0.35 * roof_consistency + 0.2 * min(poly.area / 60, 1)
        out.append({"geom": poly, "height": round(height, 2), "floors": max(1, int(round((height - 0.5) / 3.2))),
                    "score": round(score, 3), "regularised": regularised, "area": round(poly.area, 1)})
    return out, {"elevated_px": int(elevated.sum()), "vegetation_px": int((elevated & vegetation).sum())}


def height_map_png(dsm, dtm, path, max_h=18.0):
    """Colour-coded nDSM for the map (transparent where ground)."""
    ndsm = np.clip(dsm - dtm, 0, max_h)
    col = cv2.applyColorMap((ndsm / max_h * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    alpha = np.where(ndsm > 1.5, 200, 0).astype(np.uint8)
    cv2.imwrite(str(path), np.dstack([col, alpha]))
