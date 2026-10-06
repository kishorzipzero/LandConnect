"""Blind tests: scores on errors the detection rules were not tuned on.

Two modes, reported separately from the development areas (demo, jayanagar):

1. Random batch - a new synthetic ward with a machine-drawn seed and random layout / noise parameters.
   The seed is written only to the hidden truth file, never printed, and the rules are frozen (the
   rule-set version is recorded). This tests over-fitting to one dataset. It does NOT test the generator
   itself, because the same code plants the errors.

       python -m landconnect.blind random

2. Teammate batch - the honest test. A copy of the demo data goes to data/blind_manual/. A teammate who has
   not read the rules edits records by hand (owner names, extents, land use, floors, polygons in QGIS ...)
   and logs every edit in data/blind_manual/_truth/manual_errors.csv. Scoring compares against a run on the
   untouched copy, so only conflicts that the edits caused count:
       found        = a logged edit that produced a new conflict of a matching kind on that plot
       false alarm  = a new conflict on a plot nobody edited

       python -m landconnect.blind kit      # prepare the copy and the baseline run
       (teammate edits the files and fills in manual_errors.csv)
       python -m landconnect.blind score    # run and score
"""
import csv
import datetime as dt
import random
import secrets
import shutil
import sys

from . import config, conflicts, geo, synth

ERROR_KINDS = {
    "owner_name": ["OWNER_MISMATCH", "MUTATION_PENDING"],
    "sale_not_mutated": ["MUTATION_PENDING", "OWNER_MISMATCH"],
    "extent": ["AREA_MISMATCH"],
    "boundary": ["GEOMETRY_DISAGREEMENT", "AREA_MISMATCH", "ENCROACHMENT"],
    "missing_revenue_record": ["NO_REVENUE_RECORD"],
    "missing_tax_record": ["MISSING_IN_MUNICIPAL", "SERVICE_WITHOUT_TAX"],
    "land_use": ["TAX_USE_MISMATCH"],
    "floors": ["FLOORS_BEYOND_PERMISSION"],
    "utility_line": ["UTILITY_UNDER_BUILDING"],
}
BENCH = config.OUTPUT_DIR / "benchmarks"


def _run(ws, label):
    import os
    from . import pipeline
    os.environ["LANDCONNECT_BENCH_LABEL"] = label
    try:
        return pipeline.run(ws, user="blind-test")
    finally:
        os.environ.pop("LANDCONNECT_BENCH_LABEL", None)


def _save(name, entry):
    p = BENCH / f"{name}.json"
    hist = geo.read_json(p) if p.exists() else []
    geo.write_json(p, (hist + [entry])[-10:], indent=1)


# ----------------------------------------------------------------------------- 1. random batch
def random_batch(ws="blind"):
    seed = secrets.randbits(31)
    rng = random.Random(seed)
    params = {"n_cols": rng.choice([5, 6, 7]), "n_rows": rng.choice([4, 5, 6]),
              "muni_noise": round(rng.uniform(0.3, 0.9), 2),
              "muni_shift": (round(rng.uniform(-1.2, 1.2), 2), round(rng.uniform(-1.2, 1.2), 2))}
    root = config.DATA_DIR / ws
    synth.generate(root, seed=seed, rasters=True, **params)
    tpath = root / "_truth" / "truth.json"
    truth = geo.read_json(tpath)
    truth["blind"] = {"seed": seed, "params": params}                  # hidden: never printed or published
    geo.write_json(tpath, truth)
    geo.write_json(root / "workspace.json", {
        "title": "Blind test batch (hidden seed)", "kind": "synthetic",
        "description": "A fresh synthetic ward generated with a random hidden seed and random layout and noise, "
                       "scored with frozen rules. Errors are in new places the rules were never tuned on."}, indent=2)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    result = _run(ws, f"Blind batch {stamp}")
    ev = result["evaluation"]
    entry = {"at": stamp, "rules": conflicts.RULESET, "kind": "random", "overall": ev["overall"],
             "issues": ev["issues"], "change_detection": ev.get("change_detection")}
    _save("blind_runs", entry)
    o = ev["overall"]
    print(f"Blind batch ({stamp}, rules {conflicts.RULESET}): precision {o['precision']}, recall {o['recall']}, "
          f"F1 {o['f1']} ({o['found']} found, {o['false_alarms']} false alarms, {o['missed']} missed)")
    return entry


