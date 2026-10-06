# Land Connect: Project Source of Truth (SIH 2026, PS 26013)

> **How to use this document.** It describes what the MVP **actually** does. Every claim was checked
> against the code (`landconnect/*.py`, `web/*`), the database schema, the API routes, the published
> results (`output/<area>/result.json`) and the stored benchmark files (`output/benchmarks/*.json`).
> Status tags:
> - **[IMPLEMENTED]**: working in the MVP.
> - **[PARTIAL]**: part of it works (the tag says which part).
> - **[PLANNED]**: future scope only.
> - **[VERIFY]**: not established by project evidence.
>
> Do not upgrade any tag when making slides.
>
> **Data honesty in one line:**
> - **Real:** OpenStreetMap buildings, roads and landmarks; Microsoft AI building footprints; Tamil Nadu cadastral parcels (TNGIS and NCSCM, via datameet).
> - **Simulated:** land records, department records, GNSS points, field visits and elevation models (DSM/DTM).
> - **Rendered:** drone imagery exists only for synthetic wards.

---

## 1. PROJECT IDENTITY

| Item | Content |
|---|---|
| **Name** | **Land Connect** |
| **PS** | SIH 2026 PS **26013**: "Automated Integration and Intelligent Harmonization of Multi-source Geospatial Data for Urban Land Record Management". Ministry of Rural Development, Department of Land Resources (DoLR). Software. Smart Automation. *(The code and README label it "PS 013"; same problem statement.)* |
| **One-line** | A pipeline and web GIS that takes the land and property files different departments already keep, in any format and coordinate system, turns them into one corrected parcel record with a confidence score, and sends only the real disagreements to human reviewers. |

**Executive summary.** Each Indian city holds several land and property records that disagree. Revenue (RoR/RTC/patta), Survey (old cadastral sheets), the municipal property-tax GIS, utilities, town planning and registration each have their own version.

Land Connect ingests these files as delivered and runs an automated 11-step pipeline:
1. Reads any format.
2. Maps each department's columns to one schema.
3. Detects and fixes coordinate systems, including GNSS-based georeferencing of unreferenced sheets.
4. Extracts buildings.
5. Repairs parcel topology.
6. Links records with an ML matcher.
7. Detects changes.
8. Raises 18 kinds of conflicts.
9. Auto-resolves conflicts only where a more reliable source settles them.
10. Scores each parcel's confidence.
11. Publishes maps, APIs, exports and a hash-chained audit trail.

The web app then gives officers, approvers and admins the workflows around the result: a review queue, maker–checker record updates, an on-map editor, department-wise views with need-to-know access control, search and plain-language questions, PDF reports, and exports.

**Problem.** Records about the same land sit in separate systems and do not match:
- different IDs ("Sy.No. 102/1" vs "102-1" vs "KH-102/1");
- different units (guntas, sq yd, sq ft, hectares);
- different coordinate systems (local grids, Kalianpur 1975, WGS 84, Web Mercator);
- positional offsets of 1–6 m;
- stale owners;
- missing or merged properties.

Reconciling them is done by hand in desktop GIS, parcel by parcel.

**Objective.** Automate that reconciliation so most parcels need no manual GIS work, show every disagreement with its evidence, keep humans in charge of disputed cases, and make every change traceable.

**Stakeholders.**
- **Directly supported by roles in the app:** Revenue, Survey & Land Records, the municipal body (property tax), Town Planning, Registration (IGR), water, electricity, gas and Roads. Four roles: viewer, officer, approver and state admin.
- **Other potential consumers (not integrated):** DoLR/NAKSHA programme teams, and citizens via RTI or court requests.

**Real-world use case (as demonstrated).** Jayanagar, Bengaluru (real building geometry) and Thiruvanmiyur, Chennai (real government cadastral parcels):
- The system finds that the old cadastral sheet is about 4–6 m off the ground and corrects it with GNSS points.
- It links about 99% of department records to parcels.
- It flags, for example:
  - sales not mutated in the RoR;
  - built plots missing from the tax register (with estimated annual tax at risk);
  - floors beyond the building permission;
  - buildings crossing plot lines.

---

## 2. PS 26013 REQUIREMENT MAPPING

| PS requirement | Our implementation | Status |
|---|---|---|
| Integrate drone imagery / ORI | **Synthetic areas:** rendered orthophotos (2 dates) processed by the building extractor. **Any area:** an uploaded RGB GeoTIFF orthomosaic is published as the "Drone" map type (display only; not fed to extraction). No real drone data was available. | [PARTIAL] |
| DSM / DTM | **nDSM = DSM − DTM:** building heights, floors and height-change detection. **Synthetic ward:** raster arrays. **Jayanagar / Thiruvanmiyur:** GeoTIFF DSM/DTM **simulated** by our code (labelled "sim" in the UI). **Real uploaded DSM/DTM GeoTIFFs** are accepted by the loader, but none have been tested with real survey data. | [PARTIAL] |
| Cadastral maps | Shapefile / GeoJSON / KML etc. **Real TNGIS + NCSCM** parcels (Tamil Nadu). Simulated sheets for Bengaluru and the synthetic wards. | [IMPLEMENTED] |
| Revenue records (RoR) | CSV extracts (Bhoomi-style RTC, Chitta/Patta) in Kannada/Tamil/English, various units. **Simulated data.** | [IMPLEMENTED] (logic) / data simulated |
| Municipal GIS | KML / GeoJSON property-tax layers. **Simulated data.** | [IMPLEMENTED] (logic) / data simulated |
| Utility data | Electricity, water, gas connection ledgers (CSV with GPS), sewer/water line layers. **Simulated.** | [IMPLEMENTED] (logic) / data simulated |
| Ground truth (GT) | Field-visit CSV / GPX waypoints. Used to auto-resolve owner conflicts. **Simulated.** | [IMPLEMENTED] (logic) / data simulated |
| GNSS / CORS | GNSS/DGPS corner points (CSV / GeoJSON) for georeferencing and corner snapping. **No live CORS stream, RINEX or RTK processing.** | [PARTIAL] |
| Building footprints | **Real** OSM + Microsoft ML footprints, fused and cross-checked; or extracted from ORI+DSM. | [IMPLEMENTED] |
| AI/ML spatial matching | Gradient-boosted classifier (scikit-learn) on 7 geometric/ID features, trained on synthetic wards. | [IMPLEMENTED] (see §9 caveats) |
| Topology correction | Bow-tie repair, duplicates, GNSS snapping, shared-corner merging, overlap/gap/sliver fixing. | [IMPLEMENTED] |
| Attribute mapping | Column-name and column-value evidence, unit detection, ID/name normalisation, Indic script transliteration. Reviewer confirmations are persisted. | [IMPLEMENTED] |
| Georeferencing / coordinate transformation | CRS detection (declared / inferred), pyproj reprojection to the local UTM zone, 6-parameter affine fit on GNSS control with outlier rejection, residual datum-shift correction, layer co-registration. | [IMPLEMENTED] |
| Change detection | Two-date footprint + height comparison: new / demolished / extension / new floor. | [IMPLEMENTED] (on synthetic or simulated second epochs) |
| Spatial conflict resolution | 18 conflict types. Auto-resolution only by a more reliable source (GNSS, field visit). Otherwise human review with side-by-side versions. Learned rules need admin approval. | [IMPLEMENTED] |
| Confidence scoring | Weighted 5-component score (0–100) with conflict caps; thresholds 85 / 70. | [IMPLEMENTED] (heuristic weights) |
| Reduce manual GIS work | Share of parcels finalised without manual work: 79–84% across areas (§19). | [IMPLEMENTED] (measured on our datasets) |
| Land-record consistency | Maker–checker updates, re-applied on every run; cross-department checks. | [IMPLEMENTED] |
| Interoperability | REST API, OGC API – Features, exports (GeoJSON / CSV / KML / GPKG / SHP), ISO 19152 LADM-aligned GeoPackage, API keys. | [IMPLEMENTED] |
| Accelerate cadastral workflows | End-to-end runs in seconds to about a minute per ward-sized area (§19). | [IMPLEMENTED] |

**Contradictions or weak spots vs the PS (be ready for these):**
1. **No real drone, DSM/DTM, GNSS/CORS, RoR or municipal data.** All simulated or synthetic. Only building footprints, roads/landmarks and TN cadastral parcels are real.
2. **Building extraction is classical computer vision** (thresholds + morphology on nDSM + a vegetation index), **not deep learning**.
3. **The ML matcher's cross-validation score (ROC-AUC 1.0) is on synthetic training data.** It effectively relies on only two overlap features (§9).
4. **No NAKSHA system integration**; only a ULPIN-*format* ID generator.

---

## 3. COMPLETE SOLUTION FLOW (as implemented)

The pipeline has **11 steps** (`pipeline.py`, `STEPS`). Every step logs what it did and writes audit entries.

```
Raw department files (any mix of formats / CRSs)          data/<area>/raw/
  │
  1 INGEST        read via GDAL/OGR (pyogrio) + CSV/GPX/GeoJSONL/images/GeoTIFF; detect dataset ROLE
  │               (cadastral, revenue, municipal, gnss, GT, footprints, utility, electricity...) ; SHA-256 each file
  2 SCHEMA        map columns → canonical fields (plot_id, owner_name, area, land_use, record_id...) using
  │               column-name + column-value evidence; detect area units → m²; normalise IDs & names
  3 CRS           detect CRS (declared .prj/WKT or inferred from coordinate ranges); choose local UTM zone;
  │               reproject; cadastral sheet in unknown grid or legacy datum → affine fit on GNSS control
  4 EXTRACT       buildings from ORI + DSM/DTM (classical CV)  OR  from AI footprint layers (Microsoft) fused
  │               with OSM; heights/floors from nDSM
  5 TOPOLOGY      repair bow-ties, drop duplicates, snap corners to GNSS, merge shared corners,
  │               resolve overlaps, fill gaps, remove slivers
  6 MATCH         co-register layers (remove whole-layer shifts); ML matcher links parcels ↔ municipal
  │               polygons (detects merged properties); link RoR, GT, department records (plot ref + GPS)
  7 CHANGE        compare two survey dates: new / demolished / extension / new floor
  8 CONFLICT      18 rule checks → conflict tickets; auto-resolve only with a more reliable source;
  │               route to departments; apply saved reviewer decisions and admin-enabled learned rules
  9 CONFIDENCE    C = 100·(0.30G + 0.20A + 0.20S + 0.15M + 0.15T), capped by open conflicts →
  │               approved (≥85) / flagged (70–85) / review (<70) / verified (human-decided)
 10 EVALUATE      only where a hidden answer key exists: precision/recall/F1, boundary error, calibration
 11 PUBLISH       result.json (15 layers), SQLite index, hash-chained audit, web layers
  │
  ▼
Web app (map, review, requests, exports, APIs, PDF reports)
```

