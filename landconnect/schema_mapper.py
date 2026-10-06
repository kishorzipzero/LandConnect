"""Intelligent attribute mapping.

Each department names its columns differently ("Khasra No.", "plot_no", "SURVEY_NO"...).
Columns are mapped to one canonical schema using two kinds of evidence:
  * the column NAME (whole-token synonym match, or fuzzy similarity for typos)
  * the column VALUES (does it look like a plot number? a person's name? an area?)
so a column called "OCCUPANT" is still recognised as the owner name from its values.
It also detects area units (sq ft, sq yd, acres...) and normalises IDs and names.
"""
import re

from indic_transliteration import sanscript
from rapidfuzz import fuzz

CANONICAL = {
    "plot_id": ["plot no", "plot", "survey no", "sy no", "khasra", "khasra no", "survey number", "plot ref",
                "parcel id", "survey", "plot number", "dag no", "gat no", "ulpin", "ts no", "town survey no"],
    "plot_sub": ["sub div", "subdiv", "sub division", "hissa", "hissa no", "subdivision", "subdivision no"],
    "owner_name": ["owner", "owner name", "owner nm", "pattadar", "pattadar name", "landholder", "name",
                   "verified owner", "khatadar", "khathadar", "holder", "occupier", "raiyat"],
    "area": ["area", "extent", "area sqm", "extent sqm", "prop area", "plot area", "area in meters", "site area"],
    "land_use": ["usage", "land use", "landuse", "use", "classification", "land classification", "verified use", "desc"],
    "record_id": ["pid", "prop id", "khata", "khata no", "khatha", "khatha no", "point id", "id", "asset id",
                  "patta no", "assessment no", "assessment",
                  "visit id", "point"],
}
FIELD_LABELS = {"plot_id": "Plot / survey ID", "plot_sub": "Sub-division", "owner_name": "Owner name",
                "area": "Area", "land_use": "Land use", "record_id": "Record ID"}
NEGATIVE_TOKENS = {"father", "husband", "guardian", "relation", "mother", "spouse"}
AREA_UNITS = [  # (tokens that indicate the unit, label, factor to square metres)
    ({"sqft", "sft", "ft"}, "sq ft", 0.092903),
    ({"sqyd", "yd", "yard", "yards", "gaj"}, "sq yd", 0.836127),
    ({"gunta", "guntas", "guntha", "gu"}, "gunta (Karnataka/Telangana)", 101.17141),
    ({"cent", "cents"}, "cent (Tamil Nadu/Kerala)", 40.468564),
    ({"ankanam"}, "ankanam (Andhra Pradesh)", 6.689),
    ({"acre", "acres", "ac"}, "acre", 4046.856),
    ({"ha", "hectare", "hectares"}, "hectare", 10000.0),
    ({"sqm", "m2", "meters", "metres", "sq m"}, "sq m", 1.0),
]
HONORIFICS = {"SRI", "SHRI", "SMT", "KUM", "DR", "MR", "MRS", "MS", "LATE", "SHRIMATI", "KUMARI"}
PLOT_RE = re.compile(r"^\D*\d{1,4}\s*[/\-]\s*[0-9A-Za-z]{1,4}\D*$")
INDIC_RE = re.compile(r"[\u0900-\u0DFF]")
_SCRIPTS = [(0x0C80, sanscript.KANNADA), (0x0B80, sanscript.TAMIL), (0x0C00, sanscript.TELUGU),
            (0x0D00, sanscript.MALAYALAM), (0x0900, sanscript.DEVANAGARI), (0x0980, sanscript.BENGALI),
            (0x0A80, sanscript.GUJARATI), (0x0A00, sanscript.GURMUKHI), (0x0B00, sanscript.ORIYA)]


def _tokens(s):
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def name_score(column, field):
    toks = set(_tokens(column))
    if toks & NEGATIVE_TOKENS:
        return 0.0
    norm = " ".join(_tokens(column))
    best = 0.0
    for syn in CANONICAL[field]:
        st = set(syn.split())
        if st <= toks:
            best = max(best, 100.0 if len(st) > 1 or len(toks) <= 3 else 90.0)
        else:
            best = max(best, float(fuzz.ratio(norm, syn)))
    return best


