"use strict";

// ------------------------------------------------------------------ helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (n, d = 1) => n == null ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });
const pct = (x, d = 1) => x == null ? "–" : (x * 100).toFixed(d) + "%";
const icon = p => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${p}</svg>`;
const I = {
  download: icon('<path d="M12 4v12M7 11l5 5 5-5M5 20h14"/>'), link: icon('<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>'),
  chain: icon('<rect x="3" y="9" width="8" height="6" rx="3"/><rect x="13" y="9" width="8" height="6" rx="3"/><path d="M9 12h6"/>'),
  history: icon('<path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2"/>'),
};
let WS = null, R = null, CFG = {}, WORKSPACES = [], ME = null;
const ROLE_RANK = { viewer: 1, officer: 2, reviewer: 3, admin: 4 };
const can = role => !!ME && ROLE_RANK[ME.role] >= ROLE_RANK[role];

function wsUrl(path) { return path + (path.includes("?") ? "&" : "?") + "ws=" + encodeURIComponent(WS); }
async function api(path, opts = {}) {
  const r = await fetch(wsUrl(path), opts);
  if (r.status === 401) { showLogin(); throw new Error("Please sign in"); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}
function toast(msg, err = false) {
  $(".toast")?.remove();
  const t = document.createElement("div");
  t.className = "toast" + (err ? " err" : "");
  t.setAttribute("role", err ? "alert" : "status");
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), err ? 6000 : 3500);
}

const STATUS_COLORS = { approved: "#2f8f57", flagged: "#c98a16", review: "#c8412f", verified: "#2a6fb0" };
const STATUS_LABELS = { approved: "Auto-approved", flagged: "Approved, flagged", review: "Needs review", verified: "Verified by reviewer" };
const CHANGE_COLORS = { new: "#12a4c4", demolished: "#d03d74", extension: "#e07b12", new_floor: "#7d4bb0" };
const CHANGE_LABELS = { new: "New construction", demolished: "Demolished", extension: "Footprint extension", new_floor: "Floor added" };
const VERSION_COLORS = ["#00e5ff", "#ff3df5"];

let tab = "overview", reviewFilter = "open", severityFilter = "all", selectedConflict = null;
let epoch = "t2", basemap = null, auditFilter = { subject: "", kind: "" }, queryState = null;

// ------------------------------------------------------------------ map
const map = L.map("map", { maxZoom: 22, zoomSnap: 0.25, zoomControl: false });
L.control.zoom({ position: "bottomright" }).addTo(map);
L.control.scale({ position: "bottomright", imperial: false }).addTo(map);
map.setView([12.93, 77.58], 16);
map.attributionControl.setPrefix("Leaflet");
const selection = L.layerGroup();
const queryLayer = L.layerGroup();

const BASEMAPS = {
  satellite: () => L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxNativeZoom: 19, maxZoom: 22, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" }),
  google: () => L.tileLayer(CFG.google_tiles, { maxNativeZoom: 20, maxZoom: 22, attribution: "Imagery © Google" }),
  streets: () => L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", { maxNativeZoom: 17, maxZoom: 22, attribution: "Map © Esri, HERE, Garmin, © OpenStreetMap contributors" }),
};
const LAYER_DEFS = [
  ["heights", "Building heights (DSM − DTM)", "Imagery", false],
  ["heights_t1", "Building heights, earlier survey", "Imagery", false],
  ["dsm", "Surface model (DSM)", "Imagery", false],
  ["dtm", "Terrain model (DTM)", "Imagery", false],
  ["parcels", "Parcels by confidence", "Harmonised output", true],
  ["buildings", "Buildings (fused)", "Harmonised output", true],
  ["proposals", "Pending map proposals", "Harmonised output", true],
  ["changes", "Detected changes", "Harmonised output", false],
  ["conflicts", "Open conflicts", "Harmonised output", true],
  ["utility", "Utility network", "Harmonised output", true],
  ["connections", "Service connections (electricity, water, gas)", "Departments", false],
  ["roads", "Road network (OSM)", "Context", false],
  ["poi", "Landmarks (OSM)", "Context", false],
  ["municipal", "Municipal layer, as received", "Source data", false],
  ["cadastral_naive", "Cadastral, before correction", "Source data", false],
  ["cadastral_georef", "Cadastral, georeferenced", "Source data", false],
  ["cadastral_alt", "Second cadastral survey, as received", "Source data", false],
  ["ai_buildings", "AI building footprints, as received", "Source data", false],
  ["footprints", "Building footprints, as received", "Source data", false],
  ["gnss", "GNSS control points", "Source data", false],
  ["ground_truth", "Field visits (GT)", "Source data", false],
];
const vis = Object.fromEntries(LAYER_DEFS.map(([k, , , on]) => [k, on]));
const layers = {};
let parcelIndex = {}, baseLayer = null;

const parcelStyle = f => { const c = STATUS_COLORS[f.properties.status]; return { color: c, weight: 1.5, fillColor: c, fillOpacity: 0.18 }; };
const nonEmpty = k => R.layers[k] && R.layers[k].features.length;

function buildLayers() {
  Object.values(layers).forEach(l => map.removeLayer(l));
  for (const k in layers) delete layers[k];
  parcelIndex = {};
  const Lr = R.layers, v = `?v=${encodeURIComponent(R.generated_at)}`;
  if (R.rasters.heights_t2) layers.heights = L.imageOverlay(R.rasters.heights_t2.url + v, R.rasters.heights_t2.bounds, { opacity: 0.85 });
  if (R.rasters.heights_t1) layers.heights_t1 = L.imageOverlay(R.rasters.heights_t1.url + v, R.rasters.heights_t1.bounds, { opacity: 0.85 });
  if (R.rasters.dsm_t2) layers.dsm = L.imageOverlay(R.rasters.dsm_t2.url + v, R.rasters.dsm_t2.bounds, { opacity: 0.9 });
  if (R.rasters.dtm) layers.dtm = L.imageOverlay(R.rasters.dtm.url + v, R.rasters.dtm.bounds, { opacity: 0.9 });
  layers.parcels = L.geoJSON(Lr.parcels, { style: parcelStyle, onEachFeature: (f, l) => {
    parcelIndex[f.properties.plot_id] = l;
    l.bindTooltip(`${esc(f.properties.plot_id)} · ${Math.round(f.properties.score)}`, { sticky: true });
    l.on("click", () => { lastParcelClick = Date.now(); if (!ED.on && !MEAS.on && !AREA.on && Date.now() - (AREA.done || 0) > 400) inspect(f.properties.plot_id); });
  } });
  layers.buildings = L.geoJSON(Lr.buildings, { style: f => /^BLD-CR/.test(f.properties.id) ? { color: "#ffd23f", weight: 1.6, fillOpacity: 0.12, fillColor: "#ffd23f" } : ({ color: "#ffffff", weight: 1, fillOpacity: 0, opacity: f.properties.sources.length > 1 ? 0.95 : 0.55, dashArray: f.properties.sources.length > 1 ? null : "3 3" }),
    onEachFeature: (f, l) => {
      l.bindTooltip(`${esc(f.properties.id)} · ${f.properties.floors ? f.properties.floors + " floor(s)" : "height unknown"} · ${esc(f.properties.sources.join(" + "))}`, { sticky: true });
      l.on("click", e => {
        if (ED.on || MEAS.on || AREA.on || Date.now() - (AREA.done || 0) < 400 || !f.properties.plot_id || !parcelIndex[f.properties.plot_id]) return;
        L.DomEvent.stopPropagation(e); lastParcelClick = Date.now();
        inspect(f.properties.plot_id, false, { building: f.properties.id });
      });
    } });
  if (nonEmpty("changes")) layers.changes = L.geoJSON(Lr.changes, { style: f => ({ color: CHANGE_COLORS[f.properties.type], weight: 2.5, fillColor: CHANGE_COLORS[f.properties.type], fillOpacity: 0.35, dashArray: f.properties.type === "demolished" ? "5 4" : null }),
    onEachFeature: (f, l) => l.bindTooltip(`${CHANGE_LABELS[f.properties.type]} · ${esc(f.properties.detail)}`, { sticky: true }) });
  if (nonEmpty("utility")) layers.utility = L.geoJSON(Lr.utility, { style: f => /legacy/.test(f.properties.asset_type) ? { color: "#b5179e", weight: 2.5, dashArray: "6 4" } : /sewer/.test(f.properties.asset_type) ? { color: "#8d6e63", weight: 1.8, dashArray: "2 3" } : { color: "#3b82c4", weight: 2 },
    onEachFeature: (f, l) => l.bindTooltip(`${esc(f.properties.id)} · ${esc(f.properties.asset_type)}`, { sticky: true }) });
  if (nonEmpty("connections")) layers.connections = L.geoJSON(Lr.connections, { pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 3.5, color: "#fff", weight: 1, fillColor: deptColor(f.properties.dept), fillOpacity: 1 }),
    onEachFeature: (f, l) => l.bindTooltip(`${esc(deptName(f.properties.dept))} ${esc(f.properties.id)} · ${esc(f.properties.name || "")} · ${esc(f.properties.category || "")} → plot ${esc(f.properties.plot_id || "unlinked")}`) });
  if (nonEmpty("roads")) layers.roads = L.geoJSON(Lr.roads, { style: { color: "#f3d27a", weight: 2, opacity: 0.8 }, onEachFeature: (f, l) => f.properties.name && l.bindTooltip(esc(f.properties.name), { sticky: true }) });
  if (nonEmpty("poi")) layers.poi = L.geoJSON(Lr.poi, { style: { color: "#7cc6a4", weight: 1.2, fillOpacity: 0.12 },
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 4, color: "#fff", weight: 1, fillColor: "#2f8f57", fillOpacity: 1 }),
    onEachFeature: (f, l) => l.bindTooltip(esc(f.properties.name || f.properties.kind)) });
  if (nonEmpty("municipal")) layers.municipal = L.geoJSON(Lr.municipal, { style: { color: "#a78bfa", weight: 1.3, fillOpacity: 0, dashArray: "4 3" },
    onEachFeature: (f, l) => l.bindTooltip(`${esc(f.properties.pid)} · plot ${esc(f.properties.plot_no)}`, { sticky: true }) });
  layers.cadastral_naive = L.geoJSON(Lr.cadastral_naive, { style: { color: "#ff5a5a", weight: 1.3, fillOpacity: 0 } });
  layers.cadastral_georef = L.geoJSON(Lr.cadastral_georef, { style: { color: "#ffc233", weight: 1.3, fillOpacity: 0 } });
  if (nonEmpty("cadastral_alt")) layers.cadastral_alt = L.geoJSON(Lr.cadastral_alt, { style: { color: "#ff5ec4", weight: 1.3, fillOpacity: 0, dashArray: "5 3" },
    onEachFeature: (f, l) => l.bindTooltip(`${esc(f.properties.source || "second survey")} · no. ${esc(f.properties.plot_id || "–")}`, { sticky: true }) });
  if (nonEmpty("ai_buildings")) layers.ai_buildings = L.geoJSON(Lr.ai_buildings, { style: { color: "#ff8f3d", weight: 1.2, fillOpacity: 0 } });
  if (nonEmpty("footprints")) layers.footprints = L.geoJSON(Lr.footprints, { style: { color: "#cbd5e1", weight: 1, fillOpacity: 0, dashArray: "3 3" } });
  if (nonEmpty("gnss")) layers.gnss = L.geoJSON(Lr.gnss, { pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 3.5, color: "#fff", weight: 1, fillColor: "#e0317a", fillOpacity: 1 }),
    onEachFeature: (f, l) => l.bindTooltip(`${esc(f.properties.id)} · plot ${esc(f.properties.plot_id)}`) });
  if (nonEmpty("ground_truth")) layers.ground_truth = L.geoJSON(Lr.ground_truth, { pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 6, color: "#111a22", weight: 1.5, fillColor: "#ffd166", fillOpacity: 1 }),
    onEachFeature: (f, l) => l.bindTooltip(`${esc(f.properties.id || "visit")} · ${esc(f.properties.owner)}`) });
  buildConflictMarkers();
  if (!basemap || !basemapAvailable(basemap)) basemap = R.rasters.ortho_t2 ? "drone" : "satellite";
  applyBasemap();
  applyVisibility();
}

function buildConflictMarkers() {
  if (layers.conflicts) map.removeLayer(layers.conflicts);
  const g = L.layerGroup(), seen = new Set();
  for (const c of R.conflicts) {
    if (c.status !== "open" || !c.center || seen.has(c.plot_id + c.severity)) continue;
    seen.add(c.plot_id + c.severity);
    const m = L.circleMarker(c.center, { radius: c.severity === "high" ? 6 : 4.5, color: "#fff", weight: 1.5, fillColor: c.severity === "high" ? "#c8412f" : "#c98a16", fillOpacity: 1 });
    m.bindTooltip(`${esc(c.title)} · ${esc(c.plot_id)}`);
    m.on("click", () => openConflict(c.id));
    g.addLayer(m);
  }
  layers.conflicts = g;
}

const ORDER = ["dtm", "dsm", "heights_t1", "heights", "@statewide", "footprints", "ai_buildings", "municipal", "cadastral_naive", "cadastral_georef", "cadastral_alt", "roads", "poi", "parcels",
  "buildings", "changes", "proposals", "utility", "connections", "gnss", "ground_truth", "conflicts"];
const deptMeta = d => R?.departments?.departments?.[d] || { short: d, name: d, color: "#74828f" };
const deptColor = d => deptMeta(d).color;
const deptName = d => deptMeta(d).short;
const canDept = d => !!ME && (ME.department === "all" || ME.department === d);
function applyVisibility() {
  const order = ORDER.flatMap(k => k === "@statewide" ? STATEWIDE.map(s => s.key) : [k]);
  STATEWIDE.forEach(s => { if (vis[s.key] && !layers[s.key]) layers[s.key] = statewideLayer(s); });
  for (const k of order) if (layers[k] && map.hasLayer(layers[k])) map.removeLayer(layers[k]);
  for (const k of order) if (vis[k] && layers[k]) layers[k].addTo(map);
  selection.addTo(map); queryLayer.addTo(map);
  renderLayerControl(); renderLegend();
}

function basemapAvailable(k) { return k === "none" || k === "satellite" || k === "streets" || (k === "google" && CFG.google_tiles) || (k === "drone" && R?.rasters?.ortho_t2); }
function applyBasemap() {
  if (baseLayer) map.removeLayer(baseLayer);
  baseLayer = null;
  if (basemap === "drone") {                         // the survey image, with satellite imagery around it for context
    const o = R.rasters[`ortho_${epoch}`] || R.rasters.ortho_t2;
    const img = L.imageOverlay(o.url + `?v=${encodeURIComponent(R.generated_at)}`, o.bounds, { interactive: false,
      attribution: `Drone survey ${esc(o.date || "")}${o.source ? " · " + esc(o.source) : ""}` });
    baseLayer = L.layerGroup([BASEMAPS.satellite(), img]).addTo(map);
    img.bringToBack();
  } else if (BASEMAPS[basemap]) baseLayer = BASEMAPS[basemap]();
  if (baseLayer && !map.hasLayer(baseLayer)) { baseLayer.addTo(map); baseLayer.bringToBack?.(); }
  renderBasemaps();
}
function renderBasemaps() {
  const opts = [["drone", "Drone"], ["satellite", "Satellite"], ["google", "Google"], ["streets", "Map"], ["none", "None"]].filter(([k]) => k === "drone" || basemapAvailable(k));
  const o2 = R?.rasters?.ortho_t2;
  let html = opts.map(([k, l]) => {
    const na = !basemapAvailable(k);
    const tip = k === "drone" ? (na ? "No drone survey for this area yet" : `Drone orthomosaic${o2.date ? ", " + o2.date : ""}${o2.res_m ? ` · ${o2.res_m} m/pixel` : ""}`) : l;
    return `<button class="seg ${basemap === k ? "active" : ""} ${na ? "na" : ""}" data-bm="${k}" title="${esc(tip)}"><i style="${na ? "" : thumbCss(k)}">${na ? "<b>✈</b>" : ""}</i>${l}</button>`;
  }).join("");
  if (basemap === "drone" && R.rasters.ortho_t1) {
    const lbl = e => /^\d{4}/.test(R.rasters["ortho_" + e].date || "") ? R.rasters["ortho_" + e].date.slice(0, 4) : (e === "t1" ? "Earlier" : "Latest");
    html += `<div class="eps"><span class="faint">Survey</span>` + ["t1", "t2"].map(e => `<button class="seg ${epoch === e ? "active" : ""}" data-ep="${e}" title="Survey date">${esc(lbl(e))}</button>`).join("") + `</div>`;
  }
  html += `<div class="drone-note hidden" id="drone-note">
    <b>No drone survey for this area yet.</b> Drone imagery shows each roof at a few centimetres per pixel, sharper than satellite.
    ${can("admin") ? `To add one, upload the orthomosaic as a GeoTIFF (RGB, with its coordinate system), then run the pipeline. Two files with the year in the name (for example <span class="mono">ortho_2024.tif</span>, <span class="mono">ortho_2026.tif</span>) give a before / after switch.<div style="margin-top:8px"><button class="btn small primary" id="drone-upload">Upload drone imagery</button></div>`
      : "Ask an administrator to upload the orthomosaic."}</div>`;
  $("#basemaps").innerHTML = html;
  updateLayersThumb();
  $$("#basemaps [data-bm]").forEach(b => b.onclick = () => {
    if (!basemapAvailable(b.dataset.bm)) { $("#drone-note").classList.remove("hidden"); return; }
    basemap = b.dataset.bm; applyBasemap();
  });
  $("#drone-upload") && ($("#drone-upload").onclick = () => { toggleLayers(false); $("#btn-upload").click(); });
  $$("#basemaps [data-ep]").forEach(b => b.onclick = () => { epoch = b.dataset.ep; applyBasemap(); });
}

function renderLayerControl() {
  let html = "", grp = "";
  for (const [k, label, group] of LAYER_DEFS) {
    if (!layers[k]) continue;
    if (group !== grp) { html += `<div class="grp">${group}</div>`; grp = group; }
    const sim = (k === "dsm" && R.rasters.dsm_t2?.simulated) || (k === "dtm" && R.rasters.dtm?.simulated) || (k.startsWith("heights") && R.elevation?.simulated);
    const date = k === "heights_t1" ? R.rasters.heights_t1?.date : k === "heights" && R.elevation ? R.rasters.heights_t2?.date : null;
    html += `<label><input type="checkbox" data-layer="${k}" ${vis[k] ? "checked" : ""}> ${label}${date ? ` <span class="faint">${esc(date.slice(0, 4))}</span>` : ""}${sim ? ` <span class="prov sim" title="Generated for this prototype: building-level elevation data is not public">sim</span>` : ""}</label>`;
  }
  if (STATEWIDE.length) {
    html += `<div class="grp">State-wide cadastral maps <span class="faint" style="font-weight:400">(zoomed out: parcel density; zoom in for parcels)</span> <span class="prov real" title="Parcels published by state portals (TNGIS, KGIS, NCSCM, APSAC, Bhunaksha), collected by datameet. CC0 1.0 with attribution.">real</span></div>`;
    html += `<div style="margin:0 0 4px"><a href="#" id="sw-all">Show all states (${new Set(STATEWIDE.map(s => s.state)).size})</a></div>`;
    for (const s of STATEWIDE)
      html += `<label><input type="checkbox" data-layer="${s.key}" ${vis[s.key] ? "checked" : ""}> <span><span class="sw-dot" style="background:${s.color}"></span>${esc(s.label)} <a href="#" class="sw-fit" data-swfit="${s.key}" title="Zoom to the whole map">view all</a></span></label>`;
  }
  $("#layer-list").innerHTML = html;
  $("#sw-all") && ($("#sw-all").onclick = e => {
    e.preventDefault();
    const main = Object.values(STATEWIDE.reduce((m, s) => { if (!m[s.state] || s.bytes > m[s.state].bytes) m[s.state] = s; return m; }, {}));
    main.forEach(s => { vis[s.key] = true; });
    applyVisibility();
    const b = main.reduce((a, s) => [Math.min(a[0], s.bounds[0]), Math.min(a[1], s.bounds[1]), Math.max(a[2], s.bounds[2]), Math.max(a[3], s.bounds[3])], [180, 90, -180, -90]);
    map.flyToBounds([[b[1], b[0]], [b[3], b[2]]], { duration: 0.8 });
  });
  $$("[data-swfit]").forEach(a => a.onclick = e => { e.preventDefault(); e.stopPropagation(); const s = STATEWIDE.find(x => x.key === a.dataset.swfit); vis[s.key] = true; applyVisibility(); const b = s.bounds; map.flyToBounds([[b[1], b[0]], [b[3], b[2]]], { duration: 0.8 }); });
  $$("#layer-list input").forEach(i => i.onchange = () => { vis[i.dataset.layer] = i.checked; applyVisibility(); });
}

function renderLegend() {
  let html = `<div class="ttl">Parcel status</div>` + Object.keys(STATUS_COLORS).map(s => `<div class="row"><span class="dot" style="background:${STATUS_COLORS[s]}"></span>${STATUS_LABELS[s]}</div>`).join("");
  if (vis.changes && layers.changes) html += `<div class="ttl" style="margin-top:6px">Changes</div>` + Object.keys(CHANGE_COLORS).map(t => `<div class="row"><span class="dot" style="background:${CHANGE_COLORS[t]}"></span>${CHANGE_LABELS[t]}</div>`).join("");
  $("#legend").innerHTML = html;
}

// ------------------------------------------------------------------ state-wide cadastral maps (PMTiles vector tiles)
let STATEWIDE = [];
const SW_COLORS = ["#ff9f43", "#48dbfb", "#ff6b9a", "#feca57", "#1dd1a1", "#a29bfe", "#ff7675", "#55efc4", "#fab1a0", "#74b9ff"];
async function loadStatewide() {
  try {
    const cat = await api("/api/statewide");
    STATEWIDE = cat.map((s, i) => ({ ...s, key: "sw:" + s.id, color: SW_COLORS[i % SW_COLORS.length],
      label: `${s.state_name}: ${(s.description || s.file).replace(new RegExp("^" + s.state_name + "\\s*", "i"), "")}` }));
    STATEWIDE.forEach(s => { if (!(s.key in vis)) vis[s.key] = false; });
    renderLayerControl();
  } catch { STATEWIDE = []; }
}
function statewideLayer(s) {
  const line = new protomapsL.LineSymbolizer({ color: s.color, width: z => z < 10 ? 0.4 : z < 14 ? 0.8 : 1.3, opacity: 0.9 });
  const fill = new protomapsL.PolygonSymbolizer({ fill: s.color, opacity: 0.06 });
  const rules = (s.vector_layers.length ? s.vector_layers : [{ id: undefined }]).flatMap(v => [
    { dataLayer: v.id, symbolizer: fill, minzoom: 13 }, { dataLayer: v.id, symbolizer: line }]);
  const tiles = protomapsL.leafletLayer({ url: s.url, maxDataZoom: s.max_zoom, paintRules: rules, labelRules: [], pane: "overlayPane",
    attribution: `Cadastrals © state land-records portals via <a href="https://github.com/ramSeraph/indian_cadastrals" target="_blank" rel="noopener">datameet</a> (CC0)` });
  const group = L.layerGroup([tiles]);
  group.tiles = tiles;
  if (s.overview) {                      // zoomed out: parcel density from the tile index; zoomed in: the parcels
    const ov = L.imageOverlay(s.overview.url, s.overview.bounds, { opacity: 0.75, interactive: false, className: "sw-overview" });
    const sync = () => { const far = map.getZoom() < SW_DETAIL_ZOOM; ov.setOpacity(far ? 0.75 : 0); tiles.setOpacity(far ? 0.35 : 1); };
    group.addLayer(ov);
    group.on("add", () => { map.on("zoomend", sync); sync(); });
    group.on("remove", () => map.off("zoomend", sync));
  }
  return group;
}
const SW_DETAIL_ZOOM = 11;
let lastParcelClick = 0;
map.on("click", e => {
  if (ED.on || MEAS.on || Date.now() - lastParcelClick < 400) return;
  const hits = [];
  for (const s of STATEWIDE) {
    const l = layers[s.key]?.tiles;
    if (!l || !map.hasLayer(layers[s.key]) || map.getZoom() < SW_DETAIL_ZOOM) continue;
    for (const [, feats] of l.queryTileFeaturesDebug(e.latlng.lng, e.latlng.lat, 2))
      for (const f of feats) if (f.feature?.props) hits.push({ s, props: f.feature.props });
  }
  if (!hits.length) return;
  const { s, props } = hits[0];
  const rows = Object.entries(props).filter(([, v]) => v !== "" && v != null).slice(0, 18)
    .map(([k, v]) => `<tr><td class="faint">${esc(k)}</td><td>${esc(v)}</td></tr>`).join("");
  L.popup({ maxWidth: 340 }).setLatLng(e.latlng).setContent(`<div class="sw-pop"><b>${esc(s.state_name)} cadastral parcel</b> <span class="prov real">real</span>
    <table>${rows}</table><div class="faint">${esc(s.source || "")}${s.source_url ? ` · <a href="${esc(s.source_url)}" target="_blank" rel="noopener">source</a>` : ""} · via datameet (CC0). Shown from vector tiles (zoom ≤ ${s.max_zoom}, about ±2–3 m at street level); not part of this area's harmonised records.</div></div>`).openOn(map);
});

// ------------------------------------------------------------------ inspector
const parcelProps = pid => R.layers.parcels.features.find(f => f.properties.plot_id === pid)?.properties;

function inspect(pid, fly = false, opt = {}) {
  const p = parcelProps(pid);
  if (!p) return;
  const layer = parcelIndex[pid];
  selection.clearLayers();
  if (layer) {
    L.geoJSON(layer.toGeoJSON(), { style: { color: "#0f5e74", weight: 4, fillOpacity: 0 }, interactive: false }).addTo(selection);
    const bf = opt.building && R.layers.buildings.features.find(b => b.properties.id === opt.building);
    if (bf) L.geoJSON(bf, { style: { color: "#ff8a00", weight: 3, fillColor: "#ff8a00", fillOpacity: 0.25 }, interactive: false }).addTo(selection);
    if (fly) map.flyToBounds(layer.getBounds().pad(1.6), { duration: 0.6, maxZoom: 21 });
  }
  const comp = Object.entries(p.components).map(([k, v]) => {
    const c = v >= 0.85 ? STATUS_COLORS.approved : v >= 0.7 ? STATUS_COLORS.flagged : STATUS_COLORS.review;
    return `<div class="comp"><span>${esc(R.component_labels[k])} <span class="faint">× ${R.weights[k]}</span></span><div class="track"><div class="fill" style="width:${v * 100}%;background:${c}"></div></div><span class="val">${Math.round(v * 100)}</span></div>`;
  }).join("");
  const cf = R.conflicts.filter(c => c.plot_id === pid);
  const kv = (k, v) => v == null || v === "" ? "" : `<div class="k">${k}</div><div class="v">${v}</div>`;
  const capped = p.score < p.score_base ? `<div class="faint" style="font-size:12px">Capped from ${num(p.score_base)} while conflicts are open</div>` : "";
  $("#inspector").innerHTML = `
    <div class="insp-head"><div><div class="pid">${esc(pid)}</div><span class="status ${p.status}">${STATUS_LABELS[p.status]}</span></div>
      <button class="close" id="insp-close" aria-label="Close">×</button></div>
    ${opt.building ? buildingBlock(opt.building, pid) : ""}
    <div class="score"><span class="n">${Math.round(p.score)}</span><span class="muted">/ 100 overall confidence</span></div>${capped}
    <details class="why"><summary>What do these numbers mean?</summary>
      <p><b>Overall confidence</b> (${Math.round(p.score)}/100) is how sure the system is that this plot's whole record is right: boundary, area, owner, match and topology, weighted as shown below. While a conflict is open it is capped, so a disputed plot never looks finished.</p>
      ${p.municipal_pid ? `<p><b>Match confidence</b> (${pct(p.match_prob)}) only says that the municipal record and the survey plot are the same property. It can be 100% while their contents still disagree; that disagreement is what lowers the overall score.</p>` : ""}</details>
    <div style="margin-top:8px">${comp}</div>
    <div class="kv">
      ${kv("Owner", p.resolved_owner ? `<b>${esc(p.resolved_owner)}</b><br><span class="faint">${esc(p.owner_source)}</span>` : `<span class="bad-text">${esc(p.owner_source)}</span>`)}
      ${kv("RoR owner", esc(p.ror_owner))}${kv("Municipal owner", esc(p.municipal_owner))}${kv("Field-verified", esc(p.gt_owner))}
      ${kv("Surveyed area", `<span class="mono">${num(p.area_sqm)} m²</span>`)}${kv("RoR extent", p.ror_area_sqm ? `<span class="mono">${num(p.ror_area_sqm)} m²</span>` : null)}
      ${kv("Land use", esc(p.land_use))}${kv("Khata · PID", [p.khata, p.municipal_pid].filter(Boolean).map(esc).join(" · "))}
      ${kv("ULPIN-format ID", p.ulpin ? `<span class="mono" title="14-character national parcel ID in the ULPIN (Bhu-Aadhaar) format: state code + an encoding of the parcel's location. The official ULPIN is issued by the state.">${esc(p.ulpin)}</span>` : null)}
      ${kv("Tax at risk", (() => { const t = cf.filter(c => c.status !== "resolved" && c.revenue_inr).reduce((a, c) => Math.max(a, c.revenue_inr), 0); return t ? `<span class="bad-text">${inr(t)} a year</span> <span class="faint">(estimate)</span>` : null; })())}
      ${kv("GNSS corners", p.gnss_vertices || null)}${kv("Match confidence", p.municipal_pid ? `<span class="mono" title="Same property in both registers? Not whether their data agree.">${pct(p.match_prob)}</span> <span class="faint">(identity)</span>` : null)}
    </div>
    ${deptSection(p, pid, opt)}
    <div class="section-title">Sources fused</div>${p.sources.filter(s => !/\bNone\b/.test(s)).map(s => `<div class="src">${esc(s)}${srcTag(s)}</div>`).join("")}
    ${pathToApproval(p)}
    ${chainSection(p)}
    ${(p.updates || []).length ? `<div class="section-title">Official updates</div>${p.updates.map(u => `<div class="upd"><b>${esc(u.label)}</b> → ${esc(u.value)}<div class="faint">${esc(u.request)} · proposed by ${esc(u.proposed_by)} · approved by ${esc(u.by)} · ${esc((u.at || "").replace("T", " "))}</div></div>`).join("")}` : ""}
    ${p.topo_flags.length ? `<div class="section-title">Topology fixes</div>${p.topo_flags.map(s => `<div class="src">${esc(s)}</div>`).join("")}` : ""}
    ${cf.length ? `<div class="section-title">Conflicts</div>${cf.map(c => `<div class="item ${c.severity}" data-cid="${esc(c.id)}"><div class="top"><span class="ct">${esc(c.title)}</span><span class="sev ${c.severity}">${c.status === "open" ? c.severity : c.status.replace("_", " ")}</span></div></div>`).join("")}` : ""}
    ${mappingSection(pid)}
    <div style="margin-top:12px;display:flex;gap:6px;flex-wrap:wrap"><button class="btn small" id="insp-lineage">${I.history} Decision history</button>
      ${myFields().length ? `<button class="btn small primary" id="insp-propose">Propose a change</button>` : ""}
      ${canMap() ? `<button class="btn small" id="insp-draw">Map a building</button><button class="btn small" id="insp-reshape">Edit boundary</button>` : ""}
      ${can("reviewer") ? `<a class="btn small" href="${reportUrl(pid)}">${I.download} PDF report</a>` : ""}</div>`;
  $("#inspector").classList.remove("hidden");
  $("#insp-close").onclick = closeInspector;
  bindDeptSection(p, pid);
  $("#bld-plot") && ($("#bld-plot").onclick = e => { e.preventDefault(); inspect(pid); });
  $("#insp-lineage").onclick = () => { auditFilter = { subject: pid, kind: "" }; setTab("audit"); };
  $("#insp-propose") && ($("#insp-propose").onclick = () => openProposal(pid));
  $("#insp-draw") && ($("#insp-draw").onclick = () => startEditor(pid, "building_add"));
  $("#insp-reshape") && ($("#insp-reshape").onclick = () => startEditor(pid, "geometry"));
  $$("[data-bedit]").forEach(b => b.onclick = () => startEditor(pid, "building_edit", b.dataset.bedit));
  $$("[data-brm]").forEach(b => b.onclick = () => openProposal(pid, "building_remove", b.dataset.brm));
  $$("[data-pshow]").forEach(b => b.onclick = () => showProposed(PROPOSALS.find(r => String(r.id) === b.dataset.pshow)));
  $$("[data-dres]").forEach(b => b.onclick = () => { const [k, t] = b.dataset.dres.split("|"); startEditor(pid, k, t || null); });
  $$("[data-bh]").forEach(el => {
    let hl = null;
    el.onmouseenter = () => { const f = R.layers.buildings.features.find(x => x.properties.id === el.dataset.bh); if (f) hl = L.geoJSON(f, { style: { color: "#ffd23f", weight: 3, fillOpacity: 0.3, fillColor: "#ffd23f" }, interactive: false }).addTo(selection); };
    el.onmouseleave = () => { if (hl) selection.removeLayer(hl); hl = null; };
  });
  $$("#inspector .item").forEach(el => el.onclick = () => openConflict(el.dataset.cid));
}
function mappingSection(pid) {
  const blds = plotBuildings(pid), pend = PROPOSALS.filter(r => r.plot_id === pid), drafts = canMap() ? listDrafts(pid) : [];
  if (!blds.length && !pend.length && !drafts.length && !canMap()) return "";
  return `<div class="section-title">Buildings and map changes</div>`
    + blds.map(b => `<div class="bld-row" data-bh="${esc(b.properties.id)}"><div><span class="mono">${esc(b.properties.id)}</span> · ${num(b.properties.area_sqm, 0)} m²${b.properties.floors ? ` · ${b.properties.floors} floor(s)` : ""}${b.properties.height_m ? `<div class="faint">${num(b.properties.height_m, 1)} m tall${b.properties.height_source ? ` · ${esc(b.properties.height_source)}` : ""}</div>` : ""}</div>${canMap() ? `<div class="row-btns"><button class="btn small" data-bedit="${esc(b.properties.id)}">Edit</button><button class="btn small danger" data-brm="${esc(b.properties.id)}">Remove</button></div>` : ""}</div>`).join("")
    + (blds.length ? "" : `<div class="faint" style="font-size:12px;padding:4px 0">No building mapped on this plot.</div>`)
    + pend.map(r => `<div class="bld-row pend"><div><b class="mono">${esc(r.code)}</b> ${esc(r.new_display)}<div class="faint">awaiting approval · ${esc(r.proposed_by)}</div></div><button class="btn small" data-pshow="${r.id}">Show</button></div>`).join("")
    + drafts.map(d => `<div class="bld-row draft"><div>Unsent draft: ${esc(ED_KIND[d.kind]?.label || d.kind)}${d.target ? " " + esc(d.target) : ""}<div class="faint">${esc(ago(d.at))} · this device</div></div><button class="btn small primary" data-dres="${esc(d.kind)}|${esc(d.target || "")}">Resume</button></div>`).join("");
}
const inr = n => n >= 1e7 ? `₹${num(n / 1e7, 2)} crore` : n >= 1e5 ? `₹${num(n / 1e5, 1)} lakh` : `₹${num(n, 0)}`;
const COMP_HELP = { G: "survey the corners with GNSS (DGPS)", A: "correct the recorded extent or names so the registers agree",
  S: "add a field-verification visit or a GNSS survey", M: "confirm the link to the municipal record", T: "fix the boundary's topology" };
