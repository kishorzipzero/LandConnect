IDEA TITLE (max 100 characters)
===============================

Land Connect: Harmonising Multi-source Land Records with GNSS Alignment and Confidence Scoring


ABSTRACT / SUMMARY (max 10,000 characters)
==========================================

Problem
In Indian cities the same piece of land is described by several government records that rarely agree:
- the Revenue record of rights (RoR / RTC / Chitta-Patta);
- the Survey department's cadastral sheets;
- the municipal property-tax GIS;
- building permissions (Town Planning) and registered sale deeds (Registration);
- electricity, water and gas connection ledgers.

Each department keeps its own file in its own format, language, unit and coordinate system. Plot numbers are written differently ("Sy.No. 102 Hissa 1", "102-1", "KH-102/1"). Areas come in guntas, cents, square yards, square feet or hectares. Owner names are in Kannada or Tamil in one register and in English in another. Old cadastral sheets sit in local grids or legacy datums such as Kalianpur 1975, so they are several metres off the ground. The result is stale owners, sales never mutated in the RoR, built plots missing from the tax register, and boundary disputes. Today these are reconciled by hand in desktop GIS, parcel by parcel.

Our solution
Land Connect is a working prototype: an automated harmonisation pipeline with a web GIS application built around it. Departments hand over their files as they are. The pipeline runs 11 steps and produces one corrected record per parcel with a confidence score:
1. Ingest: reads any common GIS or tabular format (Shapefile, KML, GPX, GeoPackage, GeoJSON, CSV with GPS, GeoTIFF) and identifies the type of each dataset.
2. Attribute mapping: maps every department's columns to one common schema, using both column names and column values. It converts local area units and normalises plot IDs and names, including transliteration of Indian scripts.
3. Coordinate transformation: detects or infers each file's coordinate system, reprojects everything to the local UTM zone, and georeferences old cadastral sheets onto GNSS survey points with a least-squares affine fit and outlier rejection.
4. Feature extraction: extracts buildings from drone orthophotos plus surface and terrain models (DSM/DTM), or fuses AI-extracted building footprints with OpenStreetMap buildings.
5. Topology correction: repairs self-intersecting and duplicate parcels, snaps corners to GNSS points, merges shared corners, and removes overlaps, gaps and slivers.
6. Spatial matching: a machine-learning classifier (gradient boosting) links survey plots to municipal properties and detects merged properties. Department records are linked to plots by reference and by GPS location.
7. Change detection: compares two survey dates and finds new, demolished and extended buildings and added floors.
8. Conflict detection: 18 types of cross-source and cross-department checks, including owner mismatch, area mismatch, boundary disagreement, sale not mutated, construction not on the tax register, floors beyond the permission, encroachment and two surveys disagreeing.
9. Confidence scoring: every parcel gets a score from five components (geometry, attributes, source reliability, model confidence, topology). Open conflicts cap the score.
10. Evaluation: measures accuracy wherever an answer key exists.
11. Publishing: map layers, APIs, exports and a tamper-evident audit trail.

Trust and governance
The system never overwrites a record silently. It resolves a disagreement automatically only when a more reliable source settles it:
- GNSS-surveyed corners settle boundary and area questions;
- a field-verification visit settles an owner question.

Everything else goes to a review queue that shows every version side by side, routed to the responsible department. Parcels scoring 85 or more are finalised automatically; the rest are flagged or sent for review.

Record corrections follow a maker–checker workflow:
- An officer proposes a change to a field their department legally owns.
- An approver from the same department, never the proposer, approves it.
- Approved changes update the record, close the conflicts they settle, notify the other departments and are re-applied on every future run.

The system can also draft corrections itself: for example, an owner update from a registered sale deed. These still need an approver.

Access control:
- Four roles (viewer, officer, approver, admin) across nine departments.
- A department need-to-know matrix decides how much each department sees of the others' records; it is enforced on the server.
- Every automatic and human decision is written to a hash-chained, tamper-evident audit trail.

What has been demonstrated
The prototype runs on five test areas:
- A fully synthetic ward with a known answer key.
- Jayanagar, Bengaluru: real OpenStreetMap and Microsoft AI building footprints and real roads, with simulated land records.
- Thiruvanmiyur, Chennai: real Tamil Nadu government cadastral parcels from two independent surveys (TNGIS and NCSCM), with simulated land records.
- Two blind tests: a random-seed ward and a teammate-edited dataset, both unseen when the rules were written.

