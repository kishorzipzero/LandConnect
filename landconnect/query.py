"""Natural-language query over the harmonised result.

    "parcels with owner conflicts"                 "encroachments near Jayanagar Shopping Complex"
    "vacant plots larger than 5 guntas"            "buildings with more than 3 floors"
    "plots owned by Ramesh Kumar"                  "lowest confidence 10 parcels"
    "survey 112" / "plot 112/3A"                   "field verified commercial plots needing review"
    "how many plots have a sale not mutated"       "total tax at risk from floors beyond permission"
    "water department conflicts"                   "plots without conflicts larger than 1 acre"
    "new construction since the last survey"       "plots with a broken ownership chain"
    "plots between 200 and 400 sq m"               "KATDR1T6JY17UE" (ULPIN)

Two engines produce the SAME filter object, so results are identical in shape:
  * rules  - an offline, deterministic parser (default; works with no internet, e.g. in a taluk office)
  * claude - Claude translates the question into the filter object (structured output); used when an
             Anthropic credential is configured (ANTHROPIC_API_KEY or LANDCONNECT_QUERY_ENGINE=claude).
             Any failure or refusal falls back to the rule parser.
The filter is always shown back to the user ("Interpreted as ..."), so nothing is hidden.
"""
import json
import math
import os
import re

from rapidfuzz import fuzz, process
from shapely.geometry import shape

from .schema_mapper import normalize_plot_id, owner_similarity

CONFLICT_TYPES = ["OWNER_MISMATCH", "AREA_MISMATCH", "GEOMETRY_DISAGREEMENT", "MERGED_PARCEL", "NO_REVENUE_RECORD",
                  "RECORD_WITHOUT_PARCEL", "MISSING_IN_MUNICIPAL", "ENCROACHMENT", "UTILITY_UNDER_BUILDING",
                  "UNASSESSED_CONSTRUCTION", "BUILDING_SPANS_PLOTS", "MUTATION_PENDING", "TAX_USE_MISMATCH",
                  "SERVICE_WITHOUT_TAX", "FLOORS_BEYOND_PERMISSION", "DUPLICATE_PARCEL", "INVALID_GEOMETRY",
                  "CADASTRAL_SOURCES_DISAGREE"]
DEPARTMENTS = ["revenue", "survey", "municipal", "town_planning", "registration", "water", "electricity", "gas", "roads"]
CHANGE_TYPES = ["new", "demolished", "extension", "new_floor"]
EMPTY = {"entity": "parcels", "status": [], "conflict_types": [], "has_open_conflict": None, "land_use": None,
         "owner": None, "plot": None, "min_area_sqm": None, "max_area_sqm": None, "min_score": None,
         "max_score": None, "min_floors": None, "max_floors": None, "near": None, "near_m": None,
         "gnss": None, "field_verified": None, "sort": None, "limit": None,
         "departments": [], "change_types": [], "chain_broken": None, "new_plot": None, "min_revenue": None,
         "ulpin": None, "aggregate": None}
UNITS = [(r"sq\.?\s*ft|sqft|square\s*f(ee|oo)t|sft", 0.092903), (r"sq\.?\s*yd|square\s*yards?|gaj", 0.836127),
         (r"guntas?|gunthas?", 101.17141), (r"cents?", 40.468564), (r"acres?", 4046.856), (r"hectares?|ha\b", 1e4),
         (r"grounds?", 222.967), (r"sq\.?\s*m|m2|m²|sqm|square\s*met(er|re)s?", 1.0)]