function pathToApproval(p) {
  if (p.status === "approved" || p.status === "verified") return "";
  const T = R.thresholds, open = R.conflicts.filter(c => c.plot_id === p.plot_id && c.status === "open");
  const steps = [];
  if (open.length && p.score < p.score_base)
    steps.push(`<li>Resolve ${open.length === 1 ? "the open conflict" : `the ${open.length} open conflicts`} (${[...new Set(open.map(c => c.title))].map(esc).join("; ")})${[...new Set(open.flatMap(c => c.departments || []))].length ? ` with ${[...new Set(open.flatMap(c => c.departments || []))].map(deptName).map(esc).join(", ")}` : ""}: <b>${Math.round(p.score)} → ${Math.round(p.score_base)}</b></li>`);
  let s = p.score_base;
  if (s < T.auto_approve) {
    const gains = Object.entries(p.components).map(([k, v]) => [k, (1 - v) * R.weights[k] * 100]).filter(([, g]) => g >= 1).sort((a, b) => b[1] - a[1]);
    for (const [k, g] of gains) {
      if (s >= T.auto_approve) break;
      steps.push(`<li>${esc(COMP_HELP[k] || R.component_labels[k])}: up to <b>+${Math.round(g)}</b> (${esc(R.component_labels[k].toLowerCase())} is ${Math.round(p.components[k] * 100)}/100)</li>`);
      s += g;
    }
  }
  if (!steps.length) return "";
  const goal = p.score_base >= T.auto_approve ? "auto-approved" : s >= T.auto_approve ? "auto-approved" : "approved with a flag";
  return `<div class="section-title">Path to approval</div><div class="block path"><div class="faint" style="font-size:12px;margin-bottom:4px">Needs ${T.auto_approve} to be auto-approved (now ${Math.round(p.score)}). To get this plot ${goal}:</div><ol>${steps.join("")}</ol></div>`;
}
function chainSection(p) {
  const ch = p.ownership_chain;
  if (!ch || (!ch.steps.length && ch.verdict === "No registered deed on record")) return "";
  return `<div class="section-title">Ownership chain</div><div class="block chain">
    ${ch.steps.map(s => `<div class="link ${s.ok ? "" : "bad"}"><span class="mono faint">${esc(s.date || "")}</span><div>${esc(s.from || "?")} → <b>${esc(s.to)}</b> <span class="faint">· deed ${esc(s.deed || "")}</span>${s.issue ? `<div class="bad-text" style="font-size:11.5px">${esc(s.issue)}</div>` : ""}</div></div>`).join("")}
    <div class="link now"><span class="mono faint">RoR now</span><div><b>${esc(p.ror_owner || "–")}</b></div></div>
    <div class="verdict ${ch.ok ? "ok" : "bad"}">${ch.ok ? "✓" : "!"} ${esc(ch.verdict)}</div></div>`;
}
function closeInspector() { $("#inspector").classList.add("hidden"); selection.clearLayers(); }

