"""Secure updates to land records: maker-checker change requests across departments.

1. An officer (maker) proposes a change to a field their department is the legal authority for
   (see departments.FIELDS). Changes to ownership, extent, boundary, permitted floors, deeds and
   notices need a supporting document (PDF / image); its SHA-256 is recorded.
2. A reviewer of the same department (checker) - never the proposer - approves or rejects it.
   Approval re-checks that the value has not changed since it was proposed (no lost updates).
3. An approved change is applied to the harmonised record, closes the conflicts it settles, is
   re-applied on every future pipeline run, and notifies every department that must follow it
   (e.g. an owner change notifies municipal tax, water, electricity and gas).
4. Nothing is deleted: rejected, withdrawn and stale requests stay on file. Every step is written to
   the hash-chained audit trail with the user's name.
The pipeline itself can draft requests (e.g. a registered sale that the RoR has not caught up with);
those still need a human approver.
"""
import hashlib
import json
import re
from pathlib import Path

import shapely
from pyproj import Geod
from shapely.geometry import shape

from . import audit, config, confidence, db, departments

EVIDENCE_EXT = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
MAX_EVIDENCE_MB = 10
GEOD = Geod(ellps="WGS84")
DEPT_FIELD = {"approved_floors": ("town_planning", "floors"), "registered_owner": ("registration", "buyer"),
              "tariff": ("electricity", "category"), "electricity_consumer": ("electricity", "name"),
              "water_type": ("water", "category"), "water_consumer": ("water", "name"), "gas_consumer": ("gas", "name")}


class RequestError(ValueError):
    pass


def code(i):
    return f"CR-{int(i):05d}"


def _row(r):
    if r is None:
        return None
    r = dict(r)
    r["code"] = code(r["id"])
    for k in ("old_value", "new_value"):
        r[k] = json.loads(r[k]) if r[k] not in (None, "") else None
    r["field_label"] = departments.FIELDS.get(r["field"], {}).get("label", r["field"])
    r["old_display"], r["new_display"] = describe(r["field"], r["old_value"], True), describe(r["field"], r["new_value"])
    return r


def describe(field, v, old=False):
    """Short human text for a value (boundaries and footprints are not printed as coordinates)."""
    if field == "parcel_add":
        return "—" if old or not isinstance(v, dict) else f"new plot, {_area(shape(v)):,.0f} m²"
    if field == "geometry":
        return "(boundary)" if old else "(new boundary)"
    if field == "building_add":
        if not isinstance(v, dict):
            return "—"
        fl = f", {v['floors']} floor(s)" if v.get("floors") is not None else ""
        return f"new building, {v.get('area_sqm', 0):,.0f} m²{fl}"
    if field == "building_remove":
        return "—" if old or not isinstance(v, dict) else f"remove {v.get('remove')}"
    if field == "building_edit":
        if old or not isinstance(v, dict):
            return "—"
        fl = f", {v['floors']} floor(s)" if v.get("floors") is not None else ""
        return f"{v.get('target')} corrected to {v.get('area_sqm', 0):,.0f} m²{fl}"
    return v


def _plot_buildings(result, plot_id):
    return [b for b in result["layers"]["buildings"]["features"] if b["properties"].get("plot_id") == plot_id]


def _area(g):
    return abs(GEOD.geometry_area_perimeter(g)[0])


def get(cr_id):
    return _row(db.query("SELECT * FROM change_requests WHERE id = ?", (cr_id,), one=True))


def list_requests(ws, status=None, department=None, plot_id=None, limit=500):
    sql, p = "SELECT * FROM change_requests WHERE ws = ?", [ws]
    for col, val in (("status", status), ("department", department), ("plot_id", plot_id)):
        if val:
            sql += f" AND {col} = ?"
            p.append(val)
    return [_row(r) for r in db.query(sql + " ORDER BY id DESC LIMIT ?", p + [limit])]