MONEY = [(r"crores?|cr\b", 1e7), (r"lakhs?|lacs?|l\b", 1e5), (r"k\b|thousand", 1e3)]
MONEY_WORDS = r"\btax\b|\brevenue\b|₹|\brs\b|\brs\.|\brupees?\b|\binr\b|\brisk\b"   # whole words: "floors" is not "rs"
CMP = r"(>=|<=|>|<|at least|at most|over|above|more than|greater than|larger than|bigger than|under|below|less than|smaller than|fewer than)"
TYPE_WORDS = [
    (r"owner(ship)?\s*(mismatch|conflict|dispute|differ)|different owners?|disputed owner|stale owner|owner name", ["OWNER_MISMATCH"]),
    (r"encroach", ["ENCROACHMENT"]),
    (r"area\s*mismatch|wrong (area|extent)|extent (mismatch|differ)", ["AREA_MISMATCH"]),
    (r"no (revenue|ror|rtc|patta) record|without (a )?(ror|rtc|revenue|patta)|missing (revenue|ror|rtc|patta)", ["NO_REVENUE_RECORD"]),
    (r"record without (a )?parcel|orphan", ["RECORD_WITHOUT_PARCEL"]),
    (r"unassessed|not assessed|missing (from )?(the )?(municipal|tax)|not in (the )?tax|no (property )?tax record|tax (gap|evasion)", ["MISSING_IN_MUNICIPAL", "UNASSESSED_CONSTRUCTION"]),
    (r"utility|pipe|sewer", ["UTILITY_UNDER_BUILDING"]),
    (r"merged|amalgamat", ["MERGED_PARCEL"]),
    (r"(boundary|geometry|boundaries)\s*(disagree|mismatch|differ|conflict)", ["GEOMETRY_DISAGREEMENT"]),
    (r"spans?|straddl", ["BUILDING_SPANS_PLOTS"]),
    (r"mutation|not mutated|sale (not|pending)|unmutated|deed not (in|updated)", ["MUTATION_PENDING"]),
    (r"(commercial|shop).{0,30}residential (tax|assess)|tax use|use mismatch|wrong (tax )?use", ["TAX_USE_MISMATCH"]),
    (r"(services?|connections?|electricity|water|gas).{0,20}(without|no|but no) (property )?tax|service without tax", ["SERVICE_WITHOUT_TAX"]),
    (r"floors? (beyond|above|over|more than|exceeding) (the )?(building )?permi|beyond (the )?(building )?permission|unauthori[sz]ed (floor|construction)|illegal floor|extra floor", ["FLOORS_BEYOND_PERMISSION"]),
    (r"duplicate", ["DUPLICATE_PARCEL"]),
    (r"(cadastral|survey)s? (sources? |maps? )?(disagree|differ|mismatch)|two (cadastral )?surveys|ncscm|tngis", ["CADASTRAL_SOURCES_DISAGREE"]),
    (r"self[- ]intersect|invalid (geometry|polygon|shape)|bow[- ]?tie", ["INVALID_GEOMETRY"]),
]
DEPT_WORDS = {"revenue": r"revenue", "survey": r"survey(?! no| number)", "municipal": r"municipal|corporation|bbmp|ulb",
              "town_planning": r"town ?planning|tp dept", "registration": r"registration|igr|sub[- ]?registrar",
              "water": r"water|bwssb|metrowater", "electricity": r"electricity|bescom|discom|power|tangedco",
              "gas": r"\bgas\b|png", "roads": r"roads?"}
CHANGE_WORDS = [(r"new (construction|building)s?|built since|newly built", "new"), (r"demolish", "demolished"),
                (r"extension|extended|footprint grew", "extension"), (r"floors? added|added floors?|new floors?|raised", "new_floor")]


def _cmp_dir(word):
    return "min" if word in (">", ">=", "at least", "over", "above", "more than", "greater than", "larger than",
                             "bigger than") else "max"