// ------------------------------------------------------------------ tabs
$$("#tabs button").forEach(b => b.onclick = () => setTab(b.dataset.tab));
function setTab(t) {
  tab = t;
  if (!$("#inspector").classList.contains("hidden")) closeInspector();     // the list comes back, like leaving a place card
  document.body.classList.add("panel-on"); document.body.classList.remove("collapsed"); syncSide();
  $$("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === t));
  if (t === "changes" && layers.changes && !vis.changes) { vis.changes = true; applyVisibility(); }
  render();
}

function render() {
  if (tab === "profile") { $("#tab-body").innerHTML = renderProfileShell(); loadProfile(); return; }
  if (!R) return;
  $("#n-review").textContent = R.summary.conflicts.open;
  const body = $("#tab-body");
  const fn = { overview: renderOverview, data: renderData, review: renderReview, changes: renderChanges, accuracy: renderAccuracy, audit: renderAuditShell, results: renderResults, admin: renderAdminShell, requests: renderRequestsShell }[tab];
  body.innerHTML = fn();
  if (tab === "review") bindReview();
  if (tab === "overview") $("#prov-link") && ($("#prov-link").onclick = e => { e.preventDefault(); setTab("data"); });
  if (tab === "changes") $$(".change-item").forEach(el => el.onclick = () => zoomTo(R.layers.changes.features[+el.dataset.i]));
  if (tab === "audit") loadAudit();
  if (tab === "results") bindResults();
  if (tab === "data") bindData();
  if (tab === "accuracy" && can("reviewer")) loadLearning();
  if (tab === "admin") loadAdmin();
  if (tab === "requests") loadRequests();
}

function renderOverview() {
  const s = R.summary, ev = R.evaluation, meta = R.workspace_meta || {};
  const fused = ev?.positional?.["Fused output (GNSS + topology)"], muni = ev?.positional?.["Municipal layer as delivered"];
  const st = s.status, n = s.parcels;
  const stack = Object.keys(STATUS_COLORS).map(k => st[k] ? `<div style="width:${st[k] / n * 100}%;background:${STATUS_COLORS[k]}" title="${STATUS_LABELS[k]}: ${st[k]}"></div>` : "").join("");
  const src = R.datasets.filter(d => !["raster_meta", "unknown"].includes(d.role));
  const moved = (R.coregistration || []).filter(c => c.applied);
  const steps = (R.timings || []).map((t, i) => `<div class="step"><span class="n">${String(i + 1).padStart(2, "0")}</span>
      <div><div class="t">${esc(t.title)}</div><ul>${t.log.map(l => `<li>${esc(l)}</li>`).join("")}</ul></div><span class="sec mono">${num(t.seconds, 2)} s</span></div>`).join("");
  return `
  ${R.names_masked ? `<div class="block mask-note">Personal names are masked for your account (viewer). Officers and approvers see full names.</div>` : ""}
  ${meta.description ? `<p class="lead">${esc(meta.description)} <a href="#" id="prov-link">What is real?</a></p>` : ""}
  <div class="hero"><div class="v">${num(s.no_manual_work_pct)}%</div>
    <div class="l">of parcels finalised with no manual GIS work</div>
    <div class="s">${st.approved} auto-approved · ${st.flagged} approved with a flag · ${st.review} sent to review${st.verified ? ` · ${st.verified} verified` : ""}</div></div>
  <div class="stats">
    <div class="stat"><div class="v">${n}</div><div class="l">Parcels harmonised</div><div class="s">${src.length} files, ${new Set(src.map(d => d.format)).size} formats</div></div>
    <div class="stat"><div class="v">${s.conflicts.open}</div><div class="l">Conflicts to review</div><div class="s">${s.conflicts.auto_resolved} auto-resolved · ${s.conflicts.resolved} decided</div></div>
    <div class="stat"><div class="v">${s.mean_match_confidence != null ? pct(s.mean_match_confidence) : "–"}</div><div class="l">Mean match confidence</div><div class="s">${R.match_stats ? `${R.match_stats.linked_municipal} municipal links` : ""}</div></div>
    <div class="stat"><div class="v">${fused ? num(fused.mean_m, 2) + " m" : "–"}</div><div class="l">Boundary error</div><div class="s">${muni ? `was ${num(muni.mean_m, 2)} m in the municipal layer` : "measured only where truth exists"}</div></div>
    <div class="stat"><div class="v">${num(R.total_seconds, 1)} s</div><div class="l">Processing time</div><div class="s">${s.buildings} buildings fused</div></div>
    ${R.revenue_at_risk ? `<div class="stat" title="${esc(R.revenue_at_risk.assumptions)}"><div class="v bad-text">${inr(R.revenue_at_risk.total_inr_per_year)}</div><div class="l">Property tax at risk a year</div><div class="s">${R.revenue_at_risk.plots} plots · estimate, hover for assumptions</div></div>` : ""}
    <div class="stat"><div class="v">${R.audit?.ok ? "Intact" : "Broken"}</div><div class="l">Audit chain</div><div class="s">${R.audit?.entries ?? 0} hash-linked entries</div></div>
  </div>
  ${effortBlock()}
  <div class="section-title">Parcel status</div>
  <div class="stack">${stack}</div>
  <div class="legend-row">${Object.keys(STATUS_COLORS).map(k => `<span><span class="dot" style="background:${STATUS_COLORS[k]}"></span>${STATUS_LABELS[k]} <span class="mono">${st[k]}</span></span>`).join("")}</div>
  <p class="faint" style="font-size:12px">Score ≥ ${R.thresholds.auto_approve} is auto-approved, ${R.thresholds.flag} to ${R.thresholds.auto_approve - 1} is approved with a flag, and below ${R.thresholds.flag} goes to a reviewer. Open conflicts cap the score.</p>
  ${moved.length ? `<div class="section-title">Layer shifts found and corrected</div><div class="block">${moved.map(c => `<div style="font-size:12.5px;margin:3px 0"><b>${esc(c.layer)}</b> was off by <span class="mono">${num(c.shift_m, 2)} m</span> against the ${esc(c.reference)}</div>`).join("")}</div>` : ""}
  <div class="section-title">What the pipeline did</div>
  <div>${steps}</div>`;
}

// Real or simulated? Decided from the workspace's own source list (licence "synthetic" = simulated here).
function provenance(name) {
  const meta = R.workspace_meta || {};
  if (meta.kind !== "real-world") return { real: false, note: "Synthetic benchmark ward: every file is generated, so the truth is known" };
  const s = (meta.sources || []).find(x => (x.files || []).includes(name));
  if (s && s.license !== "synthetic") return { real: true, note: `${s.name} · ${s.license}` };
  return { real: false, note: s ? s.name : "Simulated for this prototype: real records of this kind are not openly available" };
}
const provTag = name => { const p = provenance(name); return `<span class="prov ${p.real ? "real" : "sim"}" title="${esc(p.note)}">${p.real ? "Real" : "Simulated"}</span>`; };
function srcTag(s) {
  if ((R.workspace_meta || {}).kind !== "real-world") return "";
  if (/footprint|OSM|OpenStreetMap|Microsoft|AI building/i.test(s))
    return /elevation|DSM/i.test(s) ? ` <span class="prov real">real outlines</span> <span class="prov sim">simulated heights</span>` : ` <span class="prov real">real</span>`;
  return ` <span class="prov sim">simulated</span>`;
}
function provenanceBlock() {
  const meta = R.workspace_meta || {};
  const files = R.datasets.filter(d => !["raster_meta", "unknown"].includes(d.role)).map(d => ({ d, p: provenance(d.name) }));
  const real = files.filter(x => x.p.real), sim = files.filter(x => !x.p.real);
  const extra = (meta.simulated_notes || []).map(n => `<li>${esc(n)}</li>`).join("");
  return `<div class="section-title">What is real and what is simulated</div><div class="block prov-block">
    ${meta.kind === "real-world" ? `<div><span class="prov real">Real</span> ${real.map(x => esc(x.d.name)).join(", ") || "none"}${meta.real_notes ? `<div class="faint">${esc(meta.real_notes)}</div>` : ""}</div>
    <div style="margin-top:6px"><span class="prov sim">Simulated</span> ${sim.map(x => esc(x.d.name)).join(", ")}</div>` :
    `<div><span class="prov sim">Simulated</span> This is a synthetic benchmark ward: every file is generated, so the true answer is known and accuracy can be scored exactly.</div>`}
    ${extra ? `<ul class="faint" style="margin:6px 0 0 16px;padding:0">${extra}</ul>` : ""}</div>`;
}

function cadCompare() {
  const c = R.cadastral_comparison;
  if (!c) return "";
  return `<div class="block"><table class="t"><tr><th>Two real cadastral surveys compared <span class="prov real">real</span></th><th class="num">Result</th></tr>
    <tr><td>Parcels paired with ${esc(c.source || "the second survey")}</td><td class="num mono">${c.paired} / ${c.parcels}</td></tr>
    <tr><td>Mean overlap (IoU) of paired parcels</td><td class="num mono">${pct(c.mean_iou, 0)}</td></tr>
    <tr><td>Overlap 80% or more</td><td class="num mono">${pct(c.strong_overlap, 0)}</td></tr>
    <tr><td>Median boundary offset</td><td class="num mono">${num(c.median_offset_m, 2)} m</td></tr>
    <tr><td>Boundaries within 1 m</td><td class="num mono">${pct(c.agree_within_1m, 0)}</td></tr>
    <tr><td>Survey numbers agree (where the second survey has one)</td><td class="num mono">${pct(c.numbers_agree, 0)}</td></tr></table>
    <div class="faint" style="font-size:11.5px;margin-top:6px">Both layers are government surveys of the same land. Where they disagree the pipeline raises “Two cadastral surveys disagree” for the Survey department. There is no answer key: this is a measurement of the real records.</div></div>`;
}
function renderData() {
  const crsBy = Object.fromEntries((R.crs_report || []).map(c => [c.dataset, c]));
  const ds = R.datasets.filter(d => d.role !== "raster_meta").map(d => {
    const crs = crsBy[d.name];
    const maps = d.schema ? d.schema.mappings.map(m => { const need = m.needs_confirmation ?? (m.confidence < 90 && !m.confirmed_by);
      return `<div class="map-row ${need ? "need" : ""}"><span class="col">${esc(m.column)}</span><span class="faint">→</span>
        <span>${esc(m.label)}${m.unit ? ` <span class="faint">(${esc(m.unit)})</span>` : ""}</span><span class="conf">${m.confirmed_by ? "✓" : Math.round(m.confidence) + "%"}</span><span class="ev">${esc(m.evidence)}</span>
        ${need ? `<div class="confirm-row"><b>Please confirm.</b> ${can("reviewer") ? `<button class="btn small ok" data-cf="${esc(d.name)}|${esc(m.column)}|${esc(m.field)}">Correct</button>
          <select data-cfch="${esc(d.name)}|${esc(m.column)}" aria-label="Map to another field"><option value="">Change to…</option>${Object.entries(R.schema_fields || {}).filter(([k]) => k !== m.field).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}<option value="-">Not mapped</option></select>` : `<span class="faint">An approver confirms uncertain mappings.</span>`}</div>` : ""}</div>`; }).join("") : "";
    const unm = d.schema?.unmapped?.length ? `<div class="faint" style="font-size:11.5px;margin-top:4px">Kept as extra attributes: ${d.schema.unmapped.map(esc).join(", ")}</div>` : "";
    return `<div class="ds"><div class="ds-head"><span class="ds-name">${esc(d.name)}</span><span class="ds-tags">${provTag(d.name)}<span class="role">${esc(d.role_label)}</span></span></div>
      <div class="ds-meta">${esc(d.format)}${d.features ? ` · ${d.features} features` : ""}${d.geom_type ? ` · ${esc(d.geom_type)}` : ""}</div>
      ${crs ? `<div class="crs">CRS <b>${esc(crs.detected)}</b> → ${esc(R.target_crs.split(" (")[0])} <span class="faint">· ${esc(crs.method)}</span></div>` : ""}${maps}${unm}</div>`;
  }).join("");
  const g = R.georef, t = R.topology, rm = g?.rmse_m || {};
  const first = rm.translation_only != null ? ["Dragged into place (translation only)", rm.translation_only] : ["Reprojected from the declared CRS", rm.reprojected_only];
  const geo = g?.rmse_m ? `<div class="block"><table class="t"><tr><th>Cadastral sheet vs GNSS control</th><th class="num">RMSE</th></tr>
      <tr><td>${first[0]}</td><td class="num mono">${num(first[1], 2)} m</td></tr>
      <tr><td>Affine fit on parcel centres</td><td class="num mono">${num(rm.coarse_affine, 2)} m</td></tr>
      <tr><td>Refined on ${g.control_points_used} GNSS points</td><td class="num mono ok-text">${num(rm.refined_affine, 2)} m</td></tr></table>
      <div class="faint" style="font-size:12px;margin-top:6px">${esc(g.method)}. Rotation ${num(g.transform.rotation_deg, 3)}°, scale ${g.transform.scale_x}.</div></div>` : "";
  const co = (R.coregistration || []).length ? `<div class="block"><table class="t"><tr><th>Layer co-registration</th><th class="num">Shift</th><th class="num">Applied</th></tr>
      ${R.coregistration.map(c => `<tr><td>${esc(c.layer)}<div class="faint" style="font-size:11px">${esc(c.method)}</div></td><td class="num mono">${num(c.shift_m, 2)} m</td><td class="num">${c.applied ? '<span class="ok-text">yes</span>' : "no"}</td></tr>`).join("")}</table></div>` : "";
  const ba = R.building_agreement ? `<div class="block"><table class="t"><tr><th>Building layers compared</th><th class="num">Count</th></tr>
      <tr><td>AI footprints confirmed by ${esc(R.building_agreement.reference.join(", "))}</td><td class="num mono">${R.building_agreement.confirmed_by_reference} / ${R.building_agreement.primary_count}</td></tr>
      <tr><td>Only in the AI layer</td><td class="num mono">${R.building_agreement.primary_only}</td></tr>
      <tr><td>Only in the reference layer</td><td class="num mono">${R.building_agreement.reference_only}</td></tr>
      <tr><td>Mean overlap (IoU) of confirmed pairs</td><td class="num mono">${num(R.building_agreement.mean_iou_confirmed, 2)}</td></tr></table></div>` : "";
  const topo = t ? `<div class="block"><table class="t"><tr><th>Topology correction</th><th class="num">Count</th></tr>
      <tr><td>Corners snapped to GNSS</td><td class="num mono">${t.gnss_snapped_vertices}</td></tr>
      <tr><td>Shared corners merged</td><td class="num mono">${t.corners_merged}</td></tr>
      <tr><td>Overlaps resolved</td><td class="num mono">${t.overlaps_resolved}</td></tr>
      <tr><td>Gaps filled</td><td class="num mono">${t.gaps_filled}</td></tr>
      <tr><td>Self-intersections repaired</td><td class="num mono">${t.invalid_repaired.length}</td></tr>
      <tr><td>Duplicates removed</td><td class="num mono">${t.duplicates_removed.length}</td></tr>
      <tr><td>Overlaps / invalid shapes left</td><td class="num mono ok-text">${t.overlaps_remaining} / ${t.invalid_remaining}</td></tr></table></div>` : "";
  const exportLayers = Object.keys(R.layers).filter(k => R.layers[k].features.length);
  const sources = (R.workspace_meta?.sources || []).map(s => `<div style="font-size:12.5px;margin:4px 0">${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>` : esc(s.name)} <span class="faint">· ${esc(s.license)}</span></div>`).join("");
  const nNeed = R.datasets.reduce((a, d) => a + (d.schema?.mappings || []).filter(m => m.needs_confirmation ?? (m.confidence < 90 && !m.confirmed_by)).length, 0);
  return `${provenanceBlock()}<div class="section-title">Datasets received</div>${nNeed ? `<div class="block need-banner"><b>${nNeed}</b> column mapping${nNeed > 1 ? "s" : ""} below ${nNeed > 1 ? "are" : "is"} less than 90% certain. ${can("reviewer") ? "Confirm or correct them; the next pipeline run uses your answer." : "An approver will confirm them."}</div>` : ""}${ds}
    <div class="section-title">Harmonisation</div>${geo}${co}${ba}${cadCompare()}${topo}
    <div class="section-title">Export</div>
    <div class="block"><div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
      <select id="ex-layer" class="ws-select" style="width:auto" aria-label="Layer">${exportLayers.map(k => `<option value="${k}">${k}</option>`).join("")}</select>
      <select id="ex-fmt" class="ws-select" style="width:auto" aria-label="Format"><option value="geojson">GeoJSON</option><option value="csv">CSV (WKT)</option><option value="kml">KML</option><option value="gpkg">GeoPackage</option><option value="shp">Shapefile (zip)</option></select>
      <button class="btn small" id="ex-go">${I.download} Download</button></div>
      ${can("reviewer") ? `<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:10px"><input id="rep-plot" class="ws-select" style="width:150px" placeholder="Plot, e.g. 163/13" list="rep-plots">
        <datalist id="rep-plots">${R.layers.parcels.features.slice(0, 2000).map(f => `<option value="${esc(f.properties.plot_id)}">`).join("")}</datalist>
        <button class="btn small" id="rep-go">${I.download} Property report (PDF)</button><span class="faint" style="font-size:12px">All departments' records for one plot</span></div>` : ""}
      <div class="links" style="margin-top:10px"><a class="btn small" href="${wsUrl("/api/export/ladm.gpkg")}">${I.download} LADM package (ISO 19152)</a>
      <a class="btn small" href="${wsUrl("/ogc/collections")}" target="_blank" rel="noopener">${I.link} OGC API – Features</a>
      <a class="btn small" href="/docs" target="_blank" rel="noopener">${I.link} REST API</a></div></div>
    ${sources ? `<div class="section-title">Data sources and licences</div><div class="block">${sources}</div>` : ""}
    ${STATEWIDE.length ? `<div class="section-title">State-wide cadastral maps</div><div class="block">${STATEWIDE.map(s => `<div style="font-size:12.5px;margin:4px 0"><span class="sw-dot" style="background:${s.color}"></span><b>${esc(s.state_name)}</b>: ${esc(s.description || s.file)} · ${esc(s.source || "")}${s.source_url ? ` (<a href="${esc(s.source_url)}" target="_blank" rel="noopener">portal</a>)` : ""} · ${num(s.tiles, 0)} tiles, zoom ${s.min_zoom}–${s.max_zoom} · <span class="faint">${esc(s.license || "CC0 1.0, attribute datameet and the source")}</span></div>`).join("")}
      <div class="faint" style="font-size:11.5px;margin-top:6px">Collected from state portals by datameet (<a href="https://github.com/ramSeraph/indian_cadastrals" target="_blank" rel="noopener">indian_cadastrals</a>). The maintainer lists them as "not-so-open": the portals' own reuse terms are not stated, so confirm with the state before production use. Add more states with <span class="mono">python -m landconnect.statewide fetch &lt;state&gt;</span>.</div></div>` : ""}`;
}
const reportUrl = pid => wsUrl(`/api/parcels/${pid.split("/").map(encodeURIComponent).join("/")}/report.pdf`);
async function confirmMapping(dataset, column, field) {
  try {
    const r = await api("/api/schema/confirm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dataset, column, field }) });
    const i = R.datasets.findIndex(d => d.name === dataset); if (i >= 0) R.datasets[i] = r.dataset;
    toast(field ? `“${column}” confirmed. The next pipeline run uses it.` : `“${column}” will not be mapped from the next run.`); render();
  } catch (e) { toast(e.message, true); }
}
function bindData() {
  $$("[data-cf]").forEach(b => b.onclick = () => { const [d, c, f] = b.dataset.cf.split("|"); confirmMapping(d, c, f); });
  $$("[data-cfch]").forEach(s => s.onchange = () => { if (!s.value) return; const [d, c] = s.dataset.cfch.split("|"); confirmMapping(d, c, s.value === "-" ? null : s.value); });
  $("#ex-go") && ($("#ex-go").onclick = () => { location.href = wsUrl(`/api/export/${$("#ex-layer").value}.${$("#ex-fmt").value}`); });
  $("#rep-go") && ($("#rep-go").onclick = () => {
    const pid = $("#rep-plot").value.trim();
    if (!R.layers.parcels.features.some(f => f.properties.plot_id === pid)) { toast(`No plot ${pid} in this area`, true); return; }
    location.href = reportUrl(pid);
  });
}

// ------------------------------------------------------------------ review
let mineOnly = false, reviewSort = "severity";
const picked = new Set();
const SEV_RANK = { high: 0, medium: 1, low: 2 };
// Effort model, stated so it can be challenged: checking one parcel by hand across 5+ registers ~ 15 min;
// a reviewer decision on a prepared conflict (all versions side by side) ~ 4 min; a flagged parcel ~ 2 min.
const EFFORT = { manual_per_parcel: 15, per_conflict: 4, per_flagged: 2 };
function effort() {
  const s = R.summary, n = s.parcels;
  const manual = n * EFFORT.manual_per_parcel;
  const now = s.conflicts.open * EFFORT.per_conflict + (s.status.flagged || 0) * EFFORT.per_flagged;
  return { manualH: manual / 60, nowH: now / 60, savedH: Math.max(0, manual - now) / 60, pct: manual ? Math.max(0, 1 - now / manual) : 0 };
}
function effortBlock() {
  const e = effort();
  return `<div class="block effort"><div><b class="mono">${num(e.savedH, 0)} h</b> of reviewer time saved in this area <span class="faint">(${pct(e.pct, 0)} less)</span></div>
    <div class="faint">By hand: ${num(e.manualH, 0)} h to cross-check ${R.summary.parcels} parcels · with Land Connect: ${num(e.nowH, 0)} h for ${R.summary.conflicts.open} prepared conflicts and ${R.summary.status.flagged || 0} flagged parcels.
    Assumes ${EFFORT.manual_per_parcel} min per parcel by hand, ${EFFORT.per_conflict} min per prepared conflict, ${EFFORT.per_flagged} min per flagged parcel.</div></div>`;
}
const canDecideC = c => c.status === "open" && can("reviewer") && routedToMe(c);
const routedToMe = c => !ME || ME.department === "all" || (c.departments || []).includes(ME.department);
function conflictList() {
  const l = R.conflicts.filter(c => c.status === reviewFilter && (severityFilter === "all" || c.severity === severityFilter) && (!mineOnly || routedToMe(c)));
  const key = { severity: c => [SEV_RANK[c.severity], c.plot_id || ""], plot: c => [c.plot_id || "", SEV_RANK[c.severity]],
    type: c => [c.title, SEV_RANK[c.severity]], department: c => [(c.departments || [])[0] || "~", SEV_RANK[c.severity]] }[reviewSort];
  return l.sort((a, b) => { const x = key(a), y = key(b); return x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : x[1] < y[1] ? -1 : x[1] > y[1] ? 1 : 0; });
}
function renderReview() {
  const cnt = s => R.conflicts.filter(c => c.status === s).length;
  const chips = [["open", "Open"], ["auto_resolved", "Auto-resolved"], ["resolved", "Decided"]].map(([k, l]) => `<button class="chip ${reviewFilter === k ? "active" : ""}" data-f="${k}">${l} <span class="mono">${cnt(k)}</span></button>`).join("");
  const sev = [["all", "All"], ["high", "High"], ["medium", "Medium"], ["low", "Low"]].map(([k, l]) => `<button class="chip ${severityFilter === k ? "active" : ""}" data-s="${k}">${l}</button>`).join("")
    + (ME && ME.department !== "all" ? `<button class="chip ${mineOnly ? "active" : ""}" data-mine="1">${esc(deptName(ME.department))} only</button>` : "");
  const list = conflictList();
  const intro = reviewFilter === "open" ? `<p class="lead">Only disagreements that no trusted source could settle reach this queue. Choose the correct version, or mark the item as actioned. Every decision is written to the audit trail.</p>${effortBlock()}` : "";
  const sorter = `<label class="sortby">Sort <select id="rv-sort">${[["severity", "Severity"], ["type", "Problem type"], ["plot", "Plot"], ["department", "Department"]].map(([k, l]) => `<option value="${k}" ${reviewSort === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>`;
  const decidable = list.filter(canDecideC);
  [...picked].forEach(id => { if (!decidable.some(c => c.id === id)) picked.delete(id); });
  const sel = decidable.filter(c => picked.has(c.id));
  const same = sel.length && sel.every(c => JSON.stringify((c.versions || []).map(v => v.source)) === JSON.stringify((sel[0].versions || []).map(v => v.source)));
  const bulk = reviewFilter === "open" && decidable.length ? `<div class="bulkbar ${sel.length ? "on" : ""}">
      <label><input type="checkbox" id="rv-all" ${sel.length && sel.length === Math.min(decidable.length, 300) ? "checked" : ""}> ${sel.length ? `<b>${sel.length}</b> selected` : `Select all ${Math.min(decidable.length, 300)} you can decide`}</label>
      ${sel.length ? `<input id="bulk-note" placeholder="Note for all (required)" maxlength="300">
        ${same ? (sel[0].versions || []).map((v, i) => `<button class="btn small ok" data-bulk="${i}">Accept “${esc(v.source)}”</button>`).join("") : ""}
        <button class="btn small" data-bulk="actioned">Mark actioned</button>
        ${!same ? `<span class="faint" style="font-size:11.5px">Pick items of one type to accept a version for all.</span>` : ""}` : ""}</div>` : "";
  const cards = list.slice(0, 300).map(conflictCard).join("") || `<div class="empty"><h3>Nothing here</h3>No ${reviewFilter.replace("_", "-")} conflicts${severityFilter !== "all" ? " at this severity" : ""}.</div>`;
  return `<div class="filters">${chips}</div><div class="filters">${sev}${sorter}</div>${intro}${bulk}${cards}${list.length > 300 ? `<p class="faint">Showing 300 of ${list.length}. Filter by severity to narrow down.</p>` : ""}`;
}
function conflictCard(c) {
  const sel = selectedConflict === c.id;
  let body = "";
  if (sel) {
    const vers = (c.versions || []).map((v, i) => `<div class="version"><div>${v.geom ? `<span class="swatch" style="border-color:${VERSION_COLORS[i]}"></span>` : ""}<span class="vl">${esc(v.label)}</span>
        <div class="vs">${esc(v.source)}${v.ref ? " · " + esc(v.ref) : ""}</div></div>
        ${c.status === "open" && can("reviewer") && routedToMe(c) ? `<button class="btn ok small" data-choose="${i}">Accept</button>` : (c.chosen === i ? `<span class="ok-text">Chosen</span>` : "")}</div>`).join("");
    body = `${vers ? `<div class="versions">${vers}</div>` : ""}${c.suggestion ? `<div class="suggest">Suggested action: ${esc(c.suggestion)}</div>` : ""}
      ${c.status === "open" && !can("reviewer") ? `<div class="readonly-note">Decisions need an approver (reviewer role).</div>` : ""}
      ${c.status === "open" && can("reviewer") && !routedToMe(c) ? `<div class="readonly-note">Routed to ${(c.departments || []).map(deptName).map(esc).join(", ")}; not your department.</div>` : ""}
      ${c.status === "open" && can("reviewer") && routedToMe(c) ? `<div class="decide"><input id="note" placeholder="Reviewer note (optional)"><button class="btn small" data-action="actioned">${vers ? "Resolve another way" : "Mark actioned"}</button></div>` : ""}
      ${c.status === "resolved" && can("reviewer") ? `<div class="decide"><button class="btn small danger" data-action="reopen">Undo decision</button></div>` : ""}
      ${parcelIndex[c.plot_id] ? `<div style="margin-top:8px"><button class="btn small" data-plotcard="${esc(c.plot_id)}">Plot details</button></div>` : ""}`;
  }
  const res = c.resolution ? `<div class="resolution">${c.resolved_by === "system" ? "Auto-resolved: " : ""}${esc(c.resolution)}</div>` : "";
  const box = reviewFilter === "open" && canDecideC(c) ? `<input type="checkbox" class="pick" data-pick="${esc(c.id)}" ${picked.has(c.id) ? "checked" : ""} aria-label="Select for a bulk decision">` : "";
  return `<div class="item ${c.severity} ${sel ? "sel" : ""}" data-cid="${esc(c.id)}"><div class="top"><span class="ct">${box}${esc(c.title)}</span><span class="plot">${esc(c.plot_id)}</span></div>
    <div class="sev ${c.severity}">${c.severity}${(c.departments || []).length ? ` <span class="faint" style="font-weight:500">· ${c.departments.map(deptName).map(esc).join(", ")}</span>` : ""}</div><div class="d">${esc(c.detail)}</div>${res}${body}</div>`;
}
function bindReview() {
  $$("[data-f]").forEach(b => b.onclick = () => { reviewFilter = b.dataset.f; render(); });
  $$("[data-s]").forEach(b => b.onclick = () => { severityFilter = b.dataset.s; render(); });
  $$("[data-mine]").forEach(b => b.onclick = () => { mineOnly = !mineOnly; render(); });
  $("#rv-sort") && ($("#rv-sort").onchange = e => { reviewSort = e.target.value; render(); });
  $$("[data-pick]").forEach(b => b.onclick = e => { e.stopPropagation(); b.checked ? picked.add(b.dataset.pick) : picked.delete(b.dataset.pick); const note = $("#bulk-note")?.value; render(); if (note && $("#bulk-note")) $("#bulk-note").value = note; });
  $("#rv-all") && ($("#rv-all").onchange = e => { conflictList().filter(canDecideC).slice(0, 300).forEach(c => e.target.checked ? picked.add(c.id) : picked.delete(c.id)); render(); });
  $$("[data-bulk]").forEach(b => b.onclick = () => bulkDecide(b.dataset.bulk === "actioned" ? null : +b.dataset.bulk));
  $$(".tab-body .item").forEach(el => el.onclick = e => { if (!e.target.closest("button, input")) selectConflict(el.dataset.cid === selectedConflict ? null : el.dataset.cid); });
  $$("[data-choose]").forEach(b => b.onclick = () => decide(selectedConflict, +b.dataset.choose));
  $$("[data-action=actioned]").forEach(b => b.onclick = () => decide(selectedConflict, null));
  $$("[data-action=reopen]").forEach(b => b.onclick = () => reopenConflict(selectedConflict));
  $$("[data-plotcard]").forEach(b => b.onclick = e => { e.stopPropagation(); inspect(b.dataset.plotcard); });
}
function selectConflict(cid) {
  selectedConflict = cid;
  render();
  if (!cid) { selection.clearLayers(); return; }
  showConflictOnMap(R.conflicts.find(x => x.id === cid));
  $(`.tab-body .item[data-cid="${CSS.escape(cid)}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
}
function showConflictOnMap(c) {
  $("#inspector").classList.add("hidden");
  document.body.classList.add("panel-on"); document.body.classList.remove("collapsed"); syncSide();
  selection.clearLayers();
  const layer = parcelIndex[c.plot_id];
  if (layer) {
    L.geoJSON(layer.toGeoJSON(), { style: { color: "#0f5e74", weight: 4, fillOpacity: 0 }, interactive: false }).addTo(selection);
    map.flyToBounds(layer.getBounds().pad(1.6), { duration: 0.6, maxZoom: 21 });
  }
  (c.versions || []).forEach((v, i) => v.geom && L.geoJSON(v.geom, { style: { color: VERSION_COLORS[i], weight: 3, dashArray: "6 4", fillOpacity: 0 }, interactive: false }).addTo(selection));
  if (c.geom_hint) {
    const g = L.geoJSON(c.geom_hint, { style: { color: "#ff1744", weight: 3, fillColor: "#ff1744", fillOpacity: 0.45 }, interactive: false }).addTo(selection);
    if (!parcelIndex[c.plot_id]) map.flyToBounds(g.getBounds().pad(2), { duration: 0.6 });
  }
}
function openConflict(cid) {
  const c = R.conflicts.find(x => x.id === cid);
  if (!c) return;
  reviewFilter = c.status; severityFilter = "all"; tab = "review";
  $$("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === "review"));
  selectConflict(cid);
}
async function decide(cid, choice) {
  try {
    applyDecision(await api("/api/decisions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conflict_id: cid, choice, note: $("#note")?.value || "" }) }));
    toast("Decision recorded in the audit trail");
  } catch (e) { toast(`Could not save the decision: ${e.message}`, true); }
}
async function bulkDecide(choice) {
  const note = $("#bulk-note")?.value.trim() || "";
  if (note.length < 5) { toast("Write a note for the bulk decision (at least 5 characters)", true); $("#bulk-note")?.focus(); return; }
  const ids = [...picked];
  if (!confirm(`Apply this decision to ${ids.length} conflict(s)? Each one is recorded in the audit trail with your name and note.`)) return;
  try {
    const r = await api("/api/decisions/bulk", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conflict_ids: ids, choice, note }) });
    r.decided.forEach(c => { const i = R.conflicts.findIndex(x => x.id === c.id); if (i >= 0) R.conflicts[i] = { ...R.conflicts[i], ...c }; });
    r.parcels.forEach(p => { const f = R.layers.parcels.features.find(f => f.properties.plot_id === p.plot_id); if (f) { Object.assign(f.properties, p); parcelIndex[p.plot_id]?.setStyle(parcelStyle(f)); } });
    R.summary = r.summary; if (r.audit) R.audit = r.audit;
    picked.clear(); buildConflictMarkers(); applyVisibility(); render();
    toast(`${r.decided.length} decided${r.skipped.length ? `, ${r.skipped.length} skipped (${[...new Set(r.skipped.map(s => s.reason))].join("; ")})` : ""}`);
  } catch (e) { toast(e.message, true); }
}
async function reopenConflict(cid) {
  try { applyDecision(await api(`/api/decisions/${cid.split("/").map(encodeURIComponent).join("/")}/reopen`, { method: "POST" })); toast("Decision undone"); }
  catch (e) { toast(`Could not undo: ${e.message}`, true); }
}
function applyDecision(r) {
  const i = R.conflicts.findIndex(c => c.id === r.conflict.id);
  R.conflicts[i] = { ...R.conflicts[i], ...r.conflict };
  if (r.parcel) {
    const f = R.layers.parcels.features.find(f => f.properties.plot_id === r.parcel.plot_id);
    Object.assign(f.properties, r.parcel);
    parcelIndex[r.parcel.plot_id]?.setStyle(parcelStyle(f));
  }
  R.summary = r.summary; if (r.audit) R.audit = r.audit;
  buildConflictMarkers(); applyVisibility();
  const next = conflictList()[0];
  selectedConflict = null; render();
  if (next && reviewFilter === "open") selectConflict(next.id); else if (r.parcel) inspect(r.parcel.plot_id);
}

async function loadLearning() {
  const el = $("#learning"); if (!el) return;
  try {
    const L = await api("/api/learning");
    const hist = L.history.length > 1 ? `<div class="faint" style="font-size:11.5px;margin:8px 0 3px">Share of conflicts settled automatically, last ${L.history.length} runs</div>
      <div class="spark">${L.history.map(h => `<div title="${esc(h.at.replace("T", " "))}: ${pct(h.rate, 0)}" style="height:${Math.max(4, h.rate * 100)}%"></div>`).join("")}</div>` : "";
    const rows = L.patterns.map(g => `<tr><td>${esc(g.title)}</td><td class="num mono">${g.decisions}</td><td>${esc(g.top_source)} <span class="faint">(${pct(g.agreement, 0)})</span></td>
      <td class="num">${g.enabled ? `<span class="ok-text">On</span>${can("admin") ? ` <button class="btn small" data-lr="${esc(g.type)}|${esc(g.top_source)}|0">Turn off</button>` : ""}`
        : g.eligible ? (can("admin") ? `<button class="btn small ok" data-lr="${esc(g.type)}|${esc(g.top_source)}|1">Turn on (settles ${g.would_settle})</button>` : `<span class="faint">ready: an admin can turn it on</span>`)
        : `<span class="faint">needs ${Math.max(0, L.thresholds.min_decisions - g.decisions)} more or more agreement</span>`}</td></tr>`).join("");
    el.innerHTML = `<div class="faint" style="font-size:12px;margin-bottom:6px">When reviewers decide at least ${L.thresholds.min_decisions} conflicts of one type and pick the same source in ${pct(L.thresholds.min_agreement, 0)} or more of them, the system proposes a rule. An admin turns it on; it is audited, and a reviewer's own decision still wins.</div>
      ${rows ? `<table class="t"><tr><th>Conflict type</th><th class="num">Decided</th><th>Reviewers chose</th><th class="num">Rule</th></tr>${rows}</table>` : `<div class="faint">No reviewer decisions yet in this area.</div>`}${hist}`;
    $$("[data-lr]").forEach(b => b.onclick = async () => {
      const [type, source, on] = b.dataset.lr.split("|");
      try { await api("/api/learning/rule", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type, source, enabled: on === "1" }) }); toast(`Rule ${on === "1" ? "on" : "off"}. It applies from the next pipeline run.`); loadLearning(); }
      catch (e) { toast(e.message, true); }
    });
  } catch (e) { el.innerHTML = `<span class="bad-text">${esc(e.message)}</span>`; }
}

// ------------------------------------------------------------------ changes
function renderChanges() {
  const feats = R.layers.changes.features;
  if (!feats.length) return `<div class="empty"><h3>No change detection for this area</h3>Change detection compares two survey dates. This workspace has ${R.rasters.ortho_t1 ? "no changes between its surveys" : "a single date of data"}.<br>Add a second drone survey (ORI + DSM) to detect new construction, demolitions, extensions and added floors.</div>`;
  const counts = {};
  feats.forEach(f => counts[f.properties.type] = (counts[f.properties.type] || 0) + 1);
  const kp = Object.keys(CHANGE_LABELS).map(t => `<div class="stat"><div class="v" style="color:${CHANGE_COLORS[t]}">${counts[t] || 0}</div><div class="l">${CHANGE_LABELS[t]}</div></div>`).join("");
  const items = feats.map((f, i) => ({ f, i })).sort((a, b) => a.f.properties.type.localeCompare(b.f.properties.type)).map(({ f, i }) => {
    const p = f.properties;
    return `<div class="item change-item" data-i="${i}"><div class="top"><span class="ct"><span class="dot" style="background:${CHANGE_COLORS[p.type]}"></span>${CHANGE_LABELS[p.type]}</span><span class="plot">${esc(p.plot_id)}</span></div><div class="d">${esc(p.detail)}</div></div>`;
  }).join("");
  const d1 = R.rasters.ortho_t1?.date || R.elevation?.dates?.[0], d2 = R.rasters.ortho_t2?.date || R.elevation?.dates?.[1];
  const how = R.elevation ? `The ${esc(d1)} and ${esc(d2)} surface models (DSM) are compared inside each current building outline: ground before and a roof now is a new building, a larger raised area is an extension, a higher roof is an added floor, and a roof that has gone to ground is a demolition. Turn on “Building heights” and “Building heights, earlier survey” in Layers to compare.`
    : `Buildings from the ${esc(d1)} and ${esc(d2)} drone surveys are compared by footprint overlap and DSM height. Switch the map between the two years to see before and after.`;
  return `${R.elevation?.simulated ? `<div class="block sim-note"><span class="prov sim">Simulated</span> The building outlines are real; the elevation models and the ${esc(d1.slice(0, 4))} survey are generated for this prototype, because building-level DSMs come from drone or LiDAR surveys that are not public (open DEMs are 10–30 m).</div>` : ""}
    <p class="lead">${how}</p>
    <div class="stats">${kp}</div><div class="section-title">Detected changes</div>${items}`;
}
function zoomTo(feature) {
  selection.clearLayers();
  const g = L.geoJSON(feature, { style: { color: "#0f5e74", weight: 4, fillOpacity: 0 }, interactive: false }).addTo(selection);
  map.flyToBounds(g.getBounds().pad(2.5), { duration: 0.6, maxZoom: 21 });
}

// ------------------------------------------------------------------ accuracy
function renderAccuracy() {
  const ev = R.evaluation, ba = R.building_agreement;
  const agreement = ba ? `<div class="section-title">Agreement between the two real building layers (no truth needed)</div><div class="block">
      <div class="bar-row"><span>Confirmed by both</span><div class="track"><div class="fill" style="width:${ba.confirmed_by_reference / ba.primary_count * 100}%"></div></div><span class="val">${pct(ba.confirmed_by_reference / ba.primary_count, 0)}</span></div>
      <div class="bar-row"><span>Mean overlap (IoU)</span><div class="track"><div class="fill" style="width:${ba.mean_iou_confirmed * 100}%"></div></div><span class="val">${num(ba.mean_iou_confirmed, 2)}</span></div></div>` : "";
  if (!ev) return agreement + `<div class="empty"><h3>No hidden truth for this area</h3>Accuracy against truth is measured on the synthetic benchmark and on simulated land records.</div>`;
  const pos = Object.entries(ev.positional).filter(([, v]) => v);
  const max = Math.max(...pos.map(([, v]) => v.mean_m));
  const bars = pos.map(([k, v]) => `<div class="bar-row"><span>${esc(k)}</span><div class="track"><div class="fill" style="width:${v.mean_m / max * 100}%;${k.startsWith("Fused") ? "background:" + STATUS_COLORS.approved : ""}"></div></div><span class="val">${num(v.mean_m, 2)} m</span></div>`).join("");
  const m = ev.matching, x = ev.extraction, gr = ev.georeferencing, s = ev.schema_mapping;
  const pr = o => `<td class="num mono">${o.detected} / ${o.planted}</td><td class="num mono ${o.false_alarms ? "bad-text" : ""}">${o.false_alarms}</td>`;
  const ch = Object.entries(ev.change_detection || {}).map(([k, o]) => `<tr><td>${CHANGE_LABELS[k]}</td>${pr(o)}</tr>`).join("");
  const iss = Object.entries(ev.issues).filter(([, o]) => o.planted || o.false_alarms).map(([k, o]) => `<tr><td>${esc(k)}</td>${pr(o)}</tr>`).join("");
  const cal = Object.keys(STATUS_COLORS).filter(k => ev.calibration[k]).map(k => { const o = ev.calibration[k];
    return `<tr><td><span class="dot" style="background:${STATUS_COLORS[k]}"></span>${STATUS_LABELS[k]}</td><td class="num mono">${o.parcels}</td><td class="num mono">${o.with_error}</td><td class="num mono">${pct(o.error_rate)}</td></tr>`; }).join("");
  const o = ev.overall;
  const bar = (label, v, col) => `<div class="kpi"><div class="kv2">${v == null ? "–" : pct(v, 0)}</div><div class="kl">${label}</div><div class="ktrack"><div style="width:${(v || 0) * 100}%;background:${col}"></div></div></div>`;
  const head = o ? `<div class="kpis">${bar("Precision", o.precision, STATUS_COLORS.approved)}${bar("Recall", o.recall, STATUS_COLORS.verified)}${bar("F1", o.f1, "var(--accent)")}</div>
    <div class="faint" style="font-size:12px;margin:-2px 0 4px">${o.found} planted problems found, ${o.false_alarms} false alarms, ${o.missed} missed. All problem types and changes pooled. Precision = share of alarms that were real; recall = share of real problems found.</div>` : "";
  const hist = (R.benchmarks || []).filter(h => h.evaluation?.issues);
  const TRACK = ["Encroaching building", "Legacy pipe under buildings", "Wrong extent in RoR", "Floors beyond building permission", "Unassessed new construction"];
  const cell = x => x ? `${x.detected}/${x.planted} <span class="${x.false_alarms ? "bad-text" : "faint"}">· ${x.false_alarms} FA</span>` : "–";
  const beforeAfter = hist.length > 1 ? `<div class="section-title">Before and after the rule changes</div><div class="block" style="overflow-x:auto"><table class="t ba">
      <tr><th>Problem</th>${hist.map(h => `<th class="num">${esc(h.label)}</th>`).join("")}</tr>
      ${TRACK.map(k => `<tr><td>${esc(k)}</td>${hist.map(h => `<td class="num mono">${cell(h.evaluation.issues[k])}</td>`).join("")}</tr>`).join("")}
      <tr><td><b>All problems, F1</b></td>${hist.map(h => `<td class="num mono"><b>${h.evaluation.overall?.f1 != null ? num(h.evaluation.overall.f1, 3) : "–"}</b></td>`).join("")}</tr></table>
    <div class="faint" style="font-size:11.5px;margin-top:6px">“found/planted · FA” = false alarms. <b>Corrected data</b>: two generator bugs made the answer key disagree with the files it produced (courtyard holes dropped from the cadastral sheet; planted encroachments cut as holes instead of from the plot edge). <b>Rules v2</b>: a building seen in one layer only must protrude clearly beyond its positional error and must not be a shape disagreement between the two building layers; a utility line must run at least 1 m under the building's outline shrunk by that error. See rule B21.</div></div>` : "";
  const bt = R.blind_tests || {}, br = bt.blind_runs?.latest, bm = bt.blind_manual?.latest;
  const blind = (br || bm) ? `<div class="section-title">Blind tests (rules frozen, errors not seen when the rules were written)</div><div class="block"><table class="t">
      <tr><th>Test</th><th class="num">Precision</th><th class="num">Recall</th><th class="num">F1</th></tr>
      ${br ? `<tr><td>Random batch, hidden seed <span class="faint">(${esc(br.at)}, rules ${esc(br.rules)}; ${bt.blind_runs.runs} run${bt.blind_runs.runs > 1 ? "s" : ""})</span></td><td class="num mono">${pct(br.overall.precision)}</td><td class="num mono">${pct(br.overall.recall)}</td><td class="num mono">${num(br.overall.f1, 3)}</td></tr>` : ""}
      ${bm ? `<tr><td>Teammate batch, edits made by hand <span class="faint">(${esc(bm.at)}; ${bm.edits.length} edits)</span></td><td class="num mono">${pct(bm.overall.precision)}</td><td class="num mono">${pct(bm.overall.recall)}</td><td class="num mono">${num(bm.overall.f1, 3)}</td></tr>`
        : `<tr><td colspan="4" class="faint">Teammate batch: not run yet. A teammate edits data/blind_manual/raw by hand, logs each edit, then runs <span class="mono">python -m landconnect.blind score</span>.</td></tr>`}</table>
    <div class="faint" style="font-size:11.5px;margin-top:6px">The random batch uses the same generator with a new hidden seed and random layout and noise: it shows the rules are not fitted to one dataset. The teammate batch is the stronger test, because a person who has not read the rules decides what to break.</div></div>` : "";
  const hh = ev.heights ? `<tr><td>Building height from DSM − DTM, mean error · floors exactly right</td><td class="num mono">${num(ev.heights.height_mae_m * 100, 0)} cm · ${pct(ev.heights.floors_correct, 0)}</td></tr>` : "";
  const mi = R.matcher;
  const fi = mi ? Object.entries(mi.feature_importance).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<div class="bar-row"><span>${esc(k)}</span><div class="track"><div class="fill" style="width:${v * 100}%"></div></div><span class="val">${num(v * 100, 0)}%</span></div>`).join("") : "";
  const kind = R.workspace_meta?.kind;
  return `<p class="lead">${kind === "real-world" ? "Land records here are simulated on real geography, so their true layout is known. Building layers are real and have no truth; see the agreement check below." : "This ward is synthetic, so its true layout is known."} The pipeline never sees the truth; it is only used to score the output.</p>
  ${head}${blind}${beforeAfter}
  <div class="section-title">Boundary accuracy (mean offset from the true boundary)</div><div class="block">${bars}</div>
  <div class="section-title">Core components</div><div class="block"><table class="t">
    <tr><th>Component</th><th class="num">Result</th></tr>
    <tr><td>Columns mapped correctly</td><td class="num mono">${s.correct} / ${s.columns_expected}</td></tr>
    ${gr ? `<tr><td>Recovered rotation (true ${gr.true_rotation_deg}°)</td><td class="num mono">${num(gr.estimated_rotation_deg, 3)}°</td></tr>` : ""}
    <tr><td>Parcel to municipal matching, precision / recall</td><td class="num mono">${pct(m.precision)} / ${pct(m.recall)}</td></tr>
    ${hh}
    ${x ? `<tr><td>Building extraction, precision / recall</td><td class="num mono">${pct(x.precision)} / ${pct(x.recall)}</td></tr><tr><td>Building outline IoU · height error</td><td class="num mono">${num(x.mean_iou, 2)} · ${num(x.height_mae_m * 100, 1)} cm</td></tr>` : ""}
  </table></div>
  <div class="section-title">Planted problems found</div><div class="block"><table class="t"><tr><th>Problem hidden in the data</th><th class="num">Found</th><th class="num">False alarms</th></tr>${iss}</table>
    ${ev.issues_note ? `<div class="faint" style="font-size:11.5px;margin-top:6px">${esc(ev.issues_note)}</div>` : ""}</div>
  ${ch ? `<div class="section-title">Change detection</div><div class="block"><table class="t"><tr><th>Change</th><th class="num">Found</th><th class="num">False alarms</th></tr>${ch}</table></div>` : ""}
  ${(ev.calibration_bands || []).length ? `<div class="section-title">Does a higher score mean fewer mistakes?</div><div class="block">
    ${ev.calibration_bands.map(b => `<div class="bar-row"><span>Score ${esc(b.band)} <span class="faint">(${b.parcels})</span></span><div class="track"><div class="fill" style="width:${b.correct * 100}%;background:${b.correct >= 0.95 ? STATUS_COLORS.approved : b.correct >= 0.8 ? STATUS_COLORS.flagged : STATUS_COLORS.review}"></div></div><span class="val">${pct(b.correct, 0)}</span></div>`).join("")}
    <div class="faint" style="font-size:11.5px;margin-top:6px">Share of parcels in each score band whose boundary (within 0.5 m) and resolved owner are both right. A trustworthy score rises with the band.</div></div>` : ""}
  ${can("reviewer") ? `<div class="section-title">Learning from reviewers</div><div id="learning" class="block"><div class="skel"></div></div>` : ""}
  <div class="section-title">Is the confidence score honest?</div><div class="block"><table class="t"><tr><th>Status</th><th class="num">Parcels</th><th class="num">With error</th><th class="num">Error rate</th></tr>${cal}</table>
    <div class="faint" style="font-size:11.5px;margin-top:6px">A parcel counts as wrong if its boundary is off by more than 0.5 m or its resolved owner is wrong. Errors should sit in review, not in auto-approved.</div></div>
  ${agreement}
  ${mi ? `<div class="section-title">Matching model</div><div class="block"><div class="faint" style="font-size:12px;margin-bottom:6px">${esc(mi.model)}, trained on ${mi.training_pairs} candidate pairs from ${mi.training_wards} separate synthetic wards with 0.3 m to 2 m positional noise. Cross-validated ROC-AUC ${num(mi.cv_roc_auc, 3)}.</div>${fi}</div>` : ""}`;
}

// ------------------------------------------------------------------ audit
function renderAuditShell() {
  const kinds = [["", "All"], ["review", "Reviewer"], ["conflict", "Conflicts"], ["parcel", "Parcels"], ["crs", "Coordinates"], ["coregistration", "Shifts"], ["run", "Runs"]];
  return `<p class="lead">Every decision the system or a reviewer makes is appended here. Each entry carries the SHA-256 hash of the previous one, so changing any past entry breaks the chain from that point on.</p>
    <div id="chain"><div class="skel big" style="height:56px"></div></div>
    <div class="filters" style="margin-top:12px">${kinds.map(([k, l]) => `<button class="chip ${auditFilter.kind === k ? "active" : ""}" data-kind="${k}">${l}</button>`).join("")}</div>
    <div class="ask" style="margin-bottom:8px">${icon('<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>')}<input id="audit-subject" placeholder="Filter by plot, e.g. 112/3A" value="${esc(auditFilter.subject)}"></div>
    <div id="audit-list"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>`;
}
async function loadAudit() {
  $$("[data-kind]").forEach(b => b.onclick = () => { auditFilter.kind = b.dataset.kind; render(); });
  const inp = $("#audit-subject");
  inp.onchange = () => { auditFilter.subject = inp.value.trim(); render(); };
  try {
    const q = new URLSearchParams({ limit: 150 });
    if (auditFilter.subject) q.set("subject", auditFilter.subject);
    if (auditFilter.kind) q.set("kind", auditFilter.kind);
    const a = await api(`/api/audit?${q}`);
    const v = a.verify;
    $("#chain").innerHTML = `<div class="chain ${v.ok ? "" : "bad"}">${I.chain}<div><b>${v.ok ? "Chain intact" : `Chain broken at entry ${v.broken_at}`}</b> · <span class="mono">${v.entries}</span> entries<div class="h">head ${esc(v.head)}</div></div></div>`;
    $("#audit-list").innerHTML = a.entries.map(e => `<div class="entry"><span class="seq">#${e.seq}</span><div>
      <div><span class="k">${esc(e.kind)}</span> ${e.subject ? `<span class="mono">${esc(e.subject)}</span>` : ""} <span class="who">· ${esc(e.actor)} · ${esc(e.ts.replace("T", " "))}</span></div>
      <pre>${esc(JSON.stringify(e.detail)).slice(0, 600)}</pre><div class="hash">${esc(e.hash.slice(0, 24))}… ← ${esc(e.prev.slice(0, 12))}…</div></div></div>`).join("") || `<div class="empty">No entries match.</div>`;
  } catch (e) { $("#audit-list").innerHTML = `<p class="bad-text">Could not load the audit trail: ${esc(e.message)}</p>`; }
}

// ------------------------------------------------------------------ search bar: suggestions, questions, places
// As you type: grouped suggestions from everything in the area (plots, record numbers, owners, buildings,
// places, other areas, coordinates, ready-made questions with live counts). Enter runs the highlighted
// suggestion, or asks the whole sentence as a question; if nothing in the area matches and it looks like a
// place, it is looked up in OpenStreetMap (India) and the map flies there. Keys: / or Ctrl+K focus, ↑ ↓ choose,
// Enter go, Esc close.
const OMNI = { items: [], active: -1, seq: 0, timer: 0 };
const RECENT_KEY = "lc.recent";
const recent = () => { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch { return []; } };
function remember(label, action) {
  try {
    const r = recent().filter(x => x.label !== label);
    r.unshift({ label, action });
    localStorage.setItem(RECENT_KEY, JSON.stringify(r.slice(0, 8)));
  } catch { }
}
const EXAMPLES = ["103/1", "Plots with owner conflicts", "Total tax at risk", "How many plots need review",
  "Floors beyond building permission", "Plots between 200 and 400 sq m without conflicts", "Water department conflicts", "12.9295, 77.5835", "Chennai"];
function omniShow(groups, note = "") {
  OMNI.items = [];
  const html = groups.map(g => `<div class="omni-g">${esc(g.group)}</div>` + g.items.map(it => {
    const i = OMNI.items.push(it) - 1;
    return `<div class="omni-i" role="option" data-oi="${i}" id="omni-${i}"><span class="ol">${esc(it.label)}</span>${it.sub ? `<span class="os">${esc(it.sub)}</span>` : ""}</div>`;
  }).join("")).join("");
  OMNI.active = -1;
  $("#omni").innerHTML = html + (note ? `<div class="omni-note">${note}</div>` : "");
  $("#omni").classList.toggle("hidden", !html && !note);
  $("#ask-input").setAttribute("aria-expanded", html ? "true" : "false");
  $$("#omni [data-oi]").forEach(el => {
    el.onmousedown = e => { e.preventDefault(); omniGo(OMNI.items[+el.dataset.oi]); };
    el.onmousemove = () => omniActive(+el.dataset.oi);
  });
}
function omniHide() { $("#omni").classList.add("hidden"); $("#ask-input").setAttribute("aria-expanded", "false"); }
function omniActive(i) {
  OMNI.active = i;
  $$("#omni .omni-i").forEach(el => el.classList.toggle("on", +el.dataset.oi === i));
  $(`#omni-${i}`)?.scrollIntoView({ block: "nearest" });
  $("#ask-input").setAttribute("aria-activedescendant", i >= 0 ? `omni-${i}` : "");
}
function omniEmpty() {
  const r = recent();
  omniShow([...(r.length ? [{ group: "Recent", items: r.map(x => ({ label: x.label, sub: "", action: x.action })) }] : []),
    { group: "Try", items: EXAMPLES.map(q => ({ label: q, sub: "", action: /^\d+\.\d+,/.test(q) || q === "Chennai" || /^\d+\/\d+$/.test(q) ? { type: "text", q } : { type: "query", q } })) }],
    `<span class="kbd">/</span> to search from anywhere · <span class="kbd">↑</span><span class="kbd">↓</span> choose · <span class="kbd">Enter</span> go`);
}
async function omniFetch() {
  const q = $("#ask-input").value;
  if (!q.trim()) return omniEmpty();
  const seq = ++OMNI.seq;
  try {
    const r = await api(`/api/suggest?q=${encodeURIComponent(q)}`);
    if (seq === OMNI.seq && document.activeElement === $("#ask-input")) omniShow(r.groups);
  } catch { }
}
$("#ask-input").setAttribute("role", "combobox");
$("#ask-input").setAttribute("aria-controls", "omni");
$("#ask-input").setAttribute("aria-autocomplete", "list");
$("#ask-input").addEventListener("input", () => { clearTimeout(OMNI.timer); OMNI.timer = setTimeout(omniFetch, 110); });
$("#ask-input").addEventListener("focus", () => { if (R) omniFetch(); });
$("#ask-input").addEventListener("blur", () => setTimeout(omniHide, 120));
$("#ask-input").addEventListener("keydown", e => {
  const n = OMNI.items.length, open = !$("#omni").classList.contains("hidden");
  if (e.key === "ArrowDown" && n) { e.preventDefault(); omniActive(open ? (OMNI.active + 1) % n : 0); }
  else if (e.key === "ArrowUp" && n) { e.preventDefault(); omniActive((OMNI.active - 1 + n) % n); }
  else if (e.key === "Escape") { if (open) omniHide(); else $("#ask-input").blur(); }
  else if (e.key === "Enter" && open && OMNI.active >= 0) { e.preventDefault(); omniGo(OMNI.items[OMNI.active]); }
});
document.addEventListener("keydown", e => {
  if ((e.key === "/" && !e.target.closest("input, textarea, select, [contenteditable]")) || (e.key.toLowerCase() === "k" && (e.ctrlKey || e.metaKey))) {
    if (ED.on) return;
    e.preventDefault(); $("#ask-input").focus(); $("#ask-input").select();
  }
});
let pin = null;
function dropPin(center, html) {
  if (pin) map.removeLayer(pin);
  pin = L.marker(center, { icon: L.divIcon({ className: "search-pin", html: "<i></i>", iconSize: [22, 30], iconAnchor: [11, 30] }) }).addTo(map);
  if (html) pin.bindPopup(html).openPopup();
  pin.on("popupclose", () => { if (pin) { map.removeLayer(pin); pin = null; } });
}
function statewideFor(latlng) {                      // outside the area: show the state's real cadastral map
  if (layers.parcels && layers.parcels.getBounds().pad(0.2).contains(latlng)) return null;
  const cands = STATEWIDE.filter(s => latlng[1] >= s.bounds[0] && latlng[1] <= s.bounds[2] && latlng[0] >= s.bounds[1] && latlng[0] <= s.bounds[3]);
  if (!cands.length || cands.some(s => vis[s.key])) return null;
  const s = cands.sort((a, b) => b.bytes - a.bytes)[0];
  vis[s.key] = true; applyVisibility();
  return s;
}
async function omniGo(it) {
  if (!it) return;
  omniHide();
  const a = it.action || {};
  if (a.type !== "text") remember(it.label, a);
  if (a.type === "plot") {
    $("#ask-input").value = it.label;
    if (parcelIndex[a.plot_id]) inspect(a.plot_id, true); else if (a.center) map.flyTo(a.center, 19);
  } else if (a.type === "point") {
    map.flyTo(a.center, a.zoom || 19, { duration: 0.7 });
    const sw = statewideFor(a.center);
    dropPin(a.center, `<b>${esc(it.label)}</b>${it.sub ? `<div class="faint">${esc(it.sub)}</div>` : ""}${sw ? `<div class="faint">Showing ${esc(sw.label)} (real cadastral map)</div>` : ""}`);
    if (a.plot_id && parcelIndex[a.plot_id]) setTimeout(() => inspect(a.plot_id), 750);
  } else if (a.type === "bounds") {
    map.flyToBounds(a.bounds, { duration: 0.8, maxZoom: 17 });
    const sw = statewideFor(a.center);
    dropPin(a.center, `<b>${esc(it.label)}</b>${it.sub ? `<div class="faint">${esc(it.sub)}</div>` : ""}<div class="faint">Place from OpenStreetMap${sw ? ` · showing ${esc(sw.label)}` : ""}</div>`);
  } else if (a.type === "area") {
    $("#ask-input").value = "";
    await switchWorkspace(a.ws); toast(`Switched to ${it.label}`);
  } else if (a.type === "request") {
    reqFilter = ""; setTab("requests"); toast(`Showing all requests: look for CR-${String(a.id).padStart(5, "0")}`);
  } else if (a.type === "query" || a.type === "text") {
    $("#ask-input").value = a.q; runQuestion(a.q);
  }
}
$("#ask").onsubmit = e => {
  e.preventDefault();
  const q = $("#ask-input").value.trim();
  if (!q || !R) return;
  clearTimeout(OMNI.timer); OMNI.seq++;              // a suggestion arriving now must not cover the results
  if (OMNI.active >= 0 && OMNI.items[OMNI.active] && !$("#omni").classList.contains("hidden")) return omniGo(OMNI.items[OMNI.active]);
  omniHide();
  runQuestion(q);
};
async function runQuestion(q) {
  const ll = q.match(/^\s*[NS]?\s*-?\d{1,3}\.\d+\s*[,; ]\s*[EW]?\s*-?\d{1,3}\.\d+\s*$/i);
  if (ll) {                                          // coordinates: let the suggestion engine parse and place them
    const s = await api(`/api/suggest?q=${encodeURIComponent(q)}`).catch(() => null);
    const it = s?.groups.find(g => g.group === "Coordinates")?.items[0];
    if (it) return omniGo(it);
  }
  try {
    queryState = { q, loading: true };
    $("#tab-results").classList.remove("hidden"); setTab("results");
    queryState = { q, ...(await api("/api/query", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ q }) })), sort: null };
    remember(q, { type: "query", q });
    if (!queryState.total && looksLikePlace(q)) {
      queryState.places = (await api(`/api/geocode?q=${encodeURIComponent(q)}`).catch(() => ({ places: [] }))).places;
      if (queryState.places.length) placeGo(0);
    }
    try { history.replaceState(null, "", `#ws=${encodeURIComponent(WS)}&q=${encodeURIComponent(q)}`); } catch { }
    drawQueryResults(); render();
  } catch (err) { queryState = null; render(); toast(`Query failed: ${err.message}`, true); }
}
const looksLikePlace = q => !/\d{2,}|[<>=]|\b(plots?|parcels?|buildings?|conflicts?|how|total|with|without|tax|owner|review|score)\b/i.test(q) && q.trim().length >= 3;
function placeGo(i) {
  const p = queryState.places[i];
  omniGo({ label: p.label, sub: p.sub, action: p.bounds ? { type: "bounds", bounds: p.bounds, center: p.center } : { type: "point", center: p.center, zoom: 15 } });
}
function drawQueryResults() {
  queryLayer.clearLayers();
  const pts = queryState.matches.map(m => m.center);
  for (const m of queryState.matches) {
    const l = parcelIndex[m.plot_id];
    if (l && m.kind === "parcels") L.geoJSON(l.toGeoJSON(), { style: { color: "#00e5ff", weight: 3, fillColor: "#00e5ff", fillOpacity: 0.25 }, interactive: false }).addTo(queryLayer);
    else L.circleMarker(m.center, { radius: 6, color: "#00e5ff", weight: 2, fillOpacity: 0.3 }).addTo(queryLayer);
  }
  if (pts.length) map.flyToBounds(L.latLngBounds(pts).pad(0.3), { duration: 0.6, maxZoom: 20 });
  $("#n-results").textContent = queryState.total;
}
function sortedMatches() {
  const m = [...queryState.matches], k = queryState.sort;
  if (k === "area") m.sort((a, b) => b.area - a.area);
  if (k === "score") m.sort((a, b) => a.score - b.score);
  if (k === "tax") m.sort((a, b) => b.revenue - a.revenue);
  if (k === "plot") m.sort((a, b) => String(a.plot_id).localeCompare(String(b.plot_id), undefined, { numeric: true }));
  return m;
}
function renderResults() {
  if (!queryState) return `<div class="empty"><h3>Ask a question</h3>Type in the search bar: a plot number, a record number, an owner, a place, coordinates, or a question such as "vacant plots larger than 5 guntas" or "total tax at risk". Press <span class="kbd">/</span> to start.</div>`;
  if (queryState.loading) return `<div class="skel"></div><div class="skel"></div><div class="skel"></div>`;
  const s = queryState.summary || {};
  const chips = queryState.interpreted.map(t => `<span class="chip" style="cursor:default">${esc(t)}</span>`).join("");
  const items = sortedMatches();
  const list = items.map(m => `<div class="item q-item" data-i="${queryState.matches.indexOf(m)}"><div class="top"><span class="ct">${esc(m.label)}</span><span class="plot">${esc(m.plot_id || "")}</span></div><div class="d">${esc(m.detail)}</div></div>`).join("");
  const facts = s.count ? [`<b>${s.count}</b> ${queryState.filter?.entity === "buildings" ? "building" : queryState.filter?.entity === "changes" ? "change" : "plot"}${s.count === 1 ? "" : "s"}`,
    s.total_area_sqm ? `${num(s.total_area_sqm, 0)} m²` : "", s.total_revenue_inr ? `${inr(s.total_revenue_inr)}/yr tax at risk` : "",
    s.mean_score != null ? `average score ${num(s.mean_score, 0)}` : ""].filter(Boolean).join(" · ") : "";
  const places = (queryState.places || []).map((p, i) => `<div class="item place-item" data-pi="${i}"><div class="top"><span class="ct">${esc(p.label)}</span><span class="plot">${esc(p.kind || "")}</span></div><div class="d">${esc(p.sub || "")}</div></div>`).join("");
  return `${s.answer ? `<div class="answer">${esc(s.answer)}</div>` : ""}
    <p class="lead">“${esc(queryState.q)}” · <span class="mono">${queryState.total}</span> result${queryState.total === 1 ? "" : "s"} · ${queryState.engine === "claude" ? "interpreted by Claude" : "built-in rule parser"}</p>
    <div class="filters">${chips}</div>
    ${facts ? `<div class="q-facts">${facts}</div>` : ""}
    ${queryState.total ? `<div class="q-tools"><label class="sortby">Sort <select id="q-sort"><option value="">as found</option><option value="plot" ${queryState.sort === "plot" ? "selected" : ""}>plot number</option><option value="score" ${queryState.sort === "score" ? "selected" : ""}>lowest score</option><option value="area" ${queryState.sort === "area" ? "selected" : ""}>largest area</option><option value="tax" ${queryState.sort === "tax" ? "selected" : ""}>tax at risk</option></select></label>
      <button class="btn small" id="q-fit">Show all on map</button><button class="btn small" id="q-csv">${I.download} CSV</button><button class="btn small" id="q-geo">${I.download} GeoJSON</button><button class="btn small" id="q-link">${I.link} Copy link</button><button class="btn small quiet" id="q-clear">Clear highlight</button></div>` : ""}
    ${places ? `<div class="section-title">Places in India (OpenStreetMap)</div>${places}` : ""}
    ${list || (places ? "" : `<div class="empty"><h3>No matches</h3>Try fewer conditions, or type a plot number, a record number or a place.</div>`)}
    ${queryState.total > queryState.matches.length ? `<p class="faint">Showing the first ${queryState.matches.length} of ${queryState.total}; download for all of them.</p>` : ""}`;
}
function download(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
function bindResults() {
  $$(".q-item").forEach(el => el.onclick = () => { const m = queryState.matches[+el.dataset.i]; if (m.plot_id && parcelIndex[m.plot_id] && m.kind === "parcels") inspect(m.plot_id, true); else map.flyTo(m.center, 20); });
  $$(".place-item").forEach(el => el.onclick = () => placeGo(+el.dataset.pi));
  $("#q-clear") && ($("#q-clear").onclick = () => queryLayer.clearLayers());
  $("#q-sort") && ($("#q-sort").onchange = e => { queryState.sort = e.target.value || null; render(); });
  $("#q-fit") && ($("#q-fit").onclick = () => { drawQueryResults(); });
  const slug = queryState.q.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 40);
  $("#q-csv") && ($("#q-csv").onclick = () => {
    const cell = v => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const rows = [["label", "plot_id", "detail", "area_sqm", "score", "tax_at_risk_inr", "open_conflicts", "lat", "lon"].join(",")]
      .concat(sortedMatches().map(m => [m.label, m.plot_id, m.detail, m.area, m.score, m.revenue, (m.types || []).join(" "), m.center[0], m.center[1]].map(cell).join(",")));
    download(`landconnect_${WS}_${slug}.csv`, rows.join("\n"), "text/csv");
  });
  $("#q-geo") && ($("#q-geo").onclick = () => {
    const feats = sortedMatches().map(m => {
      const f = m.kind === "parcels" && R.layers.parcels.features.find(x => x.properties.plot_id === m.plot_id);
      return { type: "Feature", geometry: f ? f.geometry : { type: "Point", coordinates: [m.center[1], m.center[0]] },
        properties: { label: m.label, plot_id: m.plot_id, detail: m.detail, area_sqm: m.area, score: m.score, tax_at_risk_inr: m.revenue } };
    });
    download(`landconnect_${WS}_${slug}.geojson`, JSON.stringify({ type: "FeatureCollection", features: feats }), "application/geo+json");
  });
  $("#q-link") && ($("#q-link").onclick = async () => {
    const url = `${location.origin}/#ws=${encodeURIComponent(WS)}&q=${encodeURIComponent(queryState.q)}`;
    try { await navigator.clipboard.writeText(url); toast("Link copied: it opens this search for anyone signed in"); } catch { prompt("Copy this link", url); }
  });
}

// ------------------------------------------------------------------ pipeline run & upload
async function startRun(reset = false) {
  $("#btn-run").disabled = true;
  try { await api(reset ? "/api/reset-demo" : "/api/run", { method: "POST" }); }
  catch (e) { toast(e.message, true); $("#btn-run").disabled = false; return; }
  $("#run-overlay").classList.remove("hidden");
  $("#run-close").classList.add("hidden");
  $("#run-ws").textContent = WORKSPACES.find(w => w.id === WS)?.title || WS;
  $("#run-msg").textContent = "";
  pollRun();
}
async function pollRun() {
  const s = await api("/api/status");
  $("#run-steps").innerHTML = s.steps_meta.map(m => { const st = s.steps[m.key] || {};
    return `<li class="${st.state || ""}"><span class="ic"></span><div><div class="t">${esc(m.title)}</div><div class="d">${esc(m.description)}</div>${(st.log || []).slice(0, 3).map(l => `<div class="lg">${esc(l)}</div>`).join("")}</div><span class="sec">${st.seconds != null ? num(st.seconds, 2) + " s" : ""}</span></li>`; }).join("");
  if (s.state === "running") { setTimeout(pollRun, 350); return; }
  $("#btn-run").disabled = false;
  if (s.state === "error") { $("#run-msg").innerHTML = `<span class="bad-text">${esc(s.error)}</span>`; $("#run-close").textContent = "Close"; $("#run-close").classList.remove("hidden"); return; }
  $("#run-msg").textContent = "Done.";
  $("#run-close").textContent = "View results"; $("#run-close").classList.remove("hidden");
  await loadResult(true);
}
$("#run-close").onclick = () => $("#run-overlay").classList.add("hidden");
$("#btn-run").onclick = () => startRun(false);
$("#btn-newplot").onclick = () => startEditor(null, "parcel_add");
$("#btn-upload").onclick = () => { $("#upload-overlay").classList.remove("hidden"); $("#upload-result").innerHTML = ""; };
$("#upload-cancel").onclick = () => $("#upload-overlay").classList.add("hidden");
$("#upload-go").onclick = async () => {
  const files = $("#upload-input").files;
  if (!files.length) { $("#upload-result").innerHTML = `<p class="bad-text">Choose at least one file.</p>`; return; }
  const fd = new FormData(); [...files].forEach(f => fd.append("files", f));
  try {
    const r = await api("/api/upload", { method: "POST", body: fd });
    $("#upload-result").innerHTML = `<table class="t" style="margin-top:12px"><tr><th>File</th><th>Detected as</th><th class="num">Features</th></tr>${r.saved.map(s => `<tr><td class="mono">${esc(s.file)}</td><td>${esc(s.role_label)}</td><td class="num mono">${s.features || ""}</td></tr>`).join("")}</table><p class="muted" style="font-size:12px">Saved. Run the pipeline to integrate them.</p>`;
  } catch (e) { $("#upload-result").innerHTML = `<p class="bad-text">Upload failed: ${esc(e.message)}</p>`; }
};

// ------------------------------------------------------------------ departments, change requests, inbox
function deptOptions() {
  const d = R?.departments?.departments || {};
  return `<option value="all">All departments (state level)</option>` + Object.entries(d).map(([k, v]) => `<option value="${k}">${esc(v.name)}</option>`).join("");
}
function myFields() {
  if (!can("officer") || !R?.departments) return [];
  return Object.entries(R.departments.fields).filter(([k, f]) => canDept(f.dept) && k !== "parcel_add");
}
// ------------------------------------------------------------------ department view of a plot, a building or an area
// Every department gets its own tab: its main details, every record it holds with all source columns, the checks
// and conflicts routed to it, its official updates and the fields it may change. What a department sees of the
// others follows the access matrix (enforced on the server; the badges below only explain it).
const DEPT_ORDER = ["revenue", "survey", "municipal", "town_planning", "registration", "electricity", "water", "gas", "roads"];
const ACCESS_LABEL = { full: "Full access", summary: "Summary only", none: "Not shared" };
let deptTab = null, DREG = null;
const myAccess = d => !ME?.dept_access ? "full" : (ME.dept_access[d] || "summary");
const isMine = d => ME?.department === d;
const jobTitle = (role, dept) => (DREG?.titles?.[dept] || DREG?.titles?.all || {})[role] || (DREG?.titles?.all || {})[role] || ROLE_LABEL[role] || role;
const prettyKey = k => String(k).replace(/_/g, " ").replace(/\s+/g, " ").trim().replace(/^./, c => c.toUpperCase());
const fmtVal = v => typeof v === "number" ? v.toLocaleString("en-IN", { maximumFractionDigits: 3 }) : esc(v);
const sqm = v => v == null ? null : `<span class="mono">${num(v)} m²</span>`;
const sumBy = (xs, f) => xs.reduce((a, x) => a + (Number(f(x)) || 0), 0);
const byKey = (fields, ...names) => { const want = names.map(n => n.toLowerCase().replace(/[^a-z0-9]/g, "")); for (const [k, v] of Object.entries(fields || {})) if (want.includes(k.toLowerCase().replace(/[^a-z0-9]/g, ""))) return v; return null; };
function fieldsTable(fields, source, open = false) {
  const rows = Object.entries(fields || {});
  if (!rows.length) return "";
  return `<details class="dfields" ${open ? "open" : ""}><summary>All ${rows.length} fields${source ? ` · <span class="mono">${esc(source)}</span>` : ""}</summary>
    <table class="t">${rows.map(([k, v]) => `<tr><td class="faint">${esc(prettyKey(k))}</td><td>${fmtVal(v)}</td></tr>`).join("")}</table></details>`;
}
const kvGrid = pairs => { const h = pairs.filter(([, v]) => v != null && v !== "").map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join(""); return h ? `<div class="kv">${h}</div>` : ""; };
const check = (state, text) => `<div class="dcheck ${state}"><span>${state === "ok" ? "✓" : state === "bad" ? "!" : state === "warn" ? "?" : "·"}</span><div>${text}</div></div>`;

// main details and checks, per department
const DEPT_VIEW = {
  revenue(p, recs, reg, x) {
    const ext = reg.extent_sqm ?? p.ror_area_sqm, diff = ext && p.area_sqm ? (ext - p.area_sqm) / p.area_sqm : null;
    const facts = [["Khata / patta", reg.khata || p.khata ? `<span class="mono">${esc(reg.khata || p.khata)}</span>` : null], ["Owner on RoR", esc(reg.owner)],
      ["Survey no.", esc(reg.survey_no)], ["Recorded extent", ext ? `${sqm(ext)}${diff != null ? ` <span class="faint">(${diff >= 0 ? "+" : ""}${(diff * 100).toFixed(1)}% vs surveyed)</span>` : ""}` : null],
      ["Land classification", esc(reg.land_type)]];
    const checks = [];
    if (!reg.khata && !p.khata) checks.push(check("bad", "No record of rights for this plot"));
    else if (diff != null) checks.push(Math.abs(diff) > 0.05 ? check("warn", `Recorded extent differs from the surveyed area by ${(Math.abs(diff) * 100).toFixed(1)}%`) : check("ok", "Recorded extent matches the surveyed area"));
    const ch = p.ownership_chain;
    if (ch && ch.verdict) checks.push(check(ch.ok ? "ok" : "bad", esc(ch.verdict)));
    return { facts, checks };
  },
  survey(p, recs, reg, x) {
    const fv = reg.field_visit;
    const facts = [["Survey no.", reg.survey_no ? `<span class="mono">${esc(reg.survey_no)}</span>` : null], ["Map sheet", esc(reg.sheet)], ["Village", esc(reg.village)],
      ["Taluk", esc(reg.taluk)], ["District", esc(reg.district)], ["Surveyed area", sqm(reg.surveyed_area_sqm ?? p.area_sqm)], ["Extent on the map sheet", sqm(reg.recorded_extent_sqm)],
      ["GNSS corners", reg.gnss_corners ?? p.gnss_vertices], ["Field visit", fv ? `${esc(fv.visit || "visited")}${fv.land_use ? ` · ${esc(fv.land_use)}` : ""}${fv.owner ? `<div class="faint">owner met: ${esc(fv.owner)}</div>` : ""}` : null],
      ["ULPIN-format ID", p.ulpin ? `<span class="mono">${esc(p.ulpin)}</span>` : null]];
    const checks = [];
    checks.push((reg.gnss_corners ?? p.gnss_vertices) ? check("ok", `${reg.gnss_corners ?? p.gnss_vertices} corner(s) fixed by GNSS survey`) : check("warn", "No corner fixed by GNSS; boundary relies on the digitised map"));
    (p.topo_flags || []).forEach(t => checks.push(check("info", `Topology fix: ${esc(t)}`)));
    return { facts, checks, extra: fv?.fields ? fieldsTable(fv.fields, "field verification") : "" };
  },
  municipal(p, recs, reg, x) {
    const aa = reg.assessed_area_sqm, tax = x.cf.filter(c => c.status !== "resolved" && c.revenue_inr).reduce((a, c) => Math.max(a, c.revenue_inr), 0);
    const facts = [["Property ID", reg.pid || p.municipal_pid ? `<span class="mono">${esc(reg.pid || p.municipal_pid)}</span>` : null], ["Owner on tax register", esc(reg.owner)],
      ["Assessed use", esc(reg.usage || p.municipal_use)], ["Assessed area", aa ? `${sqm(aa)} <span class="faint">(surveyed ${num(p.area_sqm)} m²)</span>` : null], ["Ward", esc(reg.ward)],
      ["Match to survey plot", p.municipal_pid ? `<span class="mono">${pct(p.match_prob)}</span>` : null], ["Tax at risk", tax ? `<span class="bad-text">${inr(tax)} a year</span> <span class="faint">(estimate)</span>` : null]];
    const checks = [];
    if (!reg.pid && !p.municipal_pid) checks.push(check("bad", "Not on the property-tax register"));
    else if (aa && p.area_sqm) { const d = Math.abs(aa - p.area_sqm) / p.area_sqm; checks.push(d > 0.1 ? check("warn", `Assessed area differs from the survey by ${(d * 100).toFixed(0)}%`) : check("ok", "Assessed area agrees with the survey")); }
    if (p.municipal_use && p.land_use && p.municipal_use.toLowerCase() !== String(p.land_use).toLowerCase()) checks.push(check("warn", `Assessed as ${esc(p.municipal_use)}, recorded as ${esc(p.land_use)}`));
    return { facts, checks };
  },
  town_planning(p, recs, reg, x) {
    const perm = Math.max(0, ...recs.map(r => r.floors || 0)) || null;
    const tall = Math.max(0, ...x.blds.map(b => b.properties.floors || 0)) || null;
    const bua = sumBy(recs, r => r.bua || byKey(r.fields, "approved_bua_sqm", "bua"));
    const facts = [["Permissions", recs.length || null], ["Permitted floors", perm], ["Approved built-up area", bua ? sqm(bua) : null],
      ["Buildings on the plot", x.blds.length ? `${x.blds.length} · ${num(sumBy(x.blds, b => b.properties.area_sqm), 0)} m² footprint` : "none mapped"],
      ["Tallest measured", tall ? `${tall} floor(s)` : null]];
    const checks = [];
    if (perm && tall) checks.push(tall > perm ? check("bad", `${tall} floors measured, ${perm} permitted`) : check("ok", `Measured floors (${tall}) within the permission (${perm})`));
    if (!recs.length && x.blds.length && myAccess("town_planning") !== "none") checks.push(check("warn", "Buildings stand on this plot but no building permission is linked to it"));
    if (bua && x.blds.length && tall) { const built = sumBy(x.blds, b => (b.properties.area_sqm || 0) * (b.properties.floors || 1)); if (built > bua * 1.1) checks.push(check("warn", `Estimated built-up area ${num(built, 0)} m² exceeds the approved ${num(bua, 0)} m²`)); }
    return { facts, checks };
  },
  registration(p, recs, reg, x) {
    const last = [...recs].sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")))[0];
    const val = last ? byKey(last.fields, "consideration_inr", "consideration", "market_value", "sale_value") : null;
    const facts = [["Registered deeds", recs.length || null], ["Latest deed", last ? `<span class="mono">${esc(last.id)}</span>${last.date ? ` · ${esc(last.date)}` : ""}` : null],
      ["Parties", last && (last.seller || last.buyer) ? `${esc(last.seller || "?")} → <b>${esc(last.buyer || "?")}</b>` : null], ["Consideration", val ? inr(Number(val)) : null],
      ["Owner on RoR", esc(p.ror_owner)]];
    const checks = [];
    const ch = p.ownership_chain;
    if (ch && ch.verdict && recs.length) checks.push(check(ch.ok ? "ok" : "bad", esc(ch.verdict)));
    return { facts, checks };
  },
  electricity(p, recs, reg, x) {
    const live = recs.filter(r => !/disconn|closed|inactive/i.test(r.status || ""));
    const load = sumBy(recs, r => r.load);
    const facts = [["Accounts", recs.length ? `${recs.length} (${live.length} live)` : null], ["Sanctioned load", load ? `${num(load, 1)} kW` : null],
      ["Tariffs", [...new Set(recs.map(r => r.category).filter(Boolean))].map(esc).join(", ")]];
    const checks = [];
    const com = recs.filter(r => /LT-3|LT-5|HT|commercial/i.test(r.category || ""));
    if (com.length && /resid/i.test(p.municipal_use || "")) checks.push(check("warn", `Commercial tariff on a plot taxed as residential`));
    if (live.length && !p.municipal_pid) checks.push(check("bad", "Live connection on a plot missing from the tax register"));
    return { facts, checks };
  },
  water(p, recs, reg, x) {
    const meters = recs.map(r => byKey(r.fields, "meter_no", "meter", "meter_number")).filter(Boolean);
    const facts = [["Connections", recs.length || null], ["Types", [...new Set(recs.map(r => r.category).filter(Boolean))].map(esc).join(", ")],
      ["Meters", meters.length ? meters.map(m => `<span class="mono">${esc(m)}</span>`).join(", ") : null]];
    const checks = [];
    if (recs.length && !p.municipal_pid) checks.push(check("bad", "Water connection on a plot missing from the tax register"));
    return { facts, checks };
  },
  gas(p, recs, reg, x) {
    return { facts: [["Connections", recs.length || null], ["Categories", [...new Set(recs.map(r => r.category).filter(Boolean))].map(esc).join(", ")]], checks: [] };
  },
  roads(p, recs, reg, x) {
    const enc = x.cf.filter(c => c.type === "ENCROACHMENT");
    const notice = (p.updates || []).filter(u => u.field === "road_notice").pop();
    return { facts: [["Encroachments found", enc.length || "none"], ["Encroachment notice", notice ? `<span class="mono">${esc(notice.value)}</span>` : null]],
      checks: enc.map(c => check(c.status === "open" ? "bad" : "ok", esc(c.detail || c.title))) };
  },
};

function recordCard(d, r, level) {
  const bits = [r.category, r.load != null ? `${r.load} kW` : null, r.floors != null ? `${r.floors} floor(s) permitted` : null, r.bua ? `${r.bua} m² approved` : null,
    r.buyer || r.seller ? `${r.seller || "?"} → ${r.buyer || "?"}` : null, r.date].filter(Boolean);
  const st = r.status && !/^(live|active)$/i.test(r.status) ? `<span class="pill rejected">${esc(r.status)}</span>` : r.status ? `<span class="pill approved">${esc(r.status)}</span>` : "";
  return `<div class="drec"><div class="drec-h"><span class="mono">${esc(r.id || "record")}</span>${st}</div>
    ${r.name ? `<div><b>${esc(r.name)}</b></div>` : ""}${bits.length ? `<div class="faint">${bits.map(esc).join(" · ")}</div>` : ""}
    ${r.link ? `<div class="faint" style="font-size:11.5px">Linked by ${esc(r.link)}</div>` : ""}
    ${level === "full" ? fieldsTable(r.fields, r.source) : ""}</div>`;
}

function deptPanel(p, pid, d, x) {
  const level = myAccess(d), meta = deptMeta(d), recs = (p.dept_records || {})[d] || [], reg = (p.registers || {})[d] || {};
  const badge = isMine(d) ? `<span class="dbadge mine">Your department</span>` : `<span class="dbadge ${level}">${ACCESS_LABEL[level]}</span>`;
  const head = `<div class="dhead"><div><b>${esc(meta.name)}</b><div class="faint" style="font-size:12px">${esc(meta.holds || "")}</div></div>${badge}</div>`;
  if (level === "none") return head + `<div class="dlock">🔒 ${esc(meta.short)} records are not shared with ${esc(deptMeta(ME.department).short)}. An administrator can change this in Admin → Department access.</div>`;
  const view = (DEPT_VIEW[d] || (() => ({ facts: [], checks: [] })))(p, recs, reg, x);
  const cf = x.cf.filter(c => (c.departments || []).includes(d));
  const ups = (p.updates || []).filter(u => u.department === d);
  const own = Object.entries(R.departments?.fields || {}).filter(([k, f]) => f.dept === d && k !== "parcel_add");
  const mayPropose = can("officer") && canDept(d);
  const regFields = reg.fields ? fieldsTable(reg.fields, reg.source) : "";
  let h = head + kvGrid(view.facts);
  if (level === "summary") h += `<div class="dnote">Summary only: record numbers, categories and status. Names, amounts and source columns stay with ${esc(meta.short)}.</div>`;
  if (view.checks.length) h += `<div class="dsub">Checks</div>` + view.checks.join("");
  if (regFields || view.extra) h += `<div class="dsub">Register entry</div>${regFields}${view.extra || ""}`;
  if (recs.length) h += `<div class="dsub">Records (${recs.length})</div>` + recs.map(r => recordCard(d, r, level)).join("");
  else if (!Object.keys(reg).length) h += `<div class="faint" style="margin:8px 0">${esc(meta.short)} holds no record linked to this plot.</div>`;
  if (cf.length) h += `<div class="dsub">Conflicts routed to ${esc(meta.short)} (${cf.length})</div>` + cf.map(c => `<div class="item ${c.severity}" data-cid="${esc(c.id)}"><div class="top"><span class="ct">${esc(c.title)}</span><span class="sev ${c.severity}">${c.status === "open" ? c.severity : c.status.replace("_", " ")}</span></div>${c.detail ? `<div class="faint" style="font-size:12px">${esc(c.detail)}</div>` : ""}</div>`).join("");
  if (ups.length) h += `<div class="dsub">Official updates</div>` + ups.map(u => `<div class="upd"><b>${esc(u.label)}</b> → ${esc(u.value)}<div class="faint">${esc(u.request)} · approved by ${esc(u.by)} · ${esc((u.at || "").slice(0, 10))}</div></div>`).join("");
  if (own.length) h += `<div class="dsub">${esc(meta.short)} is the authority for</div><div class="dfields-own">${own.map(([k, f]) => mayPropose
    ? `<button class="chip" data-propose="${k}" title="Propose a change (goes to a ${esc(meta.short)} approver)">${esc(f.label)} ✎</button>` : `<span class="chip" style="cursor:default">${esc(f.label)}</span>`).join("")}</div>
    ${mayPropose ? "" : `<div class="faint" style="font-size:11.5px">Only ${esc(meta.short)} officers can propose changes to these; a ${esc(meta.short)} approver decides.</div>`}`;
  return h;
}

function deptSection(p, pid, opt = {}) {
  const x = { cf: R.conflicts.filter(c => c.plot_id === pid), blds: plotBuildings(pid) };
  const count = d => ((p.dept_records || {})[d] || []).length + ((p.registers || {})[d] ? 1 : 0);
  const conflictsFor = d => x.cf.filter(c => c.status === "open" && (c.departments || []).includes(d)).length;
  const def = opt.dept || (ME?.department && ME.department !== "all" ? ME.department : null) || (deptTab && DEPT_ORDER.includes(deptTab) ? deptTab : null)
    || (opt.building ? "town_planning" : DEPT_ORDER.find(d => count(d)) || "revenue");
  deptTab = def;
  const tabs = DEPT_ORDER.map(d => {
    const lv = myAccess(d), n = count(d), c = conflictsFor(d);
    return `<button class="dtab ${d === def ? "on" : ""} ${lv}" data-dtab2="${d}" style="--c:${deptColor(d)}" title="${esc(deptMeta(d).name)} · ${isMine(d) ? "your department" : ACCESS_LABEL[lv].toLowerCase()}">
      <i></i>${esc(deptMeta(d).short)}${lv === "none" ? " 🔒" : n ? ` <span class="n">${n}</span>` : ""}${c ? ` <span class="c" title="${c} open conflict(s)">${c}</span>` : ""}</button>`;
  }).join("");
  return `<div class="section-title">By department</div><div class="dtabs" id="dtabs">${tabs}</div><div class="dpanel" id="dpanel" style="--c:${deptColor(def)}">${deptPanel(p, pid, def, x)}</div>`;
}
function bindDeptSection(p, pid) {
  const x = { cf: R.conflicts.filter(c => c.plot_id === pid), blds: plotBuildings(pid) };
  const bindPanel = () => {
    $$("#dpanel [data-cid]").forEach(el => el.onclick = () => openConflict(el.dataset.cid));
    $$("#dpanel [data-propose]").forEach(b => b.onclick = () => openProposal(pid, b.dataset.propose));
  };
  { const on = $("#dtabs .dtab.on"); if (on) $("#dtabs").scrollLeft = on.offsetLeft - $("#dtabs").offsetLeft - 40; }
  $$("#dtabs [data-dtab2]").forEach(b => b.onclick = () => {
    deptTab = b.dataset.dtab2;
    $$("#dtabs .dtab").forEach(t => t.classList.toggle("on", t === b));
    $("#dpanel").style.setProperty("--c", deptColor(deptTab));
    $("#dpanel").innerHTML = deptPanel(p, pid, deptTab, x);
    bindPanel();
  });
  bindPanel();
}

// --- a selected building, shown at the top of its plot's card
function buildingBlock(bid, pid) {
  const f = R.layers.buildings.features.find(b => b.properties.id === bid);
  if (!f) return "";
  const b = f.properties, p = parcelProps(pid) || {};
  const perm = Math.max(0, ...(((p.dept_records || {}).town_planning) || []).map(r => r.floors || 0)) || null;
  const ch = (R.layers.changes?.features || []).find(c => c.properties.building === bid)?.properties;
  const verdict = perm && b.floors ? (b.floors > perm ? `<span class="bad-text">${b.floors} floors, ${perm} permitted</span>` : `<span class="ok-text">within the ${perm}-floor permission</span>`) : null;
  const conn = (R.layers.connections?.features || []).filter(c => {
    if (!c.geometry || c.geometry.type !== "Point") return false;
    const [lng, lat] = c.geometry.coordinates, g = f.geometry, polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
    return polys.some(pg => ringHas(pg[0], lng, lat));
  }).map(c => c.properties);
  return `<div class="bld-card"><div class="bld-card-h"><span class="dbadge mine">Selected building</span><span class="mono">${esc(bid)}</span></div>
    ${kvGrid([["Footprint", sqm(b.area_sqm)], ["Floors", b.floors ? `${b.floors}${verdict ? ` · ${verdict}` : ""}` : "unknown"], ["Height", b.height_m ? `${num(b.height_m, 1)} m <span class="faint">(${esc(b.height_source || "")})</span>` : null],
      ["Outline from", (b.sources || []).map(esc).join(" + ")], ["Outline agreement", b.footprint_iou != null ? `<span class="mono">${pct(b.footprint_iou)}</span> <span class="faint">(between sources)</span>` : null],
      ["Change since last survey", ch ? esc(ch.detail || CHANGE_LABELS[ch.type]) : null],
      ["Connections inside", conn.length ? conn.map(c => `${esc(deptMeta(c.dept).short)} <span class="mono">${esc(c.id)}</span>`).join(", ") : null],
      ["On plot", `<a href="#" id="bld-plot">${esc(pid)}</a>`]])}</div>`;
}

// --- select an area and see it department by department
const AREA = { on: false, start: null, rect: null, bounds: null };
function areaStart() {
  if (ED.on) return;
  if (MEAS.on) measStop();
  AREA.on = true; map.dragging.disable(); map.getContainer().style.cursor = "crosshair";
  $("#btn-area").classList.add("on");
  toast("Drag a rectangle over the area");
}
function areaStop() {
  AREA.on = false; AREA.start = null; map.dragging.enable(); map.getContainer().style.cursor = "";
  $("#btn-area").classList.remove("on");
}
map.on("mousedown", e => { if (!AREA.on) return; AREA.start = e.latlng; if (AREA.rect) map.removeLayer(AREA.rect); AREA.rect = L.rectangle([e.latlng, e.latlng], { color: "#1a73e8", weight: 2, dashArray: "6 4", fillOpacity: 0.08, interactive: false }).addTo(map); });
map.on("mousemove", e => { if (AREA.on && AREA.start) AREA.rect.setBounds(L.latLngBounds(AREA.start, e.latlng)); });
map.on("mouseup", e => {
  if (!AREA.on || !AREA.start) return;
  const b = L.latLngBounds(AREA.start, e.latlng);
  areaStop(); AREA.done = Date.now();
  if (map.latLngToContainerPoint(b.getNorthWest()).distanceTo(map.latLngToContainerPoint(b.getSouthEast())) < 12) { map.removeLayer(AREA.rect); AREA.rect = null; return; }
  showArea(b);
});
function areaPlots(b) {
  return R.layers.parcels.features.filter(f => { const l = parcelIndex[f.properties.plot_id]; return l && b.contains(l.getBounds().getCenter()); }).map(f => f.properties);
}
function areaDept(d, ps, cfs) {
  const level = myAccess(d), meta = deptMeta(d), recs = ps.flatMap(p => ((p.dept_records || {})[d] || []).map(r => ({ ...r, pid: p.plot_id })));
  const cf = cfs.filter(c => (c.departments || []).includes(d)), open = cf.filter(c => c.status === "open");
  const types = {}; open.forEach(c => types[c.title] = (types[c.title] || 0) + 1);
  const cats = {}; recs.forEach(r => { if (r.category) cats[r.category] = (cats[r.category] || 0) + 1; });
  const badge = isMine(d) ? `<span class="dbadge mine">Your department</span>` : `<span class="dbadge ${level}">${ACCESS_LABEL[level]}</span>`;
  let h = `<div class="dhead"><div><b>${esc(meta.name)}</b><div class="faint" style="font-size:12px">${esc(meta.holds || "")}</div></div>${badge}</div>`;
  if (level === "none") return h + `<div class="dlock">🔒 Not shared with ${esc(deptMeta(ME.department).short)}.</div>`;
  const f = [];
  if (d === "revenue") { const have = ps.filter(p => p.khata).length; f.push(["Plots with RoR", `${have} of ${ps.length}`], ["Extent mismatches > 5%", ps.filter(p => p.ror_area_sqm && Math.abs(p.ror_area_sqm - p.area_sqm) / p.area_sqm > 0.05).length]); }
  if (d === "survey") f.push(["Surveyed area", sqm(sumBy(ps, p => p.area_sqm))], ["GNSS corners", sumBy(ps, p => p.gnss_vertices)], ["Plots without GNSS", ps.filter(p => !p.gnss_vertices).length], ["Topology fixes", sumBy(ps, p => (p.topo_flags || []).length)]);
  if (d === "municipal") { const tax = sumBy(ps, p => Math.max(0, ...cfs.filter(c => c.plot_id === p.plot_id && c.status !== "resolved" && c.revenue_inr).map(c => c.revenue_inr))); f.push(["Assessed plots", `${ps.filter(p => p.municipal_pid).length} of ${ps.length}`], ["Tax at risk", tax ? `<span class="bad-text">${inr(tax)} a year</span>` : "none"]); }
  if (d === "town_planning") { const bl = ps.flatMap(p => plotBuildings(p.plot_id)); f.push(["Permissions", recs.length], ["Plots with a permission", new Set(recs.map(r => r.pid)).size], ["Buildings", `${bl.length} · ${num(sumBy(bl, b => b.properties.area_sqm), 0)} m² footprint`]); }
  if (d === "registration") { const val = sumBy(recs, r => byKey(r.fields, "consideration_inr", "consideration")); f.push(["Registered deeds", recs.length], ["Total consideration", val ? inr(val) : null], ["Ownership chains broken", ps.filter(p => p.ownership_chain && !p.ownership_chain.ok && (p.dept_records || {}).registration).length]); }
  if (["electricity", "water", "gas"].includes(d)) f.push(["Connections", recs.length], ["Live", recs.filter(r => !/disconn|closed|inactive/i.test(r.status || "")).length], ["Plots served", new Set(recs.map(r => r.pid)).size]);
  if (d === "electricity") f.push(["Sanctioned load", `${num(sumBy(recs, r => r.load), 0)} kW`]);
  f.push(["Open conflicts", open.length]);
  h += kvGrid(f);
  if (Object.keys(cats).length) h += `<div class="dsub">By category</div><div class="dfields-own">${Object.entries(cats).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<span class="chip" style="cursor:default">${esc(k)} <span class="mono">${n}</span></span>`).join("")}</div>`;
  if (open.length) h += `<div class="dsub">Open conflicts by type</div>` + Object.entries(types).sort((a, b) => b[1] - a[1]).map(([t, n]) => `<div class="rowline"><span>${esc(t)}</span><span class="mono">${n}</span></div>`).join("")
    + `<div class="dsub">Plots to act on</div><div class="dfields-own">${[...new Set(open.map(c => c.plot_id))].slice(0, 30).map(pid => `<button class="chip" data-plot="${esc(pid)}">${esc(pid)}</button>`).join("")}</div>`;
  if (level === "summary") h += `<div class="dnote">Summary only: counts and categories. Names and amounts stay with ${esc(meta.short)}.</div>`;
  return h;
}
function showArea(b) {
  AREA.bounds = b;
  const ps = areaPlots(b), ids = new Set(ps.map(p => p.plot_id)), cfs = R.conflicts.filter(c => ids.has(c.plot_id));
  const st = {}; ps.forEach(p => st[p.status] = (st[p.status] || 0) + 1);
  const owners = new Set(ps.map(p => p.resolved_owner).filter(Boolean)).size;
  const blds = ps.reduce((a, p) => a + plotBuildings(p.plot_id).length, 0);
  let d = ME?.department && ME.department !== "all" ? ME.department : deptTab && DEPT_ORDER.includes(deptTab) ? deptTab : "revenue";
  selection.clearLayers();
  const tabs = () => DEPT_ORDER.map(k => `<button class="dtab ${k === d ? "on" : ""} ${myAccess(k)}" data-adt="${k}" style="--c:${deptColor(k)}"><i></i>${esc(deptMeta(k).short)}${myAccess(k) === "none" ? " 🔒" : ""}</button>`).join("");
  $("#inspector").innerHTML = `<div class="insp-head"><div><div class="pid">Selected area</div><span class="faint">${ps.length} plot(s) · ${num(sumBy(ps, p => p.area_sqm) / 1e4, 2)} ha</span></div><button class="close" id="insp-close" aria-label="Close">×</button></div>
    ${ps.length ? `${kvGrid([["Plots", ps.length], ["Status", Object.entries(st).map(([k, n]) => `<span class="status ${k}">${esc(STATUS_LABELS[k] || k)} ${n}</span>`).join(" ")],
      ["Owners", owners || null], ["Buildings", blds], ["Open conflicts", cfs.filter(c => c.status === "open").length], ["Mean confidence", num(sumBy(ps, p => p.score) / ps.length)]])}
    <div class="section-title">By department</div><div class="dtabs" id="adtabs">${tabs()}</div><div class="dpanel" id="adpanel" style="--c:${deptColor(d)}">${areaDept(d, ps, cfs)}</div>
    <div class="section-title">Plots in the area</div><div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px"><button class="btn small" id="area-csv">${I.download} CSV</button><button class="btn small" id="area-ask">Show as search results</button></div>
    <div class="dfields-own">${ps.slice(0, 200).map(p => `<button class="chip" data-plot="${esc(p.plot_id)}" style="border-left:3px solid ${STATUS_COLORS[p.status]}">${esc(p.plot_id)}</button>`).join("")}</div>`
    : `<p class="faint">No plot of this area lies inside the rectangle.</p>`}`;
  $("#inspector").classList.remove("hidden");
  $("#insp-close").onclick = () => { closeInspector(); if (AREA.rect) { map.removeLayer(AREA.rect); AREA.rect = null; } };
  const bindPlots = () => $$("#inspector [data-plot]").forEach(el => el.onclick = () => inspect(el.dataset.plot, true));
  $$("#adtabs [data-adt]").forEach(t => t.onclick = () => {
    d = deptTab = t.dataset.adt;
    $$("#adtabs .dtab").forEach(x => x.classList.toggle("on", x === t));
    $("#adpanel").style.setProperty("--c", deptColor(d)); $("#adpanel").innerHTML = areaDept(d, ps, cfs); bindPlots();
  });
  bindPlots();
  $("#area-csv") && ($("#area-csv").onclick = () => {
    const cols = ["plot_id", "status", "score", "area_sqm", "land_use", "khata", "municipal_pid", "resolved_owner", "open_conflicts"];
    const csv = [cols.join(",")].concat(ps.map(p => cols.map(c => `"${String(Array.isArray(p[c]) ? p[c].length : p[c] ?? "").replace(/"/g, '""')}"`).join(","))).join("\n");
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv" })); a.download = `landconnect_${WS}_area.csv`; a.click();
  });
  $("#area-ask") && ($("#area-ask").onclick = () => {
    queryState = { q: `${ps.length} plots in the selected area`, interpreted: ["plots inside the drawn rectangle"], total: ps.length, sort: null, summary: { count: ps.length },
      matches: ps.map(p => { const c = parcelIndex[p.plot_id].getBounds().getCenter(); return { kind: "parcels", plot_id: p.plot_id, label: `Plot ${p.plot_id}`, sub: `${p.status} · ${Math.round(p.score)} · ${num(p.area_sqm, 0)} m²`, center: [c.lat, c.lng], status: p.status }; }) };
    $("#tab-results").classList.remove("hidden"); drawQueryResults(); setTab("results");
  });
}