Measured results on these areas:
- Old cadastral sheets corrected from 3–6 m positional error to about 0.4–0.5 m using GNSS points.
- 79–84% of parcels finalised without manual GIS work.
- About 99% of department records linked to the correct parcel.
- Planted problems detected with F1 between 0.94 and 0.99, including 0.99 on the blind test.
- Errors fall into the "review" band, not the auto-approved band.
- The two real Tamil Nadu surveys were compared automatically: after removing a 2 m shift, 82% mean overlap, 0.57 m median offset, survey numbers agreeing on 99% of paired parcels.
- A full area of about 1,000 parcels processes in under a minute on a laptop.

Honest scope: land, department, GNSS and elevation records are simulated, because they are not openly available. The accuracy figures are measured against errors we planted. Building extraction uses classical computer vision rather than deep learning. The prototype runs as a local application.

Outputs and interoperability
- Harmonised parcel layers with confidence scores, conflict tickets with evidence, detected changes, georeferencing reports and accuracy reports.
- A department-wise property dossier and a PDF property report per plot.
- A REST API and an OGC API – Features service.
- Exports in GeoJSON, CSV, KML, GeoPackage and Shapefile, plus an ISO 19152 LADM-aligned GeoPackage.
- Parcels carry a location-based ID in the ULPIN format (not the official ULPIN, which states issue).

Impact
Land Connect automates the mechanical part of land-record reconciliation: formats, coordinate systems, alignment, topology, linking and cross-checks. Officers only handle the genuine disagreements, with the evidence in front of them. This reduces manual GIS work, keeps records consistent across departments, surfaces untaxed construction and unmutated sales, and makes every change traceable. That supports DoLR's goals of consistent, interoperable, faster cadastral and urban land-record workflows.


IDEA DESCRIPTION (max 50,000 characters)
========================================

1. BACKGROUND AND PROBLEM

Urban land administration in India depends on records held by many separate bodies:
- Revenue (record of rights: owner, extent, land classification);
- Survey, Settlement and Land Records (cadastral maps, boundaries, GNSS control);
- the Urban Local Body (property-tax assessments);
- Town Planning (building permissions);
- Stamps and Registration (sale deeds);
- utilities (electricity, water, gas connections);
- Roads and Public Works.

Each maintains its own dataset, built at different times, with different methods and tools.

When these records are compared they disagree in predictable ways:
- Identifiers: the same plot is "Sy.No. 102 Hissa 1" in one file, "102-1" in another and "KH-102/1" in a third.
- Units: extents in guntas, cents, ankanam, square yards, square feet, acres or hectares.
- Languages: the RoR has owner names in Kannada or Tamil; the municipal register writes them in English, often with honorifics, initials or swapped order.
- Coordinate systems: old cadastral sheets were digitised in local grids or legacy datums (for example Kalianpur 1975); municipal layers are in WGS 84; utility networks may be in Web Mercator.
- Positional error: even after reprojection, whole layers are shifted by 1–6 metres, and individual boundaries carry digitising noise, self-intersections, duplicates, overlaps and gaps.
- Content: owners in the RoR are stale because registered sales were never mutated; built plots are missing from the tax register; buildings exceed the floors their permission allows; one municipal assessment covers several survey plots; buildings cross plot or road boundaries; commercial electricity tariffs sit on plots taxed as residential.

Today this is reconciled by hand. GIS staff overlay layers in desktop software, drag them into place, look up records in different systems, and decide parcel by parcel. The work is slow and hard to repeat, and it leaves no trace of why a decision was made. When new data arrives it has to be done again.

PS 26013 asks for automated integration and intelligent harmonisation of these sources: drone imagery / ORI, DSM/DTM, cadastral maps, revenue records, municipal GIS, utility data, ground truth, GNSS/CORS and building footprints. It asks for AI/ML-based spatial matching, topology correction, attribute mapping, georeferencing and coordinate transformation, change detection, spatial conflict resolution and confidence scoring, to reduce manual GIS work, improve consistency, enable interoperability and speed up cadastral workflows.


2. OBJECTIVES

