# Land Connect: end-to-end workflows

This document describes, step by step, what each role does in Land Connect and how the roles work together. Screen and button names are exactly as they appear in the app (http://127.0.0.1:8000).

Contents
1. [Roles and who grants them](#1-roles-and-who-grants-them)
2. [What each role can do](#2-what-each-role-can-do)
3. [Getting in (all roles)](#3-getting-in-all-roles)
4. [Viewer workflows](#4-viewer-workflows)
5. [Officer workflows](#5-officer-workflows)
6. [Approver workflows](#6-approver-workflows)
7. [Admin workflows](#7-admin-workflows)
8. [End-to-end scenarios across roles](#8-end-to-end-scenarios-across-roles)
9. [Rules that apply everywhere](#9-rules-that-apply-everywhere)
10. [What is recorded](#10-what-is-recorded)

---

## 1. Roles and who grants them

| Role | Who it is | Department | Granted by |
|---|---|---|---|
| **Viewer** | Another agency, a citizen-facing portal, an auditor who only reads | One department or all | State admin, or the approver of that department |
| **Officer** (maker) | Staff who keep a department's records up to date | One department | State admin, or the approver of that department |
| **Approver** (checker) | Senior staff who approve changes and settle conflicts | One department, or all (state approver) | State admin only |
| **Admin** (state admin) | Runs the system: data, pipeline, accounts, keys, rules | All | State admin only |

The nine departments: Revenue, Survey, Municipal (property tax), Town Planning, Registration (IGR), Water, Electricity, Gas, Roads.
Demo accounts: `admin`, `reviewer` (state approver), `viewer`, and for each department `<dept>.officer`, `<dept>.approver` and `<dept>.staff` (read-only), for example `revenue.officer`, `survey.approver`, `gas.staff`. Passwords are in `output/initial_credentials.txt`.

**Department job titles.** The permission level is the role; each department calls it by its own title (shown in the account menu, profile and account list):

| Department | Viewer (staff) | Officer (maker) | Approver (checker) |
|---|---|---|---|
| Revenue | Revenue clerk | Village Administrative Officer / Revenue Inspector | Tahsildar |
| Survey | Survey draughtsman | Surveyor | Assistant Director of Land Records |
| Municipal | Tax section clerk | Tax Inspector | Assistant Revenue Officer |
| Town Planning | Planning assistant | Junior Town Planner | Assistant Director of Town Planning |
| Registration | Registration clerk | Registration assistant | Sub-Registrar |
| Water | Billing clerk | Assistant Engineer | Executive Engineer |
| Electricity | Billing clerk | Section Officer (AE) | Assistant Executive Engineer |
| Gas | Customer care executive | Field engineer | Area manager |
| Roads | Works clerk | Assistant Engineer (Roads) | Executive Engineer (Roads) |

**Department access (who sees whose records).** Every department sees **its own records in full**: every source column, names, amounts and meter numbers. What it sees of another department's records is set in a matrix:
- **full:** everything.
- **summary:** that the record exists, with its number, category, status and link method. No names, amounts or source columns.
- **none:** the department's records are hidden.

Defaults follow need-to-know. For example:
- Revenue sees Survey and Registration in full.
- Municipal sees Revenue, Town Planning and Registration in full.
- Town Planning sees Survey and Municipal in full.
- Utilities and Roads do not see registered deeds at all.
- Everything else is summary.

The state admin changes any cell in **Admin → Department access**; every change is written to the audit trail. The rule is applied on the server to everything a department account receives: the map, the plot card, search suggestions, questions, exports, the OGC API and the PDF report. The plot's **common information** (boundary, area, land use, owner, khata / PID, ULPIN, confidence, conflicts) stays shared with everyone, as before. State-level accounts and admins see everything. Viewers additionally see personal names masked.

## 2. What each role can do

| Capability | Viewer | Officer | Approver | Admin |
|---|:-:|:-:|:-:|:-:|
| See the map, parcels, conflicts, changes, accuracy, audit | ✓ (names masked) | ✓ | ✓ | ✓ |
| Ask questions, search, export open data, OGC API | ✓ (names masked, no owner search) | ✓ | ✓ | ✓ |
| Read and clear own department's inbox | | ✓ | ✓ | ✓ |
| Propose a record change for own department | | ✓ | ✓ | ✓ (any department) |
| Add a plot, map a building, correct or remove one, edit a boundary | | Survey officers | Survey approvers | ✓ |
| Decide conflicts routed to own department (single or bulk) | | | ✓ | ✓ |
| Approve or reject change requests of own department (single or batch) | | | ✓ (never own request) | ✓ (never own request) |
| Confirm uncertain column mappings | | | ✓ | ✓ |
| Download the PDF property report | | | ✓ | ✓ |
| Create officers and viewers for own department, reset their passwords | | | Department approvers | ✓ (any account) |
| Upload data, run the pipeline, reset the demo | | | | ✓ |
| Issue and revoke API keys, switch learned rules on or off | | | | ✓ |

---

## 3. Getting in (all roles)

**First sign-in with a one-time password**
1. Open the app and enter the username and the one-time password you were given.
2. The **Change your password** screen opens and nothing else works until you finish it. Enter the one-time password, then a new one of at least 10 characters, twice.
3. Sign in again with the new password.

**Everyday sign-in:** enter username and password. After 5 wrong attempts in 5 minutes, sign-in from that address is paused.

**Finding your way around the screen** (it works like Google Maps):
- **Search box (top left):** type a plot number, owner, record number, ULPIN, place, coordinates, or a question such as "vacant plots over 200 m²". Press `/` or `Ctrl K` to jump to it.
- **☰ menu (in the search box):** switch areas, open any tab, upload data, run the pipeline, and see the keyboard shortcuts.
- **Chips under the search box:** one-click filters: Conflicts, Needs review, Tax at risk, Sale not mutated, Encroachments, New construction, Vacant, and Surveys disagree where there is a second survey. Click the same chip again to clear it.
- **Side panel (left):** the tabs and the plot card. The small handle on its edge, or `[`, hides and shows it. On a phone it is a sheet at the bottom; tap an empty part of the map to lower it.
- **Layers tile (bottom left, or `L`):** map type (Drone, Satellite, Google, Map, None) and the layers.
- **Drone map type:** shows the area's drone orthomosaic, with satellite imagery around it. Where two surveys exist, a year switch gives before / after. An area with no drone survey shows how to add one: an admin uploads the orthomosaic as an RGB GeoTIFF (**Upload data**; for example `ortho_2026.tif`, or two files with years for before / after) and runs the pipeline.
- **Buttons (bottom right):** your location, show the whole area, measure (`M`), full screen (`F`), and zoom.
- **Right-click the map:** copy the coordinates, see what plot is there, measure from that point, add a new plot there (survey staff and admins), or copy a link to that place.
- **Web address:** it always holds the area and the map position, so a reload or a shared link opens the same view.

**A plot, a building or an area, department by department:**
- **Click a plot:** the card shows the common information, then a **By department** row: Revenue, Survey, Municipal, Town Planning, Registration, Electricity, Water, Gas and Roads. It opens on your own department. Each tab has:
  - that department's main details (for example khata, RoR owner, extent vs. surveyed area; PID, assessed use and area, ward, tax at risk; permitted floors vs. measured floors; deeds with parties and consideration; RR numbers, tariff and load; water meters);
  - its checks (✓ / ? / !);
  - its register entry and every record with **all the columns of the source file**;
  - the conflicts routed to it, its official updates, and the fields it is the authority for. Officers of that department can click a field to propose a change.
  A badge says whether you see the tab in full, as a summary, or not at all (🔒).
- **Click a building:** the building is outlined in orange and a **Selected building** block opens on its plot's card. It shows the footprint, floors and height against the permission, the outline sources and their agreement, any change since the last survey, and the connections inside it.
- **Select an area:** press `A`, the dashed-square button, or right-click → **Select an area**, then drag a rectangle. The card shows the plots, status, owners, buildings and open conflicts in the area, then a department tab row with each department's counts. For example: RoR coverage and extent mismatches; GNSS corners; assessed plots and tax at risk; permissions and buildings; deeds and total consideration; connections by category. Each tab also lists the open conflicts by type and the plots to act on. Export the plots as CSV, or show them as search results.

**Your profile:** click your round initial at the top right. The menu gives **Manage your profile**, **Change password** and **Sign out**. The profile page has these sections:
- **Personal details:** full name, designation, employee ID, office, official e-mail, mobile (10 digits) and preferred language. Your username never changes, because it is what the audit trail records.
- **Password:** change it with a live strength check. The rules are at least 10 characters, not your username, at least two of letters, digits and symbols, not a common password, and different from the current one. Changing it signs you out on every device.
- **Where you are signed in:** each device, its address and when it was last active. Sign out one device, or **Sign out all other devices**.
- **Access:** your role and department, who granted them and when, what you can do, and who can change your access (never you).
- **Preferences:** the area, basemap and tab to open at sign-in, on any device.
- **Your work and recent activity:** changes you proposed, requests and conflicts you decided, and your last actions from the audit trail.

Every profile change is recorded in the access log.

**Systems instead of people** (for example the DISCOM's billing system pulling parcel data): an admin issues an **API key** tied to a role and a department (see M4). The key is sent in the `X-API-Key` header and behaves like an account of that role.

---

## 4. Viewer workflows

**V1. Look up a property**
1. Choose the area in the selector at the top (for example *Jayanagar, Bengaluru — real-world*).
2. Press **/** and start typing into the search bar. Suggestions appear as you type:
   - plot numbers, khata and property IDs, ULPIN
   - record numbers (electricity, water, deeds, permissions, CR-…), buildings
   - landmarks, other areas and coordinates
   - ready-made questions with how many they find

   Use ↑ ↓ and Enter, or type a full question such as *how many plots have a sale not mutated* or *total tax at risk from floors beyond permission*. The answer appears in one line above the list, and the results can be sorted, downloaded (CSV, GeoJSON) or shared as a link. The label at the right of the bar says **rules** (answered on the server with no AI service) or **Claude**.
   - A place outside the area (*Madurai*) flies the map there and switches on that state's real cadastral map.
3. Click a plot on the map. The plot panel shows:
   - the overall confidence score and its five components
   - status and area
   - the ULPIN-format ID
   - the sources fused, each tagged **real** or **simulated**
   - the department records, conflicts and ownership chain

   Owner and account-holder names appear masked, for example "P••• N•••".
4. Click **Decision history** to see every audit entry for the plot.

**V2. Understand what the data is**
- **Overview → What is real?** shows which files are real and which are simulated.
- **Changes** lists what changed between two surveys.
- **Accuracy** shows how reliable the results are, including blind tests.

**V3. Take data away**
- **Data → Export:** choose a layer and a format (GeoJSON, CSV, KML, GeoPackage, Shapefile). You can also download the **LADM package (ISO 19152)** or use the **OGC API – Features** link.
- Exports made by a viewer contain masked names.

**V4. See the real cadastral maps of South India**
1. **Layers → State-wide cadastral maps → Show all states.** Each state appears as a parcel-density map; **view all** next to a map zooms to that state.
2. Zoom in to district level. The real parcel lines appear (TNGIS for Tamil Nadu, KGIS for Karnataka, and so on).
3. Click a parcel to see its survey number, subdivision and village or district codes. These maps are for reference and are not merged into an area's harmonised records.

---

## 5. Officer workflows

**O1. Start of the day: the inbox**
1. Open **Requests**. The inbox at the top shows the open conflicts routed to your department, the requests awaiting approval, and unread notices.
2. A notice tells you that another department changed something your records must follow. For example: *"Revenue changed owner of plot 163/13 to 'Prakash Shetty' (CR-00060). Update your records."*
3. Update your own records (see O2), then click **Done** on the notice.

**O2. Propose a change to a record your department is responsible for**
1. Click the plot on the map, then **Propose a change**.
2. Choose the **Field**. Only your department's fields are listed. For example, Revenue has Owner (RoR), Recorded extent and Land classification; Municipal has Property ID and Assessed use.
3. Check the **Current value**, then enter the **New value** and a **Reason** of at least 10 characters (for example *Sale deed JNR-1-12345-2026 registered on 2026-03-02*).
4. Attach a **Supporting document** (PDF, JPG or PNG, up to 10 MB). This is required for owner, extent, land use, boundary, permitted floors, deed buyer and encroachment notices. The file's fingerprint (SHA-256) is recorded.
5. Click **Submit for approval**. You get a code such as **CR-00061**, and the request appears under **Requests → Awaiting approval**. The record itself has **not** changed yet.

**O3. Map a building that is missing** *(Survey)*
1. Click the plot, then **Map a building**. The editor opens on the satellite image.
2. Click each roof corner. A line follows the mouse, and corners snap to nearby plot and building corners (hold **Alt** to place freely).
   - Shortcut: press **T** (**Trace**) and click an AI-detected outline to copy it, then drag the whole shape to line it up with the image.
3. Double-click, or click the first corner, to close the shape. Then press **Q** (**Square**) to make the corners right angles, and adjust by dragging corners or edge dots.
4. Press **Save** (or Enter). The Save panel checks the size, the share inside the plot, and whether the building is a duplicate.
5. Enter the number of floors (optional) and a reason, then click **Submit for approval**.
6. The drawing shows on the map as an orange dashed outline (**Pending map proposals**) until it is approved.

**O4. Edit a plot boundary** *(Survey)*
1. Click the plot, then **Edit boundary**.
2. Drag corners, drag an edge dot to add a corner, and right-click a corner to delete it.
   - **Simplify** removes corners that change the outline by less than 30 cm.
   - **Trace** can copy the municipal or georeferenced cadastral outline.
3. **Save**. The checks show any overlap with neighbouring plots and the change in area. A document, such as the GNSS survey sketch, is required.
4. **Submit for approval.**

**O4b. Add a new plot, including one outside the mapped area** *(Survey)*
1. Pan the map to the land. The satellite image covers everywhere, not only the mapped area.
2. Click **+ New plot** (under the basemap buttons).
3. Click each corner of the plot (wall, fence or survey stones), double-click to close, and press **Q** to square the corners if it is rectangular.
4. **Save.** The checks confirm the size and that no existing plot is overlapped.
5. Enter the **New plot number** (a free number next to the nearest plots is suggested, for example 165/5), a reason and the survey sketch, which is required. Then **Submit for approval**.
6. After a **Survey approver** approves it, the plot appears on the map. It starts in **Needs review**, with a "no revenue record" conflict routed to Revenue, and Revenue, Municipal and Town Planning are notified.
7. Now use **Map a building** on the new plot (O3).

**O5. Correct or remove a building** *(Survey)*
- Click the plot. Under **Buildings and map changes**, click **Edit** to reshape a building or change its floors, or **Remove** for a demolished or wrongly mapped one. Then save and submit as above.

**O6. Not finished? Save a draft**
- **Save draft** keeps the drawing in this browser; it is also saved automatically as you work.
- Resume it from the plot (**Resume**) or from **Requests → Unsent map drafts on this device**. **Cancel** discards it.

**O7. Follow up**
- **Requests → Awaiting approval / Approved / Rejected** shows your requests with before and after, the approver, the date and the note.
- **Withdraw** takes back a request that has not been decided yet.

---

## 6. Approver workflows

**A1. Work the review queue (one conflict at a time)**
1. Open **Review**. Only disagreements that no trusted source could settle are here. The box at the top shows the reviewer time saved in this area and how it is calculated.
2. Narrow the list:
   - filter by **High / Medium / Low**
   - click **<your department> only**
   - **Sort** by severity, problem type, plot or department
3. Click a conflict. The map zooms to it and shows every version (for example, the Kannada RTC owner against the BBMP owner), each with its source and reference.
4. Click **Accept** on the correct version, or **Resolve another way / Mark actioned** with a note.
5. The plot's score and colour update at once, the next conflict opens, and the decision is written to the audit trail.
6. **Undo decision** (on the **Decided** list) reopens a conflict.

**A2. Decide many similar conflicts at once**
1. Tick the conflicts, or tick **Select all … you can decide**.
2. Write a note, which is required.
3. If they are all of one type, click **Accept "<source>"**; otherwise **Mark actioned**. Confirm.
4. Each conflict is decided and audited separately, marked "[bulk]". Conflicts not routed to you, or already decided, are skipped with the reason.

**A3. Approve or reject change requests**
1. Open **Requests → Awaiting approval**. Each request shows **Now** (red) and **Proposed** (green), the reason, the proposer and the attached document (click to open it).
2. For boundaries and buildings, click **Show on map**: white dashes show the current shape, colour shows the proposed one, and the change in area is displayed.
3. **Approve**, or **Reject** with a note (a note is required to reject).
4. What approval does, automatically:
   - checks that the record has not changed since the request was made; if it has, the request becomes **stale**
   - updates the record
   - closes the conflicts the change settles and re-scores the plot
   - sends a notice to every department that must follow (an owner change notifies Municipal, Water, Electricity and Gas)
   - writes the audit entry
5. You cannot approve your own request. The Four-eyes rule sends it to another approver.

**A4. Approve a batch of requests**
- Tick requests, or click **Select similar** on one to tick every pending request of the same kind. Add a note and click **Approve N** or **Reject N**.
- Each request is still checked on its own (department, four-eyes, stale value) and skipped with the reason if it fails.

**A5. Confirm uncertain column mappings**
1. **Data** shows a yellow banner when some columns were mapped with less than 90% certainty (for example *Survey_Ref → Plot / survey ID, 87%*).
2. For each one, click **Correct**, or choose **Change to…** another field or **Not mapped**.
3. Your answer is stored for the area, audited, and used from the next pipeline run.

**A6. Issue a property report**
- Click the plot and then **PDF report**, or go to **Data → Export → Property report (PDF)** and type the plot number.
- The report brings together every department's records, the map, conflicts, change requests and the audit extract. Its fingerprint is logged, so a printed copy can be checked later.

**A7. Manage your department's staff** *(department approvers)*
1. Open **Access**. It lists your department's accounts, and who granted each role and when.
2. To add someone:
   1. Enter a username, a name and the role (Officer or Viewer), then create the account.
   2. A **one-time password** is shown once. Give it to the person securely; they must change it at first sign-in.
3. **Change role**, **Reset password** (issues a new one-time password) or **Disable** when someone moves or leaves. A role change signs the person out.
4. Every grant, change, reset and disable goes into the access log.

**A8. See what reviewers agree on**
- **Accuracy → Learning from reviewers** lists, per conflict type, how many decisions were made and which source reviewers chose.
- When a pattern is consistent enough, it is marked **ready**, and an admin can turn it into a rule (M5).

---

## 7. Admin workflows

**M1. Bring in data for an area**
1. Choose the area, click **Upload**, and add the department files. Accepted:
   - vector: Shapefile (with .dbf/.shx/.prj), GeoJSON, KML, GPX, GeoPackage
   - tables: CSV with plot references or lat/lon
   - rasters: GeoTIFF DSM, DTM or orthophoto
2. Each file is recognised automatically: its type (cadastral, RoR, municipal, DSM and so on), its format and its coordinate system.
3. New real-world area (from the command line): `python -m landconnect.realdata <name> <south> <west> <north> <east> "<title>"`

**M2. Run the pipeline and read the result**
1. Click **Run pipeline**. The progress screen shows the eleven steps:
   ingest → column mapping → coordinates → building extraction → topology → matching → heights and changes → conflicts → confidence → accuracy → publish.
2. **Overview:**
   - the share of parcels finalised with no manual work
   - conflicts to review
   - boundary error
   - property tax at risk
   - reviewer time saved
   - layer shifts found and corrected
   - what each step did
3. **Data:** what came in, how it was corrected (georeferencing, co-registration, topology), and what is real or simulated.
4. **Accuracy:**
   - precision, recall and F1 for all problems together
   - blind tests
   - the before/after table of rule changes
   - boundary accuracy
   - calibration by score band

**M3. Accounts and roles**
- Open **Admin**. The permission matrix shows who may grant what.
- Create any account (viewer, officer, approver or admin) for any department, change roles, reset passwords, disable accounts, and read the access log.
- You cannot change your own access, and the last admin cannot be removed.

**M4. API keys for department systems**
- **Admin → API keys:** issue a key with a role and department. It is shown once. Revoke it when the system is retired.

**M5. Turn reviewer patterns into rules**
1. **Accuracy → Learning from reviewers:** a pattern marked **ready** has at least 8 decisions of one type with 90% or more choosing the same source.
2. Click **Turn on (settles N)**. It is audited and takes effect at the next run.
3. Conflicts it settles say "Learned rule" in their resolution, and a reviewer's own decision still wins.
4. **Turn off** reverses it. The small bar chart shows the share of conflicts settled automatically, run by run.

**M6. Prove the results are not tuned to the test**
- Random blind batch: `python -m landconnect.blind random` scores a new synthetic ward with a hidden seed.
- Teammate batch:
  1. Run `python -m landconnect.blind kit`.
  2. A teammate who has not read the rules edits `data/blind_manual/raw` and logs each edit in `data/blind_manual/_truth/manual_errors.csv`.
  3. Run `python -m landconnect.blind score`.
- Both results appear under **Accuracy → Blind tests**.

**M7. Check the audit trail has not been altered**
- **Audit** shows the chain status (**Intact**).
- From the command line: `python -m landconnect.audit <area>`. Editing any past entry breaks the chain and is reported.

**M8b. Add state-wide cadastral maps**
- `python -m landconnect.statewide fetch south` downloads Tamil Nadu, Karnataka, Kerala, Andhra Pradesh, Telangana and Puducherry (3.8 GB). Use a release tag (for example `maharashtra`) for any other state. The download resumes if interrupted.
- `python -m landconnect.statewide list` shows what is installed. New maps appear in the Layers panel without a restart.

**M8. Maintenance**
- **Admin → Reset demo** regenerates the synthetic ward.
- The Google satellite basemap needs your own key: set `GOOGLE_MAPS_API_KEY` before starting.
- Claude for questions needs `ANTHROPIC_API_KEY`. Without it, the built-in rules answer.

---

## 8. End-to-end scenarios across roles

Each scenario lists who does what, in order. *System* means Land Connect does it automatically.

### S1. A property is sold (mutation)
1. **System:** the registered deed (IGR) names a buyer, but the RoR still shows the seller. The run raises **Registered sale not yet mutated in the RoR**, routed to Revenue and Registration, and drafts **CR-xxxxx: Owner (RoR) → buyer**, citing the deed.
2. **Revenue approver:** **Requests** → opens the deed reference → **Approve**.
3. **System:**
   - updates the RoR owner and closes the owner conflicts
   - the ownership chain now reads "RoR matches the latest registered buyer"
   - the plot's score rises
   - sends notices to Municipal, Water, Electricity and Gas
4. **Municipal, Water, Electricity and Gas officers:** each sees the notice in their inbox, proposes the owner or account-holder change for their own record, then clicks **Done**.
5. **Their approvers:** approve. All five registers now agree.

### S2. New construction found between two surveys
1. **System:** comparing the 2021 and 2026 elevation models finds a new building. If the land is taxed as vacant or not taxed at all, the run raises **New construction on land assessed as vacant** (routed to Municipal and Town Planning) and adds its **tax at risk**.
2. **Municipal officer:** field visit, then **Propose a change → Property ID (tax)** or **Assessed use**.
3. **Municipal approver:** approves. The conflict closes and the revenue at risk falls.
4. **Survey officer:** if the building is missing from the map, maps it (O3) and a **Survey approver** approves.

### S3. More floors than the permission allows
1. **System:** the elevation model shows 4 floors and Town Planning's permission allows 3. The run raises **More floors than the building permission allows**, high severity, with the extra tax at risk.
2. **Town Planning approver:** inspects, then either issues a notice or regularises. If regularised, a **Town Planning officer** proposes **Permitted floors → 4** with the regularisation order attached, and the approver approves.

### S4. A building encroaches on the road
1. **System:** the building crosses the plot line by more than its positional error, confirmed by two building layers. The run raises **Building crosses the parcel boundary**, routed to Roads, Municipal and Town Planning.
2. **Roads approver:** **Review** → looks at the red protrusion on the map; if it is an outline error, **Mark actioned** with a note (and a Survey officer corrects the outline, O5).
3. **Roads officer:** otherwise proposes **Encroachment notice no.** with the notice attached; the **Roads approver** approves.

### S5. A boundary dispute is settled by a fresh GNSS survey
1. **Survey officer:** **Edit boundary**, moves the corners to the surveyed points, **Save**. The checks confirm no neighbour overlap. Attaches the DGPS sketch and submits.
2. **Survey approver:** **Show on map** to compare the shapes and read the change in area, then **Approve**.
3. **System:** replaces the boundary, re-computes the area, closes the geometry and area conflicts, and notifies Municipal, Town Planning and Roads.

### S6. A house is missing from the map
- **Survey officer** O3 → **Survey approver** A3. The house appears in yellow with "mapped by …, approved by …" in its sources.

### S6b. A building outside the mapped area
1. **Survey officer:** **+ New plot** → draws the plot's boundary on the imagery → plot number, reason, survey sketch → **Submit** (O4b).
2. **Survey approver:** **Requests → Show on map → Approve.** The plot appears, in review.
3. **Survey officer:** **Map a building** on the new plot → **Submit**. **Survey approver:** approves.
4. **Revenue officer:** the new plot's "no revenue record" conflict is in the inbox. Proposes the RoR entry (owner, extent) with documents; the **Revenue approver** approves.
5. **Municipal officer:** assesses the property for tax (Property ID, assessed use); the **Municipal approver** approves. The plot's score rises as its records are linked.

### S7. A new department uploads data with unfamiliar column names
1. **Admin:** **Upload**, then **Run pipeline**.
2. **System:** maps columns by name and values. Anything below 90% certainty goes to **Data** as "please confirm".
3. **Approver:** **Correct** or **Change to…**.
4. **Admin:** **Run pipeline** again, which uses the confirmed mapping.

### S8. Staff joins, changes role, leaves
1. **Department approver:** **Access** → creates `water.clerk` as Officer, then hands over the one-time password.
2. **New officer:** first sign-in → sets their own password → works.
3. **Promotion to approver:** only the **state admin** can grant this (**Admin → change role**).
4. **Leaving:** the department approver clicks **Disable**. The person's sessions end and the access log records who did it.

### S9. Reviewers keep making the same decision
1. **Approvers:** over time, choose "Revenue record (RoR)" in 9 of 9 cases of one conflict type.
2. **System:** **Learning from reviewers** marks the pattern **ready**.
3. **Admin:** **Turn on**, then runs the pipeline. Similar conflicts are now settled automatically with a "Learned rule" note, and the share settled automatically goes up.

### S10. A citizen, court or RTI request needs a trustworthy record
1. **Approver:** **PDF report** for the plot. Its report ID and SHA-256 are logged.
2. **Admin:** **Audit → Intact**, plus the plot's **Decision history** (every change, who proposed it, who approved it, and when).
3. **Viewer or partner agency:** for data without personal details, the **Export** or **OGC API**, where names are masked.

---

## 9. Rules that apply everywhere

- **Four eyes:** whoever proposes a change can never approve it.
- **Department authority:** each field belongs to one department, and only that department's officers propose it and its approvers approve it.
- **Evidence:** ownership, extent, land use, boundary, permitted floors, deed buyer and encroachment notices need a document. Its fingerprint (SHA-256) is stored.
- **No lost updates:** if the record changed after a request was made, approval is refused and the request is marked stale.
- **Nothing is deleted:** rejected, withdrawn and stale requests stay on file.
- **Routing:** every conflict goes to the departments that can settle it; approvers of other departments see it read-only.
- **Privacy:** viewers never see personal names.
- **Every action is audited** with the person's name, on a hash chain that shows any tampering.

The detailed rules and thresholds are in [RULES_AND_STANDARDS.md](RULES_AND_STANDARDS.md) (rules B11–B27).

## 10. What is recorded

| Action | Audit entry | Where to see it |
|---|---|---|
| Pipeline run, each step, conflicts found or auto-resolved | `run.*`, `conflict.*`, `change.detected` | Audit, Overview |
| Reviewer decision, bulk decision, undo | `review.*` (note, and "[bulk]" for bulk) | Audit, plot's Decision history |
| Change request proposed, approved, rejected, withdrawn, stale | `change.*` (old and new value, evidence SHA-256) | Requests, Audit |
| Column mapping confirmed | `schema.confirmed` | Audit |
| Learned rule on or off | `learning.rule_enabled` / `learning.rule_disabled` | Audit |
| PDF report issued | `report.generated` (report ID, SHA-256) | Audit |
| Account created, role changed, disabled, password reset or changed | `access.*` | Admin / Access → access log |