// --- admin: who sees whose records
async function loadDeptAccess() {
  const el = $("#adm-dacc");
  if (!el) return;
  if (!can("admin")) {
    const acc = ME.dept_access;
    el.innerHTML = acc ? `<table class="t"><tr><th>Department</th><th>What ${esc(deptMeta(ME.department).short)} sees</th></tr>${DEPT_ORDER.map(d => `<tr><td>${esc(deptMeta(d).name)}</td><td><span class="dbadge ${acc[d]}">${d === ME.department ? "Own records" : ACCESS_LABEL[acc[d]]}</span></td></tr>`).join("")}</table><div class="faint" style="font-size:12px;margin-top:6px">Set by the state administrator.</div>` : "";
    return;
  }
  try {
    const m = await api("/api/admin/dept-access");
    const cell = (v, t) => v === t ? `<td class="own">own</td>` : `<td class="${m.matrix[v][t] !== m.defaults[v][t] ? "changed" : ""}"><select data-v="${v}" data-t="${t}" class="acc ${m.matrix[v][t]}">${m.levels.map(l => `<option value="${l}" ${m.matrix[v][t] === l ? "selected" : ""}>${l}</option>`).join("")}</select></td>`;
    el.innerHTML = `<div class="faint" style="font-size:12.5px;margin-bottom:8px">Rows: the department looking. Columns: whose records. <b>full</b> = every field with names and amounts; <b>summary</b> = record numbers, categories and status only; <b>none</b> = hidden. Common plot information (boundary, area, land use, owner, IDs, conflicts) is shared with everyone. Changed cells are highlighted; every change is audited.</div>
      <div class="acc-wrap"><table class="t acc-grid"><tr><th></th>${DEPT_ORDER.map(t => `<th title="${esc(deptMeta(t).name)}"><span class="sw-dot" style="background:${deptColor(t)}"></span>${esc(deptMeta(t).short)}</th>`).join("")}</tr>
      ${DEPT_ORDER.map(v => `<tr><th title="${esc(deptMeta(v).name)}">${esc(deptMeta(v).short)}</th>${DEPT_ORDER.map(t => cell(v, t)).join("")}</tr>`).join("")}</table></div>`;
    $$("#adm-dacc select[data-v]").forEach(s => s.onchange = async () => {
      try { await api("/api/admin/dept-access", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ viewer: s.dataset.v, target: s.dataset.t, level: s.value }) }); toast(`${deptMeta(s.dataset.v).short} now sees ${deptMeta(s.dataset.t).short} records: ${s.value}`); }
      catch (err) { toast(err.message, true); }
      loadDeptAccess();
    });
  } catch (err) { el.innerHTML = `<span class="bad-text">${esc(err.message)}</span>`; }
}

