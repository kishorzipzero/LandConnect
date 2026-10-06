# Land Connect: SIH Demo Video Script (PS 26013)

This script was built from the actual app and rehearsed end to end on a copy of the real database. Every page, button, account and number below exists in the MVP.

**Tags used in this script:**
- **[DO NOT DEMONSTRATE]**: not implemented.
- **[PARTIAL]**: partly implemented; show only the part that works.
- **[FUTURE — DO NOT PRESENT AS IMPLEMENTED]**: planned only.

**The one rule for the narrator:** read numbers **from the screen**. The values below are what the app shows today. A live pipeline run can change timings slightly.

---

## 1. WHAT THE APP ACTUALLY HAS

**Sign-in:** one sign-in screen ("Sign in to Land Connect", Username, Password, **Sign in**). A first sign-in with a one-time password forces **Change your password**. Passwords are in `output/initial_credentials.txt`. Never show that file on screen.

**Layout (map-first, like Google Maps):**
- **Top-left:** search box with a **☰ menu**.
- **Under the search box:** quick-filter chips.
- **Left:** side panel with tabs.
- **Top-right:** area selector, **?** (shortcuts) and the round avatar (account menu: *Manage your profile*, *Change password*, *Sign out*).
- **Bottom-left:** **Layers** tile and **Legend**.
- **Bottom-right:** locate, show whole area (house icon), measure, select area, full screen, zoom.

**Side-panel tabs:** Overview · Review · Data · Changes · Accuracy · Requests · Audit · Admin (or "Access" for department approvers) · Profile (when opened) · Results (after a search).

**Areas (the selector at the top right):**
- *Ward 47, Mangaluru — synthetic benchmark*
- *Jayanagar, Bengaluru — real-world*
- *Thiruvanmiyur, Chennai — real TN cadastrals*
- *Blind test batch (hidden seed)*
- *Teammate blind test*

| Role (account) | Entry | Pages available | Main responsibility | Demo features |
|---|---|---|---|---|
| **Admin** (`admin`) | Sign in | All tabs + Admin + ☰ **Upload data** / **Run pipeline** | Data, pipeline runs, accounts, API keys, department access matrix | Overview, Data tab (formats, CRS, georeferencing), **Run pipeline**, Layers before/after, Accuracy, exports, Admin → Department access |
| **Revenue approver**, Tahsildar (`revenue.approver`) | Sign in | All tabs, "Access" instead of Admin | Approves revenue record changes; decides conflicts routed to Revenue | **Requests → Approve** a system-drafted owner mutation; **Review → Accept** a version; PDF report |
| **Revenue officer**, VAO / Revenue Inspector (`revenue.officer`) | Sign in | All tabs except Admin | Proposes changes to Revenue fields | Plot card → **Propose a change** (optional in the 7-min version) |
| Other departments (`<dept>.officer` / `.approver` / `.staff`) | Sign in | Same, scoped to their department | Their own fields; see others by need-to-know | Department tabs with 🔒 / "Summary only" badges (7-min version) |
| State approver (`reviewer`), state viewer (`viewer`) | Sign in | All tabs (viewer: names masked, read-only) | Oversight | Not needed for the video |

**The strongest demo uses two roles:**
- **Admin:** has the data and processing story (inputs → pipeline → harmonised map).
- **Revenue approver (Tahsildar):** has the government decision story (a registered sale the pipeline found → approve → record updated → plot verified).

Together they show *problem → sources → processing → harmonisation → validation → integrated record → government value*.

---

## 2. THE DEMO STORY (actual workflow)

**Area:** *Jayanagar, Bengaluru — real-world*. Real OpenStreetMap and Microsoft AI building footprints with simulated land records, and the app says so under **What is real?**

1. **Admin** sees the harmonised result:
   - 769 parcels from 15 department files in 6 formats;
   - 84.1% of parcels finalised without manual work.
