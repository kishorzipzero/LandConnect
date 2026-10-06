"""Land Connect integration pipeline: ingest -> harmonise -> extract -> fuse -> validate -> publish.

    python -m landconnect.pipeline [workspace]      # default workspace: demo
"""
import os
import time
import traceback
import uuid
from pathlib import Path

import cv2
import numpy as np
from shapely import affinity
from shapely.strtree import STRtree

from . import (audit, change, config, conflicts, confidence, coreg, crs_engine, db, departments, elevation, evaluate,
               extractor, geo, ingest, matcher, records, review, schema_mapper, topology)

STEPS = [
    ("ingest", "Ingest datasets", "Read every file (any GDAL format), detect format and dataset type"),
    ("schema", "Intelligent attribute mapping", "Map each department's columns to one schema"),
    ("crs", "Coordinate transformation", "Detect CRS, reproject, correct legacy datums / local grids"),
    ("extract", "GeoAI feature extraction", "Buildings from drone ORI + DSM, or AI-extracted footprint layers"),
    ("topology", "Topology correction", "GNSS snapping, shared corners, overlaps, gaps, invalid shapes"),
    ("match", "ML spatial matching & fusion", "Link parcels to municipal, revenue, field and building data"),
    ("change", "Change detection", "Compare survey dates"),
    ("conflict", "Conflict resolution", "Detect disagreements, auto-resolve with trusted sources"),
    ("confidence", "Confidence scoring", "Score every parcel and route low scores to review"),
    ("evaluate", "Accuracy evaluation", "Measure results against hidden truth, where one exists"),
    ("publish", "Publish", "Layers, APIs, exports and audit trail"),
]


def _role(ctx, role):
    return next((d for d in ctx["datasets"] if d.role == role), None)


def _roles(ctx, role):
    return [d for d in ctx["datasets"] if d.role == role]


def _canon(ds, src):
    out = []
    for r in ds.records:
        rec = schema_mapper.apply_mapping(ds.schema, r["props"])
        g = r["geom"]
        rec["geom"] = geo.reproject(g, src, config.TARGET_EPSG) if (g is not None and src is not None) else g
        rec["props"] = r["props"]
        rec["source"] = ds.name
        out.append(rec)
    return out


def _audit(ctx, kind, subject=None, detail=None):
    if ctx.get("audit"):
        ctx["audit"].append(kind, subject, detail)


# ----------------------------------------------------------------------------- steps
def step_ingest(ctx):
    ctx["datasets"] = ingest.load_folder(ctx["raw_dir"])
    formats = sorted({d.format for d in ctx["datasets"] if d.role != "raster_meta"})
    ctx["log"].append(f"{len(ctx['datasets'])} files read ({', '.join(formats)})")
    for d in ctx["datasets"]:
        if d.role not in ("raster_meta",):
            ctx["log"].append(f"{d.name}: {ingest.ROLE_LABELS.get(d.role, d.role)}"
                              + (f", {len(d.records)} features" if d.records else ""))
    _audit(ctx, "run.inputs", None, {"files": [{"file": d.name, "sha256": d.sha256, "role": d.role,
                                                 "format": d.format, "features": len(d.records)}
                                                for d in ctx["datasets"]]})


def schema_overrides_path(ws):
    return config.ws_paths(ws)["root"] / "schema_overrides.json" if ws else None


def step_schema(ctx):
    n = 0
    op = schema_overrides_path(ctx.get("ws"))
    overrides = geo.read_json(op) if op and op.exists() else {}
    for d in ctx["datasets"]:
        if d.columns:
            d.schema = schema_mapper.map_columns(d.columns, d.records, overrides.get(d.name))
            n += len(d.schema["mappings"])
            for m in d.schema["mappings"]:
                if "values only" in m["evidence"] or m.get("unit", "sq m") not in ("sq m", "sq m (assumed)"):
                    ctx["log"].append(f"{d.name}: '{m['column']}' → {m['label']}"
                                      + (f" ({m['unit']} → m²)" if m.get("unit") else "")
                                      + (" — from values" if "values only" in m["evidence"] else ""))
            _audit(ctx, "schema.mapping", d.name, {"mappings": [{k: m[k] for k in ("column", "field", "confidence")}
                                                                 for m in d.schema["mappings"]]})
    ctx["log"].append(f"{n} columns mapped to the canonical schema")


def _choose_target_crs(ctx):
    """UTM zone containing the data (from any dataset whose CRS is known)."""
    for d in ctx["datasets"]:
        src = d.crs.get("src")
        xy = ingest.sample_xy(d, 50)
        if src is not None and len(xy):
            lon, lat = geo.transformer(src, config.WGS84).transform(xy[:, 0].mean(), xy[:, 1].mean())
            return config.utm_epsg_for(lon, lat)
    return config.TARGET_EPSG