let crPlot = null, crPreset = null;
function openProposal(pid, field = null, preset = null) {
  crPlot = pid; crPreset = preset;
  const fields = myFields();
  $("#cr-plot").textContent = pid;
  $("#cr-field").innerHTML = fields.map(([k, f]) => `<option value="${k}">${esc(deptName(f.dept))}: ${esc(f.label)}</option>`).join("");
  $("#cr-err").classList.add("hidden");
  $("#cr-form").reset();
  if (field) $("#cr-field").value = field;
  $("#cr-field").onchange = () => { crPreset = null; fillProposalField(); };
  fillProposalField();
  $("#cr-overlay").classList.remove("hidden");
}
function fillProposalField() {
  const key = $("#cr-field").value, f = R.departments.fields[key], p = parcelProps(crPlot);
  $("#cr-form").dataset.mapOnly = "";
  if (MAP_FIELDS.includes(key)) return fillMapField(key, f, p);
  const deptKey = { approved_floors: ["town_planning", "floors"], registered_owner: ["registration", "buyer"], tariff: ["electricity", "category"],
    electricity_consumer: ["electricity", "name"], water_type: ["water", "category"], water_consumer: ["water", "name"], gas_consumer: ["gas", "name"] }[key];
  const recs = deptKey ? (p.dept_records?.[deptKey[0]] || []) : [];
  let cur = { owner: p.ror_owner, ror_extent_sqm: p.ror_area_sqm, land_use: p.land_use, municipal_use: p.municipal_use, municipal_pid: p.municipal_pid,
    road_notice: p.road_notice }[key];
  if (deptKey) cur = recs[0]?.[deptKey[1]];
  $("#cr-current").textContent = cur == null || cur === "" ? "(none)" : String(cur).slice(0, 400);
  $("#cr-record-wrap").classList.toggle("hidden", !(deptKey && recs.length > 1 && key !== "registered_owner"));
  $("#cr-record").innerHTML = recs.map(r => `<option value="${esc(r.id)}">${esc(r.id)} · ${esc(r.name || "")}</option>`).join("");
  $("#cr-value-wrap").innerHTML = f.type === "choice" ? `<select name="new_value" required>${f.choices.map(c => `<option>${esc(c)}</option>`).join("")}</select>`
    : `<input name="new_value" required ${f.type === "int" || f.type === "number" ? `type="number" step="${f.type === "int" ? 1 : 0.1}" min="0"` : ""}>`;
  $("#cr-ev-need").textContent = f.evidence ? "(required)" : "(optional)";
}
// Shapes are drawn in the map editor; the form only picks the building for a removal or correction.
function fillMapField(key, f, p) {
  const mine = plotBuildings(crPlot);
  $("#cr-record-wrap").classList.add("hidden");
  $("#cr-ev-need").textContent = f.evidence ? "(required)" : "(optional: site photo or survey sketch)";
  $("#cr-current").textContent = key === "geometry" ? `${num(p.area_sqm)} m² surveyed` : mine.length ? mine.map(b => `${b.properties.id} (${num(b.properties.area_sqm)} m²)`).join(", ") : "No building mapped on this plot";
  const pick = mine.length ? `<select name="new_value" id="cr-bld" required>${mine.map(b => `<option value="${esc(b.properties.id)}">${esc(b.properties.id)} · ${num(b.properties.area_sqm)} m² · ${esc(b.properties.sources.join(" + "))}</option>`).join("")}</select>`
    : `<span class="faint">No building on this plot</span>`;
  if (key === "building_remove") {
    $("#cr-value-wrap").innerHTML = pick;
    if (crPreset && $("#cr-bld")) $("#cr-bld").value = crPreset;
    const show = () => { const b = mine.find(x => x.properties.id === $("#cr-bld")?.value); selection.clearLayers(); if (b) L.geoJSON(b, { style: { color: "#e5484d", weight: 3, fillOpacity: 0.25 }, interactive: false }).addTo(selection); };
    $("#cr-bld") && ($("#cr-bld").onchange = show); show();
    return;
  }
  $("#cr-form").dataset.mapOnly = "1";
  $("#cr-value-wrap").innerHTML = (key === "building_edit" ? pick : "") + `<div class="drawn"><span class="faint">${key === "building_add" ? "Draw the building on the imagery." : key === "geometry" ? "Drag the plot's corners on the imagery." : "Adjust the outline or floors on the map."}</span>
    <button class="btn small primary" type="button" id="cr-open-ed">Open the map editor</button></div>`;
  $("#cr-open-ed").onclick = () => startEditor(crPlot, key, key === "building_edit" ? $("#cr-bld")?.value : null, $("#cr-form").reason.value);
}
$("#cr-cancel").onclick = () => { $("#cr-overlay").classList.add("hidden"); selection.clearLayers(); };
$("#cr-form").onsubmit = async e => {
  e.preventDefault();
  if (e.target.dataset.mapOnly) { $("#cr-err").textContent = "Use “Open the map editor” to draw this change, then Save there."; $("#cr-err").classList.remove("hidden"); return; }
  const fd = new FormData(e.target);
  fd.append("plot_id", crPlot);
  if ($("#cr-record-wrap").classList.contains("hidden")) fd.delete("record_id");
  if (!fd.get("evidence")?.size) fd.delete("evidence");
  try {
    const cr = await api("/api/change-requests", { method: "POST", body: fd });
    $("#cr-overlay").classList.add("hidden");
    toast(`${cr.code} submitted for approval`);
    loadRequestCount(); loadProposals();
    if (tab === "requests") render();
  } catch (err) { $("#cr-err").textContent = err.message; $("#cr-err").classList.remove("hidden"); }
};

// ------------------------------------------------------------------ map editor
// Draw a building, correct a building's outline, or reshape a plot boundary directly on the imagery.
//   click: add a corner · double-click or click the first corner: close the shape
//   drag a corner: move it · drag the dot on an edge: add a corner there · drag the shape: move all of it
//   right-click or Delete on a corner: remove it · S: snapping (or hold Alt) · Q: square corners
//   T: trace a detected outline · Ctrl+Z / Ctrl+Y: undo / redo · Enter: save · Esc: back / cancel
// Work is kept as a draft on this device until it is sent. Saving creates a change request: the map
// itself only changes after a survey approver, never the proposer, approves it.
const canMap = () => can("officer") && canDept("survey");
const MAP_FIELDS = ["geometry", "parcel_add", "building_add", "building_edit", "building_remove"];
const ED_KIND = {
  building_add: { label: "Map a building", color: "#ffd23f", trace: ["ai_buildings", "footprints", "changes"] },
  building_edit: { label: "Correct a building", color: "#ffd23f", trace: ["ai_buildings", "footprints", "changes"] },
  geometry: { label: "Edit the boundary", color: "#5ee0ff", trace: ["municipal", "cadastral_georef"] },
  parcel_add: { label: "Add a new plot", color: "#7cf29c", trace: ["municipal", "cadastral_georef", "footprints", "ai_buildings"] },
};
const TRACE_NAMES = { ai_buildings: "AI building footprints", footprints: "building footprints", changes: "detected changes", municipal: "the municipal layer", cadastral_georef: "the georeferenced cadastral map" };
const ED = { on: false };
const plotBuildings = pid => R.layers.buildings.features.filter(b => b.properties.plot_id === pid);
const ringOf = g => !g ? [] : g.type === "Polygon" ? g.coordinates[0] : g.type === "MultiPolygon" ? g.coordinates[0][0] : [];
function geoArea(ring) {                       // [[lng, lat], ...] -> m² (spherical excess)
  const rad = Math.PI / 180, R6 = 6378137; let s = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const [x1, y1] = ring[i], [x2, y2] = ring[i + 1];
    s += (x2 - x1) * rad * (2 + Math.sin(y1 * rad) + Math.sin(y2 * rad));
  }
  return Math.abs(s * R6 * R6 / 2);
}
function geoPerimeter(ring) { let s = 0; for (let i = 0; i < ring.length - 1; i++) s += map.distance([ring[i][1], ring[i][0]], [ring[i + 1][1], ring[i + 1][0]]); return s; }
function inRing(x, y, ring) {
  let c = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c;
  }
  return c;
}
function bboxOf(ring) { const b = [Infinity, Infinity, -Infinity, -Infinity]; for (const [x, y] of ring) { b[0] = Math.min(b[0], x); b[1] = Math.min(b[1], y); b[2] = Math.max(b[2], x); b[3] = Math.max(b[3], y); } return b; }
function overlapArea(A, B) {                   // m² of ring A inside ring B, sampled on a 40 x 40 grid
  const [x0, y0, x1, y1] = bboxOf(A), b = bboxOf(B);
  if (x1 < b[0] || b[2] < x0 || y1 < b[1] || b[3] < y0) return 0;
  const N = 40, dx = (x1 - x0) / N, dy = (y1 - y0) / N; let inA = 0, both = 0;
  for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) {
    const x = x0 + (i + 0.5) * dx, y = y0 + (j + 0.5) * dy;
    if (inRing(x, y, A)) { inA++; if (inRing(x, y, B)) both++; }
  }
  return inA ? geoArea(A) * both / inA : 0;
}
function edSelfCrosses(r) {
  const s = (a, b, c) => Math.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]));
  const n = r.length - 1;
  for (let i = 0; i < n; i++) for (let j = i + 2; j < n; j++) {
    if (i === 0 && j === n - 1) continue;
    const [a, b, c, d] = [r[i], r[i + 1], r[j], r[j + 1]];
    if (s(a, b, c) * s(a, b, d) < 0 && s(c, d, a) * s(c, d, b) < 0) return true;
  }
  return false;
}

// --- drafts, kept per area / plot / kind on this device only
const draftKey = (pid, kind, target) => `lc.draft.${WS}.${kind}.${target || "-"}.${pid}`;
function getDraft(pid, kind, target) { try { return JSON.parse(localStorage.getItem(draftKey(pid, kind, target)) || "null"); } catch { return null; } }
function putDraft(d) { try { localStorage.setItem(draftKey(d.pid, d.kind, d.target), JSON.stringify(d)); return true; } catch { return false; } }
function dropDraft(pid, kind, target) { try { localStorage.removeItem(draftKey(pid, kind, target)); } catch { } }
function listDrafts(pid = null) {
  const out = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k.startsWith(`lc.draft.${WS}.`)) continue;
      const d = JSON.parse(localStorage.getItem(k));
      if (d && (!pid || d.pid === pid)) out.push(d);
    }
  } catch { }
  return out.sort((a, b) => String(b.at).localeCompare(String(a.at)));
}
const draftLabel = d => `${ED_KIND[d.kind]?.label || d.kind}${d.target ? " " + d.target : ""} · plot ${d.pid}`;
const ago = iso => { const s = (Date.now() - Date.parse(iso)) / 1000; return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : new Date(iso).toLocaleDateString(); };

// --- start / stop
function startEditor(pid, kind, target = null, reason = "") {
  if (!canMap()) return toast("Mapping needs a survey officer, survey approver or admin account", true);
  const isNew = kind === "parcel_add";
  if (isNew) pid = "new";
  const feat = isNew ? null : R.layers.parcels.features.find(x => x.properties.plot_id === pid);
  const bld = target ? R.layers.buildings.features.find(b => b.properties.id === target) : null;
  if ((!feat && !isNew) || (kind === "building_edit" && !bld)) return toast("That plot or building is no longer on the map", true);
  endMapEdit();
  $("#inspector").classList.add("hidden"); $("#cr-overlay").classList.add("hidden");
  const base = kind === "geometry" ? ringOf(feat.geometry) : kind === "building_edit" ? ringOf(bld.geometry) : [];
  const orig = base.slice(0, -1).map(([x, y]) => L.latLng(y, x));
  const d = getDraft(pid, kind, target);
  Object.assign(ED, {
    on: true, pid, kind, target, feat, bld, orig,
    pts: d ? d.pts.map(p => L.latLng(p[0], p[1])) : orig.slice(), closed: d ? d.closed : orig.length >= 3,
    hist: [], fut: [], snap: true, trace: false, hover: -1, dirty: !!d, saveOpen: false, justDragged: false,
    floors: d?.floors ?? (bld?.properties.floors ?? ""), reason: d?.reason || reason || "",
    group: L.layerGroup().addTo(map), ctx: L.layerGroup().addTo(map),
  });
  const wrap = map.getContainer().parentElement;
  wrap.classList.add("editing");
  map.getContainer().classList.add("map-editing");
  map.doubleClickZoom.disable();
  map.on("click", edClick); map.on("dblclick", edDbl); map.on("mousemove", edMove); map.on("mouseout", edOut);
  map.on("zoomend", edRender); map.on("moveend", edCollectSnaps);
  document.addEventListener("keydown", edKey);
  window.addEventListener("beforeunload", edUnload);
  // context: the plot, the building being corrected and other proposals still waiting on this plot
  if (feat) L.geoJSON(feat, { style: { color: "#ffffff", weight: 2, opacity: 0.9, dashArray: "6 5", fillOpacity: 0 }, interactive: false }).addTo(ED.ctx);
  if (bld) L.geoJSON(bld, { style: { color: "#ffffff", weight: 1.5, opacity: 0.8, dashArray: "2 4", fillOpacity: 0 }, interactive: false }).addTo(ED.ctx);
  PROPOSALS.filter(r => r.plot_id === pid).forEach(r => { const g = r.field === "geometry" ? r.new_value : r.new_value?.footprint; if (g) L.geoJSON(g, { style: { color: "#ff9f1c", weight: 1.5, dashArray: "5 4", fillOpacity: 0.06 }, interactive: false }).addTo(ED.ctx); });
  edBuildBar();
  if (feat) {
    const bounds = (bld ? L.geoJSON(bld) : L.geoJSON(feat)).getBounds().pad(bld ? 2.5 : kind === "building_add" ? 0.15 : 0.5);
    map.flyToBounds(bounds, { duration: 0.45, maxZoom: 21, paddingBottomRight: [0, 110] });
  } else if (map.getZoom() < 18) map.setZoom(18);
  edCollectSnaps();
  edRender(false);
  const pend = PROPOSALS.filter(r => r.plot_id === pid).length;
  edHint(d ? `Draft from ${ago(d.at)} restored.` : pend ? `${pend} change(s) on this plot are already awaiting approval (orange).` : null);
}
function endMapEdit() {
  if (!ED.on) return;
  ED.on = false;
  map.off("click", edClick); map.off("dblclick", edDbl); map.off("mousemove", edMove); map.off("mouseout", edOut);
  map.off("zoomend", edRender); map.off("moveend", edCollectSnaps);
  document.removeEventListener("keydown", edKey);
  window.removeEventListener("beforeunload", edUnload);
  map.removeLayer(ED.group); map.removeLayer(ED.ctx);
  ED.bar?.remove(); ED.card?.remove(); ED.card = null;
  map.getContainer().parentElement.classList.remove("editing", "ed-saving");
  map.getContainer().classList.remove("map-editing", "ed-tracing", "ed-moving");
  map.doubleClickZoom.enable(); map.dragging.enable();
}
function edUnload(e) { if (ED.on && ED.dirty) { edAutosave(); e.preventDefault(); e.returnValue = ""; } }

