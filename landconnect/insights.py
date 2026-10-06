"""Derived information on the harmonised parcels: ULPIN, ownership chain, revenue at risk.

ULPIN (Unique Land Parcel Identification Number, "Bhu-Aadhaar") is DoLR's 14-character parcel ID generated
from the parcel's geo-coordinates. The official encoding is applied by each state's land records department;
this prototype generates an ID in the same shape - 2-letter state code + 12 characters encoding the parcel's
reference point (a geohash, ~4 cm cells) - so the harmonised parcels already carry a stable, location-based
national key. It is labelled "ULPIN-format" in the interface until a state issues the real ones.
"""
from . import config, geo
from .schema_mapper import owner_similarity

_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash(lat, lon, n=12):
    lat_r, lon_r, out, bit, ch, even = [-90.0, 90.0], [-180.0, 180.0], [], 0, 0, True
    while len(out) < n:
        r, v = (lon_r, lon) if even else (lat_r, lat)
        mid = (r[0] + r[1]) / 2
        if v >= mid:
            ch, r[0] = ch | (1 << (4 - bit)), mid
        else:
            r[1] = mid
        even, bit = not even, bit + 1
        if bit == 5:
            out.append(_B32[ch])
            bit, ch = 0, 0
    return "".join(out)


def ulpin_lonlat(lon, lat, state_code="KA"):
    return (state_code[:2] + geohash(lat, lon, 12)).upper()


def ulpin(geom_utm, state_code="KA"):
    c = geom_utm.representative_point()
    lon, lat = geo.transformer(config.TARGET_EPSG, config.WGS84).transform(c.x, c.y)
    return (state_code[:2] + geohash(lat, lon, 12)).upper()


# ----------------------------------------------------------------------------- ownership chain
def ownership_chain(ror_owner, deeds):
    """Registered deeds in date order, each checked against the one before, and the RoR against the last.
    Returns {"steps": [...], "verdict": ..., "ok": bool}."""
    deeds = sorted([d for d in deeds if d.get("buyer")], key=lambda d: str(d.get("date") or ""))
    steps, ok, verdict = [], True, "Chain intact"
    for i, d in enumerate(deeds):
        s = {"date": d.get("date"), "deed": d.get("id"), "from": d.get("seller"), "to": d.get("buyer"), "ok": True}
        if i and d.get("seller") and owner_similarity(d["seller"], deeds[i - 1]["buyer"]) < config.OWNER_MATCH_THRESHOLD:
            s["ok"], ok = False, False
            s["issue"] = f"Seller is not the buyer on the previous deed ({deeds[i - 1]['buyer']})"
        steps.append(s)
    if deeds and ror_owner:
        last = deeds[-1]
        if owner_similarity(ror_owner, last["buyer"]) >= config.OWNER_MATCH_THRESHOLD:
            verdict = "RoR matches the latest registered buyer"
        elif last.get("seller") and owner_similarity(ror_owner, last["seller"]) >= config.OWNER_MATCH_THRESHOLD:
            verdict, ok = "RoR still shows the seller of the latest deed: mutation pending", False
        else:
            verdict, ok = "RoR owner appears on no registered deed: chain broken", False
    elif not deeds:
        verdict = "No registered deed on record"
    if ok and any(not s["ok"] for s in steps):
        ok = False
    if not ok and verdict == "Chain intact":
        verdict = "Chain broken between deeds"
    return {"steps": steps, "verdict": verdict, "ok": ok}


# ----------------------------------------------------------------------------- revenue at risk
# Illustrative annual property-tax rates per m² of built-up area. Real rates depend on the ULB's unit-area
# value zones and are loaded per city; these defaults only make the size of the gap visible.
RATES_INR = {"residential": 30.0, "commercial": 90.0, "institutional": 0.0, "vacant": 0.0}
ASSUMPTIONS = ("Built-up area = footprint × floors (floors measured from DSM − DTM, or 1 if unknown). "
               "Annual tax = built-up area × rate: residential ₹30/m², commercial ₹90/m², institutional exempt. "
               "Per plot the largest of 'untaxed building' items is counted once, so gaps are not double counted.")


def _bua(buildings):
    return sum(b["geom"].area * (b.get("floors") or 1) for b in buildings)


def revenue_at_risk(ctx, conflicts):
    """Add "revenue_inr" (estimated annual property tax not being collected) to the conflicts it applies to,
    and return totals. Resolved-as-wrong items do not count."""
    by_plot = {}
    for b in ctx.get("buildings", []):
        if b.get("plot_id"):
            by_plot.setdefault(b["plot_id"], []).append(b)
    parcels = {p["plot_id"]: p for p in ctx["parcels"]}
    per_plot, by_type = {}, {}
    for c in conflicts:
        pid, t = c.get("plot_id"), c["type"]
        blds = by_plot.get(pid, [])
        p = parcels.get(pid) or {}
        use = ((p.get("gt") or {}).get("land_use") or (p.get("ror") or {}).get("land_use") or "residential").lower()
        rate = RATES_INR.get(use, RATES_INR["residential"])
        amt, key = 0.0, None
        if t in ("UNASSESSED_CONSTRUCTION", "MISSING_IN_MUNICIPAL", "SERVICE_WITHOUT_TAX"):
            amt, key = _bua(blds) * rate, "untaxed"
        elif t == "FLOORS_BEYOND_PERMISSION":
            extra = max(0, (c.get("observed_floors") or 0) - (c.get("permitted_floors") or c.get("observed_floors") or 0))
            amt, key = sum(b["geom"].area for b in blds) * max(extra, 1) * rate, "extra_floors"
        elif t == "TAX_USE_MISMATCH":
            amt, key = _bua(blds) * (RATES_INR["commercial"] - RATES_INR["residential"]), "use"
        if not key or amt <= 0 or c.get("status") == "resolved" and c.get("chosen") is None and "false" in str(c.get("resolution", "")).lower():
            continue
        c["revenue_inr"] = round(amt)
        slot = per_plot.setdefault(pid, {})
        slot[key] = max(slot.get(key, 0), amt)
        by_type[t] = by_type.get(t, 0) + round(amt)
    total = sum(v for s in per_plot.values() for v in s.values())
    return {"total_inr_per_year": round(total), "plots": len(per_plot), "by_type": by_type,
            "rates_inr_per_sqm_year": RATES_INR, "assumptions": ASSUMPTIONS}
