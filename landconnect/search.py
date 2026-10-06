"""The search bar: instant suggestions across everything in an area, plus place lookup.

suggest(ws, result, text, user) returns ranked, grouped suggestions as you type:
  Plots        plot / survey no. ("103/1", "103"), khata, municipal PID, ULPIN, owner name (any script;
               not for viewers, whose names are masked)
  Records      department record numbers: electricity RR no., water / gas connection, permission,
               deed number, and change requests (CR-00061)
  Buildings    building IDs (BLD-0138)
  Places       landmarks and roads from OpenStreetMap in this area
  Questions    ready-made questions that match the words typed, each with its live count
  Areas        other areas ("chennai") - switch to it
  Coordinates  "12.93, 77.58", "12°55'48\"N 77°35'E" - go there and show the plot underneath
Each suggestion carries an action the browser performs (open plot, fly to point, run question, ...).

geocode(text) looks a place name up in OpenStreetMap Nominatim (India only) when nothing in the area matches.
It is called only when the user presses Enter (never while typing, per Nominatim's usage policy), at most
once per second, with results cached; the place name typed is the only thing sent.
"""
import json
import re
import threading
import time
import urllib.parse
import urllib.request

from rapidfuzz import fuzz
from shapely.geometry import Point, shape

from . import config, geo, query
from .schema_mapper import normalize_plot_id, to_latin

_index = {}
_lock = threading.Lock()

TEMPLATES = [
    ("Plots with owner conflicts", "owner conflict dispute mismatch name"),
    ("Encroaching buildings", "encroachment encroach road boundary"),
    ("Plots needing review", "review pending low confidence"),
    ("Registered sale not mutated in the RoR", "mutation sale deed transfer ror rtc patta"),
    ("Floors beyond building permission", "floors permission illegal unauthorised extra building"),
    ("Buildings with no property tax record", "tax unassessed missing municipal property"),
    ("Live services but no property tax", "service electricity water gas tax connection"),
    ("Commercial use on a residential tax assessment", "commercial shop residential tax use"),
    ("Plots with tax at risk", "tax revenue risk money loss uncollected"),
    ("Total tax at risk", "total tax revenue risk how much"),
    ("New construction since the last survey", "new construction building change built survey"),
    ("Demolished buildings", "demolished demolition change"),
    ("Buildings with floors added", "floor added raised change storey"),
    ("Footprint extensions", "extension extended change"),
    ("Plots with a broken ownership chain", "ownership chain deed broken mutation"),
    ("Plots without a revenue record", "revenue ror rtc record missing orphan patta"),
    ("Utility lines under buildings", "utility pipe sewer water line"),
    ("Vacant plots larger than 5 guntas", "vacant empty land large gunta"),
    ("10 lowest confidence plots", "lowest confidence score weakest"),
    ("Buildings with more than 3 floors", "floors tall high storey building"),
    ("Plots without conflicts", "clean no conflict approved good"),
    ("GNSS-surveyed plots", "gnss dgps survey surveyed"),
    ("Field-verified plots", "field verified ground truth visit"),
    ("Plots added by survey", "new plot added survey"),
    ("Plots where two cadastral surveys disagree", "cadastral survey disagree ncscm tngis second source boundary"),
    ("How many plots need review", "how many count review"),
    ("Total area of vacant plots", "total area vacant land how much"),
]
DEPT_LABEL = {"electricity": "Electricity", "water": "Water", "gas": "Gas", "town_planning": "Permission",
              "registration": "Deed", "municipal": "Tax", "revenue": "RoR", "survey": "Survey", "roads": "Roads"}


def _key(ws, result):
    return (ws, result.get("generated_at"), json.dumps(result.get("summary"), sort_keys=True))


