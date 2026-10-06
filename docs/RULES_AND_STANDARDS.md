# Land Connect: rules and standards

This document lists every standard the prototype follows and every rule it applies, with the exact thresholds used in the code. It is written so that a land-records officer, a judge or a developer can check why the system took any decision. Each rule names the file that implements it.

**What "aligned" means here.** The prototype *uses* the standards listed in Part A. It has **not** been certified against any of them. The entries under "Alignment" say what is implemented and what is not.

---

## Part A. Standards

### A1. Geometry, formats and exchange

| Standard | How Land Connect uses it | Where |
|---|---|---|
| **OGC Simple Features / ISO 19125-1** | All geometry is Simple Features, handled by GEOS through Shapely. Polygon validity (no self-intersection, closed rings) follows its rules, and invalid shapes are repaired with `make_valid`. | `topology.py` |
| **GeoJSON, IETF RFC 7946** | Every published layer and API response is GeoJSON in WGS 84 longitude/latitude (RFC 7946 §4). Incoming GeoJSON with no `crs` member is treated as WGS 84, as the RFC requires. | `publish.py`, `crs_engine.py` |
| **OGC API – Features, Part 1: Core** | Landing page, conformance, collections, and items with `bbox`, `limit` and `offset` at `/ogc`. It declares the *core* and *geojson* conformance classes. It is a subset: no CQL filtering, and only CRS84. | `api.py` |
| **OGC GeoPackage 1.x** | Export of any layer, and of the LADM package. | `export.py` |
| **OGC KML 2.2** | Ingestion (municipal layer) and export. | `ingest.py`, `export.py` |
| **ESRI Shapefile** (with `.prj`) | Ingestion, including the CRS from the `.prj`, and zipped export with 10-character field names. | `ingest.py`, `export.py` |
| **GPX 1.1** | Ingestion of field-verification waypoints. | `ingest.py` |
| **CSV + WKT** | Ingestion of tables with lat/lon columns, and export with geometry as WKT. | `ingest.py`, `export.py` |
| **OpenAPI 3** | REST API description at `/docs`. | `api.py` |

### A2. Coordinate reference systems

| Standard | Use |
|---|---|
| **EPSG Geodetic Parameter Registry** | Every CRS is identified by an EPSG code or by the WKT in the file. |
| **PROJ (through pyproj)** | All transformations between CRSs. |
| **EPSG:4326, WGS 84** | Exchange and publication CRS. |
| **EPSG:326xx, WGS 84 / UTM** | Working CRS for measurement (metres). The zone is chosen automatically from the data location, for example 43N for Bengaluru and Mangaluru, and 44N for Madurai. |
| **EPSG:3857, Web Mercator** | Accepted as input (utility layer). |
| **EPSG:24383, Kalianpur 1975 / India zone IVa** | Legacy Indian survey CRS, accepted as input. The residual datum shift is removed with GNSS control (rule B4). |

### A3. Land administration

| Standard | Alignment |
|---|---|
| **ISO 19152 Land Administration Domain Model (LADM)** | `/api/export/ladm.gpkg` writes the core LADM classes: **LA_SpatialUnit** (parcel geometry, area, land use, quality score), **LA_BAUnit** (khata / property unit), **LA_Party** (owner), **LA_RRR** (an ownership right linking a party to a BA unit, marked *disputed* while a conflict is open) and **LA_Source** (input files with SHA-256). This is a simplified profile: no LA_Level, no 3D, no code lists. |
| **ULPIN / Bhu-Aadhaar** (14-character parcel ID, DoLR) | Not generated. The official algorithm and registry are held by the states. The parcel record has room for a ULPIN attribute, and the plot ID normaliser already handles survey/hissa formats. This is future integration. |
| **NAKSHA programme, DILRMP** | The inputs mirror NAKSHA outputs (drone ORI, DSM/DTM, GT, GNSS/CORS, feature extraction). Output is designed to feed cadastral finalisation. No official NAKSHA data schema was available to check against. |

### A4. Data quality

The accuracy report is organised along the data-quality elements of **ISO 19157**:

| ISO 19157 element | Measure in Land Connect |
|---|---|
| Positional accuracy (absolute) | Mean and 95th-percentile boundary offset from the truth, per processing stage. Control-point RMSE of georeferencing. |
| Completeness (omission / commission) | Parcels with no revenue record, records with no parcel, built plots missing from the tax register. |
| Logical consistency (topological) | Overlaps and invalid shapes remaining after correction (target 0 / 0). |
| Thematic accuracy (classification correctness) | Matching precision and recall, building-extraction precision and recall, attribute-mapping accuracy. |
| Temporal quality | Change detection between two survey dates. |

**ISO 19115 metadata** is not generated. Provenance is instead recorded per file (format, CRS, SHA-256) in the result and in the audit trail.

### A5. Integrity, text and imagery

