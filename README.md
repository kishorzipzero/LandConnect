# Land Connect

**AI-enabled integration and harmonisation of multi-source geospatial data for urban land records**
Smart India Hackathon 2026 · Problem Statement 013 · Ministry of Rural Development

Land Connect takes land data from every department and combines it into one accurate parcel map. It tells officers exactly which plots need their attention.

- **Inputs:** cadastral sheets, revenue records (RoR/RTC), municipal tax layers, GNSS/CORS surveys, field verification, drone imagery with DSM/DTM, and AI building footprints. They can arrive in any common GIS format and any coordinate system.
- **Harmonisation:** the system lines the inputs up, fixes topology, matches records across departments and detects changes.
- **Confidence:** it gives every parcel a **confidence score**. Parcels it is sure about are approved automatically. Only disagreements it cannot settle go to a reviewer, and every decision is written to a tamper-evident audit trail.

The rules and standards the system follows are in [docs/RULES_AND_STANDARDS.md](docs/RULES_AND_STANDARDS.md).

---

## Run it

**Windows:** double-click `run.bat`. It installs everything on first start, runs the pipeline and opens http://127.0.0.1:8000.

**Manual start (any OS, Python 3.11+):**
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt           # macOS/Linux: .venv/bin/pip
.venv\Scripts\python -m landconnect.synth                # synthetic benchmark ward -> data/demo
.venv\Scripts\python -m landconnect.realdata             # real-world area (Jayanagar, Bengaluru) -> data/jayanagar
.venv\Scripts\python -m landconnect.pipeline demo        # run the pipeline on an area (prints every step)
.venv\Scripts\python -m landconnect.pipeline jayanagar
.venv\Scripts\python -m uvicorn landconnect.api:app --port 8000
```
There is no database, GPU or cloud account to set up. A run takes about 5–12 seconds on a laptop.

**Any other Indian area:** give a workspace name, a bounding box (south, west, north, east) and a title:
```
.venv\Scripts\python -m landconnect.realdata madurai 9.9265 78.1400 9.9320 78.1480 "KK Nagar, Madurai"
```

**Signing in.** On first start the server creates three accounts, one per role, and writes their passwords to `output/initial_credentials.txt`. That file is never served over the web. You can also set the passwords yourself with `LANDCONNECT_ADMIN_PASSWORD`, `LANDCONNECT_REVIEWER_PASSWORD` and `LANDCONNECT_VIEWER_PASSWORD`.

| Role | Can |
|---|---|
| viewer | See maps, results, the audit trail and exports, and use the OGC API |
| officer | Everything a viewer can, plus **propose changes** to the records their own department is the authority for, with a supporting document |
| reviewer (approver) | Everything an officer can, plus **approve or reject** other officers' changes and decide conflicts **routed to their department** |
| admin | Everything a reviewer can, plus run the pipeline, upload data, manage users and issue API keys |

Every user belongs to one department (Revenue, Survey, Municipal, Town Planning, Registration, Water, Electricity, Gas or Roads) or to "all". On first start, an officer, an approver and a read-only staff account are created per department (for example `revenue.officer`, `revenue.approver`, `revenue.staff`), each with the department's job title. Each department sees its own records in full and other departments' records as the admin's **department access** matrix allows (full / summary / none); see docs/WORKFLOWS.md §1. Their passwords are added to `output/initial_credentials.txt`.

**Who grants which role.**
- The state admin grants approvers and admins.
- A department approver grants, changes and removes the viewers and officers of their own department only (the **Access** tab).
- Nobody can change their own access, and the last admin can't be removed.
- New accounts get a one-time password that must be changed at first sign-in.
- Every access change is written to a hash-chained access log. See rule B18.

**Property report (PDF).** Approvers and admins can download a report for any plot, from the parcel inspector or from **Data → Export**. It covers:
- key facts and a boundary map
- the confidence breakdown
- the records of every government body
- conflicts, official updates, and the plot's audit trail

Each report's SHA-256 is written to the audit trail. See rule B19.

**Mapping on the map.** Survey officers, survey approvers and admins can add a new plot (anywhere on the imagery, with **+ New plot**), map a building, correct or remove one, and edit a plot boundary directly on the imagery. Tools: snapping, square corners, simplify, trace a detected outline, undo/redo and drafts. **Save** runs the checks and sends a change request. The map changes only after a survey approver approves it, and pending proposals show in orange for everyone. See rule B20.

**Secure record updates (maker–checker).** An officer proposes a change from the parcel inspector. An approver from the same department, never the proposer, approves it in the **Requests** tab. The approval is blocked if the record changed after the request was made. Approved changes update the record, close the conflicts they settle, notify the other departments' inboxes and are written to the audit trail. See rules B16 and B17 in [docs/RULES_AND_STANDARDS.md](docs/RULES_AND_STANDARDS.md).

**Other government bodies.** Each area also has electricity consumers (DISCOM), water and gas connections, the sewer network, building permissions and registered sale deeds (IGR). They are linked to parcels by plot reference and GPS (99% linked correctly). The system then flags:
- registered sales not yet mutated in the RoR
- commercial electricity or water use on property taxed as residential
- live connections on untaxed properties
- floors beyond the building permission
- sewer lines under buildings

Other departments' systems use **API keys**, issued in the Admin tab and sent as the `X-API-Key` header. Only hashes of passwords (PBKDF2-SHA256), session tokens and keys are stored.

**Database.** `output/landconnect.db` (SQLite) holds users, sessions, API keys, run history, reviewer decisions and a searchable index of parcels and conflicts (`/api/search/parcels`, `/api/stats`). The same schema can move to PostgreSQL/PostGIS for production.

**Tests.** Run `.venv\Scripts\python -m pytest tests`. The 69 tests cover the matching rules, owner-name comparison, CRS recovery, topology, the audit chain, sign-in, roles, API keys, the review workflow, exports, path safety, the maker–checker workflow (department scoping, four-eyes rule, evidence checks, stale-edit blocking, notifications), delegated access and one-time passwords, the PDF report, map drawing, GeoTIFF, elevation change detection, ULPIN and ownership chains, name masking, bulk and batch decisions, column confirmations, learning from reviewers, and a full pipeline run.

**Optional settings (environment variables):**

| Variable | Effect |
|---|---|
| `ANTHROPIC_API_KEY` | Questions in the search bar are interpreted by Claude. Without it, the built-in offline parser is used. |
| `GOOGLE_MAPS_API_KEY` | Adds a Google satellite basemap through the official Map Tiles API. It is display only, as Google's terms require. The Esri satellite basemap is always available. |

---

## The search bar

Press **/** (or Ctrl+K) anywhere and start typing. Suggestions appear as you type, grouped:

| Type | Examples |
|---|---|
| Plot, khata, property ID, ULPIN | `103`, `103/1`, `1083`, `GCC-13-179-00012`, `KATDR1T6JY17UE` |
| Department record or request | `RR61…` (electricity), `W583-…` (water), `JNR-1-7…` (deed), `TP/2009/…` (permission), `CR-61` |
| Owner name, in any script (not for viewers) | `pooja`, `ಪೂಜಾ`, `முருகன்` |
| Building, landmark, road, area | `BLD-0138`, `canara`, `thiruvanmiyur` |
| Coordinates | `12.9295, 77.5835`, `12°55'46"N 77°35'01"E` |
| Questions, with live counts | `how many plots have a sale not mutated`, `total tax at risk from floors beyond permission`, `water department conflicts`, `plots between 200 and 400 sq m without conflicts`, `plots larger than 2 grounds`, `new construction since the last survey`, `plots with a broken ownership chain` |
| Any place in India | `Chennai`, `Madurai`: flies there and switches on that state's real cadastral map |