2. **Admin** shows the inputs and what the system corrected: an old cadastral sheet in the Kalianpur 1975 datum, 4.02 m off, corrected to 0.50 m on GNSS points.
3. **Admin** shows **before → after** on the map, then runs the pipeline live.
4. **Admin** clicks **Sale not mutated** and opens plot **107/7B**:
   - the RoR (in Kannada) still names the seller, *Nagaraj Shetty*;
   - the tax register already names the buyer, *Shetty Mohan*;
   - registered deed JNR-1-55381-2026 proves the sale;
   - the score is capped at 69, "Needs review".
5. **Revenue approver** opens **Requests**. The system drafted the RoR update from the deed; the approver clicks **Approve**. The plot becomes **95, Verified by reviewer**, and both conflicts close.
6. **Revenue approver** decides one more disagreement in **Review** (plot **138/18**: RoR extent 297 m² vs surveyed 244 m²) by accepting the surveyed version.
7. **Final output:** the integrated plot card with its department tabs, the **PDF report**, the **audit trail** (chain intact) and **LADM export**.

**Why this story:** it uses only real, working features. It shows automation (the 11-step pipeline) and human control (maker–checker, review). The result is visible on screen, and it ends in a record a government office would use.

---

## 3. SCREEN-BY-SCREEN SCRIPT (recommended 5-minute version)