// --- toolbar
function edBuildBar() {
  const k = ED_KIND[ED.kind], bar = L.DomUtil.create("div", "edit-dock", map.getContainer().parentElement);
  L.DomEvent.disableClickPropagation(bar); L.DomEvent.disableScrollPropagation(bar);
  const title = ED.kind === "building_edit" ? `Correct ${ED.target} on plot ${ED.pid}` : ED.kind === "parcel_add" ? "Add a new plot" : `${k.label} · plot ${ED.pid}`;
  bar.innerHTML = `<div class="ed-head"><span class="ed-swatch" style="background:${k.color}"></span><div class="ed-title"><b>${esc(title)}</b><div class="ed-hint" id="ed-hint"></div></div><div class="ed-stat mono" id="ed-stat"></div></div>
    <div class="ed-tools" role="toolbar" aria-label="Editing tools">
      <button class="tb" id="ed-undo" title="Undo (Ctrl+Z)">${EI.undo}<span>Undo</span></button>
      <button class="tb" id="ed-redo" title="Redo (Ctrl+Y)">${EI.redo}<span>Redo</span></button>
      <span class="tsep"></span>
      <button class="tb" id="ed-snap" title="Snap to nearby corners and edges (S). Hold Alt to place freely.">${EI.magnet}<span>Snap</span></button>
      <button class="tb" id="ed-square" title="Make corners that are nearly 90° exactly square (Q)">${EI.square}<span>Square</span></button>
      <button class="tb" id="ed-simplify" title="Remove corners that do not change the shape (within 30 cm)">${EI.simplify}<span>Simplify</span></button>
      <button class="tb" id="ed-trace" title="Copy an outline from ${esc(ED_KIND[ED.kind].trace.map(t => TRACE_NAMES[t]).join(" or "))} (T)">${EI.trace}<span>Trace</span></button>
      <button class="tb" id="ed-reset" title="Start again from the original shape">${EI.reset}<span>Reset</span></button>
      <span class="tgrow"></span>
      <button class="btn small quiet" id="ed-cancel" title="Esc">Cancel</button>
      <button class="btn small" id="ed-draft" title="Keep this on this device and finish later">Save draft</button>
      <button class="btn small primary" id="ed-save" title="Enter">Save</button>
    </div>`;
  ED.bar = bar;
  $("#ed-undo").onclick = edUndo; $("#ed-redo").onclick = edRedo;
  $("#ed-snap").onclick = () => { ED.snap = !ED.snap; edHideSnap(); edToolState(); edHint(ED.snap ? "Snapping on." : "Snapping off: corners go exactly where you click."); };
  $("#ed-square").onclick = edSquare; $("#ed-simplify").onclick = edSimplify; $("#ed-trace").onclick = () => edTraceToggle();
  $("#ed-reset").onclick = () => { edPush(); ED.pts = ED.orig.slice(); ED.closed = ED.orig.length >= 3; edRender(); edHint("Back to the original shape."); };
  $("#ed-cancel").onclick = edCancel; $("#ed-draft").onclick = edSaveDraft; $("#ed-save").onclick = edSaveOpen;
}
const eIcon = p => `<svg viewBox="0 0 24 24" aria-hidden="true">${p}</svg>`;
const EI = {
  undo: eIcon('<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>'), redo: eIcon('<path d="m15 14 5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>'),
  magnet: eIcon('<path d="M6 3v8a6 6 0 0 0 12 0V3"/><path d="M6 7h4M14 7h4"/>'), square: eIcon('<path d="M4 20V8M4 20h12"/><path d="M4 14h6v6"/>'),
  simplify: eIcon('<path d="M3 17l6-6 4 4 8-8"/><circle cx="9" cy="11" r="1.4"/><circle cx="13" cy="15" r="1.4"/>'),
  trace: eIcon('<path d="M4 7V4h3M17 4h3v3M20 17v3h-3M7 20H4v-3"/><rect x="8" y="8" width="8" height="8" rx="1"/>'), reset: eIcon('<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>'),
};
function edToolState() {
  if (!ED.on) return;
  $("#ed-undo").disabled = !ED.hist.length; $("#ed-redo").disabled = !ED.fut.length;
  $("#ed-snap").setAttribute("aria-pressed", ED.snap); $("#ed-trace").setAttribute("aria-pressed", ED.trace);
  $("#ed-square").disabled = !(ED.closed && ED.pts.length >= 4);
  $("#ed-simplify").disabled = !(ED.closed && ED.pts.length > 4);
  $("#ed-reset").disabled = !ED.dirty;
  $("#ed-save").disabled = !(ED.closed && ED.pts.length >= 3);
}
function edHint(msg, bad = false) {
  const el = $("#ed-hint"); if (!el) return;
  el.classList.toggle("bad-text", bad);
  el.textContent = msg || (ED.trace ? `Click an outline from ${ED_KIND[ED.kind].trace.map(t => TRACE_NAMES[t]).join(" or ")} to copy it. Esc to stop.`
    : !ED.closed ? (ED.pts.length ? "Keep clicking corners. Double-click, or click the first corner, to close the shape." : ED.kind === "parcel_add" ? "Pan to the land, then click each corner of the new plot (wall, fence or survey stones)." : `Click the first corner${ED.kind === "geometry" ? "" : " of the roof"}. Tip: press T to copy a detected outline.`)
    : "Drag corners or the whole shape. Drag an edge dot to add a corner; right-click a corner to delete it.");
}
function edStat(extra) {
  const el = $("#ed-stat"); if (!el) return;
  const pts = extra ? [...ED.pts, extra] : ED.pts, n = pts.length;
  if (n < 3) { el.textContent = `${ED.pts.length} corner${ED.pts.length === 1 ? "" : "s"}`; return; }
  const ring = pts.map(p => [p.lng, p.lat]); ring.push(ring[0]);
  const a = geoArea(ring);
  let txt = `${num(a)} m² · ${ED.pts.length} corners`;
  el.title = `Perimeter ${num(geoPerimeter(ring))} m`;
  if (ED.orig.length >= 3) {
    const o = ED.orig.map(p => [p.lng, p.lat]); o.push(o[0]);
    const d = a - geoArea(o);
    if (Math.abs(d) >= 0.05) txt += ` · ${d > 0 ? "+" : "−"}${num(Math.abs(d))} m² (${d > 0 ? "+" : "−"}${num(Math.abs(d) / geoArea(o) * 100)}%)`;
  }
  el.textContent = txt;
}

// --- history
const edSnapshot = () => ({ pts: ED.pts.map(p => L.latLng(p.lat, p.lng)), closed: ED.closed });
function edPush() { ED.hist.push(edSnapshot()); if (ED.hist.length > 200) ED.hist.shift(); ED.fut = []; ED.dirty = true; }
function edUndo() { const h = ED.hist.pop(); if (!h) return; ED.fut.push(edSnapshot()); Object.assign(ED, h); edRender(); edHint(); }
function edRedo() { const h = ED.fut.pop(); if (!h) return; ED.hist.push(edSnapshot()); Object.assign(ED, h); edRender(); edHint(); }
let edSaveTimer = 0;
function edAutosave() {
  if (!ED.on || !ED.dirty) return;
  putDraft({ pid: ED.pid, kind: ED.kind, target: ED.target, pts: ED.pts.map(p => [p.lat, p.lng]), closed: ED.closed, floors: ED.floors, reason: ED.reason, at: new Date().toISOString() });
}

// --- snapping to corners (12 px) and edges (8 px) of plots and buildings in view
function edCollectSnaps() {
  if (!ED.on) return;
  const b = map.getBounds().pad(0.25), V = [], S = [];
  const add = ring => { let prev = null; for (const [x, y] of ring) { const ll = L.latLng(y, x), inb = b.contains(ll); if (inb) V.push(ll); if (prev && (inb || b.contains(prev))) S.push([prev, ll]); prev = ll; } };
  for (const k of ["parcels", "buildings", "ai_buildings", "footprints", "municipal"]) for (const f of R.layers[k]?.features || []) {
    if (k === "buildings" && f.properties.id === ED.target) continue;
    const g = f.geometry; if (!g) continue;
    (g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : []).forEach(poly => poly.forEach(add));
  }
  ED.snapV = V; ED.snapS = S;
}
function edSnap(ll, oe) {
  ED.snapHit = null;
  if (!ED.snap || oe?.altKey) return ll;
  const p = map.latLngToContainerPoint(ll); let best = null, bd = 12;
  for (const v of ED.snapV) { const q = map.latLngToContainerPoint(v), d = Math.abs(q.x - p.x) + Math.abs(q.y - p.y); if (d < bd * 1.4 && p.distanceTo(q) < bd) { bd = p.distanceTo(q); best = v; } }
  if (best) { ED.snapHit = "corner"; return L.latLng(best.lat, best.lng); }
  bd = 8;
  for (const [a, c] of ED.snapS) {
    const A = map.latLngToContainerPoint(a), C = map.latLngToContainerPoint(c);
    if (Math.min(A.x, C.x) - bd > p.x || Math.max(A.x, C.x) + bd < p.x || Math.min(A.y, C.y) - bd > p.y || Math.max(A.y, C.y) + bd < p.y) continue;
    const q = L.LineUtil.closestPointOnSegment(p, A, C), d = p.distanceTo(q);
    if (d < bd) { bd = d; best = q; }
  }
  if (best) { ED.snapHit = "edge"; return map.containerPointToLatLng(best); }
  return ll;
}
function edShowSnap(ll) {
  if (!ED.snapHit) return edHideSnap();
  ED.snapMark.setLatLng(ll).setStyle({ radius: ED.snapHit === "corner" ? 8 : 6 });
  if (!ED.group.hasLayer(ED.snapMark)) ED.group.addLayer(ED.snapMark);
}
function edHideSnap() { if (ED.snapMark && ED.group.hasLayer(ED.snapMark)) ED.group.removeLayer(ED.snapMark); }

// --- drawing
const mid = (a, b) => L.latLng((a.lat + b.lat) / 2, (a.lng + b.lng) / 2);
const lenTxt = (a, b) => { const d = map.distance(a, b); return d < 10 ? `${d.toFixed(1)} m` : `${Math.round(d)} m`; };
function edRender(save = true) {
  if (!ED.on) return;
  ED.group.clearLayers();
  const n = ED.pts.length, col = ED_KIND[ED.kind].color;
  ED.shape = null;
  if (ED.closed && n >= 3) {
    ED.shape = L.polygon(ED.pts, { color: col, weight: 2.5, fillColor: col, fillOpacity: 0.22, className: "ed-body", bubblingMouseEvents: false }).addTo(ED.group);
    ED.shape.on("mousedown", edMoveStart);
  } else if (n >= 2) ED.shape = L.polyline(ED.pts, { color: col, weight: 2.5, interactive: false }).addTo(ED.group);
  ED.band = L.polyline([], { color: col, weight: 2, opacity: 0.95, interactive: false }).addTo(ED.group);
  ED.bandClose = L.polyline([], { color: col, weight: 1.2, opacity: 0.55, dashArray: "4 6", interactive: false }).addTo(ED.group);
  ED.snapMark = L.circleMarker([0, 0], { radius: 7, color: "#ffffff", weight: 2, fillColor: col, fillOpacity: 0.45, interactive: false });
  ED.mm = [];
  const edges = ED.closed ? n : n - 1;
  for (let i = 0; i < edges; i++) ED.mm.push(edMid(i));
  ED.vm = ED.pts.map((p, i) => edVertex(p, i));
  edStat(); edToolState();
  if (save) edAutosave();
}
function edVertex(p, i) {
  const first = i === 0 && !ED.closed;
  const m = L.marker(p, { draggable: true, keyboard: false, zIndexOffset: 1000, icon: L.divIcon({ className: "ed-v" + (first ? " first" : ""), iconSize: [14, 14] }) }).addTo(ED.group);
  m.on("dragstart", () => { edPush(); ED.dragging = true; });
  m.on("drag", e => {
    const s = edSnap(e.latlng, e.originalEvent);
    if (s !== e.latlng) m.setLatLng(s);
    ED.pts[i] = s; edShowSnap(s); edLive(i);
  });
  m.on("dragend", () => { ED.dragging = false; ED.justDragged = true; setTimeout(() => ED.justDragged = false, 50); edHideSnap(); edRender(); });
  m.on("contextmenu", e => { L.DomEvent.preventDefault(e.originalEvent); edDelete(i); });
  m.on("mouseover", () => { ED.hover = i; }); m.on("mouseout", () => { ED.hover = -1; });
  m.on("click", () => { if (!ED.justDragged && first && ED.pts.length >= 3) edClose(); });
  return m;
}
function edMid(i) {
  const n = ED.pts.length, a = ED.pts[i], b = ED.pts[(i + 1) % n];
  const px = map.latLngToContainerPoint(a).distanceTo(map.latLngToContainerPoint(b)), show = px > 46, grab = ED.closed && px > 30;
  const m = L.marker(mid(a, b), { draggable: grab, interactive: grab, keyboard: false,
    icon: L.divIcon({ className: "ed-m" + (grab ? "" : " label-only"), html: `<i></i><span${show ? "" : ' hidden'}>${lenTxt(a, b)}</span>`, iconSize: [10, 10] }) }).addTo(ED.group);
  if (grab) {
    m.on("dragstart", () => { edPush(); ED.pts.splice(i + 1, 0, m.getLatLng()); ED.dragging = true; });
    m.on("drag", e => { const s = edSnap(e.latlng, e.originalEvent); if (s !== e.latlng) m.setLatLng(s); ED.pts[i + 1] = s; edShowSnap(s); ED.shape.setLatLngs(ED.pts); edStat(); });
    m.on("dragend", () => { ED.dragging = false; ED.justDragged = true; setTimeout(() => ED.justDragged = false, 50); edHideSnap(); edRender(); });
    m.on("click", () => { if (ED.justDragged) return; edPush(); ED.pts.splice(i + 1, 0, m.getLatLng()); edRender(); });
  }
  return m;
}
function edUpdateMid(i) {
  const n = ED.pts.length;
  if (ED.closed) i = (i + n) % n; else if (i < 0 || i >= n - 1) return;
  const m = ED.mm[i]; if (!m) return;
  const a = ED.pts[i], b = ED.pts[(i + 1) % n];
  m.setLatLng(mid(a, b));
  const el = m.getElement()?.querySelector("span"); if (el) el.textContent = lenTxt(a, b);
}
function edLive(i) { ED.shape?.setLatLngs(ED.pts); edUpdateMid(i - 1); edUpdateMid(i); edStat(); }
function edDelete(i) {
  if (ED.closed && ED.pts.length <= 3) return edHint("A shape needs at least 3 corners.", true);
  edPush(); ED.pts.splice(i, 1); ED.hover = -1;
  if (ED.pts.length < 3) ED.closed = false;
  edRender();
}
function edClose() {
  if (ED.pts.length < 3) return;
  edPush(); ED.closed = true; ED.band?.setLatLngs([]); ED.bandClose?.setLatLngs([]); edHideSnap(); edRender();
  edHint(`Shape closed. Adjust it${ED.kind !== "geometry" ? ", press Q to square the corners," : ""} then Save (Enter).`);
}
function edClick(e) {
  if (ED.justDragged || ED.saveOpen) return;
  if (ED.trace) return edTraceAt(e.latlng);
  if (ED.closed) return;
  const n = ED.pts.length;
  if (n >= 3 && map.latLngToContainerPoint(ED.pts[0]).distanceTo(e.containerPoint) < 14) return edClose();
  const p = edSnap(e.latlng, e.originalEvent);
  if (n && map.latLngToContainerPoint(ED.pts[n - 1]).distanceTo(map.latLngToContainerPoint(p)) < 4) return;   // 2nd click of a double-click
  edPush(); ED.pts.push(p); edRender(); edHint();
}
function edDbl(e) { L.DomEvent.stop(e); if (!ED.closed && ED.pts.length >= 3) edClose(); }
function edMove(e) {
  if (ED.dragging || ED.saveOpen) return;
  ED.mouse = e;
  if (ED.raf) return;
  ED.raf = requestAnimationFrame(() => {
    ED.raf = 0; if (!ED.on) return;
    const ev = ED.mouse;
    if (ED.trace) return edTraceHover(ev.latlng);
    if (ED.closed) return;
    const p = edSnap(ev.latlng, ev.originalEvent); edShowSnap(p);
    const n = ED.pts.length;
    if (n) { ED.band.setLatLngs([ED.pts[n - 1], p]); ED.bandClose.setLatLngs(n >= 2 ? [p, ED.pts[0]] : []); edStat(p); }
  });
}
function edOut() { if (!ED.on || ED.closed) return; ED.band?.setLatLngs([]); ED.bandClose?.setLatLngs([]); edHideSnap(); edStat(); }
function edMoveStart(e) {
  if (e.originalEvent.button !== 0 || ED.saveOpen || ED.trace) return;
  L.DomEvent.stop(e);
  const start = e.latlng, orig = ED.pts.map(p => L.latLng(p.lat, p.lng)), before = edSnapshot();
  let moved = false;
  map.dragging.disable(); ED.dragging = true; map.getContainer().classList.add("ed-moving");
  const move = ev => {
    const dl = ev.latlng.lat - start.lat, dg = ev.latlng.lng - start.lng;
    moved = true;
    ED.pts = orig.map(p => L.latLng(p.lat + dl, p.lng + dg));
    ED.shape.setLatLngs(ED.pts); ED.vm.forEach((m, i) => m.setLatLng(ED.pts[i])); ED.mm.forEach((m, i) => edUpdateMid(i)); edStat();
  };
  const up = () => {
    map.off("mousemove", move); document.removeEventListener("mouseup", up);
    map.dragging.enable(); ED.dragging = false; map.getContainer().classList.remove("ed-moving");
    if (moved) { ED.hist.push(before); ED.fut = []; ED.dirty = true; ED.justDragged = true; setTimeout(() => ED.justDragged = false, 50); edRender(); }
  };
  map.on("mousemove", move); document.addEventListener("mouseup", up);
}

// --- square corners (orthogonalise corners within 25° of a right angle, in metres)
function edSquare() {
  if (!(ED.closed && ED.pts.length >= 4)) return edHint("Close a shape with at least 4 corners first.", true);
  const o = ED.pts[0], kx = 111320 * Math.cos(o.lat * Math.PI / 180), ky = 110540;
  let P = ED.pts.map(p => [(p.lng - o.lng) * kx, (p.lat - o.lat) * ky]);
  const n = P.length, lower = Math.cos(65 * Math.PI / 180);
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1]], nrm = v => { const l = Math.hypot(v[0], v[1]) || 1; return [v[0] / l, v[1] / l]; };
  const dotAt = i => { const a = nrm(sub(P[(i - 1 + n) % n], P[i])), b = nrm(sub(P[(i + 1) % n], P[i])); return a[0] * b[0] + a[1] * b[1]; };
  const score = () => { let s = 0; for (let i = 0; i < n; i++) { const d = Math.abs(dotAt(i)); if (d < lower) s += d; } return s; };
  if (score() < 1e-4) return edHint(P.some((_, i) => Math.abs(dotAt(i)) < lower) ? "The corners are already square." : "No corner is close enough to 90° to square.");
  for (let it = 0; it < 1000 && score() > 1e-6; it++) {
    const mv = P.map((c, i) => {
      let p = sub(P[(i - 1 + n) % n], c), q = sub(P[(i + 1) % n], c);
      const sc = 2 * Math.min(Math.hypot(...p), Math.hypot(...q)); p = nrm(p); q = nrm(q);
      const dot = p[0] * q[0] + p[1] * q[1];
      if (Math.abs(dot) >= lower) return [0, 0];
      const v = nrm([p[0] + q[0], p[1] + q[1]]);
      return [v[0] * 0.1 * dot * sc, v[1] * 0.1 * dot * sc];
    });
    P = P.map((c, i) => [c[0] + mv[i][0], c[1] + mv[i][1]]);
  }
  const pts = P.map(([x, y]) => L.latLng(o.lat + y / ky, o.lng + x / kx)), ring = pts.map(p => [p.lng, p.lat]); ring.push(ring[0]);
  if (edSelfCrosses(ring)) return edHint("Squaring would make the outline cross itself. Adjust the corners first.", true);
  edPush(); ED.pts = pts; edRender(); edHint("Corners squared. Undo (Ctrl+Z) if you preferred the original.");
}

// --- simplify: drop corners that sit on a straight line or on top of each other (30 cm tolerance)
function edSimplify() {
  if (!(ED.closed && ED.pts.length > 4)) return;
  const o = ED.pts[0], kx = 111320 * Math.cos(o.lat * Math.PI / 180), ky = 110540;
  const P = ED.pts.map(p => L.point((p.lng - o.lng) * kx, (p.lat - o.lat) * ky));
  const S = L.LineUtil.simplify([...P, P[0]], 0.3).slice(0, -1);
  if (S.length < 3 || S.length === P.length) return edHint("No corner can be removed without changing the shape.");
  const pts = S.map(q => L.latLng(o.lat + q.y / ky, o.lng + q.x / kx)), ring = pts.map(p => [p.lng, p.lat]); ring.push(ring[0]);
  if (edSelfCrosses(ring)) return edHint("Simplifying would make the outline cross itself.", true);
  const n = ED.pts.length;
  edPush(); ED.pts = pts; edRender();
  edHint(`Removed ${n - pts.length} unneeded corner${n - pts.length === 1 ? "" : "s"}; the outline moved by less than 30 cm.`);
}

// --- trace: copy an outline that another source already detected
function edTraceToggle(on = !ED.trace) {
  ED.trace = on; ED.traceHi && ED.group.removeLayer(ED.traceHi); ED.traceHi = null; ED.traceHit = null;
  map.getContainer().classList.toggle("ed-tracing", on);
  ED.band?.setLatLngs([]); ED.bandClose?.setLatLngs([]); edHideSnap();
  edToolState(); edHint();
}
function edTraceFind(ll) {
  for (const k of ED_KIND[ED.kind].trace) for (const f of R.layers[k]?.features || []) {
    const r = ringOf(f.geometry);
    if (r.length > 3 && inRing(ll.lng, ll.lat, r)) return { k, f, r };
  }
  return null;
}
function edTraceHover(ll) {
  const hit = edTraceFind(ll);
  if (hit?.f === ED.traceHit?.f) return;
  if (ED.traceHi) ED.group.removeLayer(ED.traceHi);
  ED.traceHit = hit; ED.traceHi = hit ? L.geoJSON(hit.f, { style: { color: "#ffffff", weight: 2.5, dashArray: "4 3", fillColor: "#ffffff", fillOpacity: 0.18 }, interactive: false }).addTo(ED.group) : null;
}
function edTraceAt(ll) {
  const hit = edTraceFind(ll);
  if (!hit) return edHint(`No outline from ${ED_KIND[ED.kind].trace.map(t => TRACE_NAMES[t]).join(" or ")} here. Turn that layer on to see them, or draw by hand.`, true);
  edPush();
  let r = hit.r.slice(0, -1);
  if (r.length > 60) r = r.filter((_, i) => i % Math.ceil(r.length / 60) === 0);
  ED.pts = r.map(([x, y]) => L.latLng(y, x)); ED.closed = true;
  edTraceToggle(false); edRender();
  edHint(`Copied from ${TRACE_NAMES[hit.k]}. Check it against the imagery: detected outlines can be a few metres off. Drag the shape to align it.`);
}

// --- keys
function edKey(e) {
  if (!ED.on) return;
  if (e.target.closest?.("input, textarea, select")) { if (e.key === "Escape" && ED.saveOpen) edSaveClose(); return; }
  if (e.target.closest?.(".overlay:not(.hidden)")) return;
  const k = e.key.toLowerCase(), mod = e.ctrlKey || e.metaKey;
  if (k === "escape") { e.preventDefault(); ED.trace ? edTraceToggle(false) : ED.saveOpen ? edSaveClose() : edCancel(); }
  else if (k === "enter") { e.preventDefault(); if (ED.saveOpen) return; ED.closed ? edSaveOpen() : edClose(); }
  else if (mod && k === "z") { e.preventDefault(); e.shiftKey ? edRedo() : edUndo(); }
  else if (mod && k === "y") { e.preventDefault(); edRedo(); }
  else if (ED.saveOpen || mod) return;
  else if (k === "delete" || k === "backspace") { e.preventDefault(); if (ED.hover >= 0) edDelete(ED.hover); else if (!ED.closed) edUndo(); }
  else if (k === "s") $("#ed-snap").click();
  else if (k === "q") edSquare();
  else if (k === "t") edTraceToggle();
}

// --- leaving
function edCancel() {
  if (ED.dirty && !confirm("Discard these map changes? (Use “Save draft” to keep them for later.)")) return;
  dropDraft(ED.pid, ED.kind, ED.target);
  const pid = ED.pid; endMapEdit(); inspect(pid);
}
function edSaveDraft() {
  if (!ED.dirty) { const pid = ED.pid; endMapEdit(); inspect(pid); return; }
  if (ED.card) { ED.reason = ED.card.reason.value; ED.floors = ED.card.floors?.value ?? ED.floors; }
  edAutosave();
  const pid = ED.pid; endMapEdit();
  toast("Draft saved on this device. Resume it from the plot, or from the Requests tab.");
  inspect(pid);
}

// --- save: checks, then a change request for a survey approver
function edChecks(ring) {
  const out = [], area = geoArea(ring), plotRing = ED.feat ? ringOf(ED.feat.geometry) : null;
  if (ED.kind === "parcel_add") {
    out.push(area >= 20 && area <= 200000 ? { ok: true, text: `${num(area)} m² plot` } : { ok: false, text: `${num(area)} m² is not plausible for a plot (20 – 200,000 m²)` });
    const [x0, y0, x1, y1] = bboxOf(ring); let worst = null;
    for (const f of R.layers.parcels.features) {
      const r = ringOf(f.geometry), b = bboxOf(r);
      if (b[2] < x0 || x1 < b[0] || b[3] < y0 || y1 < b[1]) continue;
      const ov = overlapArea(ring, r);
      if (ov > (worst?.ov || 0)) worst = { ov, pid: f.properties.plot_id };
    }
    out.push(!worst || worst.ov <= Math.max(2, 0.02 * area) ? { ok: true, text: worst && worst.ov > 0.5 ? `Touches plot ${worst.pid} (about ${num(worst.ov)} m²), within tolerance` : "On free land: no existing plot overlaps" }
      : { ok: false, text: `Overlaps plot ${worst.pid} by about ${num(worst.ov)} m²: draw on free land, or edit that plot instead` });
    out.push({ warn: true, text: "Attach the survey sketch or measurement: a new plot needs one." });
    out.push({ warn: true, text: "After approval it starts in review with no records; Revenue is asked to create its RoR." });
    return out;
  }
  if (ED.kind !== "geometry") {
    out.push(area >= 6 && area <= 20000 ? { ok: true, text: `${num(area)} m² footprint` } : { ok: false, text: `${num(area)} m² is not plausible for one building (6 – 20,000 m²)` });
    const inside = overlapArea(ring, plotRing) / area;
    out.push({ ok: inside >= 0.5, text: `${Math.round(inside * 100)}% inside plot ${ED.pid}${inside >= 0.5 ? "" : ": at least half must be on this plot"}` });
    if (inside >= 0.5 && inside < 0.97) out.push({ warn: true, text: "Part of it crosses the plot line. Check whether it really does." });
    const dup = plotBuildings(ED.pid).find(b => { if (b.properties.id === ED.target) return false; const r = ringOf(b.geometry); return overlapArea(ring, r) > 0.5 * Math.min(area, geoArea(r)); });
    out.push(dup ? { ok: false, text: `Mostly overlaps ${dup.properties.id}, which is already mapped. Correct that one instead.` } : { ok: true, text: "Not a duplicate of a mapped building" });
  } else {
    const [x0, y0, x1, y1] = bboxOf(ring); let worst = null;
    for (const f of R.layers.parcels.features) {
      if (f.properties.plot_id === ED.pid) continue;
      const r = ringOf(f.geometry), b = bboxOf(r);
      if (b[2] < x0 || x1 < b[0] || b[3] < y0 || y1 < b[1]) continue;
      const ov = overlapArea(ring, r);
      if (ov > (worst?.ov || 0)) worst = { ov, pid: f.properties.plot_id };
    }
    const lim = Math.max(2, 0.02 * area);
    out.push(!worst || worst.ov <= 0.5 ? { ok: true, text: "No overlap with neighbouring plots" }
      : worst.ov <= lim ? { warn: true, text: `Touches plot ${worst.pid} (about ${num(worst.ov)} m²), within tolerance` }
      : { ok: false, text: `Overlaps plot ${worst.pid} by about ${num(worst.ov)} m²: pull the line back, or change both plots` });
    const o = geoArea(plotRing), d = area - o;
    out.push({ ok: true, warn: Math.abs(d) / o > 0.1, text: `Area ${d >= 0 ? "+" : "−"}${num(Math.abs(d))} m² (${d >= 0 ? "+" : "−"}${num(Math.abs(d) / o * 100)}%) against the surveyed ${num(o)} m²${Math.abs(d) / o > 0.1 ? ": a large change" : ""}` });
    out.push({ warn: true, text: "Attach the survey sketch or measurement: boundary changes need one." });
  }
  return out;
}
function edSaveOpen() {
  if (!(ED.closed && ED.pts.length >= 3)) return edHint("Close the shape first: double-click or click the first corner.", true);
  const ring = ED.pts.map(p => [+p.lng.toFixed(8), +p.lat.toFixed(8)]); ring.push(ring[0]);
  if (edSelfCrosses(ring)) return edHint("The outline crosses itself. Move or delete a corner first.", true);
  if (ED.kind !== "building_add" && !ED.dirty && ED.kind !== "building_edit") return edHint("Nothing has changed yet.", true);
  if (ED.trace) edTraceToggle(false);
  const checks = edChecks(ring), blocked = checks.some(c => c.ok === false);
  ED.saveOpen = true; ED.ring = ring;
  map.getContainer().parentElement.classList.add("ed-saving");
  const isNew = ED.kind === "parcel_add", bld = !["geometry", "parcel_add"].includes(ED.kind), evNeed = !bld;
  const card = L.DomUtil.create("form", "ed-card", map.getContainer().parentElement);
  L.DomEvent.disableClickPropagation(card); L.DomEvent.disableScrollPropagation(card);
  card.innerHTML = `<h3>Save: ${esc(ED.kind === "building_edit" ? `correct ${ED.target}` : ED_KIND[ED.kind].label.toLowerCase())}</h3>
    <div class="faint" style="margin:-4px 0 10px">${isNew ? "" : `Plot ${esc(ED.pid)} · `}${esc($("#ed-stat").textContent)} · ${esc($("#ed-stat").title.toLowerCase())}</div>
    <ul class="ed-checks">${checks.map(c => `<li class="${c.ok === false ? "bad" : c.warn ? "warn" : "ok"}">${esc(c.text)}</li>`).join("")}</ul>
    ${isNew ? `<label class="field">New plot number <span class="faint">(survey no. / hissa, e.g. ${esc(suggestPlotNo())})</span><input name="plot_no" required pattern="[0-9A-Za-z]{1,8}(/[0-9A-Za-z]{1,6}){0,3}" value="${esc(ED.plotNo || suggestPlotNo())}"></label>` : ""}
    ${bld ? `<label class="field">Floors <span class="faint">(ground only = 1; leave blank if unknown)</span><input name="floors" type="number" min="0" max="60" step="1" value="${esc(ED.floors ?? "")}"></label>` : ""}
    <label class="field">Reason <input name="reason" required minlength="10" value="${esc(ED.reason)}" placeholder="${bld ? "e.g. New house seen on site visit, 2026-09-20" : "e.g. Re-survey by DGPS, sketch no. 45/2026"}"></label>
    <label class="field">Supporting document <span class="faint">${evNeed ? "(required)" : "(optional: site photo or sketch)"}</span><input type="file" name="evidence" accept=".pdf,.jpg,.jpeg,.png" ${evNeed ? "required" : ""}></label>
    <p class="bad-text hidden" id="eds-err" role="alert"></p>
    <div class="ed-card-foot"><button type="button" class="btn small quiet" id="eds-back">Back</button><button type="button" class="btn small" id="eds-draft">Save draft</button><button class="btn small primary" type="submit" ${blocked ? "disabled" : ""}>Submit for approval</button></div>
    <p class="faint ed-note">${blocked ? "Fix the item marked in red first." : "A survey approver checks this before the map changes. You cannot approve your own request."}</p>`;
  ED.card = card;
  card.reason.oninput = () => { ED.reason = card.reason.value; };
  card.plot_no && (card.plot_no.oninput = () => { ED.plotNo = card.plot_no.value; });
  card.floors && (card.floors.oninput = () => { ED.floors = card.floors.value; });
  $("#eds-back").onclick = edSaveClose; $("#eds-draft").onclick = edSaveDraft;
  card.onsubmit = edSubmit;
  setTimeout(() => (card.reason.value ? card.querySelector("[type=submit]") : card.reason).focus(), 30);
}
function suggestPlotNo() {
  const c = map.getCenter();
  let best = null, bd = Infinity;
  for (const f of R.layers.parcels.features) {
    const r = ringOf(f.geometry); if (!r.length) continue;
    const d = map.distance(c, [r[0][1], r[0][0]]);
    if (d < bd) { bd = d; best = f.properties.plot_id; }
  }
  const taken = new Set(R.layers.parcels.features.map(f => f.properties.plot_id));
  const sy = parseInt(String(best || "200").split("/")[0], 10) || 200;
  for (let h = 1; h < 200; h++) if (!taken.has(`${sy}/${h}`)) return `${sy}/${h}`;
  const top = Math.max(...R.layers.parcels.features.map(f => parseInt(f.properties.plot_id, 10) || 0)) + 1;
  return `${top}/1`;
}
function edSaveClose() { ED.saveOpen = false; ED.card?.remove(); ED.card = null; map.getContainer().parentElement.classList.remove("ed-saving"); edAutosave(); }
async function edSubmit(e) {
  e.preventDefault();
  const card = ED.card, btn = card.querySelector("[type=submit]"), fd = new FormData(card);
  const poly = { type: "Polygon", coordinates: [ED.ring] }, floors = fd.get("floors"), plotNo = (fd.get("plot_no") || "").trim();
  fd.delete("floors"); fd.delete("plot_no");
  if (ED.kind === "parcel_add" && R.layers.parcels.features.some(f => f.properties.plot_id === plotNo)) {
    $("#eds-err").textContent = `Plot ${plotNo} already exists. Choose another number.`; $("#eds-err").classList.remove("hidden"); return;
  }
  fd.set("plot_id", ED.kind === "parcel_add" ? plotNo : ED.pid); fd.set("field", ED.kind);
  fd.set("new_value", JSON.stringify(["geometry", "parcel_add"].includes(ED.kind) ? poly
    : { footprint: poly, floors: floors === "" || floors == null ? null : +floors, ...(ED.kind === "building_edit" ? { target: ED.target } : {}) }));
  if (!fd.get("evidence")?.size) fd.delete("evidence");
  btn.disabled = true; btn.textContent = "Sending…";
  try {
    const cr = await api("/api/change-requests", { method: "POST", body: fd });
    dropDraft(ED.pid, ED.kind, ED.target);
    const pid = ED.pid, isNewPlot = ED.kind === "parcel_add"; endMapEdit();
    toast(`${cr.code} saved and sent to a survey approver`);
    loadRequestCount(); await loadProposals();
    if (isNewPlot) showProposed(PROPOSALS.find(r => r.id === cr.id) || cr); else inspect(pid);
    if (tab === "requests") render();
  } catch (err) {
    $("#eds-err").textContent = err.message; $("#eds-err").classList.remove("hidden");
    btn.disabled = false; btn.textContent = "Submit for approval";
  }
}

