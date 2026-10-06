"""Authentication and role-based access.

Roles (each includes the ones before it):
  viewer    read results, maps, audit trail, exports, OGC API
  officer   + propose changes to the fields their department is the authority for (maker)
  reviewer  + approve / reject other officers' changes, decide conflicts routed to their department (checker)
  admin     + run the pipeline, upload data, reset the demo, manage users and API keys

Every user and API key belongs to a department (revenue, municipal, water, electricity ...) or to
'all' (state-level). Officers and reviewers can only act for their own department.

People sign in with a password (PBKDF2-HMAC-SHA256, 310 000 iterations, per-user salt) and get an
HttpOnly session cookie (12 h). Other departments' systems use API keys (`X-API-Key` header or
`Authorization: Bearer lc_...`). Only SHA-256 hashes of session tokens and API keys are stored.
"""
import datetime as dt
import hashlib
import hmac
import os
import re
import secrets

from fastapi import HTTPException, Request

from . import db

ROLES = {"viewer": 1, "officer": 2, "reviewer": 3, "admin": 4}
SESSION_HOURS = 12
COOKIE = "lc_session"
ITERATIONS = 310_000


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS).hex()
    return h, salt


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


PASSWORD_RULES = ["At least 10 characters", "Not your username", "Not a common password",
                  "At least two of: letters, digits, symbols", "Different from your current password"]
COMMON = {"password", "password1", "password123", "1234567890", "qwertyuiop", "welcome123", "admin12345",
          "landconnect", "iloveindia", "india12345", "abcdefghij", "0987654321", "1111111111", "letmein123"}


def validate_password(password, username=None, strict=False):
    """Length always; the other rules only for passwords a person chooses (strict), not generated ones."""
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters")
    if not strict:
        return
    low = password.lower()
    if username and username.lower() in low:
        raise ValueError("Password must not contain your username")
    if low in COMMON or len(set(low)) < 4:
        raise ValueError("That password is too common or too simple")
    kinds = sum(bool(re.search(p, password)) for p in (r"[A-Za-z]", r"\d", r"[^A-Za-z0-9]"))
    if kinds < 2:
        raise ValueError("Use at least two of: letters, digits, symbols")


# ----------------------------------------------------------------------------- users
def _check_dept(department):
    from .departments import DEPARTMENTS
    if department != "all" and department not in DEPARTMENTS:
        raise ValueError(f"Unknown department {department}")


def can_act_for(user, department):
    return user.get("department", "all") in ("all", department)


def create_user(username, password, role, full_name=None, department="all", created_by=None, must_change=False):
    if role not in ROLES:
        raise ValueError(f"Unknown role {role}")
    _check_dept(department)
    if not username or not username.replace("_", "").replace(".", "").isalnum():
        raise ValueError("Username may contain letters, digits, '.' and '_' only")
    validate_password(password)
    h, salt = hash_password(password)
    db.execute("INSERT INTO users (username, full_name, role, department, pw_hash, pw_salt, created_at, created_by, "
               "must_change, role_granted_by, role_granted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (username, full_name or username, role, department, h, salt, db.now(), created_by or "system",
                1 if must_change else 0, created_by or "system", db.now()))


def set_password(username, password, must_change=False):
    validate_password(password, username, strict=not must_change)
    h, salt = hash_password(password)
    db.execute("UPDATE users SET pw_hash = ?, pw_salt = ?, must_change = ?, password_changed_at = ? WHERE username = ?",
               (h, salt, 1 if must_change else 0, None if must_change else db.now(), username))
    db.execute("DELETE FROM sessions WHERE username = ?", (username,))


def list_users():
    return db.query("SELECT username, full_name, role, department, active, created_at FROM users ORDER BY department, username")


def set_active(username, active):
    db.execute("UPDATE users SET active = ? WHERE username = ?", (1 if active else 0, username))
    if not active:
        db.execute("DELETE FROM sessions WHERE username = ?", (username,))


def verify_password(username, password):
    u = db.query("SELECT * FROM users WHERE username = ? AND active = 1", (username,), one=True)
    if u is None:
        hash_password(password)              # same work either way: no user-enumeration timing signal
        return None
    h, _ = hash_password(password, u["pw_salt"])
    return u if hmac.compare_digest(h, u["pw_hash"]) else None