def step_crs(ctx):
    rep = []
    vec = [d for d in ctx["datasets"] if d.records and any(r["geom"] is not None for r in d.records)]
    for d in vec:
        d.crs = crs_engine.detect(d.declared_crs, ingest.sample_xy(d))
    config.set_target_epsg(_choose_target_crs(ctx))
    for d in vec:
        if d.crs.get("method", "").startswith("inferred: UTM"):   # zone now known
            d.crs = crs_engine.detect(d.declared_crs, ingest.sample_xy(d))
        rep.append({"dataset": d.name, "detected": d.crs["name"], "epsg": d.crs["epsg"],
                    "method": d.crs["method"], "target": f"EPSG:{config.TARGET_EPSG}"})
        _audit(ctx, "crs.detected", d.name, {"crs": d.crs["name"], "epsg": d.crs["epsg"], "method": d.crs["method"],
                                              "target_epsg": config.TARGET_EPSG})
    ctx["crs_report"] = rep
    ctx["target_crs"] = {"epsg": config.TARGET_EPSG, "name": crs_engine._name(config.TARGET_EPSG)}
    ctx["log"].append(f"Working CRS: {ctx['target_crs']['name']} (EPSG:{config.TARGET_EPSG}), chosen from data location")

    def load(role):
        out = []
        for d in _roles(ctx, role):
            out += _canon(d, d.crs.get("src"))
        return out

    ctx["municipal"] = load("municipal")
    ctx["footprints"] = load("footprints")
    ctx["ai_buildings"] = load("ai_buildings")
    ctx["utility"] = load("utility")
    ctx["gt"] = load("ground_truth")
    ctx["ror"] = load("revenue")
    ctx["roads"] = load("roads")
    ctx["dept_records"] = {dept: departments.read_records(load(role), dept)
                           for role, dept in departments.RECORD_ROLES.items() if _roles(ctx, role)}
    ctx["poi"] = load("poi")
    ctx["cadastral_alt"] = load("cadastral_alt")
    gnss = load("gnss")
    ctx["gnss"] = [{"plot_id": g["plot_id"], "pt": (g["geom"].x, g["geom"].y), "record_id": g.get("record_id"),
                    "geom": g["geom"]} for g in gnss if g.get("geom") is not None and g["geom"].geom_type == "Point"]

    cad_ds = _role(ctx, "cadastral")
    cad = _canon(cad_ds, None)
    for c in cad:
        c["geom"] = geo.largest_polygon(c["geom"]) if c["geom"] is not None else None
    cad = [c for c in cad if c["geom"] is not None and not c["geom"].is_empty]
    src = cad_ds.crs.get("src")
    if src is not None:
        for c in cad:
            c["geom"] = geo.reproject(c["geom"], src, config.TARGET_EPSG)
            c["geom_naive"] = c["geom"]
        ctx["georef"] = {"method": f"Declared CRS ({cad_ds.crs['name']}) reprojected",
                         "transform": crs_engine.decompose(np.eye(2, 3))}
        if ctx["gnss"]:
            for c in cad:
                c["geom_local"] = c["geom"]
            M, _, grep = crs_engine.georeference(cad, ctx["gnss"], already_projected=True)
            if grep["rmse_m"]["reprojected_only"] > config.DATUM_REFIT_RMSE_M:
                for c in cad:
                    c["geom"] = geo.apply_affine(c["geom_local"], M)
                grep["applied"] = True
                ctx["log"].append(f"Cadastral sheet in {cad_ds.crs['name']}: after reprojection still "
                                  f"{grep['rmse_m']['reprojected_only']:.2f} m off GNSS control (datum / sheet "
                                  f"registration) → corrected to {grep['rmse_m']['refined_affine']:.2f} m")
            else:
                grep["applied"] = False
                ctx["log"].append(f"Cadastral sheet in {cad_ds.crs['name']} fits GNSS control within "
                                  f"{grep['rmse_m']['reprojected_only']:.2f} m — no correction needed")
            ctx["georef"] = grep
    else:
        for c in cad:
            c["geom_local"] = c["geom"]
        M, naive_t, grep = crs_engine.georeference(cad, ctx["gnss"])
        for c in cad:
            c["geom"] = geo.apply_affine(c["geom_local"], M)
            c["geom_naive"] = affinity.translate(c["geom_local"], *naive_t)
        grep["applied"] = True
        ctx["georef"] = grep
        ctx["log"].append(f"Cadastral sheet in unknown local grid → georeferenced on "
                          f"{grep['control_points_used']} GNSS points, RMSE "
                          f"{grep['rmse_m']['translation_only']:.2f} m → {grep['rmse_m']['refined_affine']:.2f} m")
    if ctx["georef"].get("rmse_m"):
        _audit(ctx, "crs.georeference", cad_ds.name, {k: ctx["georef"][k] for k in
                                                      ("method", "rmse_m", "transform", "control_points_used", "applied")
                                                      if k in ctx["georef"]})
    ctx["cadastral"] = cad
    for r in rep:
        if r["epsg"] != config.TARGET_EPSG:
            ctx["log"].append(f"{r['dataset']}: {r['detected']} → UTM {config.TARGET_EPSG % 100}N")


def _vector_buildings(recs, source):
    out = []
    for r in recs:
        g = geo.largest_polygon(r["geom"]) if r.get("geom") is not None else None
        if g is None or g.is_empty or g.area < 6:
            continue
        p = r["props"]
        h = p.get("height")
        try:
            h = float(h) if h is not None and float(h) > 0 else None
        except (TypeError, ValueError):
            h = None
        lv = p.get("building:levels") or p.get("levels")
        try:
            floors = int(float(lv)) if lv else None
        except (TypeError, ValueError):
            floors = None
        if h is None and floors:
            h = floors * 3.2
        conf = p.get("confidence")
        try:
            conf = float(conf) if conf is not None else 0.75
        except (TypeError, ValueError):
            conf = 0.75
        out.append({"geom": g, "height": round(h, 1) if h else None, "floors": floors or (max(1, round(h / 3.2)) if h else None),
                    "score": conf, "area": round(g.area, 1), "source": source})
    return out