# ----------------------------------------------------------------------------- evidence
def save_evidence(ws, data, filename):
    ext = Path(filename or "").suffix.lower()
    if ext not in EVIDENCE_EXT:
        raise RequestError("Evidence must be a PDF, JPG or PNG")
    if len(data) > MAX_EVIDENCE_MB << 20:
        raise RequestError(f"Evidence is larger than {MAX_EVIDENCE_MB} MB")
    if ext == ".pdf" and not data.startswith(b"%PDF") or ext == ".png" and not data.startswith(b"\x89PNG") \
            or ext in (".jpg", ".jpeg") and not data.startswith(b"\xff\xd8"):
        raise RequestError("File content does not match its type")
    sha = hashlib.sha256(data).hexdigest()
    folder = config.ws_paths(ws)["out"] / "evidence"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha}{ext}"
    if not path.exists():
        path.write_bytes(data)
    return {"sha": sha, "name": Path(filename).name[:120]}


def evidence_path(ws, sha):
    folder = config.ws_paths(ws)["out"] / "evidence"
    for ext in EVIDENCE_EXT:
        p = folder / f"{sha}{ext}"
        if p.exists():
            return p, EVIDENCE_EXT[ext]
    return None, None


# ----------------------------------------------------------------------------- values
def _feature(result, plot_id):
    return next((f for f in result["layers"]["parcels"]["features"] if f["properties"]["plot_id"] == plot_id), None)


def _dept_rec(props, field, record_id=None, create=False):
    dept, key = DEPT_FIELD[field]
    recs = props.setdefault("dept_records", {}).setdefault(dept, [])
    if field == "registered_owner":
        recs.sort(key=lambda r: str(r.get("date") or ""))
        target = recs[-1] if recs else None
    else:
        target = next((r for r in recs if record_id and str(r.get("id")) == str(record_id)), recs[0] if recs else None)
    if target is None and create:
        target = {"id": None}
        recs.append(target)
    return target, key


PLOT_NO = re.compile(r"^[0-9A-Za-z]{1,8}(/[0-9A-Za-z]{1,6}){0,3}$")


def current_value(result, plot_id, field, record_id=None):
    if field == "parcel_add":                            # a new plot: its number must be free
        if _feature(result, plot_id) is not None:
            raise RequestError(f"Plot {plot_id} already exists on the map")
        return None
    f = _feature(result, plot_id)
    if f is None:
        raise RequestError(f"Unknown plot {plot_id}")
    p = f["properties"]
    if field == "owner":
        return p.get("ror_owner")
    if field == "ror_extent_sqm":
        return p.get("ror_area_sqm")
    if field in ("land_use", "municipal_use", "municipal_pid", "road_notice"):
        return p.get(field)
    if field == "geometry":
        return f["geometry"]
    if field in ("building_add", "building_edit", "building_remove"):
        return None                                      # additions and removals are checked spatially instead
    rec, key = _dept_rec(p, field, record_id)
    return rec.get(key) if rec else None


