"""Spatial conflict detection and resolution framework.

Every disagreement between sources becomes a conflict ticket. The system resolves it
automatically only when a more reliable source settles it (GNSS for geometry, ground-truth
field verification for ownership); otherwise it goes to the human review queue with every
version shown side by side. Nothing is overwritten silently.
"""
import os

import shapely
from shapely.geometry import Point

from . import config, departments
from .schema_mapper import owner_similarity

GEOM_TOLERANCE_M = 1.5   # mean boundary offset tolerated between cadastral and ULB layers
# Rule set. v1 = the original rules; v2 = stricter evidence for single-source buildings and utility lines
# (see docs/RULES_AND_STANDARDS.md, B21). LANDCONNECT_RULESET=v1 reproduces the old results for comparison.
RULESET = os.environ.get("LANDCONNECT_RULESET", "v2")
ENC_MIN_AREA_M2 = 4.0          # protrusion area below this is outline noise
SINGLE_MIN_DEPTH_FACTOR = 2.5  # a footprint seen in one layer only must protrude 2.5 x its positional tolerance
SINGLE_MIN_AREA_M2 = 10.0
SINGLE_MIN_SHARE = 0.10        # ... and by at least 10 % of its own area
MERGED_SHARE = 0.5             # protrusion mostly covering another plot's mapped building = merged row-house outline
PIPE_MIN_M = 1.0               # a line must run this far under the footprint shrunk by its positional tolerance
BOUNDARY_SIGMA_M = 0.3   # positional accuracy of the fused boundary (GNSS + topology)


def area_tolerance(poly):
    """Relative area difference explained by boundary uncertainty: an error of sigma along the whole
    perimeter changes the area by about perimeter x sigma. Small or thin plots get a wider tolerance;
    never tighter than 10 %."""
    return max(0.10, 1.5 * BOUNDARY_SIGMA_M * poly.length / poly.area)


SEVERITY_CAP = {"high": config.FLAG - 1, "medium": config.AUTO_APPROVE - 1, "low": 100}
TITLES = {
    "OWNER_MISMATCH": "Owner name differs between records",
    "AREA_MISMATCH": "Recorded extent differs from surveyed area",
    "GEOMETRY_DISAGREEMENT": "Boundaries disagree between sources",
    "MERGED_PARCEL": "One municipal property covers several survey plots",
    "NO_REVENUE_RECORD": "Surveyed parcel has no revenue record",
    "RECORD_WITHOUT_PARCEL": "Revenue record has no parcel on the map",
    "MISSING_IN_MUNICIPAL": "Built parcel missing from property-tax register",
    "ENCROACHMENT": "Building crosses the parcel boundary",
    "BUILDING_SPANS_PLOTS": "One building footprint spans two plots",
    "MUTATION_PENDING": "Registered sale not yet mutated in the RoR",
    "TAX_USE_MISMATCH": "Commercial use on a residential tax assessment",
    "SERVICE_WITHOUT_TAX": "Live service connections, no property tax",
    "FLOORS_BEYOND_PERMISSION": "More floors than the building permission allows",
    "UTILITY_UNDER_BUILDING": "Utility line runs under a building",
    "UNASSESSED_CONSTRUCTION": "New construction on land assessed as vacant",
    "DUPLICATE_PARCEL": "Parcel digitised twice",
    "INVALID_GEOMETRY": "Self-intersecting parcel boundary",
    "CADASTRAL_SOURCES_DISAGREE": "Two cadastral surveys disagree",
}
ALT_IOU_MIN = 0.6          # overlap below this between two cadastral surveys of one parcel = they disagree
ALT_OFFSET_MAX_M = 2.0


def _c(type_, key, severity, plot_id, detail, versions=None, suggestion="", geom_hint=None):
    return {"id": f"C-{type_}-{key}", "type": type_, "title": TITLES[type_], "severity": severity,
            "plot_id": plot_id, "detail": detail, "versions": versions or [], "suggestion": suggestion,
            "status": "open", "resolution": None, "resolved_by": None, "geom_hint": geom_hint}


def _auto(c, resolution, choice=None):
    c["status"], c["resolution"], c["resolved_by"] = "auto_resolved", resolution, "system"
    if choice is not None:
        c["chosen"] = choice
    return c


def _building_index(ctx):
    """(footprint, plot) of every fused building and every reference-layer footprint, built once per run."""
    if "_bidx" not in ctx:
        idx = [(b["geom"], b.get("plot_id")) for b in ctx["buildings"]]
        idx += [(g, ctx["assign_parcel"](g)) for g in ctx.get("ref_building_geoms") or [] if not g.is_empty]
        ctx["_bidx"] = idx
    return ctx["_bidx"]


