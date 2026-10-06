"""The signed-in user's own profile: details, preferences, sign-in sessions, activity.

Everyone can read and edit their own contact details and preferences, see where they are signed in and sign
other devices out, change their password, and see what they have done. Role and department are shown but can
only be changed by the next level up (see access.py); the username never changes.
"""
import json
import re

from . import access, audit, auth, config, db, departments

EDITABLE = ("full_name", "designation", "employee_id", "office", "email", "phone", "language")
LANGUAGES = {"en": "English", "ta": "தமிழ் (Tamil)", "kn": "ಕನ್ನಡ (Kannada)", "hi": "हिन्दी (Hindi)",
             "te": "తెలుగు (Telugu)", "ml": "മലയാളം (Malayalam)"}
PREF_KEYS = {"default_ws": str, "basemap": str, "start_tab": str, "search_examples": bool}
BASEMAPS = {"satellite", "streets", "drone", "none", "google"}
TABS = {"overview", "review", "data", "changes", "accuracy", "requests", "audit"}
CAN_DO = {
    "viewer": ["See maps, parcels, conflicts, changes, accuracy and the audit trail",
               "Search and ask questions; export open data (personal names masked)"],
    "officer": ["Everything a viewer can do, with full names",
                "Propose changes to the records your department is responsible for",
                "Clear your department's inbox notices"],
    "reviewer": ["Everything an officer can do",
                 "Decide conflicts routed to your department, one at a time or in bulk",
                 "Approve or reject change requests (never your own)",
                 "Confirm uncertain column mappings; download PDF property reports"],
    "admin": ["Everything an approver can do, for every department",
              "Upload data and run the pipeline; manage all accounts, API keys and learned rules"],
}
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
_PHONE = re.compile(r"^(\+91[\s-]?)?[6-9]\d{9}$")


class ProfileError(ValueError):
    pass


def _user(username):
    u = db.query("SELECT * FROM users WHERE username = ?", (username,), one=True)
    if u is None:
        raise KeyError(username)
    return u


def _prefs(u):
    try:
        return json.loads(u.get("prefs") or "{}")
    except (TypeError, ValueError):
        return {}