| Standard / terms | Use |
|---|---|
| **SHA-256 (FIPS 180-4)** | Hash of every input file, and the hash chain of the audit trail. |
| **Unicode** | Owner names in Kannada, Tamil, Telugu, Devanagari, Malayalam, Bengali, Gujarati, Gurmukhi and Odia are read as-is. |
| **ITRANS romanisation** (`indic-transliteration`) | Used only to *compare* names across scripts. Stored names are never changed. It is not an ISO 15919 transliteration. |
| **Open Database Licence (ODbL 1.0)** | OpenStreetMap and Microsoft Global ML Building Footprints, credited in the dashboard. |
| **Esri World Imagery terms** | Satellite basemap, **display only**, with attribution. |
| **Google Map Tiles API terms** | Optional Google satellite basemap through the official API with the user's own key, **display only**. Google imagery is never downloaded, cached for analysis or used for feature extraction. |

### A6. Indian policy context

- **National Geospatial Policy 2022** and the **Guidelines for acquiring and producing geospatial data (DST, 2021)** liberalised the use of geospatial data in India. The prototype uses only open data and simulated land records.
- **NDSAP (National Data Sharing and Accessibility Policy)**: the OGC API and open exports are how departments share harmonised layers.
- **Personal data**: owner names are personal data under the **DPDP Act 2023**. The prototype runs locally, and access control, role-based review and consent handling are future scope.

---

## Part B. Rules

### B1. Ingestion (`ingest.py`)

1. Every file in the workspace's `raw/` folder is read. GDAL/OGR formats (Shapefile, KML, GPX, GeoPackage, GeoJSON-lines, GML, FlatGeobuf, MapInfo, DXF) are read through pyogrio, which takes the CRS from the file.
2. A CSV becomes points when it has numeric `lat`/`latitude` and `lon`/`lng`/`longitude` columns within valid ranges.
3. Each file is assigned a **role** by scoring keywords in its name and columns: cadastral, revenue, municipal, AI buildings, footprints, utility, GNSS, ground truth, roads or landmarks. Geometry type adds evidence: lines suggest utility or roads, points suggest GNSS or ground truth, and a CSV without geometry suggests revenue records.
4. KML housekeeping fields (`id`, `tessellate`, `extrude`, `visibility`, `description` …) are ignored.
5. Every input file's **SHA-256** is recorded.

### B2. Attribute mapping (`schema_mapper.py`)

1. Canonical fields: plot ID, sub-division (hissa), owner name, area, land use, record ID.
2. **Name evidence**: the column's words contain a synonym as whole words (100, or 90 for a one-word synonym inside a long name). Otherwise fuzzy similarity to the synonyms is used.
3. **Value evidence**: the share of values that look like the field. Examples: `45/2A` for a plot, two or more alphabetic words for a name (after transliteration), positive numbers for an area.
4. Confidence = **0.6 × name + 0.4 × values**. A mapping is accepted at **≥ 60**.
5. A column whose name says nothing is still mapped to **owner** or **plot ID** when **≥ 85 %** of its values fit (confidence 65 × share). Example: `OCCUPANT`.
6. Columns containing *father, husband, guardian, mother, spouse or relation* are **never** mapped to owner.
7. Each field takes one column and each column one field, assigned greedily by confidence.
8. Area units are detected from the column name and converted to m²:

| Unit | m² |
|---|---|
| sq ft | 0.092903 |
| sq yd / gaj | 0.836127 |
| gunta (Karnataka, Telangana) | 101.17141 |
| cent (Tamil Nadu, Kerala) | 40.468564 |
| ankanam (Andhra Pradesh) | 6.689 |
| acre | 4046.856 |
| hectare | 10 000 |

9. **Plot ID normalisation**: words (Sy, No, Plot, Khasra, Hissa, KH…) are dropped, and leading zeros removed. The survey number and the first sub-division level are kept, including a single-letter suffix: `KH-045/2`, `Sy.No. 45 Hissa 2A` and `45-2a` become `45/2`, `45/2A` and `45/2A`.
10. **Plot ID similarity**:

| Relationship | Score |
|---|---|
| Same ID | 1.0 |
| Same hissa, different letter (`3` vs `3A`) | 0.8 |
| Parent and child (`45` vs `45/A`) | 0.7 |
| Same survey number, different hissa | 0.3 |
| Otherwise | 0.5 × fuzzy similarity |

### B3. Owner-name comparison (`schema_mapper.py`)

