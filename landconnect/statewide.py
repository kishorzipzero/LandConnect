"""State-wide cadastral maps (real) from datameet's Indian cadastrals collection, shown as vector tiles.

Source: https://github.com/ramSeraph/indian_cadastrals - parcels collected from state portals (TNGIS, KGIS,
NCSCM, APSAC, Bhunaksha ...), published as PMTiles (one file per source; the map fetches only the tiles in
view, so a whole state opens instantly). Licence stated by the maintainer: CC0 1.0, "but attribute datameet
and the original government source where possible". The maintainer files them under "not-so-open" maps
because the portals' own reuse terms are not stated - fine for a prototype with attribution; confirm with the
state before production use.

    python -m landconnect.statewide fetch tamil-nadu karnataka   # or: south | <any release tag>
    python -m landconnect.statewide list
Files go to data/_statewide/<state>/ (ignored by the area pipeline) and appear in the map's Layers panel.
"""
import gzip
import json
import re
import struct
import sys
import urllib.request
from pathlib import Path

from . import config

REPO = "ramSeraph/indian_cadastrals"
ROOT = config.DATA_DIR / "_statewide"
SOUTH = ["puducherry", "tamil-nadu", "kerala", "karnataka", "telangana", "andhra-pradesh"]
STATE_NAMES = {"tamil-nadu": "Tamil Nadu", "karnataka": "Karnataka", "kerala": "Kerala", "andhra-pradesh": "Andhra Pradesh",
               "telangana": "Telangana", "puducherry": "Puducherry"}