def value_score(values, field):
    vals = [str(v).strip() for v in values if v is not None and str(v).strip() != ""][:60]
    if not vals:
        return 0.0
    n = len(vals)
    if field == "plot_id":
        return sum(1.0 if PLOT_RE.match(v) else 0.7 if v.isdigit() else 0.0 for v in vals) / n
    if field == "plot_sub":
        return sum(1 for v in vals if re.fullmatch(r"\d{1,3}[A-Za-z]?|[A-Za-z]", v)) / n
    if field == "owner_name":
        if len(set(vals)) / n < 0.5:          # names are diverse; codes like "RES" / "water_main" repeat
            return 0.0
        vals = [to_latin(v) for v in vals]
        return sum(1 for v in vals if re.fullmatch(r"[A-Za-z][A-Za-z.' ]{2,40}", v)
                   and len(re.findall(r"[A-Za-z]+", v)) >= 2 and re.search(r"[A-Za-z]{3,}", v)) / n
    if field == "area":
        ok = 0
        for v in vals:
            try:
                ok += float(v) > 0
            except ValueError:
                pass
        return ok / n
    if field == "land_use":
        uniq = {v.lower() for v in vals}
        return 1.0 if len(uniq) <= 10 and not any(v.replace(".", "").isdigit() for v in vals) else 0.0
    if field == "record_id":
        return len(set(vals)) / n if all(re.search(r"\d", v) for v in vals) else 0.0
    return 0.0


def detect_unit(column):
    toks = set(_tokens(column))
    joined = "".join(_tokens(column))
    for unit_toks, label, factor in AREA_UNITS:
        if toks & unit_toks or any(u in joined for u in unit_toks if len(u) > 3):
            return label, factor
    return "sq m (assumed)", 1.0


CONFIRM_BELOW = 90.0      # mappings less certain than this are shown to a reviewer as "please confirm"


def map_columns(columns, records, overrides=None):
    """Return {"mappings": [...], "unmapped": [...], "area_unit": ...}. `overrides` = {column: {"field": f or None,
    "by": user}} from reviewers: a confirmed or corrected column is fixed at 100 %; field None = not mapped."""
    cands = []
    for col in columns:
        values = [r["props"].get(col) for r in records]
        for fld in CANONICAL:
            ns = name_score(col, fld)
            vs = value_score(values, fld)
            if ns >= 60:
                conf, evidence = 0.6 * ns + 0.4 * vs * 100, f"column name ({ns:.0f}%) + values ({vs * 100:.0f}%)"
            elif fld in ("owner_name", "plot_id") and vs >= 0.85 and not set(_tokens(col)) & NEGATIVE_TOKENS:
                conf, evidence = 65 * vs, f"inferred from values only ({vs * 100:.0f}% look like {FIELD_LABELS[fld].lower()})"
            else:
                continue
            if conf >= 60:
                cands.append((conf, col, fld, evidence))
    cands.sort(reverse=True)
    used_cols, used_fields, mappings = set(), set(), []
    for conf, col, fld, ev in cands:
        if col in used_cols or fld in used_fields:
            continue
        used_cols.add(col)
        used_fields.add(fld)
        m = {"column": col, "field": fld, "label": FIELD_LABELS[fld], "confidence": round(conf, 1), "evidence": ev}
        if fld == "area":
            unit, factor = detect_unit(col)
            m["unit"], m["factor"] = unit, factor
        mappings.append(m)
    for col, o in (overrides or {}).items():
        if col not in columns:
            continue
        mappings = [m for m in mappings if m["column"] != col and m["field"] != o.get("field")]
        if o.get("field") in FIELD_LABELS:
            m = {"column": col, "field": o["field"], "label": FIELD_LABELS[o["field"]], "confidence": 100.0,
                 "evidence": f"confirmed by {o.get('by', 'a reviewer')}", "confirmed_by": o.get("by")}
            if o["field"] == "area":
                m["unit"], m["factor"] = detect_unit(col)
            mappings.append(m)
    used_cols = {m["column"] for m in mappings}
    for m in mappings:
        m["needs_confirmation"] = m["confidence"] < CONFIRM_BELOW and not m.get("confirmed_by")
    return {"mappings": mappings, "unmapped": [c for c in columns if c not in used_cols]}