1. Indic scripts are transliterated to Latin (ITRANS). An anusvara before a consonant becomes *n*.
2. Text is upper-cased and punctuation removed. Honorifics are dropped: Sri, Shri, Smt, Kum, Dr, Mr, Mrs, Ms, Late.
3. Phonetic folding: OO→U, EE→I, TH→T, SH→S, DH→D, BH→B, KH→K, OW→AU, W→V, Z→J. Doubled letters are collapsed, a final *AV* becomes *AO* (Rao), and the trailing inherent *A* is dropped (Manjunatha ≈ Manjunath).
4. Trailing initials (a father's or village initial) are ignored when both names have a first and last name.
5. Every word of the shorter name must match a word of the longer one. Score = **0.7 × worst word + 0.3 × mean word**.
6. A match that relies on an initial ("R. Kumar") is capped at **85**, because an initial is weaker evidence.
7. Two names refer to **different people below 75**.

### B4. Coordinate transformation (`crs_engine.py`)

1. **Detection order**:
   - an EPSG code or WKT declared in the file (`.prj`, GeoJSON `crs`, KML)
   - WGS 84 if every coordinate is within ±180 / ±90
   - UTM if x is 160 000–840 000 m and y is 0–9 400 000 m
   - Web Mercator for larger x
   - otherwise an **unknown local grid**
2. **Working CRS**: the WGS 84 / UTM zone containing the data.
3. **Unknown local grid** (for example an old cadastral sheet):
   - A coarse affine fit is made between GNSS-surveyed parcel centres and the same parcels on the sheet.
   - It is refined over up to 6 iterations: each GNSS point is paired with the nearest vertex of its plot (within 4 m).
   - Pairs off by more than **max(3 × median, 0.5 m)** are rejected, and the 6-parameter affine is refitted by least squares.
4. **Declared legacy CRS** (for example Kalianpur 1975): after reprojection, the misfit against GNSS control is measured. If the RMSE exceeds **1.0 m**, the same affine refinement removes the residual datum or sheet-registration shift.
5. Rotation, scale, shear and RMSE before and after correction are reported and written to the audit trail.

### B5. Layer co-registration (`coreg.py`)

1. **Municipal layer**: the median displacement between matched parcels is measured, using only links with probability > 0.9 and excluding merged properties, with at least 10 pairs. The layer is corrected if the shift exceeds **0.3 m**, then matched again.
2. **Building layers**:
   - Each layer is shifted onto the parcel fabric at the position where building outlines best fall inside a single plot.
   - The search is a grid of 1 m, then 0.25 m, then 0.1 m, within ±4 m, using at most 400 buildings.
   - The shift is applied only if it exceeds **0.3 m** and the fit improves by more than 0.01.
3. The layer published "as received" is always the uncorrected original.

### B6. Topology correction (`topology.py`)

The steps run in this order:

1. **Invalid polygons**:
   - Plots with 4–5 corners are re-ordered around their centroid, which fixes a swapped-vertex bow-tie. The result is kept if it covers more than 90 % of its convex hull.
   - Otherwise OGC `make_valid` is used, keeping the largest part.
2. **Duplicates**: the same plot ID digitised twice. The first copy is kept.
3. **GNSS snapping**: a vertex moves to a GNSS point within **2.5 m** on the same plot, or within **0.8 m** on a neighbouring plot.
4. **Shared corners**: vertices of different parcels within **1.2 m** are merged into one corner (the GNSS position if there is one, otherwise their mean). Two rules prevent damage:
   - At most one vertex per parcel, and at most 6 vertices, can be in one cluster, and no vertex moves further than 1.2 m.
   - A move that would shrink a parcel below 90 % of its area is rejected.
5. **Overlaps** above 0.01 m² go to the parcel with more GNSS corners, then to the larger one. The loser is clipped.
6. **Gaps** found by morphological closing (1.5 m) that are thin (area / perimeter < 0.6) and at most **25 m²** go to the neighbour with the longest shared edge.
7. **Slivers**: after clipping, only the largest part of a parcel is kept.
8. **Topology score T**:

| Area change | T |
|---|---|
| < 1 % | 1.0 |
| < 3 % | 0.9 |
| larger | 0.75 |

   T is capped at 0.7 after a self-intersection repair, and at 0.85 for a duplicate.

The target after correction is **0 overlaps and 0 invalid shapes**.

### B7. Matching and fusion (`matcher.py`, `pipeline.py`)

1. **Candidates**: every municipal polygon within 2 m of a cadastral parcel.
2. **Features**: IoU, cover of each polygon by the other, centroid distance, area ratio, Hausdorff distance and plot-ID similarity (B2.10).
3. **Model**: gradient-boosted trees (150 trees, depth 3), trained on six separate synthetic wards whose municipal layers have 0.3–2 m vertex noise and shifts of up to 3 m. It is never trained on the ward being evaluated.
4. A pair is a **match at probability ≥ 0.5**. Each parcel keeps its best match, and a municipal polygon matched to two or more parcels is a **merged property**.
5. Revenue records and field visits link by normalised plot ID.
6. **Buildings**:
   - The primary layer is either the drone extraction or, when there is no imagery, the AI footprint layer.
   - A second layer **confirms** a building at IoU ≥ 0.3.
   - When two or more reference buildings each lie at least 60 % inside one AI footprint, the AI footprint is **split** into those buildings. AI models merge attached row houses.
   - Buildings that appear only in the reference layer are added with lower confidence.

### B8. Building extraction from drone data (`extractor.py`)

1. nDSM = DSM − DTM. A pixel is **elevated** above **2.5 m**.
2. Vegetation is removed with the excess-green index (2G − R − B) / (R + G + B) > **0.06**, smoothed with σ = 1 px.
3. The mask is cleaned with morphological opening (5 × 5 px) and closing (3 × 3 px). Buildings smaller than **12 m²** are dropped.
4. The outline is simplified (Douglas–Peucker, 1.5 px). It is replaced by its minimum rotated rectangle only when the trace is noisy (more than 6 vertices) and the building is rectangular (> 0.9).
5. Height = median nDSM inside the outline. Floors = round((height − 0.5) / 3.2).

### B9. Change detection (`change.py`)

| Condition | Result |
|---|---|
| IoU ≥ 0.5 between dates | Same building |
| Same building, footprint area ratio > **1.12** | **Extension** |
| Same building, height gain > **2.24 m** (0.7 floor) | **Floor added** |
| No building at IoU ≥ 0.2 at the earlier date | **New** |
| An earlier building with no later match | **Demolished** |

Change detection needs two survey dates. With one date, it reports that none was possible.

### B10. Source reliability hierarchy (`config.py`, `conflicts.py`)

- **Geometry**: GNSS / CORS (1.00) > ground-truth survey (0.95) > drone AI extraction (0.80) > municipal layer (0.65) > digitised cadastral sheet (0.60).
- **Ownership**: field verification (GT) > revenue record (RoR) > municipal register. When the RoR and municipal register disagree and there is no GT, the owner is **disputed** and goes to review. Nothing is chosen silently.

### B11. Conflicts (`conflicts.py`)

| Conflict | Raised when | Severity | Auto-resolved when |
|---|---|---|---|
| Owner mismatch | RoR vs municipal owner similarity < 75. With no municipal record, RoR vs field visit. The second plot of a merged property is skipped. | High | A field visit matches one version (≥ 75): that version wins and the other record is marked stale. |
| Area mismatch | \|RoR extent − surveyed area\| / surveyed > **tolerance = max(10 %, 1.5 × 0.3 m × perimeter / area)** | Medium | The boundary has at least 3 GNSS corners: the RoR extent is to be corrected. |
| Boundary disagreement | Mean boundary offset to the municipal polygon > **1.5 m** (after co-registration), or IoU < 0.5 | Medium | The boundary has at least 3 GNSS corners. |
| Merged property | One municipal polygon matches two or more parcels | Medium | Never |
| No revenue record | Parcel without an RoR row | High | Never |
| Record without parcel | RoR row whose plot is not on the map | High | Never |
| Missing in municipal | Built parcel with no tax record | Medium | Never |
| Encroachment | A building extends more than **4 m²** and deeper than the source's positional tolerance (**0.5 m** drone, **1.0 m** AI/OSM footprints) beyond its plot. When two building layers exist, **both** must show it. High if confirmed by both layers; medium if only one layer has the building. | High or medium | Never |
| Building spans plots | Largest share in one plot < 75 % and second share ≥ 20 % | Medium | Never |
| Utility under building | A utility line runs more than 0.5 m under a building | Medium | Never |
| Unassessed construction | New building on land assessed as vacant or with no tax record | Medium | Never |
| Self-intersection, duplicate | Found by topology | Low | Always (repaired) |

### B12. Confidence score (`confidence.py`)

**C = 100 × (0.30 G + 0.20 A + 0.20 S + 0.15 M + 0.15 T)**

| Component | Meaning | How it is computed |
|---|---|---|
| **G** | Geometric agreement | max(IoU / 0.85, 1 − (mean offset − 0.5 m) / 2 m). It is at least 0.97 with 3 or more GNSS corners, 0.7 for a merged property, and 0.55 with no municipal match. |
| **A** | Attribute agreement | Mean of: linked to an RoR row (1), owner similarity / 100, area agreement, and field-verified (1). It is 0.2 with no RoR. |
| **S** | Source reliability | 1.0 with 3 or more GNSS corners, 0.8 with 1–2, and 0.6 otherwise. Add 0.1 for a field visit and 0.1 for municipal corroboration (IoU > 0.8), up to a maximum of 1. |
| **M** | Model confidence | The matcher's probability, or 0.5 with no match. |
| **T** | Topology validity | From B6.8. |

**Caps**: an open *high* conflict caps the score at **69**, and an open *medium* conflict caps it at **84**. A disputed parcel can therefore never be auto-approved.

**Status**:

| Score | Status |
|---|---|
| ≥ 85 | Auto-approved |
| 70–84 | Approved with a flag |
| < 70 | Human review |

A parcel whose conflicts were all decided by a reviewer is **verified** (score ≥ 95).

### B13. Review and audit (`review.py`, `audit.py`)

1. Nothing is overwritten silently. Every conflict keeps all versions side by side until it is auto-resolved by a higher-ranked source or decided by a reviewer.
2. Reviewer decisions are stored per area, re-applied on every run, and can be undone. Undoing is logged too.
3. Every run appends to a **hash-chained audit trail**. It records:
   - input files and their SHA-256
   - the CRS detected per file
   - georeferencing and co-registration parameters
   - topology fixes per parcel
   - every conflict, and every auto-resolution with the winning source
   - every parcel's final score, status, owner and links
   - every reviewer decision
4. Each entry stores the SHA-256 of the previous entry, so `python -m landconnect.audit <area>` detects any edit or deletion and names the first broken entry.

### B14. Natural-language query (`query.py`)

1. The offline rule parser is the default. It needs no internet and gives the same answer every time.
2. Claude (`claude-opus-5`, structured output) is used only when an Anthropic credential is configured. Any failure or refusal falls back to the offline parser.
3. The interpreted filter is always shown to the user, so no answer comes from an unexplained step.
4. Areas in the question are converted to m² using the units in B2.8 (including cents, grounds and hectares). Money is read in rupees, with lakh and crore. Places are resolved against OSM landmarks and roads by fuzzy name match, within 200 m by default.
5. **What a question can ask:**
   - every conflict type, and the departments a conflict is routed to
   - changes between surveys, tax at risk, broken ownership chains and plots added by survey
   - "without conflicts", "between X and Y", and ULPIN
   - "how many", "total area", "total tax at risk" and "average score", which are answered in one line above the list
6. A question that sets no condition (for example just a place name) returns no plots rather than all of them.

### B14b. The search bar (`search.py`, `web/app.js`)

1. **As you type** (after 110 ms, 6–15 ms on the server), suggestions are grouped:
   - plots: survey no., khata, property ID, ULPIN
   - department record numbers and change requests
   - owners (any script; never for viewer accounts)
   - buildings, places, other areas and coordinates
   - ready-made questions, each with its live count

   ↑/↓ choose, Enter goes, Esc closes; "/" or Ctrl+K focuses the bar from anywhere.
2. **Ranking:** exact matches first, then a survey number's own subdivisions, then prefix matches, then word matches. Fuzzy matching is used only for names and places.
3. **Places outside the area** are looked up in OpenStreetMap Nominatim (India only):
   - only when Enter is pressed and nothing in the area matched, never while typing
   - at most once a second, with results cached
   - only the typed name is sent

   If the place lies in a state with a state-wide cadastral map, that map is switched on.
4. **Results:** a one-line answer, totals (count, area, tax at risk, average score), sorting, CSV and GeoJSON download of the matches, and a shareable link (`#q=`). The last 8 searches are kept in the browser.

### B15. Evaluation honesty (`evaluate.py`)

1. The hidden truth is used only for scoring and is never read by the pipeline.
2. The matcher is trained on different wards from the one evaluated.
3. Planted problems that no system could detect are excluded from recall and disclosed. An example is a stale owner with no second source.
4. On the real-world area, the two building layers have no truth. They are reported as **cross-source agreement**, not accuracy.
5. The encroachment truth for the real-world area is derived from the final geometry, not only from the planted list.

### B16. Departments and cross-department checks (`departments.py`)

**Which department is the authority for what.** Only that department's officers can propose a change:

| Department | Authority for |
|---|---|
| Revenue (RTC/RoR) | Owner, recorded extent, land classification |
| Survey & Land Records | Parcel boundary |
| Municipal (property tax) | Property ID, assessed use |
| Town Planning | Permitted floors |
| Stamps & Registration (IGR) | Buyer on the latest registered deed |
| Electricity | Tariff, account holder |
| Water & Sewerage | Connection type, account holder |
| City gas | Account holder |
| Roads | Encroachment notice number |

**Linking department records to parcels.** Each record is linked by its plot reference and by its GPS point:
- Clerk-style references such as `SY.101 H.3` are normalised: single-letter abbreviations are dropped.
- If the reference and the GPS point disagree, a GPS point that falls **inside** a parcel wins, because clerks mistype survey/hissa numbers far more often than a meter moves.
- A point outside every parcel (for example, on the road edge) defers to the reference.
- Every link records the method used.

**Cross-department checks:**

| Check | Raised when | Severity | Routed to |
|---|---|---|---|
| Mutation pending | The latest registered deed's buyer differs from the RoR owner (similarity < 75) | High | Revenue, Registration. The system also drafts a change request for the Revenue officer. |
| Owner dispute settled by deed | A registered deed matches one version of an open RoR-vs-municipal owner conflict | Auto-resolved | The deed outranks both registers |
| Tax use mismatch | Assessed residential, but a live electricity tariff is LT-3/LT-5/HT or a water connection is non-domestic | Medium | Municipal, Electricity, Water |
| Services without tax | Live electricity, water or gas connection on a property with no tax record, or one assessed as vacant | Medium, or adds evidence to an existing missing-in-municipal conflict and raises it to high | Municipal |
| Floors beyond permission | Floors seen in the DSM are more than the permitted floors | High | Town Planning, Municipal |
| Utility under building | Sewer, water, gas or power line more than 0.5 m under a building | Medium | The owning utility, Municipal |

**Corroboration in owner disputes.** Other departments' account holders (electricity, water, gas) are added to the conflict as supporting evidence. They are never used to decide it.

**Review routing.** An approver can decide only the conflicts routed to their department. State-level ("all") approvers can decide any.

### B17. Secure updates to land records (`records.py`)

1. **Roles.** Viewer < **officer** (maker) < **reviewer / approver** (checker) < admin. Every user and API key belongs to one department or to "all".
2. **Proposing.** An officer proposes a change to a field their department is the authority for:
   - A reason of at least 10 characters is required.
   - Owner, extent, classification, boundary, permitted floors, deed and notice changes need a **supporting document**: a PDF, JPG or PNG of at most 10 MB. The file's content must match its type, and its **SHA-256** is recorded.
   - The value is validated: text length, numeric range, choice lists, and a valid WGS 84 polygon for boundaries.
   - Only one request per field and plot can be pending at a time.
3. **Approving.** An approver of the **same department** decides. The **proposer can never approve their own request** (the four-eyes rule). Rejecting requires a written reason.
4. **Stale check.** At approval, the current value must still equal the value when the request was made. Otherwise the request becomes **stale** and must be proposed again, so no update is lost.
5. **What approval does:**
   - writes the value into the harmonised record, with the request number, proposer, approver and time
   - closes only the conflicts that this change settles
   - re-scores the parcel
   - re-applies the change on every future pipeline run
6. **Notifications.** Every department that must follow the change is notified in its inbox. For example, an owner change notifies municipal tax, water, electricity and gas.
7. **Nothing is deleted.** Rejected, withdrawn and stale requests are kept. Proposals, approvals, rejections, withdrawals and stale events all go into the hash-chained audit trail with the user's name.
8. **Drafts from the pipeline.** The pipeline can draft requests from evidence, such as a registered sale that the RoR has not caught up with. Those drafts still need a human approver.

### B18. Who grants which role (`access.py`)

| Role | Granted, changed and removed by |
|---|---|
| Viewer | State admin (any department) · department approver (own department only) |
| Officer (maker) | State admin (any department) · department approver (own department only) |
| Approver / reviewer (checker) | State admin only |
| Admin | State admin only |

1. **No self-service.** Nobody can change, disable or reset their own account through access management; the next level up does it.
2. **Admin safeguard.** The last active admin cannot be demoted or disabled.
3. **Department view.** A department approver sees and manages only the accounts of their own department, and only viewers and officers among them.
4. **One-time passwords.** New accounts and password resets get a one-time password, shown once to the person who issued it. Until the user sets their own password (at least 10 characters), every endpoint except "who am I" and "change password" refuses them.
5. **Immediate effect.** A role change ends the user's sessions, so the new rights apply at their next sign-in.
6. **Access log.** Every grant, role change, disable/enable, reset and password change is written to the hash-chained access log (`output/access_audit.jsonl`), with the name of the person who did it and when. The accounts list also shows who granted each role and when.

### B19. Property report (`report.py`)

1. **Who can download it.** Approvers (department or state) and admins only. Viewers and officers cannot.
2. **What it contains.** The PDF brings together, for one plot:
   - key facts
   - a vector boundary map with neighbours, buildings and service connections
   - the confidence breakdown
   - the records of every government body: Revenue, Survey (including boundary coordinates), Municipal, Town Planning, Registration, Water, Electricity, Gas, Roads, field verification and buildings
   - conflicts with their resolutions
   - official updates and change requests
   - an extract of the audit trail with the chain status
3. **Traceability.** Each report has a unique ID. Its SHA-256, size, the requesting user and the time are written to the area's audit trail, so a printed copy can be checked against the log.
4. **Indian scripts.** Indian-script names are shaped with HarfBuzz and the Nirmala UI font. Where that font is not available, names are transliterated to Latin instead of printing broken glyphs.
5. **Status.** The footer states that the report is not a title document, and that the certified copy of the originating department prevails.

### B20. Mapping on the map (`records.py`, map editor in `app.js`)

1. **Who can map.** Survey officers, survey approvers and state admins. A drawing is only a proposal. A survey approver or another admin must approve it before the map changes (four-eyes rule, as in B17).
2. **What can be changed.**
   - *Map a building*: draw its outline on the imagery. The number of floors is optional.
   - *Correct a building*: change an existing building's outline or its number of floors.
   - *Remove a building*: for a demolished or wrongly mapped building.
   - *Edit a boundary*: move, add or delete a plot's corners. A supporting document is required.
   - *Add a new plot*: draw the boundary of a plot that is not on the map yet, anywhere on the imagery. It needs a free survey number (for example 201/1A) and a survey sketch, may not overlap an existing plot by more than 2 m² or 2%, and must be 20–200,000 m². After approval it starts in review with a "no revenue record" conflict for Revenue, and Revenue, Municipal and Town Planning are notified.
3. **Checks.** They run twice: in the Save panel before sending, and again on the server, which is binding.
   - The outline must be one closed shape that does not cross itself, in WGS 84.
   - A building must be 6 to 20,000 m², at least half inside the plot, and not a duplicate of another mapped building (more than 50% overlap).
   - A new boundary may not overlap a neighbouring plot by more than 2 m² or 2% of its area. The Save panel also shows the change in area, and marks a change of more than 10% as large.
   - Only one pending request per building (correct or remove) and one per plot boundary. Several new buildings on one plot may wait at the same time.
4. **Stale requests.** Approval is refused if the building being corrected or removed no longer matches the map.
5. **Pipeline re-runs.** Approved drawings are re-applied after every pipeline run. Corrections and removals are matched by shape (IoU ≥ 0.5), not by building ID, because IDs are renumbered on each run.
6. **Visibility.** Pending map proposals are drawn in orange for every user (layer "Pending map proposals") and listed on the plot. Approvers can show the current and proposed shapes together before deciding.
7. **Drafts.** Unsent work is kept as a draft in the browser of the device it was drawn on, never on the server, and can be resumed from the plot or the Requests tab. Cancelling discards it.
8. **Editor aids.**
   - *Snapping* to nearby corners (12 px) and edges (8 px) of plots, buildings and footprints. S toggles it; holding Alt places a corner freely.
   - *Square* makes corners within 25° of a right angle exactly square.
   - *Simplify* removes corners that change the outline by less than 30 cm.
   - *Trace* copies an outline from the AI footprints, building footprints or detected changes (for buildings), or from the municipal or georeferenced cadastral layer (for boundaries). Detected outlines can be shifted by a few metres; the whole shape can be dragged to align it.
   - Live area, corner count, edge lengths and area change.
   - *Keys*: Enter closes the shape, then saves; Esc goes back or cancels; Ctrl+Z / Ctrl+Y undo and redo; Delete removes the corner under the pointer.

### B21. Detection rules v2 and answer-key corrections (`conflicts.py`, `realdata.py`)

Measured on Jayanagar; the Accuracy tab shows the before/after table (`output/benchmarks/`).
1. **Answer-key corrections (data, not rules).** Two generator bugs made the hidden truth disagree with the files the pipeline reads: noise was added to plot outlines without their inner holes (so some cadastral plots were larger than their true shape), and planted encroachments were cut as holes instead of from the plot edge. Both are fixed. Effect: wrong-extent false alarms 4 → 1 with the same rules.
2. **Encroachment, building seen in one layer only.** Flagged only if it protrudes more than 2.5 × its positional tolerance, by at least 10 m² and at least 10% of its own area; and not if the protrusion lies on another plot's mapped building (a merged outline), or if the other building layer has the same building with a different outline (a shape disagreement between layers, not a boundary crossing). Buildings confirmed by both layers keep rule B11. Effect: false alarms 23 → 6, recall unchanged (13/14).
3. **Utility line under a building.** The footprint is first shrunk by its positional tolerance; the line must run at least 1 m under what remains. Effect: false alarms 5 → 1.
4. `LANDCONNECT_RULESET=v1` reproduces the original rules, so both can be measured on the same data.

### B22. Elevation models, heights and change detection (`elevation.py`, `geotiff.py`)

1. **Heights.** Building height = median of DSM − DTM over the raised pixels (> 2.5 m) inside the footprint; floors = round((height − 0.6 m) / 3.2 m).
2. **Changes between two DSMs,** measured inside each current footprint: raised area before < 20% of now = new building; raised area grew by > 12 m² and > 15% = extension; roof higher by > 0.7 of a floor = floor added. Raised in the earlier survey, bare now (< 1 m) and under no current building, at least 20 m² = demolished.
3. **Formats.** DSM/DTM are GeoTIFF (OGC GeoTIFF: pixel scale, tie point, EPSG key). Uploaded GeoTIFFs are recognised as DSM, DTM or orthophoto; one DSM + DTM gives heights, two DSMs with a year in their names also give change detection.
4. **Simulated data is labelled.** For Jayanagar the elevation models and the 2021 survey are simulated on the real footprints (open DEMs are 10–30 m and cannot resolve buildings). Every layer, message and conflict built on them says so.

### B23. Blind tests (`blind.py`)

1. **Random batch:** a new synthetic ward with a machine-drawn hidden seed and random layout and noise; the rule-set version is recorded. It tests over-fitting to one dataset, not the generator.
2. **Teammate batch:** someone who has not read the rules edits a copy of the data by hand and logs each edit. Only conflicts that differ from a run on the untouched copy are scored: found = a logged edit that produced a matching conflict on that plot; false alarm = a new conflict on a plot nobody edited.
3. Blind scores are reported separately from the development areas.

### B24. Privacy (`privacy.py`)

1. Viewer accounts and viewer API keys see personal names (owners, account holders, buyers, sellers) masked as "P••• N•••" in the published result, parcel lookups, questions, exports, OGC API, change requests and the audit list. Landmark names are not masked.
2. Masking happens on the server. Viewers cannot search by owner name.
3. Officers, approvers and admins see full names; the PDF report is for approvers and admins only (B19).

### B25. Review at scale (`api.py`)

1. **Bulk conflict decisions:** up to 200 open conflicts routed to the reviewer's department; one answer (accept the same source, or mark actioned); a note is required and each item gets its own audit entry marked "[bulk]".
2. **Batch approval of change requests:** up to 100; each request is checked on its own (department, four-eyes, stale value) and skipped with a reason if it fails.
3. **Column mappings below 90% certainty** are shown as "please confirm". An approver confirms or corrects them; the answer is stored per area (`schema_overrides.json`), audited, and used from the next run.

### B26. Learning from reviewers (`learning.py`)

1. When reviewers have decided at least 8 conflicts of one type and chose the same source in at least 90% of them, a rule is proposed: "accept that source for this type".
2. Only an admin can switch a rule on or off; each change is audited. Conflicts it settles say "Learned rule" in their resolution. A reviewer's own saved decision always wins.
3. The share of conflicts settled automatically is kept per run, so the effect is visible.

### B27. Derived parcel information (`insights.py`, `web/app.js`)

1. **ULPIN-format ID:** state code + a 12-character geohash of the parcel's reference point (14 characters, as DoLR's ULPIN). Shown as "ULPIN-format" until a state issues real ULPINs; searchable.
2. **Ownership chain:** registered deeds in date order; a break is flagged when a seller is not the previous buyer, and "mutation pending" when the RoR still shows the last seller.
3. **Path to approval:** the score after the open conflicts are resolved (and by whom), then the actions that raise the weakest confidence components most.
4. **Revenue at risk:** built-up area (footprint × floors) × an annual rate per m² (illustrative: residential ₹30, commercial ₹90, institutional exempt) for untaxed buildings, extra floors and residential assessments of commercial use; counted once per plot.
5. **Reviewer time saved:** 15 min per parcel checked by hand vs 4 min per prepared conflict and 2 min per flagged parcel. The assumptions are shown next to the number.

