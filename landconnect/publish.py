"""Turn the pipeline context into the published result: WGS84 layers, reports and summary."""
import datetime as dt

from shapely.geometry import mapping

from . import config, departments, geo, ingest, insights
from .confidence import LABELS
from .pipeline import STEPS


def _g84(g, decimals=7):
    return mapping(geo.round_geom(geo.reproject(g, config.TARGET_EPSG, config.WGS84), decimals)) if g else None


def _fc(items, props_fn, geom_key="geom"):
    return geo.to_geojson_features([i for i in items if i.get(geom_key) is not None], config.TARGET_EPSG,
                                   geom_key=geom_key, props_fn=props_fn)


_BUILDING_SOURCE = "Buildings"
_STATE_CODE = "KA"


def conflicts_ruleset():
    from . import conflicts
    return conflicts.RULESET


def benchmarks(ws):
    """Scores of earlier runs (output/benchmarks/<ws>.json), for the before / after table."""
    p = config.OUTPUT_DIR / "benchmarks" / f"{ws}.json"
    return geo.read_json(p) if ws and p.exists() else []


def blind_tests():
    """Latest random blind batch and teammate batch (landconnect/blind.py), shown on every area's Accuracy tab."""
    out = {}
    for name in ("blind_runs", "blind_manual"):
        p = config.OUTPUT_DIR / "benchmarks" / f"{name}.json"
        if p.exists():
            hist = geo.read_json(p)
            out[name] = {"latest": hist[-1], "runs": len(hist)}
    return out


def record_benchmark(ws, evaluation, label=None):
    """Keep one entry per label (the latest run with that label) so rule changes can be compared."""
    from . import conflicts
    if not ws or not evaluation:
        return
    p = config.OUTPUT_DIR / "benchmarks" / f"{ws}.json"
    hist = geo.read_json(p) if p.exists() else []
    label = label or f"Rules {conflicts.RULESET}"
    entry = {"ws": ws, "label": label, "rules": conflicts.RULESET, "at": dt.datetime.now().isoformat(timespec="seconds"),
             "evaluation": {k: evaluation.get(k) for k in ("issues", "change_detection", "overall", "heights")}}
    hist = [h for h in hist if h["label"] != label] + [entry]
    p.parent.mkdir(parents=True, exist_ok=True)
    geo.write_json(p, hist[-12:], indent=1)


def _parcel_props(p):
    ror, muni, gt = p.get("ror") or {}, p.get("municipal") or {}, p.get("gt") or {}
    sources = ["Cadastral map (georeferenced)"]
    if p.get("gnss_vertices"):
        sources.append(f"GNSS control ({p['gnss_vertices']} corners)")
    if muni:
        sources.append(f"Municipal register {muni.get('record_id')}")
    if ror:
        sources.append(f"Revenue record {ror.get('record_id')}")
    if gt:
        sources.append(f"Field verification {gt['record_id']}" if gt.get("record_id") else "Field verification visit")
    if p.get("buildings"):
        sources.append(f"{_BUILDING_SOURCE} ({len(p['buildings'])} building{'s' * (len(p['buildings']) > 1)})")
    for dept, recs in p.get("dept", {}).items():
        sources.append(f"{departments.DEPARTMENTS[dept]['short']}: {', '.join(str(r.get('id')) for r in recs[:3])}")
    return {
        "plot_id": p["plot_id"], "status": p["status"], "score": p["score"], "score_base": p["score_base"],
        "components": p["components"], "conflicts": p["conflicts"], "open_conflicts": p["open_conflicts"],
        "resolved_owner": p.get("resolved_owner"), "owner_source": p.get("owner_source"),
        "ror_owner": ror.get("owner_name"), "municipal_owner": muni.get("owner_name"),
        "gt_owner": gt.get("owner_name"), "khata": ror.get("record_id"), "municipal_pid": muni.get("record_id"),
        "area_sqm": round(p["geom"].area, 1), "ror_area_sqm": round(ror["area_sqm"], 1) if ror.get("area_sqm") else None,
        "land_use": gt.get("land_use") or ror.get("land_use") or muni.get("land_use"),
        "municipal_use": muni.get("land_use"), "gnss_vertices": p.get("gnss_vertices", 0),
        "match_prob": round(p.get("match_prob", 0), 3), "topo_flags": p.get("topo_flags", []),
        "buildings": p.get("buildings", []), "sources": sources, "sheet": p.get("sheet"),
        "dept_records": (dr := departments.parcel_summary(p)), "registers": departments.registers(p), "updates": [],
        "ulpin": insights.ulpin(p["geom"], _STATE_CODE),
        "ownership_chain": insights.ownership_chain(ror.get("owner_name"), dr.get("registration", [])),
    }