def parse_rules(text, result=None):
    q = " " + text.lower().strip() + " "
    f = {**EMPTY, "status": [], "conflict_types": [], "departments": [], "change_types": []}
    if (re.search(r"\bbuildings?\b|\bhouses?\b|\bstructures?\b", re.sub(r"building (permission|permit|plan)s?", "", q))
            and not re.search(r"\bplots?\b|\bparcels?\b", q)):
        f["entity"] = "buildings"
    for pat, t in CHANGE_WORDS:
        if re.search(pat, q) and not re.search(r"permi", q):
            f["change_types"].append(t)
    if f["change_types"] or re.search(r"\bchanges?\b|since the last survey|between (the )?surveys", q):
        f["entity"] = "changes"
    for pat, st in ((r"needs? review|in review|to review|under review|pending review|\breview\b", "review"),
                    (r"auto[- ]?approved|\bapproved\b", "approved"), (r"\bflagged\b", "flagged"),
                    (r"verified by|reviewer verified|\bverified\b(?! in field)", "verified")):
        if re.search(pat, q) and not (st == "verified" and re.search(r"field[- ]verified", q)):
            f["status"].append(st)
    for pat, types in TYPE_WORDS:
        if re.search(pat, q):
            f["conflict_types"] += [t for t in types if t not in f["conflict_types"]]
    if re.search(r"(without|no|free of|clean|zero)\s+(open\s+)?(conflicts?|issues?|problems?|disputes?)|\bclean plots?\b", q):
        f["has_open_conflict"] = False
    elif not f["conflict_types"] and re.search(r"conflict|dispute|issue|problem|discrepan", q):
        f["has_open_conflict"] = True
    for d, pat in DEPT_WORDS.items():
        if re.search(rf"(routed to|for|of|in|by)\s+(the\s+)?({pat})\b|\b({pat})\s+(department|dept|conflicts?|issues?|queue|inbox|problems?)\b", q):
            f["departments"].append(d)
    for use in ("residential", "commercial", "vacant", "institutional"):
        if use in q or (use == "institutional" and re.search(r"school|temple|hospital|college", q)):
            if not (use in ("commercial", "residential") and "TAX_USE_MISMATCH" in f["conflict_types"]):
                f["land_use"] = use
    # money
    if re.search(r"tax at risk|revenue at risk|tax gap|uncollected|lost (tax|revenue)|tax loss", q):
        f["min_revenue"] = f["min_revenue"] or 1
        if not f["sort"]:
            f["sort"] = "revenue_desc"
    for m in re.finditer(CMP + r"\s*(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*([a-z\.]{0,9})", q):
        before = q[max(0, m.start() - 30):m.start()]
        if re.search(MONEY_WORDS, before + m.group(0)):
            mult = next((x for pat, x in MONEY if re.match(r"\s*(" + pat + r")", m.group(3))), 1.0)
            if _cmp_dir(m.group(1)) == "min":
                f["min_revenue"] = float(m.group(2)) * mult
    # numeric filters
    m = re.search(r"between\s+(\d+(?:\.\d+)?)\s*(?:and|to|-)\s*(\d+(?:\.\d+)?)\s*([a-z²\. ]{0,14})", q)
    if m:
        lo, hi, rest = float(m.group(1)), float(m.group(2)), m.group(3)
        before = q[max(0, m.start() - 25):m.start()]
        if re.match(r"\s*(floors?|storeys?)", rest):
            f["min_floors"], f["max_floors"], f["entity"] = int(lo), int(hi), "buildings"
        elif re.search(r"score|confiden", before):
            f["min_score"], f["max_score"] = lo, hi
        else:
            fac = next((x for pat, x in UNITS if re.match(r"\s*(" + pat + r")", rest)), 1.0)
            f["min_area_sqm"], f["max_area_sqm"] = round(lo * fac, 1), round(hi * fac, 1)
    for m in re.finditer(CMP + r"\s*(\d+(?:\.\d+)?)\s*([a-z²\. ]{0,14})", q):
        word, val, rest = m.group(1), float(m.group(2)), m.group(3)
        before = q[max(0, m.start() - 25):m.start()]
        if re.search(MONEY_WORDS, before + rest):
            continue
        if re.match(r"\s*(floors?|storeys?|stories|levels?)", rest):
            strict = {"over": 1, "above": 1, "more than": 1, "greater than": 1, ">": 1, "under": -1, "below": -1,
                      "less than": -1, "fewer than": -1, "<": -1}.get(word, 0)
            f[f"{_cmp_dir(word)}_floors"] = int(val) + strict
            if not f["conflict_types"]:
                f["entity"] = "buildings"
        elif re.search(r"score|confiden", before):
            f[f"{_cmp_dir(word)}_score"] = val
        else:
            factor = next((fac for pat, fac in UNITS if re.match(r"\s*(" + pat + r")", rest)), None)
            if factor is not None or re.search(r"area|size|extent|big|large|small", before):
                f[f"{_cmp_dir(word)}_area_sqm"] = round(val * (factor or 1.0), 1)
    m = re.search(r"(\d+)\s*\+?\s*(floors?|storeys?|stories)", q)
    if m and f["min_floors"] is None and f["max_floors"] is None and "FLOORS_BEYOND_PERMISSION" not in f["conflict_types"]:
        f["min_floors"] = int(m.group(1))
        f["entity"] = "buildings"
    # owner / plot / place / ids
    m = re.search(r"(?:owned by|owner(?: is| named)?|belonging to|in the name of|pattadar|patta holder)\s+([^\d,]+?)(?=\s+(?:with|in|near|that|which|and|having|needing|where)\b|,|\s*$)", q)
    if m and m.group(1).strip() not in ("conflict", "conflicts", "mismatch", "mismatches", "name"):
        f["owner"] = m.group(1).strip()
    m = re.search(r"(?:plot|survey|sy\.?|s\.no\.?|khasra|hissa|parcel|site)\s*(?:no\.?|number|#)?\s*(\d+(?:\s*[/\-]\s*[0-9a-z]{1,3})?)\b", q)
    if m:
        f["plot"] = normalize_plot_id(m.group(1))
    elif re.fullmatch(r"\s*\d{1,4}\s*/\s*[0-9a-z]{1,4}\s*", q):                      # just "103/1"
        f["plot"] = normalize_plot_id(q.strip())
    m = re.search(r"\b([a-z]{2}[0-9bcdefghjkmnpqrstuvwxyz]{12})\b", q)
    if m and re.search(r"\d", m.group(1)):
        f["ulpin"] = m.group(1).upper()
    m = re.search(r"(?:near|around|close to|next to)\s+(.+?)(?:\s+within\s+(\d+)\s*m)?\s*$", q)
    if m:
        f["near"] = m.group(1).strip()
        f["near_m"] = float(m.group(2)) if m.group(2) else None
    m = re.search(r"within\s+(\d+)\s*m(?:etres|eters)?\s+of\s+(.+?)\s*$", q)
    if m:
        f["near"], f["near_m"] = m.group(2).strip(), float(m.group(1))
    if re.search(r"gnss|dgps|cors|surveyed", q):
        f["gnss"] = True
    if re.search(r"(not|never|un)[- ]?(field[- ]?)?verified in (the )?field|not field[- ]?verified|without (a )?(field|site) visit", q):
        f["field_verified"] = False
    elif re.search(r"field[- ]?verified|ground[- ]?truth|verified in (the )?field|site visit", q):
        f["field_verified"] = True
    if re.search(r"broken (ownership )?chain|chain (is )?broken|ownership chain", q):
        f["chain_broken"] = True
    if re.search(r"new(ly)? (added )?plots?|added plots?|plots? added", q):
        f["new_plot"] = True
    # sorting, limits, aggregates
    if re.search(r"largest|biggest", q) and not f["sort"]:
        f["sort"] = "area_desc"
    elif re.search(r"smallest", q):
        f["sort"] = "area_asc"
    elif re.search(r"lowest (score|confidence)|least confident|weakest", q):
        f["sort"] = "score_asc"
    elif re.search(r"highest (score|confidence)|most confident", q):
        f["sort"] = "score_desc"
    elif re.search(r"(most|highest|biggest) (tax|revenue)", q):
        f["sort"] = "revenue_desc"
    m = re.search(r"(?:top|first|show)\s+(\d+)|(\d+)\s+(?:lowest|largest|smallest|highest|most|least|biggest)", q)
    if m:
        f["limit"] = int(m.group(1) or m.group(2))
    elif f["sort"] in ("area_desc", "area_asc", "score_asc", "score_desc"):
        f["limit"] = 20
    if re.search(r"total (tax|revenue)|how much (tax|revenue)|sum of (tax|revenue)", q):
        f["aggregate"] = "total_revenue"
    elif re.search(r"total (area|extent|land)|how much (land|area)", q):
        f["aggregate"] = "total_area"
    elif re.search(r"average (score|confidence)|mean (score|confidence)", q):
        f["aggregate"] = "mean_score"
    elif re.search(r"how many|count of|number of|\bcount\b", q):
        f["aggregate"] = "count"
    return f