def apply_mapping(schema, props):
    """Canonical record from raw properties."""
    out = {}
    by_field = {m["field"]: m for m in schema["mappings"]}
    if "plot_id" in by_field:
        raw = props.get(by_field["plot_id"]["column"])
        if "plot_sub" in by_field:
            raw = f"{raw}/{props.get(by_field['plot_sub']['column'])}"
        out["plot_id"] = normalize_plot_id(raw)
        out["plot_id_raw"] = raw
    if "owner_name" in by_field:
        out["owner_name"] = props.get(by_field["owner_name"]["column"])
    if "area" in by_field:
        try:
            out["area_sqm"] = float(props.get(by_field["area"]["column"])) * by_field["area"]["factor"]
        except (TypeError, ValueError):
            pass
    if "land_use" in by_field:
        out["land_use"] = normalize_use(props.get(by_field["land_use"]["column"]))
    if "record_id" in by_field:
        out["record_id"] = props.get(by_field["record_id"]["column"])
    return out


# ----------------------------------------------------------------------------- normalisation
def normalize_plot_id(value):
    """'KH-045/2', 'Sy.No. 45 Hissa 2A', 'Plot 45/A', '45-2a' -> '45/2', '45/2A', '45/A', '45/2A'.

    Words (Sy, No, Plot, Khasra, Hissa...) are dropped, numbers lose leading zeros and a one-letter
    sub-division suffix is kept. Only the survey number and first sub-division level are used.
    """
    if value is None:
        return None
    s = re.sub(r"[A-Z]{2,}", " ", str(value).upper())       # SY, NO, PLOT, KH, HISSA ...
    s = re.sub(r"(?<![A-Z0-9])[A-Z]\.", " ", s)              # abbreviations: "H." (hissa), "S." (survey)
    toks = re.findall(r"\d+[A-Z]?|(?<![A-Z0-9])[A-Z](?![A-Z0-9])", s)
    if not toks or not toks[0][0].isdigit():
        return None
    return "/".join(re.sub(r"^0+(?=\d)", "", t) for t in toks[:2])


