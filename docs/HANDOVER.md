# Handover Doc — Leaflet Price Extractor

Living log. Updated each session. Newest entry on top.

---

## Session 24 — NASCA RESOLVED; backfill data staged; manager doc

- **PM: NASCA encryption issue is RESOLVED** — historical leaflet archive is
  readable/transferred, and ALL backfill folders (W1-W28, countries x
  retailers) are compiled and staged. The project is no longer waiting on
  data — next action is simply running the batch over the staged folders
  (organize into data/leaflets/<year>-wNN/ if not already, then run_batch;
  a few overnight runs at laptop-safe intensity).
- Manager deliverable written: ~/Desktop/Leaflet_Tracker_Talking_Points.docx
  (1-page talking points for upper management; render-verified).
- App relaunched via nohup on http://localhost:8501 (survives this chat;
  relaunch: `./.venv/bin/streamlit run app.py` from project root).
- Note: work-laptop transfer kit (WORK_SETUP.md exists; zip build was blocked
  by a transient macOS permission glitch) — likely MOOT now that NASCA is
  resolved; revisit only if pipeline must also run inside the work env.

---

## Session 23 — remote path ABANDONED (PM decision)

Cloud-box attempt hit friction (password auth loops, forced-password-change,
host-key churn, tiny 2-vCPU box, upload dropped) and PM called it off. No data
ever reached the server (rsync died at 258 bytes — model list never left the
laptop). PM to delete the server in the console. `remote/bootstrap.sh` +
`remote/RUNBOOK.md` stay in the repo if the backfill volume ever justifies
another go.

**Operating plan now: laptop-only.** Safe by construction after session 22:
--ocr-budget 4 cap + nice priority; a full cold leaflet run keeps the machine
usable. W1-W28 backfill locally = spread over a few overnights, or revisit
remote later.

**Still pending: the W28 cold rebuild** (OCR cache is empty from the PM's
token-deletion test). App is ready: Batch tab -> --force -> Run. Expect
~40-60 min at jobs 1, live progress bar, fleet lights up, master back to 29.

---

## Session 22 — laptop freeze fixed + remote (cloud CPU) kit

**Freeze root cause:** nested parallelism multiplied — 2 leaflet jobs x 3 skim
workers + prewarm pools = 8+ concurrent paddle processes (1-2 GB each) ->
RAM/CPU exhaustion. Fixes:
- **Hard OCR-process budget**: jobs x inner workers <= `--ocr-budget`
  (default 4). App slider defaults to jobs=1 with explainer.
- Batch launches at background priority (`nice -15`) from the app — laptop
  stays usable during runs.
- `--ocr-budget 12` etc. unlocks big machines.

**App UX hardening this session (PM stress-testing):** progress bar full-width
top of Batch tab; auto-refresh dashboard when a batch finishes; honest
completion banner (parses "batch complete" line; red + log on crash) with
dismiss; batch_pid PID-reuse guard + batch self-cleaning pidfile; uploads with
_WXX suffix auto-file into their week folder (no more loose duplicates);
duplicate semantics validated (dupes replace, never double — master stayed 29).

**Remote kit (PM approved cheap cloud CPU; data policy cleared):**
`remote/bootstrap.sh` (Ubuntu one-shot: deps, venv, master build, OCR model
pre-download) + `remote/RUNBOOK.md` (rsync up -> tmux run -> rsync down ->
delete box; Hetzner sizing table; resume semantics identical remotely).
Backfill at 16 vCPU: ~overnight.

**Current state:** OCR cache deliberately EMPTY (PM deleted W28 tokens for a
cold-start e2e test that froze the laptop mid-run — no tokens were rebuilt).
Fleet: 4 leaflets, ledger marks them done from before; master 29 rows intact.
Next local run with --force = true cold run at safe intensity (~40-60 min at
jobs 1) OR use it as the remote kit's shakedown on the cloud box.

---

## Session 21 — app reworked: batch-first dashboard

Full app.py rewrite around the batch workflow (PM ask), verified live tab by
tab. Five tabs, global metric header (dataset rows / weeks / done / pending /
failed), light CSS polish:

- **📦 Batch** — fleet table (every PDF joined with the ledger: status icon,
  country/retailer resolution, rows, health verdicts, errors), multi-file
  upload + "file into week folders" (rename_by_week now also moves
  pre-suffixed uploads into their own week folder), and Run controls
  (--jobs slider, --force). The batch launches as a DETACHED process
  (log: output/batch.log, pidfile: output/batch.pid) — closing the browser
  never kills it; the app shows log tail + safe Stop (SIGINT) button.
- **📄 Single leaflet** — the old flow, condensed: auto-context from filename,
  profile notes, auto-find toggle, live progress, results tabs incl. OCR
  audit images. Still hot-reloads pipeline code per run.
- **📊 Dataset** — master stats + rows-per-week bar chart + downloads;
  weekly deliverables listed with per-file downloads; merge-CSVs uploader.
- **🔍 Review** — aggregated master-gap report across ALL leaflets' review
  CSVs + combined download. (Polish found here: glued price pairs
  'D5499D5199' were entering review as fake codes -> junk filter added,
  fleet regenerated in 5s via --force --jobs 2.)
- **💛 Health** — layout_health.jsonl as a sortable history table with alert
  count.

**Post-rework additions (same session, PM-driven):**
- **Live batch progress**: run_batch writes `output/batch_progress.json`
  (todo/completed/failed/in-flight) after every leaflet; the app's Run panel
  renders it as an auto-refreshing progress bar (st.fragment run_every=3s)
  with in-flight filenames + log tail.
- **Master lifecycle** (Dataset tab): "Continue from an existing
  master_raw.csv" (validates headers, archives current, regenerates weeklies).
- **♻️ Reset app** (Batch tab): archives all DATA (master, weeklies,
  per-leaflet CSVs, ledger) to `output/archive/fresh-start-<ts>/`, DELETES all
  logs (batch log/progress/health history) -> app shows a clean slate; next
  batch rebuilds from token caches in seconds. **The OCR cache is untouchable
  from the app** (PM: too dangerous) — manual `rm -rf output/ocr` is the only
  path, documented in the caption.