def validate(field, value):
    spec = departments.FIELDS.get(field)
    if spec is None:
        raise RequestError(f"Unknown field {field}")
    t = spec["type"]
    if t in ("text",):
        value = " ".join(str(value or "").split())
        if not 1 <= len(value) <= 120:
            raise RequestError("Value must be 1-120 characters")
    elif t == "number":
        value = float(value)
        if not 0 < value < 1e7:
            raise RequestError("Value out of range")
        value = round(value, 1)
    elif t == "int":
        value = int(value)
        if not 0 <= value <= 60:
            raise RequestError("Value out of range")
    elif t == "choice":
        if value not in spec["choices"]:
            raise RequestError(f"Choose one of: {', '.join(spec['choices'])}")
    elif t == "geometry":
        g = shape(json.loads(value) if isinstance(value, str) else value)
        if g.geom_type != "Polygon" or not g.is_valid:
            raise RequestError("Boundary must be a valid GeoJSON Polygon")
        minx, miny, maxx, maxy = g.bounds
        if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
            raise RequestError("Boundary must be in WGS 84 longitude / latitude")
        value = shapely.geometry.mapping(g)
        value = json.loads(json.dumps(value))            # tuples -> lists
    elif t == "parcel":
        g = shape(json.loads(value) if isinstance(value, str) else value)
        if g.geom_type != "Polygon" or not g.is_valid:
            raise RequestError("Draw the plot as one closed shape without crossing lines")
        minx, miny, maxx, maxy = g.bounds
        if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
            raise RequestError("Boundary must be in WGS 84 longitude / latitude")
        area = _area(g)
        if not 20 <= area <= 200000:
            raise RequestError(f"A {area:,.0f} m² plot is not plausible (20 - 200,000 m²)")
        value = json.loads(json.dumps(shapely.geometry.mapping(g)))
    elif t in ("building", "building_edit"):
        v = json.loads(value) if isinstance(value, str) else dict(value)
        g = shape(v.get("footprint") or v.get("geometry") or {})
        if g.geom_type != "Polygon" or not g.is_valid:
            raise RequestError("Draw the building as one closed shape without crossing lines")
        minx, miny, maxx, maxy = g.bounds
        if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
            raise RequestError("Footprint must be in WGS 84 longitude / latitude")
        area = _area(g)
        if not 6 <= area <= 20000:
            raise RequestError(f"A {area:,.0f} m² footprint is not plausible for one building (6 - 20,000 m²)")
        floors = v.get("floors")
        if floors not in (None, ""):
            floors = int(floors)
            if not 0 <= floors <= 60:
                raise RequestError("Floors must be between 0 and 60")
        else:
            floors = None
        value = {"footprint": json.loads(json.dumps(shapely.geometry.mapping(g))), "floors": floors,
                 "area_sqm": round(area, 1)}
        if t == "building_edit":
            value["target"] = str(v.get("target") or "").strip()
            if not value["target"]:
                raise RequestError("Choose the building to correct")
    elif t == "building_ref":
        value = str(value).strip()
        if not value:
            raise RequestError("Choose the building to remove")
    return value


def _check_on_map(result, plot_id, field, value):
    """Spatial sanity checks for anything drawn on the map; returns the value to store."""
    f = _feature(result, plot_id)
    if field == "parcel_add":
        g = shape(value)
        for n in result["layers"]["parcels"]["features"]:
            ng = shape(n["geometry"])
            if ng.intersects(g):
                a = _area(ng.intersection(g))
                if a > max(2.0, 0.02 * _area(g)):
                    raise RequestError(f"The new plot overlaps plot {n['properties']['plot_id']} by {a:,.0f} m². "
                                       "Draw it on free land, or edit that plot's boundary instead.")
        return value
    plot = shape(f["geometry"])
    if field == "geometry":
        g = shape(value)
        for n in result["layers"]["parcels"]["features"]:
            if n is f or n["properties"]["plot_id"] == plot_id:
                continue
            ov = g.intersection(shape(n["geometry"]))
            if not ov.is_empty and ov.area > 0:
                a = _area(ov)
                if a > max(2.0, 0.02 * _area(g)):
                    raise RequestError(f"The new boundary overlaps plot {n['properties']['plot_id']} by {a:,.0f} m². "
                                       "Adjust it, or change both plots.")
    elif field in ("building_add", "building_edit"):
        g = shape(value["footprint"])
        if field == "building_edit":
            old = next((b for b in _plot_buildings(result, plot_id) if b["properties"]["id"] == value["target"]), None)
            if old is None:
                raise RequestError(f"No building {value['target']} on plot {plot_id}")
            value["old"] = old["geometry"]
            if value["floors"] is None:
                value["floors"] = old["properties"].get("floors")
            if shape(old["geometry"]).equals(g) and value["floors"] == old["properties"].get("floors"):
                raise RequestError("Nothing changed: move a corner or change the floors")
        inside = _area(g.intersection(plot)) / max(_area(g), 1e-9) if g.intersects(plot) else 0
        if inside < 0.5:
            raise RequestError(f"Only {inside:.0%} of the drawn building lies inside plot {plot_id}; draw it on this plot")
        for b in _plot_buildings(result, plot_id):
            if b["properties"]["id"] == value.get("target"):
                continue
            bg = shape(b["geometry"])
            if bg.intersects(g) and _area(bg.intersection(g)) > 0.5 * min(_area(bg), _area(g)):
                raise RequestError(f"This building is already mapped as {b['properties']['id']}")
    elif field == "building_remove":
        b = next((b for b in _plot_buildings(result, plot_id) if b["properties"]["id"] == value), None)
        if b is None:
            raise RequestError(f"No building {value} on plot {plot_id}")
        value = {"remove": value, "footprint": b["geometry"], "area_sqm": b["properties"].get("area_sqm")}
    return value