| Step | Time | Role | Page / screen | What must be visible | Exact action | What happens | Narration | Why it matters | Next |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 0:00–0:15 | Admin | Sign-in | "Sign in to Land Connect" card | Type `admin` + password (**blur or cut the password**), click **Sign in** | App opens on Jayanagar Overview | "Land records for the same plot sit in revenue, survey, municipal, utility and registration systems, and they rarely agree. Land Connect brings them together. We sign in as the state administrator." | Introduces the user and the problem | Overview |
| 2 | 0:15–0:45 | Admin | **Overview** tab, Jayanagar | Headline card **84.1%**; tiles: 769 Parcels harmonised · 211 Conflicts to review · Boundary error 0.15 m · Processing time · ₹12.6 lakh Property tax at risk a year · Audit chain Intact | Point the cursor at the tiles; click **What is real?** | Data tab opens at "What is real and what is simulated" | "This is Jayanagar in Bengaluru. The system combined fifteen files into 769 parcels. 84 percent of them needed no manual GIS work, and 211 real disagreements were left for officers. Building footprints and roads here are real open data; the land records are simulated on that real geography, because Indian land records are not openly downloadable. The app states this openly." | The headline result, plus honesty about the data | Data tab |
| 3 | 0:45–1:20 | Admin | **Data** tab | "Datasets received" (15 files; KML, CSV, Shapefile, GPX, GeoJSON, GeoJSON lines; each with its detected type and CRS); "Harmonisation": *Cadastral sheet vs GNSS control*, *Layer co-registration*, *Topology correction* | Scroll slowly through Datasets received → Harmonisation | – | "Each department's file arrives in its own format, language and coordinate system. The cadastral sheet is in the old Kalianpur 1975 datum, and after reprojection it is still 4 metres off. The system fits it onto 1,010 GNSS survey points and brings the error down to half a metre. It also found the municipal layer shifted by 1.3 metres and the AI building layer by 3.4 metres, and corrected both. Then it repaired the parcel topology: snapped corners, closed gaps, removed overlaps." | Shows ingestion, CRS and georeferencing, topology | Map |
| 4 | 1:20–1:50 | Admin | Map + **Layers** panel | Parcels over satellite imagery | Click **Layers** (bottom left) → tick **Cadastral, before correction** → zoom into one block (scroll) → untick it → close Layers | The old sheet appears offset from the buildings, then disappears | "Here is the before and after. This outline is the cadastral sheet as delivered, metres away from the buildings. The coloured parcels are the harmonised result: aligned to GNSS, with every parcel coloured by confidence. Green was approved automatically; red needs a human." | BEFORE → AFTER on one screen | Run pipeline |
| 5 | 1:50–2:20 | Admin | ☰ menu → **Run pipeline** overlay | "Running the integration pipeline" with the 11 steps ticking | Click **☰** → **Run pipeline** → wait (≈20–50 s; **cut the wait in editing, keep 3–4 s**) → click **View results** | Steps complete; results reload | "Everything we just saw is produced by one automated pipeline: ingest, attribute mapping, coordinate transformation, building extraction, topology, matching, change detection, conflict checks, confidence scoring, evaluation and publishing. It runs in under a minute for this ward." | Proves it is live processing, not a mock-up | Chip |
| 6 | 2:20–2:40 | Admin | Chip **Sale not mutated** → **Results** tab | Results list of plots (~45) highlighted in cyan on the map | Click the **Sale not mutated** chip | Results tab opens with the list and "Interpreted as …" chips | "One click asks a cross-department question: which plots were sold, with a registered deed, but the revenue record still shows the old owner? The registration and revenue records are linked automatically, by plot reference and GPS location." | Cross-department intelligence | Plot card |
| 7 | 2:40–3:15 | Admin | Plot card **107/7B** | Status "Needs review", score 69 "Capped from … while conflicts are open"; Owner rows: RoR owner ನಾಗರಾಜ್ ಶೇಟ್ಟಿ, Municipal owner *Shetty Mohan*; **By department** tabs | Type `107/7B` in the search box → click the suggestion **Plot 107/7B** → scroll to **By department** → click **Registration**, then **Revenue** | The Registration tab shows deed JNR-1-55381-2026 (Nagaraj Shetty → Mohan Shetty); the Revenue tab check says "RoR still shows the seller of the latest deed: mutation pending" | "Plot 107/7B. The revenue record, in Kannada, still names Nagaraj Shetty. The tax register already has Mohan Shetty. The registration department's deed shows the sale. The system matched these names across scripts and spellings, capped the confidence at 69 and flagged it. It did not change the record by itself." | The integrated record plus a conflict with its evidence | Switch user |
| 8 | 3:15–3:30 | → Revenue approver | Avatar → **Sign out** → sign in as `revenue.approver` | Avatar menu shows "Tahsildar" | Click avatar → **Sign out** → sign in (blur password) | App opens; tab reads "Access" instead of Admin | "Changing a land record is the revenue department's decision. We sign in as the Tahsildar, the revenue approving authority." | Role-based access | Requests |
| 9 | 3:30–4:00 | Revenue approver | **Requests** tab | "Revenue Department (RTC / RoR): inbox", then the change requests list with "Awaiting approval"; the request for **107/7B**: *Owner (RoR) → Mohan Shetty*, reason "Registered sale JNR-1-55381-2026 dated 2026-04-27 …", proposed by system | Find the 107/7B request (**Show on map** is optional) → click **Approve** | Request approved; the RoR owner is updated; conflicts close | "The system has already drafted the correction from the registered deed, but it waits for a human. The Tahsildar checks the deed and approves. The same person can never propose and approve a change: that is the four-eyes rule." | Maker–checker governance | Plot card |
| 10 | 4:00–4:15 | Revenue approver | Plot card **107/7B** again | Status **Verified by reviewer**, score **95**; RoR owner *Mohan Shetty*; "Official updates" entry | Type `107/7B` → click suggestion **Plot 107/7B** | Card shows the new state | "The record is updated, both conflicts are closed, the plot is now verified at 95, and the other departments are notified." | AFTER: integrated, validated record | Review |
| 11 | 4:15–4:35 | Revenue approver | **Review** tab | Filter chips; item "Recorded extent differs from surveyed area · 138/18" | Click **Revenue only** → click the **138/18** item → click **Accept** on "244.0 m² · Surveyed boundary (fused)" | The item moves to Decided; 138/18 becomes Verified | "When the system can't settle a disagreement itself, it shows both versions side by side. Here the RoR says 297 square metres but the survey measures 244. The approver accepts the surveyed value, and the decision is written to the audit trail." | Human validation of conflicts | Final output |
| 12 | 4:35–5:00 | Revenue approver | Plot card 107/7B → **PDF report**; then **Audit** tab | The PDF opens or downloads; Audit chain status "intact" | Open 107/7B (search suggestion) → **PDF report** (show the first page for 3 s) → **Audit** tab → type `107/7B` in the filter | The approval entry is listed; the chain is verified | "The result is one trustworthy record for each plot, with every department's data, its confidence, and a tamper-evident history of every automatic and human decision. It can be exported to GIS formats, the OGC API or an ISO land-administration package." | Final integrated output | End |

