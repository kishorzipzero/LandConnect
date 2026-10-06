"""Demo preparation: retire change requests the pipeline drafted from deeds that are no longer in the data.

The system drafts an owner-change request for every registered sale not yet mutated in the RoR. If the
department files are regenerated afterwards, those drafts still cite the old deed numbers. This marks such
drafts 'stale' (nothing is deleted; each change is written to the audit trail). The next pipeline run then
drafts fresh requests from the current deeds.

    python -m landconnect.demo_prep jayanagar
"""
import sys

from . import config, db, geo, records


def retire_stale_drafts(ws):
    path = config.ws_paths(ws)["result"]
    if not path.exists():
        print(f"No result for '{ws}' yet: run the pipeline first.")
        return 0
    result = geo.read_json(path)
    deeds = {str(r.get("id")) for f in result["layers"]["parcels"]["features"]
             for r in (f["properties"].get("dept_records") or {}).get("registration", [])}
    rows = db.query("SELECT id, source_ref FROM change_requests WHERE ws = ? AND status = 'pending' "
                    "AND proposed_by = 'system' AND source_ref IS NOT NULL", (ws,))
    n = 0
    for r in rows:
        if r["source_ref"] in deeds:
            continue
        db.execute("UPDATE change_requests SET status = 'stale', decided_by = 'system', decided_at = ?, decision_note = ? "
                   "WHERE id = ?", (db.now(), f"Deed {r['source_ref']} is not in the current registration data", r["id"]))
        records._log(ws, "change.stale", records.get(r["id"]), "system")
        n += 1
    print(f"{ws}: {n} of {len(rows)} system-drafted request(s) marked stale. "
          f"Now run the pipeline for '{ws}' (Admin: ☰ menu → Run pipeline) to draft fresh ones.")
    return n


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    retire_stale_drafts(sys.argv[1] if len(sys.argv) > 1 else "jayanagar")
