"""Accuracy evaluation against the hidden synthetic truth (never used by the pipeline itself)."""
import numpy as np
import shapely
from shapely import wkt
from shapely.strtree import STRtree

from .schema_mapper import owner_similarity


def _boundary_offset(truth_poly, poly, step=0.5):
    """Mean distance from points every `step` m along the true boundary to the candidate boundary."""
    ring = truth_poly.exterior
    pts = shapely.line_interpolate_point(ring, np.arange(0, ring.length, step))
    return float(np.mean(shapely.distance(pts, poly.exterior)))


def _stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {"mean_m": round(float(np.mean(vals)), 3), "p95_m": round(float(np.percentile(vals, 95)), 3),
            "n": len(vals)}


def _pr(pred, true):
    pred, true = set(pred), set(true)
    tp = len(pred & true)
    return {"planted": len(true), "detected": tp, "false_alarms": len(pred - true),
            "precision": round(tp / len(pred), 3) if pred else None,
            "recall": round(tp / len(true), 3) if true else None}


def run(ctx, truth):
    tp = {t["plot_id"]: wkt.loads(t["wkt"]) for t in truth["parcels"]}
    truth = {"municipal_map": {}, "municipal_stale_owner": [], "ror_stale_owner": [], "area_mismatch": [],
             "encroachments": [], "legacy_pipes": [], "ror_missing": [], "ror_orphans": [], "cadastral_missing": [],
             "cadastral_bowties": [], "changes": [], "schema": {}, **truth}
    towner = {t["plot_id"]: t["owner"] for t in truth["parcels"]}
    parcels = ctx["parcels"]
    res = {}

    # --- positional accuracy by stage
    muni_inv = {pid: m for m, pids in truth["municipal_map"].items() if len(pids) == 1 for pid in pids}
    muni_by_id = {m["record_id"]: m for m in ctx["municipal"]}
    stages = {
        "Municipal layer as delivered": [
            _boundary_offset(tp[pid], muni_by_id[m].get("geom_raw", muni_by_id[m]["geom"]))
            for pid, m in muni_inv.items() if m in muni_by_id],
        "Cadastral, dragged into place (translation only)": [
            _boundary_offset(tp[c["plot_id"]], c["geom_naive"]) for c in ctx["cadastral"]
            if c["plot_id"] in tp and c["geom_naive"].is_valid],
        "Cadastral, affine georeferenced": [
            _boundary_offset(tp[c["plot_id"]], c["geom"]) for c in ctx["cadastral"]
            if c["plot_id"] in tp and c["geom"].is_valid],
        "Fused output (GNSS + topology)": [_boundary_offset(tp[p["plot_id"]], p["geom"]) for p in parcels
                                           if p["plot_id"] in tp],
    }
    res["positional"] = {k: _stats(v) for k, v in stages.items()}
    err = {p["plot_id"]: _boundary_offset(tp[p["plot_id"]], p["geom"]) for p in parcels if p["plot_id"] in tp}

    # --- georeferencing: recovered vs true transform (truth maps UTM -> local; we estimate local -> UTM)
    T = truth.get("transform_utm_to_local")
    rep = ctx["georef"]["transform"]
    if T:
        res["georeferencing"] = {"true_rotation_deg": -T["theta_deg"], "estimated_rotation_deg": rep["rotation_deg"],
                                 "true_scale": round(1 / T["scale"], 6), "estimated_scale": rep["scale_x"]}

    # --- schema mapping
    ok = tot = 0
    for ds in ctx["datasets"]:
        want = truth["schema"].get(ds.name)
        if want is None or not ds.schema:
            continue
        got = {m["field"]: m["column"] for m in ds.schema["mappings"]}
        for f, col in want.items():
            tot += 1
            ok += got.get(f) == col
        ok -= sum(1 for f in got if f not in want)  # penalise spurious mappings
    res["schema_mapping"] = {"columns_expected": tot, "correct": ok, "accuracy": round(ok / tot, 3) if tot else None}

    # --- parcel <-> municipal matching
    truth_link = {pid: m for m, pids in truth["municipal_map"].items() for pid in pids}
    pred = {(p["plot_id"], p["municipal"]["record_id"]) for p in parcels if p.get("municipal")}
    true = {(p["plot_id"], truth_link[p["plot_id"]]) for p in parcels if p["plot_id"] in truth_link}
    m = _pr(pred, true)
    res["matching"] = {"precision": m["precision"], "recall": m["recall"], "links": m["planted"],
                       "f1": round(2 * m["precision"] * m["recall"] / (m["precision"] + m["recall"]), 3)}

    # --- measured heights (real footprints + elevation model): compare with the heights that were simulated
    if truth.get("building_heights"):
        th = [(wkt.loads(b["wkt"]), b["height"], b["floors"]) for b in truth["building_heights"]]
        tree = STRtree([g for g, _, _ in th])
        errs, fl_ok = [], 0
        for b in ctx["buildings"]:
            if not b.get("height"):
                continue
            best, bi = 0, None
            for i in tree.query(b["geom"]):
                i = int(i)
                iou = b["geom"].intersection(th[i][0]).area / b["geom"].union(th[i][0]).area
                if iou > best:
                    best, bi = iou, i
            if best >= 0.5:
                errs.append(abs(b["height"] - th[bi][1]))
                fl_ok += b.get("floors") == th[bi][2]
        if errs:
            res["heights"] = {"buildings": len(errs), "height_mae_m": round(float(np.mean(errs)), 2),
                              "floors_correct": round(fl_ok / len(errs), 3)}

    # --- building extraction (current epoch) - only where the true buildings are known
    if not truth.get("buildings_t2"):
        return _finish(res, ctx, truth, tp, towner, err, parcels)
    tb = [(wkt.loads(b["wkt"]), b["height"]) for b in truth["buildings_t2"]]
    tree = STRtree([g for g, _ in tb])
    hits, ious, herr, used = 0, [], [], set()
    for b in ctx["buildings"]:
        best, bi = 0, None
        for i in tree.query(b["geom"]):
            i = int(i)
            iou = b["geom"].intersection(tb[i][0]).area / b["geom"].union(tb[i][0]).area
            if iou > best:
                best, bi = iou, i
        if best >= 0.5 and bi not in used:
            used.add(bi)
            hits += 1
            ious.append(best)
            herr.append(abs(b["height"] - tb[bi][1]))
    P, R = hits / max(len(ctx["buildings"]), 1), hits / max(len(tb), 1)
    res["extraction"] = {"precision": round(P, 3), "recall": round(R, 3), "f1": round(2 * P * R / (P + R), 3),
                         "mean_iou": round(float(np.mean(ious)), 3), "height_mae_m": round(float(np.mean(herr)), 3),
                         "buildings_true": len(tb), "buildings_found": len(ctx["buildings"])}

    return _finish(res, ctx, truth, tp, towner, err, parcels)