# ----------------------------------------------------------------------------- 2. teammate batch
def kit(src="demo", ws="blind_manual"):
    root = config.DATA_DIR / ws
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(config.DATA_DIR / src / "raw", root / "raw")
    (root / "_truth").mkdir(parents=True)
    with open(root / "_truth" / "manual_errors.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["plot_id", "error_kind", "what_you_changed"])
    (root / "_truth" / "README.txt").write_text(
        "Teammate blind test\n===================\n"
        "1. Edit the files in ../raw/ (Excel/LibreOffice for CSV, QGIS for GeoJSON). Do not read the rules first.\n"
        "2. For every edit add a row to manual_errors.csv: plot_id (e.g. 12/3), error_kind, what you changed.\n"
        f"   error_kind is one of: {', '.join(ERROR_KINDS)}\n"
        "3. Run:  python -m landconnect.blind score\n", encoding="utf-8")
    geo.write_json(root / "workspace.json", {
        "title": "Teammate blind test", "kind": "synthetic",
        "description": "A copy of the demo data edited by hand by a teammate who has not seen the rules."}, indent=2)
    result = _run(ws, "Teammate kit baseline")
    base = sorted({(c["type"], c["plot_id"]) for c in result["conflicts"]})
    geo.write_json(root / "_truth" / "baseline.json", base)
    print(f"Kit ready in {root}. Baseline: {len(base)} conflicts on the untouched copy. "
          f"Now edit raw/ and log each edit in _truth/manual_errors.csv, then run: python -m landconnect.blind score")


def score(ws="blind_manual"):
    root = config.DATA_DIR / ws
    base = {tuple(x) for x in geo.read_json(root / "_truth" / "baseline.json")}
    with open(root / "_truth" / "manual_errors.csv", encoding="utf-8") as f:
        logged = [r for r in csv.DictReader(f) if r.get("plot_id")]
    if not logged:
        print("manual_errors.csv is empty: log the edits first")
        return None
    result = _run(ws, "Teammate edits")
    new = {(c["type"], c["plot_id"]) for c in result["conflicts"]} - base
    rows, edited = [], {r["plot_id"].strip() for r in logged}
    for r in logged:
        kinds = ERROR_KINDS.get(r["error_kind"].strip(), [])
        hit = sorted(t for t, pid in new if pid == r["plot_id"].strip() and t in kinds)
        rows.append({"plot_id": r["plot_id"].strip(), "error_kind": r["error_kind"].strip(), "found": bool(hit), "as": hit})
    fa = sorted({(t, pid) for t, pid in new if pid not in edited})
    found = sum(r["found"] for r in rows)
    P = found / (found + len(fa)) if found + len(fa) else None
    Rc = found / len(rows)
    entry = {"at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "rules": conflicts.RULESET, "kind": "teammate",
             "overall": {"found": found, "false_alarms": len(fa), "missed": len(rows) - found,
                         "precision": round(P, 3) if P is not None else None, "recall": round(Rc, 3),
                         "f1": round(2 * P * Rc / (P + Rc), 3) if P and Rc else None},
             "edits": rows, "false_alarm_list": [f"{t} on {pid}" for t, pid in fa]}
    _save("blind_manual", entry)
    print(f"Teammate batch: {found}/{len(rows)} edits found, {len(fa)} false alarms")
    for r in rows:
        print(f"  {'FOUND ' if r['found'] else 'MISSED'} {r['plot_id']:>8} {r['error_kind']:<22} {', '.join(r['as'])}")
    return entry


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "random"
    {"random": random_batch, "kit": kit, "score": score}[cmd]()
