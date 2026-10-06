"""Government bodies that hold data about a parcel, what each one is the authority for, and the
cross-department checks that only become possible once their records are linked to one parcel.

Linking: every department record (electricity consumer, water / gas connection, building permission,
registered deed) is tied to a parcel by its plot reference AND by its GPS location. When the two
disagree, a GPS point that falls inside a parcel wins (clerks mistype survey / hissa numbers far more
often than a meter moves); a point outside every parcel (a meter on the road edge) defers to the
reference. The method used is recorded on every link.
"""
import numpy as np
from shapely.geometry import Point
from shapely.strtree import STRtree

from . import config
from .schema_mapper import normalize_plot_id, owner_similarity

DEPARTMENTS = {
    "revenue": {"name": "Revenue Department (RTC / RoR)", "short": "Revenue", "color": "#9a6b12",
                "holds": "Record of rights: owner, extent, land classification"},
    "survey": {"name": "Survey, Settlement & Land Records", "short": "Survey", "color": "#5b6b7a",
               "holds": "Cadastral maps, parcel boundaries, GNSS control"},
    "municipal": {"name": "Urban Local Body — property tax", "short": "Municipal", "color": "#6d4bb0",
                  "holds": "Property assessments (PID, assessed use, tax area)"},
    "town_planning": {"name": "Town Planning — building permissions", "short": "Town planning", "color": "#b0461e",
                      "holds": "Building plan approvals (permitted floors, built-up area)"},
    "registration": {"name": "Stamps & Registration (IGR)", "short": "Registration", "color": "#1f7a5a",
                     "holds": "Registered sale deeds (buyer, seller, date)"},
    "water": {"name": "Water Supply & Sewerage Board", "short": "Water", "color": "#2a7ab8",
              "holds": "Water connections, water mains, sewer network"},
    "electricity": {"name": "Electricity Distribution Company", "short": "Electricity", "color": "#c79a0a",
                    "holds": "Consumer accounts (RR no., tariff, sanctioned load), lines"},
    "gas": {"name": "City Gas Distribution", "short": "Gas", "color": "#c2410c",
            "holds": "Piped gas connections and pipelines"},
    "roads": {"name": "Roads & Public Works", "short": "Roads", "color": "#4b5563",
              "holds": "Road network, right of way, encroachment notices"},
}