// --- pending map proposals, shown on the map for everyone
let PROPOSALS = [];
async function loadProposals() {
  try { PROPOSALS = (await api("/api/change-requests?status=pending")).filter(r => MAP_FIELDS.includes(r.field)); } catch { PROPOSALS = []; }
  if (layers.proposals) map.removeLayer(layers.proposals);
  const g = L.layerGroup();
  for (const r of PROPOSALS) {
    const geom = ["geometry", "parcel_add"].includes(r.field) ? r.new_value : r.new_value?.footprint;
    if (!geom) continue;
    L.geoJSON(geom, { style: { color: r.field === "building_remove" ? "#e5484d" : "#ff9f1c", weight: 2, dashArray: "5 4", fillColor: "#ff9f1c", fillOpacity: 0.08 } })
      .bindTooltip(`${esc(r.code)} · ${esc(r.field_label)} · plot ${esc(r.plot_id)}<br>awaiting approval · proposed by ${esc(r.proposed_by)}`, { sticky: true })
      .on("click", () => { if (!ED.on) showProposed(r); }).addTo(g);
  }
  layers.proposals = g;
  applyVisibility();
}
function showProposed(r) {
  if (!r) return;
  selection.clearLayers();
  const plot = parcelIndex[r.plot_id];
  if (plot) L.geoJSON(plot.toGeoJSON(), { style: { color: "#ffffff", weight: 2, dashArray: "6 5", fillOpacity: 0 }, interactive: false }).addTo(selection);
  if (r.field === "building_edit" && r.new_value?.old) L.geoJSON(r.new_value.old, { style: { color: "#ffffff", weight: 1.5, dashArray: "2 4", fillOpacity: 0 }, interactive: false }).addTo(selection);
  const g = ["geometry", "parcel_add"].includes(r.field) ? r.new_value : r.new_value?.footprint;
  const col = r.field === "building_remove" ? "#e5484d" : r.field === "geometry" ? "#5ee0ff" : r.field === "parcel_add" ? "#7cf29c" : "#ffd23f";
  if (g) {
    const l = L.geoJSON(g, { style: { color: col, weight: 3, fillColor: col, fillOpacity: 0.25 }, interactive: false }).addTo(selection);
    map.flyToBounds(l.getBounds().pad(plot ? 1.2 : 2), { duration: 0.5, maxZoom: 21 });
    let extra = "";
    if (r.field === "geometry" && plot) { const d = geoArea(ringOf(g)) - geoArea(ringOf(plot.toGeoJSON().geometry)); extra = ` · area ${d >= 0 ? "+" : "−"}${num(Math.abs(d))} m²`; }
    toast(`${r.code}: ${r.new_display}${extra}. White dashes = current, colour = proposed.`);
  }
}