### B29. Real cadastral area: Thiruvanmiyur, Chennai (`tndata.py`)

1. **Real, unmodified inputs:**
   - TNGIS town-survey parcels and NCSCM coastal cadastral parcels, cut from the state-wide maps at zoom 14 (±0.6 m) and re-joined by feature ID
   - OpenStreetMap and Microsoft buildings, roads and landmarks
2. **Simulated on the real parcels:** Chitta/Patta (Tamil pattadar names, patta no., extent in hectares), Greater Chennai Corporation tax, TANGEDCO, CMWSSB, gas, CMDA permissions, TNREGINET deeds, DGPS, field visits and elevation.
   - The simulated DGPS and other records are placed on the ground, meaning the frame of the imagery-traced buildings.
   - The TNGIS file keeps its published position, so the pipeline has to detect and correct its offset (georeferencing, B2).
3. **Two real cadastres compared:**
   - Each TNGIS parcel is paired with the NCSCM parcel of the same subdivision number (NCSCM stores only that number).
   - A whole-layer shift is measured on those pairs and removed.
   - Each parcel is then paired with its best-overlapping NCSCM parcel.
   - "Two cadastral surveys disagree" is raised, routed to Survey, where the overlap is under 60%, the mean boundary offset is over 2 m, or the numbers differ. A GNSS-controlled boundary settles it automatically.
   - There is no answer key: the agreement figures are a measurement of the real records.