# Fields each department is the legal authority for (only its officers can propose changes to them)
FIELDS = {
    "owner": {"dept": "revenue", "label": "Owner (RoR)", "type": "text", "evidence": True,
              "notify": ["municipal", "water", "electricity", "gas"], "closes": ["OWNER_MISMATCH", "MUTATION_PENDING"]},
    "ror_extent_sqm": {"dept": "revenue", "label": "Recorded extent (m²)", "type": "number", "evidence": True,
                       "notify": ["municipal"], "closes": ["AREA_MISMATCH"]},
    "land_use": {"dept": "revenue", "label": "Land classification", "type": "choice", "evidence": True,
                 "choices": ["residential", "commercial", "institutional", "vacant"], "notify": ["municipal", "electricity"],
                 "closes": []},
    "geometry": {"dept": "survey", "label": "Parcel boundary (GeoJSON polygon)", "type": "geometry", "evidence": True,
                 "notify": ["municipal", "town_planning", "roads"], "closes": ["GEOMETRY_DISAGREEMENT", "AREA_MISMATCH"]},
    "municipal_pid": {"dept": "municipal", "label": "Property ID (tax)", "type": "text", "evidence": False,
                      "notify": [], "closes": ["MISSING_IN_MUNICIPAL", "SERVICE_WITHOUT_TAX", "UNASSESSED_CONSTRUCTION"]},
    "municipal_use": {"dept": "municipal", "label": "Assessed use (tax)", "type": "choice", "evidence": False,
                      "choices": ["residential", "commercial", "institutional", "vacant"], "notify": ["electricity", "water"],
                      "closes": ["TAX_USE_MISMATCH"]},
    "approved_floors": {"dept": "town_planning", "label": "Permitted floors", "type": "int", "evidence": True,
                        "notify": ["municipal"], "closes": ["FLOORS_BEYOND_PERMISSION"]},
    "registered_owner": {"dept": "registration", "label": "Buyer on latest registered deed", "type": "text",
                         "evidence": True, "notify": ["revenue"], "closes": []},
    "tariff": {"dept": "electricity", "label": "Electricity tariff", "type": "choice", "evidence": False,
               "choices": ["LT-2a Domestic", "LT-3 Commercial", "LT-5 Industrial", "LT-6 Public"], "notify": ["municipal"],
               "closes": ["TAX_USE_MISMATCH"]},
    "electricity_consumer": {"dept": "electricity", "label": "Electricity account holder", "type": "text",
                             "evidence": False, "notify": [], "closes": []},
    "water_type": {"dept": "water", "label": "Water connection type", "type": "choice", "evidence": False,
                   "choices": ["Domestic", "Non-Domestic"], "notify": ["municipal"], "closes": ["TAX_USE_MISMATCH"]},
    "water_consumer": {"dept": "water", "label": "Water account holder", "type": "text", "evidence": False,
                       "notify": [], "closes": []},
    "gas_consumer": {"dept": "gas", "label": "Gas account holder", "type": "text", "evidence": False,
                     "notify": [], "closes": []},
    "road_notice": {"dept": "roads", "label": "Encroachment notice no.", "type": "text", "evidence": True,
                    "notify": ["municipal"], "closes": []},
    "parcel_add": {"dept": "survey", "label": "Add a new plot (boundary drawn on the map)", "type": "parcel", "evidence": True,
                   "notify": ["revenue", "municipal", "town_planning"], "closes": []},
    "building_add": {"dept": "survey", "label": "Map a building (drawn on the map)", "type": "building", "evidence": False,
                     "notify": ["municipal", "town_planning"], "closes": []},
    "building_edit": {"dept": "survey", "label": "Correct a building's outline or floors", "type": "building_edit",
                      "evidence": False, "notify": ["municipal", "town_planning"], "closes": []},
    "building_remove": {"dept": "survey", "label": "Remove a mapped building (demolished or wrong)", "type": "building_ref",
                        "evidence": False, "notify": ["municipal", "town_planning"], "closes": []},
}

# Which departments must act on each conflict type (review queue routing)
ROUTING = {
    "OWNER_MISMATCH": ["revenue", "municipal"], "AREA_MISMATCH": ["revenue", "survey"],
    "GEOMETRY_DISAGREEMENT": ["survey", "municipal"], "MERGED_PARCEL": ["municipal"],
    "NO_REVENUE_RECORD": ["revenue"], "RECORD_WITHOUT_PARCEL": ["revenue", "survey"],
    "MISSING_IN_MUNICIPAL": ["municipal"], "ENCROACHMENT": ["roads", "municipal", "town_planning"],
    "BUILDING_SPANS_PLOTS": ["survey", "municipal"], "UTILITY_UNDER_BUILDING": ["water"],
    "UNASSESSED_CONSTRUCTION": ["municipal", "town_planning"], "DUPLICATE_PARCEL": ["survey"],
    "INVALID_GEOMETRY": ["survey"], "MUTATION_PENDING": ["revenue", "registration"],
    "TAX_USE_MISMATCH": ["municipal", "electricity", "water"], "SERVICE_WITHOUT_TAX": ["municipal"],
    "FLOORS_BEYOND_PERMISSION": ["town_planning", "municipal"],
    "CADASTRAL_SOURCES_DISAGREE": ["survey"],
}
UTILITY_OWNER = {"water_main": "water", "legacy_pipe": "water", "sewer_main": "water", "legacy_sewer": "water",
                 "gas_line": "gas", "power_line": "electricity"}

# Dataset role -> department, and how to read each record type
RECORD_ROLES = {"electricity": "electricity", "water": "water", "gas": "gas", "permissions": "town_planning",
                "deeds": "registration"}
