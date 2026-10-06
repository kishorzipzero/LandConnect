"""Change detection between two drone survey dates (footprint + DSM comparison)."""
from shapely.strtree import STRtree

FLOOR_M = 3.2


def detect(b_t1, b_t2, assign_parcel):
    """b_t1 / b_t2: extracted buildings [{"geom", "height", ...}]. Returns a list of changes."""
    tree = STRtree([b["geom"] for b in b_t1]) if b_t1 else None
    matched, out = set(), []
    for b in b_t2:
        best, bi = 0.0, None
        if tree is not None:
            for i in tree.query(b["geom"]):
                i = int(i)
                inter = b["geom"].intersection(b_t1[i]["geom"]).area
                iou = inter / (b["geom"].union(b_t1[i]["geom"]).area or 1e-9)
                if iou > best:
                    best, bi = iou, i
        pid = assign_parcel(b["geom"])
        if best >= 0.5:
            matched.add(bi)
            old = b_t1[bi]
            a1, a2 = old["geom"].area, b["geom"].area
            dh = b["height"] - old["height"]
            if a2 / a1 > 1.12:
                out.append(dict(type="extension", geom=b["geom"], plot_id=pid, area_t1=round(a1, 1),
                                area_t2=round(a2, 1), height_t1=old["height"], height_t2=b["height"],
                                detail=f"Footprint grew {a2 - a1:.0f} m² ({(a2 / a1 - 1) * 100:.0f}%)"))
            if dh > FLOOR_M * 0.7:
                n = max(1, round(dh / FLOOR_M))
                out.append(dict(type="new_floor", geom=b["geom"], plot_id=pid, area_t1=round(a1, 1),
                                area_t2=round(a2, 1), height_t1=old["height"], height_t2=b["height"],
                                detail=f"Height {old['height']:.1f} m → {b['height']:.1f} m (+{n} floor{'s' * (n > 1)})"))
        elif best < 0.2:
            out.append(dict(type="new", geom=b["geom"], plot_id=pid, area_t1=0, area_t2=round(b["geom"].area, 1),
                            height_t1=0, height_t2=b["height"],
                            detail=f"New building, {b['geom'].area:.0f} m², {b['floors']} floor(s)"))
    for i, old in enumerate(b_t1):
        if i not in matched:
            out.append(dict(type="demolished", geom=old["geom"], plot_id=assign_parcel(old["geom"]),
                            area_t1=round(old["geom"].area, 1), area_t2=0, height_t1=old["height"], height_t2=0,
                            detail=f"Building of {old['geom'].area:.0f} m² no longer present"))
    for n, c in enumerate(out):
        c["id"] = f"CH-{n + 1:03d}"
    return out
