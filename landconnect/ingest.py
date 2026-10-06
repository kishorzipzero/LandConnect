"""Ingestion: read whatever the departments hand over, detect format, CRS and role.

Vector formats go through GDAL/OGR (pyogrio), so Shapefile, KML, GPX, GeoPackage, GML, FlatGeobuf,
MapInfo, GeoJSON and GeoJSON-lines all work; the CRS comes from the file itself (.prj, KML = WGS 84,
GeoJSON crs member). CSVs are read directly, with lat/lon columns turned into points.
"""
import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyogrio
from shapely.geometry import Point, shape

from . import geo

GDAL_EXT = {".shp", ".kml", ".kmz", ".gpx", ".gpkg", ".gml", ".fgb", ".tab", ".geojsonl", ".geojsonseq",
            ".geojsons", ".sqlite", ".dxf"}
GEOJSON_EXT = {".geojson", ".json"}
TABLE_EXT = {".csv"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
SIDE_CAR = {".dbf", ".shx", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".xml", ".md", ".txt"}
FORMAT_NAMES = {".shp": "ESRI Shapefile", ".kml": "KML", ".kmz": "KMZ", ".gpx": "GPX", ".gpkg": "GeoPackage",
                ".gml": "GML", ".fgb": "FlatGeobuf", ".tab": "MapInfo TAB", ".geojsonl": "GeoJSON lines",
                ".geojsonseq": "GeoJSON lines", ".geojsons": "GeoJSON lines", ".sqlite": "SpatiaLite",
                ".dxf": "AutoCAD DXF", ".geojson": "GeoJSON", ".json": "GeoJSON", ".csv": "CSV",
                ".png": "Image", ".jpg": "Image", ".tif": "GeoTIFF", ".npy": "Raster array"}

# keyword -> role scoring (file name + column names)
ROLE_KEYWORDS = {
    "cadastral": ["cadastral", "cadastre", "fmb", "tippan", "village map", "survey_no", "sub_div", "sheet", "hissa_no"],
    "cadastral_alt": ["ncscm", "coastal", "czmp", "second_survey", "alt_cadastral"],
    "revenue": ["revenue", "ror", "rtc", "pahani", "khasra", "khata", "khatha", "jamabandi", "pattadar", "patta", "chitta"],
    "municipal": ["municipal", "property", "tax", "ulb", "corporation", "bbmp", "pid", "occupant", "gcc", "assessment"],
    "ai_buildings": ["ai_", "ml_", "microsoft", "machine", "extracted", "detection", "predicted"],
    "footprints": ["building", "footprint", "open_buildings", "area_in_meters", "osm_buildings"],
    "utility": ["utility", "water", "sewer", "pipe", "network", "asset", "diameter"],
    "gnss": ["gnss", "cors", "rtk", "dgps", "control", "fix_type", "h_accuracy"],
    "ground_truth": ["ground_truth", "ground truth", "field", "verified", "visit", "gt_"],
    "roads": ["road", "highway", "street"],
    "electricity": ["bescom", "electric", "discom", "tariff", "rr_no", "sanctioned_load", "power supply", "tangedco", "tneb"],
    "water": ["bwssb", "water_connection", "water connection", "connection_type", "meter_no", "consumer_no", "cmwssb", "metrowater"],
    "gas": ["png", "gas_connection", "city gas", "bp_no", "gail", "igl", "torrent"],
    "permissions": ["permission", "sanction", "approved_floors", "building_plan", "approval_date"],
    "deeds": ["deed", "igr", "registered", "buyer", "seller", "document_no", "consideration"],
    "poi": ["poi", "landmark", "amenity", "landuse"],
}
ROLE_LABELS = {
    "cadastral": "Existing cadastral map", "cadastral_alt": "Second cadastral source", "revenue": "Revenue records (RoR)", "municipal": "Municipal GIS layer",
    "footprints": "Building footprints", "ai_buildings": "AI-extracted building footprints",
    "utility": "Utility network", "gnss": "GNSS / CORS survey", "ground_truth": "Ground Truthing (GT)",
    "roads": "Road network", "poi": "Landmarks & land use",
    "electricity": "Electricity consumers (DISCOM)", "water": "Water connections", "gas": "Piped gas connections",
    "permissions": "Building permissions (Town Planning)", "deeds": "Registered deeds (IGR)", "ortho": "Drone ORI", "dsm": "DSM", "dtm": "DTM",
    "raster_meta": "Raster georeferencing", "unknown": "Unrecognised",
}


@dataclass
class Dataset:
    name: str
    path: Path
    format: str
    role: str = "unknown"
    role_confidence: float = 0.0
    records: list = field(default_factory=list)   # list of {"geom": shapely|None, "props": {...}}
    columns: list = field(default_factory=list)
    geom_type: str = ""
    declared_crs: str | None = None               # "EPSG:xxxx" or WKT as found in the file
    crs: dict = field(default_factory=dict)       # filled by crs_engine.detect
    schema: dict = field(default_factory=dict)    # filled by schema_mapper
    sha256: str = ""
    raster: dict = field(default_factory=dict)     # GeoTIFF header: size, bands, resolution, EPSG

    def summary(self):
        return {"name": self.name, "format": self.format, "role": self.role,
                "role_label": ROLE_LABELS.get(self.role, self.role), "role_confidence": round(self.role_confidence, 2),
                "features": len(self.records), "geom_type": self.geom_type, "columns": self.columns,
                "crs": {k: v for k, v in self.crs.items() if k != "src"}, "sha256": self.sha256,
                **({"raster": self.raster} if self.raster else {})}


def _clean(v):
    if v is None:
        return None
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _latlon_columns(cols, rows):
    lat = next((c for c in cols if c.lower() in ("lat", "latitude", "y_lat")), None)
    lon = next((c for c in cols if c.lower() in ("lon", "lng", "long", "longitude", "x_lon")), None)
    if lat and lon:
        try:
            vals = [(float(r[lat]), float(r[lon])) for r in rows[:20] if r[lat] and r[lon]]
            if vals and all(-90 <= a <= 90 and -180 <= b <= 180 for a, b in vals):
                return lat, lon
        except ValueError:
            pass
    return None, None


def _infer_role(ds):
    if ds.format == "Image":
        return "ortho", 0.9
    text = ds.name.lower() + " " + " ".join(c.lower() for c in ds.columns)
    scores = {role: sum(1 for kw in kws if kw in text) for role, kws in ROLE_KEYWORDS.items()}
    if ds.geom_type.endswith("LineString"):
        scores["utility"] += 0.5
        scores["roads"] += 0.5
    if ds.geom_type == "Point":
        scores["gnss"] += 0.5
        scores["ground_truth"] += 0.5
        scores["utility"] = 0                     # point records are connections, not networks
    if ds.format == "CSV" and not ds.geom_type:
        scores["revenue"] += 1
    if scores["ai_buildings"] and scores["footprints"]:
        scores["ai_buildings"] += 1                    # an AI building layer is still a building layer
    best = max(scores, key=scores.get)
    total = sum(scores.values())
    if scores[best] == 0:
        return "unknown", 0.0
    return best, scores[best] / total


KML_HOUSEKEEPING = {"id", "tessellate", "extrude", "visibility", "draworder", "icon", "altitudemode", "begin",
                    "end", "timestamp", "snippet", "description"}


def _read_gdal(path):
    """All non-empty layers of any OGR-readable file -> (records, columns, geom_type, declared_crs)."""
    recs, cols, types, crs = [], [], set(), None
    for name, _gt in pyogrio.list_layers(path):
        gdf = pyogrio.read_dataframe(path, layer=name)
        if len(gdf) == 0:
            continue
        if crs is None and gdf.crs is not None:
            epsg = gdf.crs.to_epsg(min_confidence=70)
            crs = f"EPSG:{epsg}" if epsg else gdf.crs.to_wkt()
        fcols = [c for c in gdf.columns if c != "geometry" and not gdf[c].isna().all()
                 and not (path.suffix.lower() in (".kml", ".kmz") and c.lower() in KML_HOUSEKEEPING)]
        cols += [c for c in fcols if c not in cols]
        for row in gdf.itertuples(index=False):
            d = row._asdict()
            g = d.pop("geometry", None)
            if g is not None and not g.is_empty:
                g = geo.force_2d(g)
                types.add(g.geom_type)
            recs.append({"geom": g, "props": {c: _clean(d.get(c)) for c in fcols}})
        if path.suffix.lower() == ".gpx":       # waypoints are the payload; tracks/routes ignored
            break
    return recs, cols, "/".join(sorted(types)), crs


def load_dataset(path):
    path = Path(path)
    ext = path.suffix.lower()
    name = path.name
    if name == "raster_meta.json":
        return Dataset(name, path, "JSON", role="raster_meta", role_confidence=1.0)
    if ext in SIDE_CAR or name == "workspace.json":
        return None
    fmt = FORMAT_NAMES.get(ext, ext.lstrip(".").upper())
    if ext in GEOJSON_EXT:
        data = geo.read_json(path)
        feats = data.get("features", [])
        recs = [{"geom": geo.force_2d(shape(f["geometry"])) if f.get("geometry") else None,
                 "props": f.get("properties") or {}} for f in feats]
        ds = Dataset(name, path, fmt, records=recs)
        ds.declared_crs = (data.get("crs") or {}).get("properties", {}).get("name")
        ds.columns = list(dict.fromkeys(k for r in recs[:50] for k in r["props"]))
        ds.geom_type = "/".join(sorted({r["geom"].geom_type for r in recs if r["geom"] is not None}))
    elif ext in GDAL_EXT:
        recs, cols, gtype, crs = _read_gdal(path)
        ds = Dataset(name, path, fmt, records=recs, columns=cols, geom_type=gtype, declared_crs=crs)
    elif ext in TABLE_EXT:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        cols = list(rows[0].keys()) if rows else []
        lat, lon = _latlon_columns(cols, rows)
        recs = []
        for r in rows:
            g = None
            if lat and r.get(lat) and r.get(lon):
                g = Point(float(r[lon]), float(r[lat]))
            recs.append({"geom": g, "props": {k: v for k, v in r.items() if k not in (lat, lon)}})
        ds = Dataset(name, path, fmt, records=recs, columns=[c for c in cols if c not in (lat, lon)])
        if lat:
            ds.geom_type = "Point"
            ds.declared_crs = "EPSG:4326 (lat/lon columns)"
    elif ext in (".tif", ".tiff"):
        from . import geotiff
        try:
            inf = geotiff.info(path)
        except Exception:
            ds = Dataset(name, path, "Image")                 # plain TIFF without georeferencing
        else:
            low = name.lower()
            role = ("dtm" if any(t in low for t in ("dtm", "dem", "terrain", "bare")) else
                    "dsm" if inf["kind"] == "elevation" else "ortho")
            ds = Dataset(name, path, "GeoTIFF", role=role, role_confidence=0.9, raster=inf,
                         declared_crs=f"EPSG:{inf['epsg']}" if inf["epsg"] else None)
            ds.sha256 = geo.file_sha256(path)
            return ds
    elif ext in IMAGE_EXT:
        ds = Dataset(name, path, "Image")
    elif ext == ".npy":
        role = "dtm" if "dtm" in name.lower() else "dsm"
        return Dataset(name, path, "Raster array", role=role, role_confidence=0.9, sha256=geo.file_sha256(path))
    else:
        return None
    ds.sha256 = geo.file_sha256(path)
    ds.role, ds.role_confidence = _infer_role(ds)
    return ds


def load_folder(folder):
    out = []
    for p in sorted(Path(folder).iterdir()):
        if p.is_file() and not p.name.startswith("."):
            ds = load_dataset(p)
            if ds is not None:
                out.append(ds)
    return out


def sample_xy(ds, n=200):
    pts = []
    for r in ds.records:
        g = r["geom"]
        if g is None or g.is_empty:
            continue
        c = g.representative_point() if g.geom_type != "Point" else g
        pts.append((c.x, c.y))
        if len(pts) >= n:
            break
    return np.asarray(pts) if pts else np.empty((0, 2))