- Accept each department's data as it is delivered, without manual reformatting.
- Bring every dataset into one accurate coordinate frame, using GNSS survey points as the reference.
- Build one clean, topologically valid parcel fabric.
- Link every record from every department to the right parcel.
- Detect every disagreement between sources, and settle automatically only those a more reliable source can settle.
- Give every parcel a transparent confidence score, so officers work only where it is needed.
- Keep humans in charge of disputed cases, with the evidence in front of them and a workflow that respects departmental authority.
- Make every automatic and human decision traceable and tamper-evident.
- Publish the result in open, standard formats and interfaces.


3. PROPOSED SOLUTION: LAND CONNECT

Land Connect has two parts:
- An automated harmonisation pipeline of 11 steps, which turns the raw department files for an area into a harmonised result.
- A web GIS application in which officers, approvers and administrators view that result, review disagreements, correct records through a governed workflow, search and query the data, and export it.

A "workspace" is one area being harmonised (for example a ward). Its raw files sit in one folder, and each run produces a published result, a database index and an audit trail.


4. THE HARMONISATION PIPELINE (11 STEPS)

Step 1: Ingest.
- Every file in the area is read through GDAL/OGR (via pyogrio), plus dedicated readers for CSV with coordinates, GPX, GeoJSON lines, images, raster arrays and GeoTIFF.
- The system identifies what each file is: cadastral map, revenue records, municipal layer, GNSS points, field verification, building footprints, AI footprints, utility network, electricity / water / gas consumers, building permissions, registered deeds, roads, landmarks, DSM, DTM or orthophoto.
- It computes a SHA-256 fingerprint of each file for the audit trail.

Step 2: Attribute mapping.
- Each dataset's columns are mapped to one canonical schema: plot ID, sub-division, owner name, area, land use and record ID.
- Two kinds of evidence are combined:
  - the column name: whole-token synonym matching against a dictionary of Indian land-record terms (khasra, hissa, khata, patta, pattadar, assessment no., town survey no. and so on), plus fuzzy matching for typos;
  - the column values: does the column look like plot numbers, person names or areas?
- So a column called "OCCUPANT" is still recognised as the owner name from its values, and a "father/husband name" column is not mistaken for the owner.
- Area units are detected from column names (sq ft, sq yd, gunta, cent, ankanam, acre, hectare) and converted to square metres.
- Plot IDs are normalised to one form; land-use codes are normalised (for example RES to residential).
- Owner names are normalised: Kannada, Tamil and Devanagari names are transliterated to Latin; honorifics and initials are handled; spelling variants are folded; name parts are paired one-to-one, so "K. Ganesh" and "Sumathi K." do not match just because they share an initial.
- Mapping confidence and the evidence used are recorded. Uncertain mappings are shown for an approver to confirm or correct, and confirmations are applied on every later run.

Step 3: Coordinate transformation and georeferencing.
- Each dataset's coordinate system is read from the file (EPSG code, WKT or .prj). If absent, it is inferred from the coordinate ranges (geographic, UTM-like or Web Mercator).
- The working frame is the UTM zone that contains the data (for example 43N for Bengaluru, 44N for Chennai). All layers are reprojected with pyproj.
- A cadastral sheet in an unknown local grid is georeferenced from GNSS points: a coarse affine fit on parcel centres, then repeated pairing of each GNSS point with the nearest corner of its plot, a least-squares affine refit, and rejection of outliers beyond three times the median residual.
- A sheet in a declared legacy coordinate system that is still more than 1 m off the GNSS points after reprojection gets the same correction for its residual datum or registration shift.
- The report records the error before and after (RMSE), the rotation, scale and shear, and the number of points used.

Step 4: Feature extraction.
- Where drone orthophotos and surface/terrain models exist, buildings are extracted with classical computer vision (OpenCV):
  - the normalised surface model (DSM minus DTM) above 2.5 m marks raised objects;
  - an excess-green vegetation index removes trees;
  - morphological filtering, contour tracing, polygon simplification and rectangle regularisation produce building outlines;
  - each building gets a height (median of the normalised surface model), a floor count and a shape-quality confidence.
