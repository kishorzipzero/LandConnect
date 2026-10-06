"""Property report (PDF): everything every government body holds about one plot, in one document.

Sections: key facts · boundary map · confidence · records by department (Revenue, Survey, Municipal,
Town Planning, Registration, Water, Electricity, Gas, Roads, field verification, buildings) ·
conflicts · official updates and change requests · audit trail extract.

Indian-script names (Kannada, Hindi, Tamil ...) are shaped with HarfBuzz through the Nirmala UI font
when it is installed (Windows); elsewhere they are shown transliterated to Latin rather than as
broken glyphs. The SHA-256 of every generated report is written to the audit trail by the API.
"""
import datetime as dt
import io
import math
import secrets
from pathlib import Path

from pyproj import Geod
from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)
from shapely.geometry import shape

from . import audit, config, departments, records
from .schema_mapper import INDIC_RE, to_latin

GEOD = Geod(ellps="WGS84")
INK, MUTED, FAINT = colors.HexColor("#111a22"), colors.HexColor("#56636f"), colors.HexColor("#8a96a1")
ACCENT, LINE, SOFT = colors.HexColor("#0f5e74"), colors.HexColor("#dde3e9"), colors.HexColor("#f4f6f8")
STATUS = {"approved": ("Auto-approved", "#2f8f57"), "flagged": ("Approved, flagged", "#c98a16"),
          "review": ("Needs review", "#c8412f"), "verified": ("Verified by reviewer", "#2a6fb0")}
SEVERITY = {"high": "#c8412f", "medium": "#c98a16", "low": "#74828f"}
W, H = A4
MARGIN = 16 * mm

# ----------------------------------------------------------------------------- fonts
_FONTS = None


def _fonts():
    """(regular, bold, can_shape_indic)"""
    global _FONTS
    if _FONTS:
        return _FONTS
    candidates = [Path(r"C:\Windows\Fonts\Nirmala.ttc"), Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf")]
    for path in candidates:
        if path.exists() and path.suffix == ".ttc":
            try:
                pdfmetrics.registerFont(TTFont("LC", str(path), subfontIndex=0, shapable=True))
                pdfmetrics.registerFont(TTFont("LC-Bold", str(path), subfontIndex=1, shapable=True))
                _FONTS = ("LC", "LC-Bold", True)
                return _FONTS
            except Exception:
                pass
    _FONTS = ("Helvetica", "Helvetica-Bold", False)
    return _FONTS


def _txt(v):
    """Escape for Paragraph markup; transliterate Indic text if the font cannot shape it."""
    s = ("" if v is None else str(v)).replace("→", "to")      # Nirmala UI has no arrow glyph
    if INDIC_RE.search(s) and not _fonts()[2]:
        s = f"{to_latin(s)} (transliterated)"
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _styles():
    reg, bold, _ = _fonts()
    base = dict(fontName=reg, textColor=INK, shaping=1)
    return {
        "title": ParagraphStyle("title", fontName=bold, fontSize=22, leading=26, textColor=INK, shaping=1),
        "sub": ParagraphStyle("sub", fontSize=10, leading=13, textColor=MUTED, **{k: v for k, v in base.items() if k != "textColor"}),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=12, leading=15, textColor=INK, spaceBefore=10, spaceAfter=5, shaping=1),
        "h3": ParagraphStyle("h3", fontName=bold, fontSize=10, leading=13, textColor=INK, shaping=1),
        "body": ParagraphStyle("body", fontSize=9, leading=12, **base),
        "small": ParagraphStyle("small", fontSize=7.8, leading=10, textColor=MUTED, **{k: v for k, v in base.items() if k != "textColor"}),
        "label": ParagraphStyle("label", fontSize=7.8, leading=10, textColor=MUTED, **{k: v for k, v in base.items() if k != "textColor"}),
        "value": ParagraphStyle("value", fontName=bold, fontSize=9.5, leading=12.5, textColor=INK, shaping=1),
        "cell": ParagraphStyle("cell", fontSize=8.3, leading=10.8, **base),
        "cellb": ParagraphStyle("cellb", fontName=bold, fontSize=8.3, leading=10.8, textColor=INK, shaping=1),
        "right": ParagraphStyle("right", fontSize=8, leading=10, alignment=TA_RIGHT, textColor=MUTED,
                                **{k: v for k, v in base.items() if k != "textColor"}),
    }