def _find_building(result, plot_id, footprint):
    """Building ids are renumbered when the pipeline re-runs, so a removal is matched by shape (IoU >= 0.5)."""
    g = shape(footprint)
    for b in _plot_buildings(result, plot_id):
        bg = shape(b["geometry"])
        if bg.intersects(g):
            inter, union = bg.intersection(g).area, bg.union(g).area
            if union and inter / union >= 0.5:
                return b
    return None


# ----------------------------------------------------------------------------- workflow
def _log(ws, kind, cr, actor, extra=None):
    audit.append_one(config.ws_paths(ws)["audit"], kind, cr["plot_id"], {
        "request": cr["code"], "department": cr["department"], "field": cr["field"], "record_id": cr.get("record_id"),
        "old": describe(cr["field"], cr["old_value"], True), "new": describe(cr["field"], cr["new_value"]),
        "evidence_sha256": cr.get("evidence_sha"), "source_ref": cr.get("source_ref"), **(extra or {})}, actor=actor)


def create(ws, result, user, plot_id, field, new_value, reason, evidence=None, record_id=None, source_ref=None):
    spec = departments.FIELDS.get(field)
    if spec is None:
        raise RequestError(f"Unknown field {field}")
    dept = spec["dept"]
    if field == "parcel_add":
        plot_id = " ".join(str(plot_id).split())
        if not PLOT_NO.match(plot_id):
            raise RequestError("Give the new plot a survey number such as 201/1 or 201/1A")
    if user["username"] != "system":
        from .auth import ROLES, can_act_for
        if ROLES[user["role"]] < ROLES["officer"]:
            raise PermissionError("Needs the officer role")
        if not can_act_for(user, dept):
            raise PermissionError(f"Only {departments.DEPARTMENTS[dept]['short']} officers can change '{spec['label']}'")
    reason = " ".join(str(reason or "").split())
    if len(reason) < 10:
        raise RequestError("Give a reason of at least 10 characters")
    if spec["evidence"] and not evidence and not source_ref:
        raise RequestError(f"'{spec['label']}' changes need a supporting document")
    new_value = validate(field, new_value)
    old = current_value(result, plot_id, field, record_id)
    if spec["type"] in ("geometry", "parcel", "building", "building_edit", "building_ref"):
        new_value = _check_on_map(result, plot_id, field, new_value)
    if old == new_value:
        raise RequestError("The new value is the same as the current value")
    if field != "building_add":                          # several new buildings on one plot may await approval
        pending = db.query("SELECT id, new_value FROM change_requests WHERE ws = ? AND plot_id = ? AND field = ? "
                           "AND status = 'pending'", (ws, plot_id, field))
        key = {"building_remove": "remove", "building_edit": "target"}.get(field)
        dup = next((d for d in pending if key is None or json.loads(d["new_value"]).get(key) == new_value[key]), None)
        if dup:
            raise RequestError(f"{code(dup['id'])} for this field is already awaiting approval")
    with db._lock:
        con = db.connect()
        try:
            with con:
                cur = con.execute(
                    "INSERT INTO change_requests (ws, plot_id, department, field, record_id, old_value, new_value, reason, "
                    "evidence_sha, evidence_name, source_ref, status, proposed_by, proposed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (ws, plot_id, dept, field, record_id, json.dumps(old, ensure_ascii=False),
                     json.dumps(new_value, ensure_ascii=False), reason, (evidence or {}).get("sha"),
                     (evidence or {}).get("name"), source_ref, user["username"], db.now()))
                cr_id = cur.lastrowid
        finally:
            con.close()
    cr = get(cr_id)
    _log(ws, "change.proposed", cr, user["username"], {"reason": reason})
    return cr