def _api(path):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}", headers={"User-Agent": "landconnect"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _notes(body):
    """Per-file description / source / licence from the release notes (markdown sections per file)."""
    out = {}
    for sec in re.split(r"^### ", body or "", flags=re.M)[1:]:
        m = re.match(r"\[([^\]]+)\]", sec)
        if not m:
            continue
        stem = m.group(1).split(".")[0]
        g = lambda k: (re.search(rf"^{k}:\s*(.+?)\s*$", sec, re.M) or [None, None])[1]   # noqa: E731
        src = g("Source") or ""
        url = (re.search(r"https?://\S+", src) or [None])[0]
        lic = g("License") or ""
        out[stem] = {"description": g("Description"), "source": re.sub(r"\s*-\s*https?://\S+", "", src).strip(),
                     "source_url": url, "license": re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", lic)}
    return out


def fetch(tag):
    rel = _api(f"releases/tags/{tag}")
    notes = _notes(rel.get("body"))
    d = ROOT / tag
    d.mkdir(parents=True, exist_ok=True)
    for a in rel["assets"]:
        if not a["name"].endswith(".pmtiles") or a["size"] < 1000:
            continue
        path = d / a["name"]
        have = path.stat().st_size if path.exists() else 0
        if have < a["size"]:
            req = urllib.request.Request(a["browser_download_url"], headers={"User-Agent": "landconnect",
                                                                            **({"Range": f"bytes={have}-"} if have else {})})
            with urllib.request.urlopen(req, timeout=120) as r, open(path, "ab" if have else "wb") as f:
                done, step = have, 0
                while chunk := r.read(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if done // (50 << 20) != step:
                        step = done // (50 << 20)
                        print(f"  {a['name']}: {done / 1e6:,.0f} / {a['size'] / 1e6:,.0f} MB", flush=True)
        meta = {"file": a["name"], "state": tag, "state_name": STATE_NAMES.get(tag, tag.replace("-", " ").title()),
                "bytes": a["size"], "release": f"https://github.com/{REPO}/releases/tag/{tag}",
                **notes.get(a["name"].split(".")[0], {})}
        (d / (a["name"] + ".json")).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"{tag}: {a['name']} ready ({a['size'] / 1e6:,.0f} MB)", flush=True)


# ----------------------------------------------------------------------------- PMTiles header (spec v3)
def header(path):
    with open(path, "rb") as f:
        h = f.read(127)
        if h[:7] != b"PMTiles" or h[7] != 3:
            raise ValueError("not a PMTiles v3 file")
        meta_off, meta_len = struct.unpack_from("<QQ", h, 24)
        n_tiles = struct.unpack_from("<Q", h, 80)[0]
        comp, tile_type, minz, maxz = h[97], h[99], h[100], h[101]
        minlon, minlat, maxlon, maxlat = (v / 1e7 for v in struct.unpack_from("<iiii", h, 102))
        cz, clon, clat = h[118], *(v / 1e7 for v in struct.unpack_from("<ii", h, 119))
        f.seek(meta_off)
        raw = f.read(meta_len)
    meta = json.loads(gzip.decompress(raw) if comp == 2 else raw) if raw else {}
    return {"min_zoom": minz, "max_zoom": maxz, "bounds": [minlon, minlat, maxlon, maxlat], "center": [clon, clat, cz],
            "tile_type": {1: "mvt", 2: "png", 3: "jpeg", 4: "webp"}.get(tile_type, "unknown"), "tiles": n_tiles,
            "vector_layers": [{"id": v.get("id"), "fields": v.get("fields", {})} for v in meta.get("vector_layers", [])],
            "attribution": meta.get("attribution")}


def catalog():
    out = []
    for p in sorted(ROOT.glob("*/*.pmtiles")):
        side = p.with_name(p.name + ".json")
        if not side.exists():
            continue                                                  # still downloading (metadata is written last)
        info = json.loads(side.read_text(encoding="utf-8"))
        if info.get("bytes") and p.stat().st_size < info["bytes"]:
            continue
        try:
            info.update(header(p))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        info["id"] = f"{p.parent.name}/{p.name}"
        info["url"] = f"/statewide/{p.parent.name}/{p.name}"
        try:
            info["overview"] = {**overview(p), "url": f"/statewide/{p.parent.name}/{p.name}/overview.png"}
        except Exception:                                             # overview is optional
            info["overview"] = None
        out.append(info)
    return out


def path_of(state, name):
    p = (ROOT / state / name).resolve()
    if ROOT.resolve() not in p.parents or p.suffix != ".pmtiles" or not p.exists():
        raise FileNotFoundError(name)
    return p


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd, *args = sys.argv[1:] or ["list"]
    if cmd == "fetch":
        for t in (SOUTH if args == ["south"] else args):
            fetch(t)
    else:
        for c in catalog():
            print(f"{c['id']}: {c.get('description')} · z{c['min_zoom']}-{c['max_zoom']} · {c['tiles']:,} tiles · "
                  f"layers {[v['id'] for v in c['vector_layers']]}")


# ----------------------------------------------------------------------------- tile index -> state-wide overview
def _varint(b, i):
    v = s = 0
    while True:
        c = b[i]
        i += 1
        v |= (c & 0x7F) << s
        if c < 0x80:
            return v, i
        s += 7


def _directory(raw, comp):
    b = gzip.decompress(raw) if comp == 2 else raw
    n, i = _varint(b, 0)
    ids, runs, lens, offs = [], [], [], []
    last = 0
    for _ in range(n):
        d, i = _varint(b, i)
        last += d
        ids.append(last)
    for arr in (runs, lens):
        for _ in range(n):
            v, i = _varint(b, i)
            arr.append(v)
    for k in range(n):
        v, i = _varint(b, i)
        offs.append(offs[k - 1] + lens[k - 1] if v == 0 and k > 0 else v - 1)
    return list(zip(ids, runs, lens, offs))


def _zxy(tid):
    acc, z = 0, 0
    while True:
        num = 1 << (2 * z)
        if acc + num > tid:
            break
        acc, z = acc + num, z + 1
    t, x, y, s, n = tid - acc, 0, 0, 1, 1 << z
    while s < n:                                          # Hilbert curve index -> x, y (PMTiles spec)
        rx = 1 & (t // 2)
        ry = 1 & (t ^ rx)
        if ry == 0:
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        x, y, t, s = x + s * rx, y + s * ry, t // 4, s * 2
    return z, x, y


def tile_index(path):
    """Every tile in the file as (z, x, y, bytes), read from the directories only (no tile is decoded)."""
    with open(path, "rb") as f:
        h = f.read(127)
        root_off, root_len, _, _, leaf_off = struct.unpack_from("<QQQQQ", h, 8)
        comp = h[97]
        f.seek(root_off)
        todo, out = [f.read(root_len)], []
        while todo:
            for tid, run, length, off in _directory(todo.pop(), comp):
                if run == 0:                                # a leaf directory
                    f.seek(leaf_off + off)
                    todo.append(f.read(length))
                else:
                    for k in range(run):
                        out.append((*_zxy(tid + k), length))
    return out


def overview(path):
    """Density picture of the whole map (one pixel per tile at the deepest zoom, coloured by its size), for
    views zoomed out further than the file has parcels. Cached next to the file."""
    import math
    import cv2
    import numpy as np
    png, side = path.with_name(path.name + ".overview.png"), path.with_name(path.name + ".overview.json")
    if side.exists() and png.exists() and side.stat().st_mtime >= path.stat().st_mtime:
        return json.loads(side.read_text(encoding="utf-8"))
    tiles = tile_index(path)
    zooms = sorted({t[0] for t in tiles})
    zc = max(zooms)
    deep = [t for t in tiles if t[0] == zc]
    xs, ys = [t[1] for t in deep], [t[2] for t in deep]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    img = np.zeros((y1 - y0 + 1, x1 - x0 + 1), np.float32)
    for _, x, y, n in deep:
        img[y - y0, x - x0] = math.log1p(n)
    lo = np.percentile(img[img > 0], 2) if (img > 0).any() else 0
    v = np.clip((img - lo) / max(img.max() - lo, 1e-6), 0, 1)
    col = cv2.applyColorMap((v * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    alpha = np.where(img > 0, 150 + (v * 90).astype(np.uint8), 0).astype(np.uint8)
    big = cv2.resize(np.dstack([col, alpha]), None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(png), big)
    lon = lambda x: x / (1 << zc) * 360 - 180                                                       # noqa: E731
    lat = lambda y: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / (1 << zc)))))           # noqa: E731
    info = {"bounds": [[lat(y1 + 1), lon(x0)], [lat(y0), lon(x1 + 1)]], "data_zooms": zooms,
            "data_min_zoom": min(z for z in zooms if sum(1 for t in tiles if t[0] == z) > 4), "detail_zoom": zc,
            "tiles_at_detail_zoom": len(deep), "bytes_at_detail_zoom": sum(t[3] for t in deep)}
    side.write_text(json.dumps(info), encoding="utf-8")
    return info
