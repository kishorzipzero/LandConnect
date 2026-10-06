"""Who may grant which role: delegated user administration.

  Role granted            Who can grant, change or remove it
  ----------------------  -----------------------------------------------------------------
  viewer                  state admin (any department) · department approver (own department)
  officer                 state admin (any department) · department approver (own department)
  reviewer (approver)     state admin only
  admin                   state admin only

Rules
  * Nobody can change, disable or reset their own account through these functions.
  * The last active admin cannot be demoted or disabled.
  * A department approver sees and manages only the viewers and officers of their own department.
  * New accounts and password resets get a one-time password; the user must change it at first sign-in.
  * Every grant, role change, disable / enable and reset is appended to a hash-chained access log
    (output/access_audit.jsonl) with the name of the person who did it.
"""
import secrets

from . import audit, auth, config, db

GRANTABLE_BY_APPROVER = ("viewer", "officer")
MATRIX = [
    {"role": "viewer", "label": "Viewer", "granted_by": "State admin · department approver (own department)"},
    {"role": "officer", "label": "Officer (proposes changes)", "granted_by": "State admin · department approver (own department)"},
    {"role": "reviewer", "label": "Approver (approves changes, decides conflicts)", "granted_by": "State admin only"},
    {"role": "admin", "label": "State admin", "granted_by": "State admin only"},
]


def log_path():
    return config.OUTPUT_DIR / "access_audit.jsonl"


def _log(kind, subject, detail, actor):
    audit.append_one(log_path(), kind, subject, detail, actor=actor)


def is_state_admin(actor):
    return actor["role"] == "admin"


def is_dept_approver(actor):
    return actor["role"] == "reviewer" and actor.get("department", "all") != "all"


def can_grant(actor, role, department):
    if is_state_admin(actor):
        return True
    return is_dept_approver(actor) and role in GRANTABLE_BY_APPROVER and department == actor["department"]


def _get(username):
    u = db.query("SELECT username, full_name, role, department, active, must_change, created_by, created_at, "
                 "role_granted_by, role_granted_at FROM users WHERE username = ?", (username,), one=True)
    if u is None:
        raise KeyError(username)
    return u


def _guard(actor, target):
    if target["username"] == actor["username"]:
        raise PermissionError("You cannot change your own access; ask the next level up")
    if not can_grant(actor, target["role"], target["department"]):
        raise PermissionError("This account is outside what you can manage")


def _last_admin(target):
    if target["role"] != "admin" or not target["active"]:
        return False
    return db.query("SELECT COUNT(*) n FROM users WHERE role = 'admin' AND active = 1", one=True)["n"] <= 1


def manageable(actor):
    if is_state_admin(actor):
        return db.query("SELECT username, full_name, role, department, active, must_change, created_by, created_at, "
                        "role_granted_by, role_granted_at FROM users ORDER BY department, role DESC, username")
    if is_dept_approver(actor):
        return db.query("SELECT username, full_name, role, department, active, must_change, created_by, created_at, "
                        "role_granted_by, role_granted_at FROM users WHERE department = ? ORDER BY role DESC, username",
                        (actor["department"],))
    return []


def grantable_roles(actor):
    if is_state_admin(actor):
        return {"roles": list(auth.ROLES), "departments": "any"}
    if is_dept_approver(actor):
        return {"roles": list(GRANTABLE_BY_APPROVER), "departments": [actor["department"]]}
    return {"roles": [], "departments": []}


def create(actor, username, role, department, full_name=None):
    department = department or actor.get("department", "all")
    if not can_grant(actor, role, department):
        raise PermissionError(f"You cannot grant the {role} role in {department}")
    temp = secrets.token_urlsafe(12)
    auth.create_user(username, temp, role, full_name, department, created_by=actor["username"], must_change=True)
    _log("access.user_created", username, {"role": role, "department": department}, actor["username"])
    return temp


def change_role(actor, username, role, department=None):
    target = _get(username)
    _guard(actor, target)
    department = department or target["department"]
    if not can_grant(actor, role, department):
        raise PermissionError(f"You cannot grant the {role} role in {department}")
    if role not in auth.ROLES:
        raise ValueError(f"Unknown role {role}")
    auth._check_dept(department)
    if _last_admin(target) and role != "admin":
        raise PermissionError("This is the last active admin")
    if (target["role"], target["department"]) == (role, department):
        raise ValueError("No change")
    db.execute("UPDATE users SET role = ?, department = ?, role_granted_by = ?, role_granted_at = ? WHERE username = ?",
               (role, department, actor["username"], db.now(), username))
    db.execute("DELETE FROM sessions WHERE username = ?", (username,))           # new rights on next sign-in
    _log("access.role_changed", username, {"from": {"role": target["role"], "department": target["department"]},
                                          "to": {"role": role, "department": department}}, actor["username"])
    return _get(username)


def set_active(actor, username, active):
    target = _get(username)
    _guard(actor, target)
    if not active and _last_admin(target):
        raise PermissionError("This is the last active admin")
    auth.set_active(username, active)
    _log("access.enabled" if active else "access.disabled", username, {}, actor["username"])
    return _get(username)


def reset_password(actor, username):
    target = _get(username)
    _guard(actor, target)
    temp = secrets.token_urlsafe(12)
    auth.set_password(username, temp, must_change=True)
    _log("access.password_reset", username, {}, actor["username"])
    return temp


def history(actor, limit=100):
    entries = audit.read(log_path(), limit=1000)
    if is_dept_approver(actor):
        mine = {u["username"] for u in manageable(actor)}
        entries = [e for e in entries if e["subject"] in mine or e["actor"] == actor["username"]]
    return {"verify": audit.verify(log_path()), "entries": entries[:limit]}
