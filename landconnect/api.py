"""Land Connect web server: dashboard, sign-in, pipeline control, review workflow, audit trail,
natural-language query, exports and data-exchange APIs. Every data endpoint takes ?ws=<area>.

Access: viewer < reviewer < admin (see auth.py). People sign in with a password (session cookie);
other departments' systems use API keys (X-API-Key header).

Run:  python -m uvicorn landconnect.api:app --port 8000
Docs: http://127.0.0.1:8000/docs      OGC API - Features: http://127.0.0.1:8000/ogc
"""
import json
import os
import re
import shutil
import threading
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (access, audit, auth, config, db, departments, export, geo, ingest, pipeline, query, records, report,
               review, synth)

VIEWER, OFFICER = auth.require("viewer"), auth.require("officer")
REVIEWER, ADMIN = auth.require("reviewer"), auth.require("admin")
SELF = auth.require("viewer", allow_must_change=True)
MAX_UPLOAD_MB = 250
UPLOAD_EXT = ingest.GDAL_EXT | ingest.GEOJSON_EXT | ingest.TABLE_EXT | ingest.IMAGE_EXT | {".npy", ".dbf", ".shx", ".prj", ".cpg"}
SAFE_NAME = re.compile(r"^[\w.\-]+$")


@asynccontextmanager
async def lifespan(_app):
    db.connect().close()                                   # create tables
    created = auth.bootstrap()
    if created:
        print(f"\n  Land Connect: first start - accounts created. Passwords are in {created}\n", flush=True)
    dept_users = auth.seed_department_users()
    if dept_users:
        print(f"\n  Land Connect: department accounts created. Passwords are in {dept_users}\n", flush=True)
    for ws in config.list_workspaces():                    # migrate old decision files, index old results
        try:                                               # one damaged area must never stop the server
            p = config.ws_paths(ws)
            db.import_legacy_decisions(ws, p["decisions"])
            if p["result"].exists() and not db.query("SELECT 1 FROM parcels WHERE ws = ? LIMIT 1", (ws,)):
                r = geo.read_json(p["result"])
                db.index_result(ws, r, r.get("run_id"))
        except Exception as e:
            print(f"  Land Connect: could not index area '{ws}' ({type(e).__name__}: {e}). "
                  f"It still opens; run the pipeline on it to rebuild.", flush=True)
    yield