COLS = {  # canonical attribute -> column name synonyms (matched on lower-case, punctuation-free names)
    "id": ["rr no", "rr_no", "consumer no", "consumer_no", "bp no", "bp_no", "permission no", "permission_no",
           "document no", "document_no", "account no"],
    "name": ["consumer name", "consumer_name", "name", "customer name", "customer_name", "applicant", "account holder"],
    "category": ["tariff", "connection type", "connection_type", "category"],
    "status": ["status", "connection status"],
    "load": ["sanctioned load kw", "sanctioned_load_kw", "load kw"],
    "floors": ["approved floors", "approved_floors", "permitted floors"],
    "bua": ["approved bua sqm", "approved_bua_sqm"],
    "date": ["date", "registration date", "registration_date", "approval date"],
    "buyer": ["buyer", "purchaser", "claimant"], "seller": ["seller", "vendor", "executant"],
    "plot": ["survey ref", "survey_ref", "plot ref", "plot_ref", "sy no", "plot no"],
}
COMMERCIAL_TARIFF = ("LT-3", "LT-5", "HT")


_JUNK = {"id", "description", "timestamp", "begin", "end", "altitudemode", "tessellate", "extrude", "visibility",
         "draworder", "icon", "latitude", "longitude", "lat", "lon", "lng", "x", "y", "geometry", "fid"}


def clean_fields(props, limit=40):
    """Every column of a source record, as delivered, for the department's detail view (junk and blanks dropped)."""
    out = {}
    for k, v in (props or {}).items():
        if str(k).lower() in _JUNK or v is None:
            continue
        if isinstance(v, float):
            if v != v:                                           # NaN
                continue
            v = int(v) if v.is_integer() and abs(v) < 1e15 else round(v, 6)
        elif not isinstance(v, (int, bool)):
            v = str(v).strip()
            if not v or v.lower() in ("nan", "none", "null", "nat"):
                continue
        out[str(k)] = v
        if len(out) >= limit:
            break
    return out


def find_field(props, *names):
    want = {_norm(n) for n in names}
    for k, v in (props or {}).items():
        if _norm(k) in want and v not in (None, ""):
            return v
    return None


def _norm(c):
    return " ".join(str(c).lower().replace("_", " ").replace(".", " ").replace("(", " ").replace(")", " ").split())


def _col(props, key):
    want = {_norm(s) for s in COLS[key]}
    for k in props:
        if _norm(k) in want:
            return k
    return None


def read_records(recs, dept):
    """Canonical department records from a loaded dataset (already reprojected)."""
    if not recs:
        return []
    cols = {k: _col(recs[0]["props"], k) for k in COLS}
    out = []
    for r in recs:
        p = r["props"]
        g = lambda k: p.get(cols[k]) if cols[k] else None  # noqa: E731
        out.append({"dept": dept, "id": g("id") or r.get("record_id"), "name": g("name") or r.get("owner_name"),
                    "category": g("category"), "status": g("status") or "Active", "load": g("load"),
                    "floors": int(g("floors")) if g("floors") not in (None, "") else None, "bua": g("bua"),
                    "date": g("date"), "buyer": g("buyer"), "seller": g("seller"),
                    "plot_ref": normalize_plot_id(g("plot")) if g("plot") else r.get("plot_id"),
                    "geom": r.get("geom"), "source": r.get("source"), "fields": clean_fields(p)})
    return out


