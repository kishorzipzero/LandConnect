"""Role-based privacy: personal names are masked for users who do not need them.

Viewers (read-only accounts and read-only API keys, e.g. another agency or the public portal) see
"P••• N•••" instead of owner, account-holder, buyer and seller names - everywhere the API returns data:
the published result, one-parcel lookups, search, questions, exports, OGC API and change requests.
Officers, approvers and admins see full names. Masking happens on the server, so a viewer cannot
recover names from the browser.
"""
import json
import re

MASKED_ROLES = {"viewer"}
NAME_KEYS = {"resolved_owner", "ror_owner", "municipal_owner", "gt_owner", "owner", "owner_name", "name", "buyer",
             "seller", "from", "to", "prev_owner"}
PERSON_LAYERS = {"parcels", "connections", "ground_truth"}
NAME_FIELDS = {"owner", "registered_owner", "electricity_consumer", "water_consumer", "gas_consumer"}
_cache = {}


def should_mask(user):
    return bool(user) and user.get("role") in MASKED_ROLES


def mask_name(s):
    if not isinstance(s, str) or not s.strip():
        return s
    return " ".join(w[0] + "•••" if len(w) > 1 else w for w in s.split())


def _collect(o, out, key=None):
    if isinstance(o, dict):
        for k, v in o.items():
            _collect(v, out, k)
    elif isinstance(o, list):
        for v in o:
            _collect(v, out, key)
    elif isinstance(o, str) and key in NAME_KEYS and len(o.strip()) >= 3:
        out.add(o.strip())


def names_in(result):
    names = set()
    for k in PERSON_LAYERS:
        for f in result["layers"].get(k, {}).get("features", []):
            _collect(f["properties"], names)
    for c in result["conflicts"]:
        for v in c.get("versions") or []:
            if c["type"] in ("OWNER_MISMATCH", "MUTATION_PENDING") and isinstance(v.get("label"), str):
                names.add(v["label"].strip())
    return names


_WORD = re.compile(r"[^\s'\"(),.:;]+")
MAX_WORDS = 5


def _scan(text, names):
    """Mask every occurrence of a known name in free text: check runs of up to 5 words against the name set."""
    if len(text) < 3 or " " not in text and text not in names:
        return mask_name(text) if text in names else text
    words = list(_WORD.finditer(text))
    out, pos, i = [], 0, 0
    while i < len(words):
        for n in range(min(MAX_WORDS, len(words) - i), 0, -1):
            a, b = words[i].start(), words[i + n - 1].end()
            if text[a:b] in names:
                out += [text[pos:a], mask_name(text[a:b])]
                pos, i = b, i + n
                break
        else:
            i += 1
    return "".join(out) + text[pos:] if out else text


def _walk(o, names, key=None):
    if isinstance(o, dict):
        return {k: _walk(v, names, k) for k, v in o.items()}
    if isinstance(o, list):
        return [_walk(v, names, key) for v in o]
    if isinstance(o, str):
        return mask_name(o) if key in NAME_KEYS else _scan(o, names)
    return o


def masked_result(ws, result):
    """A copy of the result with every personal name masked (cached until the result changes)."""
    key = (ws, result.get("generated_at"), json.dumps(result.get("summary"), sort_keys=True), len(result["conflicts"]))
    hit = _cache.get(ws)
    if hit and hit[0] == key:
        return hit[1]
    names = names_in(result)
    layers = {k: (_walk(v, names) if k in PERSON_LAYERS else v) for k, v in result["layers"].items()}
    out = {k: (v if k == "layers" else _walk(v, names) if k == "conflicts" else v) for k, v in result.items()}
    out["layers"] = layers
    _cache[ws] = (key, out, names)
    return out


def mask_text_for(ws, result, obj):
    """Mask names in any other JSON-able object (change requests, audit entries) with the area's name list."""
    masked_result(ws, result)
    return _walk(obj, _cache[ws][2])


# ----------------------------------------------------------------------------- department need-to-know
def _cut(d, recs, level):
    from .departments import SUMMARY_KEYS
    if level == "full":
        return recs
    return [{k: v for k, v in r.items() if k in SUMMARY_KEYS} for r in recs]


def dept_view(result, access):
    """A copy of the result limited to what one department may see of the others (departments.access_for).
    Only other departments' linked records and registers change; the plot's common information does not."""
    from .departments import DEPARTMENTS, SUMMARY_REGISTER_KEYS
    if not access:
        return result
    hidden = [DEPARTMENTS[d]["short"] + ":" for d, lv in access.items() if lv == "none"]
    feats = []
    for f in result["layers"]["parcels"]["features"]:
        p = dict(f["properties"])
        p["dept_records"] = {d: _cut(d, recs, access.get(d, "summary")) for d, recs in (p.get("dept_records") or {}).items()
                             if access.get(d, "summary") != "none"}
        regs = {}
        for d, reg in (p.get("registers") or {}).items():
            lv = access.get(d, "summary")
            if lv == "full":
                regs[d] = reg
            elif lv == "summary":
                regs[d] = {k: v for k, v in reg.items() if k in SUMMARY_REGISTER_KEYS.get(d, ())}
        p["registers"] = regs
        if hidden:
            p["sources"] = [s for s in p.get("sources", []) if not any(s.startswith(h) for h in hidden)]
        feats.append({**f, "properties": p})
    layers = dict(result["layers"])
    layers["parcels"] = {**result["layers"]["parcels"], "features": feats}
    if "connections" in layers:
        conn = []
        for f in layers["connections"]["features"]:
            lv = access.get(f["properties"].get("dept"), "summary")
            if lv == "none":
                continue
            conn.append(f if lv == "full" else {**f, "properties": {k: v for k, v in f["properties"].items() if k != "name"}})
        layers["connections"] = {**layers["connections"], "features": conn}
    return {**result, "layers": layers, "dept_access": access}