# ----------------------------------------------------------------------------- page furniture
class _Canvas(rl_canvas.Canvas):
    """Adds 'Page x of y' once the total is known."""
    def __init__(self, *a, meta=None, **k):
        super().__init__(*a, **k)
        self._pages, self._meta = [], meta

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self._furniture(total)
            super().showPage()
        super().save()

    def _furniture(self, total):
        reg, bold, _ = _fonts()
        m = self._meta
        self.setFillColor(ACCENT)
        self.rect(0, H - 6 * mm, W, 6 * mm, stroke=0, fill=1)
        self.setFont(bold, 8)
        self.setFillColor(ACCENT)
        self.drawString(MARGIN, H - 13 * mm, "LAND CONNECT  ·  PROPERTY REPORT")
        self.setFont(reg, 8)
        self.setFillColor(MUTED)
        self.drawRightString(W - MARGIN, H - 13 * mm, f"{m['report_id']}  ·  {m['generated']}")
        self.setStrokeColor(LINE)
        self.setLineWidth(0.6)
        self.line(MARGIN, H - 15.5 * mm, W - MARGIN, H - 15.5 * mm)
        self.line(MARGIN, 15 * mm, W - MARGIN, 15 * mm)
        self.setFont(reg, 7)
        self.setFillColor(FAINT)
        self.drawString(MARGIN, 10.5 * mm, f"Generated by {m['user']} · {m['area']} · run {m['run']}")
        self.drawString(MARGIN, 7 * mm, "Not a title document; rely on the originating department's certified copy. "
                                        "This file's SHA-256 is recorded in the audit trail.")
        self.setFont(bold, 7.5)
        self.setFillColor(MUTED)
        self.drawRightString(W - MARGIN, 7 * mm, f"Page {self._pageNumber} of {total}")


# ----------------------------------------------------------------------------- building blocks
def _kv_table(pairs, S, cols=2):
    """Label / value grid, `cols` pairs per row."""
    cells, row = [], []
    for label, value in pairs:
        row.append([Paragraph(_txt(label), S["label"]), Paragraph(value if isinstance(value, str) and value.startswith("<") else _txt(value if value not in (None, "") else "—"), S["value"])])
        if len(row) == cols:
            cells.append(row)
            row = []
    if row:
        row += [[Paragraph("", S["label"]), Paragraph("", S["value"])]] * (cols - len(row))
        cells.append(row)
    width = (W - 2 * MARGIN) / cols
    t = Table(cells, colWidths=[width] * cols)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                           ("TOPPADDING", (0, 0), (-1, -1), 2)]))
    return t


def _grid(header, rows, widths, S, zebra=True):
    data = [[Paragraph(_txt(h), S["label"]) for h in header]]
    for r in rows:
        data.append([c if not isinstance(c, (str, int, float, type(None))) else Paragraph(_txt(c if c not in (None, "") else "—"), S["cell"]) for c in r])
    total = W - 2 * MARGIN
    t = Table(data, colWidths=[total * w for w in widths], repeatRows=1)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINE),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if zebra:
        for i in range(2, len(data), 2):
            style.append(("BACKGROUND", (0, i), (-1, i), SOFT))
    t.setStyle(TableStyle(style))
    return t