def _device(ua):
    ua = ua or ""
    browser = next((b for k, b in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Chrome/", "Chrome"), ("Firefox/", "Firefox"),
                                   ("Safari/", "Safari")) if k in ua), "Browser" if ua else "Unknown")
    os_ = next((o for k, o in (("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                               ("Mac OS", "macOS"), ("Linux", "Linux")) if k in ua), "")
    if "python" in ua.lower() or "httpx" in ua.lower():
        browser, os_ = "Script / API client", ""
    return f"{browser} on {os_}" if os_ else browser


def sessions(username, current_hash=None):
    rows = db.query("SELECT token_hash, created_at, expires_at, last_seen_at, ip, user_agent FROM sessions "
                    "WHERE username = ? AND expires_at > ? ORDER BY COALESCE(last_seen_at, created_at) DESC",
                    (username, db.now()))
    return [{"id": r["token_hash"][:16], "created_at": r["created_at"], "last_seen_at": r["last_seen_at"] or r["created_at"],
             "expires_at": r["expires_at"], "ip": r["ip"] or "", "device": _device(r["user_agent"]),
             "current": r["token_hash"] == current_hash} for r in rows]


def activity(username, limit=25):
    """This person's most recent actions in every area's audit trail and in the access log."""
    needle = f'"actor": "{username}"'
    found = []
    paths = [(ws, config.ws_paths(ws)["audit"]) for ws in config.list_workspaces()] + [("access", access.log_path())]
    for ws, path in paths:
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                if needle not in line and f'"actor":"{username}"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("actor") == username:
                    found.append({"ts": e.get("ts"), "kind": e.get("kind"), "subject": e.get("subject"), "area": ws,
                                  "detail": _summary(e)})
    found.sort(key=lambda e: e["ts"] or "", reverse=True)
    return found[:limit]


def _summary(e):
    d = e.get("detail") or {}
    k = e.get("kind", "")
    if k.startswith("change."):
        return f"{d.get('request', '')} {d.get('field', '')}: {d.get('old', '')} → {d.get('new', '')}".strip()
    if k.startswith("review."):
        return (d.get("note") or d.get("conflict") or "")[:120]
    if k.startswith("access."):
        return ", ".join(f"{a}: {b}" for a, b in d.items() if not isinstance(b, dict))[:120]
    if k == "report.generated":
        return f"report {d.get('report_id', '')}"
    return ""


def stats(username):
    cr = db.query("SELECT status, COUNT(*) n FROM change_requests WHERE proposed_by = ? GROUP BY status", (username,))
    decided = db.query("SELECT COUNT(*) n FROM change_requests WHERE decided_by = ? AND status IN ('approved', 'rejected')",
                       (username,), one=True)["n"]
    reviews = db.query("SELECT COUNT(*) n FROM decisions WHERE decided_by = ?", (username,), one=True)["n"]
    by = {r["status"]: r["n"] for r in cr}
    return {"requests_proposed": sum(by.values()), "requests_by_status": by, "requests_decided": decided,
            "conflicts_decided": reviews}


def dept_can_do(u):
    """The department-specific part of what this account may do."""
    dept = u["department"]
    if dept == "all" or u["role"] == "admin":
        return []
    short = departments.DEPARTMENTS[dept]["short"]
    fields = [f["label"].lower() for f in departments.FIELDS.values() if f["dept"] == dept]
    out = [f"See every {short} record in full, with names, amounts and all source columns"]
    if u["role"] in ("officer", "reviewer") and fields:
        out.append(f"Propose changes to: {', '.join(fields)}")
    if u["role"] == "reviewer":
        out.append(f"Approve {short} changes and decide conflicts routed to {short}")
    return out


def dept_access_rows(u):
    """[(department, level)] of what this account sees of every other department (None = everything)."""
    m = departments.access_for({"role": u["role"], "department": u["department"]})
    if m is None:
        return None
    return [{"department": d, "name": departments.DEPARTMENTS[d]["short"], "level": m[d]} for d in departments.DEPARTMENTS]


def get(user, current_hash=None):
    u = _user(user["username"])
    dept = u["department"]
    dmeta = departments.DEPARTMENTS.get(dept, {})
    grantable = access.grantable_roles(user)
    upper = "state admin" if u["role"] in ("reviewer", "admin") else "state admin or your department's approver"
    return {
        "username": u["username"], "full_name": u["full_name"] or "", "role": u["role"],
        "role_label": {"viewer": "Viewer", "officer": "Officer", "reviewer": "Approver", "admin": "State admin"}[u["role"]],
        "department": dept, "department_name": "All departments" if dept == "all" else dmeta.get("name", dept),
        "designation": u.get("designation") or "", "employee_id": u.get("employee_id") or "", "office": u.get("office") or "",
        "email": u.get("email") or "", "phone": u.get("phone") or "", "language": u.get("language") or "en",
        "prefs": _prefs(u), "active": bool(u["active"]), "must_change": bool(u["must_change"]),
        "created_at": u["created_at"], "created_by": u.get("created_by"), "role_granted_by": u.get("role_granted_by"),
        "role_granted_at": u.get("role_granted_at"), "password_changed_at": u.get("password_changed_at"),
        "last_login_at": u.get("last_login_at"), "last_login_ip": u.get("last_login_ip"),
        "previous_login_at": u.get("previous_login_at"),
        "title": departments.title(u["role"], dept), "dept_access": dept_access_rows(u),
        "can_do": CAN_DO[u["role"]] + dept_can_do(u), "can_grant": grantable, "role_changed_by": upper,
        "sessions": sessions(u["username"], current_hash), "activity": activity(u["username"]), "stats": stats(u["username"]),
        "languages": LANGUAGES, "password_rules": auth.PASSWORD_RULES,
    }


def update(user, body):
    u = _user(user["username"])
    changes = {}
    for k in EDITABLE:
        if k not in body or body[k] is None:
            continue
        v = " ".join(str(body[k]).split())
        if k == "full_name" and not 2 <= len(v) <= 80:
            raise ProfileError("Full name must be 2 to 80 characters")
        if k in ("designation", "office") and len(v) > 80:
            raise ProfileError(f"{k.title()} is too long (80 characters at most)")
        if k == "employee_id" and (len(v) > 30 or (v and not re.fullmatch(r"[A-Za-z0-9/\-_.]+", v))):
            raise ProfileError("Employee ID: letters, digits and / - _ . only, 30 characters at most")
        if k == "email" and v and not _EMAIL.match(v):
            raise ProfileError("That e-mail address does not look right")
        if k == "phone":
            v = re.sub(r"[\s-]", "", v)
            if v and not _PHONE.match(v):
                raise ProfileError("Mobile number: 10 digits starting 6-9, optionally with +91")
        if k == "language" and v not in LANGUAGES:
            raise ProfileError("Unknown language")
        if (u.get(k) or "") != v:
            changes[k] = v
    prefs = _prefs(u)
    if isinstance(body.get("prefs"), dict):
        for k, v in body["prefs"].items():
            if k not in PREF_KEYS or not isinstance(v, PREF_KEYS[k]):
                continue
            if k == "default_ws" and v and v not in config.list_workspaces():
                raise ProfileError("Unknown area")
            if k == "basemap" and v and v not in BASEMAPS:
                raise ProfileError("Unknown basemap")
            if k == "start_tab" and v and v not in TABS:
                raise ProfileError("Unknown start tab")
            prefs[k] = v
        if json.dumps(prefs, sort_keys=True) != json.dumps(_prefs(u), sort_keys=True):
            changes["prefs"] = json.dumps(prefs)
    if changes:
        db.execute(f"UPDATE users SET {', '.join(k + ' = ?' for k in changes)} WHERE username = ?",
                   (*changes.values(), u["username"]))
        audit.append_one(access.log_path(), "access.profile_updated", u["username"],
                         {"fields": sorted(k for k in changes if k != "prefs") + (["preferences"] if "prefs" in changes else [])},
                         actor=u["username"])
    return sorted(changes)


def revoke(user, session_id=None, others=False, current_hash=None):
    rows = db.query("SELECT token_hash FROM sessions WHERE username = ?", (user["username"],))
    gone = [r["token_hash"] for r in rows
            if (others and r["token_hash"] != current_hash) or (session_id and r["token_hash"].startswith(session_id))]
    if session_id and not gone:
        raise KeyError(session_id)
    for h in gone:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (h,))
    if gone:
        audit.append_one(access.log_path(), "access.sessions_ended", user["username"], {"count": len(gone)},
                         actor=user["username"])
    return len(gone)
