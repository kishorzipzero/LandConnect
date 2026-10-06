"""Exports: any published layer to GeoJSON, CSV (WKT), KML, GeoPackage or a zipped Shapefile, and the
harmonised parcels as an ISO 19152 LADM-aligned GeoPackage (spatial units, parties, rights, basic
administrative units, sources)."""
import io
import json
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

FORMATS = {"geojson": ("application/geo+json", "geojson"), "csv": ("text/csv", "csv"),
           "kml": ("application/vnd.google-earth.kml+xml", "kml"), "gpkg": ("application/geopackage+sqlite3", "gpkg"),
           "shp": ("application/zip", "zip")}


def _gdf(result, layer):
    feats = result["layers"][layer]["features"]
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326") if feats else gpd.GeoDataFrame(geometry=[], crs=4326)
    for c in gdf.columns:
        if c != "geometry" and gdf[c].map(lambda v: isinstance(v, (list, dict))).any():
            gdf[c] = gdf[c].map(lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    return gdf


def export_layer(result, layer, fmt):
    if fmt == "geojson":
        return json.dumps(result["layers"][layer], ensure_ascii=False).encode("utf-8")
    gdf = _gdf(result, layer)
    if fmt == "csv":
        df = pd.DataFrame(gdf.drop(columns="geometry"))
        df["wkt"] = gdf.geometry.to_wkt()
        return df.to_csv(index=False).encode("utf-8-sig")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        if fmt == "kml":
            pyogrio.write_dataframe(gdf, td / f"{layer}.kml", driver="KML")
            return (td / f"{layer}.kml").read_bytes()
        if fmt == "gpkg":
            pyogrio.write_dataframe(gdf, td / f"{layer}.gpkg", layer=layer, driver="GPKG")
            return (td / f"{layer}.gpkg").read_bytes()
        if fmt == "shp":
            # Shapefile: 10-character field names, one geometry type per file
            names, used = {}, set()
            for c in gdf.columns:
                if c == "geometry":
                    continue
                n, k = c[:10], 1
                while n in used:
                    n, k = f"{c[:8]}_{k}", k + 1
                names[c] = n
                used.add(n)
            gdf = gdf.rename(columns=names)
            pyogrio.write_dataframe(gdf, td / f"{layer}.shp", driver="ESRI Shapefile", encoding="UTF-8")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in td.glob(f"{layer}.*"):
                    z.write(f, f.name)
            return buf.getvalue()
    raise ValueError(fmt)


def export_ladm(result):
    """ISO 19152 LADM core classes: LA_SpatialUnit, LA_BAUnit, LA_Party, LA_RRR, LA_Source."""
    parcels = result["layers"]["parcels"]["features"]
    su, ba, party, rrr, src = [], [], [], [], []
    party_ids = {}
    for f in parcels:
        p = f["properties"]
        suid = f"SU-{p['plot_id']}"
        baid = f"BA-{p['khata'] or p['plot_id']}"
        su.append({"su_id": suid, "label": p["plot_id"], "area_m2": p["area_sqm"], "land_use": p.get("land_use"),
                   "quality_score": p["score"], "quality_status": p["status"],
                   "survey_method": "GNSS-controlled" if p.get("gnss_vertices") else "digitised cadastral, georeferenced",
                   "ba_unit_id": baid, "geometry": f["geometry"]})
        ba.append({"ba_unit_id": baid, "name": f"Khata {p['khata']}" if p.get("khata") else p["plot_id"],
                   "type": "basicPropertyUnit", "municipal_pid": p.get("municipal_pid")})
        owner = p.get("resolved_owner")
        if owner:
            pid = party_ids.setdefault(owner, f"P-{len(party_ids) + 1:05d}")
            if len(party) < len(party_ids):
                party.append({"party_id": pid, "name": owner, "type": "naturalPerson"})
            rrr.append({"rrr_id": f"R-{p['plot_id']}", "type": "ownership", "party_id": pid, "ba_unit_id": baid,
                        "share": "1/1", "source": p.get("owner_source"),
                        "status": "disputed" if p.get("open_conflicts") else "confirmed"})
    for i, d in enumerate(result.get("datasets", [])):
        if d.get("role") not in ("raster_meta", "unknown"):
            src.append({"source_id": f"S-{i + 1:03d}", "file": d["name"], "type": d.get("role_label"),
                        "format": d.get("format"), "sha256": d.get("sha256")})
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "landconnect_ladm.gpkg"
        gdf = gpd.GeoDataFrame.from_features([{"type": "Feature", "geometry": r.pop("geometry"), "properties": r}
                                              for r in su], crs="EPSG:4326")
        pyogrio.write_dataframe(gdf, path, layer="la_spatialunit", driver="GPKG")
        for name, rows in (("la_baunit", ba), ("la_party", party), ("la_rrr", rrr), ("la_source", src)):
            if rows:
                pyogrio.write_dataframe(pd.DataFrame(rows), path, layer=name, driver="GPKG", append=False)
        return path.read_bytes()
