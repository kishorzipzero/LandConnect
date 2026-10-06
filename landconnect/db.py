"""SQLite system of record: users, sessions, API keys, pipeline runs, reviewer decisions, and a
queryable index of harmonised parcels and conflicts.

The map payload (layers) is still published as output/<ws>/result.json because the browser loads it
whole; everything that must be durable, searchable or attributable to a person lives here.
SQLite needs no server; the same schema maps one-to-one onto PostgreSQL/PostGIS for production.
"""
import datetime as dt
import json
import sqlite3
import threading

from . import config

DB_PATH = config.OUTPUT_DIR / "landconnect.db"
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username    TEXT PRIMARY KEY,
    full_name   TEXT,
    role        TEXT NOT NULL CHECK (role IN ('viewer', 'officer', 'reviewer', 'admin')),
    department  TEXT NOT NULL DEFAULT 'all',
    pw_hash     TEXT NOT NULL,
    pw_salt     TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    username    TEXT NOT NULL REFERENCES users(username),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash    TEXT PRIMARY KEY,
    prefix      TEXT NOT NULL,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('viewer', 'officer', 'reviewer', 'admin')),
    department  TEXT NOT NULL DEFAULT 'all',
    created_by  TEXT,
    created_at  TEXT NOT NULL,
    last_used   TEXT,
    active      INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    ws          TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_by  TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    seconds     REAL,
    summary     TEXT,
    error       TEXT,
    audit_head  TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    ws          TEXT NOT NULL,
    conflict_id TEXT NOT NULL,
    choice      INTEGER,
    note        TEXT,
    decided_by  TEXT,
    decided_at  TEXT NOT NULL,
    PRIMARY KEY (ws, conflict_id)
);
CREATE TABLE IF NOT EXISTS parcels (
    ws             TEXT NOT NULL,
    plot_id        TEXT NOT NULL,
    status         TEXT,
    score          REAL,
    owner          TEXT,
    area_sqm       REAL,
    land_use       TEXT,
    khata          TEXT,
    municipal_pid  TEXT,
    open_conflicts INTEGER,
    geometry       TEXT,
    properties     TEXT,
    run_id         TEXT,
    PRIMARY KEY (ws, plot_id)
);
CREATE TABLE IF NOT EXISTS conflicts (
    ws          TEXT NOT NULL,
    conflict_id TEXT NOT NULL,
    type        TEXT,
    severity    TEXT,
    status      TEXT,
    plot_id     TEXT,
    detail      TEXT,
    resolution  TEXT,
    resolved_by TEXT,
    run_id      TEXT,
    PRIMARY KEY (ws, conflict_id)
);
CREATE TABLE IF NOT EXISTS change_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ws            TEXT NOT NULL,
    plot_id       TEXT NOT NULL,
    department    TEXT NOT NULL,
    field         TEXT NOT NULL,
    record_id     TEXT,
    old_value     TEXT,
    new_value     TEXT NOT NULL,
    reason        TEXT NOT NULL,
    evidence_sha  TEXT,
    evidence_name TEXT,
    source_ref    TEXT,
    status        TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'stale', 'withdrawn')),
    proposed_by   TEXT NOT NULL,
    proposed_at   TEXT NOT NULL,
    decided_by    TEXT,
    decided_at    TEXT,
    decision_note TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ws           TEXT NOT NULL,
    department   TEXT NOT NULL,
    plot_id      TEXT,
    kind         TEXT NOT NULL,
    message      TEXT NOT NULL,
    ref          TEXT,
    created_at   TEXT NOT NULL,
    acked_by     TEXT,
    acked_at     TEXT
);
CREATE TABLE IF NOT EXISTS dept_access (
    viewer       TEXT NOT NULL,
    target       TEXT NOT NULL,
    level        TEXT NOT NULL CHECK (level IN ('full', 'summary', 'none')),
    updated_by   TEXT,
    updated_at   TEXT,
    PRIMARY KEY (viewer, target)
);
CREATE INDEX IF NOT EXISTS ix_cr ON change_requests (ws, status, department);
CREATE INDEX IF NOT EXISTS ix_cr_plot ON change_requests (ws, plot_id);
CREATE INDEX IF NOT EXISTS ix_notes ON notifications (ws, department, acked_at);
CREATE INDEX IF NOT EXISTS ix_runs_ws ON runs (ws, started_at);
CREATE INDEX IF NOT EXISTS ix_parcels_status ON parcels (ws, status);
CREATE INDEX IF NOT EXISTS ix_conflicts_status ON conflicts (ws, status, severity);
CREATE INDEX IF NOT EXISTS ix_conflicts_plot ON conflicts (ws, plot_id);
"""


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def connect(path=None):
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def _migrate(con):
    _migrate_roles(con)
    cols = [r[1] for r in con.execute("PRAGMA table_info(users)")]
    for col, ddl in (("must_change", "INTEGER NOT NULL DEFAULT 0"), ("created_by", "TEXT"),
                     ("role_granted_by", "TEXT"), ("role_granted_at", "TEXT"),
                     # profile: contact details, preferences, sign-in history
                     ("designation", "TEXT"), ("employee_id", "TEXT"), ("office", "TEXT"), ("email", "TEXT"),
                     ("phone", "TEXT"), ("language", "TEXT"), ("prefs", "TEXT"), ("password_changed_at", "TEXT"),
                     ("last_login_at", "TEXT"), ("last_login_ip", "TEXT"), ("previous_login_at", "TEXT")):
        if col not in cols:
            with con:
                con.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
    scols = [r[1] for r in con.execute("PRAGMA table_info(sessions)")]
    for col in ("ip", "user_agent", "last_seen_at"):
        if col not in scols:
            with con:
                con.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT")


def _migrate_roles(con):
    """Older databases: add the department column and the officer role (SQLite cannot alter a CHECK,
    so the table is rebuilt; foreign keys are paused so sessions keep pointing at 'users')."""
    for table in ("users", "api_keys"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        if "department" in cols:
            continue
        ddl = next(stmt for stmt in SCHEMA.split(";") if f"CREATE TABLE IF NOT EXISTS {table} (" in stmt)
        con.execute("PRAGMA foreign_keys=OFF")
        with con:
            con.execute(ddl.replace(f"IF NOT EXISTS {table} (", f"{table}_new ("))
            keep = ", ".join(cols)
            con.execute(f"INSERT INTO {table}_new ({keep}) SELECT {keep} FROM {table}")
            con.execute(f"DROP TABLE {table}")
            con.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        con.execute("PRAGMA foreign_keys=ON")


def query(sql, params=(), one=False):
    with _lock:
        con = connect()
        try:
            rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        finally:
            con.close()
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    with _lock:
        con = connect()
        try:
            with con:
                cur = con.execute(sql, params)
                return cur.rowcount
        finally:
            con.close()


def executemany(sql, rows):
    with _lock:
        con = connect()
        try:
            with con:
                con.executemany(sql, rows)
        finally:
            con.close()


# ----------------------------------------------------------------------------- runs
def run_started(run_id, ws, user=None):
    execute("INSERT OR REPLACE INTO runs (run_id, ws, status, started_by, started_at) VALUES (?, ?, 'running', ?, ?)",
            (run_id, ws, user, now()))


def run_finished(run_id, status, seconds=None, summary=None, error=None, audit_head=None):
    execute("UPDATE runs SET status = ?, finished_at = ?, seconds = ?, summary = ?, error = ?, audit_head = ? "
            "WHERE run_id = ?", (status, now(), seconds, json.dumps(summary) if summary else None, error,
                                 audit_head, run_id))


def runs(ws, limit=20):
    out = query("SELECT * FROM runs WHERE ws = ? ORDER BY started_at DESC LIMIT ?", (ws, limit))
    for r in out:
        r["summary"] = json.loads(r["summary"]) if r["summary"] else None
    return out


# ----------------------------------------------------------------------------- decisions
def load_decisions(ws):
    return {r["conflict_id"]: {"choice": r["choice"], "note": r["note"] or "", "at": r["decided_at"],
                               "by": r["decided_by"]}
            for r in query("SELECT * FROM decisions WHERE ws = ?", (ws,))}


def save_decision(ws, conflict_id, choice, note, user, at):
    execute("INSERT OR REPLACE INTO decisions (ws, conflict_id, choice, note, decided_by, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (ws, conflict_id, choice, note, user, at))


def delete_decision(ws, conflict_id):
    return execute("DELETE FROM decisions WHERE ws = ? AND conflict_id = ?", (ws, conflict_id))


def clear_decisions(ws):
    execute("DELETE FROM decisions WHERE ws = ?", (ws,))


def import_legacy_decisions(ws, path):
    """One-time migration of the old decisions.json files."""
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    for cid, d in data.items():
        save_decision(ws, cid, d.get("choice"), d.get("note", ""), d.get("by", "legacy"), d.get("at") or now())
    path.rename(path.with_suffix(".json.migrated"))
    return len(data)


# ----------------------------------------------------------------------------- parcel / conflict index
def index_result(ws, result, run_id=None):
    parcels = []
    for f in result["layers"]["parcels"]["features"]:
        p = f["properties"]
        parcels.append((ws, p["plot_id"], p["status"], p["score"], p.get("resolved_owner"), p.get("area_sqm"),
                        p.get("land_use"), p.get("khata"), p.get("municipal_pid"), p.get("open_conflicts"),
                        json.dumps(f["geometry"]), json.dumps(p, ensure_ascii=False), run_id))
    conflicts, seen = [], {}
    for c in result["conflicts"]:                          # results saved by older versions may repeat an id
        n = seen[c["id"]] = seen.get(c["id"], 0) + 1
        cid = c["id"] if n == 1 else f"{c['id']}#{n}"
        conflicts.append((ws, cid, c["type"], c["severity"], c["status"], c["plot_id"], c["detail"], c.get("resolution"),
                          c.get("resolved_by"), run_id))
    with _lock:
        con = connect()
        try:
            with con:
                con.execute("DELETE FROM parcels WHERE ws = ?", (ws,))
                con.execute("DELETE FROM conflicts WHERE ws = ?", (ws,))
                con.executemany("INSERT INTO parcels VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", parcels)
                con.executemany("INSERT INTO conflicts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", conflicts)
        finally:
            con.close()


def update_indexed(ws, conflict, parcel_props):
    """Keep the index in step with a reviewer decision."""
    execute("UPDATE conflicts SET status = ?, resolution = ?, resolved_by = ? WHERE ws = ? AND conflict_id = ?",
            (conflict["status"], conflict.get("resolution"), conflict.get("resolved_by"), ws, conflict["id"]))
    if parcel_props:
        execute("UPDATE parcels SET status = ?, score = ?, owner = ?, open_conflicts = ?, properties = ? "
                "WHERE ws = ? AND plot_id = ?",
                (parcel_props["status"], parcel_props["score"], parcel_props.get("resolved_owner"),
                 parcel_props.get("open_conflicts"), json.dumps(parcel_props, ensure_ascii=False), ws,
                 parcel_props["plot_id"]))


def stats(ws):
    by_status = {r["status"]: r["n"] for r in query("SELECT status, COUNT(*) n FROM parcels WHERE ws = ? GROUP BY status", (ws,))}
    by_type = query("SELECT type, severity, status, COUNT(*) n FROM conflicts WHERE ws = ? GROUP BY type, severity, status "
                    "ORDER BY n DESC", (ws,))
    return {"parcels_by_status": by_status, "conflicts": by_type,
            "decisions": query("SELECT COUNT(*) n FROM decisions WHERE ws = ?", (ws,), one=True)["n"]}