---

## 4. ROLE ORDER

| Question | Answer (actual app) |
|---|---|
| First role | **Admin** |
| Data upload | **Admin** (☰ → **Upload data**). For the video, data is already loaded; show the dialog only in the 7-min version. |
| Processing | **Admin** (☰ → **Run pipeline**) |
| Validation | **Revenue approver** (Requests → Approve; Review → Accept). The system validates automatically inside the pipeline (conflict checks, confidence caps). |
| View integrated data | Both (plot card, department tabs) |
| Reports | **Revenue approver** (PDF report needs approver or admin); **Admin** (exports, LADM) |

**Transition line:** "Changing a land record is the revenue department's decision, so we sign in as the Tahsildar."

---

## 5. EXACT PAGE ORDER

**ROLE 1: ADMIN**
1. **Sign-in:** username `admin` → **Sign in**. *Visible:* Overview of the last area.
2. **Area selector** (top right): choose *Jayanagar, Bengaluru — real-world* if it isn't selected. *Result:* the map flies to Jayanagar.
3. **Overview** tab. *Visible:* 84.1% headline and the tiles. *Click:* **What is real?**
4. **Data** tab. *Visible:* "What is real and what is simulated", "Datasets received", "Harmonisation". *Click:* nothing; scroll.
5. **Layers** tile → tick **Cadastral, before correction** → zoom in on a block → untick → **×**.
6. **☰** → **Run pipeline**. *Visible:* the 11 steps. *Click:* **View results** when done.
7. Chip **Sale not mutated**. *Visible:* Results tab and highlighted plots.
8. Type `107/7B` in search → click suggestion **Plot 107/7B**. *Visible:* plot card; scroll to **By department**; click **Registration**, then **Revenue**.
9. Avatar → **Sign out**.

**ROLE 2: REVENUE APPROVER**
1. **Sign-in:** `revenue.approver` → **Sign in**.
2. **Requests** tab → the 107/7B request → **Approve**.
3. Type `107/7B` → click suggestion **Plot 107/7B**. *Visible:* **Verified by reviewer, 95**.
4. **Review** tab → **Revenue only** → click **138/18** → **Accept** on "244.0 m²".
5. Open 107/7B again (search suggestion) → **PDF report**.
6. **Audit** tab → filter `107/7B`.

---

## 6. WHAT TO SHOW / HIDE

**Show:**
- Overview tiles and headline.
- Data-tab file list with formats and CRS; georeferencing RMSE; co-registration shifts.
- Satellite map with coloured parcels, conflict pins and building outlines.
- The "Cadastral, before correction" layer.
- Pipeline step list.
- Results list.
- Plot card: owner rows, score bar, **By department** tabs with records and checks.
- Requests list and **Approve**.
- Review item with both versions and **Accept**.
- PDF report; Audit tab with chain status.

**Hide or avoid:**
- Passwords (blur, or cut the typing).
- `output/initial_credentials.txt`.
- Browser tabs and bookmarks bar (use full screen: F11).
- DevTools; the terminal; code.
- The `localhost` address bar (hidden by F11).
- The Windows taskbar and notifications (turn on Focus Assist).
- Unrelated folders.

**Do not open:**
- The Upload dialog without files.
- Any area you haven't rehearsed.

---

## 7. NARRATION

The full spoken text is in §17. Speak calmly and at a normal pace.

**Avoid these words:**
- "AI does everything";
- "deep learning";
- "real government data" (say "real building and road data; simulated land records").

---

## 8. THE AI / GIS MOMENT

**Where:** the **Data** tab (Harmonisation), backed up by **Layers → Cadastral, before correction** and the **Run pipeline** step list.