- Leaflet PDFs in data/leaflets are never touched by any app action.

---

## Session 20 — parallel full-OCR + concurrent leaflets (race-free)

- **`ocr_page.prewarm_pages()`**: candidate pages OCR in parallel (3 workers,
  heavy model) into the token cache; the extraction loop then reads from cache.
  Wired into run_batch + run_leaflet. Each page persists the moment it finishes
  — interrupt-safety unchanged. (App stays sequential: live log + macOS spawn
  constraint under streamlit.)
- **`run_batch --jobs N`**: N leaflets concurrently. RACE-FREE BY DESIGN: the
  master merge + ledger moved OUT of workers — workers extract and return the
  CSV path; the PARENT merges serially as futures complete (single writer, no
  lock needed). Workers use inner_workers=2 to bound RAM (jobs x inner paddle
  procs). Default --jobs 1; use 2-3 for backfills.
- Validated: `--force --jobs 2` full re-run of the W28 fleet -> 4/4 done,
  master exactly 29 rows (idempotent), **5 seconds total** (all cached — the
  replay superpower: reprocessing a whole week after logic changes is free).
- Cosmetic known-wart: batch ledger prints "week ?" for Emax (ledger reads
  dates.year_week, not the filename fallback the ROWS get — rows are correct).

**Speed final state: 28pp ~4 min, 52pp ~7-8 min, backfill @ --jobs 3 ≈
overnight per ~10 weeks of data. Remaining ideas if ever needed: Apple Vision
OCR bakeoff (ground truth ready — potential 5-10x).**

---

## Session 19 — renames, batch drill PASSED, weekly outputs, parallel skim

- **Sample fleet renamed to convention** (UAE_C4_W28 / UAE_LULU_W28 /
  UAE_SHARAF_W28 / UAE_EMAX_W28) with full identity migration: OCR caches
  renamed (order matters — Carrefour stem is a prefix of the others), stale
  master rows purged, root duplicate PDFs removed (cmp-verified first).
- **Interrupt-resume drill PASSED**: batch killed with SIGINT mid-Sharaf-skim ->
  ledger clean (3 done, no partial entries), resume processed ONLY Sharaf,
  final 4/4 done 0 failed. The foolproof claim is now proven, not promised.
- **Output naming (PM ask)**: everything is `Leaflet_Extraction_*`. Per-leaflet:
  `Leaflet_Extraction_<COUNTRY_RETAILER_WXX>.csv` (+_review/_out_of_scope).
  Weekly deliverable: `Leaflet_Extraction_<Year-Week>.csv` auto-regenerated on
  every master merge (mirrors master exactly). Old outputs archived to
  output/archive/pre-cleanup. Emax no-dates issue CLOSED: week falls back to
  the filename _WXX (+note) so no row lands week-less.
- **Parallel skim (3 workers)**: 28 pages in 2:16 vs ~7 min sequential (~3x).
  macOS spawn caveat: workers only from real-script entry points (run_batch /
  run_leaflet have __main__ guards ✓); the APP uses workers=1 deliberately
  (live per-page log + spawn-safety under streamlit).
- Mobile-skim recall previously verified identical to heavy models; speed gain
  ~20% (paddle caps det resolution internally — the 3-5x projection was wrong).

**Per-leaflet reality now: ~5 min (28pp) to ~11 min (52pp) end to end.**

**Remaining (all waiting on the world):** W1-W28 leaflet collection -> one
`run_batch.py`; first-leaflet audit per new country; review-CSV -> master-list
gap loop with Model List owner.

---

## Session 18 — batch runner (crash-proof backfill) + skim tier tuning

**PM workflow locked:** files arrive named `COUNTRY_RETAILER_WXX.pdf` (verified:
BAH_LULU_W12 etc. all resolve country/currency/retailer with zero input), PM
organizes them into `data/leaflets/<year>-w<NN>/` folders, then ONE command
processes everything and must survive interruption at any second.

**`src/run_batch.py`** — resumable at every level:
- ledger `output/batch_state.json` (atomic temp+rename writes; one entry per
  file: done/failed + stats + health verdicts). Re-run skips done; `--force`.
- per-page token cache: pages replay from `output/ocr/*_tokens.csv` (no re-OCR
  on resume); skim reports reused from `*_skim.json`.
- master merged after EVERY leaflet (idempotent) — ctrl-C keeps all completed.
- one bad PDF -> failed entry + batch continues. `--week`, `--dry-run` filters.

**Skim tiers finalized** (single-fridge-page edge cases, PM-raised):
1. keyword COUNT >= 3 (real appliance pages 5-16 kws, FPs 1-2 — token SIZE was
   tested and REJECTED: real headers print body-sized; largest keyword found
   was a 'DishWash' soap banner at 2.6x median)
2. ONE master-code hit (single known fridge on a grocery page — proven incl.
   OCR-noised code via confusion tolerance)
3. any keyword + code-shaped token (single NEW model not in master — the RT50
   lesson at page level; costs ~2-3 FP pages/flyer, worth it)
Controls verified negative (grocery, soap, 'SMALL APPLIANCES' banner pages).

**In flight:** mobile-model skim benchmark (models downloaded+cached now;
timing pending). Batch acceptance test (with interrupt-resume drill) queued
behind it.

---

## Session 17 — pre-filter BUILT + acceptance-tested end to end

`src/prefilter.py` (design validated session 15): skim every page at 1.5x ->
keyword + master-code-safety-net scoring -> candidates = hits ±1. Wired as the
DEFAULT full-PDF path in CLI (override: --pages / --no-prefilter) and app
("🔎 Auto-find appliance pages" checkbox, on when pages blank; live skim log).
Skim report persists immediately to `output/ocr/<file>_skim.json` (candidate
list inspectable mid-run; stdout buffering hid it otherwise).