def decide(ws, result, cr_id, user, approve, note=""):
    from .auth import ROLES, can_act_for
    cr = get(cr_id)
    if cr is None or cr["ws"] != ws:
        raise KeyError(cr_id)
    if cr["status"] != "pending":
        raise RequestError(f"{cr['code']} is already {cr['status']}")
    if ROLES[user["role"]] < ROLES["reviewer"] or not can_act_for(user, cr["department"]):
        raise PermissionError(f"Only a {departments.DEPARTMENTS[cr['department']]['short']} approver can decide this")
    if user["username"] == cr["proposed_by"]:
        raise PermissionError("Four-eyes rule: you cannot approve your own request")
    note = " ".join(str(note or "").split())[:500]
    if not approve and len(note) < 5:
        raise RequestError("Say why the request is rejected")
    props = None
    if approve:
        now_val = current_value(result, cr["plot_id"], cr["field"], cr.get("record_id"))
        gone = ((cr["field"] == "building_remove" and _find_building(result, cr["plot_id"], cr["new_value"]["footprint"]) is None)
                or (cr["field"] == "building_edit" and _find_building(result, cr["plot_id"], cr["new_value"]["old"]) is None))
        if now_val != cr["old_value"] or gone:
            db.execute("UPDATE change_requests SET status = 'stale', decided_by = ?, decided_at = ?, decision_note = ? "
                       "WHERE id = ?", (user["username"], db.now(), "Record changed after the request was made", cr_id))
            cr = get(cr_id)
            _log(ws, "change.stale", cr, user["username"])
            raise RequestError(f"{cr['code']} is out of date: the record changed after it was proposed. Ask for a new request.")
    status = "approved" if approve else "rejected"
    db.execute("UPDATE change_requests SET status = ?, decided_by = ?, decided_at = ?, decision_note = ? WHERE id = ?",
               (status, user["username"], db.now(), note, cr_id))
    cr = get(cr_id)
    if approve:
        props = apply(ws, result, cr)
        spec = departments.FIELDS[cr["field"]]
        for dept in spec["notify"]:
            db.execute("INSERT INTO notifications (ws, department, plot_id, kind, message, ref, created_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (ws, dept, cr["plot_id"], "record_changed",
                        f"{departments.DEPARTMENTS[cr['department']]['short']} changed {spec['label'].lower()} of plot "
                        f"{cr['plot_id']} to '{_short(cr['new_value'], cr['field'])}' ({cr['code']}). Update your records.",
                        cr["code"], db.now()))
    _log(ws, f"change.{status}", cr, user["username"], {"note": note, "proposed_by": cr["proposed_by"]})
    return cr, props


def withdraw(ws, cr_id, user):
    cr = get(cr_id)
    if cr is None or cr["ws"] != ws:
        raise KeyError(cr_id)
    if cr["status"] != "pending":
        raise RequestError(f"{cr['code']} is already {cr['status']}")
    if user["username"] != cr["proposed_by"] and user["role"] != "admin":
        raise PermissionError("Only the proposer can withdraw a request")
    db.execute("UPDATE change_requests SET status = 'withdrawn', decided_by = ?, decided_at = ? WHERE id = ?",
               (user["username"], db.now(), cr_id))
    cr = get(cr_id)
    _log(ws, "change.withdrawn", cr, user["username"])
    return cr


def _short(v, field=None):
    if field in ("building_add", "building_edit", "building_remove", "parcel_add"):
        return describe(field, v)
    return "(boundary)" if isinstance(v, dict) else str(v)