| | |
|---|---|
| **Show** | "Cadastral sheet vs GNSS control" rows *Reprojected from the declared CRS 4.02 m* → *Refined on 1010 GNSS points 0.50 m*, "Layer co-registration" (municipal 1.32 m; AI footprints 3.43 m; applied), "Topology correction" counts, the before/after layer |
| **Click** | Layers → tick / untick **Cadastral, before correction** |
| **What changes** | The misaligned original sheet appears over the aligned, harmonised parcels |
| **What the algorithms do** | 1. Detect each file's coordinate system. 2. Reproject to UTM 43N. 3. Least-squares **affine fit** of the sheet onto GNSS points, iterating nearest-corner pairing and rejecting outliers. 4. **Co-registration** finds whole-layer shifts (median displacement; best building-inside-plot fit). 5. **Topology** snaps corners, merges shared corners, fixes overlaps and gaps. 6. A **gradient-boosting classifier** (scikit-learn), trained on synthetic wards, matches survey plots to municipal properties from overlap and shape features. Conflict checks and confidence scores are **rule-based**. |
| **What to say** | "The positioning is classical geodesy: an affine fit on GNSS points with outlier rejection. The record matching uses a machine-learning classifier trained on synthetic wards. The conflict checks and confidence score are transparent rules, so an officer can see exactly why a plot was flagged." |

**Do not claim:**
- deep learning [DO NOT DEMONSTRATE];
- drone-based building extraction on real imagery [PARTIAL: synthetic ward only];
- real DSM (Jayanagar heights are simulated, marked "sim").

---

## 9. BEFORE → AFTER

| | Screen | What to show |
|---|---|---|
| **Before** | Data tab "Datasets received" + Layers → **Cadastral, before correction** | 15 separate files in 6 formats and several CRSs; the sheet 4 m off the buildings |
| **Process** | ☰ → **Run pipeline** overlay | The 11 steps completing |
| **After** | Map (Parcels by confidence) + plot card with **By department** | One aligned parcel fabric; one record per plot combining RoR, municipal, registration, utilities and planning |
| **Before → after (record)** | Plot 107/7B before and after the approval | 69 "Needs review", RoR = seller → 95 "Verified by reviewer", RoR = buyer |

---

## 10. FINAL RESULT (hold 5–6 seconds)

**Screen:** plot card **107/7B** after approval: **Verified by reviewer · 95**, owner Mohan Shetty, "Official updates: Owner (RoR) → Mohan Shetty · CR-… · approved by revenue.approver", the **By department** row visible, and the parcel highlighted on the satellite map.

**What has changed:**
- The RoR owner has been updated from a registered deed.
- Two conflicts are closed.
- The confidence went from 69 to 95.
- Other departments were notified.

**What is integrated:** survey boundary (GNSS-aligned), RoR, municipal tax record, registration deed, electricity, water and building permission, all on one card.

**Final narration:** "One plot, one record, agreed by every department, with the evidence and an audit trail behind every change. That is what Land Connect gives a land records office: less manual GIS work, and records that stay consistent."

---

## 11. TRANSITIONS

- **Overview → Data:** "Let's look at what came in."
- **Data → Layers:** "Here is what that correction looks like on the ground."
- **Layers → Run pipeline:** "All of this comes from one automated run."
- **Run → Chip:** "Now let's ask the data a question a revenue office cares about."
- **Chip → Plot:** "Take one of these plots."
- **Plot → Sign out:** "The system has found the problem. Fixing the record is the revenue department's call."
- **Requests → Plot:** "Let's see what changed."
- **Plot → Review:** "Not everything comes with a deed attached."
- **Review → PDF/Audit:** "And everything we did is on the record."

---

## 12. 3-MINUTE VERSION

