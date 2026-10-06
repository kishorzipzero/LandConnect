"""Confidence scoring for integrated outputs.

C = 100 x (0.30 G + 0.20 A + 0.20 S + 0.15 M + 0.15 T)
  G geometric agreement, A attribute agreement, S source reliability,
  M model confidence, T topology validity (all 0..1)
Open conflicts cap the score (high -> below the review line, medium -> below auto-approve),
so a parcel can never be auto-approved while sources still disagree about it.
"""
from . import config
from .conflicts import SEVERITY_CAP

LABELS = {"G": "Geometric agreement", "A": "Attribute agreement", "S": "Source reliability",
          "M": "Model confidence", "T": "Topology validity"}


def components(p):
    muni = p.get("municipal")
    gnss = p.get("gnss_vertices", 0)

    iou = p["match_features"]["iou"] if muni and not p.get("municipal_merged") else None
    if iou is not None:
        # overlap penalises small plots; mean boundary offset does not - take the better of the two
        off = p.get("muni_offset_m", 0.0)
        G = max(min(1.0, iou / 0.85), min(1.0, max(0.0, 1 - max(0.0, off - 0.5) / 2.0)))
    else:
        G = 0.7 if p.get("municipal_merged") else 0.55
    if gnss >= 3:
        G = max(G, 0.97)

    parts = []
    if p.get("ror"):
        parts.append(1.0)                                   # linked to a revenue record by plot ID
        if "owner_agreement" in p:
            parts.append(p["owner_agreement"] / 100)
        if "area_agreement" in p:
            parts.append(p["area_agreement"])
    else:
        parts.append(0.2)
    if p.get("gt"):
        parts.append(1.0)
    A = sum(parts) / len(parts)

    R = config.SOURCE_RELIABILITY
    S = R["gnss"] if gnss >= 3 else 0.8 if gnss >= 1 else R["cadastral"]
    if p.get("gt"):
        S += 0.1
    if iou is not None and iou > 0.8:
        S += 0.1                                            # independently corroborated by the ULB layer
    S = min(1.0, S)

    M = p["match_prob"] if muni else 0.5
    T = p.get("T", 1.0)
    return {"G": round(G, 3), "A": round(A, 3), "S": round(S, 3), "M": round(M, 3), "T": round(T, 3)}


def base_score(comp):
    return round(100 * sum(config.WEIGHTS[k] * comp[k] for k in config.WEIGHTS), 1)


def finalize(p, conflicts_by_id):
    """Apply conflict caps / human verification and set the parcel status."""
    score = p["score_base"]
    open_ = [conflicts_by_id[c] for c in p["conflicts"] if conflicts_by_id[c]["status"] == "open"]
    human = [conflicts_by_id[c] for c in p["conflicts"] if conflicts_by_id[c]["status"] == "resolved"]
    for c in open_:
        score = min(score, SEVERITY_CAP[c["severity"]])
    if human and not open_:
        status = "verified"
        score = max(score, 95.0)
    elif score >= config.AUTO_APPROVE:
        status = "approved"
    elif score >= config.FLAG:
        status = "flagged"
    else:
        status = "review"
    p["score"], p["status"] = round(score, 1), status
    p["open_conflicts"] = len(open_)