# ----------------------------------------------------------------------------- Claude engine
FILTER_SCHEMA = {
    "type": "object",
    "properties": {
        "entity": {"type": "string", "enum": ["parcels", "buildings", "changes"]},
        "status": {"type": "array", "items": {"type": "string", "enum": ["approved", "flagged", "review", "verified"]}},
        "conflict_types": {"type": "array", "items": {"type": "string", "enum": CONFLICT_TYPES}},
        "departments": {"type": "array", "items": {"type": "string", "enum": DEPARTMENTS}},
        "change_types": {"type": "array", "items": {"type": "string", "enum": CHANGE_TYPES}},
        **{k: {"anyOf": [{"type": t}, {"type": "null"}]} for k, t in (
            ("has_open_conflict", "boolean"), ("owner", "string"), ("plot", "string"), ("min_area_sqm", "number"),
            ("max_area_sqm", "number"), ("min_score", "number"), ("max_score", "number"), ("min_floors", "integer"),
            ("max_floors", "integer"), ("near", "string"), ("near_m", "number"), ("gnss", "boolean"),
            ("field_verified", "boolean"), ("limit", "integer"), ("chain_broken", "boolean"), ("new_plot", "boolean"),
            ("min_revenue", "number"), ("ulpin", "string"))},
        "land_use": {"anyOf": [{"type": "string", "enum": ["residential", "commercial", "vacant", "institutional"]},
                               {"type": "null"}]},
        "sort": {"anyOf": [{"type": "string", "enum": ["area_desc", "area_asc", "score_asc", "score_desc", "revenue_desc"]},
                           {"type": "null"}]},
        "aggregate": {"anyOf": [{"type": "string", "enum": ["count", "total_area", "total_revenue", "mean_score"]},
                                {"type": "null"}]},
    },
    "required": list(EMPTY), "additionalProperties": False,
}
SYSTEM = ("You translate a land-records officer's question into a filter over harmonised parcel data. "
          "Parcels have a plot ID like '112/3A' (survey number / hissa), owner, area in m², land use, a confidence "
          "score 0-100, a status (approved, flagged, review, verified), an estimated annual property tax at risk in "
          "rupees (min_revenue), an ownership chain of registered deeds (chain_broken) and a 14-character ULPIN. "
          "Conflict types: " + ", ".join(CONFLICT_TYPES) + ". Departments conflicts are routed to: " +
          ", ".join(DEPARTMENTS) + ". Changes between two surveys: " + ", ".join(CHANGE_TYPES) +
          " (use entity 'changes'). Convert areas to square metres (1 sq ft = 0.092903 m², 1 gunta = 101.17 m², "
          "1 cent = 40.47 m², 1 ground = 222.97 m², 1 acre = 4046.86 m²) and money to rupees (1 lakh = 100000). "
          "has_open_conflict false means 'without conflicts'. aggregate is set for 'how many' (count), 'total area', "
          "'total tax at risk' (total_revenue) or 'average score' questions. Use null or [] for anything the question "
          "does not constrain. Put a place or landmark the user mentions in 'near'. Owner names may be in any Indian "
          "script; copy them as written.")