def summarize(result):
    feats = [f["properties"] for f in result["layers"]["parcels"]["features"]]
    n = len(feats) or 1
    st = {s: sum(1 for f in feats if f["status"] == s) for s in ("approved", "flagged", "review", "verified")}
    cs = result["conflicts"]
    ch = {}
    for f in result["layers"]["changes"]["features"]:
        t = f["properties"]["type"]
        ch[t] = ch.get(t, 0) + 1
    return {
        "parcels": len(feats), "status": st,
        "auto_approved_pct": round(100 * st["approved"] / n, 1),
        "no_manual_work_pct": round(100 * (st["approved"] + st["flagged"]) / n, 1),
        "mean_score": round(sum(f["score"] for f in feats) / n, 1),
        "conflicts": {"total": len(cs), "open": sum(1 for c in cs if c["status"] == "open"),
                      "auto_resolved": sum(1 for c in cs if c["status"] == "auto_resolved"),
                      "resolved": sum(1 for c in cs if c["status"] == "resolved"),
                      "open_high": sum(1 for c in cs if c["status"] == "open" and c["severity"] == "high")},
        "buildings": len(result["layers"]["buildings"]["features"]),
        "changes": ch,
        "mean_match_confidence": (result.get("match_stats") or {}).get("mean_match_confidence"),
    }


def _conflict_out(c):
    out = {k: v for k, v in c.items() if k not in ("versions", "geom_hint")}
    out["versions"] = [{k: (_g84(v) if k == "geom" else v) for k, v in ver.items()} for ver in c["versions"]]
    out["geom_hint"] = _g84(c["geom_hint"]) if c.get("geom_hint") is not None else None
    return out