def step_extract(ctx):
    raw = Path(ctx["raw_dir"])
    meta_path = raw / "raster_meta.json"
    ctx["buildings_t1"], ctx["buildings_t2"], ctx["rasters"] = [], [], {}
    ctx["extraction_mode"] = "none"
    web = ctx["paths"]["web"]
    web.mkdir(parents=True, exist_ok=True)
    if meta_path.exists():
        ctx["extraction_mode"] = "raster"
        meta = geo.read_json(meta_path)
        ctx["raster_meta"] = meta
        for key in ("t1", "t2"):
            rgb, dsm, dtm = extractor.load_epoch(raw, meta, key)
            b, info = extractor.extract_buildings(rgb, dsm, dtm, meta)
            for x in b:
                x["source"] = f"Drone ORI + DSM ({meta['epochs'][key]['date'][:4]})"
            ctx[f"buildings_{key}"] = b
            date = meta["epochs"][key]["date"]
            ctx["log"].append(f"Survey {date}: {len(b)} buildings extracted "
                              f"({info['vegetation_px'] * meta['ortho']['res'] ** 2:.0f} m² of tree canopy rejected)")
            bounds = _warp_to_wgs84(rgb, meta["ortho"], web / f"ortho_{key}.jpg", meta["epsg"])
            ctx["rasters"][f"ortho_{key}"] = {"url": f"{ctx['url_prefix']}/ortho_{key}.jpg", "bounds": bounds, "date": date}
            if key == "t2":
                tmp = web / "_heights.png"
                extractor.height_map_png(dsm, dtm, tmp)
                hm = cv2.imread(str(tmp), cv2.IMREAD_UNCHANGED)
                bounds = _warp_to_wgs84(hm, meta["dsm"], web / "heights_t2.png", meta["epsg"])
                ctx["rasters"]["heights_t2"] = {"url": f"{ctx['url_prefix']}/heights_t2.png", "bounds": bounds, "date": date}
                tmp.unlink(missing_ok=True)
                _publish_hillshades(ctx, dsm, dtm, meta["dsm"], meta["epsg"], date, simulated=True)
    elif ctx["ai_buildings"] or ctx["footprints"]:
        # No imagery supplied: integrate AI feature-extraction OUTPUTS (e.g. ML building footprints)
        ctx["extraction_mode"] = "vector"
        primary = ctx["ai_buildings"] or ctx["footprints"]
        name = primary[0]["source"]
        ctx["buildings_t2"] = _vector_buildings(primary, name)
        ctx["log"].append(f"No drone imagery: using {len(ctx['buildings_t2'])} AI-extracted building footprints "
                          f"from {name}")
    else:
        ctx["log"].append("No imagery or building layers supplied; extraction skipped")
    if not meta_path.exists():
        _publish_uploaded_orthos(ctx)
    for n, b in enumerate(ctx["buildings_t2"]):
        b["id"] = f"BLD-{n + 1:04d}"


def step_topology(ctx):
    parcels = [{"plot_id": c["plot_id"], "geom": c["geom"], "extent_sqm": c.get("area_sqm"),
                "sheet": c["props"].get("SHEET"), "props": c["props"], "source": c.get("source")} for c in ctx["cadastral"]]
    parcels, rep = topology.clean(parcels, ctx.get("gnss", []))
    ctx["parcels"], ctx["topology"] = parcels, rep
    ctx["log"].append(f"{rep['gnss_snapped_vertices']} corners snapped to GNSS, {rep['corners_merged']} shared "
                      f"corners merged, {rep['overlaps_resolved']} overlaps and {rep['gaps_filled']} gaps fixed")
    if rep["invalid_repaired"] or rep["duplicates_removed"]:
        ctx["log"].append(f"{len(rep['invalid_repaired'])} self-intersecting and "
                          f"{len(rep['duplicates_removed'])} duplicate parcel(s) repaired")
    for p in parcels:
        if p["topo_flags"]:
            _audit(ctx, "topology.fix", p["plot_id"], {"fixes": p["topo_flags"], "area_change_pct": p["area_change_pct"]})
    _audit(ctx, "topology.summary", None, {k: v for k, v in rep.items() if not isinstance(v, list)})


def _iou(a, b):
    u = a.union(b).area
    return a.intersection(b).area / u if u else 0.0


def _coreg_record(ctx, layer, reference, method, dx, dy, applied, **extra):
    rec = {"layer": layer, "reference": reference, "method": method, "dx_m": round(dx, 2), "dy_m": round(dy, 2),
           "shift_m": round(float(np.hypot(dx, dy)), 2), "applied": applied, **extra}
    ctx["coregistration"].append(rec)
    _audit(ctx, "coregistration", layer, rec)
    if applied:
        ctx["log"].append(f"{layer} was shifted {rec['shift_m']:.2f} m as a whole relative to {reference} "
                          f"(dx {dx:+.2f}, dy {dy:+.2f}) → corrected")
    return rec