def claude_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY")) or os.environ.get("LANDCONNECT_QUERY_ENGINE") == "claude"


def parse_claude(text):
    import anthropic
    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model="claude-opus-5",
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": FILTER_SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": text}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("request declined")
    body = next(b.text for b in response.content if b.type == "text")
    return {**EMPTY, **json.loads(body)}


def parse(text, result=None, engine=None):
    engine = engine or ("claude" if claude_available() else "rules")
    if engine == "claude":
        try:
            return parse_claude(text), "claude"
        except Exception as e:           # offline, no credit, refusal ... -> deterministic parser
            f = parse_rules(text, result)
            f["_note"] = f"Claude unavailable ({type(e).__name__}); used the offline parser"
            return f, "rules"
    return parse_rules(text, result), "rules"


# ----------------------------------------------------------------------------- execution
def _dist_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[1], a[0], b[1], b[0]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def _resolve_place(name, result):
    """A plot ID or a named landmark / road from the OSM layers -> (lon, lat, label)."""
    pid = normalize_plot_id(name) if re.search(r"\d", name) else None
    for f in result["layers"]["parcels"]["features"]:
        if pid and f["properties"]["plot_id"] == pid:
            c = shape(f["geometry"]).centroid
            return c.x, c.y, f"plot {pid}"
    named = [(f["properties"].get("name"), f) for lyr in ("poi", "roads") for f in result["layers"].get(lyr, {}).get("features", [])
             if f["properties"].get("name")]
    if not named:
        return None
    best = process.extractOne(name, [n for n, _ in named], scorer=fuzz.WRatio)
    if not best or best[1] < 70:
        return None
    feat = named[best[2]][1]
    c = shape(feat["geometry"]).centroid
    return c.x, c.y, best[0]


def _revenue_by_plot(result):
    rev = {}
    for c in result["conflicts"]:
        if c.get("revenue_inr") and c["status"] != "resolved":
            rev[c["plot_id"]] = max(rev.get(c["plot_id"], 0), c["revenue_inr"])
    return rev