def bootstrap():
    """First start: create one account per role. Passwords come from the environment or are generated
    and written once to output/initial_credentials.txt (outside every served path)."""
    if db.query("SELECT COUNT(*) n FROM users", one=True)["n"]:
        return None
    lines = ["Land Connect - initial accounts (change these passwords after first sign-in)", ""]
    for user, role, name in (("admin", "admin", "Administrator"), ("reviewer", "reviewer", "Land records reviewer"),
                             ("viewer", "viewer", "Read-only viewer")):
        pw = os.environ.get(f"LANDCONNECT_{user.upper()}_PASSWORD") or secrets.token_urlsafe(12)
        create_user(user, pw, role, name)
        lines.append(f"{user:9s} ({role:8s})  password: {pw}")
    path = db.DB_PATH.parent / "initial_credentials.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def seed_department_users():
    """An officer (maker), an approver (checker) and a read-only staff account per department, created once.
    Passwords come from LANDCONNECT_DEPT_PASSWORD or are generated and appended to output/initial_credentials.txt.
    Each account's designation is its department's job title (departments.TITLES)."""
    from .departments import DEPARTMENTS, title as job_title
    lines = []
    for dept, meta in DEPARTMENTS.items():
        for suffix, role in (("officer", "officer"), ("approver", "reviewer"), ("staff", "viewer")):
            user = f"{dept}.{suffix}"
            if db.query("SELECT 1 FROM users WHERE username = ?", (user,), one=True):
                db.execute("UPDATE users SET designation = ? WHERE username = ? AND role = ? "
                           "AND (designation IS NULL OR designation = '')", (job_title(role, dept), user, role))
                continue
            pw = os.environ.get("LANDCONNECT_DEPT_PASSWORD") or secrets.token_urlsafe(12)
            create_user(user, pw, role, f"{meta['short']} {job_title(role, dept).lower()}", dept)
            db.execute("UPDATE users SET designation = ? WHERE username = ?", (job_title(role, dept), user))
            lines.append(f"{user:26s} ({role:8s}, {dept:13s})  password: {pw}")
    if lines:
        path = db.DB_PATH.parent / "initial_credentials.txt"
        with open(path, "a", encoding="utf-8") as f:
            f.write("\nDepartment accounts (officer proposes, approver approves, staff is read-only)\n"
                    + "\n".join(lines) + "\n")
        return path
    return None


# ----------------------------------------------------------------------------- sessions & API keys
def login(username, password, ip=None, user_agent=None):
    u = verify_password(username, password)
    if u is None:
        return None, None
    token = secrets.token_urlsafe(32)
    exp = (dt.datetime.now() + dt.timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")
    db.execute("INSERT INTO sessions (token_hash, username, created_at, expires_at, ip, user_agent, last_seen_at) "
               "VALUES (?, ?, ?, ?, ?, ?, ?)", (_sha(token), username, db.now(), exp, ip, (user_agent or "")[:300], db.now()))
    db.execute("UPDATE users SET previous_login_at = last_login_at, last_login_at = ?, last_login_ip = ? WHERE username = ?",
               (db.now(), ip, username))
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now(),))
    return token, {"username": u["username"], "full_name": u["full_name"], "role": u["role"],
                   "department": u["department"], "must_change": bool(u["must_change"])}


def logout(token):
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha(token),))


def create_api_key(name, role, created_by, department="all"):
    if role not in ROLES or role == "admin":
        raise ValueError("API keys can be viewer, officer or reviewer")
    _check_dept(department)
    key = "lc_" + secrets.token_urlsafe(32)
    db.execute("INSERT INTO api_keys (key_hash, prefix, name, role, department, created_by, created_at) "
               "VALUES (?, ?, ?, ?, ?, ?, ?)", (_sha(key), key[:10], name, role, department, created_by, db.now()))
    return key


def list_api_keys():
    return db.query("SELECT prefix, name, role, department, created_by, created_at, last_used, active FROM api_keys "
                    "ORDER BY created_at DESC")


def revoke_api_key(prefix):
    return db.execute("UPDATE api_keys SET active = 0 WHERE prefix = ?", (prefix,))


def identify(request: Request):
    """The caller (user or API client) or None."""
    auth = request.headers.get("authorization", "")
    key = request.headers.get("x-api-key") or (auth[7:] if auth.lower().startswith("bearer lc_") else None)
    if key:
        k = db.query("SELECT * FROM api_keys WHERE key_hash = ? AND active = 1", (_sha(key),), one=True)
        if k:
            db.execute("UPDATE api_keys SET last_used = ? WHERE key_hash = ?", (db.now(), k["key_hash"]))
            return {"username": f"api:{k['name']}", "full_name": k["name"], "role": k["role"],
                    "department": k["department"], "kind": "api_key"}
        return None
    token = request.cookies.get(COOKIE) or (auth[7:] if auth.lower().startswith("bearer ") else None)
    if not token:
        return None
    s = db.query("SELECT s.expires_at, s.last_seen_at, u.username, u.full_name, u.role, u.department, u.must_change "
                 "FROM sessions s JOIN users u "
                 "ON u.username = s.username WHERE s.token_hash = ? AND u.active = 1", (_sha(token),), one=True)
    if s is None or s["expires_at"] < db.now():
        return None
    now = db.now()
    if not s["last_seen_at"] or s["last_seen_at"][:16] != now[:16]:       # at most once a minute
        db.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (now, _sha(token)))
    return {"username": s["username"], "full_name": s["full_name"], "role": s["role"],
            "department": s["department"], "must_change": bool(s["must_change"]), "kind": "session",
            "_session": _sha(token)}


def require(role, allow_must_change=False):
    """FastAPI dependency: the caller must hold at least `role`. Accounts still on a one-time password
    can only change it (or read who they are) until they do."""
    def dep(request: Request):
        user = identify(request)
        if user is None:
            raise HTTPException(401, "Sign in required")
        if user.get("must_change") and not allow_must_change:
            raise HTTPException(403, "Change your one-time password first")
        if ROLES[user["role"]] < ROLES[role]:
            raise HTTPException(403, f"Needs the {role} role")
        return user
    return dep