def _dept_block(title, color, content, S):
    head = Table([[Paragraph(_txt(title), S["h3"])]], colWidths=[W - 2 * MARGIN])
    head.setStyle(TableStyle([("LINEBEFORE", (0, 0), (0, 0), 3, colors.HexColor(color)), ("LEFTPADDING", (0, 0), (-1, -1), 7),
                              ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    # keep the heading with the start of its content; long tables may continue on the next page
    if isinstance(content, list):
        return [KeepTogether([Spacer(1, 7), head, Spacer(1, 4), content[0]])] + content[1:]
    return [KeepTogether([Spacer(1, 7), head, Spacer(1, 4), content])]


def _none(S, text="No record held."):
    return Paragraph(_txt(text), S["small"])


def _pill(text, hexcolor, S):
    t = Table([[Paragraph(f'<font color="white">{_txt(text)}</font>', S["cellb"])]])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(hexcolor)), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 2),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def _bar(value, color, width=46 * mm, height=4):
    d = Drawing(width, height + 2)
    d.add(Rect(0, 1, width, height, fillColor=LINE, strokeColor=None, rx=2, ry=2))
    d.add(Rect(0, 1, max(0.5, width * max(0.0, min(1.0, value))), height, fillColor=colors.HexColor(color), strokeColor=None, rx=2, ry=2))
    return d


# ----------------------------------------------------------------------------- map
def _map(result, feature, plot_id, S, width=W - 2 * MARGIN, height=72 * mm):
    reg, bold, _ = _fonts()
    g = shape(feature["geometry"])
    lon0, lat0 = g.centroid.x, g.centroid.y
    kx, ky = 111320 * math.cos(math.radians(lat0)), 110540

    def xy(lon, lat):
        return (lon - lon0) * kx, (lat - lat0) * ky

    minx, miny, maxx, maxy = g.bounds
    span = max((maxx - minx) * kx, (maxy - miny) * ky)
    pad = max(15.0, span * 0.6)
    x0, y0 = xy(minx, miny)
    x1, y1 = xy(maxx, maxy)
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    scale = min(width / (x1 - x0), height / (y1 - y0))
    ox = (width - (x1 - x0) * scale) / 2
    oy = (height - (y1 - y0) * scale) / 2
    view = shape({"type": "Polygon", "coordinates": [[(lon0 + x0 / kx, lat0 + y0 / ky), (lon0 + x1 / kx, lat0 + y0 / ky),
                                                        (lon0 + x1 / kx, lat0 + y1 / ky), (lon0 + x0 / kx, lat0 + y1 / ky)]]})

    def pts(geom):
        out = []
        for lon, lat in list(geom.exterior.coords):
            x, y = xy(lon, lat)
            out += [ox + (x - x0) * scale, oy + (y - y0) * scale]
        return out

    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=colors.HexColor("#fbfcfd"), strokeColor=LINE, strokeWidth=0.6))
    labels = []
    for f in result["layers"]["parcels"]["features"]:
        pg = shape(f["geometry"])
        if pg.intersects(view) and f["properties"]["plot_id"] != plot_id:
            clipped = pg.intersection(view)
            for part in getattr(clipped, "geoms", [clipped]):
                if part.geom_type == "Polygon" and part.area > 0:
                    d.add(Polygon(pts(part), fillColor=None, strokeColor=colors.HexColor("#b8c2cc"), strokeWidth=0.5))
            c = pg.representative_point()
            if view.buffer(-0.00003).contains(c):
                labels.append((c, f["properties"]["plot_id"]))
    for f in result["layers"]["buildings"]["features"]:
        bg = shape(f["geometry"])
        if bg.intersects(view) and bg.geom_type == "Polygon" and view.contains(bg):
            d.add(Polygon(pts(bg), fillColor=colors.HexColor("#e9edf1"), strokeColor=colors.HexColor("#7b8896"), strokeWidth=0.4))
    for part in getattr(g, "geoms", [g]):
        d.add(Polygon(pts(part), fillColor=colors.Color(15 / 255, 94 / 255, 116 / 255, alpha=0.14), strokeColor=ACCENT, strokeWidth=1.6))
    for f in result["layers"].get("connections", {}).get("features", []):
        if f["properties"].get("plot_id") == plot_id:
            lon, lat = f["geometry"]["coordinates"]
            x, y = xy(lon, lat)
            d.add(Circle(ox + (x - x0) * scale, oy + (y - y0) * scale, 2.6,
                         fillColor=colors.HexColor(departments.DEPARTMENTS[f["properties"]["dept"]]["color"]),
                         strokeColor=colors.white, strokeWidth=0.6))
    for c, pid in labels:
        x, y = xy(c.x, c.y)
        d.add(String(ox + (x - x0) * scale, oy + (y - y0) * scale - 2, pid, fontName=reg, fontSize=5.5,
                     fillColor=colors.HexColor("#8a96a1"), textAnchor="middle"))
    x, y = xy(g.centroid.x, g.centroid.y)
    d.add(String(ox + (x - x0) * scale, oy + (y - y0) * scale - 3, plot_id, fontName=bold, fontSize=9,
                 fillColor=ACCENT, textAnchor="middle"))
    # scale bar
    target = (x1 - x0) / 5
    step = next(s for s in (5, 10, 20, 25, 50, 100, 200, 500) if s >= target * 0.6)
    bx, by = 10, 10
    d.add(Rect(bx - 4, by - 5, step * scale + 30, 16, fillColor=colors.white, strokeColor=None))
    d.add(Line(bx, by, bx + step * scale, by, strokeColor=INK, strokeWidth=1.2))
    d.add(Line(bx, by - 2, bx, by + 2, strokeColor=INK, strokeWidth=1))
    d.add(Line(bx + step * scale, by - 2, bx + step * scale, by + 2, strokeColor=INK, strokeWidth=1))
    d.add(String(bx + step * scale + 4, by - 2.5, f"{step} m", fontName=reg, fontSize=7, fillColor=INK))
    # north arrow
    nx, ny = width - 14, height - 26
    d.add(Polygon([nx, ny + 14, nx - 4.5, ny, nx, ny + 4, nx + 4.5, ny], fillColor=INK, strokeColor=None))
    d.add(String(nx, ny + 16, "N", fontName=bold, fontSize=7, fillColor=INK, textAnchor="middle"))
    return d