def link(ctx):
    """Attach every department record to a parcel; ctx['dept_records'][dept] -> list with plot_id + method."""
    parcels = ctx["parcels"]
    by_id = {p["plot_id"]: p for p in parcels}
    geoms = [p["geom"] for p in parcels]
    tree = STRtree(geoms) if geoms else None
    stats = {}
    for dept, recs in ctx.get("dept_records", {}).items():
        n_ok = 0
        for r in recs:
            by_ref = r["plot_ref"] if r["plot_ref"] in by_id else None
            by_loc, method, inside_hit = None, None, False
            pt = r["geom"] if r.get("geom") is not None and r["geom"].geom_type == "Point" else None
            if pt is not None and tree is not None:
                inside = [int(i) for i in tree.query(pt, predicate="intersects")]
                if inside:
                    by_loc, inside_hit = parcels[inside[0]]["plot_id"], True
                else:
                    near = tree.nearest(pt)
                    if near is not None and geoms[int(near)].distance(pt) <= 5:
                        by_loc = parcels[int(near)]["plot_id"]
            if by_ref and by_loc and by_ref != by_loc:
                if inside_hit:
                    r["plot_id"], method = by_loc, "location (reference disagrees)"
                else:
                    r["plot_id"], method = by_ref, "reference (point outside parcels)"
            elif by_ref:
                r["plot_id"], method = by_ref, "reference + location" if by_loc == by_ref else "reference"
            elif by_loc:
                r["plot_id"], method = by_loc, "location"
            else:
                r["plot_id"], method = None, "unlinked"
            r["link_method"] = method
            if r["plot_id"]:
                n_ok += 1
                by_id[r["plot_id"]].setdefault("dept", {}).setdefault(dept, []).append(r)
        stats[dept] = {"records": len(recs), "linked": n_ok}
    ctx["dept_link_stats"] = stats
    if stats:
        ctx["log"].append("Department records linked: " + ", ".join(
            f"{DEPARTMENTS[d]['short']} {s['linked']}/{s['records']}" for d, s in stats.items()))


def _live(recs):
    return [r for r in recs if str(r.get("status") or "").lower() not in ("disconnected", "closed", "inactive")]


