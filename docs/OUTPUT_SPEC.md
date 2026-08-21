# Output Spec (AUTHORITATIVE) — supersedes the kickoff column list

One row **per product per page appearance**. CSV, exact headers, exact order.
Prices always **3 decimals** (Gulf fils convention) — even for 2-decimal
currencies, write `.000`. Never infer a missing value: blank + note in Notes/Flags.

## Columns (exact order)

| # | Header | Source in our pipeline |
|---|---|---|
| 1 | Leaflet / File Name | filename verbatim |
| 2 | Retailer | `country_config` retailer, printed form (e.g. "Sharaf DG") |
| 3 | Country / Region | `country_config` country (full name) |
| 4 | Week Number | from filename (`W28`) — CONFIRM source |
| 5 | Year-Week | `YYYY-Www` from year + week |
| 6 | Promo Start Date | DD/MM/YYYY — from page or filename — CONFIRM |
| 7 | Promo End Date | DD/MM/YYYY |
| 8 | Promo Duration (days) | end − start + 1 (inclusive) |
| 9 | Brand | master (via matched BOM), printed form |
| 10 | Model Number | **as printed on leaflet** (the alias/consumer name), exact, no spaces changed |
| 11 | Product Category | master: REF \| WM |
| 12 | Sub-Category | master sub_cat MAPPED to output taxonomy (see below) |
| 13 | Capacity / Size | **from leaflet** spec line, formatted per rules |
| 14 | Regular Price ("was") | `prices.resolve` RRP, 3dp |
| 15 | Promo Price ("now") | `prices.resolve` PRP, 3dp |
| 16 | Discount Amount | **computed** Regular − Promo, 3dp |
| 17 | Discount % | **computed** (Amount/Regular), 1dp, `%` sign |
| 18 | Other Offer Details | non-price offers verbatim-ish (free delivery, bundles) |
| 19 | Page / Position | leaflet page number (int) |
| 20 | Notes / Flags | extraction caveats + conflicts only; blank if clean |
| 21 | Currency | ISO code (AED/SAR/QAR/KWD/BHD/OMR) |

## Field rules that change our build

- **Discount is COMPUTED from prices**, not the badge. If the badge % disagrees
  with the computed value, keep computed in cols 16/17 and record the discrepancy
  in Notes/Flags. (Our `prices.py` already cross-checks; switch it to output the
  computed % and flag on badge mismatch.)
- **Model Number = leaflet-printed name** (RT50CG6404S9), not the BOM. We use the
  BOM only to join brand/category. Matcher already keeps the printed code.
- **Capacity from leaflet**, formatted:
  - REF: `<n> L (gross)`; if gross+net printed: `<n> L gross / <n> L net`
  - WM: `<n> kg`; Combo: `<n> kg wash / <n> kg dry`
  - Badge-vs-spec capacity conflict → keep nothing silently; note it.
- **Prices: 3 decimals in OUTPUT always.** (Parsing decimals stay per-currency in
  `country_config` — 2 vs 3 — so we READ correctly; we FORMAT at 3dp.)
- **No row merging.** Same SKU on 2 pages = 2 rows.

## Sub-Category — RESOLVED (PM confirmed)

**Use the master list's own sub-category taxonomy verbatim.** No translation
layer. The matched model's `sub_category` from `master_clean.xlsx` is written
straight to the output column:

REF: TMF · BMF · SBS · FDR · ODR · UPRIGHT FREEZER · BUILT IN
WM:  WASHER · TLD · COMBO · TWIN TUB · DRYER

(The FL/TL/"Single Door / Mini / Compact" wording in the original prompt's field
rules is superseded by this.)

## Decisions LOCKED

- **Dates/week source = PAGE.** OCR the promo date line off the leaflet (sample
  cover: "7 July – 18 July"). Derive Week Number + Year-Week from the START date's
  ISO week. Duration = end − start + 1 (inclusive). Year: not always printed on the
  date line — fall back to promo year from context/filename and flag if absent.
- **Scope = EVERYTHING in the master.** No sub-category filtering. All REF/WM
  sub-cats kept (incl. DRYER, TWIN TUB, UPRIGHT FREEZER, BUILT IN, COMBO);
  anything without a clean output bucket -> "Other".

## Still to confirm

- (none — all schema questions resolved)