4. **Not scored here:** encroachment, self-intersection and duplicate scores, because the parcels and buildings are real and have no answer key.
5. **Tamil names** (rule B6): Tamil script does not mark voicing or aspiration, so names are folded (k/g, t/th/d, s/ch/j, p/b, zh/l) before comparison.
   - Tamil titles (Thiru, Thirumathi) are ignored, and a Tamil initial ("ச.") counts as an initial.
   - Examples: "திரு. முருகன்" = "Murugan S" (100), "லட்சுமி" = "Lakshmi" (92), "ச. முருகன்" ≠ "R. Murugan" (15).

### B28. State-wide cadastral maps (`statewide.py`)

1. **Source.** Parcels published by state portals (TNGIS, KGIS, APSAC, Bhunaksha, Bhuvan, LSGD, NCSCM), collected by datameet and released as PMTiles v3 (Mapbox Vector Tiles, OGC-compatible). Licence as stated by the maintainer: CC0 1.0, attributing datameet and the government source. The maintainer lists them as "not-so-open" because the portals' reuse terms are not stated; they are labelled **real** and credited on the map and in the Data tab.
2. **Serving.** Files stay on the server and are read in HTTP byte ranges, so the browser loads only the tiles in view. Access needs a signed-in account. Downloads resume and record each file's source and licence.
3. **Display.** Below zoom 11 each map is shown as a density picture built from the file's tile index (one cell per detail tile, coloured by its size), because the published low-zoom tiles thin the parcels out. From zoom 11 the parcels themselves are drawn and can be clicked.
4. **Use.** Reference only: they are not merged into an area's harmonised records, and the pipeline does not score them. Positional detail is limited by the tiles' deepest zoom (13–14, about ±0.6–2.4 m).