- Where no imagery exists, the pipeline uses AI-extracted building footprints (Microsoft's machine-learning footprints), co-registers them, fuses them with OpenStreetMap buildings, splits AI outlines that merge several row houses, and records how well the two layers agree.
- Where surface and terrain models are available as GeoTIFF files, building heights and floors are measured inside each footprint.

Step 5: Topology correction. Applied in order:
1. Repair self-intersecting ("bow-tie") polygons by re-ordering the corners, or with a make-valid fallback.
2. Remove parcels digitised twice.
3. Snap corners to GNSS points (within 2.5 m for the same plot, 0.8 m otherwise).
4. Merge corners shared by neighbouring parcels (within 1.2 m) using a nearest-neighbour graph.
5. Resolve overlaps in favour of the more reliable parcel.
6. Fill thin gaps (up to 25 m²), giving each to the neighbour with the longest shared edge.
7. Remove slivers.

Every repair is recorded against the parcel.

Step 6: Spatial matching and fusion.
- Co-registration first: whole-layer shifts between sources are measured and removed.
  - For matched parcels, the median displacement is used.
  - For building layers, a coarse-to-fine grid search finds the shift that puts the most buildings entirely inside a single plot.
  - A shift is applied only if it exceeds 0.3 m and clearly improves the fit.
- Parcel-to-municipal matching uses a supervised machine-learning model: a gradient-boosting classifier (scikit-learn, 150 trees).
  - For every candidate pair within 2 m, found with an R-tree spatial index, it computes seven features: overlap (IoU), coverage of each polygon, centroid distance, area ratio, Hausdorff distance and ID similarity.
  - It predicts a match probability. Each plot keeps its best partner; a municipal polygon matched by two or more plots is flagged as a merged property.
  - The model was trained on six separate synthetic training wards with different noise and shifts, never on the ward being evaluated.
- Revenue records and field visits are linked by normalised plot ID.
- Department records (electricity, water, gas, permissions, deeds) are linked by plot reference and by GPS location. When the two disagree, a GPS point inside a parcel wins, because clerks mistype plot numbers more often than meters move. The method used is recorded on every link.

Step 7: Change detection.
- Buildings from two survey dates are compared by footprint overlap:
  - no counterpart: a new building;
  - footprint grown by more than 12%: an extension;
  - height up by at least about 70% of a storey: an added floor;
  - earlier building now missing: demolished.
- Where only surface models of two dates exist, height changes are measured inside each current footprint.

Step 8: Conflict detection and resolution.
18 conflict types are checked:
- owner name differs between records;
- recorded extent differs from surveyed area (tolerance scaled to the plot's shape);
- boundaries disagree between the cadastral and municipal layers;
- one municipal property covers several survey plots;
- surveyed parcel without a revenue record;
- revenue record without a parcel;
- built parcel missing from the property-tax register;
- building crosses the parcel boundary (encroachment);
- one building spans two plots;
- registered sale not yet mutated in the RoR;
- commercial use on a residential tax assessment;
- live service connections but no property tax;
- more floors than the building permission allows;
- utility line running under a building;
- new construction on land assessed as vacant;
- parcel digitised twice;
- self-intersecting boundary;
- two independent cadastral surveys disagree.

Each conflict carries a severity, a plain-language explanation, each source's version with its reference, a suggested action, and the departments it is routed to. For tax-related problems it also carries an estimate of annual property tax at risk, based on stated assumed rates.

Automatic resolution happens only when a more reliable source settles the question. Source reliability ranks GNSS highest, then field verification, drone or AI extraction, the municipal layer, and the cadastral sheet.
- A boundary or area disagreement on a parcel with three or more GNSS-surveyed corners is settled in favour of the surveyed boundary.
- An owner disagreement is settled when a field-verification visit confirms one of the names.
- Invalid and duplicate geometry is repaired automatically.

Everything else stays open for a human. Saved reviewer decisions are re-applied on every run. The rule set is versioned, so its effect can be compared on benchmarks.

Step 9: Confidence scoring.
Confidence = 100 x (0.30 G + 0.20 A + 0.20 S + 0.15 M + 0.15 T):
- G: geometric agreement (overlap and boundary offset against the municipal polygon, raised for GNSS-surveyed plots);
- A: attribute agreement (revenue record present, owner-name similarity, area agreement, field verification);
- S: reliability of the sources behind the parcel;
- M: the matching model's probability;
- T: topology validity.

Open conflicts cap the score: a high-severity conflict keeps it below 70, a medium one below 85. So no parcel is auto-approved while its sources disagree.
- Score 85 or more: approved automatically.
- 70 to 85: approved with a flag.
- Below 70: sent to review.
- Parcels whose conflicts a human has decided are marked verified.

The weights are set by hand and documented. Calibration is measured on the test areas (see section 9).

Step 10: Evaluation. Where a hidden answer key exists (synthetic and simulated test areas), the pipeline measures:
- precision and recall for each problem type;
- boundary error against the true boundaries;
- matching, linking and schema-mapping accuracy;
- change-detection accuracy;
- how often parcels in each confidence band contain an error.

Results are kept as a benchmark history.

Step 11: Publishing.
- The harmonised result is published as 15 map layers (harmonised parcels and buildings, detected changes, the source layers before and after correction, GNSS points, field visits, service connections, utilities, roads and landmarks), with the reports.
- Parcels and conflicts are indexed in a database.
- Every step's decisions are appended to the audit trail.


5. THE APPLICATION: HOW PEOPLE USE THE RESULT

Map-first interface.
- A web map of the area on satellite, street, drone or plain background.
- Parcels are coloured by confidence status; conflict pins, building outlines, detected changes and utility lines are shown.
- A search box with instant suggestions finds plots, khata, property IDs, ULPIN-format IDs, owners (in Latin or Indian scripts), record numbers, buildings, coordinates and place names.
- One-click filters: conflicts, needs review, tax at risk, sale not mutated, encroachments, new construction, vacant, surveys disagree.
- Plain-language questions ("vacant plots over 200 sq m", "how many plots have a sale not mutated", "total tax at risk from floors beyond permission") are answered by an offline rule-based parser. The interpretation is shown to the user, and results can be exported.
- Map tools include measuring distance and area, selecting an area, locating the user, and a right-click "what's here" menu.

Plot card (the integrated record). Clicking a plot shows:
- its confidence score and the five components;
- owner, areas, khata / property ID, ULPIN-format ID;
- the sources that were fused;
- its conflicts, ownership chain (sale deeds against the RoR owner), official updates and topology fixes.

A "By department" section then gives one tab per department (Revenue, Survey, Municipal, Town Planning, Registration, Electricity, Water, Gas, Roads) with:
- that department's main details and automatic checks;
- its register entry and every linked record, with all the columns of the original source file;
- the conflicts routed to it, its official updates, and the fields it is the legal authority for.

Clicking a building shows its footprint, floors against the permission, height, outline sources, changes since the last survey and the connections inside it. Selecting an area gives a department-by-department summary with the plots to act on.

Review. Approvers see a queue of the disagreements routed to their department:
- every source's version side by side;
- decide one conflict or many at once, or undo a decision;
- every decision is written to the audit trail.

Governed record updates (maker–checker).
- An officer proposes a change to a field their department legally owns, for example owner and extent (Revenue), boundary (Survey), assessed use (Municipal), permitted floors (Town Planning) or tariff (Electricity). They give a reason and, where required, attach a supporting document.
- An approver of the same department, never the proposer (the four-eyes rule), approves or rejects it.
- A request is blocked if the record changed after it was made.
- Approved changes update the record, close the conflicts they settle, notify the affected departments, and are re-applied on every future pipeline run.
- The system itself drafts requests where it has evidence, for example an owner update from a registered sale deed. These still need an approver.

On-map editing. Survey staff can add a plot, correct a boundary, or map, correct or remove a building directly on the map. Tools include corner snapping, squaring, simplification, tracing an AI outline and saved drafts. Every edit goes through the same approval workflow and spatial checks.

Learning from reviewers. When reviewers decide the same type of conflict the same way consistently (at least 8 decisions, at least 90% agreement), the system proposes a rule. An administrator must enable it, it can be switched off, and every conflict it settles says so.

Roles and access.
- Four roles: viewer, officer, approver, administrator.
- Every account belongs to one of nine departments or to the state level, with the department's own job titles (for example Village Administrative Officer / Revenue Inspector and Tahsildar in Revenue).
- Each department sees its own records in full; it sees other departments' records in full, as a summary, or not at all, according to a need-to-know matrix the administrator controls.
- The matrix is enforced on the server for the map, the plot card, search, questions, exports, the OGC API and the PDF report.
- Personal names are masked for read-only viewers.
- Passwords are stored with PBKDF2; sessions and API keys are stored only as hashes; repeated failed sign-ins are rate-limited.

Other screens:
- Overview: headline results and what the pipeline did.
- Data: files received, detected formats and coordinate systems, column mappings, georeferencing and co-registration reports, topology repairs, provenance of real versus simulated data.
- Changes, Accuracy (benchmarks and blind tests), Requests and department inboxes, Audit trail with chain verification.
- Administration: accounts, roles, API keys, department access, pipeline runs, data upload.
- Profile: personal details, password, sessions.

The administrator can upload department files and run the pipeline from the browser, watching each step's progress.


6. OUTPUTS AND INTEROPERABILITY

- Harmonised parcel records with status and confidence score.
- Conflict tickets with evidence and routing.
- Detected changes; georeferencing, co-registration and accuracy reports.
- Estimated property tax at risk.
- Per-plot PDF property report (with Indian-script text).
- Exports of any layer as GeoJSON, CSV, KML, GeoPackage or zipped Shapefile, and an ISO 19152 LADM-aligned GeoPackage of parcels, parties, rights and sources.
- A REST API (documented with OpenAPI) and an OGC API – Features service, so other systems can read the harmonised parcels.
- API keys, each tied to a role and department, for machine access.
- ULPIN-format parcel IDs: a 2-letter state code plus a geohash of the parcel's reference point. These are stable location keys until states issue official ULPINs.
- State-wide context: real cadastral parcel maps for six southern states and Puducherry (from state portals, via datameet) are shown as streamed vector tiles for context and inspection. They are not harmonised.


7. TECHNOLOGY

- Backend and pipeline: Python, FastAPI and Uvicorn.
- GIS: Shapely 2 (geometry, spatial index), pyproj (coordinate systems), pyogrio / GDAL (formats), GeoPandas (exports).
- Machine learning: scikit-learn (gradient-boosting matcher; nearest-neighbour corner merging).
- Computer vision: OpenCV.
- Text: RapidFuzz (fuzzy matching), indic-transliteration (Indian scripts).
- Numerics: NumPy and SciPy.
- Reports: ReportLab with HarfBuzz text shaping.
- Database: SQLite (accounts, runs, parcel and conflict index, decisions, change requests, notifications, access settings), JSON results and JSON-lines audit trails.
- Frontend: a single-page web application with Leaflet and PMTiles vector tiles, installable and able to show the last results offline.
- Deployment: runs locally with a one-click start script.
- Testing: 79 automated tests cover the algorithms, pipeline, API, access control and workflows.


8. WHAT IS REAL AND WHAT IS SIMULATED

Real government land records, GNSS surveys, drone surveys and utility ledgers are not openly available. So the prototype is demonstrated on five areas, and the application labels real and simulated data openly:
- A fully synthetic ward (Mangaluru) with a known answer key, including rendered drone orthophotos and surface models for two survey dates.
- Jayanagar, Bengaluru: real OpenStreetMap buildings, roads and landmarks and real Microsoft AI building footprints. The cadastral sheet (in the Kalianpur 1975 datum), revenue records with Kannada names, municipal tax layer, GNSS points, field visits, department records and elevation models are simulated on that real geography.
- Thiruvanmiyur, Chennai: real Tamil Nadu government cadastral parcels from two independent surveys (TNGIS and NCSCM) plus real footprints. Records are simulated on those real parcels.
- A blind test with a hidden random seed, and a teammate-edited dataset, both unseen when the rules were written.


9. RESULTS MEASURED SO FAR

All accuracy figures are measured against errors we planted in simulated records. They show that the logic works, not field performance.

Positional correction (error of old sheets against GNSS points, before and after):
- synthetic ward: 3.2 m to 0.4 m;
- Jayanagar Kalianpur sheet: 4.0 m to 0.5 m;
- Thiruvanmiyur: 6.0 m to 0.03 m. This figure is optimistic, because the simulated GNSS points there were placed using the same offset the system recovered.
- Mean boundary error of the harmonised parcels against the answer key: 0.13 m (synthetic) and 0.15 m (Jayanagar), against 1.7 m and 2.4 m for simply dragging the sheet into place.

Problem detection (precision / recall / F1 over all planted problem types):
- synthetic ward: 0.99 / 1.00 / 0.996;
- blind ward: 0.99 / 0.99 / 0.991;
- Jayanagar: 0.96 / 0.97 / 0.963;
- Thiruvanmiyur: 0.92 / 0.96 / 0.939.

Other results:
- Parcel-to-municipal matching: F1 1.00 on the synthetic and Jayanagar areas, 0.999 on Thiruvanmiyur.
- Department records linked correctly: 99.0–99.6%.
- Column mapping: 94–100% correct.
- Change detection: 16–17 of 17 planted changes found per area.
- Parcels finalised without manual GIS work: 80.8% (synthetic), 84.1% (Jayanagar), 79.1% (Thiruvanmiyur).
- Calibration: no parcel in the auto-approved band had an error; the parcels that did have errors were in the review band.
- Real-data comparison (no answer key needed): the two independent Tamil Nadu cadastral surveys were co-registered (a 2.0 m shift found) and compared. 905 of 937 parcels were paired, with a mean overlap of 82% and a median boundary offset of 0.57 m, and survey numbers agree on 99% of paired parcels. Parcels where the two surveys disagree are raised as conflicts for the Survey department.
- Speed: about 4 seconds for a 240-parcel ward, and under a minute for about 770–940 parcels with 8,000–12,000 source features, on a laptop.

Known weaknesses:
- The "floors beyond permission" check still raises false alarms where AI building outlines span plots (precision 0.33 in Thiruvanmiyur).
- The matching model's near-perfect validation score reflects synthetic training data; the model relies mainly on overlap features.


10. INNOVATION

- Disagreement is treated as information, not overwritten. Every conflict keeps every version with its source. Only a more reliable source may settle one automatically, and every automatic decision states its reason and can be reversed.
- GNSS-anchored georeferencing and layer co-registration, with the error measured before and after, replace manual "drag into place" alignment.
- Cross-department intelligence: checks that need two or more departments together, such as sales never mutated, services on untaxed plots, floors beyond permission and commercial tariffs on residential assessments, with estimated revenue impact.
- Multilingual record matching across Kannada, Tamil and English names and local area units.
- Governance built into the data flow: department authority per field, the four-eyes rule, a need-to-know access matrix, admin-approved learned rules and a tamper-evident audit trail.
- Honest evaluation: hidden answer keys, blind tests, versioned rules with benchmark history, and calibration of the confidence score.
- Standards-based outputs: OGC API – Features, ISO 19152 LADM, ULPIN-format keys.


11. BENEFITS FOR GOVERNMENT

- Less manual GIS work: most parcels are finalised automatically, and officers see only the genuine disagreements, each with its evidence.
- Consistent records across departments, kept consistent: approved corrections are re-applied on every run and other departments are notified.
- Revenue recovery: construction missing from the tax register and use mismatches are found, with an estimate of tax at risk.
- Faster mutation: registered sales not reflected in the RoR are found, and the correction is drafted from the deed for an approver.
- Accountability: every automatic and human decision is traceable to its evidence and person.
- Interoperability: standard exports and APIs let other departments and national programmes consume the harmonised parcels.
- Repeatability: new data is harmonised by re-running the pipeline, not by redoing the manual work.


12. LIMITATIONS AND FUTURE WORK

Current limitations:
- Land, department, GNSS and elevation data are simulated.
- Building extraction from drone imagery has only been run on rendered images. Real orthomosaics can be uploaded and displayed, but are not yet processed.
- There is no deep-learning model.
- The matching model is trained on synthetic data.
- CORS data streams are not processed; GNSS input is survey point files.
- There is no connection yet to NAKSHA or state land-record systems.
- The prototype runs as a single local application with SQLite, without HTTPS or single sign-on.

Planned next steps:
- A pilot with one urban local body and revenue office using real data.
- Connectors to state land-record, municipal, registration and utility systems.
- Deep-learning building and boundary extraction from real drone orthophotos.
- CORS / RTK data ingestion and a surveyor field application.
- Retraining the matcher and calibrating the confidence weights on reviewer-labelled real cases.
- A spatial database (PostGIS), a job queue and parallel processing of wards for city- and state-scale deployment in a government data centre, with HTTPS, single sign-on and two-factor authentication.
- Integration with NAKSHA outputs and official ULPIN.