| Time | Role | Page | Action | Narration |
|---|---|---|---|---|
| 0:00–0:15 | Admin | Sign-in → Overview | Sign in | "Land records for one plot sit in six or more departments and don't agree. Land Connect harmonises them." |
| 0:15–0:40 | Admin | Overview (Jayanagar) | Point at the headline and tiles | "Fifteen department files became 769 parcels. 84 percent needed no manual GIS work; 211 disagreements were left for officers. Footprints and roads are real open data; the land records are simulated." |
| 0:40–1:10 | Admin | Data tab → Layers | Scroll Harmonisation; tick **Cadastral, before correction**, then untick | "The old sheet was 4 metres off. Fitted to GNSS points, it's now half a metre. Here's before and after." |
| 1:10–1:30 | Admin | Chip **Sale not mutated** | Click | "One click finds plots sold by registered deed but still in the seller's name in the RoR." |
| 1:30–2:00 | Admin | Plot 107/7B | Search, open, click the **Registration** tab | "The RoR in Kannada says Nagaraj Shetty; tax and the deed say Mohan Shetty. Confidence is capped at 69 and the plot is flagged, not changed." |
| 2:00–2:40 | Revenue approver | Sign out/in → **Requests** → **Approve** → plot 107/7B | Approve, then reopen the plot | "The Tahsildar approves the system-drafted correction. The plot is now verified at 95, with both conflicts closed." |
| 2:40–3:00 | Revenue approver | **Audit** tab (filter 107/7B) | Show chain status | "Every automatic and human decision is on a tamper-evident audit trail. One plot, one agreed record." |

---

## 13. 5-MINUTE VERSION (recommended)

Use **§3** exactly as written.

---

## 14. 7-MINUTE VERSION (adds technical depth)

§3 steps 1–12, plus these inserts:

| Insert after | Time | Role | Page | Action | Narration |
|---|---|---|---|---|---|
| Step 3 | +0:30 | Admin | Data tab "Datasets received" → the column mappings of *rtc_bhoomi_extract.csv* (the extent column recognised as area in guntas) | Point at it | "Columns are mapped by their names and by their values, so 'Extent in guntas' becomes square metres and a column called OCCUPANT is recognised as the owner." |
| Step 5 | +0:30 | Admin | **Accuracy** tab | Show "All problems, F1" and the Blind tests section | "Because the land records are simulated, we planted known errors and measured detection: F1 0.96 here, and 0.99 on a blind ward the rules had never seen. These numbers are against planted errors, not field data." |
| Step 7 | +0:40 | Admin | Plot 107/7B → click a building → then **A** and drag a rectangle | Building block (orange); area summary with department tabs | "Clicking a building shows its footprint, floors and permission check. Selecting an area gives a department-by-department summary: RoR coverage, tax at risk, connections." |
| Step 8 | +0:30 | Admin | Area selector → *Thiruvanmiyur, Chennai — real TN cadastrals* → chip **Surveys disagree** | Show the results | "Here the parcels are real Tamil Nadu government maps from two independent surveys. The system aligned them and found where they disagree: a real result, with no simulated answer key." |
| Step 10 | +0:30 | `electricity.officer` (optional) | Plot card → **Registration** tab (🔒) and **Water** tab ("Summary only") | Show the badges | "Each department sees its own records in full and others only as the access policy allows. The electricity office cannot see sale deeds." |
| End | +0:20 | Admin | **Data** tab → Export → **LADM package (ISO 19152)** | Click (download starts) | "The harmonised parcels export to GIS formats and an ISO 19152 land-administration package, and they're also served through an OGC API." |

---

## 15. RECORDING CHECKLIST

**One-time preparation (do this once, before any rehearsal):**
1. Start the app (`run.bat`), or make sure the server is running.
2. In a terminal in the project folder, run
   `.venv\Scripts\python.exe -m landconnect.demo_prep jayanagar`.
   - It retires 45 older system-drafted requests that cite deeds from an earlier version of the data. They are marked "stale", nothing is deleted, and each change is audited.
3. Sign in as **admin** → area **Jayanagar** → ☰ → **Run pipeline** → wait for **View results**.
   - This drafts fresh requests from the current deeds. The request for 107/7B will say *"Registered sale JNR-1-55381-2026 dated 2026-04-27: Nagaraj Shetty → Mohan Shetty"*.
4. Check in **Requests** (as `revenue.approver`) that the 107/7B request is **Awaiting approval**. **Do not approve it yet.**