Results show a one-line answer, totals, sorting, CSV/GeoJSON download of the matches and a shareable link. See rules B14 and B14b.

## A real Tamil Nadu area: Thiruvanmiyur, Chennai

`python -m landconnect.tndata` builds the area. The pipeline picks UTM zone 44N for Chennai by itself.

- **Real parcels:** 935 TNGIS parcels and 1,118 NCSCM coastal parcels, two independent government surveys of the same land.
- **Real buildings and roads:** OpenStreetMap and Microsoft buildings, roads and landmarks.
- **Simulated records:** Chitta/Patta with Tamil pattadar names, Greater Chennai Corporation tax, TANGEDCO, CMWSSB, CMDA and TNREGINET.

What it shows about the real records:
- the TNGIS map sits several metres from the imagery-traced buildings, and the pipeline finds and corrects that offset
- how far TNGIS and NCSCM agree once NCSCM's whole-layer shift is removed, with a "Two cadastral surveys disagree" conflict where they don't
- 51 TNGIS parcels that reuse another parcel's survey/subdivision number

See rule B29.

## State-wide cadastral maps (real): all of South India

Real parcel maps from the state land-records portals, collected by datameet ([indian_cadastrals](https://github.com/ramSeraph/indian_cadastrals)), are shown as vector tiles on the same map:

| State | Maps | Size |
|---|---|---|
| Tamil Nadu | TNGIS cadastrals (whole state), NCSCM coastal | 931 MB |
| Karnataka | KGIS cadastrals, NCSCM coastal | 1.1 GB |
| Andhra Pradesh | APSAC cadastrals, forest, NCSCM coastal | 890 MB |
| Telangana | Bhunaksha cadastrals | 681 MB |
| Kerala | Bhuvan cadastrals, LSGD urban, NCSCM coastal | 208 MB |
| Puducherry | NCSCM coastal | 3 MB |

- **See them:** open **Layers → State-wide cadastral maps → Show all states**. Zoomed out, you see a parcel-density map of each state; from district zoom (11) inwards, the real parcel lines appear. Click a parcel for its survey number, subdivision, village and district codes.
- **Get them:** `python -m landconnect.statewide fetch south`, or any other state tag such as `maharashtra` (the whole of India is roughly 15–25 GB). The files are saved to `data/_statewide/`, and the map reads only the tiles in view.
- **Licence:** CC0 1.0 per the maintainer, crediting datameet and the source portal. The maintainer lists them as "not-so-open" because the portals' own reuse terms are not stated, so confirm with the state before production use.
- **Accuracy:** the published tiles hold full detail up to zoom 13–14, which is about ±0.6–2.4 m at street level. In Bengaluru, KGIS has only large survey-number blocks, not individual sites.



| Area | What is real | What is simulated | Why |
|---|---|---|---|
| **Ward 47, Mangaluru — synthetic benchmark** (`data/demo`) | Nothing | Everything, including drone orthophotos, DSMs for two dates and a DTM | The true layout is known exactly, so every number can be measured |
| **Jayanagar, Bengaluru — real-world** (`data/jayanagar`) | 722 OpenStreetMap buildings, 551 **Microsoft AI-extracted building footprints**, 121 roads, 127 landmarks, and the satellite basemap | Land records drawn on the real geography (a cadastral **Shapefile in Kalianpur 1975**, a **Bhoomi-style RTC with Kannada owner names** and extents in guntas, a **BBMP tax KML**, DGPS control, GPX field visits); department records; and **DSM/DTM GeoTIFFs for 2021 and 2026** measured on the real footprints | Indian land and utility records are not openly downloadable, and open DEMs (Bhuvan CartoDEM, SRTM) are 10–30 m, too coarse to measure a building |

The app labels every file **Real** or **Simulated** (Data tab, parcel sources, map layers). Both areas contain planted problems. The hidden truth is in `_truth/` and is used only to score results.

### What the real-world test showed

- **Microsoft's AI footprints sit about 3.4 m off OpenStreetMap** across Jayanagar. Without correction this produced 511 false encroachments; the **co-registration** step removes such shifts automatically.
- **The AI model merges attached row houses into blobs.** Where OSM shows the separate buildings, 80 AI footprints are split.
- **Where the two building layers disagree about a building's outline, that is not an encroachment.** Rules v2 only flag a single-layer building when it protrudes well beyond its positional error and is not a shape disagreement between layers (rule B21). Encroachment precision went from 36% to 68% with no loss of recall.

---

## Results

Measured against the hidden truth. Open the **Accuracy** tab to see them, including the before/after table.

| Measure | Synthetic ward (240 parcels) | Jayanagar, real geography (768 parcels) |
|---|---|---|
| **All planted problems pooled: precision / recall / F1** | 99% / 100% / 0.996 | 96% / 97% / 0.963 |
| Parcels finalised with no manual work | ~81% | ~84% |
| Errors among **auto-approved** parcels | **0%** | **0%** |
| Mean boundary error: municipal layer → fused | 0.55 m → 0.13 m | 0.86 m → 0.15 m |
| Stale owners caught | 16 / 16, 0 false alarms | 47 / 47, 0 false alarms |
| Encroachments caught | 3 / 3, 1 false alarm | 13 / 14, **6** false alarms (was 23) |
| Legacy pipe under buildings | 4 / 4, 0 false alarms | 2 / 2, **1** false alarm (was 5) |
| Floors beyond building permission | 6 / 6 | 6 / 6, 1 false alarm |
| Change detection between two surveys | 17 / 17 | 16 / 16, 1 false alarm |
| Building heights from DSM − DTM | 0.4 cm error | 3 cm error, floors 100% right (simulated DSM) |

**Blind tests** (rules frozen): three random synthetic wards with hidden seeds scored F1 0.98, 0.98 and 0.99. A teammate kit (`python -m landconnect.blind kit`) lets someone who has not read the rules break the data by hand and score it (`python -m landconnect.blind score`). That is the stronger test, and it is still to be run.

**Before and after:** `output/benchmarks/<area>.json` keeps the scores of the original rules, the corrected data and the new rules. Two generator bugs made the answer key disagree with the files it produced; they are fixed and reported separately from the rule changes (rule B21).

### Honest limitations

- The synthetic drone imagery is rendered, so building extraction is easier than on real ORI. With real imagery, `extract_buildings()` should be replaced by a trained model such as SAM or U-Net. It is a single function.
- The matcher reports near-100% match confidence because its training noise is simpler than real ULB data. It should be retrained on the labels the review queue produces.
- In Jayanagar only the buildings, roads, landmarks and basemap are real. Records and elevation models are simulated and labelled as such.
- The ULPIN shown is in the official format (state + 12 location characters) but is not an issued ULPIN. Revenue at risk uses stated, illustrative tax rates.
- Map layers are published as one JSON file per area and the database is SQLite. At production scale these would move to PostGIS, with GeoServer for WMS/WFS, behind HTTPS.

---

## Your feature list, checked

| Feature | Status | Where |
|---|---|---|
| Ingest several datasets in different formats and CRSs | ✅ GeoJSON, GeoJSON-lines, Shapefile, KML, GPX, GeoPackage, CSV (lat/lon), **GeoTIFF** (DSM, DTM, ORI), drone PNG | `ingest.py`, `geotiff.py` |
| Standardise to one CRS and schema | ✅ Auto UTM zone, legacy datums, unknown grids; uncertain column mappings go to an approver to confirm | `crs_engine.py`, `schema_mapper.py` |
| Fuzzy matching of IDs and names | ✅ Hierarchical plot IDs, Indic-script names | `schema_mapper.py` |
| Spatial matching when IDs do not agree | ✅ ML matcher on geometry and ID features | `matcher.py` |
| Unified output dataset with a national parcel key | ✅ One parcel layer plus buildings and changes; ULPIN-format ID per parcel | `publish.py`, `insights.py` |
| Interactive map, click to inspect | ✅ Satellite, street map, drone, DSM, DTM and height layers | `web/` |
| Conflict / discrepancy detector | ✅ 17 conflict types, auto-resolution, learned rules | `conflicts.py`, `learning.py` |
| Stats dashboard | ✅ Overview: finalised share, conflicts, reviewer time saved, tax at risk | `web/app.js` |
| Confidence score and path to approval | ✅ Parcel score out of 100, calibration by score band, "what raises this score" | `confidence.py`, `web/app.js` |
| DSM / DTM heights and change detection over time | ✅ Heights and floors per building; new, demolished, extension, floor added | `elevation.py`, `change.py` |
| Review at scale | ✅ Sorting, bulk decisions, batch approval of requests, all audited | `api.py`, `web/app.js` |
| Learning from reviewers | ✅ Consistent decisions become proposed rules an admin switches on | `learning.py` |
| Blockchain-style audit trail | ✅ SHA-256 hash chain, tamper check, per-parcel history | `audit.py` |
| Privacy | ✅ Owner names masked for viewer accounts on every endpoint | `privacy.py` |
| Search and natural-language query | ✅ Instant suggestions (plots, records, owners, places, coordinates), questions with one-line answers and totals, places anywhere in India, export of results; offline rule parser, Claude when a key is set | `search.py`, `query.py` |
| Tamil Nadu terminology and names | ✅ Chitta/Patta, TS no., subdivision, grounds and cents, Tamil-script names with initials and titles | `schema_mapper.py`, `tndata.py` |
| Mobile-friendly and offline | ✅ Responsive layout, installable app, service-worker cache | `web/sw.js` |
| Export and interoperability | ✅ GeoJSON, CSV, KML, GeoPackage, Shapefile, ISO 19152 LADM, OGC API – Features | `export.py`, `api.py` |
| Authentication / roles | ✅ Viewer, officer, approver, admin; delegated granting; API keys | `auth.py`, `access.py` |

The step-by-step workflows for every role are in [docs/WORKFLOWS.md](docs/WORKFLOWS.md).

---

## Five-minute demo script

1. **Overview (Jayanagar):** ~84% of parcels need no manual work, 176 hours of review saved, ₹12.6 lakh a year of property tax at risk. Click *What is real?* to show the real/simulated labels.
2. **Accuracy:** the precision/recall/F1 bar, the blind tests and the before/after table (encroachment false alarms 23 → 6).
3. **Click a red plot:** the path to approval, the ownership chain (mutation pending), ULPIN and tax at risk.
4. **Review:** sort by problem type, select several, decide in bulk with a note. The decisions appear in **Audit**.
5. **Changes:** 2021 vs 2026 elevation surveys; turn on *Building heights* for both years in Layers.
6. **Map a building:** draw a house and send it for approval; approve it as `survey.approver` (four-eyes).
7. **Sign in as `viewer`:** owner names appear as "P••• N•••".
8. **Data → Export:** LADM package, OGC API, PDF property report.

---

## Project structure

```
landconnect/
  config.py          paths, workspaces, CRS choice, thresholds, weights
  synth.py           synthetic benchmark ward + hidden truth
  realdata.py        real-world area builder (OSM + Microsoft AI footprints + simulated land records)
  ingest.py          any GDAL format / CSV, file hashes, dataset-type detection
  schema_mapper.py   attribute mapping, units, plot IDs, Indic-script name matching
  crs_engine.py      CRS detection, reprojection, GNSS georeferencing, legacy datum correction
  coreg.py           layer co-registration (whole-layer shifts)
  extractor.py       building extraction from drone ORI + DSM/DTM
  topology.py        topology correction
  matcher.py         ML spatial matching (trained on separate wards)
  change.py          change detection between survey dates
  conflicts.py       conflict detection and auto-resolution rules
  confidence.py      confidence scoring and status
  review.py          reviewer decisions (persisted, undoable, audited)
  audit.py           hash-chained audit trail
  query.py           natural-language query (offline parser / Claude)
  export.py          GeoJSON, CSV, KML, GeoPackage, Shapefile, LADM exports
  evaluate.py        accuracy vs hidden truth
  pipeline.py        orchestration (also a CLI)
  publish.py         WGS 84 layers, reports, summary
  api.py             FastAPI server, sign-in, review, audit, query, exports, OGC API - Features
  auth.py            users, roles, sessions, API keys
  db.py              SQLite database: runs, decisions, parcel / conflict index
web/                 dashboard (Leaflet, vanilla JS, installable, works offline)
docs/                rules and standards
tests/               pytest suite
data/<area>/raw      input files for each area        data/<area>/_truth   hidden truth (evaluation only)
output/<area>/       result, map images, decisions, audit trail
```

Data licences: OpenStreetMap © OpenStreetMap contributors (ODbL). Microsoft Global ML Building Footprints (ODbL). Satellite basemap © Esri, Maxar, Earthstar Geographics (display only).