def step_match(ctx):
    model, info = matcher.load_or_train()
    ctx["matcher_info"] = info
    ctx["coregistration"] = []
    parcels, muni = ctx["parcels"], ctx["municipal"]
    links, merged = matcher.match(model, parcels, muni)

    # co-register the municipal layer onto the (GNSS-controlled) cadastral fabric, then match again
    members = {i for ms in merged.values() for i in ms}
    good = [(parcels[i]["geom"], muni[j]["geom"]) for i, (j, pr, _) in links.items() if pr > 0.9 and i not in members]
    if len(good) >= 10:
        dx, dy, mad = coreg.offset_from_pairs([a for a, _ in good], [b for _, b in good])
        applied = bool(np.hypot(dx, dy) > coreg.MIN_SHIFT_M)
        if applied:
            for m in muni:
                m["geom_raw"] = m["geom"]
                m["geom"] = affinity.translate(m["geom"], dx, dy)
            links, merged = matcher.match(model, parcels, muni)
        _coreg_record(ctx, "Municipal layer", "cadastral + GNSS fabric", "median displacement of matched parcels",
                      dx, dy, applied, pairs=len(good), spread_m=round(mad, 2))

    ctx["municipal_merges"] = merged
    merged_members = {i for ms in merged.values() for i in ms}
    for i, p in enumerate(parcels):
        if i in links:
            j, prob, feats = links[i]
            p["municipal"], p["match_prob"], p["match_features"] = muni[j], prob, feats
            p["municipal_merged"] = i in merged_members
            if not p["municipal_merged"]:
                p["muni_offset_m"] = round(geo.mean_boundary_offset(p["geom"], muni[j]["geom"]), 3)
    ror = {r["plot_id"]: r for r in ctx["ror"] if r.get("plot_id")}
    gt = {g["plot_id"]: g for g in ctx["gt"] if g.get("plot_id")}
    present = {p["plot_id"] for p in parcels}
    for p in parcels:
        p["ror"], p["gt"] = ror.get(p["plot_id"]), gt.get(p["plot_id"])
    ctx["ror_orphans"] = [r for pid, r in ror.items() if pid not in present]

    geoms = [p["geom"] for p in parcels]
    tree = STRtree(geoms)

    def assign(g):
        best, bi = 0.0, None
        for i in tree.query(g):
            a = geoms[int(i)].intersection(g).area
            if a > best:
                best, bi = a, int(i)
        return parcels[bi]["plot_id"] if bi is not None else None

    ctx["assign_parcel"] = assign
    ctx["parcel_tree_query"] = lambda g: [parcels[int(i)] for i in tree.query(g)]

    # buildings: primary (drone extraction or AI layer) corroborated by every other building layer.
    # Each building layer is first co-registered to the parcel fabric (buildings must sit inside plots).
    pgeoms = [p["geom"] for p in parcels]
    primary_src = ctx["buildings_t2"][0]["source"] if ctx["buildings_t2"] else None
    if ctx["buildings_t2"]:
        dx, dy, before, after = coreg.offset_by_containment([b["geom"] for b in ctx["buildings_t2"]], pgeoms)
        applied = bool(before is not None and np.hypot(dx, dy) > coreg.MIN_SHIFT_M and after - before > 0.01)
        if applied:
            for b in ctx["buildings_t2"] + ctx["buildings_t1"]:
                b["geom"] = affinity.translate(b["geom"], dx, dy)
        _coreg_record(ctx, primary_src, "parcel fabric", "best fit of buildings inside plots", dx, dy, applied,
                      fit_before=before, fit_after=after)
    refs = [f for f in (ctx["footprints"] + ctx["ai_buildings"]) if f["source"] != primary_src]
    for src in sorted({f["source"] for f in refs}):
        grp = [f for f in refs if f["source"] == src]
        dx, dy, before, after = coreg.offset_by_containment([geo.largest_polygon(f["geom"]) for f in grp], pgeoms)
        applied = bool(before is not None and np.hypot(dx, dy) > coreg.MIN_SHIFT_M and after - before > 0.01)
        for f in grp:
            f["geom_reg"] = affinity.translate(f["geom"], dx, dy) if applied else f["geom"]
        _coreg_record(ctx, src, "parcel fabric", "best fit of buildings inside plots", dx, dy, applied,
                      fit_before=before, fit_after=after)
    rgeoms = [geo.largest_polygon(f.get("geom_reg", f["geom"])) for f in refs]
    rtree = STRtree(rgeoms) if rgeoms else None
    ctx["ref_building_geoms"], ctx["ref_building_tree"] = rgeoms, rtree
    ctx["primary_building_source"] = primary_src
    used_ref = set()
    by_plot = {p["plot_id"]: p for p in parcels}
    for b in ctx["buildings_t2"]:
        best, bk = 0.0, None
        if rtree is not None:
            for k in rtree.query(b["geom"]):
                k = int(k)
                v = _iou(b["geom"], rgeoms[k])
                if v > best:
                    best, bk = v, k
        b["footprint_iou"] = round(best, 3)
        b["sources"] = [b["source"]]
        b["pos_tol_m"] = 0.5 if ctx["extraction_mode"] == "raster" else 1.0
        if best >= 0.3 and bk is not None:
            used_ref.add(bk)
            b["sources"].append(refs[bk]["source"])
            b["ref_geom"] = rgeoms[bk]
        b["confidence"] = round(100 * (0.6 * b["score"] + 0.4 * (min(1, best / 0.7) if rtree is not None else 0.5)), 1)
    # AI models often merge attached row-houses into one blob. Where the reference layer shows 2+ separate
    # buildings mostly inside one AI footprint, replace the blob by those buildings.
    if ctx["extraction_mode"] == "vector" and rtree is not None:
        kept, split = [], 0
        for b in ctx["buildings_t2"]:
            inside = [int(k) for k in rtree.query(b["geom"])
                      if rgeoms[int(k)].area > 6 and rgeoms[int(k)].intersection(b["geom"]).area >= 0.6 * rgeoms[int(k)].area]
            if len(inside) >= 2:
                split += 1
                for k in inside:
                    used_ref.add(k)
                    nb = _vector_buildings([{**refs[k], "geom": rgeoms[k]}], refs[k]["source"])
                    for x in nb:
                        x.update(id=f"{b['id']}-{len(kept) + 1}", footprint_iou=round(_iou(x["geom"], b["geom"]), 3),
                                 sources=[refs[k]["source"], f"{b['source']} (merged footprint, split)"],
                                 confidence=80.0, pos_tol_m=1.0, ref_geom=x["geom"])
                        kept.append(x)
            else:
                kept.append(b)
        ctx["buildings_t2"] = kept
        ctx["ai_footprints_split"] = split
        if split:
            ctx["log"].append(f"{split} AI footprints merged several buildings (row houses) → split using the reference layer")
    # buildings present only in a reference layer (e.g. mapped in OSM but missed by the AI model)
    if ctx["extraction_mode"] == "vector":
        extra = []
        for k, f in enumerate(refs):
            if k in used_ref or rgeoms[k].is_empty or rgeoms[k].area < 6:
                continue
            extra += _vector_buildings([{**f, "geom": rgeoms[k]}], f["source"])
        for b in extra:
            b.update(footprint_iou=0.0, sources=[b["source"]], confidence=round(100 * 0.45, 1), pos_tol_m=1.0)
        n0 = len(ctx["buildings_t2"])
        for n, b in enumerate(extra):
            b["id"] = f"BLD-{n0 + n + 1:04d}"
        ctx["buildings_t2"] += extra
        m = sum(1 for b in ctx["buildings_t2"][:n0] if len(b["sources"]) > 1)
        ctx["building_agreement"] = {
            "primary": primary_src, "reference": sorted({f["source"] for f in refs}),
            "primary_count": n0, "confirmed_by_reference": m, "primary_only": n0 - m, "reference_only": len(extra),
            "mean_iou_confirmed": round(float(np.mean([b["footprint_iou"] for b in ctx["buildings_t2"][:n0]
                                                       if len(b["sources"]) > 1] or [0])), 3)}
        ctx["log"].append(f"Buildings: {m} of {n0} AI footprints confirmed by {', '.join(ctx['building_agreement']['reference'])}"
                          f" (mean IoU {ctx['building_agreement']['mean_iou_confirmed']:.2f}); {len(extra)} only in the reference layer")
    for b in ctx["buildings_t2"]:
        b["plot_id"] = assign(b["geom"])
        if b["plot_id"] in by_plot:
            by_plot[b["plot_id"]].setdefault("buildings", []).append(b["id"])
    ctx["buildings"] = ctx["buildings_t2"]
    departments.link(ctx)
    _compare_cadastral(ctx, parcels)
    n_m = sum(1 for p in parcels if p.get("municipal"))
    n_r = sum(1 for p in parcels if p.get("ror"))
    probs = [p["match_prob"] for p in parcels if p.get("municipal")]
    ctx["match_stats"] = {"parcels": len(parcels), "linked_municipal": n_m, "linked_ror": n_r,
                          "linked_gt": sum(1 for p in parcels if p["gt"]),
                          "mean_match_confidence": round(float(np.mean(probs)), 4) if probs else None}
    ctx["log"].append(f"Matcher (CV ROC-AUC {info['cv_roc_auc']:.3f}): {n_m}/{len(parcels)} parcels linked to "
                      f"municipal records (mean match confidence {100 * (ctx['match_stats']['mean_match_confidence'] or 0):.1f}%), "
                      f"{len(merged)} merged properties found")
    ctx["log"].append(f"{n_r}/{len(parcels)} parcels linked to RoR, {len(ctx['ror_orphans'])} RoR rows without a "
                      f"parcel, {sum(1 for p in parcels if p['gt'])} field-verified")