# ----------------------------------------------------------------------------- applying changes
def _new_parcel(result, cr):
    """An approved new plot: added with no records yet, so it starts in review with a 'no revenue record' conflict
    for Revenue. Re-applied after every pipeline run, like every approved change."""
    from . import conflicts as cf, insights
    pid, g = cr["plot_id"], shape(cr["new_value"])
    f = _feature(result, pid)
    if f is not None:
        return f["properties"]
    c = g.representative_point()
    state = (result.get("workspace_meta") or {}).get("state_code", "KA")
    comp = {"G": 0.9, "A": 0.2, "S": 0.8, "M": 0.5, "T": 1.0}    # surveyed with a sketch; no records linked yet
    p = {"plot_id": pid, "status": "review", "score": 0.0, "score_base": confidence.base_score(comp), "components": comp,
         "conflicts": [], "open_conflicts": 0, "resolved_owner": None, "owner_source": "No revenue record yet",
         "ror_owner": None, "municipal_owner": None, "gt_owner": None, "khata": None, "municipal_pid": None,
         "area_sqm": round(_area(g), 1), "ror_area_sqm": None, "land_use": None, "municipal_use": None,
         "gnss_vertices": 0, "match_prob": 0.0, "topo_flags": [], "buildings": [], "sheet": None, "dept_records": {},
         "sources": [f"New plot surveyed by {cr['proposed_by']}, approved by {cr['decided_by']} ({cr['code']})"],
         "updates": [], "new_plot": True, "ulpin": insights.ulpin_lonlat(c.x, c.y, state),
         "ownership_chain": {"steps": [], "verdict": "No registered deed on record", "ok": True}}
    cid = f"C-NO_REVENUE_RECORD-{pid}"
    conflict = next((x for x in result["conflicts"] if x["id"] == cid), None)
    if conflict is None:
        conflict = cf._c("NO_REVENUE_RECORD", pid, "high", pid,
                         f"New plot {pid} was added by survey ({cr['code']}); no RoR entry carries this number yet.",
                         suggestion="Create the revenue record (RoR) for the new plot")
        conflict["departments"] = departments.ROUTING.get("NO_REVENUE_RECORD", ["revenue"])
        conflict["center"] = [round(c.y, 7), round(c.x, 7)]
        result["conflicts"].append(conflict)
    p["conflicts"] = [cid]
    confidence.finalize(p, {cid: conflict})
    result["layers"]["parcels"]["features"].append({"type": "Feature", "geometry": cr["new_value"], "properties": p})
    from . import publish
    result["summary"] = publish.summarize(result)
    return p


def apply(ws, result, cr):
    """Write an approved change into the published result, close the conflicts it settles, rescore."""
    if cr["field"] == "parcel_add":
        return _new_parcel(result, cr)
    f = _feature(result, cr["plot_id"])
    if f is None:
        return None
    p, field, v = f["properties"], cr["field"], cr["new_value"]
    label = departments.FIELDS[field]["label"]
    dept_short = departments.DEPARTMENTS[cr["department"]]["short"]
    if field == "owner":
        p["ror_owner"] = p["resolved_owner"] = v
        p["owner_source"] = f"Revenue record, updated by {cr['code']}"
    elif field == "ror_extent_sqm":
        p["ror_area_sqm"] = v
    elif field in ("land_use", "municipal_use", "municipal_pid", "road_notice"):
        p[field] = v
    elif field == "geometry":
        f["geometry"] = v
        p["area_sqm"] = round(abs(GEOD.geometry_area_perimeter(shape(v))[0]), 1)
    elif field == "building_add":
        bid = f"BLD-{cr['code']}"
        layer = result["layers"]["buildings"]["features"]
        if not any(b["properties"]["id"] == bid for b in layer):
            layer.append({"type": "Feature", "geometry": v["footprint"], "properties": {
                "id": bid, "plot_id": cr["plot_id"], "height_m": None, "floors": v.get("floors"),
                "area_sqm": v.get("area_sqm"), "confidence": None, "footprint_iou": None,
                "sources": [f"mapped by {cr['proposed_by']}, approved by {cr['decided_by']} ({cr['code']})"]}})
        p["buildings"] = [b["properties"]["id"] for b in _plot_buildings(result, cr["plot_id"])]
    elif field == "building_edit":
        b = _find_building(result, cr["plot_id"], v["old"])
        if b is not None:
            b["geometry"] = v["footprint"]
            bp = b["properties"]
            bp.update(area_sqm=v.get("area_sqm"), floors=v.get("floors"))
            note = f"outline corrected by {cr['proposed_by']}, approved by {cr['decided_by']} ({cr['code']})"
            if note not in bp["sources"]:
                bp["sources"] = bp["sources"] + [note]
        p["buildings"] = [b["properties"]["id"] for b in _plot_buildings(result, cr["plot_id"])]
    elif field == "building_remove":
        b = _find_building(result, cr["plot_id"], v["footprint"])
        if b is not None:
            result["layers"]["buildings"]["features"].remove(b)
        p["buildings"] = [b["properties"]["id"] for b in _plot_buildings(result, cr["plot_id"])]
    else:
        rec, key = _dept_rec(p, field, cr.get("record_id"), create=True)
        rec[key] = v
        if rec.get("id") is None:
            rec["id"] = cr.get("source_ref") or cr["code"]
    upd = {"request": cr["code"], "field": field, "label": label, "value": _short(v, field), "department": cr["department"],
           "by": cr["decided_by"], "at": cr["decided_at"], "proposed_by": cr["proposed_by"]}
    p.setdefault("updates", [])
    if not any(u["request"] == cr["code"] for u in p["updates"]):
        p["updates"].append(upd)
    closes = set(departments.FIELDS[field]["closes"])
    for c in result["conflicts"]:
        if c["plot_id"] != cr["plot_id"] or c["type"] not in closes or c["status"] not in ("open", "auto_resolved"):
            continue
        if c["type"] == "TAX_USE_MISMATCH" and field in ("tariff", "water_type") and not str(v).lower().startswith(("lt-2", "domestic")):
            continue
        if c["type"] == "TAX_USE_MISMATCH" and field == "municipal_use" and v not in ("commercial", "institutional"):
            continue
        if c["type"] == "FLOORS_BEYOND_PERMISSION" and field == "approved_floors" and v < c.get("observed_floors", 0):
            continue
        c["status"], c["resolved_by"] = "resolved", f"{cr['decided_by']} ({cr['code']})"
        c["resolution"] = f"Closed by approved change {cr['code']} ({dept_short}): {label} → {_short(v)}"
    confidence.finalize(p, {c["id"]: c for c in result["conflicts"]})
    from . import publish
    result["summary"] = publish.summarize(result)
    for c in result["conflicts"]:
        if c["plot_id"] == cr["plot_id"]:
            db.update_indexed(ws, c, p)
    return p