let reqFilter = "pending";
const reqPicked = new Set();
function renderRequestsShell() {
  const chips = [["pending", "Awaiting approval"], ["approved", "Approved"], ["rejected", "Rejected"], ["", "All"]]
    .map(([k, l]) => `<button class="chip ${reqFilter === k ? "active" : ""}" data-rf="${k}">${l}</button>`).join("");
  return `<p class="lead">Officers propose changes to the records their department is the authority for. An approver from the same department, never the proposer, approves or rejects. Approved changes update the record, close the conflicts they settle and notify the other departments.</p>
    <div id="inbox"><div class="skel"></div></div><div id="drafts"></div>
    <div class="section-title">Change requests</div><div class="filters">${chips}</div><div id="req-batch"></div><div id="req-list"><div class="skel"></div><div class="skel"></div></div>`;
}
async function loadRequests() {
  $$("[data-rf]").forEach(b => b.onclick = () => { reqFilter = b.dataset.rf; render(); });
  const ds = canMap() ? listDrafts() : [];
  $("#drafts").innerHTML = ds.length ? `<div class="section-title">Unsent map drafts on this device</div>` + ds.map((d, i) => `<div class="bld-row draft"><div>${esc(draftLabel(d))}<div class="faint">${esc(ago(d.at))}</div></div><div class="row-btns"><button class="btn small primary" data-dr="${i}">Resume</button><button class="btn small" data-dx="${i}">Discard</button></div></div>`).join("") : "";
  $$("[data-dr]").forEach(b => b.onclick = () => { const d = ds[b.dataset.dr]; if (parcelProps(d.pid)) startEditor(d.pid, d.kind, d.target); else toast("That plot is not in this area", true); });
  $$("[data-dx]").forEach(b => b.onclick = () => { const d = ds[b.dataset.dx]; dropDraft(d.pid, d.kind, d.target); render(); });
  try {
    const dept = ME.department !== "all" ? ME.department : "";
    const [box, list] = await Promise.all([api("/api/inbox"), api(`/api/change-requests?${new URLSearchParams({ status: reqFilter })}`)]);
    const title = ME.department === "all" ? "All departments" : deptMeta(ME.department).name;
    $("#inbox").innerHTML = `<div class="section-title">${esc(title)}: inbox</div><div class="block">
      <div style="display:flex;gap:18px;flex-wrap:wrap;font-size:12.5px"><span><b class="mono">${box.open_conflicts}</b> open conflicts routed here</span><span><b class="mono">${box.pending_requests}</b> requests awaiting approval</span><span><b class="mono">${box.notifications.length}</b> unread notices</span></div>
      ${Object.entries(box.open_by_type).slice(0, 6).map(([k, v]) => `<div class="faint" style="font-size:12px;margin-top:3px">${esc(k)}: <span class="mono">${v}</span></div>`).join("")}
      ${box.notifications.slice(0, 20).map(n => `<div class="note-row"><div><b>${esc(deptName(n.department))}</b> · ${esc(n.message)}<div class="faint">${esc(n.created_at.replace("T", " "))}</div></div>${can("officer") && canDept(n.department) ? `<button class="btn small" data-ack="${n.id}">Done</button>` : ""}</div>`).join("")}</div>`;
    $$("[data-ack]").forEach(b => b.onclick = async () => { await api(`/api/inbox/${b.dataset.ack}/ack`, { method: "POST" }); loadRequests(); });
    const mine = list.filter(r => !dept || r.department === dept || r.proposed_by === ME.username);
    $("#req-list").innerHTML = mine.map(r => {
      const canDecide = r.status === "pending" && can("reviewer") && canDept(r.department) && r.proposed_by !== ME.username;
      const old = r.old_display, nw = r.new_display, onMap = MAP_FIELDS.includes(r.field);
      const box = canDecide ? `<input type="checkbox" class="pick" data-rp="${r.id}" ${reqPicked.has(r.id) ? "checked" : ""} aria-label="Select for batch decision">` : "";
      return `<div class="cr"><div class="top"><div>${box}<b class="mono">${esc(r.code)}</b> · plot <a href="#" data-plot="${esc(r.plot_id)}">${esc(r.plot_id)}</a> · ${esc(deptName(r.department))}</div><span class="pill ${r.status}">${esc(r.status)}</span></div>
        <div class="diff"><div class="dl">${esc(r.field_label)}</div><div class="was"><span>Now</span>${esc(old ?? "(none)")}</div><div class="will"><span>${r.status === "approved" ? "Changed to" : "Proposed"}</span>${esc(nw ?? "(none)")}</div></div>
        <div class="faint" style="font-size:12px">${esc(r.reason)}</div>
        ${onMap ? `<button class="btn small" data-showcr="${r.id}" style="margin-top:6px">Show on map</button>` : ""}
        <div class="faint" style="font-size:11.5px;margin-top:4px">Proposed by ${esc(r.proposed_by)} · ${esc(r.proposed_at.replace("T", " "))}${r.evidence_sha ? ` · <a href="${wsUrl("/api/evidence/" + r.evidence_sha)}" target="_blank" rel="noopener">${esc(r.evidence_name || "document")}</a> <span class="mono">${esc(r.evidence_sha.slice(0, 10))}…</span>` : r.source_ref ? ` · evidence ${esc(r.source_ref)}` : ""}</div>
        ${r.decided_by ? `<div class="faint" style="font-size:11.5px">${esc(r.status)} by ${esc(r.decided_by)} · ${esc((r.decided_at || "").replace("T", " "))}${r.decision_note ? ` · “${esc(r.decision_note)}”` : ""}</div>` : ""}
        ${canDecide ? `<div class="decide"><button class="btn small quiet" data-like="${esc(r.field)}|${esc(r.department)}" title="Select every pending request of this kind">Select similar</button><input placeholder="Note (required to reject)" data-note="${r.id}"><button class="btn ok small" data-appr="${r.id}">Approve</button><button class="btn small danger" data-rej="${r.id}">Reject</button></div>` : ""}
        ${r.status === "pending" && r.proposed_by === ME.username ? `<div class="decide"><button class="btn small" data-wd="${r.id}">Withdraw</button></div>` : ""}
        ${r.status === "pending" && r.proposed_by === ME.username && can("reviewer") ? `<div class="readonly-note">Four-eyes rule: another approver must decide your own request.</div>` : ""}</div>`;
    }).join("") || `<div class="empty">No requests here.</div>`;
    $$("[data-plot]").forEach(a => a.onclick = e => { e.preventDefault(); inspect(a.dataset.plot, true); });
    $$("[data-showcr]").forEach(b => b.onclick = () => showProposed(mine.find(r => String(r.id) === b.dataset.showcr)));
    const decidableReqs = mine.filter(r => r.status === "pending" && can("reviewer") && canDept(r.department) && r.proposed_by !== ME.username);
    [...reqPicked].forEach(id => { if (!decidableReqs.some(r => r.id === id)) reqPicked.delete(id); });
    const drawBatch = () => {
      const n = reqPicked.size;
      $("#req-batch").innerHTML = n ? `<div class="bulkbar on"><b>${n}</b> selected <input id="batch-note" placeholder="Note for all (required to reject)"><button class="btn small ok" id="batch-ok">Approve ${n}</button><button class="btn small danger" id="batch-no">Reject ${n}</button><button class="btn small quiet" id="batch-clear">Clear</button></div>` : "";
      if (!n) return;
      const go = async approve => {
        const note = $("#batch-note").value.trim();
        if (!approve && note.length < 5) { toast("Say why the requests are rejected", true); return; }
        if (!confirm(`${approve ? "Approve" : "Reject"} ${n} request(s)? Each is checked and audited on its own.`)) return;
        try {
          const res = await api("/api/change-requests/batch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: [...reqPicked], approve, note }) });
          reqPicked.clear();
          toast(`${res.decided.length} ${approve ? "approved" : "rejected"}${res.skipped.length ? `, ${res.skipped.length} skipped: ${[...new Set(res.skipped.map(s => s.reason))].join("; ")}` : ""}`, !!res.skipped.length);
          if (res.decided.some(c => c.status === "approved")) await loadResult(false); else render();
          loadRequestCount(); loadProposals();
        } catch (e) { toast(e.message, true); }
      };
      $("#batch-ok").onclick = () => go(true); $("#batch-no").onclick = () => go(false);
      $("#batch-clear").onclick = () => { reqPicked.clear(); $$("[data-rp]").forEach(b => b.checked = false); drawBatch(); };
    };
    $$("[data-rp]").forEach(b => b.onchange = () => { b.checked ? reqPicked.add(+b.dataset.rp) : reqPicked.delete(+b.dataset.rp); drawBatch(); });
    $$("[data-like]").forEach(b => b.onclick = () => { const [f, d] = b.dataset.like.split("|"); decidableReqs.filter(r => r.field === f && r.department === d).forEach(r => reqPicked.add(r.id)); $$("[data-rp]").forEach(x => x.checked = reqPicked.has(+x.dataset.rp)); drawBatch(); });
    drawBatch();
    const verdict = async (id, ok) => {
      try {
        const r = await api(`/api/change-requests/${id}/${ok ? "approve" : "reject"}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note: $(`[data-note="${id}"]`).value }) });
        if (r.parcel && MAP_FIELDS.includes(r.request.field)) {
          await loadResult(false); toast(`${r.request.code} approved: map updated`); return;
        }
        if (r.parcel) {
          const f = R.layers.parcels.features.find(f => f.properties.plot_id === r.parcel.plot_id);
          if (f) { Object.assign(f.properties, r.parcel); parcelIndex[r.parcel.plot_id]?.setStyle(parcelStyle(f)); }
          r.conflicts.forEach(c => { const i = R.conflicts.findIndex(x => x.id === c.id); if (i >= 0) R.conflicts[i] = c; });
          R.summary = r.summary; buildConflictMarkers(); applyVisibility();
        }
        toast(`${r.request.code} ${r.request.status}`); loadRequestCount(); render();
        if (MAP_FIELDS.includes(r.request.field)) loadProposals();
      } catch (err) { toast(err.message, true); }
    };
    $$("[data-appr]").forEach(b => b.onclick = () => verdict(b.dataset.appr, true));
    $$("[data-rej]").forEach(b => b.onclick = () => verdict(b.dataset.rej, false));
    $$("[data-wd]").forEach(b => b.onclick = async () => { try { await api(`/api/change-requests/${b.dataset.wd}/withdraw`, { method: "POST" }); toast("Withdrawn"); render(); loadProposals(); } catch (err) { toast(err.message, true); } });
  } catch (e) { $("#req-list").innerHTML = `<p class="bad-text">${esc(e.message)}</p>`; }
}
async function loadRequestCount() {
  try {
    const q = new URLSearchParams({ status: "pending" });
    if (ME.department !== "all") q.set("department", ME.department);
    $("#n-requests").textContent = (await api(`/api/change-requests?${q}`)).length;
  } catch { }
}

// ------------------------------------------------------------------ administration
function renderAdminShell() {
  const admin = can("admin");
  return `<p class="lead">${admin ? "State administration: every account, API keys for other departments' systems, and run history." : `Access for ${esc(deptMeta(ME.department).name)}: you can add, change and remove the officers and viewers of your department.`}</p>
    <div class="section-title">Who can grant which role</div><div class="block" id="adm-matrix"><div class="skel"></div></div>
    <div class="section-title">Department access: who sees whose records</div><div class="block" id="adm-dacc"><div class="skel"></div></div>
    <div class="section-title">${admin ? "All accounts" : "Your department's accounts"}</div><div class="block" id="adm-users"><div class="skel"></div></div>
    <form class="block" id="adm-new-user"><b style="font-size:13px">Add an account</b>
      <div class="form-row"><label class="field">Username<input name="username" required pattern="[A-Za-z0-9._]+" placeholder="e.g. rev.officer2"></label>
      <label class="field">Full name<input name="full_name" placeholder="Name and designation"></label></div>
      <div class="form-row"><label class="field">Role<select name="role" id="adm-role"></select></label>
      <label class="field">Department<select name="department" id="adm-dept"></select></label></div>
      <div class="faint" style="font-size:12px;margin-bottom:8px">A one-time password is shown once. The person must change it at first sign-in.</div>
      <button class="btn small primary" type="submit">Create account</button><div id="adm-user-out"></div></form>
    <div class="section-title">Access log</div><div class="block" id="adm-log"><div class="skel"></div></div>
    ${admin ? `<div class="section-title">Recent pipeline runs</div><div class="block" id="adm-runs"><div class="skel"></div></div>
    <div class="section-title">API keys</div><div class="block" id="adm-keys"><div class="skel"></div></div>
    <form class="block" id="adm-new-key"><b style="font-size:13px">Issue a key for another department's system</b>
      <div class="form-row"><label class="field">Name<input name="name" required placeholder="e.g. BESCOM billing system"></label>
      <label class="field">Role<select name="role"><option value="viewer">Viewer (read / OGC API)</option><option value="officer">Officer (can submit changes)</option></select></label></div>
      <label class="field">Department<select name="department">${deptOptions()}</select></label>
      <button class="btn small primary" type="submit">Issue key</button><div id="adm-key-out"></div></form>
    ${WS === "demo" ? `<div class="section-title">Synthetic benchmark</div><div class="block"><button class="btn small danger" id="adm-reset">Regenerate the demo ward</button> <span class="faint" style="font-size:12px">Clears reviewer decisions for this area.</span></div>` : ""}` : ""}`;
}
const ROLE_LABEL = { viewer: "Viewer", officer: "Officer", reviewer: "Approver", admin: "Admin" };
function oneTime(u, pw) {
  return `<div class="keybox">${esc(u)} · one-time password: <b>${esc(pw)}</b></div><div class="faint" style="font-size:12px;margin-top:4px">Shown once. Hand it over securely; it must be changed at first sign-in.</div>`;
}
async function loadAdmin() {
  const admin = can("admin");
  try {
    loadDeptAccess();
    const [mx, users, log] = await Promise.all([api("/api/access/matrix"), api("/api/access/users"), api("/api/access/audit")]);
    const g = mx.you_can_grant;
    $("#adm-matrix").innerHTML = `<table class="t"><tr><th>Role</th><th>Granted, changed and removed by</th></tr>${mx.matrix.map(m => `<tr><td><b>${esc(m.label)}</b></td><td>${esc(m.granted_by)}</td></tr>`).join("")}</table>
      <div class="faint" style="font-size:12px;margin-top:6px">Nobody can change their own access. The last admin cannot be removed. Every change is written to the access log below.</div>`;
    $("#adm-role").innerHTML = g.roles.map(r => `<option value="${r}">${ROLE_LABEL[r]}</option>`).join("");
    $("#adm-dept").innerHTML = g.departments === "any" ? deptOptions() : g.departments.map(d => `<option value="${d}">${esc(deptMeta(d).name)}</option>`).join("");
    const roleSel = u => `<select data-rolefor="${esc(u.username)}" class="ws-select" style="width:auto;padding:3px 6px">${g.roles.map(r => `<option value="${r}" ${r === u.role ? "selected" : ""}>${ROLE_LABEL[r]}</option>`).join("")}${g.roles.includes(u.role) ? "" : `<option selected disabled>${ROLE_LABEL[u.role]}</option>`}</select>`;
    const manageableRow = u => u.username !== ME.username && (admin || ["viewer", "officer"].includes(u.role));
    $("#adm-users").innerHTML = `<table class="t"><tr><th>Account</th><th>Role</th><th>Department</th><th>Granted by</th><th class="num"></th></tr>${users.map(u => `<tr>
      <td><b>${esc(u.username)}</b>${u.active ? "" : ' <span class="pill rejected">disabled</span>'}${u.must_change ? ' <span class="pill pending">one-time password</span>' : ""}<div class="faint">${esc(u.full_name || "")}</div></td>
      <td>${manageableRow(u) ? roleSel(u) : esc(ROLE_LABEL[u.role])}</td>
      <td>${esc(u.department === "all" ? "All (state)" : deptName(u.department))}<div class="faint">${esc(jobTitle(u.role, u.department))}</div></td>
      <td class="faint">${esc(u.role_granted_by || u.created_by || "system")}<div>${esc((u.role_granted_at || u.created_at || "").slice(0, 10))}</div></td>
      <td class="num">${u.username === ME.username ? '<span class="faint">you</span>' : manageableRow(u) ? `<button class="btn small" data-reset="${esc(u.username)}">Reset password</button> <button class="btn small ${u.active ? "danger" : ""}" data-user="${esc(u.username)}" data-active="${u.active ? 0 : 1}">${u.active ? "Disable" : "Enable"}</button>` : ""}</td></tr>`).join("")}</table>`;
    $("#adm-log").innerHTML = `<div class="faint" style="font-size:12px;margin-bottom:6px">${log.verify.ok ? "Hash chain intact" : "Hash chain BROKEN"} · ${log.verify.entries} entries</div>` +
      (log.entries.slice(0, 25).map(e => `<div class="note-row"><div><b>${esc(e.kind.replace("access.", "").replace("_", " "))}</b> · ${esc(e.subject || "")} ${e.detail.to ? `→ ${esc(ROLE_LABEL[e.detail.to.role])} (${esc(e.detail.to.department)})` : e.detail.role ? `as ${esc(ROLE_LABEL[e.detail.role])} (${esc(e.detail.department)})` : ""}<div class="faint">by ${esc(e.actor)} · ${esc(e.ts.replace("T", " "))}</div></div></div>`).join("") || `<span class="faint">No access changes yet.</span>`);
    $$("[data-rolefor]").forEach(sel => sel.onchange = async () => {
      const u = sel.dataset.rolefor;
      if (!confirm(`Change ${u} to ${ROLE_LABEL[sel.value]}? Their current session will end.`)) { loadAdmin(); return; }
      try { await api(`/api/access/users/${encodeURIComponent(u)}/role`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role: sel.value }) }); toast(`${u} is now ${ROLE_LABEL[sel.value]}`); }
      catch (err) { toast(err.message, true); }
      loadAdmin();
    });
    $$("[data-user]").forEach(b => b.onclick = async () => {
      try { await api(`/api/access/users/${encodeURIComponent(b.dataset.user)}/active?active=${b.dataset.active === "1"}`, { method: "POST" }); } catch (err) { toast(err.message, true); }
      loadAdmin();
    });
    $$("[data-reset]").forEach(b => b.onclick = async () => {
      if (!confirm(`Issue a new one-time password for ${b.dataset.reset}?`)) return;
      try { const r = await api(`/api/access/users/${encodeURIComponent(b.dataset.reset)}/reset-password`, { method: "POST" }); $("#adm-user-out").innerHTML = oneTime(r.username, r.one_time_password); loadAdmin(); }
      catch (err) { toast(err.message, true); }
    });
    if (admin) {
      const [runs, keys] = await Promise.all([api("/api/runs?limit=10"), api("/api/admin/api-keys")]);
      $("#adm-runs").innerHTML = runs.length ? `<table class="t"><tr><th>Started</th><th>By</th><th>Status</th><th class="num">Time</th><th class="num">To review</th></tr>${runs.map(r => `<tr><td class="mono">${esc(r.started_at.replace("T", " "))}</td><td>${esc(r.started_by || "")}</td><td>${r.status === "done" ? '<span class="ok-text">done</span>' : r.status === "failed" ? `<span class="bad-text" title="${esc(r.error)}">failed</span>` : esc(r.status)}</td><td class="num mono">${r.seconds != null ? num(r.seconds, 1) + " s" : ""}</td><td class="num mono">${r.summary ? r.summary.status.review : ""}</td></tr>`).join("")}</table>` : `<span class="faint">No runs recorded yet.</span>`;
      $("#adm-keys").innerHTML = keys.length ? `<table class="t"><tr><th>Key</th><th>Role · department</th><th>Last used</th><th></th></tr>${keys.map(k => `<tr><td><b>${esc(k.name)}</b><div class="mono faint">${esc(k.prefix)}…</div></td><td>${esc(k.role)}<div class="faint">${esc(k.department === "all" ? "all" : deptName(k.department))}</div></td><td class="mono">${esc((k.last_used || "never").replace("T", " "))}</td><td class="num">${k.active ? `<button class="btn small danger" data-key="${esc(k.prefix)}">Revoke</button>` : '<span class="faint">revoked</span>'}</td></tr>`).join("")}</table>` : `<span class="faint">No keys issued yet.</span>`;
      $$("[data-key]").forEach(b => b.onclick = async () => { await api(`/api/admin/api-keys/${encodeURIComponent(b.dataset.key)}/revoke`, { method: "POST" }); loadAdmin(); });
      $("#adm-new-key").onsubmit = async e => {
        e.preventDefault();
        try {
          const r = await api("/api/admin/api-keys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(e.target))) });
          $("#adm-key-out").innerHTML = `<div class="keybox">${esc(r.key)}</div><div class="faint" style="font-size:12px;margin-top:4px">Copy it now. Only its hash is stored. Send it as the <code>X-API-Key</code> header.</div>`;
          loadAdmin();
        } catch (err) { toast(err.message, true); }
      };
      $("#adm-reset") && ($("#adm-reset").onclick = () => startRun(true));
    }
  } catch (e) { toast(e.message, true); }
  $("#adm-new-user").onsubmit = async e => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    try { const r = await api("/api/access/users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); $("#adm-user-out").innerHTML = oneTime(r.username, r.one_time_password); e.target.reset(); loadAdmin(); }
    catch (err) { toast(err.message, true); }
  };
}

// ------------------------------------------------------------------ password change
function showPasswordChange(forced) {
  $("#pw-title").textContent = forced ? "Set your own password" : "Change your password";
  $("#pw-lead").textContent = forced ? "You signed in with a one-time password. Choose your own password (at least 10 characters) to continue." : "At least 10 characters. You will be signed out and asked to sign in again.";
  $("#pw-cancel").classList.toggle("hidden", forced);
  $("#pw-err").classList.add("hidden");
  $("#pw-form").reset();
  $("#pw-overlay").classList.remove("hidden");
}
$("#pw-cancel").onclick = () => $("#pw-overlay").classList.add("hidden");
$("#pw-form").onsubmit = async e => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  const err = m => { $("#pw-err").textContent = m; $("#pw-err").classList.remove("hidden"); };
  if (f.new !== f.confirm) return err("The two new passwords do not match");
  const r = await fetch("/api/me/password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current: f.current, new: f.new }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) return err(j.detail || "Could not change the password");
  $("#pw-overlay").classList.add("hidden");
  toast("Password changed. Sign in with your new password.");
  ME = null; showLogin();
};

// ------------------------------------------------------------------ profile
let PROFILE = null;
function openProfile() {
  $("#tab-profile").classList.remove("hidden");
  setTab("profile");
}
const when = iso => iso ? new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "–";
const since = iso => { if (!iso) return ""; const s = (Date.now() - Date.parse(iso)) / 1000; return s < 90 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`; };
const ACT_LABEL = { "change.proposed": "Proposed a change", "change.approved": "Approved a change", "change.rejected": "Rejected a change",
  "change.withdrawn": "Withdrew a request", "change.stale": "Request went stale", "review.decision": "Decided a conflict", "review.undo": "Undid a decision",
  "report.generated": "Downloaded a property report", "schema.confirmed": "Confirmed a column mapping", "access.user_created": "Created an account",
  "access.role_changed": "Changed a role", "access.password_reset": "Reset a password", "access.password_changed": "Changed password",
  "access.profile_updated": "Updated profile", "access.sessions_ended": "Signed out devices", "access.disabled": "Disabled an account",
  "access.enabled": "Enabled an account", "learning.rule_enabled": "Turned on a learned rule", "learning.rule_disabled": "Turned off a learned rule",
  "run.started": "Ran the pipeline", "admin.reset_demo": "Reset the demo" };
function renderProfileShell() {
  return `<div id="profile"><div class="skel big"></div><div class="skel"></div><div class="skel"></div></div>`;
}
async function loadProfile() {
  try { PROFILE = await api("/api/me/profile"); }
  catch (e) { $("#profile").innerHTML = `<p class="bad-text">${esc(e.message)}</p>`; return; }
  drawProfile();
}
function pwStrength(pw, username) {
  const checks = [
    [pw.length >= 10, "At least 10 characters"],
    [!!pw && !pw.toLowerCase().includes(username.toLowerCase()), "Not your username"],
    [[/[A-Za-z]/, /\d/, /[^A-Za-z0-9]/].filter(r => r.test(pw)).length >= 2, "Two of: letters, digits, symbols"],
    [new Set(pw.toLowerCase()).size >= 4, "Not a repeated or simple pattern"]];
  const score = checks.filter(c => c[0]).length + (pw.length >= 14 ? 1 : 0) + ([/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].every(r => r.test(pw)) ? 1 : 0);
  return { checks, score, label: !pw ? "" : score <= 2 ? "Weak" : score <= 4 ? "Fair" : "Strong" };
}
function drawProfile() {
  const P = PROFILE, initials = (P.full_name || P.username).split(/\s+/).map(w => w[0]).join("").slice(0, 2).toUpperCase();
  const f = (name, label, val, attrs = "", hint = "") => `<label class="field">${label}${hint ? ` <span class="faint">${hint}</span>` : ""}<input name="${name}" value="${esc(val)}" ${attrs}></label>`;
  const s = P.stats, by = s.requests_by_status || {};
  const pref = P.prefs || {};
  const sessions = P.sessions.map(x => `<div class="sess ${x.current ? "cur" : ""}"><div><b>${esc(x.device)}</b>${x.current ? ` <span class="prov real">this device</span>` : ""}
      <div class="faint">${esc(x.ip || "unknown address")} · signed in ${esc(when(x.created_at))} · active ${esc(since(x.last_seen_at))}</div></div>
      ${x.current ? "" : `<button class="btn small" data-endsess="${esc(x.id)}">Sign out</button>`}</div>`).join("");
  const acts = P.activity.map(a => `<div class="act"><span class="mono faint">${esc(when(a.ts))}</span><div><b>${esc(ACT_LABEL[a.kind] || a.kind)}</b>${a.subject ? ` · ${esc(a.subject)}` : ""}${a.area !== "access" ? ` <span class="faint">· ${esc(a.area)}</span>` : ""}${a.detail ? `<div class="faint">${esc(a.detail)}</div>` : ""}</div></div>`).join("");
  $("#profile").innerHTML = `
  <div class="prof-actions"><button class="btn small" id="prof-pw-go">Change password</button><button class="btn small danger" id="prof-logout">Sign out</button></div>
  <div class="prof-head"><div class="avatar big">${esc(initials)}</div><div>
    <div class="prof-name">${esc(P.full_name || P.username)}</div>
    <div class="faint">@${esc(P.username)}${P.designation ? ` · ${esc(P.designation)}` : ""}</div>
    <div style="margin-top:6px"><span class="role">${esc(P.role_label)}</span> <span class="role" style="background:var(--surface-3);color:var(--ink-2)">${esc(P.department_name)}</span>
      ${P.must_change ? `<span class="prov sim">one-time password</span>` : ""}</div></div></div>
  <div class="prof-facts">
    <div><span class="faint">Last sign-in</span>${esc(when(P.previous_login_at || P.last_login_at))}</div>
    <div><span class="faint">Password changed</span>${P.password_changed_at ? esc(since(P.password_changed_at)) : "never"}</div>
    <div><span class="faint">Member since</span>${esc(when(P.created_at).split(",")[0])}</div>
    <div><span class="faint">Signed in on</span>${P.sessions.length} device${P.sessions.length === 1 ? "" : "s"}</div></div>

  <div class="section-title">Personal details</div>
  <form class="block prof-form" id="prof-form" novalidate>
    <div class="grid2">
      ${f("full_name", "Full name", P.full_name, 'required minlength="2" maxlength="80" autocomplete="name"')}
      ${f("designation", "Designation", P.designation, 'maxlength="80" placeholder="e.g. Revenue Inspector"')}
      ${f("employee_id", "Employee ID", P.employee_id, 'maxlength="30" placeholder="e.g. KA-REV-10423"')}
      ${f("office", "Office", P.office, 'maxlength="80" placeholder="e.g. Taluk office, Bengaluru South"')}
      ${f("email", "Official e-mail", P.email, 'type="email" maxlength="120" autocomplete="email" placeholder="name@karnataka.gov.in"')}
      ${f("phone", "Mobile", P.phone, 'type="tel" maxlength="16" autocomplete="tel" placeholder="98XXXXXXXX"', "(10 digits)")}
      <label class="field">Preferred language<select name="language">${Object.entries(P.languages).map(([k, l]) => `<option value="${k}" ${P.language === k ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
      <label class="field">Username<input value="${esc(P.username)}" disabled title="Usernames never change: they are what the audit trail records"></label>
    </div>
    <p class="bad-text hidden" id="prof-err" role="alert"></p>
    <div class="prof-foot"><span class="faint" id="prof-saved"></span><button class="btn primary small" type="submit" id="prof-save" disabled>Save details</button></div>
  </form>

  <div class="section-title">Password</div>
  <form class="block prof-form" id="pw2-form" autocomplete="off">
    <p class="faint" style="margin:0 0 8px;font-size:12.5px">${P.password_changed_at ? `Last changed ${esc(when(P.password_changed_at))}.` : "You have not changed your password yet."} Changing it signs you out on every device.</p>
    <div class="grid2">
      <label class="field">Current password<span class="pw-wrap"><input name="current" type="password" autocomplete="current-password" required><button type="button" class="pw-eye" data-eye="current" aria-label="Show password">show</button></span></label>
      <span></span>
      <label class="field">New password<span class="pw-wrap"><input name="new" type="password" autocomplete="new-password" required minlength="10"><button type="button" class="pw-eye" data-eye="new" aria-label="Show password">show</button></span></label>
      <label class="field">Repeat new password<input name="confirm" type="password" autocomplete="new-password" required minlength="10"></label>
    </div>
    <div class="pw-meter"><div id="pw-bar"></div></div><div class="faint" id="pw-label" style="font-size:12px;min-height:16px"></div>
    <ul class="pw-rules" id="pw-rules"></ul>
    <p class="bad-text hidden" id="pw2-err" role="alert"></p>
    <div class="prof-foot"><span></span><button class="btn primary small" type="submit" id="pw2-save" disabled>Change password</button></div>
  </form>

  <div class="section-title">Where you are signed in</div>
  <div class="block">${sessions || `<div class="faint">No active sessions.</div>`}
    ${P.sessions.length > 1 ? `<div class="prof-foot"><span class="faint">Don't recognise a device? Sign it out and change your password.</span><button class="btn small danger" id="end-others">Sign out all other devices</button></div>` : ""}</div>

  <div class="section-title">Access</div>
  <div class="block prof-access">
    <div><b>${esc(P.title || P.role_label)}</b> <span class="faint">(${esc(P.role_label)})</span> · ${esc(P.department_name)}${P.role_granted_by ? ` <span class="faint">· granted by ${esc(P.role_granted_by)} ${esc(when(P.role_granted_at).split(",")[0])}</span>` : ""}</div>
    <ul>${P.can_do.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
    ${P.dept_access ? `<div class="dsub">Other departments' records you can see</div><div class="dfields-own">${P.dept_access.map(a => `<span class="dbadge ${a.department === P.department ? "mine" : a.level}" title="${esc(ACCESS_LABEL[a.level])}">${esc(a.name)}: ${a.department === P.department ? "own" : esc(a.level)}</span>`).join("")}</div>`
      : `<div class="faint">You see every department's records in full.</div>`}
    ${P.can_grant.roles.length ? `<div class="faint">You can grant: ${P.can_grant.roles.map(esc).join(", ")}${Array.isArray(P.can_grant.departments) ? ` (${P.can_grant.departments.map(d => esc(deptName(d))).join(", ")})` : " (any department)"}.</div>` : ""}
    <div class="faint">Your role and department can be changed only by the ${esc(P.role_changed_by)}. You cannot change your own access.</div></div>

  <div class="section-title">Preferences</div>
  <form class="block prof-form" id="pref-form">
    <div class="grid2">
      <label class="field">Open this area at sign-in<select name="default_ws"><option value="">Last area used</option>${WORKSPACES.map(w => `<option value="${esc(w.id)}" ${pref.default_ws === w.id ? "selected" : ""}>${esc(w.title)}</option>`).join("")}</select></label>
      <label class="field">Basemap<select name="basemap"><option value="">Automatic</option>${[["satellite", "Satellite"], ["streets", "Map"], ["drone", "Drone (where available)"], ["none", "None"]].map(([k, l]) => `<option value="${k}" ${pref.basemap === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="field">Start on<select name="start_tab">${[["", "Overview"], ["review", "Review"], ["requests", "Requests"], ["data", "Data"], ["changes", "Changes"], ["accuracy", "Accuracy"], ["audit", "Audit"]].map(([k, l]) => `<option value="${k}" ${(pref.start_tab || "") === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    </div>
    <div class="prof-foot"><span class="faint" id="pref-saved">Used every time you sign in, on any device.</span><button class="btn small" type="submit">Save preferences</button></div>
  </form>

  <div class="section-title">Your work</div>
  <div class="stats prof-stats">
    <div class="stat"><div class="v">${s.requests_proposed}</div><div class="l">Changes proposed</div><div class="s">${by.approved || 0} approved · ${by.pending || 0} waiting · ${by.rejected || 0} rejected</div></div>
    <div class="stat"><div class="v">${s.requests_decided}</div><div class="l">Requests you decided</div></div>
    <div class="stat"><div class="v">${s.conflicts_decided}</div><div class="l">Conflicts you decided</div></div></div>
  <div class="section-title">Recent activity</div>
  <div class="block">${acts || `<div class="faint">Nothing recorded yet.</div>`}
    <div class="faint" style="font-size:11.5px;margin-top:6px">From the tamper-evident audit trail of every area and the access log.</div></div>`;
  bindProfile();
}
function bindProfile() {
  $("#prof-logout").onclick = async () => { await fetch("/api/logout", { method: "POST" }); location.reload(); };
  $("#prof-pw-go").onclick = () => { $("#pw2-form").scrollIntoView({ behavior: "smooth", block: "center" }); setTimeout(() => $("#pw2-form input[name=current]").focus(), 350); };
  const pf = $("#prof-form"), start = JSON.stringify(Object.fromEntries(new FormData(pf)));
  pf.oninput = () => { $("#prof-save").disabled = JSON.stringify(Object.fromEntries(new FormData(pf))) === start; $("#prof-saved").textContent = ""; };
  pf.onsubmit = async e => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(pf));
    const err = m => { $("#prof-err").textContent = m; $("#prof-err").classList.remove("hidden"); };
    $("#prof-err").classList.add("hidden");
    if ((body.full_name || "").trim().length < 2) return err("Enter your full name");
    if (body.email && !/^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(body.email)) return err("That e-mail address does not look right");
    if (body.phone && !/^(\+91[\s-]?)?[6-9]\d{9}$/.test(body.phone.replace(/[\s-]/g, ""))) return err("Mobile number: 10 digits starting 6-9, optionally with +91");
    try {
      const r = await api("/api/me/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      PROFILE = r.profile; ME.full_name = PROFILE.full_name; renderUser(); drawProfile();
      $("#prof-saved").textContent = r.changed.length ? "Saved." : "Nothing changed.";
    } catch (x) { err(x.message); }
  };
  const pw = $("#pw2-form");
  const refresh = () => {
    const st = pwStrength(pw.new.value, PROFILE.username);
    $("#pw-rules").innerHTML = st.checks.map(([ok, t]) => `<li class="${pw.new.value ? (ok ? "ok" : "bad") : ""}">${esc(t)}</li>`).join("") +
      `<li class="${pw.confirm.value ? (pw.confirm.value === pw.new.value ? "ok" : "bad") : ""}">Both new passwords match</li>`;
    $("#pw-bar").style.width = `${Math.min(100, st.score / 6 * 100)}%`;
    $("#pw-bar").className = st.label.toLowerCase();
    $("#pw-label").textContent = st.label ? `Strength: ${st.label}` : "";
    $("#pw2-save").disabled = !(pw.current.value && st.checks.every(c => c[0]) && pw.confirm.value === pw.new.value);
  };
  pw.oninput = refresh; refresh();
  $$("[data-eye]").forEach(b => b.onclick = () => { const i = pw[b.dataset.eye]; const show = i.type === "password"; i.type = show ? "text" : "password"; b.textContent = show ? "hide" : "show"; });
  pw.onsubmit = async e => {
    e.preventDefault();
    const err = m => { $("#pw2-err").textContent = m; $("#pw2-err").classList.remove("hidden"); };
    $("#pw2-err").classList.add("hidden");
    if (pw.new.value === pw.current.value) return err("Choose a password different from your current one");
    const r = await fetch("/api/me/password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current: pw.current.value, new: pw.new.value }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return err(j.detail || "Could not change the password");
    toast("Password changed. Sign in with your new password.");
    ME = null; showLogin();
  };
  $$("[data-endsess]").forEach(b => b.onclick = async () => {
    try { await api(`/api/me/sessions/${b.dataset.endsess}/end`, { method: "POST" }); toast("That device is signed out"); loadProfile(); }
    catch (x) { toast(x.message, true); }
  });
  $("#end-others") && ($("#end-others").onclick = async () => {
    if (!confirm("Sign out every other device? This one stays signed in.")) return;
    try { const r = await api("/api/me/sessions/end-others", { method: "POST" }); toast(`${r.ended} device${r.ended === 1 ? "" : "s"} signed out`); loadProfile(); }
    catch (x) { toast(x.message, true); }
  });
  $("#pref-form").onsubmit = async e => {
    e.preventDefault();
    const prefs = Object.fromEntries(new FormData(e.target));
    try {
      const r = await api("/api/me/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prefs }) });
      PROFILE = r.profile; ME.prefs = PROFILE.prefs;
      $("#pref-saved").textContent = "Saved. They apply from your next sign-in.";
    } catch (x) { toast(x.message, true); }
  };
}

// ------------------------------------------------------------------ sign-in
function showLogin() {
  $("#login-overlay").classList.remove("hidden");
  setTimeout(() => $("#login-user").focus(), 50);
}
$("#login-form").onsubmit = async e => {
  e.preventDefault();
  $("#login-err").classList.add("hidden");
  const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: $("#login-user").value, password: $("#login-pass").value }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { $("#login-err").textContent = j.detail || "Sign-in failed"; $("#login-err").classList.remove("hidden"); return; }
  $("#login-pass").value = "";
  $("#login-overlay").classList.add("hidden");
  ME = j.user;
  if (ME.must_change) { showPasswordChange(true); return; }
  if (!WORKSPACES.length) start(); else { renderUser(); switchWorkspace(WS); }
};
function renderUser() {
  const initials = (ME.full_name || ME.username).split(/\s+/).map(w => w[0]).join("").slice(0, 2).toUpperCase();
  $("#user-chip").innerHTML = `<button class="me-btn" id="btn-me" title="Your profile: details, password, devices, activity"><div class="who"><div>${esc(ME.full_name || ME.username)}</div><div class="role-tag">${esc(ME.role)}</div></div><div class="avatar">${esc(initials)}</div></button>`;
  $(".role-tag").textContent = ME.role + (ME.department && ME.department !== "all" ? " · " + (R ? deptName(ME.department) : ME.department) : "");
  $("#btn-me").onclick = e => { e.stopPropagation(); toggleAcct(); };
  $("#btn-run").classList.toggle("hidden", !can("admin"));
  $("#btn-upload").classList.toggle("hidden", !can("admin"));
  const manager = can("admin") || (ME.role === "reviewer" && ME.department !== "all");
  $("#tab-admin").classList.toggle("hidden", !manager);
  $("#tab-admin").textContent = can("admin") ? "Admin" : "Access";
}

// ------------------------------------------------------------------ workspaces & boot
async function loadResult(fit) {
  endMapEdit();
  R = await api("/api/result");
  DREG = await api("/api/departments").catch(() => null);
  buildLayers();
  loadProposals();
  $("#btn-newplot").classList.toggle("hidden", !canMap());
  activeChip = null; renderChips();
  if (!STATEWIDE.length) loadStatewide();
  if (fit && layers.parcels) map.fitBounds(layers.parcels.getBounds(), { padding: [20, 20] });
  closeInspector(); selectedConflict = null; queryState = null; queryLayer.clearLayers();
  renderUser(); loadRequestCount();
  $("#tab-results").classList.add("hidden");
  if (tab === "results") tab = "overview";
  $$("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  render();
}
async function switchWorkspace(ws) {
  WS = ws;
  try { localStorage.setItem("lc.ws", ws); } catch { }
  $("#ws").value = ws;
  $("#tab-body").innerHTML = `<div class="skel big"></div><div class="skel"></div><div class="skel" style="width:70%"></div>`;
  const s = await api("/api/status");
  if (s.has_result) await loadResult(true);
  else { R = null; $("#tab-body").innerHTML = `<div class="empty"><h3>Not integrated yet</h3>This area's files are loaded but the pipeline has not run.<br><br><button class="btn primary" id="first-run">Run the pipeline</button></div>`; $("#first-run").onclick = () => startRun(false); }
  if (s.state === "running") { $("#run-overlay").classList.remove("hidden"); pollRun(); }
}
$("#ws").onchange = e => { basemap = null; switchWorkspace(e.target.value); };

// deep links: #ws=jayanagar&tab=review&conflict=<id>&plot=<id>&q=<question>&basemap=drone&epoch=t1&layers=a,-b
function applyHash() {
  if (!R) return;
  const h = new URLSearchParams(location.hash.slice(1));
  if (h.get("basemap")) { basemap = h.get("basemap"); if (h.get("epoch")) epoch = h.get("epoch"); applyBasemap(); }
  if (h.get("layers")) { h.get("layers").split(",").forEach(k => { vis[k.replace(/^-/, "")] = !k.startsWith("-"); }); applyVisibility(); }
  if (h.get("tab")) setTab(h.get("tab"));
  if (h.get("q")) { $("#ask-input").value = h.get("q"); $("#ask").requestSubmit(); }
  else if (h.get("conflict")) openConflict(h.get("conflict"));
  else if (h.get("plot")) inspect(h.get("plot"), true);
  const at = (h.get("at") || "").split(",").map(Number);
  if (at.length === 3 && at.every(Number.isFinite) && !h.get("plot") && !h.get("conflict")) map.setView([at[0], at[1]], at[2]);
}

// ------------------------------------------------------------------ map-first shell (search pill, side sheet, chips, controls)
const narrow = () => innerWidth <= 760;
function syncSide() {
  const b = document.body, insp = !$("#inspector").classList.contains("hidden");
  const on = !b.classList.contains("collapsed") && (b.classList.contains("panel-on") || insp);
  if (on === b.classList.contains("side-on")) return;
  b.classList.toggle("side-on", on);
  $("#btn-collapse").title = on ? "Collapse side panel ([)" : "Expand side panel ([)";
  setTimeout(() => map.invalidateSize({ pan: false }), 260);
}
new MutationObserver(() => {
  if (!$("#inspector").classList.contains("hidden")) document.body.classList.remove("collapsed");
  syncSide();
}).observe($("#inspector"), { attributes: true, attributeFilter: ["class"] });
function toggleSide(force) {
  const b = document.body, on = force ?? !b.classList.contains("side-on");
  if (on) { b.classList.remove("collapsed"); if ($("#inspector").classList.contains("hidden")) b.classList.add("panel-on"); }
  else b.classList.add("collapsed");
  syncSide();
}
$("#btn-collapse").onclick = () => toggleSide();
new ResizeObserver(([e]) => document.documentElement.style.setProperty("--tr-w", Math.ceil(e.contentRect.width) + "px")).observe($(".gm-topright"));
if (!narrow()) document.body.classList.add("panel-on");
syncSide();

// --- tile thumbnails for the map types
function tileXY(z) {
  const c = map.getCenter(), n = 2 ** z, r = c.lat * Math.PI / 180;
  return { z, x: Math.floor((c.lng + 180) / 360 * n), y: Math.floor((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * n) };
}
function thumbUrl(k) {
  const { z, x, y } = tileXY(15);
  if (k === "satellite") return `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`;
  if (k === "streets") return `https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/${z}/${y}/${x}`;
  if (k === "google" && CFG.google_tiles) return CFG.google_tiles.replace("{s}", "mt0").replace("{z}", z).replace("{x}", x).replace("{y}", y);
  if (k === "drone" && R?.rasters?.ortho_t2) return R.rasters.ortho_t2.url;
  return "";
}
const thumbCss = k => { const u = thumbUrl(k); return u ? `background-image:url('${u}')` : "background:repeating-linear-gradient(45deg,#eef1f4 0 6px,#e3e8ec 6px 12px)"; };
function updateLayersThumb() {
  const alt = ["satellite", "google", "drone"].includes(basemap) ? "streets" : "satellite";
  $("#layers-thumb").style.cssText = thumbCss(alt);
}
function toggleLayers(force) {
  const el = $("#layer-ctl"), open = force ?? el.classList.contains("hidden");
  el.classList.toggle("hidden", !open);
  if (open) { closeAcct(); renderBasemaps(); }
}
$("#btn-layers").onclick = e => { e.stopPropagation(); toggleLayers(); };
$("#layers-close").onclick = () => toggleLayers(false);

// --- quick filters (each chip is a saved question for the search engine)
const CHIPS = [
  ["conflicts", "Conflicts", "plots with open conflicts"],
  ["review", "Needs review", "plots needing review"],
  ["tax", "Tax at risk", "tax at risk"],
  ["mutation", "Sale not mutated", "sale not mutated"],
  ["encroach", "Encroachments", "plots with encroachments"],
  ["newbuild", "New construction", "new construction"],
  ["vacant", "Vacant", "vacant plots"],
  ["surveys", "Surveys disagree", "surveys disagree", () => !!R?.cadastral_comparison],
];
let activeChip = null;
function renderChips() {
  $("#chips").innerHTML = CHIPS.filter(c => !c[3] || c[3]()).map(([k, l]) =>
    `<button class="gm-chip ${activeChip === k ? "on" : ""}" data-chip="${k}">${esc(l)}</button>`).join("");
  $$("#chips [data-chip]").forEach(b => b.onclick = () => {
    const c = CHIPS.find(x => x[0] === b.dataset.chip);
    if (activeChip === c[0]) { clearChip(); return; }
    activeChip = c[0]; renderChips();
    $("#ask-input").value = c[2]; runQuestion(c[2]);
  });
}
function clearChip() {
  activeChip = null; renderChips();
  queryState = null; queryLayer.clearLayers(); $("#ask-input").value = "";
  const h = new URLSearchParams(location.hash.slice(1)); h.delete("q");
  try { history.replaceState(null, "", "#" + h.toString().replace(/%2C/g, ",")); } catch { }
  $("#tab-results").classList.add("hidden");
  if (tab === "results") setTab("overview");
}
$("#ask-input").addEventListener("input", () => { if (activeChip) { activeChip = null; renderChips(); } });

// --- menu drawer
function openDrawer(open = true) {
  $("#drawer").classList.toggle("open", open); $("#drawer").setAttribute("aria-hidden", String(!open));
  $("#drawer-bg").classList.toggle("hidden", !open);
  if (!open) return;
  closeAcct(); toggleLayers(false);
  $("#drawer-areas").innerHTML = `<div class="cap">Areas</div>` + WORKSPACES.map(w =>
    `<button class="drawer-item ${w.id === WS ? "on" : ""}" data-dws="${esc(w.id)}">${icon('<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>')}${esc(w.title)}</button>`).join("");
  $("#drawer-tabs").innerHTML = `<div class="cap">Go to</div>` + $$("#tabs button").filter(b => !b.classList.contains("hidden")).map(b =>
    `<button class="drawer-item ${b.dataset.tab === tab ? "on" : ""}" data-dtab="${b.dataset.tab}">${b.innerHTML}</button>`).join("");
  $$("#drawer [data-dws]").forEach(b => b.onclick = () => { openDrawer(false); basemap = null; switchWorkspace(b.dataset.dws); });
  $$("#drawer [data-dtab]").forEach(b => b.onclick = () => { openDrawer(false); setTab(b.dataset.dtab); });
}
$("#btn-menu").onclick = () => openDrawer(true);
$("#drawer-bg").onclick = () => openDrawer(false);
$$("#drawer #btn-upload, #drawer #btn-run").forEach(b => b.addEventListener("click", () => openDrawer(false)));

// --- help
const showHelp = (open = true) => $("#help-overlay").classList.toggle("hidden", !open);
$("#btn-help").onclick = () => showHelp();
$("#drawer-help").onclick = () => { openDrawer(false); showHelp(); };
$("#help-close").onclick = () => showHelp(false);
$("#help-overlay").onclick = e => { if (e.target.id === "help-overlay") showHelp(false); };

// --- account menu
function closeAcct() { $("#acct").classList.add("hidden"); }
function toggleAcct() {
  const el = $("#acct");
  if (!el.classList.contains("hidden")) return closeAcct();
  toggleLayers(false);
  const initials = (ME.full_name || ME.username).split(/\s+/).map(w => w[0]).join("").slice(0, 2).toUpperCase();
  const dept = ME.department && ME.department !== "all" ? " · " + (R ? deptName(ME.department) : ME.department) : "";
  el.innerHTML = `<div class="avatar">${esc(initials)}</div><div class="nm">Hi, ${esc((ME.full_name || ME.username).split(" ")[0])}!</div>
    <div class="sub">${esc(ME.title || ME.role)}${esc(dept)}<br>${esc(ME.username)}</div>
    <button class="btn" id="acct-profile">Manage your profile</button>
    <button class="btn" id="acct-pw">Change password</button>
    <button class="btn danger" id="acct-out">Sign out</button>`;
  el.classList.remove("hidden");
  $("#acct-profile").onclick = () => { closeAcct(); openProfile(); };
  $("#acct-pw").onclick = () => {
    closeAcct(); openProfile();
    let tries = 0;
    const go = () => { const b = $("#prof-pw-go"); if (b) b.click(); else if (tries++ < 30) setTimeout(go, 100); };
    go();
  };
  $("#acct-out").onclick = async () => { await fetch("/api/logout", { method: "POST" }); location.reload(); };
}
document.addEventListener("click", e => {
  if (!e.target.isConnected) return;               // the clicked button was just re-rendered
  if (!e.target.closest("#acct, #btn-me")) closeAcct();
  if (!e.target.closest("#layer-ctl, #btn-layers")) toggleLayers(false);
  if (!e.target.closest("#ctx")) hideCtx();
});

// --- bottom-right controls
let meMarker = null;
$("#btn-locate").onclick = () => {
  if (!navigator.geolocation) return toast("This browser cannot share its location", true);
  navigator.geolocation.getCurrentPosition(p => {
    const ll = [p.coords.latitude, p.coords.longitude];
    if (meMarker) map.removeLayer(meMarker);
    meMarker = L.marker(ll, { icon: L.divIcon({ className: "", html: '<div class="me-dot"></div>', iconSize: [18, 18], iconAnchor: [9, 9] }), interactive: false }).addTo(map);
    map.flyTo(ll, 17, { duration: 0.8 });
    statewideFor(ll);
  }, err => toast(err.code === 1 ? "Location permission was refused" : "Could not find your location", true), { enableHighAccuracy: true, timeout: 10000 });
};
$("#btn-fit").onclick = () => { if (layers.parcels) map.flyToBounds(layers.parcels.getBounds(), { padding: [20, 20], duration: 0.6 }); };
const toggleFull = () => document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen?.().catch(() => { });
$("#btn-full").onclick = toggleFull;

// --- measure distance and area
const MEAS = { on: false, pts: [], g: L.layerGroup() };
function measStart(from) {
  if (ED.on) return;
  MEAS.on = true; MEAS.pts = from ? [from] : [];
  MEAS.g.clearLayers().addTo(map);
  map.doubleClickZoom.disable();
  map.getContainer().style.cursor = "crosshair";
  $("#btn-measure").classList.add("on");
  measDraw();
}
function measStop() {
  if (!MEAS.on) return;
  MEAS.on = false; MEAS.pts = []; MEAS.g.clearLayers(); map.removeLayer(MEAS.g);
  map.doubleClickZoom.enable(); map.getContainer().style.cursor = "";
  $("#btn-measure").classList.remove("on");
  $("#measure-card").classList.add("hidden");
}
function measArea(pts) {                               // m², local equirectangular projection (fine at plot and ward scale)
  const lat0 = pts.reduce((a, p) => a + p.lat, 0) / pts.length * Math.PI / 180, R0 = 6371008.8;
  const xy = pts.map(p => [p.lng * Math.PI / 180 * R0 * Math.cos(lat0), p.lat * Math.PI / 180 * R0]);
  let a = 0;
  for (let i = 0; i < xy.length; i++) { const [x1, y1] = xy[i], [x2, y2] = xy[(i + 1) % xy.length]; a += x1 * y2 - x2 * y1; }
  return Math.abs(a) / 2;
}
const fmtDist = m => m >= 1000 ? `${num(m / 1000, 2)} km` : `${num(m, 1)} m`;
const fmtArea = a => a >= 1e6 ? `${num(a / 1e6, 3)} km²` : `${num(a, 1)} m² <span class="faint">(${num(a * 10.7639, 0)} sq ft · ${num(a / 4046.86, 3)} acre)</span>`;
function measDraw() {
  MEAS.g.clearLayers();
  const p = MEAS.pts;
  let d = 0;
  p.forEach((q, i) => {
    if (i) d += map.distance(p[i - 1], q);
    L.circleMarker(q, { radius: 4, color: "#fff", weight: 2, fillColor: "#1a73e8", fillOpacity: 1 }).addTo(MEAS.g)
      .bindTooltip(i ? fmtDist(d) : "Start", { permanent: true, direction: "right", className: "measure-lbl", offset: [6, 0] });
  });
  if (p.length > 1) L.polyline(p, { color: "#1a73e8", weight: 3 }).addTo(MEAS.g);
  const area = p.length >= 3 ? measArea(p) : 0;
  if (area) L.polygon(p, { color: "#1a73e8", weight: 0, fillOpacity: 0.12, interactive: false }).addTo(MEAS.g);
  $("#measure-card").innerHTML = `<div>${p.length < 2 ? "Click the map to add points" : `Distance <b>${fmtDist(d)}</b>`}${area ? `<br>Area <b>${fmtArea(area)}</b>` : ""}</div>
    <button class="btn small" id="meas-undo" ${p.length ? "" : "disabled"}>Undo</button><button class="btn small primary" id="meas-done">Done</button>`;
  $("#measure-card").classList.remove("hidden");
  $("#meas-undo").onclick = e => { e.stopPropagation(); MEAS.pts.pop(); measDraw(); };
  $("#meas-done").onclick = e => { e.stopPropagation(); measStop(); };
}
map.on("click", e => { if (MEAS.on) { MEAS.pts.push(e.latlng); measDraw(); } });
$("#btn-measure").onclick = () => MEAS.on ? measStop() : measStart();

// --- right-click: what's here
function ringHas(ring, x, y) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function plotAt(ll) {
  for (const f of R?.layers.parcels.features || []) {
    const g = f.geometry, polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
    if (polys.some(pg => ringHas(pg[0], ll.lng, ll.lat) && !pg.slice(1).some(h => ringHas(h, ll.lng, ll.lat)))) return f.properties.plot_id;
  }
  return null;
}
function hideCtx() { $("#ctx").classList.add("hidden"); }
async function copyText(t, what) { try { await navigator.clipboard.writeText(t); toast(`${what} copied`); } catch { toast(t); } }
map.on("contextmenu", e => {
  if (ED.on) return;
  const ll = e.latlng, pid = plotAt(ll), el = $("#ctx");
  const coords = `${ll.lat.toFixed(6)}, ${ll.lng.toFixed(6)}`;
  el.innerHTML = `<button data-c="copy" class="coords" title="Copy">${coords}</button><hr>
    <button data-c="here">${pid ? `What's here? <span class="faint">plot ${esc(pid)}</span>` : "What's here?"}</button>
    <button data-c="measure">Measure distance from here</button>
    <button data-c="area">Select an area (department summary)</button>
    ${canMap() ? `<button data-c="add">Add a new plot here</button>` : ""}
    <button data-c="centre">Centre the map here</button>
    <button data-c="share">Copy a link to this place</button>`;
  const wrap = $(".mapwrap").getBoundingClientRect(), ev = e.originalEvent;
  el.classList.remove("hidden");
  el.style.left = Math.min(ev.clientX - wrap.left, wrap.width - el.offsetWidth - 8) + "px";
  el.style.top = Math.min(ev.clientY - wrap.top, wrap.height - el.offsetHeight - 8) + "px";
  $$("#ctx [data-c]").forEach(b => b.onclick = ev2 => {
    ev2.stopPropagation(); hideCtx();
    const c = b.dataset.c;
    if (c === "copy") copyText(coords, "Coordinates");
    else if (c === "here") {
      if (pid) inspect(pid);
      else { const sw = statewideFor([ll.lat, ll.lng]); dropPin(ll, `<b>${coords}</b><div class="faint">No plot of this area here${sw ? ` · showing ${esc(sw.label)}` : ""}</div>`); }
    } else if (c === "measure") measStart(ll);
    else if (c === "area") areaStart();
    else if (c === "add") { map.panTo(ll); startEditor(null, "parcel_add"); }
    else if (c === "centre") map.panTo(ll);
    else if (c === "share") copyText(`${location.origin}/#ws=${encodeURIComponent(WS)}&at=${ll.lat.toFixed(6)},${ll.lng.toFixed(6)},19`, "Link");
  });
});
map.on("movestart", hideCtx);
$("#btn-area").onclick = () => AREA.on ? areaStop() : areaStart();

// --- phones: tapping empty map lowers the sheet
map.on("click", () => {
  if (!narrow() || ED.on || MEAS.on || Date.now() - lastParcelClick < 400) return;
  document.body.classList.remove("panel-on"); closeInspector(); syncSide();
  setTimeout(() => map.invalidateSize({ pan: false }), 300);
});

// --- the view is kept in the address, so a reload or a shared link opens the same place
let hashTimer = 0;
map.on("moveend", () => {
  clearTimeout(hashTimer);
  hashTimer = setTimeout(() => {
    if (!R || ED.on) return;
    const h = new URLSearchParams(location.hash.slice(1)), c = map.getCenter();
    h.set("ws", WS); h.set("at", `${c.lat.toFixed(6)},${c.lng.toFixed(6)},${+map.getZoom().toFixed(2)}`);
    try { history.replaceState(null, "", "#" + h.toString().replace(/%2C/g, ",")); } catch { }
    if (!$("#layer-ctl").classList.contains("hidden")) renderBasemaps(); else updateLayersThumb();
  }, 400);
});

// --- keys
document.addEventListener("keydown", e => {
  if (ED.on || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest("input, textarea, select, [contenteditable]")) return;
  const k = e.key;
  if (k === "Escape") {
    if (!$("#ctx").classList.contains("hidden")) return hideCtx();
    if (!$("#help-overlay").classList.contains("hidden")) return showHelp(false);
    if ($("#drawer").classList.contains("open")) return openDrawer(false);
    if (!$("#acct").classList.contains("hidden")) return closeAcct();
    if (!$("#layer-ctl").classList.contains("hidden")) return toggleLayers(false);
    if (MEAS.on) return measStop();
    if (AREA.on) return areaStop();
    if ($(".overlay:not(.hidden)")) return;
    if (!$("#inspector").classList.contains("hidden")) return closeInspector();
    return;
  }
  if ($(".overlay:not(.hidden)")) return;
  if (k === "[") { e.preventDefault(); toggleSide(); }
  else if (k === "l" || k === "L") toggleLayers();
  else if (k === "m" || k === "M") MEAS.on ? measStop() : measStart();
  else if (k === "a" || k === "A") AREA.on ? areaStop() : areaStart();
  else if (k === "f" || k === "F") toggleFull();
  else if (k === "?") showHelp();
});


function updateOnline() { $("#offline").classList.toggle("hidden", navigator.onLine); }
window.addEventListener("online", updateOnline); window.addEventListener("offline", updateOnline);

async function start() {
  const meRes = await fetch("/api/me");
  if (meRes.status === 401) { showLogin(); return; }
  ME = await meRes.json();
  if (ME.must_change) { showPasswordChange(true); return; }
  renderUser();
  const [w, cfg] = await Promise.all([fetch("/api/workspaces").then(r => r.json()), fetch("/api/config").then(r => r.json()).catch(() => ({}))]);
  WORKSPACES = w.workspaces; CFG = cfg;
  $("#engine").textContent = CFG.query_engine === "claude" ? "Claude" : "rules";
  $("#ask-input").placeholder = "Search plots, records, owners, places, or ask a question   ( / )";
  $("#engine").title = CFG.query_engine === "claude" ? "Questions are interpreted by Claude; the answers come only from this server's records."
    : "Questions are answered by the built-in rule parser on this server. No AI service is used and no data leaves the server.";
  $("#ws").innerHTML = WORKSPACES.map(x => `<option value="${esc(x.id)}">${esc(x.title)}</option>`).join("");
  const h = new URLSearchParams(location.hash.slice(1));
  let saved = null; try { saved = localStorage.getItem("lc.ws"); } catch { }
  const pref = ME.prefs || {};
  if (pref.basemap) basemap = pref.basemap;
  if (pref.start_tab) { tab = pref.start_tab; $$("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab)); }
  const first = [h.get("ws"), pref.default_ws, saved, w.default].find(x => x && WORKSPACES.some(y => y.id === x));
  await switchWorkspace(first);
  applyHash();
}
updateOnline();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => { });
start();