**Acceptance test — full 52-page Lulu, one command, zero page hints:**
- skim hits [27,28,30,31,40,41,42,43,51], master code on [41] -> 16 candidates
- p41 found (keyword + code), dates auto-found (15-23 Jul), health OK
- 5/5 SKUs extracted matching pixel-verified truth; the filename-W28-vs-page-W29
  cross-check note fired on every row as designed
- **30:12 wall clock** vs ~60+ unfiltered

**Speed work (in progress):**
- Mobile-model skim wired (`get_ocr(fast=True)`: PP-OCRv5_mobile det/rec;
  skim-only — full pass keeps accuracy models). Benchmark running; bar = same
  hit pages at a fraction of the ~15-min heavy skim.
- Remaining fat: housewares FP block (pp.26-32, 7 pages ≈ 7 min of full OCR).
  Candidate fix: keyword must come from a LARGE token (section headers; we have
  box heights) or need 2+ signals. Tune against saved p27-31 tokens.
- Future if needed: parallel leaflets (2-3 processes) for weekly batches/backfill.

---

## Session 16 — layout rules as tracked data + drift detection (PM ask)

PM requirement: layout rules differ by (country, retailer) AND can change
week-over-week — track and store that. Built:

1. **Profiles are now DATA**: `data/master/layout_profiles.json` (zoom,
   price_position, notes, `validated` audit trail per profile). Lookup chain:
   `Retailer/Country` override -> `Retailer` -> DEFAULT. Edit the json, no code
   changes; loaded fresh on every call so app picks edits up immediately.
2. **Layout health check** (`src/layout_health.py`): every run computes
   observed signals (price-direction share via new `ExtractedRow.cluster_dy`,
   clean rate, priced rate, capacity fill, rescue count) and compares vs the
   profile -> verdicts. Appends to **`output/layout_health.jsonl`** — the
   per-(file, retailer, country, week) history = the week-over-week change log.
   `history(retailer, country)` filters it.