# ----------------------------------------------------------------------------- report
def _fmt_date(s):
    return (s or "").replace("T", " ")[:16]


def build(ws, result, plot_id, user):
    feat = next((f for f in result["layers"]["parcels"]["features"] if f["properties"]["plot_id"] == plot_id), None)
    if feat is None:
        raise KeyError(plot_id)
    S = _styles()
    p = feat["properties"]
    area_title = result.get("workspace_meta", {}).get("title") or result.get("ward", ws)
    now = dt.datetime.now()
    report_id = f"LC-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2).upper()}"
    geom = shape(feat["geometry"])
    area_geod, perim = GEOD.geometry_area_perimeter(geom)
    c = geom.centroid
    recs = p.get("dept_records") or {}
    conflicts = [x for x in result["conflicts"] if x["plot_id"] == plot_id]
    buildings = [f["properties"] for f in result["layers"]["buildings"]["features"] if f["properties"].get("plot_id") == plot_id]
    story = []

    # --- title block
    st_label, st_color = STATUS[p["status"]]
    left = [Paragraph(f"Plot {_txt(plot_id)}", S["title"]), Spacer(1, 3),
            Paragraph(f"{_txt(area_title)} · centroid {c.y:.6f}° N, {c.x:.6f}° E", S["sub"])]
    right = Table([[Paragraph(f'<font size="26">{round(p["score"])}</font><font size="9" color="#56636f"> / 100</font>',
                              ParagraphStyle("sc", fontName=_fonts()[1], fontSize=26, leading=28, alignment=TA_RIGHT))],
                   [Paragraph("confidence score", S["right"])], [_pill(st_label, st_color, S)]],
                  colWidths=[48 * mm])
    right.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "RIGHT"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    head = Table([[left, right]], colWidths=[W - 2 * MARGIN - 50 * mm, 50 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [Spacer(1, 4), head, Spacer(1, 10)]

    owner_src = p.get("owner_source") or ""
    if not p.get("resolved_owner"):
        owner_src = "Records disagree — see Conflicts below"
    story.append(_kv_table([
        ("Owner (resolved)", p.get("resolved_owner") or "Disputed — awaiting review"), ("Owner source", owner_src),
        ("Surveyed area", f"{abs(area_geod):,.1f} m²  ·  {abs(area_geod) / 101.17141:,.2f} gunta  ·  {abs(area_geod) / 0.092903:,.0f} sq ft"),
        ("Recorded extent (RoR)", f"{p['ror_area_sqm']:,.1f} m²" if p.get("ror_area_sqm") else None),
        ("Land use", (p.get("land_use") or "").title()), ("Khata no. · Property ID", " · ".join(x for x in (p.get("khata"), p.get("municipal_pid")) if x) or None),
        ("GNSS-controlled corners", p.get("gnss_vertices") or "None"), ("Open conflicts", p.get("open_conflicts", 0)),
    ], S))

    # --- map
    story.append(Paragraph("Boundary and surroundings", S["h2"]))
    story.append(_map(result, feat, plot_id, S))
    legend = "Harmonised boundary (teal) with neighbouring plots, buildings and this plot's service connections: " + ", ".join(
        f'<font color="{departments.DEPARTMENTS[d]["color"]}">●</font> {departments.DEPARTMENTS[d]["short"]}'
        for d in ("electricity", "water", "gas") if recs.get(d))
    story.append(Paragraph(legend, S["small"]))

    # --- confidence
    comps = p.get("components", {})
    labels = result.get("component_labels", {})
    weights = result.get("weights", {})
    rows = []
    for k, v in comps.items():
        col = "#2f8f57" if v >= 0.85 else "#c98a16" if v >= 0.7 else "#c8412f"
        rows.append([Paragraph(_txt(labels.get(k, k)), S["cell"]), Paragraph(f"× {weights.get(k, '')}", S["cell"]),
                     _bar(v, col), Paragraph(f"{round(v * 100)}", S["cellb"])])
    conf = [Paragraph("Confidence breakdown", S["h2"]),
            _grid(["Component", "Weight", "", "Score"], rows, [0.42, 0.12, 0.34, 0.12], S, zebra=False)]
    if p["score"] < p.get("score_base", p["score"]):
        conf.append(Paragraph(f"Base score {p['score_base']:.1f} is capped at {p['score']:.1f} while conflicts are open.", S["small"]))
    story.append(KeepTogether(conf))

    # --- records by government body
    story.append(Paragraph("Records held by government bodies", S["h2"]))
    D = departments.DEPARTMENTS

    def block(dept, content):
        story.extend(_dept_block(D[dept]["name"], D[dept]["color"], content, S))

    block("revenue", _kv_table([("Owner in RoR", p.get("ror_owner") or "No revenue record"), ("Khata no.", p.get("khata")),
                                ("Recorded extent", f"{p['ror_area_sqm']:,.1f} m²  ({p['ror_area_sqm'] / 101.17141:,.2f} gunta)" if p.get("ror_area_sqm") else None),
                                ("Land classification", (p.get("land_use") or "").title())], S)
          if p.get("ror_owner") or p.get("khata") else _none(S, "No revenue record carries this plot number."))
    verts = list(geom.exterior.coords)[:-1] if geom.geom_type == "Polygon" else []
    surv = [_kv_table([("Surveyed area", f"{abs(area_geod):,.1f} m²"), ("Perimeter", f"{perim:,.1f} m"),
                       ("GNSS-controlled corners", p.get("gnss_vertices") or 0), ("Cadastral sheet", p.get("sheet")),
                       ("Topology corrections", "; ".join(p.get("topo_flags") or []) or "None needed")], S)]
    if verts:
        surv += [Spacer(1, 3), _grid(["Corner", "Latitude", "Longitude"],
                                     [[i + 1, f"{lat:.7f}", f"{lon:.7f}"] for i, (lon, lat) in enumerate(verts[:16])],
                                     [0.16, 0.42, 0.42], S)]
        if len(verts) > 16:
            surv.append(Paragraph(f"{len(verts) - 16} more corners in the GeoJSON / LADM export.", S["small"]))
    story.extend(_dept_block(D["survey"]["name"], D["survey"]["color"], surv, S))
    block("municipal", _kv_table([("Property ID", p.get("municipal_pid")), ("Owner in tax register", p.get("municipal_owner")),
                                  ("Assessed use", (p.get("municipal_use") or "").title()),
                                  ("Match confidence", f"{p.get('match_prob', 0) * 100:.1f}%")], S)
          if p.get("municipal_pid") else _none(S, "Not in the property-tax register."))
    perms = recs.get("town_planning", [])
    block("town_planning", _grid(["Permission no.", "Floors permitted", "Link"], [[r.get("id"), r.get("floors"), r.get("link")] for r in perms],
                                 [0.4, 0.25, 0.35], S) if perms else _none(S, "No building permission on record."))
    deeds = sorted(recs.get("registration", []), key=lambda r: str(r.get("date") or ""), reverse=True)
    block("registration", _grid(["Document no.", "Registered", "Seller", "Buyer"],
                                [[r.get("id"), r.get("date"), r.get("seller"), r.get("buyer")] for r in deeds], [0.26, 0.16, 0.29, 0.29], S)
          if deeds else _none(S, "No registered deed found for this plot."))
    for dept, header in (("water", "Consumer no."), ("electricity", "RR no."), ("gas", "BP no.")):
        rs = recs.get(dept, [])
        block(dept, _grid([header, "Account holder", "Category / tariff", "Load", "Status"],
                          [[r.get("id"), r.get("name"), r.get("category"), f"{r['load']} kW" if r.get("load") else None, r.get("status")] for r in rs],
                          [0.2, 0.3, 0.22, 0.12, 0.16], S) if rs else _none(S, "No connection linked to this plot."))
    block("roads", _kv_table([("Encroachment notice", p.get("road_notice"))], S) if p.get("road_notice")
          else _none(S, "No encroachment notice on record."))
    gt = p.get("gt_owner")
    story.extend(_dept_block("Field verification (ground truth)", "#8a96a1",
                             _kv_table([("Owner confirmed on site", gt)], S) if gt else _none(S, "Not field-verified."), S))
    story.extend(_dept_block("Buildings (drone survey / AI footprints)", "#7b8896",
                             _grid(["Building", "Floors", "Height", "Footprint", "Sources"],
                                   [[b.get("id"), b.get("floors"), f"{b['height_m']:.1f} m" if b.get("height_m") else None,
                                     f"{b.get('area_sqm', 0):,.0f} m²", " + ".join(b.get("sources", []))] for b in buildings],
                                   [0.16, 0.1, 0.12, 0.14, 0.48], S) if buildings else _none(S, "No building detected on this plot."), S))

    # --- conflicts
    story.append(Paragraph("Conflicts between records", S["h2"]))
    if conflicts:
        rows = []
        for x in conflicts:
            sev = f'<font color="{SEVERITY[x["severity"]]}"><b>{x["severity"].title()}</b></font>'
            state = {"open": "Open", "auto_resolved": "Auto-resolved", "resolved": "Resolved"}[x["status"]]
            detail = _txt(x["detail"]) + (f'<br/><font color="#2f8f57">{_txt(x["resolution"])}</font>' if x.get("resolution") else "")
            rows.append([Paragraph(_txt(x["title"]), S["cellb"]), Paragraph(sev, S["cell"]), Paragraph(state, S["cell"]),
                         Paragraph(detail, S["cell"])])
        story.append(_grid(["Issue", "Severity", "Status", "Detail"], rows, [0.24, 0.1, 0.12, 0.54], S))
    else:
        story.append(_none(S, "All sources agree for this plot."))

    # --- official updates and change requests
    reqs = records.list_requests(ws, plot_id=plot_id)
    story.append(Paragraph("Official updates and change requests", S["h2"]))
    if reqs:
        rows = [[r["code"], r["field_label"], r["old_display"], r["new_display"], r["status"].title(),
                 f"{r['proposed_by']} / {r['decided_by'] or '—'}", _fmt_date(r.get("decided_at") or r["proposed_at"])] for r in reqs]
        story.append(_grid(["Request", "Field", "From", "To", "Status", "Proposed / decided by", "Date"], rows,
                           [0.11, 0.15, 0.17, 0.17, 0.1, 0.18, 0.12], S))
    else:
        story.append(_none(S, "No change requests for this plot."))

    # --- audit trail
    apath = config.ws_paths(ws)["audit"]
    ver = audit.verify(apath)
    entries = audit.read(apath, subject=plot_id, limit=14)
    story.append(Paragraph("Audit trail (latest entries for this plot)", S["h2"]))
    story.append(Paragraph(("Hash chain intact" if ver["ok"] else f"Hash chain BROKEN at entry {ver['broken_at']}") +
                           f" · {ver['entries']} entries in total · head {ver['head'][:32]}…", S["small"]))
    story.append(Spacer(1, 3))
    story.append(_grid(["#", "When", "Event", "By", "Entry hash"],
                       [[e["seq"], _fmt_date(e["ts"]), e["kind"], e["actor"], e["hash"][:16] + "…"] for e in entries],
                       [0.08, 0.2, 0.3, 0.2, 0.22], S) if entries else _none(S, "No entries yet."))

    buf = io.BytesIO()
    meta = {"report_id": report_id, "generated": now.strftime("%d %b %Y, %H:%M"),
            "user": f"{user['full_name'] or user['username']} ({user['role']}, {user.get('department', 'all')})",
            "area": area_title, "run": result.get("run_id") or "—"}
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=20 * mm, bottomMargin=20 * mm,
                            title=f"Property report — plot {plot_id}", author="Land Connect", subject=area_title,
                            creator="Land Connect (SIH 2026, PS 013)")
    doc.build(story, canvasmaker=lambda *a, **k: _Canvas(*a, meta=meta, **k))
    return buf.getvalue(), report_id