def cross_conflicts(ctx, conflicts, make, auto):
    """Checks that need more than one department. `make` / `auto` are conflicts._c / conflicts._auto."""
    by_id_conf = {}
    for c in conflicts:
        by_id_conf.setdefault((c["type"], c["plot_id"]), c)
    out = []
    proposals = []
    for p in ctx["parcels"]:
        pid, d = p["plot_id"], p.get("dept", {})
        muni = p.get("municipal") or {}
        elec, water, gas = _live(d.get("electricity", [])), _live(d.get("water", [])), _live(d.get("gas", []))
        # 1. registered sale not yet mutated in the RoR
        deeds = sorted(d.get("registration", []), key=lambda r: str(r.get("date") or ""))
        ror = p.get("ror") or {}
        if deeds and ror.get("owner_name"):
            last = deeds[-1]
            if last.get("buyer") and owner_similarity(last["buyer"], ror["owner_name"]) < config.OWNER_MATCH_THRESHOLD:
                out.append(make("MUTATION_PENDING", pid, "high", pid,
                                f"Deed {last['id']} registered on {last.get('date')} transfers the plot from "
                                f"'{last.get('seller')}' to '{last['buyer']}', but the RoR still names '{ror['owner_name']}'.",
                                [{"label": last["buyer"], "source": "Registered deed (IGR)", "ref": last["id"]},
                                 {"label": ror["owner_name"], "source": "Revenue record (RoR)", "ref": ror.get("record_id")}],
                                "Mutate the RoR in favour of the registered buyer"))
                proposals.append({"plot_id": pid, "field": "owner", "new_value": last["buyer"], "source_ref": last["id"],
                                  "reason": f"Registered sale {last['id']} dated {last.get('date')}: "
                                            f"{last.get('seller')} → {last['buyer']}"})
            # a registered deed settles an owner dispute between RoR and the municipal register
            own = by_id_conf.get(("OWNER_MISMATCH", pid))
            if own and own["status"] == "open" and last.get("buyer"):
                sims = [owner_similarity(v["label"], last["buyer"]) for v in own["versions"]]
                k = int(np.argmax(sims))
                if sims[k] >= config.OWNER_MATCH_THRESHOLD:
                    auto(own, f"Registered deed {last['id']} ({last.get('date')}) names '{last['buyer']}': "
                              f"{own['versions'][k]['source']} is correct, the other record is stale.", k)
        # other departments' account holders as corroborating evidence in an open owner dispute
        own = by_id_conf.get(("OWNER_MISMATCH", pid))
        if own and own["status"] == "open":
            notes = []
            for dept, recs in (("electricity", elec), ("water", water), ("gas", gas)):
                for r in recs[:1]:
                    sims = [owner_similarity(v["label"], r.get("name") or "") for v in own["versions"]]
                    if max(sims) >= config.OWNER_MATCH_THRESHOLD:
                        notes.append(f"{DEPARTMENTS[dept]['short']} account {r['id']} is in the name of "
                                     f"'{r['name']}' (matches {own['versions'][int(np.argmax(sims))]['source']})")
            if notes:
                own["detail"] += " Other departments: " + "; ".join(notes) + "."
        # 2. commercial use seen by utilities, residential assessment for tax
        if muni and muni.get("land_use") == "residential":
            com = [r for r in elec if str(r.get("category") or "").startswith(COMMERCIAL_TARIFF)] + \
                  [r for r in water if str(r.get("category") or "").lower().startswith("non")]
            if com:
                out.append(make("TAX_USE_MISMATCH", pid, "medium", pid,
                                "Assessed as residential for property tax, but " + "; ".join(
                                    f"{DEPARTMENTS[r['dept']]['short']} {r['id']} is billed as {r['category']}" for r in com) + ".",
                                [{"label": "Residential", "source": "Municipal assessment", "ref": muni.get("record_id")}] +
                                [{"label": r["category"], "source": DEPARTMENTS[r["dept"]]["name"], "ref": r["id"]} for r in com],
                                "Re-assess the property tax as commercial, or correct the utility category"))
        # 3. live services on a property with no tax assessment
        svc = elec + water + gas
        if svc and (not muni or muni.get("land_use") == "vacant"):
            ev = "; ".join(f"{DEPARTMENTS[r['dept']]['short']} {r['id']}" for r in svc)
            existing = by_id_conf.get(("MISSING_IN_MUNICIPAL", pid)) or by_id_conf.get(("UNASSESSED_CONSTRUCTION", pid))
            if existing:
                existing["detail"] += f" Live service connections confirm occupation: {ev}."
                existing["severity"] = "high"
                existing["service_evidence"] = True
            else:
                out.append(make("SERVICE_WITHOUT_TAX", pid, "medium", pid,
                                f"Live service connections ({ev}) on a property "
                                + ("assessed as vacant." if muni else "with no property-tax record."),
                                suggestion="Assess the property for tax"))
                out[-1]["service_evidence"] = True
        # 4. more floors than the building permission allows
        perms = [r for r in d.get("town_planning", []) if r.get("floors") is not None]
        floors = [b.get("floors") for b in ctx.get("buildings", []) if b.get("plot_id") == pid and b.get("floors")]
        if perms and floors:
            allowed = max(r["floors"] for r in perms)
            seen = max(floors)
            if seen > allowed:
                out.append(make("FLOORS_BEYOND_PERMISSION", pid, "high", pid,
                                f"Permission {perms[-1]['id']} allows {allowed} floor(s); the elevation model (DSM − DTM) shows {seen}.",
                                [{"label": f"{seen} floors observed", "source": "Elevation model (DSM − DTM)"},
                                 {"label": f"{allowed} floors permitted", "source": "Town Planning", "ref": perms[-1]["id"]}],
                                "Inspect for unauthorised construction; regularise or issue notice"))
                out[-1]["observed_floors"], out[-1]["permitted_floors"] = seen, allowed
    ctx["change_proposals"] = proposals
    return out


def route(conflicts):
    for c in conflicts:
        depts = list(ROUTING.get(c["type"], []))
        if c["type"] == "UTILITY_UNDER_BUILDING":
            depts = [c.get("utility_dept", "water"), "municipal"]
        c["departments"] = depts


