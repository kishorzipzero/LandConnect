"""Small geometry / GeoJSON helpers shared by all modules."""
import hashlib
import json
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import mapping, shape

_transformers = {}


def transformer(src_epsg, dst_epsg):
    key = (src_epsg, dst_epsg)
    if key not in _transformers:
        _transformers[key] = Transformer.from_crs(src_epsg, dst_epsg, always_xy=True)
    return _transformers[key]


def force_2d(geom):
    return shapely.force_2d(geom) if geom is not None and shapely.has_z(geom) else geom


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def reproject(geom, src_epsg, dst_epsg):
    """src/dst: EPSG code (int) or any CRS string pyproj understands (e.g. WKT from a .prj)."""
    if src_epsg == dst_epsg:
        return geom
    tr = transformer(src_epsg, dst_epsg)

    def f(xy):
        x, y = tr.transform(xy[:, 0], xy[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(geom, f)


def mean_boundary_offset(a, b, step=1.0):
    """Symmetric mean distance (m) between two polygon boundaries, sampled every `step` metres."""
    def one(p, q):
        ring = p.exterior
        pts = shapely.line_interpolate_point(ring, np.arange(0, ring.length, step))
        return float(np.mean(shapely.distance(pts, q.exterior))) if len(pts) else 0.0
    return (one(a, b) + one(b, a)) / 2


def apply_affine(geom, M):
    """M is a 2x3 matrix: [x', y'] = M @ [x, y, 1]."""
    M = np.asarray(M)
    return shapely.transform(geom, lambda xy: xy @ M[:, :2].T + M[:, 2])


def round_geom(geom, decimals):
    return shapely.transform(geom, lambda xy: np.round(xy, decimals))


def ring_points(poly):
    """Exterior vertices of a polygon without the closing duplicate."""
    return np.asarray(poly.exterior.coords)[:-1]


def largest_polygon(geom):
    """Largest polygon inside any (possibly nested) geometry; an empty Polygon if there is none."""
    if geom.geom_type == "Polygon":
        return geom
    polys = [g for g in shapely.get_parts(shapely.get_parts(geom)) if g.geom_type == "Polygon" and not g.is_empty]
    if not polys:  # nested collections (GeometryCollection of MultiPolygons, ...)
        polys = [g for part in shapely.get_parts(geom) for g in shapely.get_parts(part)
                 if g.geom_type == "Polygon" and not g.is_empty]
    return max(polys, key=lambda g: g.area) if polys else shapely.Polygon()


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data, indent=None):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)


def write_feature_collection(path, features, crs_name=None, decimals=3):
    """features: iterable of (shapely geometry, properties dict), coordinates already in the file's CRS."""
    fc = {"type": "FeatureCollection", "features": []}
    if crs_name:
        fc["crs"] = {"type": "name", "properties": {"name": crs_name}}
    for geom, props in features:
        fc["features"].append({
            "type": "Feature",
            "properties": props,
            "geometry": mapping(round_geom(geom, decimals)),
        })
    write_json(path, fc)


def to_geojson_features(items, src_epsg, geom_key="geom", props_fn=None, decimals=7):
    """Convert internal records (dicts holding a shapely geometry in src_epsg) to WGS84 GeoJSON features."""
    feats = []
    for it in items:
        g = it[geom_key]
        if g is None or g.is_empty:
            continue
        g84 = round_geom(reproject(g, src_epsg, 4326), decimals)
        props = props_fn(it) if props_fn else {k: v for k, v in it.items() if k != geom_key}
        feats.append({"type": "Feature", "properties": props, "geometry": mapping(g84)})
    return {"type": "FeatureCollection", "features": feats}


def feature_geoms(fc):
    return [(shape(f["geometry"]) if f.get("geometry") else None, f.get("properties") or {})
            for f in fc.get("features", [])]