def build(ctx):
    global _BUILDING_SOURCE, _STATE_CODE
    _meta = geo.read_json(ctx["paths"]["meta"]) if ctx.get("paths", {}).get("meta") and ctx["paths"]["meta"].exists() else {}
    _STATE_CODE = _meta.get("state_code", "KA")
    revenue = insights.revenue_at_risk(ctx, ctx["conflicts"])
    _BUILDING_SOURCE = {"raster": "Drone ORI + DSM", "vector": "Building footprints (AI + OSM)"}.get(ctx.get("extraction_mode"), "Buildings")
    if ctx.get("elevation"):
        _BUILDING_SOURCE += " + elevation model"
    parcels = ctx["parcels"]
    layers = {
        "parcels": _fc(parcels, _parcel_props),
        "buildings": _fc(ctx["buildings"], lambda b: {
            "id": b["id"], "plot_id": b.get("plot_id"), "height_m": b.get("height"), "floors": b.get("floors"),
            "height_source": b.get("height_source"),
            "area_sqm": b["area"], "confidence": b["confidence"], "footprint_iou": b["footprint_iou"],
            "sources": b["sources"]}),
        "changes": _fc(ctx["changes"], lambda c: {k: v for k, v in c.items() if k != "geom"}),
        "cadastral_naive": _fc(ctx["cadastral"], lambda c: {"plot_id": c["plot_id"]}, "geom_naive"),
        "cadastral_georef": _fc(ctx["cadastral"], lambda c: {"plot_id": c["plot_id"]}),
        "cadastral_alt": _fc(ctx.get("cadastral_alt") or [], lambda c: {"plot_id": c.get("plot_id"), "source": c.get("source")}),
        "municipal": _fc([{**m, "geom": m.get("geom_raw", m["geom"])} for m in ctx["municipal"]], lambda m: {"pid": m.get("record_id"), "plot_no": m.get("plot_id_raw"),
                                                      "owner": m.get("owner_name"), "usage": m.get("land_use")}),
        "footprints": _fc(ctx["footprints"], lambda f: {"source": f["source"], "confidence": f["props"].get("confidence")}),
        "ai_buildings": _fc(ctx.get("ai_buildings", []), lambda f: {"source": f["source"],
                                                                     "confidence": f["props"].get("confidence")}),
        "roads": _fc(ctx.get("roads", []), lambda r: {"name": r["props"].get("name"),
                                                       "class": r["props"].get("highway")}),
        "poi": _fc(ctx.get("poi", []), lambda r: {"name": r["props"].get("name"), "kind": r["props"].get("kind")}),
        "connections": _fc([r for recs in ctx.get("dept_records", {}).values() for r in recs
                            if r.get("geom") is not None and r["geom"].geom_type == "Point"],
                           lambda r: {"dept": r["dept"], "id": r["id"], "name": r.get("name"), "category": r.get("category"),
                                      "status": r.get("status"), "plot_id": r.get("plot_id"), "link": r.get("link_method")}),
        "utility": _fc(ctx["utility"], lambda u: {"id": u.get("record_id"), **u["props"]}),
        "gnss": _fc(ctx["gnss"], lambda g: {"id": g["record_id"], "plot_id": g["plot_id"]}),
        "ground_truth": _fc(ctx["gt"], lambda g: {"id": g.get("record_id"), "plot_id": g.get("plot_id"),
                                                  "owner": g.get("owner_name"), "use": g.get("land_use")}),
    }
    centroids = {p["plot_id"]: p["geom"].centroid for p in parcels}
    conflicts = []
    for c in ctx["conflicts"]:
        co = _conflict_out(c)
        cen = centroids.get(c["plot_id"])
        if cen is not None:
            lon, lat = geo.transformer(config.TARGET_EPSG, config.WGS84).transform(cen.x, cen.y)
            co["center"] = [round(lat, 7), round(lon, 7)]
        conflicts.append(co)
    sev = {"high": 0, "medium": 1, "low": 2}
    conflicts.sort(key=lambda c: (c["status"] != "open", sev[c["severity"]], c["plot_id"] or ""))

    datasets = []
    for d in ctx["datasets"]:
        s = d.summary()
        s["schema"] = d.schema or None
        datasets.append(s)

    meta = geo.read_json(ctx["paths"]["meta"]) if ctx.get("paths", {}).get("meta") and ctx["paths"]["meta"].exists() else {}
    result = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"), "workspace": ctx.get("ws"),
        "ward": meta.get("title", config.WARD_NAME), "workspace_meta": meta,
        "target_crs": f"EPSG:{config.TARGET_EPSG} ({(ctx.get('target_crs') or {}).get('name', '')})",
        "extraction_mode": ctx.get("extraction_mode"), "match_stats": ctx.get("match_stats"),
        "cadastral_comparison": ctx.get("cadastral_comparison"),
        "ruleset": conflicts_ruleset(), "elevation": ctx.get("elevation"), "benchmarks": benchmarks(ctx.get("ws")), "blind_tests": blind_tests(), "revenue_at_risk": revenue,
        "schema_fields": __import__("landconnect.schema_mapper", fromlist=["FIELD_LABELS"]).FIELD_LABELS,
        "building_agreement": ctx.get("building_agreement"), "coregistration": ctx.get("coregistration"),
        "departments": departments.registry(), "dept_link_stats": ctx.get("dept_link_stats"),
        "weights": config.WEIGHTS, "component_labels": LABELS,
        "thresholds": {"auto_approve": config.AUTO_APPROVE, "flag": config.FLAG},
        "steps": [{"key": k, "title": t, "description": d} for k, t, d in STEPS],
        "datasets": datasets, "role_labels": ingest.ROLE_LABELS,
        "crs_report": ctx.get("crs_report"), "georef": ctx.get("georef"), "topology": ctx.get("topology"),
        "matcher": ctx.get("matcher_info"), "evaluation": ctx.get("evaluation"),
        "rasters": ctx.get("rasters", {}), "layers": layers, "conflicts": conflicts,
    }
    result["summary"] = summarize(result)
    return result
