"""
Whole-leaflet runner: PDF -> OCR every page -> extract -> one CSV per spec.

Flow:
  1. Resolve country/currency/retailer from the filename (or --country override).
     Refuses to run without a currency — wrong decimals silently corrupt prices.
  2. OCR pages (models cached after first run; each page saves audit artifacts).
  3. Promo dates: scan the first --date-pages pages' tokens for a date range.
  4. Extract product rows per page; every page's rows (incl. GAP reviews) collect.
  5. Write the 21-column CSV + print a run summary.

Usage:
    python src/run_leaflet.py "data/leaflets/<file>.pdf" [--country UAE]
        [--year 2026] [--zoom 3] [--pages 24-28] [--date-pages 3]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from country_config import resolve
from profiles import get_profile
from layout_health import assess
from prefilter import skim
from dates import parse_promo_dates
from extract_page import extract
from matcher import Matcher
from ocr_page import render_page, ocr_image, save_audit, prewarm_pages
from output_writer import to_output_row, write_outputs
from validate import validate_rows

import fitz


def parse_pages(spec: str | None, n_pages: int) -> list[int]:
    if not spec:
        return list(range(1, n_pages + 1))
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return [p for p in out if 1 <= p <= n_pages]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--country", default=None, help="override when filename has no country token")
    ap.add_argument("--year", type=int, default=None, help="promo year hint if not printed")
    ap.add_argument("--zoom", type=float, default=None,
                    help="OCR render scale; default = retailer profile (Sharaf 4x, else 3x)")
    ap.add_argument("--pages", default=None,
                    help="e.g. '24-28' or '1,25'; default: PRE-FILTER finds appliance pages")
    ap.add_argument("--no-prefilter", action="store_true",
                    help="full-OCR every page instead of skimming for appliance pages")
    ap.add_argument("--date-pages", type=int, default=3, help="scan first N pages for dates")
    args = ap.parse_args()

    ctx = resolve(args.pdf.name, args.country)
    if not ctx.ok:
        raise SystemExit(
            f"Cannot resolve country/currency from filename {args.pdf.name!r}.\n"
            f"Pass --country (UAE/KSA/Qatar/Kuwait/Bahrain/Oman). Refusing to guess.")
    profile = get_profile(ctx.retailer, ctx.country)
    zoom = args.zoom if args.zoom else profile.zoom
    print(f"context: {ctx.country} / {ctx.currency} ({ctx.decimals}dp) / "
          f"{ctx.retailer} [{ctx.resolved_by}]  |  profile: {profile.name} zoom={zoom}")

    n_pages = len(fitz.open(args.pdf))
    matcher = Matcher()
    if args.pages or args.no_prefilter:
        pages = parse_pages(args.pages, n_pages)
    else:
        rep = skim(args.pdf, n_pages, matcher)
        print(rep.summary())
        pages = rep.candidates
        if not pages:
            print("pre-filter found NO appliance candidates — nothing to extract "
                  "(skim report above shows per-page signals; use --no-prefilter "
                  "or --pages to override)")
    # dates live on the cover — always OCR the date pages too
    date_scan = [p for p in range(1, args.date_pages + 1) if p <= n_pages]
    pages = sorted(set(pages) | set(date_scan))
    prewarm_pages(args.pdf, pages, zoom, args.pdf.stem.replace(" ", "_"), workers=3)

    all_texts_for_dates: list[str] = []
    out_rows: list[dict] = []
    stats = {"pages": 0, "ok": 0, "flagged": 0, "gaps": 0}

    # dates parsed after the date-pages are OCR'd; rows built at the end so every
    # row carries the same PromoDates.
    extracted = []
    for pno in pages:
        img = render_page(args.pdf, pno, zoom)
        tokens = ocr_image(img)
        save_audit(img, tokens, f"{args.pdf.stem}_p{pno}".replace(" ", "_"))
        if pno <= args.date_pages:
            all_texts_for_dates.extend(t.text for t in tokens)
        rows = extract(tokens, page_no=pno, price_decimals=ctx.decimals, matcher=matcher,
                       price_position=profile.price_position)
        stats["pages"] += 1
        for r in rows:
            if not r.matched_code:
                stats["gaps"] += 1
            elif r.flags:
                stats["flagged"] += 1
            else:
                stats["ok"] += 1
        extracted.extend(rows)
        print(f"  p{pno}: {len(tokens)} tokens -> {len(rows)} rows")

    extracted = validate_rows(extracted, ctx.currency)

    dates = parse_promo_dates(all_texts_for_dates, args.year)
    print(f"dates: {dates.start} -> {dates.end} ({dates.year_week})  flags={dates.flags!r}")

    health = assess(extracted, profile, ctx, dates.year_week)
    for v in health["verdicts"]:
        print(f"  !! LAYOUT HEALTH: {v}")
    if not health["verdicts"]:
        print("layout health: OK (matches profile)")

    for r in extracted:
        out_rows.append(to_output_row(r, ctx, dates, year_hint=args.year))

    base = Path("output") / f"Leaflet_Extraction_{args.pdf.stem}".replace(" ", "_")
    written = write_outputs(out_rows, base)
    print(f"\n=== run summary ===")
    print(f"pages OCR'd:    {stats['pages']}")
    print(f"rows clean:     {stats['ok']}")
    print(f"rows flagged:   {stats['flagged']}")
    print(f"rows GAP:       {stats['gaps']}  (code-shaped, not in master -> review)")
    for kind, (path, n) in written.items():
        print(f"wrote {kind:<13} -> {path}  ({n} rows)")


if __name__ == "__main__":
    main()