OUTLINE_DISAGREE_SHARE = 0.05   # overlap (of either outline) that makes two layers "the same building"


def _other_layer_overlaps(ctx, b):
    primary = ctx.get("primary_building_source")
    if b["sources"][0] == primary:
        cands = [g for g in ctx.get("ref_building_geoms") or [] if g.intersects(b["geom"])]
    else:
        cands = [x["geom"] for x in ctx["buildings"] if x is not b and x["sources"][0] == primary and x["geom"].intersects(b["geom"])]
    for g in cands:
        a = g.intersection(b["geom"]).area
        if a >= OUTLINE_DISAGREE_SHARE * min(g.area, b["geom"].area):
            return True
    return False


def detect(ctx):
    parcels, out = ctx["parcels"], []
    by_plot = {p["plot_id"]: p for p in parcels}

    for p in parcels:
        pid, ror, muni, gt = p["plot_id"], p.get("ror"), p.get("municipal"), p.get("gt")

        # topology findings (already fixed automatically)
        for f in p.get("topo_flags", []):
            if f.startswith("self-intersection"):
                out.append(_auto(_c("INVALID_GEOMETRY", pid, "low", pid, f"Cadastral polygon crossed itself; {f}."),
                                 "Repaired automatically by topology engine"))
            if f.startswith("duplicate"):
                out.append(_auto(_c("DUPLICATE_PARCEL", pid, "low", pid, "Two cadastral polygons carry this plot ID."),
                                 "Duplicate dropped; original digitisation kept"))

        # ownership: RoR vs municipal register; when there is no municipal record, RoR vs field visit.
        # The 2nd+ plot of a merged municipal property carries the first plot's owner -> skip (MERGED covers it).
        other = None
        if muni and muni.get("owner_name") and not (p.get("municipal_merged") and muni.get("plot_id") != pid):
            other = (muni["owner_name"], "Municipal register", muni.get("record_id"))
        elif gt and gt.get("owner_name") and not muni:
            other = (gt["owner_name"], "Field verification (GT)", gt.get("record_id"))
        if ror and ror.get("owner_name") and other:
            sim = owner_similarity(ror["owner_name"], other[0])
            p["owner_agreement"] = sim
            if sim < config.OWNER_MATCH_THRESHOLD:
                versions = [{"label": ror["owner_name"], "source": "Revenue record (RoR)", "ref": ror.get("record_id")},
                            {"label": other[0], "source": other[1], "ref": other[2]}]
                c = _c("OWNER_MISMATCH", pid, "high", pid,
                       f"RoR says '{ror['owner_name']}', {other[1].lower()} says '{other[0]}' "
                       f"(name similarity {sim:.0f}%). Likely an un-mutated sale or inheritance.",
                       versions, "Verify ownership documents; update the stale record")
                if gt and gt.get("owner_name"):
                    sims = [owner_similarity(v["label"], gt["owner_name"]) for v in versions]
                    k = max(range(2), key=lambda i: sims[i])
                    if sims[k] >= config.OWNER_MATCH_THRESHOLD:
                        stale = versions[1 - k]["source"]
                        _auto(c, f"Field verification {gt.get('record_id')} confirms '{gt['owner_name']}'. "
                                 f"{stale} is stale; mutation to be raised.", k)
                out.append(c)

        # extent
        if ror and ror.get("area_sqm") and p["geom"].area > 1:
            surveyed = p["geom"].area
            diff = (ror["area_sqm"] - surveyed) / surveyed
            tol = area_tolerance(p["geom"])
            p["area_agreement"] = max(0.0, 1 - abs(diff) / (2 * tol))
            if abs(diff) > tol:
                c = _c("AREA_MISMATCH", pid, "medium", pid,
                       f"RoR extent {ror['area_sqm']:.0f} m² vs surveyed {surveyed:.0f} m² ({diff * 100:+.0f}%, "
                       f"tolerance ±{tol * 100:.0f}% for this plot).",
                       [{"label": f"{surveyed:.1f} m²", "source": "Surveyed boundary (fused)"},
                        {"label": f"{ror['area_sqm']:.1f} m²", "source": "Revenue record (RoR)"}],
                       "Correct RoR extent to the surveyed area")
                if p.get("gnss_vertices", 0) >= 3:
                    _auto(c, "Boundary is GNSS-surveyed; RoR extent to be corrected to surveyed area.", 0)
                out.append(c)

        # geometry agreement with municipal layer (after co-registration): mean boundary offset beyond the
        # positional tolerance of a ULB layer, or hardly any overlap
        if muni and not p.get("municipal_merged"):
            iou = p["match_features"]["iou"]
            off = p.get("muni_offset_m", 0.0)
            if off > GEOM_TOLERANCE_M or iou < 0.5:
                c = _c("GEOMETRY_DISAGREEMENT", pid, "medium", pid,
                       f"Cadastral and municipal boundaries differ by {off:.1f} m on average "
                       f"(overlap {iou * 100:.0f}%), beyond the {GEOM_TOLERANCE_M} m tolerance.",
                       [{"label": "Cadastral + GNSS (fused)", "source": "cadastral", "geom": p["geom"]},
                        {"label": "Municipal polygon", "source": "municipal", "geom": muni["geom"]}],
                       "Keep the higher-reliability boundary")
                if p.get("gnss_vertices", 0) >= 3:
                    _auto(c, "GNSS-controlled boundary outranks the municipal polygon.", 0)
                out.append(c)

        if not ror:
            out.append(_c("NO_REVENUE_RECORD", pid, "high", pid,
                          "Parcel exists on the cadastral map but no RoR entry carries this khasra number.",
                          suggestion="Create or trace the revenue record"))
        if not muni and p.get("buildings"):
            out.append(_c("MISSING_IN_MUNICIPAL", pid, "medium", pid,
                          f"{len(p['buildings'])} building(s) on this plot but no property-tax record.",
                          suggestion="Add the property to the tax register"))

    # municipal polygon covering several parcels
    for j, members in ctx.get("municipal_merges", {}).items():
        m = ctx["municipal"][j]
        ids = [parcels[i]["plot_id"] for i in members]
        out.append(_c("MERGED_PARCEL", m["record_id"], "medium", ids[0],
                      f"Municipal property {m['record_id']} spans survey plots {', '.join(ids)}.",
                      [{"label": f"Split into {len(ids)} assessments", "source": "cadastral"},
                       {"label": "Keep one combined assessment", "source": "municipal"}],
                      "Split the tax assessment per survey plot, or record an amalgamation",
                      geom_hint=m["geom"]))

    # revenue records with no geometry
    for r in ctx.get("ror_orphans", []):
        out.append(_c("RECORD_WITHOUT_PARCEL", r["plot_id"], "high", r["plot_id"],
                      f"RoR khasra {r.get('plot_id_raw')} ({r.get('owner_name')}) matches no parcel on the map.",
                      suggestion="Locate in the field and add to the cadastral map, or retire the record"))

    # buildings
    for b in ctx["buildings"]:
        p = by_plot.get(b["plot_id"])
        if p is None:
            continue
        outside = b["geom"].difference(p["geom"])
        tol = b.get("pos_tol_m", 0.5)
        depth = max(Point(xy).distance(p["geom"]) for xy in b["geom"].exterior.coords) if outside.area > 0 else 0
        shares = sorted((q["geom"].intersection(b["geom"]).area / b["geom"].area, q["plot_id"])
                        for q in ctx["parcel_tree_query"](b["geom"]))[::-1]
        if len(shares) > 1 and shares[0][0] < 0.75 and shares[1][0] >= 0.2:
            # half in one plot, half in another: a merged footprint or amalgamated plots, not a sliver over the line
            out.append(_c("BUILDING_SPANS_PLOTS", f"{b['id']}", "medium", p["plot_id"],
                          f"Building {b['id']} lies {shares[0][0] * 100:.0f}% in plot {shares[0][1]} and "
                          f"{shares[1][0] * 100:.0f}% in plot {shares[1][1]}.",
                          suggestion="Check for plot amalgamation, or a footprint that merges two buildings",
                          geom_hint=b["geom"]))
            continue
        # beyond the positional tolerance of the building source; if a second source exists it must agree
        ref_depth = (max(Point(xy).distance(p["geom"]) for xy in shapely.get_coordinates(b["ref_geom"]))
                     if b.get("ref_geom") is not None and not b["ref_geom"].is_empty else None)
        single = ref_depth is None and len(b.get("sources", [])) < 2 and b.get("pos_tol_m", 0.5) > 0.5
        flag = outside.area > ENC_MIN_AREA_M2 and depth > tol and (ref_depth is None or ref_depth > tol)
        if flag and RULESET != "v1" and single:
            # one layer only: demand clearly more than its positional error, and rule out a merged outline
            flag = (depth > SINGLE_MIN_DEPTH_FACTOR * tol and outside.area >= SINGLE_MIN_AREA_M2
                    and outside.area >= SINGLE_MIN_SHARE * b["geom"].area)
            if flag:
                # the protrusion lies on another plot's mapped building (in any layer): two buildings cannot
                # occupy the same ground, so this is a merged or mis-traced outline, not an encroachment
                others = [g for g, pid in _building_index(ctx) if pid not in (None, p["plot_id"]) and g.intersects(outside)]
                covered = sum(g.intersection(outside).area for g in others)
                flag = covered < MERGED_SHARE * outside.area
            if flag:
                # the other building layer has this building with a different outline: the layers disagree about
                # the shape, which is not evidence that the building crosses the boundary
                flag = not _other_layer_overlaps(ctx, b)
        if flag:
            into = "road / public land"
            for q in ctx["parcel_tree_query"](outside):
                if q["plot_id"] != p["plot_id"] and q["geom"].intersection(outside).area > 0.5:
                    into = f"plot {q['plot_id']}"
            out.append(_c("ENCROACHMENT", p["plot_id"], "medium" if single else "high", p["plot_id"],
                          f"Building {b['id']} extends {depth:.1f} m ({outside.area:.0f} m²) beyond the boundary "
                          f"into {into}." + (" Seen in one building layer only - verify." if single else
                                             " Confirmed by both building layers." if ref_depth else ""),
                          suggestion="Issue notice; verify on site", geom_hint=outside))
        core = b["geom"] if RULESET == "v1" else b["geom"].buffer(-b.get("pos_tol_m", 0.5))
        for u in ctx["utility"]:
            inter = u["geom"].intersection(core) if not core.is_empty else None
            if inter is not None and inter.length > (0.5 if RULESET == "v1" else PIPE_MIN_M):
                inter = u["geom"].intersection(b["geom"])
                out.append(_c("UTILITY_UNDER_BUILDING", f"{u['record_id']}-{b['id']}", "medium", p["plot_id"],
                              f"{u['props'].get('asset_type', 'utility').replace('_', ' ')} {u['record_id']} "
                              f"runs {inter.length:.1f} m under building {b['id']}.",
                              suggestion="Relocate the line or record an easement", geom_hint=inter))
                out[-1]["utility_dept"] = departments.UTILITY_OWNER.get(u["props"].get("asset_type"), "water")

    for ch in ctx["changes"]:
        if ch["type"] != "new" or not ch["plot_id"]:
            continue
        p = by_plot.get(ch["plot_id"])
        muni = p.get("municipal") if p else None
        if muni is None or muni.get("land_use") == "vacant":
            where = "assessed as vacant" if muni else "with no tax record"
            out.append(_c("UNASSESSED_CONSTRUCTION", ch["plot_id"], "medium", ch["plot_id"],
                          f"{ch['detail']} built since the last survey on land {where}.",
                          suggestion="Reassess property tax", geom_hint=ch["geom"]))
    for p in parcels:
        a = p.get("alt_cadastral")
        if not a:
            continue
        bad_geom = a["iou"] < ALT_IOU_MIN or a["offset_m"] > ALT_OFFSET_MAX_M
        bad_id = a["alt_id"] and not a["id_agrees"]
        if bad_geom or bad_id:
            why = []
            if bad_geom:
                why.append(f"the boundaries overlap {a['iou'] * 100:.0f}% (mean offset {a['offset_m']:.1f} m)")
            if bad_id:
                why.append(f"{a['source'] or 'the second survey'} numbers it {a['alt_id']}")
            out.append(_c("CADASTRAL_SOURCES_DISAGREE", p["plot_id"], "medium", p["plot_id"],
                          f"Plot {p['plot_id']}: two independent cadastral surveys disagree - " + " and ".join(why) + ".",
                          [{"label": f"Plot {p['plot_id']} (main cadastral)", "source": "cadastral", "geom": p["geom"]},
                           {"label": f"{a['alt_id'] or 'unnumbered'} ({a['source'] or 'second survey'})", "source": "cadastral_alt",
                            "geom": a["geom"]}],
                          "Resolve with a field survey (DGPS) and correct the wrong source"))
            if p.get("gnss_vertices", 0) >= 3:
                _auto(out[-1], "GNSS-controlled boundary outranks the second survey.", 0)
    out += departments.cross_conflicts(ctx, out, _c, _auto)
    departments.route(out)
    seen = {}
    for c in out:                          # e.g. two encroaching buildings on one plot: keep every id unique
        n = seen[c["id"]] = seen.get(c["id"], 0) + 1
        if n > 1:
            c["id"] = f"{c['id']}#{n}"
    return out