def parcel_summary(p):
    """Compact per-parcel view of every department's linked records (for the inspector / API)."""
    out = {}
    for dept, recs in p.get("dept", {}).items():
        out[dept] = [{k: v for k, v in (("id", r.get("id")), ("name", r.get("name")), ("category", r.get("category")),
                                        ("status", r.get("status")), ("load", r.get("load")), ("floors", r.get("floors")),
                                        ("date", r.get("date")), ("buyer", r.get("buyer")), ("seller", r.get("seller")),
                                        ("bua", r.get("bua")), ("link", r.get("link_method")), ("source", r.get("source")),
                                        ("fields", r.get("fields"))) if v not in (None, "", {})} for r in recs]
    return out


def registers(p):
    """The three registers every plot is built from, with all their source columns:
    Revenue (record of rights), Municipal (property tax) and Survey (cadastral map, GNSS, field visit)."""
    ror, muni, gt, cad = p.get("ror") or {}, p.get("municipal") or {}, p.get("gt") or {}, p.get("props") or {}
    out = {}
    if ror:
        f = clean_fields(ror.get("props"))
        out["revenue"] = {k: v for k, v in {
            "khata": ror.get("record_id"), "owner": ror.get("owner_name"), "survey_no": ror.get("plot_id"),
            "extent_sqm": round(ror["area_sqm"], 1) if ror.get("area_sqm") else None, "land_type": ror.get("land_use"),
            "source": ror.get("source"), "fields": f}.items() if v not in (None, "", {})}
    if muni:
        f = clean_fields(muni.get("props"))
        out["municipal"] = {k: v for k, v in {
            "pid": muni.get("record_id"), "owner": muni.get("owner_name"), "usage": muni.get("land_use"),
            "assessed_area_sqm": round(muni["area_sqm"], 1) if muni.get("area_sqm") else None,
            "ward": find_field(f, "ward", "ward no", "ward name", "zone"), "source": muni.get("source"),
            "fields": f}.items() if v not in (None, "", {})}
    f = clean_fields(cad)
    sv = {"survey_no": p["plot_id"], "sheet": p.get("sheet"),
          "village": find_field(f, "village", "revenue village", "village name"),
          "taluk": find_field(f, "taluk", "taluka", "tehsil", "mandal"), "district": find_field(f, "district"),
          "surveyed_area_sqm": round(p["geom"].area, 1),
          "recorded_extent_sqm": round(p["extent_sqm"], 1) if p.get("extent_sqm") else None,
          "gnss_corners": p.get("gnss_vertices") or 0, "source": p.get("source"), "fields": f}
    if gt:
        sv["field_visit"] = {k: v for k, v in {"visit": gt.get("record_id"), "owner": gt.get("owner_name"),
                                              "land_use": gt.get("land_use"), "fields": clean_fields(gt.get("props"))}.items()
                             if v not in (None, "", {})}
    out["survey"] = {k: v for k, v in sv.items() if v not in (None, "", {})}
    return out


def registry():
    return {"departments": {k: dict(v) for k, v in DEPARTMENTS.items()},
            "fields": {k: {kk: vv for kk, vv in v.items()} for k, v in FIELDS.items()},
            "titles": TITLES, "routing": ROUTING, "access_levels": ACCESS_LEVELS}


# ----------------------------------------------------------------------------- department roles and access
# Job titles for each department's accounts. The permission level is the account's role (viewer = read-only
# staff, officer = maker, reviewer = checker / approving authority); the title is what the department calls it.
TITLES = {
    "revenue": {"viewer": "Revenue clerk", "officer": "Village Administrative Officer / Revenue Inspector", "reviewer": "Tahsildar"},
    "survey": {"viewer": "Survey draughtsman", "officer": "Surveyor", "reviewer": "Assistant Director of Land Records"},
    "municipal": {"viewer": "Tax section clerk", "officer": "Tax Inspector", "reviewer": "Assistant Revenue Officer"},
    "town_planning": {"viewer": "Planning assistant", "officer": "Junior Town Planner", "reviewer": "Assistant Director of Town Planning"},
    "registration": {"viewer": "Registration clerk", "officer": "Registration assistant", "reviewer": "Sub-Registrar"},
    "water": {"viewer": "Billing clerk", "officer": "Assistant Engineer", "reviewer": "Executive Engineer"},
    "electricity": {"viewer": "Billing clerk", "officer": "Section Officer (AE)", "reviewer": "Assistant Executive Engineer"},
    "gas": {"viewer": "Customer care executive", "officer": "Field engineer", "reviewer": "Area manager"},
    "roads": {"viewer": "Works clerk", "officer": "Assistant Engineer (Roads)", "reviewer": "Executive Engineer (Roads)"},
    "all": {"viewer": "State observer", "officer": "State officer", "reviewer": "State approving authority", "admin": "Administrator"},
}