def apply_approved(ws, result):
    n = 0
    for cr in sorted(list_requests(ws, status="approved", limit=100000), key=lambda r: (r["decided_at"] or "", r["id"])):
        if apply(ws, result, cr) is not None:
            n += 1
    return n


def propose_from_pipeline(ws, result, proposals):
    """Draft requests the pipeline found evidence for (e.g. registered sale not mutated). Never re-drafts a
    value that was already proposed, approved or rejected for that plot."""
    n = 0
    for pr in proposals:
        seen = db.query("SELECT 1 FROM change_requests WHERE ws = ? AND plot_id = ? AND field = ? AND new_value = ? "
                        "AND status IN ('pending', 'approved', 'rejected')",
                        (ws, pr["plot_id"], pr["field"], json.dumps(pr["new_value"], ensure_ascii=False)), one=True)
        if seen or _feature(result, pr["plot_id"]) is None:
            continue
        try:
            create(ws, result, {"username": "system", "role": "officer", "department": "all"}, pr["plot_id"], pr["field"],
                   pr["new_value"], pr["reason"], source_ref=pr.get("source_ref"))
            n += 1
        except RequestError:
            pass
    return n


# ----------------------------------------------------------------------------- inbox
def inbox(ws, department, result=None, include_acked=False):
    sql, p = "SELECT * FROM notifications WHERE ws = ?", [ws]
    if department != "all":
        sql += " AND department = ?"
        p.append(department)
    if not include_acked:
        sql += " AND acked_at IS NULL"
    notes = db.query(sql + " ORDER BY id DESC LIMIT 300", p)
    routed = []
    if result is not None:
        routed = [c for c in result["conflicts"] if c["status"] == "open"
                  and (department == "all" or department in c.get("departments", []))]
    pending = list_requests(ws, status="pending", department=None if department == "all" else department)
    return {"notifications": notes, "open_conflicts": len(routed),
            "open_by_type": _count(routed), "pending_requests": len(pending)}


def _count(conflicts):
    out = {}
    for c in conflicts:
        out[c["title"]] = out.get(c["title"], 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def ack(ws, note_id, user):
    from .auth import can_act_for
    n = db.query("SELECT * FROM notifications WHERE id = ? AND ws = ?", (note_id, ws), one=True)
    if n is None:
        raise KeyError(note_id)
    if not can_act_for(user, n["department"]):
        raise PermissionError("Not your department's notification")
    db.execute("UPDATE notifications SET acked_by = ?, acked_at = ? WHERE id = ?", (user["username"], db.now(), note_id))
    return True
