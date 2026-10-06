"""Human-in-the-loop review: decisions on conflicts, stored in the database per workspace, attributed to
the signed-in reviewer, re-applied on every run and written to the tamper-evident audit trail."""
import datetime as dt

from . import audit, config, confidence, db


def _apply(conflict, decision, parcel):
    choice = decision.get("choice")
    versions = conflict.get("versions") or []
    picked = versions[choice]["label"] if choice is not None and 0 <= choice < len(versions) else None
    who = decision.get("by") or "reviewer"
    conflict["status"], conflict["resolved_by"], conflict["chosen"] = "resolved", who, choice
    conflict["resolution"] = (f"{who} accepted: {picked}" if picked else f"{who} confirmed / actioned") + \
        (f" — {decision['note']}" if decision.get("note") else "")
    conflict["decided_at"] = decision.get("at")
    if parcel is not None and conflict["type"] == "OWNER_MISMATCH" and picked:
        parcel["resolved_owner"], parcel["owner_source"] = picked, f"Reviewer decision ({versions[choice]['source']})"


def apply_saved_decisions(ctx):
    ws = ctx.get("ws")
    if not ws:
        return
    db.import_legacy_decisions(ws, config.ws_paths(ws)["decisions"])
    decisions = db.load_decisions(ws)
    by_plot = {p["plot_id"]: p for p in ctx["parcels"]}
    for c in ctx["conflicts"]:
        if c["id"] in decisions and c["status"] == "open":
            _apply(c, decisions[c["id"]], by_plot.get(c["plot_id"]))


def decide(result, ws, conflict_id, choice=None, note="", user="reviewer"):
    """Apply a reviewer decision to the published result in place, persist it and audit it."""
    paths = config.ws_paths(ws)
    c = next((c for c in result["conflicts"] if c["id"] == conflict_id), None)
    if c is None:
        raise KeyError(conflict_id)
    if c["status"] == "auto_resolved":
        raise ValueError("Conflict was already resolved automatically")
    if choice is not None and not (0 <= choice < len(c.get("versions") or [])):
        raise ValueError("Choice does not match any version of this conflict")
    at = dt.datetime.now().isoformat(timespec="seconds")
    decision = {"choice": choice, "note": note, "at": at, "by": user}
    props = _parcel_props(result, c["plot_id"])
    _apply(c, decision, props)
    _refresh(result, props)
    db.save_decision(ws, conflict_id, choice, note, user, at)
    db.update_indexed(ws, c, props)
    picked = c["versions"][choice] if choice is not None and c["versions"] else None
    audit.append_one(paths["audit"], "review.decision", c["plot_id"],
                     {"conflict": conflict_id, "type": c["type"], "accepted": picked and picked["label"],
                      "accepted_source": picked and picked["source"], "note": note,
                      "new_status": props and props["status"]}, actor=user)
    result["audit"] = audit.verify(paths["audit"])
    return c, props


def reopen(result, ws, conflict_id, user="reviewer"):
    paths = config.ws_paths(ws)
    c = next((c for c in result["conflicts"] if c["id"] == conflict_id), None)
    if c is None or c["status"] != "resolved":
        raise KeyError(conflict_id)
    c["status"], c["resolution"], c["resolved_by"], c["chosen"] = "open", None, None, None
    props = _parcel_props(result, c["plot_id"])
    if props is not None and c["type"] == "OWNER_MISMATCH":
        props["resolved_owner"], props["owner_source"] = None, "Disputed — awaiting review"
    _refresh(result, props)
    db.delete_decision(ws, conflict_id)
    db.update_indexed(ws, c, props)
    audit.append_one(paths["audit"], "review.undo", c["plot_id"], {"conflict": conflict_id}, actor=user)
    result["audit"] = audit.verify(paths["audit"])
    return c, props


def _parcel_props(result, plot_id):
    return next((f["properties"] for f in result["layers"]["parcels"]["features"]
                 if f["properties"]["plot_id"] == plot_id), None)


def _refresh(result, props):
    from . import publish
    if props is not None:
        confidence.finalize(props, {c["id"]: c for c in result["conflicts"]})
    result["summary"] = publish.summarize(result)