def id_similarity(a, b):
    """1.0 same plot; 0.8 same hissa, other letter (3 vs 3A); 0.7 parent/child (45 vs 45/A);
    0.3 same survey number, other hissa; otherwise scaled fuzzy similarity."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    pa, pb = a.split("/"), b.split("/")
    if pa[0] == pb[0]:
        if len(pa) == 1 or len(pb) == 1:
            return 0.7
        if re.sub(r"[A-Z]", "", pa[1]) == re.sub(r"[A-Z]", "", pb[1]):
            return 0.8
        return 0.3
    return fuzz.ratio(a, b) / 100 * 0.5


def normalize_use(value):
    v = str(value or "").strip().lower()
    for key, words in {"residential": ["res", "resid", "house"], "commercial": ["com", "shop", "commer"],
                       "institutional": ["ins", "inst", "school", "govt"], "vacant": ["vac", "vacant", "open"]}.items():
        if any(v.startswith(w) for w in words):
            return key
    return v or None


def _phonetic(t):
    """Collapse common transliteration variants: SOONITA / SUNEETA -> SUNITA, GEETHA -> GITA."""
    for a, b in (("OO", "U"), ("EE", "I"), ("TH", "T"), ("SH", "S"), ("DH", "D"), ("BH", "B"), ("KH", "K"),
                 ("OW", "AU"), ("W", "V"), ("Z", "J")):
        t = t.replace(a, b)
    t = re.sub(r"(.)\1+", r"\1", t)
    t = re.sub(r"AV$", "AO", t)                          # RAAV (Kannada ರಾವ್) ~ RAO
    # inherent vowel of Indic transliteration: MANJUNATHA ~ MANJUNATH
    return t[:-1] if len(t) > 4 and t.endswith("A") and t[-2] not in "AEIOU" else t


def to_latin(text):
    """Transliterate Indic-script text (Kannada, Tamil, Telugu, Devanagari, ...) to Latin letters."""
    text = str(text or "")
    if not INDIC_RE.search(text):
        return text
    out = text.translate(_TAMIL_ONLY)                     # letters the ITRANS scheme leaves untransliterated
    for base, scheme in _SCRIPTS:
        if any(base <= ord(ch) < base + 0x80 for ch in text):
            out = sanscript.transliterate(out, scheme, sanscript.ITRANS)
    for a in (".n", ".N", "~N", "~n"):
        out = out.replace(a, "n")
    out = re.sub(r"M(?=[bcdfghjklmnpqrstvwyzBCDFGHJKLNPQRSTVWYZ])", "n", out)   # anusvara before a consonant
    return re.sub(r"[^A-Za-z .']", "", out.replace(".h", ""))


TAMIL_RE = re.compile(r"[஀-௿]")
_TAMIL_ONLY = str.maketrans({"ன": "ந", "ற": "ர", "ழ": "ல", "ள": "ல"})
TAMIL_HONORIFICS = {"TIRU", "THIRU", "TIRUMATI", "TIRUMATHI", "THIRUMATHI", "THIRUMATI", "TMT", "TIRUMIKU", "THIRUMIGU"}


def _tamil_fold(t):
    """Tamil script does not mark voicing or aspiration (க = k/g, த = t/th/d, ச = s/ch/j, ப = p/b), and ழ is
    written zh or l: fold both spellings of a name onto one form before comparing."""
    for a, b in (("CH", "S"), ("J", "S"), ("SH", "S"), ("KS", "TS"), ("X", "TS"), ("KH", "K"), ("GH", "K"), ("G", "K"),
                 ("TH", "T"), ("DH", "T"), ("D", "T"), ("BH", "P"), ("PH", "P"), ("B", "P"), ("F", "P"), ("ZH", "L"), ("Z", "L")):
        t = t.replace(a, b)
    return re.sub(r"(.)+", r"", t)


def normalize_name(name, tamil=False):
    raw = str(name or "")
    # a Tamil initial is one letter and a full stop ("ச. முருகன்" = "S. Murugan"): keep only its first Latin letter
    raw = re.sub(r"(?<![஀-௿])([஀-௿])[ா-்]?\s*\.", lambda m: to_latin(m.group(1))[:1] + ". ", raw)
    s = re.sub(r"[^A-Z ]", " ", to_latin(raw).upper())
    toks = [_phonetic(t) for t in s.split() if t not in HONORIFICS and t not in TAMIL_HONORIFICS]
    if tamil:
        toks = [f for f in (_tamil_fold(t) for t in toks) if f not in _FOLDED_TITLES]
    # trailing initials (father's / village initial) are optional once both a first and last name are present
    if sum(len(t) > 1 for t in toks) >= 2:
        toks = [t for t in toks if len(t) > 1]
    return toks


_FOLDED_TITLES = {_tamil_fold(_phonetic(h)) for h in HONORIFICS | TAMIL_HONORIFICS}


def owner_similarity(a, b):
    """0-100. Handles honorifics, initials ("R. Kumar"), swapped order and spelling variants.

    Every token of the shorter name must find a good partner in the other name, so
    "Suresh Kumar" vs "Ramesh Kumar" scores low even though the surname matches.
    """
    tamil = bool(TAMIL_RE.search(str(a or "")) or TAMIL_RE.search(str(b or "")))
    ta, tb = normalize_name(a, tamil), normalize_name(b, tamil)
    if not ta or not tb:
        return 0.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    # pair words one-to-one (best pairs first): a word of the other name, or its initial, is used only once, so
    # "K. Ganesh" vs "Sumathi K." cannot pair Ganesh with the same K that already matched K
    cand = []
    for i, t in enumerate(short):
        for j, u in enumerate(long_):
            if len(t) == 1 or len(u) == 1:
                cand.append((100.0 if t[0] == u[0] else 0.0, True, i, j))
            else:
                cand.append((float(fuzz.ratio(t, u)), False, i, j))
    cand.sort(key=lambda c: (-c[0], c[1]))
    got, used, via_initial = {}, set(), False
    for s, init, i, j in cand:
        if i in got or j in used:
            continue
        got[i], used = s, used | {j}
        via_initial |= init and s > 0
    scores = [got.get(i, 0.0) for i in range(len(short))]
    score = min(scores) * 0.7 + (sum(scores) / len(scores)) * 0.3
    # "R. Kumar" could be Ramesh or Ravi: an initial is weaker evidence than a full name
    return min(score, 85.0) if via_initial else score