**Differences from the generic flow in the brief:**
- Validation happens **inside** steps 3, 5 and 8, not as a separate step.
- "Evaluate" is a benchmark step that needs a hidden answer key, not a production validation.

---

## 4. ACTUAL FEATURES

### 4a. Implemented (core pipeline)

| Feature | Purpose | Input | Processing / logic | Output | Tech | Status |
|---|---|---|---|---|---|---|
| Multi-format ingest | Accept files as departments deliver them | SHP, KML/KMZ, GPX, GPKG, GML, FGB, GeoJSON(L), CSV with lat/lon, PNG/JPG, GeoTIFF, .npy | GDAL/OGR read; dataset role detected from name/columns; SHA-256 per file | Typed datasets | pyogrio (GDAL 3.x), pandas, Pillow | [IMPLEMENTED] |
| Attribute mapping | One schema from many column names | Column names + values | Token synonyms + fuzzy match (RapidFuzz) + value profiling (looks like ID / name / area); negative tokens ("father"); unit detection (sq ft, sq yd, gunta, cent, ankanam, acre, ha) | Canonical fields + confidence + evidence | rapidfuzz, regex | [IMPLEMENTED] |
| CRS detection & reprojection | Common coordinate frame | Declared CRS or raw coordinates | Parse EPSG/WKT; else infer (lat/lon, UTM-like, Web Mercator ranges); pick UTM zone from data location; pyproj | All layers in local UTM | pyproj | [IMPLEMENTED] |
| GNSS georeferencing | Fix sheets in unknown grids / legacy datums | Cadastral polygons + GNSS corner points | Coarse affine (parcel centres) → 6 iterations of closest-vertex pairing + least-squares affine + outlier rejection (3× median residual) | 2×3 transform, RMSE before/after, rotation/scale | NumPy | [IMPLEMENTED] |
| Layer co-registration | Remove whole-layer shifts (1–4 m) | Two layers | Median centroid displacement of matched pairs; for buildings, grid search (1 m → 0.25 m → 0.1 m) maximising building-inside-one-plot containment; applied only if > 0.3 m and fit improves | dx, dy, applied flag | Shapely 2, STRtree | [IMPLEMENTED] |
| Topology correction | Clean parcel fabric | Georeferenced parcels + GNSS | 7 ordered operations (§10) | Valid, gap/overlap-free fabric + per-parcel flags | Shapely, SciPy, scikit-learn NearestNeighbors | [IMPLEMENTED] |
| ML spatial matching | Link parcel ↔ municipal polygon | Candidate pairs within 2 m | 7 features → GradientBoostingClassifier probability; best partner per parcel; one polygon matched to 2+ parcels = merged property | Links + probability + features | scikit-learn | [IMPLEMENTED] |
| Record linking | Attach department records to parcels | Plot references + GPS points | Normalised plot ref AND point-in-parcel; GPS wins when inside a parcel; nearest within 5 m otherwise; method recorded | Linked records per parcel | Shapely STRtree | [IMPLEMENTED] |
| Building extraction (raster) | Buildings from drone data | ORI (RGB) + DSM + DTM | nDSM > 2.5 m AND NOT vegetation (excess-green > 0.06) → morphology → contours → polygon, rectangularise if > 0.9 rectangular; height = median nDSM; floors = round((h−0.5)/3.2) | Building polygons with height/floors/score | OpenCV | [IMPLEMENTED] (synthetic ORI only) |
| Building fusion (vector) | Use AI footprints when no imagery | Microsoft ML footprints + OSM | Co-register; split AI outlines that merge row houses using OSM; record agreement (IoU) | Fused building layer + agreement stats | Shapely | [IMPLEMENTED] |
| Heights from DSM/DTM | Floors for permission checks | DSM/DTM GeoTIFFs | Median nDSM in a window around each footprint | height_m, floors | NumPy, custom GeoTIFF reader | [IMPLEMENTED] (tested on simulated DSMs) |
| Change detection | Detect construction | Two epochs | Footprint IoU matching (≥ 0.5 same, < 0.2 new), area growth > 12% = extension, height +0.7 floor = new floor; unmatched old = demolished. Vector areas: DSM differencing inside footprints | Changes layer (CH-###) | Shapely, NumPy | [IMPLEMENTED] |
| Conflict detection & auto-resolution | Surface disagreements | Fused parcels + all links | 18 types (§12); severity; routed to departments; auto-resolve only with GNSS / field visit | Conflict tickets with versions | Python rules | [IMPLEMENTED] |
| Second-cadastral comparison | Compare two independent surveys | TNGIS vs NCSCM (real) | Co-register, pair, IoU/offset/number agreement; conflict if IoU < 0.6 or offset > 2 m | Comparison stats + conflicts | Shapely | [IMPLEMENTED] |
| Confidence scoring | Prioritise work | Components per parcel | Formula in §13 | Score + status per parcel | Python | [IMPLEMENTED] |
| Accuracy evaluation | Measure the pipeline | Hidden answer key | P/R/F1 per issue type, boundary error, calibration by score band | evaluation block in result | Python | [IMPLEMENTED] (synthetic/simulated keys only) |
| Revenue at risk | Quantify tax impact | Tax-related conflicts | Area × assumed ₹/m²/year rates (residential 30, commercial 90) | ₹/year per plot and total, labelled "estimate" | Python | [IMPLEMENTED] (assumed rates) |
| ULPIN-format ID | Stable location-based key | Parcel reference point | 2-letter state code + 12-char geohash | 14-char ID labelled "ULPIN-format" | Python | [IMPLEMENTED] (not the official ULPIN) |
| Ownership chain | Check sale history vs RoR | Registered deeds + RoR owner | Seller→buyer chain; latest buyer vs RoR (name similarity) | Verdict per plot | Python | [IMPLEMENTED] |

### 4b. Implemented (application and workflow)

| Feature | Purpose | Status |
|---|---|---|
| Review queue: side-by-side versions, choose a source or mark actioned, bulk decisions, undo (reopen) | Human verification | [IMPLEMENTED] |
| Maker–checker change requests: officer proposes a field of their department with a reason (+ evidence file for some fields); approver of the same department approves; four-eyes rule; stale check; re-applied on every future run; notifications to affected departments | Secure record updates | [IMPLEMENTED] |
| Map editor for survey staff: add plot, edit boundary, map / correct / remove building (snapping, square, simplify, trace an AI outline, drafts, spatial checks); goes through maker–checker | On-map corrections | [IMPLEMENTED] |
| Learning from reviewers: if ≥ 8 decisions of one conflict type agree ≥ 90% on a source, a rule is **proposed**; admin enables it; it is audited | Reduce repeat review | [IMPLEMENTED] |
| Schema-mapping confirmation by reviewers (persisted overrides) | Human-in-the-loop mapping | [IMPLEMENTED] |
| Search omnibox: plots, khata, PID, ULPIN, owners (Latin/Indic fuzzy), record numbers, buildings, coordinates, places (OSM Nominatim on Enter) | Find anything | [IMPLEMENTED] |
| Plain-language questions ("vacant plots over 200 m²", "how many sales not mutated") via an offline rule parser; optional Claude engine only if an API key is configured | Query without GIS skills | [IMPLEMENTED] rule parser / [PARTIAL] Claude engine (code present, not configured by default) |
| Department-wise plot card (9 departments), building card, area selection with department summaries | Department views | [IMPLEMENTED] |
| RBAC, department need-to-know access matrix, name masking for viewers | Access control | [IMPLEMENTED] |
| Hash-chained audit trail + verification endpoint | Tamper evidence | [IMPLEMENTED] |
| PDF property report (multilingual script shaping) | Officials' document | [IMPLEMENTED] |
| Exports: GeoJSON / CSV / KML / GPKG / SHP, LADM-aligned GeoPackage, OGC API – Features | Interoperability | [IMPLEMENTED] |
| State-wide real cadastral maps (6 South Indian states + Puducherry, 12 PMTiles files, ~3.7 GB) as vector tiles | Context beyond the pilot area | [IMPLEMENTED] (display and click-inspect only; not harmonised) |
| Blind tests (random hidden seed; teammate-edited kit) + benchmark history | Guard against overfitting | [IMPLEMENTED] |
| Offline-capable app shell (service worker) | Poor connectivity | [PARTIAL] (shell and last results cached; editing needs the server) |
| Upload data + run pipeline from the UI (admin) | Operate without CLI | [IMPLEMENTED] |

### 4c. Partial and future

| Item | Status |
|---|---|
| Real drone ORI → building extraction (extractor needs `raster_meta.json` + DSM arrays; uploaded ortho GeoTIFFs are display-only) | [PARTIAL] |
| Deep-learning extraction (SAM / U-Net); the code has a single replaceable entry point `extract_buildings` | [PLANNED] |
| Live CORS / RINEX / RTK processing | [PLANNED] |
| NAKSHA / Bhoomi / TNGIS / municipal system connectors | [PLANNED] |
| PostGIS, multi-server deployment, HTTPS, SSO, 2FA, encryption at rest | [PLANNED] |
| Official ULPIN issuance | [PLANNED] (a state function) |

---

## 5. END-TO-END USER WORKFLOW

1. **Start:** `run.bat` / `run.sh`. It creates a virtual env, installs requirements, generates the synthetic ward if missing, runs the pipeline and serves the app at `http://127.0.0.1:8000`.
2. **Sign in** (session cookie). First sign-in with a one-time password forces a password change.
3. **Pick an area** (top-right selector or ☰ menu): the synthetic ward, Jayanagar, Thiruvanmiyur, or the two blind tests.
4. **Overview tab.**
   - Headline: % of parcels finalised without manual work.
   - Parcels harmonised, conflicts to review, boundary error, processing time, tax at risk, audit chain status and reviewer hours saved (an estimate from stated assumptions).
5. **Map.**
   - Parcels are coloured by status. Conflicts are shown as pins, plus buildings, changes and utilities.
   - Basemaps: Drone / Satellite / Map / None (Google if a key is set).
   - Quick-filter chips: Conflicts, Needs review, Tax at risk, Sale not mutated, Encroachments, New construction, Vacant, Surveys disagree.
6. **Click a plot** to open the card.
   - Common info: owner, areas, IDs, confidence components, sources fused, path to approval, ownership chain, conflicts.
   - **By department** tabs. Each shows the department's main details, checks, all source columns, routed conflicts, updates, and the fields it controls.
   - Click a building for its own block; drag a rectangle (`A`) for an area summary.
7. **Approver:** Review tab → choose which version is right (or bulk-decide). The decision is audited and the parcel becomes "verified".
8. **Officer:** "Propose a change" on a field of their department (with a reason and evidence) → Requests tab.
   - The approver of the same department approves or rejects it; it can never be the proposer.
   - The record updates, related conflicts close and other departments get notices.
9. **Survey officer:** map a building, edit a boundary or add a plot on the map. These also go through approval.
10. **Admin:** upload new department files → run the pipeline (progress shown per step) → review Data (detected formats, CRS, mappings), Accuracy (benchmarks), Audit, and Admin (accounts, API keys, department access matrix, learned rules).
11. **Outputs:** export layers or the LADM GeoPackage, download the PDF property report, query via REST/OGC API, and search or ask questions.

---

## 6. TECHNICAL ARCHITECTURE

| Layer | Actual implementation |
|---|---|
| Frontend | Single-page app in vanilla JavaScript (~2,900 lines), Leaflet 1.9 map, protomaps-leaflet for PMTiles vector tiles, Geist font, service worker; no framework, no build step |
| Backend | Python 3 (developed on 3.14), FastAPI + Uvicorn; 70 route handlers (64 under /api and /ogc; the rest serve the app, tiles and outputs) |
| Database | SQLite (WAL mode), 10 tables; plus JSON result files and JSONL audit logs per area |
| GIS | Shapely 2 (geometry, STRtree index), pyproj (CRS), pyogrio/GDAL (I/O), GeoPandas (exports), custom minimal GeoTIFF reader/writer |
| ML | scikit-learn GradientBoostingClassifier (matching); scikit-learn NearestNeighbors and SciPy connected components (corner merging) |
| Computer vision | OpenCV: thresholding, morphology, contour tracing, polygon simplification, min-area rectangles |
| ETL | `pipeline.py`: 11 steps, run in a background thread per area, with progress polling |
| External calls | OSM Nominatim (place search, on Enter only), Esri World Imagery / Street Map tiles (display), optional Google Map Tiles (key), optional Anthropic Claude (query parsing, key). **No government system is connected.** |
| Deployment | Local: one Uvicorn process on 127.0.0.1:8000; `run.bat` / `run.sh`. No Docker, cloud or HTTPS in the repo. |
| Security | PBKDF2-SHA256 (310,000 iterations) passwords, hashed session tokens (HttpOnly cookie, 12 h), hashed API keys, RBAC + department access matrix, rate limit on sign-in, security headers, hash-chained audit |

```
┌──────────────────────── Browser (SPA: Leaflet + vanilla JS + service worker) ───────────────────────┐
│ Map & layers │ Plot/building/area cards │ Review │ Requests │ Map editor │ Search/Q&A │ Admin │ Profile │
└───────────────┬───────────────────────────────────────────────────────────────────────┬─────────────┘
                │ REST (JSON), OGC API – Features, PMTiles byte ranges, exports          │ tiles
┌───────────────▼─────────────────── FastAPI (Uvicorn) ─────────────────┐         Esri / OSM Nominatim
│ auth & RBAC · dept access filter · privacy masking · review · records │         (external, display/search)
│ search · query parser · exports · OGC · PDF reports · uploads · runs  │
└───────┬──────────────────────────────┬────────────────────────────────┘
        │                              │ background thread
┌───────▼─────────┐        ┌───────────▼───────────────── Pipeline (11 steps) ─────────────────────────┐
│ SQLite (users,  │        │ ingest → schema → CRS/georef → extract → topology → match → change →       │
│ sessions, keys, │◄───────┤ conflict → confidence → evaluate → publish                                  │
│ runs, parcels,  │ index  │ (pyogrio/GDAL, pyproj, Shapely, scikit-learn, OpenCV, NumPy/SciPy)          │
│ conflicts, CRs, │        └───────────┬───────────────────────────────────────────────────────────────┘
│ decisions,      │                    │
│ notices, access)│        data/<area>/raw (inputs) → output/<area>/result.json, audit.jsonl, web/*.png
└─────────────────┘        data/_statewide/<state>/*.pmtiles (real cadastral vector tiles)
```

---

## 7. TECHNOLOGY STACK (only what is used)

| Layer | Technology | Purpose | Status |
|---|---|---|---|
| Language | Python 3 (3.14 on the dev machine) | Backend + pipeline | [IMPLEMENTED] |
| API | FastAPI, Uvicorn, Pydantic, python-multipart | REST, uploads, OpenAPI docs at /docs | [IMPLEMENTED] |
| Geometry | Shapely 2.1 | Overlay, validation, indexing (STRtree) | [IMPLEMENTED] |
| CRS | pyproj 3.8 | Detection, reprojection | [IMPLEMENTED] |
| I/O | pyogrio 0.13 (GDAL), GeoPandas, pandas | Read/write vector formats | [IMPLEMENTED] |
| ML | scikit-learn 1.9 | Gradient boosting matcher, nearest neighbours | [IMPLEMENTED] |
| Numeric | NumPy, SciPy | Affine fits, rasters, graph components | [IMPLEMENTED] |
| CV | OpenCV (headless) | Building extraction, raster warping | [IMPLEMENTED] |
| Text | RapidFuzz, indic-transliteration | Fuzzy column/name matching; Kannada/Tamil/Devanagari → Latin | [IMPLEMENTED] |
| Reports | ReportLab + uharfbuzz | PDF with Indic script shaping | [IMPLEMENTED] |
| Imaging | Pillow | GeoTIFF read/write | [IMPLEMENTED] |
| DB | SQLite | Accounts, runs, index, workflows | [IMPLEMENTED] |
| Frontend | Leaflet, protomaps-leaflet, vanilla JS/CSS, service worker | Map UI | [IMPLEMENTED] |
| Tiles | PMTiles (datameet cadastral archives) | State-wide parcel display | [IMPLEMENTED] |
| Tests | pytest, httpx/TestClient; Playwright (developer UI checks, not in repo tests) | Automated tests | [IMPLEMENTED] |
| Optional | Anthropic SDK (Claude) | Alternative query parser | [PARTIAL] (off unless a key is set) |

**Not used (do not list):** PostGIS, PostgreSQL, TensorFlow, PyTorch, deep learning models, Docker, Kubernetes, cloud services, blockchain networks, QGIS/ArcGIS.

---

## 8. DATASETS

### 8a. Areas (workspaces)

| Area | What is real | What is simulated | Parcels |
|---|---|---|---|
| **Ward 47, Mangaluru (demo)** | Nothing (fully synthetic, known truth) | Everything, incl. rendered drone ORI (2024, 2026) + DSM/DTM | 240 |
| **Jayanagar, Bengaluru** | OSM buildings (722), Microsoft ML footprints (551), OSM roads (121), landmarks (127) | Cadastral sheet (Kalianpur 1975 / India IVa shapefile), RTC (Kannada names, guntas), BBMP tax KML, DGPS (1,010 pts), field visits GPX, all department records, DSM/DTM | 769 |
| **Thiruvanmiyur, Chennai** | **TNGIS cadastral (989 parcels) and NCSCM cadastral (1,118)**, real government surveys via datameet (CC0), OSM, Microsoft footprints (1,632) | Chitta/Patta (Tamil names, hectares), GCC tax KML, DGPS (736), GPX visits, department records, DSM/DTM | 937 |
| **Blind test (random seed)** | None | Synthetic ward with hidden random seed/layout/noise | 231 |
| **Teammate blind test** | None | Demo copy edited by hand by a teammate; no answer key | 240 |
| **State-wide maps** | Cadastral PMTiles: Andhra Pradesh (APSAC, forest, NCSCM), Karnataka (KGIS, NCSCM), Kerala (Bhuvan, LSGDP urban, NCSCM), Tamil Nadu (TNGIS, NCSCM), Telangana, Puducherry | n/a | display only |

### 8b. Dataset types used

| Name | Source/type | Format | Spatial? | Important fields | Processing | Output | Status |
|---|---|---|---|---|---|---|---|
| Cadastral map | Survey dept (real TNGIS/NCSCM; else simulated) | SHP / GeoJSON | Polygon | survey no, sub-div/hissa, extent, village, sheet | CRS detect → georef on GNSS → topology | Parcel fabric | [IMPLEMENTED] |
| RoR / RTC / Chitta | Revenue (simulated) | CSV | No | survey/hissa, khata/patta, owner (Kannada/Tamil), extent (gunta/ha/sq yd), land type | Schema map, unit → m², ID normalise, name transliterate | Linked RoR per parcel | [IMPLEMENTED] |
| Property tax | Municipal (simulated) | KML / GeoJSON | Polygon | PID, site no, owner, area sq ft, usage, ward | Schema map, co-register, ML match | Links, merged properties | [IMPLEMENTED] |
| GNSS / DGPS | Survey (simulated) | CSV / GeoJSON | Point | point id, survey ref, lat/lon, fix, HRMS | Georeferencing, corner snapping | Transform + snapped corners | [IMPLEMENTED] |
| Field verification (GT) | Survey (simulated) | CSV / GPX | Point | visit id, plot, owner, use | Link to parcel | Owner conflict auto-resolution | [IMPLEMENTED] |
| Building footprints | OSM (real), Microsoft ML (real) | GeoJSON / GeoJSONL | Polygon | geometry, confidence | Co-register, fuse, split row houses | Fused buildings | [IMPLEMENTED] |
| Drone ORI | Rendered (synthetic wards) | PNG + meta | Raster | RGB | Vegetation index + nDSM extraction | Buildings | [PARTIAL] |
| DSM / DTM | Rendered (synthetic) / simulated GeoTIFF | NPY / GeoTIFF | Raster | elevation | nDSM heights, change | Heights, floors, changes | [PARTIAL] |
| Electricity / water / gas | Utilities (simulated) | CSV (+GPS) | Point | RR/consumer/BP no, name, tariff/type, load, meter, status | Link by ref + GPS | Connections per parcel | [IMPLEMENTED] |
| Sewer / water lines | Utilities (simulated) | GeoJSON | Line | asset type | Under-building check | Conflicts | [IMPLEMENTED] |
| Building permissions | Town planning (simulated) | CSV | No | permission no, applicant, approved floors, BUA, date | Link | Floors check | [IMPLEMENTED] |
| Registered deeds | IGR (simulated) | CSV | No | doc no, date, buyer, seller, consideration | Link, ownership chain | Mutation-pending conflicts | [IMPLEMENTED] |
| Roads / landmarks | OSM (real) | GeoJSON | Line / point | name, highway, kind | Display, "near X" questions | Layers | [IMPLEMENTED] |

**PS dataset classification:**

| PS dataset | Classification |
|---|---|
| Drone imagery / ORI | Sample-simulated (rendered for synthetic wards); uploaded real orthomosaic display only |
| DSM / DTM | Sample-simulated |
| Cadastral maps | **Implemented with real data** (Tamil Nadu); simulated elsewhere |
| Revenue records | Sample-simulated |
| Municipal GIS | Sample-simulated |
| Utility data | Sample-simulated |
| Ground truth | Sample-simulated |
| GNSS / CORS | GNSS points sample-simulated; CORS streams not implemented (future) |
| Building footprints | **Implemented with real data** (OSM + Microsoft) |

---

## 9. AI / ML: precise and honest

| Where | What it really is | Category |
|---|---|---|
| **Parcel ↔ municipal matching** | `GradientBoostingClassifier` (150 trees, depth 3, lr 0.1). Candidate pairs: municipal polygons within 2 m of a parcel (STRtree). **7 features:** IoU, coverage of A, coverage of B, centroid distance, area ratio, Hausdorff distance, ID similarity. Output: match probability; threshold 0.5; best partner per parcel; a polygon won by 2+ parcels = merged property. | **Supervised ML** |
| Training data | **6 synthetic training wards** (never the evaluated ward) with vertex noise 0.3–2.0 m and shifts up to 3 m: 5,378 pairs, 1,299 positive. | Synthetic |
| Measured | 4-fold CV ROC-AUC **1.000**, F1 **0.9996** (synthetic). Link F1 vs answer key: 1.000 (demo, Jayanagar), 0.999 (Thiruvanmiyur). | Measured |
| **Caveat** | Feature importance: `cover_a` 0.551, `iou` 0.449, all others **0.0**. The model effectively learned an overlap rule. The task is easy on these data because layers are co-registered first. Real messier data could need retraining with real labelled pairs. | Be upfront |
| **Building extraction** | Classical CV: nDSM > 2.5 m, excess-green vegetation mask, morphological open/close, contour tracing, Douglas–Peucker, min-area rectangle regularisation; confidence = 0.45·rectangularity + 0.35·roof-height consistency + 0.2·size. | **Computer vision, rule-based (no DL)** |
| Measured | Extraction F1 1.000, mean IoU 0.919, height MAE 0.004 m on **synthetic rendered imagery**. This is not evidence for real imagery. | Synthetic only |
| AI footprints | Uses Microsoft's ML-extracted footprints (their deep model, their output) as an input layer, fused with OSM. | Third-party AI output |
| Attribute mapping | Fuzzy string matching + value profiling + synonym dictionaries. | Heuristic / rule-based |
| Owner-name similarity | Transliteration (Kannada/Tamil/Devanagari → Latin), honorific/initial handling, phonetic folding, one-to-one token pairing, RapidFuzz scores; threshold 75. | Heuristic |
| Georeferencing / co-registration | Least squares + iterative closest-vertex + robust statistics + grid search. | Classical optimisation |
| Topology corner merging | NearestNeighbors radius graph + connected components. | Algorithmic (uses an ML library, not ML) |
| Conflict rules, confidence score | Deterministic rules; hand-set weights. | Rule-based / heuristic |
| Learning from reviewers | Frequency rule (≥ 8 decisions, ≥ 90% agreement) → proposed rule, admin-approved. | Statistical rule induction, not ML |
| Plain-language questions | Offline deterministic parser (default). Optional Claude (LLM) parser if a key is configured; not used in the demo by default. | Rule-based (LLM optional) |

**Bottom line for slides:**
- **Say:**
  - one supervised ML model (the matcher);
  - classical computer vision for buildings;
  - statistical/heuristic intelligence elsewhere;
  - an extraction interface ready for deep models.
- **Do not say:** "deep learning", "neural network" or "AI-powered everything".

---

## 10. GIS / SPATIAL PROCESSING

| Capability | Status | Actual implementation |
|---|---|---|
| CRS handling | [IMPLEMENTED] | Declared EPSG/WKT/.prj, or inference from coordinate ranges; legacy Kalianpur 1975 handled via pyproj; working CRS = UTM zone of the data (43N Bengaluru/Mangaluru, 44N Chennai) |
| Georeferencing | [IMPLEMENTED] | 6-parameter affine from GNSS control (≥ 3 control parcels), iterative closest-vertex, outlier rejection; residual datum-shift refit when RMSE after reprojection > 1 m |
| Coordinate transformation | [IMPLEMENTED] | pyproj reprojection; outputs in EPSG:4326 for web/export |
| Spatial joins | [IMPLEMENTED] | STRtree queries: parcel ↔ municipal, point-in-parcel for records, building ↔ parcel |
| Intersection / overlay | [IMPLEMENTED] | IoU, coverage, encroachment protrusion area, utility-line-under-footprint |
| Buffer / distance | [IMPLEMENTED] | 2 m candidate search, 5 m nearest parcel for points, negative buffers for tolerance, Hausdorff |
| Polygon matching | [IMPLEMENTED] | ML matcher (§9); building IoU matching between epochs/sources |
| Geometry validation | [IMPLEMENTED] | Validity check, bow-tie repair, make_valid fallback, duplicate IDs |
| Topology correction | [IMPLEMENTED] | Invalid repair → duplicates → GNSS snap (2.5 m same plot / 0.8 m other) → shared-corner merge (1.2 m) → overlaps to the more reliable parcel → gaps ≤ 25 m² to the longest shared edge → slivers |
| Feature extraction | [PARTIAL] | Buildings from ORI+DSM (synthetic) or fused AI footprints (real) |
| Change detection | [IMPLEMENTED] | Epoch comparison (vector) or DSM differencing inside footprints (raster) |
| Spatial conflict detection | [IMPLEMENTED] | Encroachment, building spanning plots, utility under building, geometry disagreement, surveys disagree |
| Spatial indexing | [IMPLEMENTED] | Shapely STRtree in memory; PMTiles directory for state-wide tiles. No DB spatial index (SQLite stores geometry as text). |
| Map visualisation | [IMPLEMENTED] | Leaflet layers, status colouring, conflict pins, heights hillshade, drone overlay, PMTiles vector tiles, legend, measure tool, area selection |

---

## 11. DATA HARMONISATION (how the files become one record)

1. **Schema normalisation:** each dataset's columns map to canonical fields: `plot_id`, `plot_sub`, `owner_name`, `area`, `land_use`, `record_id`. Name evidence plus value evidence gives a confidence. Unmapped or uncertain columns are shown in the Data tab for confirmation.
2. **Field / type normalisation:**
   - Area units → m²: sq ft, sq yd, gunta, cent, ankanam, acre, ha.
   - Plot IDs normalised: "Sy.No. 102 Hissa 1" / "102-1" / "KH-102/1" → `102/1`.
   - Land use normalised: RES → residential, and so on.
   - Names transliterated and folded.
3. **CRS normalisation:** everything goes to the area's UTM zone (§10).
4. **Geometry normalisation:** repair, snap, merge corners, remove overlaps and gaps (§10).
5. **Duplicate detection:** same plot ID digitised twice → keep one (auto-resolved, low severity). Duplicate conflict IDs are de-duplicated before storage.
6. **Missing values:**
   - Missing RoR → NO_REVENUE_RECORD.
   - RoR row with no parcel → RECORD_WITHOUT_PARCEL.
   - Built plot not in the tax register → MISSING_IN_MUNICIPAL.
   - Blank source columns are dropped from display.
7. **Record matching:**
   - Parcel ↔ municipal: ML.
   - Parcel ↔ RoR / GT: normalised plot ID.
   - Department records: plot ref + GPS, with the method recorded per link.
8. **Conflicting attributes:** kept side by side as "versions" on a conflict ticket. Never silently overwritten (§12).
9. **Final unified record (per parcel):**
   - fused boundary, plot ID, status, score and components;
   - resolved owner with its source; RoR / municipal / GT owners;
   - khata, PID, ULPIN-format ID;
   - surveyed area, RoR extent, land use;
   - buildings with heights and floors;
   - linked department records with **all source columns**, and registers (revenue / survey / municipal);
   - conflicts, official updates, ownership chain;
   - list of sources fused.

   It is stored in `result.json`, indexed in SQLite (`parcels` table) and served by the API.

---

## 12. CONFLICT RESOLUTION

**18 conflict types:**

| Group | Types |
|---|---|
| Ownership & extent | OWNER_MISMATCH, AREA_MISMATCH |
| Boundaries | GEOMETRY_DISAGREEMENT, CADASTRAL_SOURCES_DISAGREE |
| Parcel geometry | INVALID_GEOMETRY, DUPLICATE_PARCEL |
| Record presence | MERGED_PARCEL, NO_REVENUE_RECORD, RECORD_WITHOUT_PARCEL, MISSING_IN_MUNICIPAL |
| Buildings | ENCROACHMENT, BUILDING_SPANS_PLOTS |
| Registration | MUTATION_PENDING |
| Tax & services | TAX_USE_MISMATCH, SERVICE_WITHOUT_TAX, UNASSESSED_CONSTRUCTION |
| Planning & utilities | FLOORS_BEYOND_PERMISSION, UTILITY_UNDER_BUILDING |

Each ticket carries severity, detail, versions, a suggestion, the routed departments and, for tax-related types, estimated revenue.

| Situation | Automated | Manual |
|---|---|---|
| Different owner names | Similarity < 75 → conflict. **Auto-resolved** if a field visit matches one side (≥ 75); the other side is marked stale. | Otherwise approver chooses; officer proposes a mutation via maker–checker |
| Boundary mismatch (cadastral vs municipal) | Conflict if mean offset > 1.5 m or IoU < 0.5 (after co-registration). **Auto-resolved** when the parcel has ≥ 3 GNSS corners (GNSS outranks municipal). | Otherwise reviewer; survey officer can edit the boundary |
| Two cadastral surveys disagree | Conflict if IoU < 0.6 or offset > 2 m after co-registration | Survey reviews |
| Building vs cadastral | Encroachment needs a protrusion ≥ 4 m²; stricter evidence if only one building layer sees it; merged-outline and outline-disagreement checks (rules v2) | Reviewer / Roads / Town Planning |
| CRS mismatch | **Fully automatic:** detection, reprojection, georeferencing, co-registration (logged with RMSE) | None required |
| Duplicates / invalid polygons | **Auto-resolved** (low severity) | none |
| Missing data | Conflict tickets | Departments act |
| Area mismatch | Tolerance = max(10%, 1.5·0.3 m·perimeter/area). **Auto-resolved** when GNSS-surveyed (RoR to be corrected) | Otherwise reviewer / revenue officer |

- **Priority of sources:** GNSS 1.00 > ground truth 0.95 > drone/AI 0.80 > municipal 0.65 > cadastral 0.60.
- **Learned rules** exist but are **off until an admin enables them**. Saved human decisions always override.

---

## 13. CONFIDENCE SCORING [IMPLEMENTED, heuristic weights]

**C = 100 × (0.30·G + 0.20·A + 0.20·S + 0.15·M + 0.15·T)**, where each component is between 0 and 1:

- **G, geometric agreement:**
  - max(IoU/0.85, 1 − max(0, offset − 0.5)/2) against the municipal polygon;
  - 0.7 if the parcel is part of a merged property; 0.55 if there is no municipal match;
  - at least 0.97 if the parcel has ≥ 3 GNSS corners.
- **A, attribute agreement:** mean of:
  - RoR linked (1.0, or 0.2 if missing);
  - owner similarity / 100;
  - area agreement;
  - field-verified (1.0).
- **S, source reliability:**
  - 1.0 with ≥ 3 GNSS corners; 0.8 with 1–2; 0.6 for cadastral only;
  - +0.1 if field-verified; +0.1 if municipal IoU > 0.8;
  - capped at 1.
- **M, model confidence:** ML match probability (0.5 if no municipal record).
- **T, topology validity:** 1.0, reduced by topology repairs.

**Caps and statuses:**
- An open high-severity conflict caps the score at 69; an open medium one caps it at 84.
- **approved** ≥ 85; **flagged** 70–84.9; **review** < 70.
- **verified:** a human decided its conflicts and none remain open (score raised to ≥ 95).

**Calibration (measured against the answer keys):**
- Demo: 0% of parcels with an error in the approved band; 4.3% of "review" parcels had an error.
- Jayanagar: 0% approved / 2.5% review.
- Thiruvanmiyur: 0% approved / 2.1% review.

In other words, errors land in the review band, not the approved band. The weights are **hand-set**, not learned.

---

## 14. DATABASE (SQLite, `output/landconnect.db`)

| Table | PK | Key fields | Relationships |
|---|---|---|---|
| users | username | full_name, role (viewer/officer/reviewer/admin), department, pw_hash, pw_salt, active, must_change, designation, contacts, prefs, login history | 1-N sessions |
| sessions | token_hash | username (FK users), expires_at, ip, user_agent, last_seen_at | N-1 users |
| api_keys | key_hash | prefix, name, role, department, created_by, last_used, active | |
| runs | run_id | ws, status, started_by, times, seconds, summary JSON, audit_head | logical 1-N parcels/conflicts (run_id) |
| decisions | (ws, conflict_id) | choice, note, decided_by, decided_at | logical → conflicts |
| parcels | (ws, plot_id) | status, score, owner, area_sqm, land_use, khata, municipal_pid, open_conflicts, **geometry (GeoJSON text)**, properties JSON, run_id | logical 1-N conflicts, change_requests |
| conflicts | (ws, conflict_id) | type, severity, status, plot_id, detail, resolution, resolved_by, run_id | N-1 parcels (logical) |
| change_requests | id | ws, plot_id, department, field, record_id, old/new value, reason, evidence_sha, status (pending/approved/rejected/stale/withdrawn), proposed_by/at, decided_by/at | N-1 parcels (logical), users (logical) |
| notifications | id | ws, department, plot_id, kind, message, ref, acked_by/at | |
| dept_access | (viewer, target) | level (full/summary/none), updated_by/at | |

- **Declared foreign key:** only `sessions → users`. The others are **logical** (by ws/plot_id/username).
- **Spatial column:** `parcels.geometry` as GeoJSON **text**. No spatial DB index.
- **Kept outside the DB:** full results (`result.json`), audit trails (`audit.jsonl`), learned rules (`learned_rules.json`), evidence files (by SHA-256).

```
users 1───N sessions         runs 1···N parcels (run_id)        parcels 1···N conflicts (ws, plot_id)
  │                                                            parcels 1···N change_requests
  └──(username)···· change_requests.proposed_by / decided_by   conflicts 1···1 decisions (ws, conflict_id)
api_keys (standalone)   notifications (ws, department)   dept_access (viewer_dept × target_dept)
```

---

## 15. APIs (actual routes; OpenAPI docs at `/docs`)

| Method | Endpoint | Purpose |
|---|---|---|
| POST | /api/login, /api/logout | Session sign-in/out (rate-limited: 5 failures / 5 min per IP) |
| GET | /api/me | Current user, title, department access |
| GET/PUT | /api/me/profile | Own profile details, prefs, sessions, activity |
| POST | /api/me/password | Change own password |
| POST | /api/me/sessions/{id}/end, /api/me/sessions/end-others | Sign out devices |
| GET/POST | /api/admin/users; POST /api/admin/users/{u}/active | Admin account management |
| GET/PUT | /api/admin/dept-access | Department access matrix |
| GET/POST | /api/admin/api-keys; POST /api/admin/api-keys/{prefix}/revoke | API keys |
| GET/POST | /api/access/matrix, /api/access/users, /api/access/users/{u}/role, /active, /reset-password; GET /api/access/audit | Delegated granting (admin or department approver), access log |
| GET | /api/workspaces, /api/config | Areas; front-end config |
| POST/GET | /api/run, /api/status, /api/runs | Run pipeline (admin), progress, history |
| GET | /api/result | Full published result (filtered per user) |
| POST | /api/reset-demo | Regenerate synthetic ward |
| POST | /api/upload | Upload department files (admin; ≤ 250 MB each) |
| POST | /api/decisions, /api/decisions/bulk, /api/decisions/{id}/reopen | Reviewer decisions |
| POST | /api/schema/confirm | Confirm a column mapping |
| GET/POST | /api/learning, /api/learning/rule | Learned rules (admin enables) |
| GET | /api/departments | Departments, fields, titles, open work |
| GET/POST | /api/change-requests, /batch, /{id}/approve, /reject, /withdraw | Maker–checker |
| GET | /api/evidence/{sha} | Evidence file of a request |
| GET/POST | /api/inbox, /api/inbox/{id}/ack | Department notifications |
| GET | /api/audit, /api/audit/verify | Audit entries; hash-chain verification |
| GET | /api/stats | DB counts |
| GET | /api/search/parcels, /api/suggest, /api/geocode | Search, suggestions, OSM place search |
| POST | /api/query | Plain-language question → filter + results |
| GET | /api/parcels/{plot_id} | One harmonised parcel + conflicts |
| GET | /api/parcels/{plot_id}/report.pdf | PDF property report (approver/admin) |
| GET | /api/export/{layer}.{fmt} (geojson/csv/kml/gpkg/shp); /api/export/ladm.gpkg | Exports |
| GET | /ogc, /ogc/conformance, /ogc/collections, /ogc/collections/{id}, /ogc/collections/{id}/items | OGC API – Features (bbox, limit, offset, status filters) |
| GET | /api/statewide, /statewide/{state}/{name}, …/overview.png | State-wide PMTiles |

- **Authentication:** session cookie, or `X-API-Key` header.
- **Filtering:** every data endpoint applies role masking and department access filtering on the server.

---

## 16. FRONTEND / UI (one page, map-first like Google Maps)

| Screen / component | Purpose | User actions | Data / API |
|---|---|---|---|
| Sign-in + forced password change | Access | Sign in; set new password | /api/login, /api/me/password |
| Map (Leaflet) | Main view | Pan/zoom, click plot/building, right-click menu (coordinates, what's here, measure, select area, add plot, share link) | /api/result |
| Search pill + suggestions | Find things / ask questions | Type, arrow keys, Enter | /api/suggest, /api/query, /api/geocode |
| Quick-filter chips | One-click queries | Toggle chip | /api/query |
| Side panel tabs | Work lists | Overview, Review, Data, Changes, Accuracy, Requests, Audit, Admin/Access, Profile, Results | various |
| Plot card | Parcel dossier | Read; open conflict; propose change; map building; edit boundary; PDF; department tabs | result data, /api/change-requests |
| Building block / Area summary | Building or area view | Click building; drag rectangle; CSV export | client-side on result |
| Review tab | Decide conflicts | Choose version, bulk decide, undo | /api/decisions* |
| Requests tab | Maker–checker | Approve/reject/withdraw, batch | /api/change-requests* |
| Map editor | Survey edits | Draw/adjust polygons, snap, square, trace, drafts, save as request | /api/change-requests |
| Data tab | Provenance | See formats, CRS, mappings; confirm mapping | result, /api/schema/confirm |
| Accuracy tab | Benchmarks | View P/R/F1, calibration, blind tests, learned rules | result, /api/learning |
| Audit tab | Traceability | Filter, verify chain | /api/audit, /api/audit/verify |
| Admin tab | Administration | Accounts, roles, resets, API keys, department access matrix, runs | /api/access*, /api/admin* |
| Profile | Own account | Edit details, prefs, change password, end sessions | /api/me/* |
| Layers panel | Map type and layers | Drone / Satellite / Google / Map / None, survey year, layer toggles, state-wide maps | /api/statewide |
| Upload + Run overlays | Admin data ops | Upload files, run, watch progress | /api/upload, /api/run, /api/status |
| Tools | Utility | Measure distance/area, locate me, fit area, full screen, keyboard shortcuts, menu drawer | client-side |

**Layout:**
- **Desktop:** side sheet on the left, map filling the rest.
- **Phone:** the side sheet becomes a bottom sheet.

---

## 17. OUTPUTS (what the MVP actually produces)

- **Per-area `result.json` with 15 layers:** parcels, buildings, changes, cadastral_naive, cadastral_georef, cadastral_alt, municipal, footprints, ai_buildings, roads, poi, connections, utility, gnss, ground_truth.
- Harmonised parcel records with status and confidence (0–100) and its 5 components.
- Conflict tickets (18 types) with versions, severity, routing, auto-resolution text and **estimated** tax at risk.
- Detected changes (new / demolished / extension / new floor).
- Georeferencing report: RMSE before/after, rotation, scale, control points used.
- Co-registration report per layer; cadastral-vs-cadastral comparison (Thiruvanmiyur).
- Accuracy report (only where an answer key exists) and benchmark history.
- **Exports:** GeoJSON, CSV (WKT), KML, GeoPackage, zipped Shapefile (EPSG:4326); LADM-aligned GeoPackage.
- PDF property report per plot.
- Hash-chained audit log with a verification result.
- Web dashboards: Overview, Accuracy, department inboxes, area summaries.
- Heights hillshade PNGs and drone overlays (where rasters exist).

---

## 18. VALIDATION & TESTING

| Aspect | Actual |
|---|---|
| Input validation | Upload: safe filename, allowed extensions, 250 MB cap. API bodies: Pydantic. Change requests: field/type checks, evidence types (PDF/JPG/PNG), reason required. Passwords: length, variety, common-password and username checks. |
| Geometry validation | Validity checks and repair; editor proposals checked (valid polygon, inside/near area, overlaps). |
| CRS validation | Every layer's CRS reported with method; RMSE against GNSS control; refit when residual > 1 m; co-registration applied only if the fit improves. |
| Attribute validation | Mapping confidence; value-based checks (ID-like / name-like / area-like); reviewer confirmation. |
| Duplicate detection | Duplicate plot IDs; duplicate conflict IDs de-duplicated. |
| Conflict validation | Precision/recall per conflict type against planted errors (answer keys). |
| Output validation | Audit chain verification; benchmark history; blind tests. |
| Automated tests | **79 pytest tests pass** (57 test functions, some parametrised). They cover: core algorithms, pipeline, API, access control, workflows (maker–checker, four-eyes, stale requests), map editing, quality/learning, profile, department access, drone upload. |
| UI checks | Playwright scripts used during development (not part of the repo test suite). |

---

## 19. PERFORMANCE (measured; dev machine = one Windows 11 laptop)

| Area | Parcels | Input features (approx.) | Pipeline time | Issue detection vs answer key (P / R / F1) | Link F1 | Boundary error of fused output (mean) | Auto-finalised share |
|---|---|---|---|---|---|---|---|
| Demo (synthetic) | 240 | ~2,100 | **4.0 s** | 0.991 / 1.000 / **0.996** | 1.000 | 0.13 m (translation-only 1.66 m; municipal as delivered 0.55 m) | 80.8% |
| Blind random (synthetic) | 231 | ~2,000 | 3.5 s | 0.991 / 0.991 / **0.991** | 1.000 | 0.12 m | 80.5% |
| Jayanagar (real geometry, sim. records) | 769 | ~8,500 | **18.5–53 s** (varied with machine load) | 0.957 / 0.970 / **0.963** | 1.000 | 0.15 m (translation-only 2.35 m) | 84.1% |
| Thiruvanmiyur (real cadastral, sim. records) | 937 | ~12,000 | 26 s | 0.918 / 0.960 / **0.939** | 0.999 | 0.007 m ⚠ | 79.1% |
| Teammate blind | 240 | ~2,100 | 3.6 s | no answer key | n/a | n/a | 80.8% |

Other measured figures:
- **Georeferencing RMSE:**
  - demo 3.23 m → 0.41 m;
  - Jayanagar 4.02 m → 0.50 m (Kalianpur sheet);
  - Thiruvanmiyur 6.03 m → 0.03 m.
- **Change detection (planted 17):** 17/17 found on the demo; 1 false alarm on Jayanagar; 1 missed extension on Thiruvanmiyur.
- **Department record linking accuracy:** 99.0% demo, 99.3% Jayanagar, 99.6% Thiruvanmiyur.
- **Schema mapping accuracy:** 100% (demo, Jayanagar), 94.4% (Thiruvanmiyur).
- **Real-data finding (no answer key):** TNGIS vs NCSCM, 905 of 937 parcels paired after a 2.0 m shift; mean IoU 0.82; median offset 0.57 m; survey numbers agree on 99.3%.
- **Building agreement (real):**
  - Jayanagar: 560 of 679 Microsoft footprints confirmed by OSM (mean IoU 0.51).
  - Thiruvanmiyur: 917 of 1,686 (mean IoU 0.54).
- **Known weak rule:** "floors beyond permission" in Thiruvanmiyur, precision 0.33 (12 false alarms), from AI outlines spanning plots.

**Caveats (circular or synthetic):**
- ⚠ **Thiruvanmiyur 0.007 m boundary error is circular.** The simulated GNSS points were placed using the same imagery-derived offset the pipeline then recovers. Do not present it as real accuracy.
- ⚠ **Height MAE (0.02–0.03 m) is measured on simulated DSMs** built from the same buildings.
- **All issue-detection scores are against errors we planted in simulated records.**

**Not measured in the current MVP:** API response times (no systematic benchmark), throughput or concurrency, memory use, and performance on real government records.

---

## 20. SECURITY

| Item | Status |
|---|---|
| Authentication: username + password, PBKDF2-HMAC-SHA256 310k iterations with per-user salt; sessions = random token, only its SHA-256 stored, HttpOnly cookie, 12 h expiry; one-time passwords force a change | [IMPLEMENTED] |
| Brute force: 5 failed sign-ins per 5 min per IP → HTTP 429 | [IMPLEMENTED] |
| RBAC: viewer / officer / approver / admin; department scoping; four-eyes (never approve own request); nobody changes own access; last admin protected | [IMPLEMENTED] |
| Department need-to-know: full / summary / none matrix, admin-editable, enforced server-side on result, parcel, search, query, export, OGC, PDF | [IMPLEMENTED] |
| Privacy: personal names masked for viewers server-side | [IMPLEMENTED] |
| API security: hashed API keys with role and department (`X-API-Key`), revocable | [IMPLEMENTED] |
| Headers: nosniff, X-Frame-Options DENY, Referrer-Policy, no-store for API | [IMPLEMENTED] |
| Audit: hash-chained JSONL per area (pipeline decisions, reviews, changes, access, reports) + verify endpoint | [IMPLEMENTED] |
| HTTPS / TLS | [PLANNED] (server binds to 127.0.0.1 over HTTP) |
| Encryption at rest, HSM, key vault | [PLANNED] |
| 2FA / SSO (e.g. government identity providers) | [PLANNED] |
| CSRF: session cookie is SameSite=Strict + HttpOnly (no separate CSRF token); Content-Security-Policy header not set | [PARTIAL] |
| External anchoring of the audit head (gazette/timestamp) | [PLANNED] (described in code comments only) |

---

## 21. SCALABILITY

**Current (MVP):**
- One Python process; each area is processed in memory, in a background thread.
- Results are JSON files; SQLite holds the index and workflows.
- Shapely STRtree for spatial queries.
- The largest tested area is 937 parcels / ~12,000 features, processed in about 26 s.
- State-wide cadastral display already scales: 6 states' parcels (~3.7 GB PMTiles) are streamed by byte range, with no server-side processing.

**Evolution (all [PLANNED]):**

| Stage | Changes |
|---|---|
| City (10⁴–10⁵ parcels) | Run the pipeline per ward/tile in parallel (areas are independent); PostGIS instead of JSON + SQLite; a job queue (e.g. Celery/RQ); vector tiles for results |
| District / state | Distributed workers, object storage for rasters, Cloud-Optimised GeoTIFF, incremental runs (only changed files), retrain the matcher on real labelled pairs |
| National | Federated deployment per state (land records are a state subject), ULPIN as the join key, standard APIs (OGC / LADM) to NAKSHA and state portals, SSO |

---

## 22. MVP vs FULL SYSTEM

| Capability | Current MVP | Production / future |
|---|---|---|
| Data | Real footprints and TN cadastral; everything else simulated | Real department feeds via connectors |
| Drone / DSM | Synthetic rendered + simulated; upload display | Real ORI/DSM processing, COG tiling |
| Extraction | Classical CV + third-party AI footprints | Deep segmentation (SAM / U-Net) behind the same interface |
| GNSS | Point files | CORS/RTK ingestion, RINEX processing |
| Matching | GBM trained on synthetic wards | Retrained on reviewer-labelled real pairs |
| Storage | JSON + SQLite | PostGIS, object storage |
| Deployment | Local single process | Government cloud / state data centre, HTTPS, HA |
| Identity | Local accounts | SSO, 2FA |
| Integration | REST, OGC API, exports | NAKSHA, Bhoomi, TNGIS, municipal ERP connectors |
| ULPIN | Format-compatible generated ID | Official state-issued ULPIN |

---

## 23. REAL GOVERNMENT USE CASE

| Workflow | Demonstrated (on our data) | Potential (not built / not proven) |
|---|---|---|
| Cadastral modernisation | Georeference old sheets on GNSS, repair topology, compare two government surveys (real TNGIS vs NCSCM) | Bulk processing of state sheet archives |
| Land records (RoR) | Detect stale owners, extent mismatches, sales not mutated; maker–checker corrections | Connect to Bhoomi / TN e-services |
| Municipal planning / tax | Find built plots missing from tax, use mismatches, merged assessments; **estimated** tax at risk | Feed into municipal tax systems |
| Property verification | Department-wise plot dossier, ownership chain, PDF report | Citizen / bank / court verification service |
| Utilities | Link connections to parcels by ref + GPS; services on untaxed plots; lines under buildings | Utility GIS sync |
| GT / GNSS | Field visits settle owner conflicts; GNSS drives georeferencing and corner snapping | Field app for surveyors |
| NAKSHA (urban land survey) | Uses the NAKSHA input types (drone ORI, DSM/DTM, GNSS, GT, legacy records); ULPIN-format IDs; LADM export | Direct integration with NAKSHA outputs (not done) |

---

## 24. INNOVATION & DEMONSTRATED BENEFITS (defensible)

1. **The disagreement is the product.** Nothing is silently overwritten; each conflict shows every version with its source. Auto-resolution happens only when a *more reliable* source settles it.
2. **GNSS-driven georeferencing + layer co-registration**, measured: RMSE 3–6 m → 0.4–0.5 m.
3. **Real cross-survey check on government data:** TNGIS vs NCSCM agreement quantified automatically.
4. **Honest evaluation:**
   - hidden answer keys;
   - blind tests on random seeds and a teammate-edited kit;
   - rule-set versioning (v1/v2) with benchmark history;
   - calibration showing errors land in the review band.
5. **Multilingual record matching:** Kannada/Tamil owner names vs English municipal names (transliteration + one-to-one token matching). Local units such as guntas, cents and ankanam are handled.
6. **Governance built in:** maker–checker with four-eyes, department authority per field, department need-to-know access, hash-chained audit, admin-approved learned rules.
7. **Cross-department intelligence:** checks that need 2+ departments (sale not mutated, services without tax, floors beyond permission, commercial tariff on residential tax), with estimated revenue impact.
8. **Standards:** OGC API – Features, LADM-aligned GeoPackage, ULPIN-format keys.

**Measured benefit:** 79–84% of parcels finalised without manual GIS work on our datasets.

**Estimated (not measured):** reviewer hours saved. This is shown in the app with stated assumptions: 15 min per parcel by hand, 4 min per prepared conflict, 2 min per flagged parcel.

---

## 25. LIMITATIONS & RISKS

1. **No real government land records.** RoR, municipal, utilities, GNSS, GT and DSM are simulated; the real inputs are footprints, roads and TN cadastral only.
2. **Accuracy numbers are on planted errors.** They prove the logic, not field performance.
3. **The matcher is trained on synthetic data.** It uses only overlap features in practice; ROC-AUC 1.0 reflects an easy synthetic task.
4. **No deep learning; extraction is tested only on rendered imagery.** Uploaded real orthos are not extracted.
5. **Simulated DSM, so height and floor results are circular.** The Thiruvanmiyur boundary error is also circular.
6. **Single-process local deployment:** SQLite, no HTTPS, no load testing.
7. **Rule thresholds are hand-tuned** (e.g. 1.5 m, 0.5 IoU, 75 similarity); rules v2 was tuned on Jayanagar (blind tests mitigate this).
8. **Weak rule:** floors-beyond-permission false alarms (precision 0.33 in Thiruvanmiyur).
9. **ULPIN is format-only;** there is no NAKSHA integration.
10. **Tax-at-risk uses assumed rates.**

**Highest-risk evaluator questions (truthful answers):**
- *"Is this real data?"* Building footprints, roads and the Tamil Nadu cadastral maps are real (TNGIS and NCSCM). Land and department records are simulated because they are not openly available. We say so in the app ("sim" / "real" tags).
- *"ROC-AUC 1.0 is suspicious."* Yes: it is on synthetic training wards, and the model mostly uses overlap. We show it to prove the pipeline works end to end; retraining on real reviewer-labelled pairs is the plan. The review queue already collects those labels.
- *"Where is deep learning?"* There isn't any. We use Microsoft's AI footprints as an input and classical CV on drone + DSM. The extractor has one function interface to swap in SAM/U-Net.
- *"Your 0.007 m accuracy?"* Circular: the simulated GNSS was placed with the same offset. The meaningful real result there is 6 m → corrected, and the TNGIS–NCSCM agreement.
- *"Does it scale?"* Up to ~1,000 parcels / 12k features in under a minute on a laptop. City scale needs PostGIS and parallel ward processing; that is not built yet.
- *"What if the system is wrong?"* It never overwrites. Uncertain cases score below 85 and go to review; conflicts cap scores. Every auto-resolution states its reason and can be reopened. Record changes need an officer and a different approver.
- *"NAKSHA?"* Same input types and a ULPIN-format key, but not connected to NAKSHA systems.

---

## 26. FUTURE SCOPE (not in the current MVP)

- Real data pilots with one ULB + revenue office; connectors (Bhoomi, TN e-services, municipal ERP, DISCOM billing).
- Deep-learning building/boundary extraction from real drone ORI; COG raster tiling.
- CORS/RTK/RINEX ingestion; a surveyor field app with offline sync.
- Retrain the matcher and calibrate confidence weights on reviewer-labelled real data.
- PostGIS, job queue, parallel ward processing, containerised deployment in a state data centre; HTTPS, SSO, 2FA, encryption at rest.
- NAKSHA / official ULPIN integration; citizen verification portal.
- Fix the floors-beyond-permission false alarms (count only buildings mostly inside the plot).

---

## 27. EVALUATOR Q&A

| Question | Answer |
|---|---|
| What problem? | The same land has different, conflicting records in revenue, survey, municipal, utility, planning and registration systems. Reconciling them is manual GIS work. |
| Why this solution? | It automates the mechanical 80% (formats, CRS, alignment, topology, linking, checks) and gives humans only the real disagreements, with evidence. |
| What is actually innovative? | Evidence-ranked conflict handling (never silent overwrite), GNSS georeferencing + co-registration with measured RMSE, a real two-survey comparison, cross-department checks, maker–checker + department access + hash-chained audit, and blind-tested evaluation. |
| Where exactly is AI? | (1) A gradient-boosting classifier for parcel ↔ municipal matching. (2) Classical computer vision on drone ORI + DSM. (3) Microsoft's AI footprints as input. Everything else is algorithms and rules, and we label it that way. |
| How does spatial matching work? | Candidates within 2 m (R-tree) → 7 features (IoU, coverages, centroid distance, area ratio, Hausdorff, ID similarity) → match probability; best partner per parcel; one polygon matching 2+ parcels = merged property. Records link by normalised plot ID + GPS point-in-parcel. |
| How are CRS differences handled? | Read the declared CRS or infer it from coordinates → reproject to the local UTM zone → if a sheet is still off GNSS control (legacy datum or unknown grid), fit an affine transform on GNSS points with outlier rejection → remove whole-layer shifts by co-registration. RMSE is reported. |
| How are conflicts resolved? | 18 rule types. Auto-resolved only when a more reliable source decides (GNSS for geometry and area, field visit for owner). Otherwise an approver of the routed department picks a version; record changes go through maker–checker. |
| How is confidence calculated? | 100 × (0.30 geometry + 0.20 attributes + 0.20 source reliability + 0.15 model + 0.15 topology), capped by open conflicts. ≥ 85 approved, 70–85 flagged, < 70 review. |
| How is accuracy validated? | Hidden answer keys with planted errors → per-type precision/recall; boundary error vs truth; calibration by score band; blind tests on random seeds and a teammate-edited kit; benchmark history per rule version. |
| What if AI is wrong? | Low confidence goes to review; conflicts cap scores; every automatic decision is reasoned, audited and reversible; nothing changes official records without a human approver. |
| How does human verification work? | Review queue (side-by-side versions, bulk decisions, undo), schema-mapping confirmation, maker–checker with the four-eyes rule, learned rules only after admin approval. |
| Can it handle large datasets? | Tested up to 937 parcels / ~12k features in ~26 s on a laptop; state-wide parcels are displayed via PMTiles. City/state processing needs PostGIS and parallel workers (planned). |
| Drone / GNSS / CORS? | Drone ORI + DSM extraction works on synthetic data; real orthomosaic GeoTIFFs can be uploaded and displayed. GNSS points drive georeferencing and snapping. No CORS streaming yet. |
| How does it support NAKSHA? | It handles NAKSHA's input types and produces ULPIN-format IDs and LADM exports. Not integrated with NAKSHA systems. |
| Different from QGIS/ArcGIS? | Those are manual toolboxes. This is an automated, repeatable pipeline with department-specific rules, confidence, review workflow, record-update governance and audit. No GIS skills needed to use it. |
| What is currently working? | Everything tagged [IMPLEMENTED]: 11-step pipeline, web app, review, maker–checker, editor, search/Q&A, exports, APIs, RBAC, audit. 79 automated tests pass. |
| Biggest limitation? | No real government land records to validate on; accuracy is proven on simulated records over real geography. |
| How would departments integrate it? | Drop exported files into an area today, or use API keys + REST/OGC for systems; connectors per department are next. |
| How would it scale? | Areas are independent, so run wards in parallel; move to PostGIS and a job queue; deploy per state. |

---

## 28. PRESENTATION SOURCE MATERIAL (10 slides)

**Slide 1: Land Connect**
- One record of land from many departments, with the disagreements made visible.
- SIH 2026 · PS 26013 · MoRD / DoLR · Smart Automation.
- *Visual:* app screenshot (map + plot card).
- *Notes:* "We harmonise the land records that different departments already keep."

**Slide 2: Problem**
- 6–9 departments keep different versions of the same land.
- Different IDs, units, languages, coordinate systems; 1–6 m offsets.
- Stale owners, unmutated sales, untaxed buildings.
- Manual GIS reconciliation, parcel by parcel.
- *Visual:* the same plot in 3 records that disagree.
- *Notes:* use the Jayanagar example: the Kannada RoR name vs the English tax name.

**Slide 3: Solution**
- Automated 11-step harmonisation pipeline.
- Confidence score per parcel; auto-finalise ≥ 85.
- Conflicts shown with evidence; auto-resolved only by a more reliable source.
- Human review + maker–checker record updates.
- *Visual:* before/after (raw layers vs harmonised parcels).
- *Notes:* "Nothing is silently overwritten."

**Slide 4: Architecture**
- Browser SPA (Leaflet) → FastAPI → pipeline (Shapely, pyproj, GDAL, scikit-learn, OpenCV) → SQLite + JSON + audit.
- REST + OGC API – Features + exports.
- *Visual:* the diagram in §6.
- *Notes:* runs locally; all open-source.

**Slide 5: Workflow**
- Upload → run → overview → review → propose / approve → export / report.
- Roles: viewer, officer, approver, admin; 9 departments.
- *Visual:* workflow strip + role icons.
- *Notes:* show the four-eyes rule.

**Slide 6: AI/ML + GIS**
- GBM matcher (7 geometric/ID features).
- Classical CV buildings from ORI + DSM; Microsoft AI footprints fused with OSM.
- GNSS affine georeferencing (RMSE 3–6 m → 0.4–0.5 m); co-registration; topology repair.
- Change detection (new / demolished / extension / floor).
- *Visual:* georeferencing before/after, extraction overlay.
- *Notes:* be precise about which parts are ML and which are CV or rules.

**Slide 7: Features + innovation**
- 18 conflict types incl. cross-department (sale not mutated, services without tax, floors beyond permission).
- Multilingual owner matching; local units.
- Department-wise dossier + need-to-know access; hash-chained audit.
- ULPIN-format IDs, LADM export, OGC API.
- *Visual:* plot card with department tabs.

**Slide 8: Results + validation**
- Issue detection F1: 0.996 (synthetic), 0.991 (blind), 0.963 (Jayanagar), 0.939 (Thiruvanmiyur).
- Boundary error 0.13–0.15 m vs 1.7–2.4 m translation-only.
- Real TNGIS vs NCSCM: 82% mean overlap, 0.57 m median offset.
- 79–84% of parcels finalised without manual work.
- *Visual:* Accuracy tab screenshot.
- *Notes:* **state clearly that the records are simulated on real geography and the scores are against planted errors.**

**Slide 9: Scale + government use**
- Tested up to ~1,000 parcels in under a minute; 6-state cadastral display via PMTiles.
- Path: PostGIS + parallel wards → state deployment.
- Uses: revenue, survey, ULB tax, town planning, IGR, utilities.
- *Visual:* state-wide map screenshot.

**Slide 10: Future + conclusion**
- Real-data pilot; deep extraction; CORS; NAKSHA/ULPIN integration; retraining on reviewer labels.
- *Takeaway:* one trustworthy, auditable land record.
- *Notes:* end on the limitation plus the plan.

---

## 29. PITCHES

**30 s.** Every Indian city keeps several different records of the same land, and they disagree. Land Connect reads each department's files as they are, aligns them on GNSS control, links them with an ML matcher and scores every parcel. It finalises about 80% of parcels automatically and sends only the real disagreements to the right department, with the evidence side by side. Nothing changes without a human approver, and every step is on a tamper-evident audit trail.

**1 min.** Revenue, survey, municipal tax, planning, registration and utilities each hold their own version of a parcel: different IDs, units, languages, coordinate systems, and boundaries up to 6 m apart. Land Connect runs an 11-step pipeline:
- reads any format and maps each department's columns to one schema;
- georeferences old sheets on GNSS points (errors of 3–6 m drop to about half a metre);
- repairs topology and links records with a gradient-boosting matcher;
- detects 18 kinds of conflicts, such as stale owners, unmutated sales, untaxed buildings and floors beyond permission;
- scores each parcel.

On our test areas (real building and Tamil Nadu cadastral geometry, simulated records) it finds planted problems with F1 between 0.94 and 0.99 and finalises 79–84% of parcels without manual work. The rest go to department approvers through maker–checker workflows with full audit.

**3 min.** Use the 1-minute version, then add:
- **Trust design:** auto-resolution only by a more reliable source; conflicts cap the score; calibration shows errors land in the review band.
- **Real-data evidence:** TNGIS vs NCSCM compared automatically, with 82% overlap and a 0.57 m median offset after correcting a 2 m shift.
- **Governance:** four-eyes approval, department authority per field, need-to-know access matrix, hash chain.
- **Interoperability:** OGC API, LADM, ULPIN-format.
- **Honesty and roadmap:** simulated records; the ML is one matcher; the drone extraction is classical CV. Next: a real pilot, deep extraction, PostGIS scale-out and NAKSHA integration.

---

## 30. FINAL FACT SHEET

- **Project:** Land Connect
- **PS ID:** SIH 2026 PS 26013 (MoRD / DoLR; code says "PS 013")
- **Problem:** Conflicting, unaligned land records across departments; manual GIS reconciliation
- **Solution:** Automated harmonisation pipeline + governed review/update web app
- **Frontend:** Vanilla JS SPA, Leaflet, protomaps-leaflet (PMTiles), service worker
- **Backend:** Python, FastAPI, Uvicorn
- **Database:** SQLite (10 tables) + JSON results + JSONL hash-chained audit
- **GIS:** Shapely 2, pyproj, pyogrio/GDAL, GeoPandas
- **AI/ML:** scikit-learn GradientBoostingClassifier (matching, trained on synthetic wards); OpenCV classical CV (buildings); Microsoft ML footprints as input; optional Claude query parser (off by default)
- **Algorithms:** CRS inference; GNSS affine + ICP-style refinement with outlier rejection; co-registration (median displacement, containment grid search); topology repair (snap/merge/overlap/gap/sliver); STRtree joins; IoU/Hausdorff features; nDSM + ExG extraction; epoch/DSM change detection; fuzzy + transliterated name matching; weighted confidence with conflict caps
- **Input datasets:** Real OSM + Microsoft footprints, OSM roads/landmarks, TNGIS + NCSCM cadastral; simulated RoR, municipal tax, GNSS, GT, utilities, permissions, deeds, DSM/DTM; synthetic drone ORI
- **Processing:** 11 steps: ingest → schema → CRS → extract → topology → match → change → conflict → confidence → evaluate → publish
- **Outputs:** Harmonised parcels with scores, conflicts, changes, georef/co-registration reports, accuracy reports, exports (GeoJSON/CSV/KML/GPKG/SHP/LADM), PDF reports, audit log
- **Main features:** Review queue, maker–checker, map editor, department-wise views + access matrix, search & questions, OGC API, state-wide cadastral maps, blind tests
- **APIs:** 64 REST/OGC endpoints (session cookie or API key)
- **Security:** PBKDF2 passwords, hashed sessions/keys, RBAC + department access, masking, rate limit, security headers, hash-chained audit; no HTTPS/2FA yet
- **Deployment:** Local single process (`run.bat` / `run.sh`, 127.0.0.1:8000)
- **Current status:** Working MVP; 79 automated tests pass; 5 test areas
- **Major limitation:** No real government land records; ML trained on synthetic data; no deep learning; local deployment only
- **Innovation:** Evidence-ranked conflicts, never silent overwrite; measured GNSS georeferencing; real two-survey comparison; cross-department checks; governance + audit; blind-tested evaluation
- **Future scope:** Real pilot, DL extraction, CORS, PostGIS scale-out, NAKSHA/ULPIN integration, SSO/HTTPS
- **Demo flow:**
  1. Sign in as admin.
  2. Open Jayanagar → Overview.
  3. Click the "Sale not mutated" chip → open a plot → department tabs.
  4. Review tab → decide a conflict.
  5. Sign in as `revenue.officer` → propose a change; sign in as `revenue.approver` → approve it.
  6. Accuracy tab → Thiruvanmiyur TNGIS vs NCSCM.
  7. Export the LADM GeoPackage.