def _publish_hillshades(ctx, dsm, dtm, grid, epsg, date, simulated):
    web = ctx["paths"]["web"]
    for key, z, title in (("dsm_t2", dsm, "Surface model (DSM)"), ("dtm", dtm, "Terrain model (DTM)")):
        tmp = web / f"_{key}.png"
        elevation.hillshade_png(z, tmp)
        img = cv2.imread(str(tmp), cv2.IMREAD_UNCHANGED)
        bounds = _warp_to_wgs84(img, grid, web / f"{key}.png", epsg)
        tmp.unlink(missing_ok=True)
        ctx["rasters"][key] = {"url": f"{ctx['url_prefix']}/{key}.png", "bounds": bounds, "date": date,
                               "title": title, "simulated": simulated}


def _step_elevation(ctx, ev):
    """Real footprints + elevation models: measure heights, publish the rasters, detect changes."""
    meta, dtm, dsms = ev
    grid, sim = meta["grid"], bool(meta.get("simulated"))
    dates = [meta["epochs"].get("t1", {}).get("date"), meta["epochs"]["t2"]["date"]]
    ctx["elevation"] = {"simulated": sim, "dates": dates, "resolution_m": grid["res"], "note": meta.get("note")}
    n = 0
    for b in ctx["buildings_t2"]:
        h, _ = elevation.sample(b["geom"], dsms["t2"], dtm, grid)
        if h:
            b["height"], b["floors"], n = h, elevation.floors_from(h), n + 1
            b["height_source"] = "elevation model" + (" (simulated)" if sim else "")
    tag = " (SIMULATED)" if sim else ""
    ctx["log"].append(f"Heights and floors for {n} of {len(ctx['buildings_t2'])} buildings from DSM − DTM{tag}")
    web = ctx["paths"]["web"]
    web.mkdir(parents=True, exist_ok=True)
    for k in [k for k in ("t1", "t2") if k in dsms]:
        tmp = web / f"_heights_{k}.png"
        extractor.height_map_png(dsms[k], dtm, tmp)
        img = cv2.imread(str(tmp), cv2.IMREAD_UNCHANGED)
        bounds = _warp_to_wgs84(img, grid, web / f"heights_{k}.png", meta["epsg"])
        tmp.unlink(missing_ok=True)
        ctx["rasters"][f"heights_{k}"] = {"url": f"{ctx['url_prefix']}/heights_{k}.png", "bounds": bounds,
                                          "date": meta["epochs"][k]["date"], "simulated": sim}
    _publish_hillshades(ctx, dsms["t2"], dtm, grid, meta["epsg"], dates[1], sim)
    if "t1" not in dsms:
        ctx["changes"] = []
        ctx["log"].append("One elevation survey supplied: heights measured; change detection needs a second DSM")
        return
    ctx["changes"] = elevation.detect_changes(ctx["buildings_t2"], dsms["t1"], dsms["t2"], dtm, grid,
                                              ctx["assign_parcel"], dates)
    counts = {}
    for c in ctx["changes"]:
        counts[c["type"]] = counts.get(c["type"], 0) + 1
        _audit(ctx, "change.detected", c["plot_id"], {"type": c["type"], "detail": c["detail"], "simulated_epoch": sim})
    ctx["log"].append(f"Survey {dates[0]} vs {dates[1]}{tag}: " +
                      (", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items()) or "no changes"))


def _compare_cadastral(ctx, parcels):
    """Two independent cadastral surveys of the same land (e.g. TNGIS and NCSCM): pair each parcel with the other
    source's best-overlapping parcel and measure how far they agree. There is no answer key - this is a real finding."""
    alt = [a for a in ctx.get("cadastral_alt") or [] if a.get("geom") is not None]
    if not alt:
        return
    ageoms = [geo.largest_polygon(a["geom"]) for a in alt]
    # the second survey may be shifted as a whole (a different base map): measure it on parcels whose numbers
    # agree, remove it, then compare shapes - so only real differences are reported
    tree = STRtree(ageoms)
    a_g, b_g = [], []
    for p in parcels:
        sub = p["plot_id"].split("/")[-1].lstrip("0")
        for j in tree.query(p["geom"].buffer(10)):
            j = int(j)
            if sub and str(alt[j].get("plot_id") or "").split("/")[-1].strip().lstrip("0") == sub:
                a_g.append(p["geom"])
                b_g.append(ageoms[j])
                break
    shift = None
    if len(a_g) >= 10:
        sdx, sdy, spread = coreg.offset_from_pairs(a_g, b_g)
        applied = bool(np.hypot(sdx, sdy) > coreg.MIN_SHIFT_M)
        if applied:
            ageoms = [affinity.translate(g, sdx, sdy) for g in ageoms]
            for a, g in zip(alt, ageoms):
                a["geom"] = g
            tree = STRtree(ageoms)
        shift = {"dx_m": round(sdx, 2), "dy_m": round(sdy, 2), "shift_m": round(float(np.hypot(sdx, sdy)), 2),
                 "pairs": len(a_g), "spread_m": round(spread, 2) if spread is not None else None}
        _coreg_record(ctx, alt[0].get("source") or "second cadastral survey", "cadastral + GNSS fabric",
                      "median displacement of parcels with the same number", sdx, sdy, applied, pairs=len(a_g))
    ious, offs, same, pairs = [], [], 0, 0
    for p in parcels:
        best, bj = 0.0, None
        for j in tree.query(p["geom"]):
            j = int(j)
            v = _iou(p["geom"], ageoms[j])
            if v > best:
                best, bj = v, j
        if bj is None or best < 0.1:
            continue
        a = alt[bj]
        sub = p["plot_id"].split("/")[-1]
        aid = str(a.get("plot_id") or "").split("/")[-1].strip()
        id_ok = bool(aid) and aid.lstrip("0") == sub.lstrip("0")
        off = geo.mean_boundary_offset(p["geom"], ageoms[bj])
        p["alt_cadastral"] = {"iou": round(best, 3), "offset_m": round(off, 2), "alt_id": aid or None, "id_agrees": id_ok,
                              "geom": ageoms[bj], "source": alt[bj].get("source")}
        pairs += 1
        ious.append(best)
        offs.append(off)
        same += id_ok
    n_alt_ids = sum(1 for p in parcels if p.get("alt_cadastral", {}).get("alt_id"))
    ctx["cadastral_comparison"] = {
        "source": alt[0].get("source"), "parcels": len(parcels), "alt_parcels": len(alt), "paired": pairs,
        "mean_iou": round(float(np.mean(ious)), 3) if ious else None,
        "median_offset_m": round(float(np.median(offs)), 2) if offs else None,
        "agree_within_1m": round(sum(1 for o in offs if o <= 1.0) / len(offs), 3) if offs else None,
        "numbers_agree": round(same / n_alt_ids, 3) if n_alt_ids else None,
        "strong_overlap": round(sum(1 for v in ious if v >= 0.8) / len(ious), 3) if ious else None, "shift": shift}
    cc = ctx["cadastral_comparison"]
    ctx["log"].append(f"Second cadastral source {cc['source']}: {pairs} of {len(parcels)} parcels paired, mean overlap "
                      f"{(cc['mean_iou'] or 0) * 100:.0f}%, median boundary offset {cc['median_offset_m']} m, survey numbers "
                      f"agree on {(cc['numbers_agree'] or 0) * 100:.0f}% of those that carry one")


def step_change(ctx):
    ev = elevation.load(ctx["raw_dir"], ctx["datasets"]) if ctx.get("extraction_mode") == "vector" else None
    if ev is not None:
        return _step_elevation(ctx, ev)
    if not ctx["buildings_t1"]:
        ctx["changes"] = []
        ctx["log"].append("Only one survey date supplied — change detection needs two")
        return
    ctx["changes"] = change.detect(ctx["buildings_t1"], ctx["buildings_t2"], ctx["assign_parcel"])
    counts = {}
    for c in ctx["changes"]:
        counts[c["type"]] = counts.get(c["type"], 0) + 1
        _audit(ctx, "change.detected", c["plot_id"], {"type": c["type"], "detail": c["detail"]})
    ctx["log"].append(", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items()) or "No changes")


def step_conflict(ctx):
    from . import learning
    ctx["conflicts"] = conflicts.detect(ctx)
    n_learned = learning.apply(ctx, ctx["conflicts"])
    if n_learned:
        ctx["log"].append(f"{n_learned} conflicts settled by rules learned from reviewer decisions (enabled by an admin)")
    auto = sum(1 for c in ctx["conflicts"] if c["status"] == "auto_resolved")
    for c in ctx["conflicts"]:
        _audit(ctx, "conflict." + ("auto_resolved" if c["status"] == "auto_resolved" else "opened"), c["plot_id"],
               {"id": c["id"], "type": c["type"], "severity": c["severity"], "detail": c["detail"],
                "resolution": c.get("resolution"),
                "winner": (c["versions"][c["chosen"]]["source"] if c.get("chosen") is not None and c["versions"] else None)})
    ctx["log"].append(f"{len(ctx['conflicts'])} conflicts found, {auto} resolved automatically by trusted sources, "
                      f"{len(ctx['conflicts']) - auto} sent to review")


def step_confidence(ctx):
    by_plot = {}
    for c in ctx["conflicts"]:
        by_plot.setdefault(c["plot_id"], []).append(c["id"])
    for p in ctx["parcels"]:
        p.setdefault("match_prob", 0.0)
        p.setdefault("match_features", {})
        p["conflicts"] = by_plot.get(p["plot_id"], [])
        p["components"] = confidence.components(p)
        p["score_base"] = confidence.base_score(p["components"])
        gt, ror, muni = p.get("gt"), p.get("ror"), p.get("municipal")
        own_conf = next((c for c in ctx["conflicts"] if c["id"] == f"C-OWNER_MISMATCH-{p['plot_id']}"), None)
        if gt and gt.get("owner_name"):
            p["resolved_owner"], p["owner_source"] = gt["owner_name"], "Ground truth (field verified)"
        elif own_conf:
            p["resolved_owner"], p["owner_source"] = None, "Disputed — awaiting review"
        elif ror:
            p["resolved_owner"], p["owner_source"] = ror.get("owner_name"), "Revenue record (RoR)"
        elif muni:
            p["resolved_owner"], p["owner_source"] = muni.get("owner_name"), "Municipal register"
        else:
            p["resolved_owner"], p["owner_source"] = None, "Unknown"
    review.apply_saved_decisions(ctx)
    cb = {c["id"]: c for c in ctx["conflicts"]}
    st = {}
    for p in ctx["parcels"]:
        confidence.finalize(p, cb)
        st[p["status"]] = st.get(p["status"], 0) + 1
        muni, ror = p.get("municipal") or {}, p.get("ror") or {}
        _audit(ctx, "parcel.finalised", p["plot_id"], {
            "status": p["status"], "score": p["score"], "components": p["components"],
            "owner": p.get("resolved_owner"), "owner_source": p.get("owner_source"),
            "links": {"municipal": muni.get("record_id"), "match_prob": round(p.get("match_prob", 0), 4),
                      "ror": ror.get("record_id"), "gt": (p.get("gt") or {}).get("record_id"),
                      "gnss_corners": p.get("gnss_vertices", 0)}})
    ctx["log"].append(", ".join(f"{v} {k}" for k, v in sorted(st.items())))


def step_evaluate(ctx):
    tpath = ctx["paths"]["truth"]
    if not tpath.exists():
        ctx["evaluation"] = None
        ctx["log"].append("No hidden truth for this workspace — accuracy vs truth not measured")
        return
    ctx["evaluation"] = evaluate.run(ctx, geo.read_json(tpath))
    ev = ctx["evaluation"]
    from . import publish
    publish.record_benchmark(ctx.get("ws"), ev, os.environ.get("LANDCONNECT_BENCH_LABEL"))
    pos = ev.get("positional", {})
    msg = []
    if pos.get("Fused output (GNSS + topology)"):
        msg.append(f"boundary error {pos['Fused output (GNSS + topology)']['mean_m']:.2f} m (fused)")
    if ev.get("matching"):
        msg.append(f"matching F1 {ev['matching']['f1']:.3f}")
    if ev.get("extraction"):
        msg.append(f"extraction F1 {ev['extraction']['f1']:.3f}")
    ctx["log"].append("Against hidden truth: " + "; ".join(msg))


def step_publish(ctx):
    from . import publish
    result = publish.build(ctx)
    if ctx.get("ws"):
        # records.* append to the same audit file: hand it over cleanly so the hash chain stays linear
        if ctx.get("audit"):
            ctx["audit"].close()
        n_new = records.propose_from_pipeline(ctx["ws"], result, ctx.get("change_proposals", []))
        n_applied = records.apply_approved(ctx["ws"], result)
        if ctx.get("audit"):
            ctx["audit"] = audit.AuditLog(ctx["paths"]["audit"])
        if n_new or n_applied:
            ctx["log"].append(f"{n_new} change request(s) drafted for officers; {n_applied} approved change(s) re-applied")
    geo.write_json(ctx["paths"]["result"], result)
    ctx["result"] = result
    ctx["log"].append(f"{sum(len(l['features']) for l in result['layers'].values())} features published in "
                      f"{len(result['layers'])} layers")


STEP_FUNCS = {"ingest": step_ingest, "schema": step_schema, "crs": step_crs, "extract": step_extract,
              "topology": step_topology, "match": step_match, "change": step_change, "conflict": step_conflict,
              "confidence": step_confidence, "evaluate": step_evaluate, "publish": step_publish}


def new_ctx(raw_dir, ws=None, with_audit=False):
    paths = config.ws_paths(ws) if ws else {"raw": Path(raw_dir), "truth": Path(raw_dir).parent / "_truth" / "truth.json",
                                             "web": Path(raw_dir).parent / "_web", "decisions": None}
    ctx = {"raw_dir": Path(raw_dir), "log": [], "ws": ws, "paths": paths,
           "url_prefix": f"/output/{ws}/web" if ws else "", "run_id": uuid.uuid4().hex[:12]}
    if with_audit and ws:
        ctx["audit"] = audit.AuditLog(paths["audit"])
    return ctx


def prepare_vectors(raw_dir):
    """The vector part of the pipeline (used to build matcher training data)."""
    ctx = new_ctx(raw_dir)
    for s in ("ingest", "schema", "crs", "topology"):
        STEP_FUNCS[s](ctx)
    return ctx


ORTHO_MAX_PX = 4096          # longest side of the published web image; the uploaded file itself is kept as is


def _publish_uploaded_orthos(ctx):
    """Drone orthomosaics uploaded as RGB GeoTIFFs become the 'Drone' map type. One file = the current survey;
    two or more with a year in the name = the latest two surveys (older one shown as the earlier epoch)."""
    import re
    from . import geotiff
    orthos = [d for d in ctx["datasets"] if d.format == "GeoTIFF" and d.role == "ortho"]
    if not orthos:
        return
    year = lambda d: (re.search(r"(19|20)\d\d", d.name) or [None])[0]   # noqa: E731
    orthos = sorted(orthos, key=lambda d: (year(d) or "", d.name))[-2:]
    keys = ["t2"] if len(orthos) == 1 else ["t1", "t2"]
    web = ctx["paths"]["web"]
    for key, d in zip(keys, orthos):
        try:
            arr, grid, epsg = geotiff.read(d.path)
            if epsg is None:
                raise ValueError("no EPSG code in the GeoTIFF keys")
            if arr.ndim == 2:
                arr = np.repeat(arr[:, :, None], 3, axis=2)
            arr = arr[:, :, :3]
            if arr.dtype != np.uint8:                                 # 16-bit or float imagery: stretch to 8 bit
                a = arr.astype(np.float32)
                lo, hi = np.percentile(a, 1), np.percentile(a, 99)
                arr = np.clip((a - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)
            res_x, res_y = grid["res"], grid.get("res_y", grid["res"])
            if abs(res_x - res_y) > 1e-9 * max(res_x, 1):           # square the pixels before warping
                h, w = arr.shape[:2]
                arr = cv2.resize(arr, (w, max(1, round(h * res_y / res_x))), interpolation=cv2.INTER_AREA)
            f = max(arr.shape[:2]) / ORTHO_MAX_PX
            if f > 1:
                arr = cv2.resize(arr, (round(arr.shape[1] / f), round(arr.shape[0] / f)), interpolation=cv2.INTER_AREA)
                res_x *= f
            g = {"x0": grid["x0"], "y_top": grid["y_top"], "res": res_x}
            bounds = _warp_to_wgs84(np.ascontiguousarray(arr), g, web / f"ortho_{key}.jpg", epsg)
            ctx["rasters"][f"ortho_{key}"] = {"url": f"{ctx['url_prefix']}/ortho_{key}.jpg", "bounds": bounds,
                                              "date": year(d) or "uploaded", "source": d.name,
                                              "res_m": round(grid["res"], 3)}
            ctx["log"].append(f"Drone orthomosaic {d.name} published as the Drone map "
                              f"({grid['width']}×{grid['height']} px, {grid['res']:.2f} units/px, EPSG:{epsg})")
        except Exception as e:                                       # a bad image must not stop the run
            ctx["log"].append(f"Drone orthomosaic {d.name} could not be read: {e}")


def _warp_to_wgs84(img, grid, out_path, src_epsg):
    """Resample a projected raster onto a lat/lon grid so web maps overlay it exactly."""
    h, w = img.shape[:2]
    x0, y_top, res = grid["x0"], grid["y_top"], grid["res"]
    corners = np.array([[x0, y_top], [x0 + w * res, y_top], [x0, y_top - h * res], [x0 + w * res, y_top - h * res]])
    lon, lat = geo.transformer(src_epsg, config.WGS84).transform(corners[:, 0], corners[:, 1])
    west, east, south, north = min(lon), max(lon), min(lat), max(lat)
    cols, rows = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    lons = west + cols * (east - west) / w
    lats = north - rows * (north - south) / h
    ex, ny = geo.transformer(config.WGS84, src_epsg).transform(lons.ravel(), lats.ravel())
    map_x = ((np.asarray(ex) - x0) / res - 0.5).reshape(h, w).astype(np.float32)
    map_y = ((y_top - np.asarray(ny)) / res - 0.5).reshape(h, w).astype(np.float32)
    warped = cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    if str(out_path).endswith(".jpg"):
        cv2.imwrite(str(out_path), cv2.cvtColor(warped, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    else:
        cv2.imwrite(str(out_path), warped)
    return [[south, west], [north, east]]


def run(ws=None, progress=None, user="cli"):
    """Run every step for a workspace. progress(step_key, state, info) is called as steps start/finish.
    The run is recorded in the database (who, when, outcome) and the parcel / conflict index refreshed."""
    ws = ws or config.DEFAULT_WS
    paths = config.ws_paths(ws)
    paths["out"].mkdir(parents=True, exist_ok=True)
    ctx = new_ctx(paths["raw"], ws, with_audit=True)
    db.run_started(ctx["run_id"], ws, user)
    _audit(ctx, "run.started", None, {"run_id": ctx["run_id"], "workspace": ws, "started_by": user})
    timings = []
    t_all = time.perf_counter()
    try:
        for key, title, _ in STEPS:
            if progress:
                progress(key, "running", {})
            start, n_log = time.perf_counter(), len(ctx["log"])
            try:
                STEP_FUNCS[key](ctx)
            except Exception as e:
                _audit(ctx, "run.failed", None, {"run_id": ctx["run_id"], "step": key, "error": str(e)})
                db.run_finished(ctx["run_id"], "failed", round(time.perf_counter() - t_all, 2),
                                error=f"{key}: {type(e).__name__}: {e}")
                if progress:
                    progress(key, "error", {"error": str(e), "trace": traceback.format_exc()})
                raise
            dt = time.perf_counter() - start
            timings.append({"key": key, "title": title, "seconds": round(dt, 2), "log": ctx["log"][n_log:]})
            if key == "publish":
                ctx["result"]["timings"] = timings
                ctx["result"]["total_seconds"] = round(time.perf_counter() - t_all, 2)
                _audit(ctx, "run.completed", None, {"run_id": ctx["run_id"], "summary": ctx["result"]["summary"],
                                                    "seconds": ctx["result"]["total_seconds"]})
                ctx["audit"].close()
                ctx["result"]["audit"] = audit.verify(paths["audit"])
                ctx["result"]["run_id"] = ctx["run_id"]
                geo.write_json(paths["result"], ctx["result"])
                db.index_result(ws, ctx["result"], ctx["run_id"])
                db.run_finished(ctx["run_id"], "done", ctx["result"]["total_seconds"], ctx["result"]["summary"],
                                audit_head=ctx["result"]["audit"]["head"])
            if progress:
                progress(key, "done", timings[-1])
    finally:
        ctx["audit"].close()
    return ctx["result"]


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ws_arg = sys.argv[1] if len(sys.argv) > 1 else None
    r = run(ws_arg, progress=lambda k, s, i: print(f"[{s:>7}] {k}", *(f"\n          {l}" for l in i.get("log", [])))
            if s != "running" else None)
    print(f"\nDone in {r['total_seconds']} s · audit chain {'intact' if r['audit']['ok'] else 'BROKEN'} "
          f"({r['audit']['entries']} entries)")