3. Wired into CLI (prints verdicts) and app (st.warning banners; "layout
   health OK" caption when clean).
4. **Tested with synthetic drift**: Emax replayed with the wrong direction ->
   "LOW CLEAN RATE" + "MANY MATCHED ROWS WITHOUT PRICES" verdicts; correct
   profile -> healthy (clean 0.83, priced 1.0, below_share 1.0).

**Operating procedure when a DRIFT verdict fires:** audit that page's annotated
PNG vs rows -> edit layout_profiles.json (or add a Retailer/Country override)
-> add a `validated` entry -> replay saved tokens (no re-OCR).

**Next up (unchanged):** the appliance-page pre-filter (design validated,
session 15) -> then W1-W28 backfill when PM's leaflets arrive.

---

## Session 15 — EMAX (4th retailer) audited + integrated; all 4 retailers live

Sample: `UAE_EMAX_W28.pdf` (28pp, mobile-sale flyer; appliances = p21 only;
filename convention resolved everything automatically). First-leaflet audit
found Emax's quirks; all handled:

- **Prices sit BELOW the code** (inverted vs all others) -> new profile field
  `price_position` ('above'|'below'), threaded through extract/CLI/app. Before
  this, washers silently took the fridge row's prices.
- **Glued price tokens**: 'WAS8999' (label+price), 'D5499D5199' (NOW+card price
  merged) -> `_expand_glued()` splits into virtual tokens; 'WORTHD1999' freebie
  banners excluded from prices entirely (FREEBIE regex).
- **'NET-607L' capacity chips** -> NET_CHIP pattern in find_capacity_chips;
  chips may sit below the code (wider dy window for chips only, -320); the
  code-between guard generalized to both directions.
- Junk filters extended: ^WAS\d, ^WORTH, ^(NET|GROSS)-?\d.
- 3-price stacks (WAS/NOW/card) resolve like Sharaf's member pattern; card
  price -> Other Offer Details.
- **Pixel-verified via replay: Emax p21 = 5 clean + 1 honest flag** (LG
  W1S1CVK2EHM prints no WAS price). All 4 regression pages unchanged.
- **First cross-retailer SKU**: RS70F64K1TAE priced at Sharaf (6299->4799,
  member 4703) AND Emax (6399->5199, card 4899).
- Emax cover prints no promo dates (mobile-sale flyer) — rows have blank
  Year-Week; ask PM how Emax weeks are determined (filename week only?).
- **master_raw = 29 rows / 4 leaflets / ALL FOUR RETAILERS.**

**Pre-filter design VALIDATED empirically (PM's skim idea):** keyword signal
(APPLIANCE/REFRIGERAT/FRIDGE/WASHER/FREEZER/DRYER/DISHWASH) found every true
appliance page across all 4 leaflets with ZERO misses (FPs cheap). Design:
skim EVERY page at low zoom (~1.5x, ~4x faster) — do NOT stride/skip pages
(Carrefour's section is 1 page with no signal on neighbours) — then full-zoom
keyword pages ±1. Safety net: run matcher on skim tokens; master hit on a
non-keyword page joins candidates. NOT BUILT YET — next session.

---

## Session 14 — retailer profiles + Sharaf 4x + garble rescue (resume list DONE)

Worked the session-13 resume checklist end to end:
- **`src/profiles.py`** — per-retailer presets (PM ask): zoom + tile-anatomy
  notes. Sharaf zoom=4, others 3, Emax=DEFAULT until a sample exists. Wired into
  app (profile expander + zoom slider default) and CLI (--zoom default=profile).
- **Sharaf re-OCR'd at 4x (pp.9-10)**: p9 NOW-garbles fixed at the source —
  8/8 clean, every price pixel-verified, members captured.
- p10 still garbled at 4x ('D4799'->'47999' conf .74) -> built the **garble
  rescue**: price tokens under conf 0.80 are quarantined; when one sits
  vertically between two clean prices the stack is WAS/garbled-NOW/member
  (bottom = member even when the 'DG' chip text doesn't OCR), and NOW is
  recovered from the garble's digit-substrings constrained to member<=x<WAS,
  smallest-above-member wins. Rescues carry an explicit flag
  ("NOW price 4799 rescued from garbled OCR token '47999'"). All 3 p10 rescues
  verified correct (4799/799/949). Regressions (Carrefour p25, Lulu p41) pass.
- **W29 "mystery" resolved — not a bug**: Lulu cover prints promo 15–23 July
  2026 -> ISO W29. Page dates win per spec. Added the **filename-vs-page week
  cross-check** to output_writer (mismatch -> Notes/Flags).
- Sharaf CSVs regenerated (14 extract / 6 review / 48 out-of-scope) and merged:
  **master_raw = 23 rows, 3 leaflets, all 3 sampled retailers.**

**PM VALIDATION (session 14):** PM confirmed the extraction is correct across
all three retailers — everything not captured traces to models absent from the
master list, not to pipeline misses. The `*_review.csv` files are therefore the
authoritative master-gap report: hand them to whoever owns Model List.xlsx;
after adding models, re-run `build_master.py` + replay saved tokens (no re-OCR)
to pick them up.

**W28 DATASET SEEDED (session 14, PM go-ahead):** full replay of all three
leaflets through the current pipeline -> master_raw = **23 rows / 3 leaflets**:
Carrefour 4 (W28, retailer UNKNOWN — sample filename lacks a retailer token;
real `UAE_C4_W28` files won't), Lulu 5 (W29 per page dates 15–23 Jul), Sharaf 14
(W28 — dates FOUND on cover: '10 JUL - 9 AUG 2026' @0.96, month-long promo, so
the earlier "Sharaf has no dates" open item is CLOSED).

**Lesson — renames change identity:** the `_w28` rename changed 'Leaflet / File
Name', so replace-by-filename left 9 stale pre-rename rows in master (purged
one-off). Workflow rule: ALWAYS file with rename_by_week BEFORE extracting.

**Open items:**
- Emax: still no sample (4th retailer).
- Master-store "mapped all to sharaf" PM concern: data checked twice, correct
  both times — likely an Excel view artifact; show PM the groupby next session.
- Backfill W1-W28 pending PM collecting leaflets; batch pre-filter (find
  appliance pages cheaply) still to build before bulk runs.

---

## Session 13 — Sharaf layout family (PAUSED HERE — resume notes below)

Sharaf = 3rd layout family, 12-page ALL-electronics leaflet, appliances pp.9-10
(PM tip). Dense 2-col grid. Tile: brand top-left, image, spec bullets right,
capacity CHIP left ('650' over 'LITRES' over 'GROSS CAPACITY'), code centered
under image, price stack bottom-right: WAS Đxxxx (struck red) / NOW Đxxxx /
black 'DG member' chip Đxxxx (THREE prices).

**Fixed this session (all verified by replay):**
1. Capacity chips were joining price clusters ('555 LITRES' from the NEXT tile
   became a promo price — PM caught it). `find_capacity_chips`: number-over-
   unit-word pairs are excluded from prices AND harvested as capacity. ✓
2. 3-price stack: rrp=max, promo=MIDDLE (printed NOW), member=min ->
   'Member price X' in Other Offer Details (new `other_details` on ExtractedRow,
   wired to writer col 18).
3. **Column pairing** (`column_pools`): codes and price stacks each form clean
   x-columns on grid layouts; pair by order, link within column. Fixes the
   Siemens bug where dy 49.5 vs 50.0 (half a pixel!) picked the wrong column.
   Falls back to distance logic when columns don't split cleanly (Carrefour).
4. 'DG'-chip adjacency -> member price can't become promo; if NOW is unreadable
   -> flag "NOW price unreadable — member price present" (Hisense case). ✓
5. Crosswalk typo fixed: RT75DG7A14S9A -> RT75DG7A14S9AE (leaflet-verified);
   master rebuilt; Samsung RT75 now matches exact.

**State:** Sharaf p9: 7 clean + 1 honest flag; p10: 6 clean. Carrefour p25 +
Lulu p41 regressions PASS. BUT see known issue #1 — several Sharaf promo values
are actually the member price.

**KNOWN ISSUES — the resume list:**
1. **Sharaf NOW prices garble at 3x zoom** ('D1999' -> '2019999', 'D2499' ->
   '20024999', 'D799' -> '20D799'): the vertical 'NOW' label glues onto the
   price. When NOW garbles AND the DG chip text also fails to OCR, the member
   price silently becomes promo (Hitachi 2939 vs true 2999, Bosch 3429 vs 3499,
   Siemens 1959 vs 1999, GRB652 1959 vs 1999, RS70 4703 vs 4799, WFQP 783 vs
   799, SGW 930 vs 949). **CONFIRMED FIX: 4x zoom reads them cleanly**
   (D1999/D2999/D3499 all ≥0.91 conf at 4x). Optional belt+braces: garble
   rescue regex '20...' + constraint member <= x <= was.
2. **PM WANTS RETAILER PRESETS** (explicit ask): a per-retailer profile bundling
   zoom (Sharaf: 4, others: 3), tile anatomy hints (price-above-code vs below),
   member-chip handling, capacity source (chip vs spec line), keyed off
   `country_config` retailer. Next session: build `src/profiles.py`, thread
   through app + runner, re-run Sharaf pp.9-10 at 4x end-to-end.
3. Master-store question from PM (unresolved, deprioritized): after merging
   Sharaf the master "mapped all to Sharaf" — master_raw.csv checked and looked
   CORRECT (4 UNKNOWN-retailer Carrefour rows + 5 Lulu rows); possibly PM was
   looking at retailer=UNKNOWN rows or an Excel sort artifact. Revisit with PM.
4. Midea `MDRS710FIE46AED` ambiguous vs master (2 candidates) -> review row.
5. Sharaf p10 also has washers+dryers below; ovens/cooking (BXOFM905...,
   EKG9241Z7X, KDD90CNE) correctly not in master -> review rows.

**Resume checklist:** (a) build retailer profiles w/ Sharaf zoom=4; (b) re-OCR
Sharaf 9-10 at 4x, verify NOW prices vs pixels; (c) garble-rescue rule if any
remain; (d) regen Sharaf CSVs + re-merge master; (e) revisit PM's master-store
concern; (f) Emax = 4th retailer, no sample yet.

---

## Session 12 — week workflow: filer, week folders, master store

**Filename convention CONFIRMED by PM:** incoming files are `COUNTRY_RETAILER`
(`BAH_LULU.pdf`, `QTR_LULU.pdf`); we complete to `BAH_LULU_W28.pdf`. Resolver
extended with BAH/QTR/KUW/OMA codes — all 6 countries verified w/ correct decimals.

**New pieces:**
- `src/rename_by_week.py` — files loose PDFs from data/leaflets/ into
  `data/leaflets/<YYYY>-w<NN>/` with `_WNN` suffix. Week = trailing business
  week (last completed ISO week) unless `--week/--year` given. `--dry-run`
  supported; idempotent (skips already-tagged files). Sample W28 files filed.
- **data/ layout reworked:** leaflets live under week folders; app picker
  browses recursively (rglob) and shows week-relative paths; uploads land in
  root for the filer.
- `src/master_store.py` + app section "4 · Master dataset" — the accumulating
  raw dataset (`output/master_raw.csv`, same 21 cols). Merge semantics:
  idempotent replace-by-leaflet-filename; unmatched rows refused. App offers:
  merge previous weeks' extract CSVs (bulk upload), "add run to master" button
  after each extraction, master stats + download. Seeded w/ W28 Carrefour+Lulu
  (9 rows); double-merge test: 9 replaced, total stays 9 ✓.
- Also this session (prior message): three-file output split wired into app+CLI
  (`_extract` = matched SKUs only / `_review` / `_out_of_scope`), pipeline
  hot-reload on every app Run (stale-module trap closed), dates pages 1-3
  always OCR'd regardless of page scope.

**Weekly workflow now:** drop `BAH_LULU.pdf`-style files -> `python
src/rename_by_week.py` -> app: pick file, Run -> review 3 tabs -> "Add run to
master". PM has a third sample (Sharaf, filed under 2026-w28) not yet run.

---

## Session 11 — Lulu tuning + VALIDATION LAYER (PM-requested)

PM ran Lulu p41-52 via the app: prices wrong (Panasonic "230", Hoover "8888"),
capacities missing, review pile flooded. Token-level diagnosis on p41:

**Root causes + fixes (all in extract_page/prices/validate):**
1. Junk single numbers INSIDE product images ('230' voltage, '8888' drum
   display) beat real price blocks on x-affinity. Fix: **cluster strength** —
   a real block has >=2 prices, or price+%, or glyph-prefixed price; lone bare
   numbers only ever used with a "weak price cluster" flag.
2. Lulu price blocks are left-aligned & narrow -> overlap rule whiffs. Unified
   link order: above -> strong-preferred -> x-overlap(+anchor) -> dx w/ vertical
   tiebreak. Both layouts verified.
3. Capacity window widened 220->700 @3x for Lulu's spec-bullets-at-top anatomy,
   BUT guarded by "no other product code between spec line and code" — that
   guard is what stops cross-tile theft (Samsung-shows-550L bug).
4. Financing/wattage junk in review pile (TABBY1050, EMID175, POWER:600W) ->
   pack-size filter extended.
5. **`src/validate.py` — the column-appropriateness layer (PM asked for this
   explicitly):** REF must have L / WM must have kg (violations BLANKED+flagged),
   capacity ranges (REF 40-1200L, WM 2-30kg/side), promo<regular, per-currency
   appliance price floors, matched-row metadata completeness. Wired into app +
   run_leaflet after extraction.
6. No-badge price pairs: EXACTLY-2-price sane-ratio pair = clean conf 0.85
   (0.88 w/ glyph); 3+ prices no badge = review. (Lulu never prints % badges —
   the Đ glyph does NOT OCR as a letter on Lulu, unlike Carrefour's 'D2999'.)
7. Out-of-category pages (zero master matches) -> GAP rows go to a separate
   `*_out_of_scope.csv`, keeping the main review pile small.

**Verified state:** Carrefour p25 4 clean @0.97 (all caps/prices correct);
Lulu p41 4 clean @0.85 + 1 correctly-flagged (Panasonic capacity garbage
blanked by validator) + 2 legit small-appliance GAPs. Both CSVs regenerated
from saved tokens (replay, no re-OCR). Lulu CSV has no dates (pages 41-52 run
didn't include cover — known gap: runner/app should always OCR pp.1-3 for dates).

---

## Session 10 — Carrefour CSV final + local web UI

**Carrefour W28 run complete (28 pages):**
- Dates OCR'd off the cover: **07/07/2026 → 16/07/2026, W28** (year inferred -> flagged).
- Junk filter added (`_looks_like_packsize`): grocery pack-size strings
  (750G+250G, 24X500ML, 20GPROTEIN, FRSZEN1-1, 1.89LX2...) no longer pollute the
  review pile. CSV regenerated by REPLAYING saved tokens (no re-OCR):
  **75 -> 35 rows** = 4 clean + 31 honest gaps (mostly out-of-scope electronics
  pp.23-27 + the p25 freezer/cooler/ACs).
- Financing filter (tabby/EMI adjacency) + >75%-discount plausibility guard added
  ahead of Lulu. p25 regression: PASS (8 rows, 4 clean).
- Replay pattern is important: saved `output/ocr/*_tokens.csv` lets us re-extract
  with improved logic in seconds, no OCR. This is how extraction iterations work.

**Local web UI (`app.py`, Streamlit) — PM's runner from now on:**
    ./.venv/bin/streamlit run app.py
- Sidebar: upload/pick PDF -> auto context from filename (+ override dropdowns) ->
  pages/zoom -> Run. Main: metrics, Clean/Review/All tabs, OCR audit image viewer
  (confidence-coloured boxes), Download CSV.
- Verified in-browser end-to-end (p25 run: 8 rows/4 clean, same as CLI).
- Launch config in Desktop/Grad Proj/.claude/launch.json ("leaflet-extractor").

**Next** — Lulu appliance pages (~40-47) via the app or CLI with --country UAE
(PM to confirm Lulu country); expect capacity-window tuning for the Lulu profile.

---

## PROJECT END SCOPE (PM, session 10)

Backfill **W1–W28 for every leaflet, every country**, build the raw dataset, then
**append week-by-week from W29 onward**. Architectural consequences (planned):
1. **Appliance-page pre-filter** — cheap low-res OCR sweep to find the 1–4
   appliance pages before full 3x OCR (28-page leaflet -> ~4 pages of real work).
2. **Accumulating raw store** — `output/master_raw.csv` keyed by (file, year-week,
   country, retailer); re-running a leaflet REPLACES its rows (idempotent);
   weekly appends never touch history.
3. **Filename discipline** — backfill files named `<country> <retailer> w<NN>.pdf`
   (e.g. `uae c4 w12.pdf`); filename week cross-checks the page-printed dates,
   mismatch -> flag. Manual overrides don't scale to ~600 files.
Open ops questions: are W1–W28 PDFs already collected? Who names them?

---

## Session 9 — full-leaflet assembly (dates, 21-col writer, batch runner)

**Done**
- `src/dates.py` — promo date-range parser (named + numeric formats, '7 - 18 July',
  dd/mm ranges). Year policy per spec: printed year > `--year` hint (flagged
  "inferred") > blank + flag. Handles Dec->Jan wrap; refuses end<start ambiguity.
  Duration inclusive; Week/Year-Week from start date ISO week.
- `src/output_writer.py` — exact 21-column CSV per OUTPUT_SPEC. 3dp prices always;
  Discount Amount/% COMPUTED (badge only cross-checks, >1.5pp disagreement ->
  Notes/Flags); brand display (LG stays upper, SAMSUNG->Samsung); country display
  names; utf-8-sig for Excel.
- `src/run_leaflet.py` — whole-PDF runner: filename ctx (refuses to run without
  currency), OCR every page w/ audit artifacts, date scan over first N pages,
  extraction, summary stats, one CSV. Run from PROJECT ROOT (`python
  src/run_leaflet.py ...`) — data paths are root-relative.

**In progress** — full 28-page run on the sample (`--country UAE --year 2026`).

---

## Session 8 — FIRST REAL END-TO-END EXTRACTION (p.25: 8/8 correct)

OCR (PaddleOCR v6 models, p.25 @3x zoom) read **every model code on the page at
conf ≥0.99** — RV760PUK7K4PWH, LTC752HFCM, RT50CG6404S9, LVF0800PBPC, etc. The
3x upscale made 150 DPI a non-issue. Audit artifacts land in `output/ocr/`
(`*_tokens.csv` + `*_annotated.png`, boxes coloured by confidence).

**OCR quirks learned (all handled):**
- Currency glyph OCRs as a letter prefix on struck RRPs (`D2999`); promo prices
  read bare — free secondary RRP signal. PRICE_TOKEN strips the prefix.
- Spec-line separator `|` OCRs as glued `1`/`I` (`550L1`, `110LI`) — CAPACITY
  regex tolerates one artifact char after the unit.
- Arabic lines -> empty tokens (en model), dropped.

**`src/extract_page.py` (Stage 3+4) — linking rules that survived real geometry:**
- Tiles are TALL; naive nearest-neighbour links codes to the WRONG tile (Samsung
  code sits closer to the row below's prices than its own).
- Correct rule: price cluster ABOVE the code + **x-range overlap** using the
  code∪spec-line as the tile's x-anchor (codes are left-aligned, prices centered
  — center-distance misfires on middle tiles). Stacked same-column tiles:
  vertically nearest overlapping cluster.
- Capacity anchor scored by horizontal offset FIRST (adjacent tiles share a row).
- **Unmatched code-shaped tokens become GAP review rows** (with linked prices) —
  never silently dropped. That's how master coverage gaps surface.

**p.25 final: 8 rows, all correct.**
- 4 clean @0.97: Hitachi RV760 (550L, 3499->2599), LG LTC752 (509L, 2999->2099),
  Samsung RT50 alias (393L, 2149->1699), LG washer LVF0800 (8kg, 1999->1399).
- 4 GAP rows: Hitachi chest freezer HRCS11316MNWAE + Hoover cooler HBCK117B
  (genuine master-coverage questions for PM) and 2 ACs (out of scope — correct).

**PM warning (logged):** tile positions/layout WILL vary across retailers
(Carrefour/Lulu/SharafDG/Emax) and countries. Current linking rules = a
Carrefour-UAE profile. Policy: the FIRST leaflet of each new retailer/country
gets a manual audit pass (annotated PNG vs extracted rows) before batch trust;
if geometry differs, promote the tunables in `extract_page.py` to per-retailer
profiles keyed off `country_config`. Honest caveat: the %-arithmetic check does
NOT catch cross-tile mislinks (a stolen cluster is internally consistent) — the
mislink detectors are the shared-cluster flag, GAP rows, and the manual audit.

**Next** — (1) OCR the cover page for promo dates (spec: dates from PAGE);
(2) `output_writer.py`: 21-column CSV per OUTPUT_SPEC; (3) batch runner over all
pages of a PDF (skip pages with no matches); (4) then the whole-leaflet run.

---

## Session 7 — authoritative output spec + OCR install

- **PM supplied the authoritative output spec** -> `docs/OUTPUT_SPEC.md` (21 cols,
  CSV, prices 3dp always, one row per product per page, computed discount, no merge).
  Supersedes the kickoff column list.
- Decisions LOCKED: **dates from PAGE** (OCR the date line, derive week from start
  date); **scope = everything in master** (no sub-cat filter; unknown buckets->Other).
- Currency is multi-country by filename; 4 retailers (Carrefour/Lulu/SharafDG/Emax).
- **PaddleOCR 3.7.0 + paddlepaddle 3.3.1 installed** (venv ~1GB). 3.x API =
  `PaddleOCR(...).predict(img)` -> rec_texts/rec_scores/rec_polys.
- `src/ocr_page.py` — renders a page UPSCALED, OCRs it, writes audit artifacts:
  `output/ocr/<stem>_tokens.csv` + `_annotated.png` (boxes coloured by confidence).
  This is the OCR-tracking tool the PM asked for.
- Sub-category RESOLVED: output uses the master's own taxonomy verbatim (REF:
  TMF/BMF/SBS/FDR/ODR/...; WM: WASHER/TLD/COMBO/...). No translation layer.

**In progress** — first real OCR run on p.25 @3x (downloading models). Next: feed
tokens to matcher + prices, tune bbox-proximity linking, emit first real rows.

---

## Session 6 — per-file country/currency resolver

Currency is multi-country (AED/SAR/QAR/KWD/BHD/OMR), resolved PER FILE from the
filename (PM convention: `uae c4 ...`, `kuwait lulu ...`). Critical: KWD/BHD/OMR
use **3 decimals** (fils/baisa), AED/SAR/QAR use **2** — wrong decimals silently
corrupts every price, so this must be set before Stage 3 parsing.

**Done**
- `src/country_config.py` — `resolve(filename, country_override=None)` -> country,
  currency, decimals, retailer. Never guesses: no country token => UNKNOWN/STOP;
  caller must pass an override. Extensible alias tables for countries + retailers.
- Verified: `uae c4`->AED/2dp/Carrefour, `kuwait lulu`->KWD/3dp/Lulu,
  `oman emax`->OMR/3dp. Sample `Temporary Printing Window.pdf` -> UNKNOWN (correct);
  use `country_override="UAE"` for it (grocery prices were 2dp -> consistent w/ AED).

**Retailers CONFIRMED (4 for now):** Carrefour (`c4`), Lulu, SharafDG (`sharaf`),
Emax. Countries: 6 GCC (UAE/KSA/Qatar/Kuwait/Bahrain/Oman).

**Also done this session**
- `src/prices.py` — currency-aware price + `%` finder and RRP/PRP resolver.
  Higher price = RRP, lower = PRP, CROSS-CHECKED against the `-NN%` badge
  (`RRP*(1-NN%) ≈ PRP`). Agree -> conf 0.97; disagree -> flag (catches OCR price
  misreads). Validated on real p.25 numbers (LG/Samsung/Hitachi) + a KWD 3dp case.
  Fixed regex bug: `-31%` was matching `31` as a price -> added `(?!\s*%)` guard.

**Next action** — PaddleOCR install running (background). Then OCR p.25 with
ctx=UAE/AED/2dp: render upscaled -> tokens+boxes -> matcher (codes) + prices
(bbox proximity) -> first real end-to-end rows.

---

## Session 5 — BOM<->leaflet-name crosswalk (alias layer)

Root cause of the `RT50CG6404S9` "not in master": leaflets print the **consumer /
marketing model name**; the master stores the **BOM code**. PM supplied a 14-entry
crosswalk (`data/master/bom_to_model.json`, key = BOM, value = leaflet name).
Renames follow a capacity-forward pattern: RT38->RT50, RT47->RT66, RT53->RT75,
RT58->RT81, RT62->RT85.

**Done**
- Verified all 14 BOM keys exist in master (join is safe). 4 are self-maps
  (leaflet name == BOM), already present -> skipped.
- `build_master.py` now emits an **alias layer**: each renamed leaflet name becomes
  a matchable row (`is_alias=True`, `bom_code` -> the master BOM). New cols:
  `bom_code`, `is_alias`. Master: 3215 base + 10 alias = **3225 rows, 12 cols**.
- Aliases get the FULL matcher treatment — `RT50CG6404S9` matches exact, and
  `RT5OCG64O4S9` (OCR 0->O) recovers via confusion, both resolving to SAMSUNG/TMF
  and tracing back to BOM `RT38CG6404S9AE`.
- Eval re-run: no regression (CLEAN/CONFUSION 100%, 0 misjudged, 0 new collisions).
- `matcher.info()` now returns `bom_code` + `is_alias`.

Note: crosswalk is currently REF-only (Samsung). Extend the JSON if WM or other
brands have the same BOM-vs-leaflet rename.

**Next action** — unchanged: install PaddleOCR, OCR page 25, run real end-to-end.
Confirmed settings: PaddleOCR + AED currency.

---

## Session 4 — first real leaflet inspected (BIG findings)

File: `Temporary Printing Window.pdf` (28 pages, 55 MB).

**1. It's a browser "Print to PDF" → every page is ONE flat image (~150 DPI,
1273×1800 PNG). No text layer** (the only extractable text is print-chrome:
timestamp, "about:blank", page numbers). => **We are in the OCR branch.**

**2. It's a mixed Carrefour flyer, not a dedicated appliance leaflet.** ~24 pages
groceries; electronics on pp.24–27; **fridges + washers are on ~1 page (p.25)**.
Implication: only 1–2 pages per leaflet are in scope. The master lookup naturally
filters — no food item matches an appliance code. Need a page/section pre-filter
so we don't OCR 28 pages of biscuits.

**3. Layout answers Stages 3–4 (couldn't have guessed blind):**
- Price pattern is clean + consistent: big red PROMO price, `-NN%` badge, and a
  struck-through ORIGINAL price below. => PRP = red price, RRP = strikethrough.
- Model code printed in a small spec line: `"<Brand> Fridge <CODE>"`, plus a
  separate `"Net Capacity: 391L …"` line => `capacity_on_leaflet` is available.
- Bilingual EN/AR. Currency shows as a stylised glyph; prices ~1699–2599 fit
  **AED (UAE)** best — CONFIRM country/currency with PM.

**4. Matcher checked on 3 real p.25 codes:**
- LG `LTC752HFCM` -> EXACT ✓
- Hitachi `RV760PUK7K4PWH` -> EXACT ✓
- Samsung `RT50CG6404S9` -> genuinely NOT in master (nearest 77%) -> correctly
  flagged. Real master coverage gap; "not in master" is a legit flag category and
  a useful report for the PM in its own right.

**5. OCR difficulty is real.** A human (me) misread all three codes at normal zoom
(`RV760`→RV750, `RT50CG6404`→RT50CC640A); only legible at 8–14× crops. At native
150 DPI, OCR WILL err on these codes — expect a real review pile. Render pages
UPSCALED before OCR. This validates the conservative matcher design.

**Open items raised**
- Confirm currency/country (looks AED).
- Region-tag generalization: master appends region without a slash sometimes
  (`RT38CG6404S9AE`); leaflets print without it. `build_master` only strips after
  `/`. Generalize stem extraction when it matters (didn't bite us here).

**Next action** — install OCR (PaddleOCR or alt), OCR **page 25 only**, feed tokens
to the matcher, and see real (not synthetic) recovery. Then build Stage 3 price
association on real boxes.

---

## Session 3 — Stage 2 matcher built + validated (no leaflets needed)

**Done**
- `src/matcher.py` — token -> model code, or a flag. Policy, in order:
  exact -> short-code-exact-only -> confusion recovery -> fuzzy(85, margin) ->
  near-tie confusion disambiguation. Never silently guesses; flags carry a reason
  + best_guess for reviewers.
- Confusion map tightened to the spec set (`0O 1IL 5S 8B 2Z`, + `6G`); wide maps
  over-collapse distinct codes.
- `src/eval_matcher.py` — synthetic OCR-noise harness (no PDFs). Results:
  - CLEAN: 100% correct, 0 wrong.
  - CONFUSION (realistic scan noise): **100% correct, 0 wrong.**
  - HEAVY (2-edit destructive): 49% correct / 49% flagged / 0.8% collision with
    another real code (unavoidable) / **0.5% genuine misjudgment**.
  - Short codes: near-misses always flagged, never fuzzy-matched. ✓
- Design note: colour/finish is IN the code (`…B4`) with no separable colour
  suffix, so the matcher matches on `stem_code` (region tag stripped) as a whole;
  the confusion machinery guards the low-redundancy trailing chars.

**Next action** — Stages 3–6 are the ones that genuinely need a real leaflet
(price regex, bbox proximity, RRP/PRP, output). Nothing more to build blind.
Waiting on: one leaflet PDF in `data/leaflets/`.

---

## Session 2 — master list inspected + cleaned

**Reality check on the master list** (differs from kickoff assumptions):
- **3,215 model codes** (not ~200), 98 brands. Samsung ~179 rows; ~3,000 competitor.
- **No `capacity` column** → decision: capacity comes from the LEAFLET only, no
  master cross-check. Keep `capacity_on_leaflet` as read-only.
- **No `segment` column** → mapping: `segment = Product Category (REF/WM)`,
  `subcategory = Sub Category`.
- Suffix = **region tag** (`/AE`,`/SG`,`/GU`, 109 codes), NOT colour. Colour is
  baked into the code (`…B4`). Open: do leaflets print the region tag? (needs a PDF)

**Done**
- `src/build_master.py` → cleans raw list to `data/master/master_clean.xlsx`:
  brand/subcat casing normalized, 7 dup rows dropped, derived cols added
  (`norm_code`, `stem_code`, `region_suffix`, `code_len`, `is_short`).
- **63 short codes (≤5 chars)** flagged `is_short` — matcher must match these
  EXACTLY, never fuzzy (`148`, `66100` would false-match all over a leaflet).
- Known gotcha: empty `region_suffix` reads back from xlsx as NaN → matcher loader
  must `.fillna('')`.

**Open decisions (can defer — don't block matcher build)**
- Scope: list includes DRYERS + washer-dryer COMBOs. Extract those or REF+Washer only?
- Brands: all 98, or a tracked shortlist?

**Next action** — build `src/matcher.py` (Stage 2). Leaflet-independent; unit-test
with synthetic OCR noise now.

---

## Session 1 — project scaffold

**Done**
- Created fresh project at `~/Desktop/leaflet-price-extractor`.
- Folder skeleton: `data/leaflets`, `data/master`, `src`, `output`, `docs`, `notebooks`.
- `requirements.txt` (pdfplumber, PyMuPDF, rapidfuzz, pandas, openpyxl; PaddleOCR
  commented out until we confirm scans exist).
- `src/inspect_pdf.py` — Stage 1 step 0: reports whether a leaflet has a text
  layer and prints sample words-with-boxes from the busiest page.

**Not done yet (deliberately)** — no pipeline code until we've inspected a real
leaflet and the master list. Don't build blind.

**Next action**
1. Drop one leaflet PDF into `data/leaflets/` and the master Excel into `data/master/`.
2. `python src/inspect_pdf.py data/leaflets/<file>.pdf`
3. Report back: digital or scan, and how clean the extraction looks.

---

## Open questions for the PM (answer before pipeline code)

1. **Digital PDF or scan?** — answer by running `inspect_pdf.py` first.
2. **Does the model-code suffix affect price, or is it just colour?** If colour
   only, we match on stem alone and carry the suffix as unresolved metadata —
   this deletes the whole suffix-confusion problem. *Check the master list first.*
3. **Row granularity** — one row per model per leaflet, or one row per price point
   (same model appearing multiple times / multiple date ranges)?
4. **Promo dates** — printed on the page, in the filename, or supplied separately?
5. **Currency + language variation** across countries — same layout family or
   genuinely different artwork?

---

## Design decisions locked (do not relitigate)

- No ML training. Model code = category label. Detection = lookup vs ~200 strings.
- Stem: fuzzy match (rapidfuzz, ~85). Suffix: never fuzzy — exact match via a
  confusion map (`0↔O`, `1↔I↔l`, `5↔S`, `8↔B`, `2↔Z`), scoped to valid suffixes
  for that stem. One survivor → take it; two+ → flag.
- Require a margin between best and second-best candidate, else → review pile.
- Confidence threshold + review pile from day one. ~85% auto / 15% flagged is a win.
- Never silently guess. Ambiguity → `flag_reason` column.
- Deterministic Python. Every decision inspectable and explainable.

## Output columns (Stage 6)

leaflet_source · promo_start · promo_end · segment · subcategory · model_code ·
capacity · capacity_on_leaflet · rrp · promo_price · confidence · flag_reason · page_number
