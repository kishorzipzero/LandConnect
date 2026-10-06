"""Learning from reviewers: turn consistent human decisions into proposed auto-resolution rules.

Every reviewer decision says which source was right for one conflict. When, for one conflict type, reviewers
have decided at least MIN_DECISIONS cases and chosen the same source in at least MIN_AGREEMENT of them, the
system proposes a rule: "for this conflict type, accept <source> automatically". A rule is never switched on by
itself: an admin enables it (and can switch it off), it is written to the audit trail, and every conflict it
settles says so in its resolution. Saved reviewer decisions still override a learned rule.
The auto-resolve rate of each pipeline run is kept, so the effect of the rules is visible over time.
"""
from . import config, db, geo

MIN_DECISIONS = 8
MIN_AGREEMENT = 0.9


def rules_path(ws):
    return config.ws_paths(ws)["root"] / "learned_rules.json"


def load_rules(ws):
    if not ws:
        return []
    p = rules_path(ws)
    return geo.read_json(p) if p.exists() else []


def save_rules(ws, rules):
    geo.write_json(rules_path(ws), rules, indent=2)


def stats(ws, result):
    """Per conflict type: reviewer decisions, the source they chose most, agreement, and whether a rule is due."""
    by_id = {c["id"]: c for c in result["conflicts"]}
    groups = {}
    for d in db.query("SELECT conflict_id, choice FROM decisions WHERE ws = ?", (ws,)):
        c = by_id.get(d["conflict_id"])
        if c is None:
            continue
        g = groups.setdefault(c["type"], {"type": c["type"], "title": c["title"], "decisions": 0, "choices": {}})
        g["decisions"] += 1
        src = (c["versions"][d["choice"]]["source"] if d["choice"] is not None and d["choice"] < len(c["versions"])
               else "(resolved another way)")
        g["choices"][src] = g["choices"].get(src, 0) + 1
    enabled = {(r["type"], r["source"]) for r in load_rules(ws) if r.get("enabled")}
    out = []
    for g in groups.values():
        src, k = max(g["choices"].items(), key=lambda kv: kv[1])
        g["top_source"], g["agreement"] = src, round(k / g["decisions"], 3)
        g["eligible"] = (g["decisions"] >= MIN_DECISIONS and g["agreement"] >= MIN_AGREEMENT
                         and src != "(resolved another way)")
        g["enabled"] = (g["type"], src) in enabled
        g["would_settle"] = sum(1 for c in result["conflicts"] if c["type"] == g["type"] and c["status"] == "open"
                                and any(v.get("source") == src for v in c.get("versions", [])))
        out.append(g)
    out.sort(key=lambda g: (-g["eligible"], -g["decisions"]))
    return {"patterns": out, "rules": load_rules(ws), "thresholds": {"min_decisions": MIN_DECISIONS,
                                                                    "min_agreement": MIN_AGREEMENT},
            "history": auto_rate_history(ws)}


def auto_rate_history(ws, limit=15):
    rows = []
    for r in reversed(db.runs(ws, limit)):
        s = (r.get("summary") or {}).get("conflicts")
        if r["status"] == "done" and s and s.get("total"):
            rows.append({"at": r["started_at"], "auto_resolved": s["auto_resolved"], "total": s["total"],
                         "rate": round(s["auto_resolved"] / s["total"], 3)})
    return rows


def set_rule(ws, ctype, source, enabled, user, based_on=None):
    rules = [r for r in load_rules(ws) if not (r["type"] == ctype and r["source"] == source)]
    rules.append({"type": ctype, "source": source, "enabled": bool(enabled), "by": user, "at": db.now(),
                  "based_on": based_on})
    save_rules(ws, rules)
    return rules


def apply(ctx, conflicts):
    """Auto-resolve open conflicts covered by an enabled learned rule. Returns how many."""
    rules = {(r["type"], r["source"]): r for r in load_rules(ctx.get("ws")) if r.get("enabled")}
    n = 0
    for c in conflicts:
        if c["status"] != "open":
            continue
        for i, v in enumerate(c.get("versions") or []):
            r = rules.get((c["type"], v.get("source")))
            if r:
                b = r.get("based_on") or {}
                c["status"], c["resolved_by"], c["chosen"] = "auto_resolved", "learned rule", i
                c["resolution"] = (f"Learned rule (enabled by {r['by']}): reviewers chose {v['source']} in "
                                   f"{b.get('agree', '?')} of {b.get('decisions', '?')} decided cases of this type.")
                c["learned_rule"] = True
                n += 1
                break
    return n