def run(result, f, max_items=500):
    """-> (matches[:max_items], total, notes, summary). summary answers 'how many / total / average' questions."""
    explain = []
    ent = f.get("entity") or "parcels"
    open_by_plot, depts_by_plot = {}, {}
    for c in result["conflicts"]:
        if c["status"] == "open":
            open_by_plot.setdefault(c["plot_id"], set()).add(c["type"])
            if c.get("service_evidence"):                     # live connections on an untaxed property
                open_by_plot[c["plot_id"]].add("SERVICE_WITHOUT_TAX")
            depts_by_plot.setdefault(c["plot_id"], set()).update(c.get("departments") or [])
    rev = _revenue_by_plot(result)
    parcels = {x["properties"]["plot_id"]: x for x in result["layers"]["parcels"]["features"]}
    near = None
    if f.get("near"):
        near = _resolve_place(f["near"], result)
        if near:
            explain.append(f"within {int(f.get('near_m') or 200)} m of {near[2]}")
        else:
            explain.append(f"place '{f['near']}' not found — ignored")

    if ent == "buildings":
        feats = result["layers"]["buildings"]["features"]
    elif ent == "changes":
        feats = result["layers"]["changes"]["features"]
    else:
        feats = result["layers"]["parcels"]["features"]
    out = []
    for feat in feats:
        p = feat["properties"]
        pid = p.get("plot_id")
        pp = parcels.get(pid, {}).get("properties", {}) if ent != "parcels" else p
        if f.get("status") and pp.get("status") not in f["status"]:
            continue
        types = open_by_plot.get(pid, set())
        if f.get("conflict_types") and not (types & set(f["conflict_types"])):
            continue
        if f.get("has_open_conflict") is True and not types:
            continue
        if f.get("has_open_conflict") is False and types:
            continue
        if f.get("departments") and not (depts_by_plot.get(pid, set()) & set(f["departments"])):
            continue
        if f.get("change_types") and ent == "changes" and p.get("type") not in f["change_types"]:
            continue
        if f.get("land_use") and (pp.get("land_use") or "") != f["land_use"]:
            continue
        area = p.get("area_sqm") if ent != "changes" else (p.get("area_t2") or p.get("area_t1"))
        if f.get("min_area_sqm") is not None and (area or 0) < f["min_area_sqm"]:
            continue
        if f.get("max_area_sqm") is not None and (area or 0) > f["max_area_sqm"]:
            continue
        if f.get("min_score") is not None and (pp.get("score") or 0) < f["min_score"]:
            continue
        if f.get("max_score") is not None and (pp.get("score") or 100) > f["max_score"]:
            continue
        if f.get("min_floors") is not None and (p.get("floors") or 0) < f["min_floors"]:
            continue
        if f.get("max_floors") is not None and (p.get("floors") or 99) > f["max_floors"]:
            continue
        if f.get("plot"):
            want = f["plot"]
            if not pid or not (pid == want or ("/" not in want and pid.split("/")[0] == want)):
                continue
        if f.get("ulpin") and pp.get("ulpin") != f["ulpin"]:
            continue
        if f.get("gnss") and not pp.get("gnss_vertices"):
            continue
        if f.get("field_verified") is True and not pp.get("gt_owner"):
            continue
        if f.get("field_verified") is False and pp.get("gt_owner"):
            continue
        if f.get("chain_broken") and (pp.get("ownership_chain") or {}).get("ok", True):
            continue
        if f.get("new_plot") and not pp.get("new_plot"):
            continue
        if f.get("min_revenue") and rev.get(pid, 0) < f["min_revenue"]:
            continue
        if f.get("owner"):
            names = [pp.get(k) for k in ("resolved_owner", "ror_owner", "municipal_owner", "gt_owner") if pp.get(k)]
            if not any(owner_similarity(f["owner"], n) >= 75 for n in names):
                continue
        g = shape(feat["geometry"])
        c = g.centroid if g.geom_type != "Point" else g
        if near and _dist_m((c.x, c.y), near[:2]) > (f.get("near_m") or 200):
            continue
        label = (f"Plot {pid}" if ent == "parcels" else p.get("id") or p.get("type", "").replace("_", " ").title())
        tax = rev.get(pid, 0)
        detail = (f"{pp.get('status', '')} · {pp.get('score', '')} · {area or 0:.0f} m²"
                  + (f" · {pp.get('resolved_owner')}" if pp.get("resolved_owner") else "")
                  + (f" · tax at risk ₹{tax:,.0f}/yr" if tax and (f.get("min_revenue") or f.get("sort") == "revenue_desc") else "")) \
            if ent == "parcels" else \
            (f"plot {pid} · {p.get('floors') or '?'} floor(s) · {area or 0:.0f} m²" if ent == "buildings" else p.get("detail", ""))
        out.append({"label": label, "plot_id": pid, "detail": detail, "center": [c.y, c.x], "kind": ent,
                    "area": area or 0, "score": pp.get("score") or 0, "revenue": tax, "status": pp.get("status"),
                    "types": sorted(types)})
    sort = f.get("sort")
    if sort:
        key, rev_ = {"area_desc": ("area", True), "area_asc": ("area", False), "score_asc": ("score", False),
                     "score_desc": ("score", True), "revenue_desc": ("revenue", True)}[sort]
        out.sort(key=lambda r: r[key], reverse=rev_)
    if f.get("limit"):
        out = out[:int(f["limit"])]
    return out[:max_items], len(out), explain, summarize(out, f, ent)