def build_index(ws, result):
    """Everything searchable in one area, built once per published result."""
    k = _key(ws, result)
    with _lock:
        hit = _index.get(ws)
        if hit and hit["key"] == k:
            return hit
    plots, records, people, places, buildings = [], [], [], [], []
    for f in result["layers"]["parcels"]["features"]:
        p = f["properties"]
        c = shape(f["geometry"]).representative_point()
        center = [round(c.y, 7), round(c.x, 7)]
        plots.append({"pid": p["plot_id"], "center": center, "khata": str(p.get("khata") or ""),
                      "pid_muni": str(p.get("municipal_pid") or ""), "ulpin": p.get("ulpin") or "",
                      "status": p.get("status"), "score": p.get("score"), "area": p.get("area_sqm")})
        for n in {p.get(x) for x in ("resolved_owner", "ror_owner", "municipal_owner", "gt_owner") if p.get(x)}:
            people.append({"name": n, "latin": to_latin(n).lower(), "pid": p["plot_id"], "center": center})
        for dept, recs in (p.get("dept_records") or {}).items():
            for r in recs:
                if r.get("id"):
                    records.append({"id": str(r["id"]), "dept": dept, "pid": p["plot_id"], "center": center,
                                    "name": r.get("name") or r.get("buyer")})
    for f in result["layers"]["buildings"]["features"]:
        g = shape(f["geometry"]).representative_point()
        buildings.append({"id": f["properties"]["id"], "pid": f["properties"].get("plot_id"), "center": [g.y, g.x],
                          "floors": f["properties"].get("floors")})
    for lyr in ("poi", "roads"):
        for f in result["layers"].get(lyr, {}).get("features", []):
            n = f["properties"].get("name")
            if n:
                g = shape(f["geometry"]).representative_point()
                places.append({"name": n, "kind": "road" if lyr == "roads" else (f["properties"].get("kind") or "landmark"),
                               "center": [g.y, g.x]})
    seen, uniq = set(), []
    for pl in places:                                   # a road comes in many pieces: keep one per name
        if pl["name"].lower() not in seen:
            seen.add(pl["name"].lower())
            uniq.append(pl)
    counts = {}
    for label, _ in TEMPLATES:
        f = query.parse_rules(label)
        try:
            _, total, _, s = query.run(result, f, max_items=0)
            counts[label] = s.get("answer") or f"{total}"
        except Exception:
            counts[label] = None
    idx = {"key": k, "plots": plots, "people": people, "records": records, "buildings": buildings, "places": uniq,
           "counts": counts}
    with _lock:
        _index[ws] = idx
    return idx


# ----------------------------------------------------------------------------- coordinates
_DMS = re.compile(r"(\d{1,3})\s*[°d]\s*(\d{1,2})?\s*['′m]?\s*(\d{1,2}(?:\.\d+)?)?\s*[\"″s]?\s*([NSEW])", re.I)


def parse_coords(text):
    """-> (lat, lon) for "12.93, 77.58", "77.58 12.93" (India ranges decide the order) or DMS; else None."""
    t = text.strip()
    dms = _DMS.findall(t)
    if len(dms) == 2:
        vals = {}
        for d, m, s, h in dms:
            v = float(d) + float(m or 0) / 60 + float(s or 0) / 3600
            vals[h.upper()] = -v if h.upper() in "SW" else v
        lat = vals.get("N", vals.get("S"))
        lon = vals.get("E", vals.get("W"))
        return (lat, lon) if lat is not None and lon is not None else None
    m = re.fullmatch(r"\s*([NS])?\s*(-?\d{1,3}\.\d+)\s*[,; ]\s*([EW])?\s*(-?\d{1,3}\.\d+)\s*", t, re.I)
    if not m:
        return None
    a, b = float(m.group(2)), float(m.group(4))
    if 5 <= a <= 38 and 67 <= b <= 99:
        return a, b
    if 5 <= b <= 38 and 67 <= a <= 99:
        return b, a
    return (a, b) if -90 <= a <= 90 and -180 <= b <= 180 else None