def _finish(res, ctx, truth, tp, towner, err, parcels):
    # --- change detection
    if not truth["changes"]:
        res["change_detection"] = {}
    ch = {}
    for t in ("new", "demolished", "extension", "new_floor"):
        ch[t] = _pr([c["plot_id"] for c in ctx["changes"] if c["type"] == t],
                    [c["plot_id"] for c in truth["changes"] if c["type"] == t])
    if truth["changes"]:
        res["change_detection"] = ch

    # --- planted issues -> detected conflicts
    conf = ctx["conflicts"]

    def plots(t):
        return [c["plot_id"] for c in conf if c["type"] == t]

    unassessed = (truth["unassessed_new"] if "unassessed_new" in truth
                  else [c["plot_id"] for c in truth["changes"] if c["type"] == "new"])
    # a stale owner can only be spotted when a second, independent source names the owner
    has_second = {p["plot_id"] for p in parcels if (p.get("municipal") and not p.get("municipal_merged")) or p.get("gt")}
    stale = truth["ror_stale_owner"] + truth["municipal_stale_owner"]
    issues = {
        "Stale owner in RoR or municipal record": _pr(plots("OWNER_MISMATCH"), [s for s in stale if s in has_second]),
        "Wrong extent in RoR": _pr(plots("AREA_MISMATCH"), truth["area_mismatch"]),
        "Encroaching building": _pr(plots("ENCROACHMENT"), truth["encroachments"]),
        "Legacy pipe under buildings": _pr({c["id"].split("-")[2] + "-" + c["id"].split("-")[3]
                                            for c in conf if c["type"] == "UTILITY_UNDER_BUILDING"},
                                           truth["legacy_pipes"]),
        "Merged municipal properties": _pr([c["id"].replace("C-MERGED_PARCEL-", "") for c in conf
                                            if c["type"] == "MERGED_PARCEL"],
                                           [m for m, pids in truth["municipal_map"].items() if len(pids) > 1]),
        "Parcel without revenue record": _pr(plots("NO_REVENUE_RECORD"), truth["ror_missing"]),
        "Revenue record without parcel": _pr(plots("RECORD_WITHOUT_PARCEL"),
                                             truth["ror_orphans"] + truth["cadastral_missing"]),
        "Unassessed new construction": _pr(plots("UNASSESSED_CONSTRUCTION"), unassessed),
        "Self-intersecting polygon": _pr(ctx["topology"]["invalid_repaired"], truth["cadastral_bowties"]),
        "Duplicate parcel": _pr(ctx["topology"]["duplicates_removed"],
                                [truth["cadastral_duplicate"]] if truth.get("cadastral_duplicate") else []),
    }
    if truth.get("dept_links"):
        svc = {c["plot_id"] for c in conf if c.get("service_evidence")}
        issues["Registered sale not mutated in RoR"] = _pr(plots("MUTATION_PENDING"), truth.get("mutation_pending", []))
        issues["Commercial use on residential tax"] = _pr(plots("TAX_USE_MISMATCH"), truth.get("tax_use_mismatch", []))
        issues["Live services, no property tax"] = _pr(svc, truth.get("service_without_tax", []))
        issues["Floors beyond building permission"] = _pr(plots("FLOORS_BEYOND_PERMISSION"),
                                                          truth.get("floors_beyond_permission", []))
        tl = truth["dept_links"]
        got = [(r["id"], r.get("plot_id")) for recs in ctx.get("dept_records", {}).values() for r in recs if r["id"] in tl]
        res["dept_linking"] = {"records": len(got), "correct": sum(1 for i, pid in got if tl[i] == pid),
                               "accuracy": round(sum(1 for i, pid in got if tl[i] == pid) / max(len(got), 1), 4)}
    for k in truth.get("unscored", []):
        issues.pop(k, None)
    res["issues"] = issues
    res["unscored"] = truth.get("unscored", [])
    # --- one headline number: every planted problem and every detected change, pooled (micro-average)
    pooled = list(issues.values()) + list(res.get("change_detection", {}).values())
    tp_ = sum(o["detected"] for o in pooled)
    fp_ = sum(o["false_alarms"] for o in pooled)
    fn_ = sum(o["planted"] - o["detected"] for o in pooled)
    P = tp_ / (tp_ + fp_) if tp_ + fp_ else None
    Rc = tp_ / (tp_ + fn_) if tp_ + fn_ else None
    res["overall"] = {"found": tp_, "false_alarms": fp_, "missed": fn_,
                      "precision": round(P, 3) if P is not None else None, "recall": round(Rc, 3) if Rc is not None else None,
                      "f1": round(2 * P * Rc / (P + Rc), 3) if P and Rc else None}
    res["issues_note"] = (f"{len(set(stale) - has_second)} stale owner record(s) sit on parcels with no second "
                          f"source naming the owner, so no system could detect them; they are excluded above.")

    # --- is the confidence score honest? error rate per status
    calib = {}
    for p in parcels:
        pid = p["plot_id"]
        if pid not in err:
            continue
        owner_ok = True
        if p.get("resolved_owner"):
            owner_ok = owner_similarity(p["resolved_owner"], towner[pid]) >= 75
        wrong = err[pid] > 0.5 or not owner_ok
        s = calib.setdefault(p["status"], {"parcels": 0, "with_error": 0})
        s["parcels"] += 1
        s["with_error"] += wrong
    for s in calib.values():
        s["error_rate"] = round(s["with_error"] / s["parcels"], 3)
    res["calibration"] = calib
    # --- does a higher score really mean fewer errors? share of parcels that are right, per score band
    bands = [(0, 50), (50, 70), (70, 85), (85, 95), (95, 101)]
    rows = []
    for lo, hi in bands:
        grp = [p for p in parcels if p["plot_id"] in err and lo <= p.get("score", 0) < hi]
        if not grp:
            continue
        right = 0
        for p in grp:
            ok = err[p["plot_id"]] <= 0.5
            if ok and p.get("resolved_owner"):
                ok = owner_similarity(p["resolved_owner"], towner[p["plot_id"]]) >= 75
            right += ok
        rows.append({"band": f"{lo}–{min(hi, 100)}", "parcels": len(grp), "correct": round(right / len(grp), 3)})
    res["calibration_bands"] = rows
    return res