app = FastAPI(
    title="Land Connect API",
    description="AI-enabled integration and harmonisation of multi-source geospatial land data (SIH 2026, PS 013). "
                "Sign in with POST /api/login (session cookie) or send an API key in the X-API-Key header. "
                "Roles: viewer (read), officer (+ propose record changes for their department), reviewer (+ approve "
                "changes and decide conflicts for their department), admin (+ run pipeline, upload, users, keys). "
                "Users and API keys belong to one department or to 'all'.",
    version="0.3.0", lifespan=lifespan,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/") or request.url.path.startswith("/ogc"):
        resp.headers.setdefault("Cache-Control", "no-store")
    elif request.url.path in ("/", "/sw.js", "/manifest.webmanifest") or request.url.path.startswith("/static/"):
        # the app's own files: always re-check with the server (a quick ETag check), so an update shows on reload
        resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


_lock = threading.Lock()
_results, _runs = {}, {}
LAYER_TITLES = {
    "parcels": "Harmonised parcels with confidence", "buildings": "Buildings (fused)",
    "changes": "Detected changes", "municipal": "Municipal property layer (as received)",
    "cadastral_georef": "Cadastral map (georeferenced)", "cadastral_alt": "Second cadastral survey (as received)", "cadastral_naive": "Cadastral map (before correction)",
    "footprints": "Building footprints (as received)", "ai_buildings": "AI building footprints (as received)",
    "utility": "Utility network", "gnss": "GNSS control points", "ground_truth": "Ground-truth field visits",
    "roads": "Road network", "poi": "Landmarks & land use",
}


def _ws(ws):
    ws = ws or config.DEFAULT_WS
    if ws not in config.list_workspaces():
        raise HTTPException(404, f"Unknown workspace '{ws}'")
    return ws


def _view(ws, user):
    """The result as this user may see it: personal names masked for viewers, and other departments' records
    limited to what the user's department may see (see privacy.py and departments.access_for)."""
    from . import privacy
    r = _result(ws)
    r = privacy.masked_result(ws, r) if privacy.should_mask(user) else r
    return privacy.dept_view(r, departments.access_for(user))


def privacy_masked(user):
    from . import privacy
    return privacy.should_mask(user)


def _mask_any(ws, user, obj):
    from . import privacy
    r = _result(ws, required=False)
    return privacy.mask_text_for(ws, r, obj) if r is not None and privacy.should_mask(user) else obj


def _result(ws, required=True):
    if ws not in _results and config.ws_paths(ws)["result"].exists():
        _results[ws] = geo.read_json(config.ws_paths(ws)["result"])
    r = _results.get(ws)
    if r is None and required:
        raise HTTPException(404, "No result yet — run the pipeline")
    return r


def _save(ws):
    geo.write_json(config.ws_paths(ws)["result"], _results[ws])


# ----------------------------------------------------------------------------- sign-in
class Login(BaseModel):
    username: str
    password: str


_failures = {}   # ip -> [timestamps] : 5 failed sign-ins per 5 minutes


@app.post("/api/login", tags=["auth"], summary="Sign in; sets an HttpOnly session cookie")
def login(body: Login, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    recent = [t for t in _failures.get(ip, []) if t > time.time() - 300]
    if len(recent) >= 5:
        raise HTTPException(429, "Too many failed sign-ins. Wait 5 minutes.")
    token, user = auth.login(body.username.strip(), body.password, ip, request.headers.get("user-agent"))
    if token is None:
        _failures[ip] = recent + [time.time()]
        raise HTTPException(401, "Wrong username or password")
    _failures.pop(ip, None)
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="strict", max_age=auth.SESSION_HOURS * 3600)
    return {"user": user}


@app.post("/api/logout", tags=["auth"])
def logout(request: Request, response: Response):
    auth.logout(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@app.get("/api/me", tags=["auth"], summary="The signed-in user")
def me(user=Depends(SELF)):
    from . import profile
    out = {k: v for k, v in user.items() if not k.startswith("_")}
    out["title"] = departments.title(user.get("role"), user.get("department"))
    out["dept_access"] = departments.access_for(user)          # None = sees every department in full
    if user.get("kind") == "session":
        u = db.query("SELECT prefs, language FROM users WHERE username = ?", (user["username"],), one=True) or {}
        out["prefs"], out["language"] = profile._prefs(u), u.get("language") or "en"
    return out


def _session_only(user):
    if user.get("kind") != "session":
        raise HTTPException(400, "Profiles belong to people; API keys have none")


@app.get("/api/me/profile", tags=["auth"], summary="Your profile: details, access, sessions, activity")
def my_profile(user=Depends(SELF)):
    from . import profile
    _session_only(user)
    return profile.get(user, user.get("_session"))


class ProfileUpdate(BaseModel):
    full_name: str | None = None
    designation: str | None = None
    employee_id: str | None = None
    office: str | None = None
    email: str | None = None
    phone: str | None = None
    language: str | None = None
    prefs: dict | None = None


@app.put("/api/me/profile", tags=["auth"], summary="Update your own contact details and preferences")
def update_my_profile(body: ProfileUpdate, user=Depends(VIEWER)):
    from . import profile
    _session_only(user)
    try:
        changed = profile.update(user, body.model_dump())
    except profile.ProfileError as e:
        raise HTTPException(400, str(e))
    return {"changed": changed, "profile": profile.get(user, user.get("_session"))}


@app.post("/api/me/sessions/{session_id}/end", tags=["auth"], summary="Sign out one of your devices")
def end_my_session(session_id: str, user=Depends(VIEWER)):
    from . import profile
    _session_only(user)
    if len(session_id) < 8:
        raise HTTPException(400, "Unknown session")
    try:
        profile.revoke(user, session_id=session_id)
    except KeyError:
        raise HTTPException(404, "Unknown session")
    return {"sessions": profile.sessions(user["username"], user.get("_session"))}


@app.post("/api/me/sessions/end-others", tags=["auth"], summary="Sign out every device except this one")
def end_other_sessions(user=Depends(VIEWER)):
    from . import profile
    _session_only(user)
    n = profile.revoke(user, others=True, current_hash=user.get("_session"))
    return {"ended": n, "sessions": profile.sessions(user["username"], user.get("_session"))}


class Password(BaseModel):
    current: str
    new: str


@app.post("/api/me/password", tags=["auth"], summary="Change your own password")
def change_password(body: Password, user=Depends(SELF)):
    if user.get("kind") != "session" or auth.verify_password(user["username"], body.current) is None:
        raise HTTPException(400, "Current password is wrong")
    if body.new == body.current:
        raise HTTPException(400, "Choose a password different from your current one")
    try:
        auth.set_password(user["username"], body.new)
    except ValueError as e:
        raise HTTPException(400, str(e))
    access._log("access.password_changed", user["username"], {}, user["username"])
    return {"ok": True, "note": "Password changed; sign in again."}


# ----------------------------------------------------------------------------- administration
class NewUser(BaseModel):
    username: str
    password: str
    role: str = "viewer"
    full_name: str | None = None
    department: str = "all"


@app.get("/api/admin/users", tags=["admin"])
def users(_=Depends(ADMIN)):
    return auth.list_users()


@app.post("/api/admin/users", tags=["admin"], summary="Create a user")
def create_user(body: NewUser, admin=Depends(ADMIN)):
    try:
        auth.create_user(body.username, body.password, body.role, body.full_name, body.department)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(409, "User already exists")
    audit.append_one(config.ws_paths(config.DEFAULT_WS)["audit"], "admin.user_created", body.username,
                     {"role": body.role, "department": body.department}, actor=admin["username"])
    return {"ok": True}


@app.get("/api/admin/dept-access", tags=["admin"],
         summary="Department access matrix: how much of each department's records every other department sees")
def dept_access(_=Depends(ADMIN)):
    rows = db.query("SELECT viewer, target, level, updated_by, updated_at FROM dept_access")
    return {"levels": departments.ACCESS_LEVELS, "matrix": departments.access_matrix(),
            "defaults": {v: {t: ("full" if v == t else departments.DEFAULT_ACCESS.get(v, {}).get(t, departments.DEFAULT_OTHER))
                             for t in departments.DEPARTMENTS} for v in departments.DEPARTMENTS},
            "changed": rows}


class AccessCell(BaseModel):
    viewer: str
    target: str
    level: str


@app.put("/api/admin/dept-access", tags=["admin"], summary="Change what one department may see of another's records")
def set_dept_access(body: AccessCell, admin=Depends(ADMIN)):
    try:
        old = departments.access_matrix().get(body.viewer, {}).get(body.target)
        departments.set_access(body.viewer, body.target, body.level, admin["username"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.append_one(config.ws_paths(config.DEFAULT_WS)["audit"], "admin.dept_access", f"{body.viewer}->{body.target}",
                     {"from": old, "to": body.level}, actor=admin["username"])
    return {"ok": True, "matrix": departments.access_matrix()}


@app.post("/api/admin/users/{username}/active", tags=["admin"], summary="Enable or disable a user")
def user_active(username: str, active: bool, admin=Depends(ADMIN)):
    if username == admin["username"] and not active:
        raise HTTPException(400, "You cannot disable yourself")
    auth.set_active(username, active)
    return {"ok": True}


class NewKey(BaseModel):
    name: str
    role: str = "viewer"
    department: str = "all"


@app.get("/api/admin/api-keys", tags=["admin"])
def api_keys(_=Depends(ADMIN)):
    return auth.list_api_keys()


@app.post("/api/admin/api-keys", tags=["admin"], summary="Issue an API key (shown once)")
def create_key(body: NewKey, admin=Depends(ADMIN)):
    try:
        key = auth.create_api_key(body.name, body.role, admin["username"], body.department)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"key": key, "note": "Store this key now; only its hash is kept."}


@app.post("/api/admin/api-keys/{prefix}/revoke", tags=["admin"])
def revoke_key(prefix: str, _=Depends(ADMIN)):
    if not auth.revoke_api_key(prefix):
        raise HTTPException(404, "Unknown key")
    return {"ok": True}


# ----------------------------------------------------------------------------- delegated access management
class Grant(BaseModel):
    username: str
    role: str
    department: str | None = None
    full_name: str | None = None


class RoleChange(BaseModel):
    role: str
    department: str | None = None


def _access(fn, *a):
    try:
        return fn(*a)
    except KeyError:
        raise HTTPException(404, "Unknown user")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:                           # e.g. duplicate username
        if "UNIQUE" in str(e):
            raise HTTPException(409, "Username already exists")
        raise


@app.get("/api/access/matrix", tags=["access"], summary="Who may grant which role")
def access_matrix(user=Depends(VIEWER)):
    return {"matrix": access.MATRIX, "you_can_grant": access.grantable_roles(user)}


@app.get("/api/access/users", tags=["access"], summary="Accounts you can manage")
def access_users(user=Depends(REVIEWER)):
    return access.manageable(user)


@app.post("/api/access/users", tags=["access"], summary="Create an account; returns a one-time password (shown once)")
def access_create(body: Grant, user=Depends(REVIEWER)):
    temp = _access(access.create, user, body.username.strip(), body.role, body.department, body.full_name)
    return {"username": body.username.strip(), "one_time_password": temp,
            "note": "Give this to the person securely. They must change it at first sign-in."}


@app.post("/api/access/users/{username}/role", tags=["access"], summary="Change an account's role or department")
def access_role(username: str, body: RoleChange, user=Depends(REVIEWER)):
    return _access(access.change_role, user, username, body.role, body.department)


@app.post("/api/access/users/{username}/active", tags=["access"], summary="Enable or disable an account")
def access_active(username: str, active: bool, user=Depends(REVIEWER)):
    return _access(access.set_active, user, username, active)


@app.post("/api/access/users/{username}/reset-password", tags=["access"],
          summary="Issue a new one-time password (shown once)")
def access_reset(username: str, user=Depends(REVIEWER)):
    return {"username": username, "one_time_password": _access(access.reset_password, user, username)}


@app.get("/api/access/audit", tags=["access"], summary="Access log: grants, role changes, disables, resets")
def access_audit(user=Depends(REVIEWER)):
    return access.history(user)


# ----------------------------------------------------------------------------- property report (PDF)
@app.get("/api/parcels/{plot_id:path}/report.pdf", tags=["exchange"],
         summary="Detailed property report: every government body's records for one plot (approvers and admins)")
def property_report(plot_id: str, ws: str | None = None, user=Depends(REVIEWER)):
    ws = _ws(ws)
    r = _view(ws, user)                       # a department approver's report holds what their department may see
    try:
        pdf, report_id = report.build(ws, r, plot_id, user)
    except KeyError:
        raise HTTPException(404, "Unknown plot")
    import hashlib
    audit.append_one(config.ws_paths(ws)["audit"], "report.generated", plot_id,
                     {"report_id": report_id, "sha256": hashlib.sha256(pdf).hexdigest(), "bytes": len(pdf)},
                     actor=user["username"])
    safe = plot_id.replace("/", "-")
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="LandConnect_{ws}_plot_{safe}_{report_id}.pdf"'})


# ----------------------------------------------------------------------------- workspaces & config
@app.get("/api/workspaces", tags=["workspaces"], summary="Areas available for harmonisation")
def workspaces(_=Depends(VIEWER)):
    out = []
    for ws in config.list_workspaces():
        p = config.ws_paths(ws)
        meta = geo.read_json(p["meta"]) if p["meta"].exists() else {}
        out.append({"id": ws, "title": meta.get("title", ws), "kind": meta.get("kind", "custom"),
                    "description": meta.get("description"), "sources": meta.get("sources", []),
                    "has_result": p["result"].exists()})
    return {"default": config.DEFAULT_WS, "workspaces": out}


_google = {"session": None, "expiry": 0}


@app.get("/api/config", tags=["workspaces"], summary="Front-end configuration (basemaps, query engine)")
def app_config(_=Depends(VIEWER)):
    cfg = {"query_engine": "claude" if query.claude_available() else "rules", "google_tiles": None}
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if key:
        # Official Google Map Tiles API (2D satellite tiles) - display only, per Google's terms
        if not _google["session"] or _google["expiry"] < time.time() + 3600:
            try:
                req = urllib.request.Request(
                    f"https://tile.googleapis.com/v1/createSession?key={key}",
                    data=json.dumps({"mapType": "satellite", "language": "en-IN", "region": "IN"}).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    s = json.loads(r.read())
                _google.update(session=s["session"], expiry=int(s.get("expiry", time.time() + 86400)))
            except Exception as e:
                cfg["google_error"] = str(e)
        if _google["session"]:
            cfg["google_tiles"] = (f"https://tile.googleapis.com/v1/2dtiles/{{z}}/{{x}}/{{y}}"
                                   f"?session={_google['session']}&key={key}")
    return cfg


# ----------------------------------------------------------------------------- pipeline control
def _run_pipeline(ws, username):
    run = _runs[ws]

    def progress(key, st, info):
        with _lock:
            run["steps"][key] = {"state": st, **{k: v for k, v in info.items() if k != "trace"}}

    try:
        res = pipeline.run(ws, progress=progress, user=username)
        with _lock:
            _results[ws] = res
            run["state"] = "done"
    except Exception as e:  # surfaced to the UI; the run row in the database holds the detail
        with _lock:
            run["state"], run["error"] = "error", f"{type(e).__name__}: {e}"


@app.post("/api/run", tags=["pipeline"], summary="Run the integration pipeline on an area")
def start_run(ws: str | None = None, user=Depends(ADMIN)):
    ws = _ws(ws)
    with _lock:
        if _runs.get(ws, {}).get("state") == "running":
            raise HTTPException(409, "Pipeline already running")
        _runs[ws] = {"state": "running", "steps": {}, "error": None, "started_by": user["username"]}
    threading.Thread(target=_run_pipeline, args=(ws, user["username"]), daemon=True).start()
    return {"started": True, "ws": ws}


@app.get("/api/status", tags=["pipeline"], summary="Progress of the current / last run")
def status(ws: str | None = None, _=Depends(VIEWER)):
    ws = _ws(ws)
    return {**_runs.get(ws, {"state": "idle", "steps": {}, "error": None}),
            "steps_meta": [{"key": k, "title": t, "description": d} for k, t, d in pipeline.STEPS],
            "has_result": _result(ws, required=False) is not None}


@app.get("/api/runs", tags=["pipeline"], summary="Run history (who ran what, when, outcome)")
def run_history(ws: str | None = None, limit: int = 20, _=Depends(VIEWER)):
    return db.runs(_ws(ws), min(limit, 200))


@app.get("/api/result", tags=["pipeline"], summary="Full published result (layers, reports, conflicts)")
def result(ws: str | None = None, user=Depends(VIEWER)):
    ws = _ws(ws)
    r = _result(ws)
    from . import publish          # scores change when other areas or blind tests run: always the latest
    r["benchmarks"], r["blind_tests"] = publish.benchmarks(ws), publish.blind_tests()
    out = _view(ws, user)
    out["benchmarks"], out["blind_tests"] = r["benchmarks"], r["blind_tests"]
    out["names_masked"] = privacy_masked(user)
    return out


@app.post("/api/reset-demo", tags=["pipeline"], summary="Regenerate the synthetic demo ward and run")
def reset_demo(seed: int = 47, user=Depends(ADMIN)):
    ws = config.DEFAULT_WS
    if _runs.get(ws, {}).get("state") == "running":
        raise HTTPException(409, "Pipeline running")
    synth.generate(seed=seed)
    db.clear_decisions(ws)
    audit.append_one(config.ws_paths(ws)["audit"], "admin.reset_demo", None, {"seed": seed}, actor=user["username"])
    _results.pop(ws, None)
    return start_run(ws, user)


@app.post("/api/upload", tags=["pipeline"], summary="Upload department datasets into an area")
async def upload(ws: str | None = None, files: list[UploadFile] = File(...), user=Depends(ADMIN)):
    ws = _ws(ws)
    raw = config.ws_paths(ws)["raw"]
    out = []
    for f in files:
        name = Path(f.filename or "").name
        if not SAFE_NAME.match(name) or Path(name).suffix.lower() not in UPLOAD_EXT:
            raise HTTPException(400, f"Not accepted: {name!r}. Allowed: {', '.join(sorted(UPLOAD_EXT))}")
        dest = raw / name
        size = 0
        with open(dest, "wb") as fh:
            while chunk := await f.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD_MB << 20:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(413, f"{name} is larger than {MAX_UPLOAD_MB} MB")
                fh.write(chunk)
        ds = ingest.load_dataset(dest) if dest.suffix.lower() not in ingest.SIDE_CAR else None
        out.append({"file": name, "bytes": size, "role": ds.role if ds else "sidecar",
                    "role_label": ingest.ROLE_LABELS.get(ds.role, ds.role) if ds else "Sidecar file",
                    "features": len(ds.records) if ds else 0, "sha256": ds.sha256 if ds else None})
    audit.append_one(config.ws_paths(ws)["audit"], "admin.upload", None, {"files": out}, actor=user["username"])
    return {"saved": out}


# ----------------------------------------------------------------------------- review workflow
class Decision(BaseModel):
    conflict_id: str
    choice: int | None = None
    note: str = ""


@app.post("/api/decisions", tags=["review"], summary="Record a reviewer decision on a conflict")
def decide(d: Decision, ws: str | None = None, user=Depends(REVIEWER)):
    ws = _ws(ws)
    r = _result(ws)
    _check_routed(r, d.conflict_id, user)
    with _lock:
        try:
            c, props = review.decide(r, ws, d.conflict_id, d.choice, d.note[:500], user["username"])
        except KeyError:
            raise HTTPException(404, "Unknown conflict")
        except ValueError as e:
            raise HTTPException(400, str(e))
        _save(ws)
    return {"conflict": c, "parcel": props, "summary": r["summary"], "audit": r.get("audit")}


class SchemaConfirm(BaseModel):
    dataset: str
    column: str
    field: str | None = None


@app.post("/api/schema/confirm", tags=["pipeline"],
          summary="Confirm or correct how a column was mapped (applies from the next pipeline run)")
def schema_confirm(body: SchemaConfirm, ws: str | None = None, user=Depends(REVIEWER)):
    from . import schema_mapper
    ws = _ws(ws)
    r = _result(ws)
    ds = next((d for d in r["datasets"] if d["name"] == body.dataset), None)
    if ds is None or body.column not in (ds.get("columns") or [m["column"] for m in (ds.get("schema") or {}).get("mappings", [])]
                                         + (ds.get("schema") or {}).get("unmapped", [])):
        raise HTTPException(404, "Unknown dataset or column")
    if body.field is not None and body.field not in schema_mapper.FIELD_LABELS:
        raise HTTPException(400, "Unknown field")
    path = pipeline.schema_overrides_path(ws)
    ov = geo.read_json(path) if path.exists() else {}
    ov.setdefault(body.dataset, {})[body.column] = {"field": body.field, "by": user["username"], "at": db.now()}
    geo.write_json(path, ov, indent=2)
    audit.append_one(config.ws_paths(ws)["audit"], "schema.confirmed", body.dataset,
                     {"column": body.column, "field": body.field}, actor=user["username"])
    with _lock:                                        # show it at once; the records are re-read on the next run
        sch = ds.get("schema") or {"mappings": [], "unmapped": []}
        sch["mappings"] = [m for m in sch["mappings"] if m["column"] != body.column and m["field"] != body.field]
        if body.field:
            sch["mappings"].append({"column": body.column, "field": body.field, "label": schema_mapper.FIELD_LABELS[body.field],
                                    "confidence": 100.0, "evidence": f"confirmed by {user['username']} (applies on the next run)",
                                    "confirmed_by": user["username"], "needs_confirmation": False})
        elif body.column not in sch["unmapped"]:
            sch["unmapped"].append(body.column)
        ds["schema"] = sch
        _save(ws)
    return {"dataset": ds, "note": "Saved. Run the pipeline to re-read the records with this mapping."}


class LearnedRule(BaseModel):
    type: str
    source: str
    enabled: bool = True


@app.get("/api/learning", tags=["review"], summary="Patterns in reviewer decisions and the rules learned from them")
def learning_stats(ws: str | None = None, _=Depends(REVIEWER)):
    from . import learning
    ws = _ws(ws)
    return learning.stats(ws, _result(ws))


@app.post("/api/learning/rule", tags=["review"], summary="Enable or disable a learned auto-resolution rule (admin)")
def learning_rule(body: LearnedRule, ws: str | None = None, user=Depends(ADMIN)):
    from . import learning
    ws = _ws(ws)
    st = {g["type"]: g for g in learning.stats(ws, _result(ws))["patterns"]}
    g = st.get(body.type)
    if body.enabled and (g is None or not g["eligible"] or g["top_source"] != body.source):
        raise HTTPException(400, "Not enough consistent reviewer decisions for this rule yet")
    based = {"decisions": g["decisions"], "agree": g["choices"].get(body.source)} if g else None
    rules = learning.set_rule(ws, body.type, body.source, body.enabled, user["username"], based)
    audit.append_one(config.ws_paths(ws)["audit"], "learning.rule_" + ("enabled" if body.enabled else "disabled"),
                     body.type, {"source": body.source, "based_on": based}, actor=user["username"])
    return {"rules": rules, "note": "Takes effect on the next pipeline run."}


class BulkDecision(BaseModel):
    conflict_ids: list[str]
    choice: int | None = None
    note: str = ""


@app.post("/api/decisions/bulk", tags=["review"],
          summary="Decide many open conflicts at once (same answer for all; each one is audited separately)")
def decide_bulk(d: BulkDecision, ws: str | None = None, user=Depends(REVIEWER)):
    ws = _ws(ws)
    r = _result(ws)
    note = " ".join(d.note.split())
    if len(note) < 5:
        raise HTTPException(400, "Give a note for a bulk decision (it is written to every item's audit entry)")
    if not 0 < len(d.conflict_ids) <= 200:
        raise HTTPException(400, "Choose between 1 and 200 conflicts")
    by_id = {c["id"]: c for c in r["conflicts"]}
    done, skipped, parcels = [], [], {}
    with _lock:
        for cid in dict.fromkeys(d.conflict_ids):
            c = by_id.get(cid)
            if c is None:
                skipped.append({"id": cid, "reason": "unknown"})
                continue
            if user.get("department", "all") != "all" and user["department"] not in c.get("departments", []):
                skipped.append({"id": cid, "reason": "not routed to your department"})
                continue
            if c["status"] != "open":
                skipped.append({"id": cid, "reason": f"already {c['status']}"})
                continue
            if d.choice is not None and not 0 <= d.choice < len(c.get("versions") or []):
                skipped.append({"id": cid, "reason": "that version does not exist here"})
                continue
            nc, props = review.decide(r, ws, cid, d.choice, f"[bulk] {note}"[:500], user["username"])
            done.append(nc)
            if props:
                parcels[props["plot_id"]] = props
        if done:
            _save(ws)
    return {"decided": done, "skipped": skipped, "parcels": list(parcels.values()), "summary": r["summary"],
            "audit": r.get("audit")}


@app.post("/api/decisions/{conflict_id:path}/reopen", tags=["review"], summary="Undo a reviewer decision")
def reopen(conflict_id: str, ws: str | None = None, user=Depends(REVIEWER)):
    ws = _ws(ws)
    r = _result(ws)
    _check_routed(r, conflict_id, user)
    with _lock:
        try:
            c, props = review.reopen(r, ws, conflict_id, user["username"])
        except KeyError:
            raise HTTPException(404, "No reviewer decision to undo")
        _save(ws)
    return {"conflict": c, "parcel": props, "summary": r["summary"], "audit": r.get("audit")}


def _check_routed(result, conflict_id, user):
    c = next((c for c in result["conflicts"] if c["id"] == conflict_id), None)
    if c is not None and user.get("department", "all") != "all" and user["department"] not in c.get("departments", []):
        raise HTTPException(403, "This conflict is not routed to your department")


# ----------------------------------------------------------------------------- departments & record updates
@app.get("/api/departments", tags=["departments"],
         summary="Government bodies, the fields each is the authority for, and their open work in an area")
def list_departments(ws: str | None = None, _=Depends(VIEWER)):
    ws = _ws(ws)
    r = _result(ws, required=False)
    out = departments.registry()
    stats = (r or {}).get("dept_link_stats") or {}
    for dept, meta in out["departments"].items():
        box = records.inbox(ws, dept, r)
        meta.update(records=stats.get(dept), open_conflicts=box["open_conflicts"],
                    pending_requests=box["pending_requests"], unread=len(box["notifications"]))
    return out


@app.get("/api/change-requests", tags=["departments"], summary="Record change requests (newest first)")
def change_requests(ws: str | None = None, status: str | None = None, department: str | None = None,
                    plot_id: str | None = None, user=Depends(VIEWER)):
    ws = _ws(ws)
    return _mask_any(ws, user, records.list_requests(ws, status, department, plot_id))


@app.post("/api/change-requests", tags=["departments"],
          summary="Propose a change to a field your department is the authority for (maker)")
async def propose(ws: str | None = None, plot_id: str = Form(...), field: str = Form(...), new_value: str = Form(...),
                  reason: str = Form(...), record_id: str | None = Form(None), evidence: UploadFile | None = File(None),
                  user=Depends(OFFICER)):
    ws = _ws(ws)
    r = _result(ws)
    ev = None
    try:
        if evidence is not None and evidence.filename:
            ev = records.save_evidence(ws, await evidence.read(records.MAX_EVIDENCE_MB * 1024 * 1024 + 1),
                                       evidence.filename)
        with _lock:
            cr = records.create(ws, r, user, plot_id, field, new_value, reason, evidence=ev,
                                record_id=record_id or None)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except (records.RequestError, ValueError) as e:
        raise HTTPException(400, str(e))
    return cr


class Verdict(BaseModel):
    note: str = ""


def _decide_cr(ws, cr_id, user, approve, note):
    ws = _ws(ws)
    r = _result(ws)
    with _lock:
        try:
            cr, props = records.decide(ws, r, cr_id, user, approve, note)
        except KeyError:
            raise HTTPException(404, "Unknown request")
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except records.RequestError as e:
            raise HTTPException(409 if ("out of date" in str(e) or "already" in str(e)) else 400, str(e))
        if props is not None:
            _save(ws)
    return {"request": cr, "parcel": props, "summary": r["summary"],
            "conflicts": [c for c in r["conflicts"] if c["plot_id"] == cr["plot_id"]]}


class BatchVerdict(BaseModel):
    ids: list[int]
    approve: bool
    note: str = ""


@app.post("/api/change-requests/batch", tags=["departments"],
          summary="Approve or reject several requests at once (each is still checked and audited on its own)")
def batch_requests(body: BatchVerdict, ws: str | None = None, user=Depends(REVIEWER)):
    ws = _ws(ws)
    r = _result(ws)
    if not 0 < len(body.ids) <= 100:
        raise HTTPException(400, "Choose between 1 and 100 requests")
    note = " ".join(body.note.split())
    done, skipped, changed = [], [], False
    with _lock:
        for cr_id in dict.fromkeys(body.ids):
            try:
                cr, props = records.decide(ws, r, cr_id, user, body.approve, f"[batch] {note}".strip())
                done.append(cr)
                changed |= props is not None
            except KeyError:
                skipped.append({"id": cr_id, "reason": "unknown request"})
            except (PermissionError, records.RequestError) as e:
                skipped.append({"id": cr_id, "reason": str(e)})
        if changed:
            _save(ws)
    return {"decided": done, "skipped": skipped, "summary": r["summary"]}


@app.post("/api/change-requests/{cr_id}/approve", tags=["departments"], summary="Approve a request (checker)")
def approve_request(cr_id: int, body: Verdict, ws: str | None = None, user=Depends(REVIEWER)):
    return _decide_cr(ws, cr_id, user, True, body.note)


@app.post("/api/change-requests/{cr_id}/reject", tags=["departments"], summary="Reject a request with a reason")
def reject_request(cr_id: int, body: Verdict, ws: str | None = None, user=Depends(REVIEWER)):
    return _decide_cr(ws, cr_id, user, False, body.note)


@app.post("/api/change-requests/{cr_id}/withdraw", tags=["departments"], summary="Withdraw your own pending request")
def withdraw_request(cr_id: int, ws: str | None = None, user=Depends(OFFICER)):
    try:
        return records.withdraw(_ws(ws), cr_id, user)
    except KeyError:
        raise HTTPException(404, "Unknown request")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except records.RequestError as e:
        raise HTTPException(409, str(e))


@app.get("/api/evidence/{sha}", tags=["departments"], summary="Supporting document of a change request")
def evidence(sha: str, ws: str | None = None, _=Depends(VIEWER)):
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise HTTPException(404)
    path, media = records.evidence_path(_ws(ws), sha)
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type=media)


@app.get("/api/inbox", tags=["departments"], summary="Your department's notifications and open work")
def inbox(ws: str | None = None, department: str | None = None, user=Depends(VIEWER)):
    ws = _ws(ws)
    dept = department or user.get("department", "all")
    if user.get("department", "all") not in ("all", dept):
        raise HTTPException(403, "Not your department")
    return records.inbox(ws, dept, _result(ws, required=False))


@app.post("/api/inbox/{note_id}/ack", tags=["departments"], summary="Mark a notification as handled")
def ack(note_id: int, ws: str | None = None, user=Depends(OFFICER)):
    try:
        records.ack(_ws(ws), note_id, user)
    except KeyError:
        raise HTTPException(404, "Unknown notification")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"ok": True}


# ----------------------------------------------------------------------------- audit trail & statistics
@app.get("/api/audit", tags=["audit"], summary="Audit trail entries (newest first)")
def audit_entries(ws: str | None = None, subject: str | None = None, kind: str | None = None, limit: int = 200,
                  user=Depends(VIEWER)):
    ws = _ws(ws)
    path = config.ws_paths(ws)["audit"]
    return {"verify": audit.verify(path),
            "entries": _mask_any(ws, user, audit.read(path, subject=subject, kind=kind, limit=min(limit, 1000)))}


@app.get("/api/audit/verify", tags=["audit"], summary="Re-compute the hash chain")
def audit_verify(ws: str | None = None, _=Depends(VIEWER)):
    return audit.verify(config.ws_paths(_ws(ws))["audit"])


@app.get("/api/stats", tags=["exchange"], summary="Parcel and conflict counts from the database")
def stats(ws: str | None = None, _=Depends(VIEWER)):
    return db.stats(_ws(ws))


@app.get("/api/search/parcels", tags=["exchange"], summary="Search the parcel index")
def search_parcels(ws: str | None = None, status: str | None = None, owner: str | None = None,
                   plot: str | None = None, min_score: float | None = None, max_score: float | None = None,
                   ulpin: str | None = None, limit: int = 100, offset: int = 0, user=Depends(VIEWER)):
    from . import privacy
    ws = _ws(ws)
    if owner and privacy.should_mask(user):
        raise HTTPException(403, "Searching by owner name needs an officer or approver account")
    sql, params = "SELECT plot_id, status, score, owner, area_sqm, land_use, khata, municipal_pid, open_conflicts " \
                  "FROM parcels WHERE ws = ?", [ws]
    if ulpin:
        sql += " AND json_extract(properties, '$.ulpin') = ?"
        params.append(ulpin.strip().upper())
    if status:
        sql += " AND status = ?"
        params.append(status)
    if owner:
        sql += " AND owner LIKE ?"
        params.append(f"%{owner}%")
    if plot:
        sql += " AND (plot_id = ? OR plot_id LIKE ?)"
        params += [plot, f"{plot}/%"]
    if min_score is not None:
        sql += " AND score >= ?"
        params.append(min_score)
    if max_score is not None:
        sql += " AND score <= ?"
        params.append(max_score)
    total = db.query(sql.replace("SELECT plot_id, status, score, owner, area_sqm, land_use, khata, municipal_pid, "
                                 "open_conflicts", "SELECT COUNT(*) n"), params, one=True)["n"]
    rows = db.query(sql + " ORDER BY plot_id LIMIT ? OFFSET ?", params + [min(limit, 1000), offset])
    if privacy.should_mask(user):
        for r in rows:
            r["owner"] = privacy.mask_name(r.get("owner"))
    return {"total": total, "items": rows}


# ----------------------------------------------------------------------------- natural-language query
class Query(BaseModel):
    q: str
    engine: str | None = None


@app.post("/api/query", tags=["query"], summary="Ask a question in plain language")
def ask(body: Query, ws: str | None = None, user=Depends(VIEWER)):
    ws = _ws(ws)
    r = _view(ws, user)
    f, engine = query.parse(body.q[:300], r, body.engine)
    if query.is_empty(f) and f.get("entity") == "parcels":
        return {"engine": engine, "filter": {k: v for k, v in f.items() if not k.startswith("_")},
                "interpreted": ["no condition recognised"], "total": 0, "matches": [], "summary": {"count": 0}}
    matches, total, notes, summary = query.run(r, f)
    return {"engine": engine, "filter": {k: v for k, v in f.items() if not k.startswith("_")},
            "interpreted": query.describe(f) + notes + ([f["_note"]] if f.get("_note") else []),
            "total": total, "matches": matches, "summary": summary}


@app.get("/api/suggest", tags=["query"], summary="Instant search suggestions: plots, records, owners, places, questions")
def suggest(q: str = "", ws: str | None = None, user=Depends(VIEWER)):
    from . import search
    ws = _ws(ws)
    r = _result(ws, required=False)
    if r is None:
        return {"q": q, "groups": []}
    return search.suggest(ws, r, q[:120], user)


@app.get("/api/geocode", tags=["query"],
         summary="Find a place in India by name (OpenStreetMap Nominatim; only the typed name is sent)")
def geocode(q: str, _=Depends(VIEWER)):
    from . import search
    return {"q": q, "places": search.geocode(q[:120]), "source": "OpenStreetMap Nominatim"}


# ----------------------------------------------------------------------------- exchange / export
@app.get("/api/parcels/{plot_id:path}", tags=["exchange"], summary="One harmonised parcel with linked records")
def parcel(plot_id: str, ws: str | None = None, user=Depends(VIEWER)):
    ws = _ws(ws)
    r = _view(ws, user)
    f = next((f for f in r["layers"]["parcels"]["features"] if f["properties"]["plot_id"] == plot_id), None)
    if f is None:
        raise HTTPException(404, "Unknown plot")
    return {**f, "conflicts": [c for c in r["conflicts"] if c["plot_id"] == plot_id]}


@app.get("/api/export/ladm.gpkg", tags=["exchange"], summary="ISO 19152 LADM GeoPackage of the harmonised parcels")
def export_ladm(ws: str | None = None, user=Depends(VIEWER)):
    ws = _ws(ws)
    return Response(export.export_ladm(_view(ws, user)), media_type="application/geopackage+sqlite3",
                    headers={"Content-Disposition": f'attachment; filename="landconnect_{ws}_ladm.gpkg"'})


@app.get("/api/export/{layer}.{fmt}", tags=["exchange"],
         summary="Download a layer (geojson, csv, kml, gpkg, shp) in EPSG:4326")
def export_layer(layer: str, fmt: str, ws: str | None = None, user=Depends(VIEWER)):
    ws = _ws(ws)
    r = _view(ws, user)
    if layer not in r["layers"] or fmt not in export.FORMATS:
        raise HTTPException(404, "Unknown layer or format")
    media, ext = export.FORMATS[fmt]
    return Response(export.export_layer(r, layer, fmt), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="landconnect_{ws}_{layer}.{ext}"'})


# ----------------------------------------------------------------------------- state-wide cadastral maps (vector tiles)
@app.get("/api/statewide", tags=["exchange"],
         summary="State-wide cadastral maps available as vector tiles (real data from state portals, via datameet)")
def statewide_list(_=Depends(VIEWER)):
    from . import statewide
    return statewide.catalog()


@app.get("/statewide/{state}/{name}/overview.png", tags=["exchange"], summary="Density overview of a state-wide map")
def statewide_overview(state: str, name: str, _=Depends(VIEWER)):
    from . import statewide
    try:
        path = statewide.path_of(state, name)
    except FileNotFoundError:
        raise HTTPException(404, "Unknown map")
    statewide.overview(path)
    return FileResponse(path.with_name(path.name + ".overview.png"), media_type="image/png",
                        headers={"Cache-Control": "private, max-age=86400"})


@app.get("/statewide/{state}/{name}", tags=["exchange"], summary="PMTiles file (read in HTTP ranges by the map)")
def statewide_file(state: str, name: str, _=Depends(VIEWER)):
    from . import statewide
    try:
        path = statewide.path_of(state, name)
    except FileNotFoundError:
        raise HTTPException(404, "Unknown map")
    return FileResponse(path, media_type="application/octet-stream", headers={"Cache-Control": "private, max-age=86400"})


# ----------------------------------------------------------------------------- OGC API - Features (subset)
def _base(request: Request):
    return str(request.base_url).rstrip("/")


def _collection(request, cid, ws):
    return {"id": cid, "title": LAYER_TITLES.get(cid, cid), "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
            "links": [{"rel": "items", "type": "application/geo+json",
                       "href": f"{_base(request)}/ogc/collections/{cid}/items?ws={ws}"}]}


@app.get("/ogc", tags=["OGC API - Features"], summary="Landing page")
def ogc_landing(request: Request, ws: str | None = None, _=Depends(VIEWER)):
    b, ws = _base(request), _ws(ws)
    return {"title": f"Land Connect — OGC API - Features ({ws})", "links": [
        {"rel": "conformance", "href": f"{b}/ogc/conformance"}, {"rel": "data", "href": f"{b}/ogc/collections?ws={ws}"},
        {"rel": "service-doc", "href": f"{b}/docs"}]}


@app.get("/ogc/conformance", tags=["OGC API - Features"])
def ogc_conformance():
    return {"conformsTo": ["http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
                           "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson"]}


@app.get("/ogc/collections", tags=["OGC API - Features"])
def ogc_collections(request: Request, ws: str | None = None, _=Depends(VIEWER)):
    ws = _ws(ws)
    r = _result(ws, required=False) or {"layers": {}}
    return {"collections": [_collection(request, c, ws) for c in r["layers"]]}


@app.get("/ogc/collections/{cid}", tags=["OGC API - Features"])
def ogc_collection(cid: str, request: Request, ws: str | None = None, _=Depends(VIEWER)):
    ws = _ws(ws)
    r = _result(ws)
    if cid not in r["layers"]:
        raise HTTPException(404, "Unknown collection")
    return _collection(request, cid, ws)


def _bbox_hit(geom, bbox):
    coords = []

    def walk(c):
        if isinstance(c[0], (int, float)):
            coords.append(c)
        else:
            for x in c:
                walk(x)
    walk(geom["coordinates"])
    xs, ys = [c[0] for c in coords], [c[1] for c in coords]
    return not (max(xs) < bbox[0] or min(xs) > bbox[2] or max(ys) < bbox[1] or min(ys) > bbox[3])


@app.get("/ogc/collections/{cid}/items", tags=["OGC API - Features"])
def ogc_items(cid: str, ws: str | None = None, limit: int = 100, offset: int = 0, bbox: str | None = None,
              status: str | None = None, user=Depends(VIEWER)):
    ws = _ws(ws)
    r = _view(ws, user)
    if cid not in r["layers"]:
        raise HTTPException(404, "Unknown collection")
    feats = r["layers"][cid]["features"]
    if bbox:
        try:
            bb = [float(v) for v in bbox.split(",")]
            assert len(bb) == 4
        except (ValueError, AssertionError):
            raise HTTPException(400, "bbox must be minLon,minLat,maxLon,maxLat")
        feats = [f for f in feats if _bbox_hit(f["geometry"], bb)]
    if status:
        feats = [f for f in feats if f["properties"].get("status") == status]
    page = feats[offset:offset + min(limit, 10_000)]
    return JSONResponse({"type": "FeatureCollection", "numberMatched": len(feats), "numberReturned": len(page),
                         "features": page}, media_type="application/geo+json")


# ----------------------------------------------------------------------------- static
@app.get("/output/{ws}/web/{name}", include_in_schema=False)
def output_image(ws: str, name: str, _=Depends(VIEWER)):
    ws = _ws(ws)
    if not SAFE_NAME.match(name):
        raise HTTPException(404)
    path = config.ws_paths(ws)["web"] / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # served from the root so it can cache the whole app for offline use
    return FileResponse(config.WEB_DIR / "sw.js", media_type="application/javascript")


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(config.WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.WEB_DIR / "index.html")
