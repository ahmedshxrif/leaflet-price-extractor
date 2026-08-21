---
title: Leaflet Price Extractor
emoji: 🧾
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8501
pinned: false
---

# Leaflet Price Extractor

Deterministic pipeline that extracts appliance pricing (REF/WM) from Gulf
retail promo leaflets into a weekly CSV dataset. No ML training — product
detection is a lookup against the Samsung + competitor model master list;
every decision is inspectable and flagged, never guessed.

**Live state and history: [docs/HANDOVER.md](docs/HANDOVER.md) — read its top
entry first.** Output schema: [docs/OUTPUT_SPEC.md](docs/OUTPUT_SPEC.md).

## The weekly routine

```bash
# 1. Drop leaflets into their week folder, named COUNTRY_RETAILER_WXX.pdf
#    e.g. data/leaflets/2026-w30/BAH_LULU_W30.pdf
#    (loose files in data/leaflets/ can be week-filed with:
#     ./.venv/bin/python src/rename_by_week.py)

# 2. Run the batch (crash-safe: interrupt any time, re-run to continue)
./.venv/bin/python src/run_batch.py --year 2026            # --jobs 3 for backfills

# 3. Collect the deliverable
open output/Leaflet_Extraction_2026-W30.csv
```

Everything resolves from the filename (country → currency/decimals, retailer →
layout profile, week). No per-file configuration.

Or point-and-click: `./.venv/bin/streamlit run app.py` — upload, run, review
the three result tabs, inspect OCR audit images, merge into the master.

## Outputs (all in `output/`)

| File | What it is |
|---|---|
| `Leaflet_Extraction_<Year-Week>.csv` | **The weekly deliverable** — all that week's leaflets, 21 columns per spec |
| `master_raw.csv` | The ever-growing full dataset (idempotent merges; re-running a leaflet replaces its rows) |
| `Leaflet_Extraction_<leaflet>.csv` | Per-leaflet detail |
| `…_review.csv` | Codes found on appliance pages but missing from the master list → hand to the Model List owner |
| `…_out_of_scope.csv` | Gadget/grocery noise, kept for audit |
| `ocr/` | Token cache + annotated audit images. **Keep it** — it makes re-runs and logic replays near-instant |
| `batch_state.json` / `layout_health.jsonl` | Batch ledger / layout-drift history |

## How it works (one paragraph)

Each leaflet is skimmed at low zoom to find the appliance pages (keyword quorum,
master-code hit, or keyword+code-shaped token — validated zero-miss on all four
retailers), then only those pages get full OCR at the retailer's profile zoom.
Tokens are matched against the cleaned master list (exact → confusion-map →
fuzzy-with-margin; short codes exact-only), prices are linked by tile geometry
per retailer profile, cross-checked (discount badges, price plausibility,
member/card prices separated), validated column-by-column (REF must have litres,
WM must have kg, promo < regular…), and written per spec. Anything ambiguous is
flagged, never guessed.

## Per-retailer rules & drift

Layout rules live in `data/master/layout_profiles.json` (zoom, price position,
notes, validation trail) — editable data, with `Retailer/Country` overrides.
Every run writes signals to `output/layout_health.jsonl` and warns loudly if a
page stops matching its profile (retailer redesigns announce themselves).

## Key source files

`src/run_batch.py` (batch, resumable) · `src/run_leaflet.py` (single file) ·
`src/prefilter.py` (appliance-page skim) · `src/extract_page.py` (tile linking) ·
`src/matcher.py` (code matching) · `src/prices.py` · `src/validate.py` ·
`src/profiles.py` + `src/layout_health.py` (rules & drift) ·
`src/master_store.py` (dataset + weekly files) · `src/build_master.py`
(rebuild after editing Model List.xlsx or the BOM crosswalk).

## Setup (once)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # incl. paddleocr — OCR models download on first run
python src/build_master.py        # cleans Model List.xlsx -> master_clean.xlsx
```