# ----------------------------------------------------------------------------- suggestions
def _score(q, s):
    s = (s or "").lower()
    if not s:
        return 0
    if s == q:
        return 100
    if s.startswith(q):
        return max(85, 95 - (len(s) - len(q)) // 2)
    if any(w.startswith(q) for w in re.split(r"[\s/\-_.,]+", s)):
        return 80
    return 0


def suggest(ws, result, text, user=None, limit=12):
    from . import privacy
    q = " ".join(text.lower().split())
    out = []
    if not q:
        return {"groups": [], "q": text}
    idx = build_index(ws, result)
    masked = privacy.should_mask(user)
    ll = parse_coords(text)
    if ll:
        pid = None
        pt = Point(ll[1], ll[0])
        for f in result["layers"]["parcels"]["features"]:
            if shape(f["geometry"]).contains(pt):
                pid = f["properties"]["plot_id"]
                break
        out.append({"group": "Coordinates", "score": 101, "label": f"{ll[0]:.6f}, {ll[1]:.6f}",
                    "sub": f"inside plot {pid}" if pid else "go to this point",
                    "action": {"type": "point", "center": list(ll), "plot_id": pid}})
    qp = normalize_plot_id(q) if re.match(r"^\d", q) else None
    for p in idx["plots"]:
        best, why = 0, ""
        if qp and (p["pid"] == qp or p["pid"].lower() == q):
            best, why = 100, ""
        elif qp and p["pid"].startswith(qp + ("" if "/" in qp else "/")):
            best, why = 97 - min(6, len(p["pid"]) - len(qp)), ""           # survey no. -> its hissas
        for fld, lab in (("khata", "khata"), ("pid_muni", "property ID"), ("ulpin", "ULPIN")):
            s = _score(q, p[fld]) - (8 if fld != "ulpin" else 0)
            if s > best:
                best, why = s, f"{lab} {p[fld]}"
        if best >= 80:
            out.append({"group": "Plots", "score": best, "label": f"Plot {p['pid']}",
                        "sub": " · ".join(x for x in (why, p["status"], f"{p['area']:,.0f} m²" if p["area"] else "") if x),
                        "action": {"type": "plot", "plot_id": p["pid"], "center": p["center"]}})
    if not masked and len(q) >= 3 and not re.match(r"^\d", q):
        for pe in idx["people"]:
            s = max(_score(q, pe["name"]), _score(q, pe["latin"]),
                    fuzz.partial_ratio(q, pe["latin"]) - 12 if len(q) >= 4 else 0)
            if s >= 78:
                out.append({"group": "Owners", "score": s - 5, "label": pe["name"], "sub": f"plot {pe['pid']}",
                            "action": {"type": "plot", "plot_id": pe["pid"], "center": pe["center"]}})
    from .departments import access_for
    access = access_for(user) or {}
    if len(q) >= 2:
        for r in idx["records"]:
            level = access.get(r["dept"], "full")
            if level == "none":                                  # this department's records are not shared with you
                continue
            s = _score(q, r["id"])
            if s >= 80:
                show_name = r.get("name") and not masked and level == "full"
                out.append({"group": "Records", "score": s, "label": f"{DEPT_LABEL.get(r['dept'], r['dept'])} {r['id']}",
                            "sub": f"plot {r['pid']}" + (f" · {r['name']}" if show_name else ""),
                            "action": {"type": "plot", "plot_id": r["pid"], "center": r["center"]}})
        for b in idx["buildings"]:
            s = _score(q, b["id"])
            if s >= 90:
                out.append({"group": "Buildings", "score": s, "label": b["id"],
                            "sub": f"plot {b['pid']}" + (f" · {b['floors']} floor(s)" if b.get("floors") else ""),
                            "action": {"type": "plot", "plot_id": b["pid"], "center": b["center"]}})
    m = re.fullmatch(r"cr[- ]?0*(\d+)", q)
    if m:
        out.append({"group": "Records", "score": 99, "label": f"CR-{int(m.group(1)):05d}", "sub": "change request",
                    "action": {"type": "request", "id": int(m.group(1))}})
    if len(q) >= 3:
        for pl in idx["places"]:
            if len(pl["name"]) < 3:
                continue
            s = max(_score(q, pl["name"]), fuzz.partial_ratio(q, pl["name"].lower()) - 10
                    if len(q) >= 4 and len(pl["name"]) >= len(q) else 0)
            if s >= 78:
                out.append({"group": "Places", "score": s - 3, "label": pl["name"], "sub": pl["kind"],
                            "action": {"type": "point", "center": pl["center"], "zoom": 18}})
        for w in config.list_workspaces():
            meta = config.ws_paths(w)["meta"]
            title = geo.read_json(meta).get("title", w) if meta.exists() else w
            s = max(_score(q, w), _score(q, title))
            if s >= 78 and w != ws:
                out.append({"group": "Areas", "score": s, "label": title, "sub": "switch to this area",
                            "action": {"type": "area", "ws": w}})
    words = [w for w in re.split(r"\W+", q) if len(w) >= 2]
    if words and not (qp and len(words) == 1):
        for label, kw in TEMPLATES:
            hay = (label + " " + kw).lower()
            hit = sum(1 for w in words if re.search(r"\b" + re.escape(w), hay))
            if hit == len(words) or (hit and len(words) > 2 and hit >= len(words) - 1):
                n = idx["counts"].get(label)
                out.append({"group": "Questions", "score": 70 + 5 * hit, "label": label,
                            "sub": f"{n} found" if n and n.isdigit() else (n or ""),
                            "action": {"type": "query", "q": label}})
    if len(q) >= 3:
        out.append({"group": "Ask", "score": 1, "label": f"Ask: “{text.strip()}”", "sub": "search every record in this area",
                    "action": {"type": "query", "q": text.strip()}})
    out.sort(key=lambda s: -s["score"])
    groups, per, total = {}, {}, 0
    for s in out:
        g = s["group"]
        if per.get(g, 0) >= (6 if g in ("Plots", "Questions") else 4) or total >= limit + 3:
            continue
        per[g] = per.get(g, 0) + 1
        total += 1
        groups.setdefault(g, []).append({k: v for k, v in s.items() if k not in ("group", "score")})
    order = ["Coordinates", "Plots", "Records", "Owners", "Buildings", "Places", "Areas", "Questions", "Ask"]
    return {"q": text, "groups": [{"group": g, "items": groups[g]} for g in order if g in groups]}


# ----------------------------------------------------------------------------- place lookup (OpenStreetMap Nominatim)
_geo_cache, _geo_last = {}, [0.0]


def geocode(text):
    key = " ".join(text.lower().split())
    if not key or len(key) < 3:
        return []
    if key in _geo_cache:
        return _geo_cache[key]
    with _lock:
        wait = 1.05 - (time.time() - _geo_last[0])
        if wait > 0:
            time.sleep(wait)
        _geo_last[0] = time.time()
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": text, "format": "jsonv2", "countrycodes": "in", "limit": 5, "addressdetails": 0})
    req = urllib.request.Request(url, headers={"User-Agent": "LandConnect/0.3 (SIH 2026 prototype)",
                                               "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            rows = json.load(r)
    except Exception:
        return []
    out = [{"label": x.get("display_name", "").split(",")[0], "sub": ", ".join(x.get("display_name", "").split(",")[1:4]).strip(),
            "center": [float(x["lat"]), float(x["lon"])],
            "bounds": [[float(x["boundingbox"][0]), float(x["boundingbox"][2])], [float(x["boundingbox"][1]), float(x["boundingbox"][3])]]
            if x.get("boundingbox") else None, "kind": x.get("type")} for x in rows]
    _geo_cache[key] = out
    return out