def title(role, department):
    return TITLES.get(department or "all", TITLES["all"]).get(role) or TITLES["all"].get(role) or role


# How much of another department's records an account may see on a plot:
#   full     every field of every record, with names, amounts and meter numbers
#   summary  that a record exists, its number, category, status and how it was linked; no names or amounts
#   none     the department's records are not shown at all
# The plot's common information (boundary, area, land use, owner, IDs, confidence, conflicts) is shared with
# everyone, as before. A department always has full access to its own records; state-level accounts and
# admins see everything. Defaults follow need-to-know; an admin can change any cell (stored in the database).
ACCESS_LEVELS = ("full", "summary", "none")
DEFAULT_ACCESS = {
    "revenue": {"survey": "full", "registration": "full", "municipal": "summary"},
    "survey": {"revenue": "full", "town_planning": "summary", "roads": "summary"},
    "municipal": {"revenue": "full", "town_planning": "full", "registration": "full", "survey": "summary"},
    "town_planning": {"survey": "full", "municipal": "full", "revenue": "summary"},
    "registration": {"revenue": "full", "survey": "summary", "municipal": "summary"},
    "water": {"registration": "none"},
    "electricity": {"registration": "none"},
    "gas": {"registration": "none"},
    "roads": {"survey": "full", "registration": "none", "town_planning": "summary"},
}
DEFAULT_OTHER = "summary"
# Keys kept at 'summary' level (everything else, including the raw source fields, is dropped)
SUMMARY_KEYS = {"id", "category", "status", "link", "floors", "date", "load"}
SUMMARY_REGISTER_KEYS = {
    "revenue": {"khata", "survey_no", "land_type"},
    "survey": {"survey_no", "sheet", "village", "surveyed_area_sqm", "gnss_corners"},
    "municipal": {"pid", "usage", "ward"},
}


def access_matrix():
    """{viewer_dept: {target_dept: level}} with admin overrides applied."""
    from . import db
    m = {v: {t: ("full" if v == t else DEFAULT_ACCESS.get(v, {}).get(t, DEFAULT_OTHER)) for t in DEPARTMENTS}
         for v in DEPARTMENTS}
    for r in db.query("SELECT viewer, target, level FROM dept_access"):
        if r["viewer"] in m and r["target"] in m and r["viewer"] != r["target"] and r["level"] in ACCESS_LEVELS:
            m[r["viewer"]][r["target"]] = r["level"]
    return m


def set_access(viewer, target, level, by):
    from . import db
    if viewer not in DEPARTMENTS or target not in DEPARTMENTS:
        raise ValueError("Unknown department")
    if viewer == target:
        raise ValueError("A department always has full access to its own records")
    if level not in ACCESS_LEVELS:
        raise ValueError(f"Level must be one of {', '.join(ACCESS_LEVELS)}")
    db.execute("INSERT INTO dept_access (viewer, target, level, updated_by, updated_at) VALUES (?, ?, ?, ?, ?) "
               "ON CONFLICT(viewer, target) DO UPDATE SET level = excluded.level, updated_by = excluded.updated_by, "
               "updated_at = excluded.updated_at", (viewer, target, level, by, db.now()))


def access_for(user):
    """{target_dept: level} for one account, or None when it sees everything (admin / state level)."""
    if not user or user.get("role") == "admin" or user.get("department", "all") == "all":
        return None
    return access_matrix().get(user["department"])