**Before each take:**
- **Accounts:** `admin` and `revenue.approver`. Have the passwords from `output/initial_credentials.txt` ready off-screen.
- **Browser:** Chrome, one tab, full screen (F11), zoom 100%, window at least 1440 px wide; Windows notifications off.
- Sign out of every account before the take starts.
- **Pre-test:** Overview, Data, Layers, Run pipeline, chip, 107/7B card, Requests, Review (138/18), PDF report, Audit.

**Slow steps:**
- Run pipeline: about 20–50 s. Cut it in editing and say "(sped up)" or show a caption.
- The first map load of satellite tiles takes a second or two.

**Approvals can't be undone for a retake:**
- **107/7B:** approve only in the final take. For rehearsals, use backup plots with the same story: **109/6A** (Kavitha Kumar → Pooja Kumar), **110/7** (Asha Patil → Shobha Swamy) or **103/3**.
- **138/18:** Review decisions *can* be undone (the item's **Undo decision** button). Backup: **120/14** (RoR 1,088 m² vs surveyed 903 m²).

**Backups to keep ready:** screenshots of the Overview, the 107/7B card before and after, the Requests list, and the Data-tab Harmonisation section.

**Must NOT appear:**
- passwords or the credentials file;
- the terminal or DevTools;
- error toasts (retake instead);
- the upload dialog with test junk;
- other people's data.

---

## 16. OPERATOR CHEAT SHEET (keep beside the keyboard)

1. F11 full screen. Open the app → **Sign in** as `admin`.
2. Top-right area selector: **Jayanagar, Bengaluru — real-world**.
3. **Overview**: hover the tiles → click **What is real?**
4. **Data**: scroll to **Harmonisation** (slowly).
5. **Layers** → tick **Cadastral, before correction** → scroll-zoom into a block → untick → **×**.
6. **☰** → **Run pipeline** → wait → **View results**.
7. Chip **Sale not mutated**.
8. Type `107/7B` → click suggestion **Plot 107/7B** (Enter alone only lists it) → scroll to **By department** → **Registration** → **Revenue**.
9. Avatar → **Sign out** → sign in as `revenue.approver`.
10. **Requests** → 107/7B request → **Approve**.
11. Type `107/7B` → click suggestion **Plot 107/7B** (show **Verified · 95**; hold 3 s).
12. **Review** → **Revenue only** → click **138/18** → **Accept** on **244.0 m²**.
13. Type `107/7B` → click suggestion → **PDF report**. Chrome downloads it; click the download to open it (show page 1 for 3 s), then return to the app tab.
14. **Audit** → type `107/7B` → hold on the list + chain status (5 s).

---

## 17. NARRATOR CHEAT SHEET

1. "Land records for the same plot sit in revenue, survey, municipal, utility and registration systems, and they rarely agree. Land Connect brings them together. We sign in as the state administrator."
2. "This is Jayanagar in Bengaluru. The system combined fifteen department files into 769 parcels. 84 percent needed no manual GIS work, and 211 real disagreements were left for officers. The buildings and roads are real open data. The land records are simulated on that real geography, because they aren't openly available, and the app says so right here."
3. "Every department's file arrives in its own format, language and coordinate system. The cadastral sheet uses the old Kalianpur datum and is still four metres off after conversion. Using 1,010 GNSS survey points, the system fits it to within half a metre. It also found the municipal layer and the AI building layer shifted as a whole, and corrected them, then repaired the parcel topology."
4. "Here's the before and after. This outline is the sheet as delivered, metres away from the buildings. The coloured parcels are the harmonised result, coloured by confidence: green was approved automatically, red needs a person."
5. "All of this comes from one automated run: ingest, attribute mapping, coordinate transformation, building extraction, topology, matching, change detection, conflict checks, confidence scoring and publishing. For this ward it takes under a minute."
6. "Now a question a revenue office cares about: which plots were sold with a registered deed, but still show the seller in the RoR? The system answers it by linking registration and revenue records by plot reference and GPS."
7. "Plot 107/7B. The RoR, in Kannada, still says Nagaraj Shetty. The tax register and the registered deed say Mohan Shetty. The system matched those names across scripts, flagged the plot and capped its confidence at 69. It did not change the record by itself."
8. "Changing a land record is the revenue department's decision. We sign in as the Tahsildar."
9. "The system has already drafted the correction from the deed, and it's waiting for approval. The Tahsildar approves. The person who proposes a change can never approve it."
10. "The RoR now names Mohan Shetty. Both conflicts are closed and the plot is verified at 95."
11. "Where the evidence isn't decisive, the approver sees both versions side by side. The RoR says 297 square metres; the survey measures 244. The approver accepts the surveyed value."
12. "Every plot now has one record with every department's data and a confidence score. There's a report for the office, and a tamper-evident history of every automatic and human decision. That's less manual GIS work, and land records that stay consistent."

---

## 18. IF SOMETHING FAILS

| Live action | Possible failure | Prepare beforehand | Backup | What the narrator says |
|---|---|---|---|---|
| Sign in | Wrong password / "Too many failed sign-ins" (5 in 5 min) | Test both accounts right before recording | Wait 5 minutes, or restart the server | Retake; say nothing on camera |
| Run pipeline | Slow (> 60 s) or fails | Run it once before recording; nothing else running on the laptop (close OneDrive sync if possible) | Skip the run; the result already exists. Show the Data tab step log instead | "The results you've seen were produced by this pipeline; here is its step log." (true, because the result comes from an earlier run) |
| 107/7B request missing | demo_prep or the admin run not done | Checklist steps 2–3 | Use 109/6A, 110/7 or 103/3 | Same narration with that plot's names |
| Approve error "stale" | The record changed after drafting | Don't edit 107/7B before the take | Use a backup plot | Retake |
| Review item not shown | The filter is on "Decided" or another severity | Click **Open** and **All** first | 120/14 | Retake |
| Map tiles don't load | No internet (Esri tiles) | Check the connection | Layers → **None** (parcels still show) | "The basemap needs the internet; the parcels are ours." |
| PDF report | Slow to generate | Generate it once before | Skip to Audit | none needed |

**Never show a result that didn't happen.** If a step fails, retake it or skip it.

---

## 19. FINAL RECOMMENDED SEQUENCE

**ADMIN**
1. **Sign in** → "Land records for the same plot sit in many departments and rarely agree. Land Connect brings them together."
2. **Overview (Jayanagar)** → "Fifteen files, 769 parcels, 84 percent finished without manual GIS work, 211 disagreements left for officers."
3. **Data → Harmonisation** → "The old cadastral sheet was four metres off; fitted to GNSS points, it's within half a metre."
4. **Layers → Cadastral, before correction** → "Before: the sheet as delivered. After: the harmonised parcels, coloured by confidence."
5. **☰ → Run pipeline (processing)** → "One automated run: mapping, alignment, topology, matching, checks and scoring."
6. **Chip: Sale not mutated** → "Which plots were sold by registered deed but still show the seller in the RoR?"
7. **Plot 107/7B (result)** → "The RoR says Nagaraj Shetty; tax and the deed say Mohan Shetty. The plot is flagged, not changed."

**REVENUE APPROVER (Tahsildar)**

8. **Sign in** → "Fixing the record is the revenue department's decision."
9. **Requests → Approve (validation)** → "The system drafted the correction from the deed; the Tahsildar approves it. Four eyes, always."
10. **Plot 107/7B** → "Verified at 95; the record is updated and the conflicts are closed."
11. **Review → 138/18 → Accept** → "Where evidence isn't decisive, a person chooses, and the choice is recorded."
12. **PDF report + Audit (final output)** → "One agreed record per plot, with every decision traceable. Less manual GIS work, consistent land records."

**Do not present these as working:**
- Real drone ORI processing on real imagery, and deep-learning extraction [FUTURE — DO NOT PRESENT AS IMPLEMENTED].
- Real DSM/DTM: Jayanagar heights are simulated [PARTIAL].
- CORS streaming [FUTURE].
- NAKSHA integration and official ULPIN issuance [FUTURE] (the app shows *ULPIN-format* IDs only).
- Cloud / HTTPS deployment [FUTURE].