def summarize(items, f, ent="parcels"):
    """One-line answer plus totals for the matched items."""
    n = len(items)
    area = sum(i["area"] for i in items)
    tax = sum(i["revenue"] for i in items)
    score = sum(i["score"] for i in items) / n if n else None
    noun = {"parcels": "plot", "buildings": "building", "changes": "change"}[ent]
    s = {"count": n, "total_area_sqm": round(area, 1), "total_revenue_inr": round(tax), "mean_score": round(score, 1) if score else None}
    agg = f.get("aggregate")
    if agg == "total_area":
        s["answer"] = f"{area:,.0f} m² ({area / 4046.856:,.2f} acres) across {n} {noun}{'s' * (n != 1)}"
    elif agg == "total_revenue":
        s["answer"] = f"₹{tax:,.0f} a year of property tax at risk across {n} {noun}{'s' * (n != 1)} (estimate)"
    elif agg == "mean_score":
        s["answer"] = f"Average confidence {score:.1f} over {n} {noun}{'s' * (n != 1)}" if n else "No matches"
    elif agg == "count":
        s["answer"] = f"{n} {noun}{'s' * (n != 1)}"
    return s


def is_empty(f):
    """True when the question set no condition at all (e.g. just a place name): do not list every plot."""
    return not any(v not in (None, [], "parcels") for k, v in f.items() if not k.startswith("_") and k not in ("entity", "limit"))


def describe(f):
    parts = [f.get("entity") or "parcels"]
    if f.get("status"):
        parts.append("status " + " / ".join(f["status"]))
    if f.get("conflict_types"):
        parts.append("with " + ", ".join(t.replace("_", " ").lower() for t in f["conflict_types"]))
    if f.get("has_open_conflict") is True:
        parts.append("with any open conflict")
    if f.get("has_open_conflict") is False:
        parts.append("with no open conflict")
    if f.get("departments"):
        parts.append("routed to " + ", ".join(d.replace("_", " ") for d in f["departments"]))
    if f.get("change_types"):
        parts.append("change: " + ", ".join(t.replace("_", " ") for t in f["change_types"]))
    for k, lab in (("land_use", "land use"), ("owner", "owner ≈"), ("plot", "plot"), ("ulpin", "ULPIN")):
        if f.get(k):
            parts.append(f"{lab} {f[k]}")
    for k, lab, unit in (("min_area_sqm", "area ≥", " m²"), ("max_area_sqm", "area ≤", " m²"), ("min_score", "score ≥", ""),
                         ("max_score", "score ≤", ""), ("min_floors", "floors ≥", ""), ("max_floors", "floors ≤", "")):
        if f.get(k) is not None:
            parts.append(f"{lab} {f[k]}{unit}")
    if f.get("min_revenue"):
        parts.append("tax at risk" + (f" ≥ ₹{f['min_revenue']:,.0f}" if f["min_revenue"] > 1 else ""))
    if f.get("gnss"):
        parts.append("GNSS-surveyed")
    if f.get("field_verified") is True:
        parts.append("field-verified")
    if f.get("field_verified") is False:
        parts.append("not field-verified")
    if f.get("chain_broken"):
        parts.append("ownership chain broken")
    if f.get("new_plot"):
        parts.append("added by survey")
    if f.get("sort"):
        parts.append("sorted by " + f["sort"].replace("_", " "))
    if f.get("limit"):
        parts.append(f"top {f['limit']}")
    if f.get("aggregate"):
        parts.append({"count": "count", "total_area": "total area", "total_revenue": "total tax at risk",
                      "mean_score": "average score"}[f["aggregate"]])
    return parts
