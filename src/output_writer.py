"""
Stage 6 — write the final CSV, exactly per docs/OUTPUT_SPEC.md.

Rules that live here (not in extraction):
  - Prices formatted at 3 decimals ALWAYS (Gulf fils convention), regardless of
    the currency's parse decimals.
  - Discount Amount / Discount % are COMPUTED from the two prices. The leaflet's
    badge % is only a cross-check: if it disagrees with the computed %, the
    computed value is kept and the discrepancy goes to Notes / Flags.
  - Never infer: missing values stay blank, with a note.
  - One row per product per page appearance; no merging.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from country_config import LeafletContext
from dates import PromoDates
from extract_page import ExtractedRow

_FILENAME_WEEK = re.compile(r"[_\s][wW](\d{1,2})\b")

HEADERS = [
    "Leaflet / File Name", "Retailer", "Country / Region", "Week Number",
    "Year-Week", "Promo Start Date", "Promo End Date", "Promo Duration (days)",
    "Brand", "Model Number", "Product Category", "Sub-Category",
    "Capacity / Size", 'Regular Price ("was")', 'Promo Price ("now")',
    "Discount Amount", "Discount %", "Other Offer Details", "Page / Position",
    "Notes / Flags", "Currency",
]

COUNTRY_DISPLAY = {
    "UAE": "United Arab Emirates", "KSA": "Saudi Arabia", "Qatar": "Qatar",
    "Kuwait": "Kuwait", "Bahrain": "Bahrain", "Oman": "Oman",
}
RETAILER_DISPLAY = {
    "Carrefour": "Carrefour", "Lulu": "Lulu", "SharafDG": "Sharaf DG", "Emax": "Emax",
}

BADGE_TOLERANCE_PP = 1.5   # computed vs badge % may differ by this many points


def _fmt3(v: float | None) -> str:
    return f"{v:.3f}" if v is not None else ""


def to_output_row(r: ExtractedRow, ctx: LeafletContext, dates: PromoDates,
                  year_hint: int | None = None) -> dict:
    notes = [r.flags] if r.flags else []

    amount = pct_str = ""
    if r.rrp is not None and r.promo_price is not None and r.rrp > 0:
        diff = r.rrp - r.promo_price
        pct = diff / r.rrp * 100
        amount, pct_str = _fmt3(diff), f"{pct:.1f}%"
        if r.discount_pct_badge is not None and abs(pct - r.discount_pct_badge) > BADGE_TOLERANCE_PP:
            notes.append(f"badge says -{r.discount_pct_badge}% but computed {pct:.1f}%")
    elif r.promo_price is not None:
        notes.append("no regular price on leaflet — discount not computable")

    if dates.flags:
        notes.append(dates.flags)

    # cross-check: week encoded in the filename vs week derived from the page's
    # promo start date. The page wins (locked spec) — a mismatch is only noted.
    week_no, year_week = dates.week_number, dates.year_week
    m = _FILENAME_WEEK.search(ctx.source_file)
    if m and dates.week_number and int(m.group(1)) != dates.week_number:
        notes.append(f"filename says W{int(m.group(1)):02d} but promo dates on "
                     f"page give W{dates.week_number:02d} — page dates used")
    # fallback: no dates printed on the page (Emax mobile-sale flyers) -> take
    # the week from the filename so the row still lands in its weekly file.
    if not dates.found and m and year_hint:
        week_no = int(m.group(1))
        year_week = f"{year_hint}-W{week_no:02d}"
        notes.append("promo dates not printed — week taken from filename")

    return {
        "Leaflet / File Name": ctx.source_file,
        "Retailer": RETAILER_DISPLAY.get(ctx.retailer, ctx.retailer),
        "Country / Region": COUNTRY_DISPLAY.get(ctx.country, ctx.country),
        "Week Number": week_no or "",
        "Year-Week": year_week,
        "Promo Start Date": dates.start.strftime("%d/%m/%Y") if dates.start else "",
        "Promo End Date": dates.end.strftime("%d/%m/%Y") if dates.end else "",
        "Promo Duration (days)": dates.duration_days or "",
        # master stores brands UPPERCASE; print short brands as-is (LG, TCL),
        # title-case the rest (SAMSUNG -> Samsung, SUPER GENERAL -> Super General)
        "Brand": (r.brand if len(r.brand) <= 3 else r.brand.title()) if r.brand else "",
        "Model Number": r.model_number,
        "Product Category": r.product_category,
        "Sub-Category": r.sub_category,
        "Capacity / Size": r.capacity,
        'Regular Price ("was")': _fmt3(r.rrp),
        'Promo Price ("now")': _fmt3(r.promo_price),
        "Discount Amount": amount,
        "Discount %": pct_str,
        "Other Offer Details": r.other_details,
        "Page / Position": r.page,
        "Notes / Flags": "; ".join(n for n in notes if n),
        "Currency": ctx.currency,
    }


def write_csv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(rows)


def split_rows(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """(matched, review, out_of_scope).

    matched      — rows with master metadata: the actual dataset.
    review       — unmatched code candidates from APPLIANCE pages: each is an
                   OCR mangle or a genuine master coverage gap (worth a human).
    out_of_scope — unmatched rows from pages with zero master matches (TVs,
                   groceries, gadgets): kept for audit, not for review time.
    """
    matched = [r for r in rows if r["Brand"]]
    unmatched = [r for r in rows if not r["Brand"]]
    ooc = [r for r in unmatched if "out-of-category page" in r["Notes / Flags"]]
    review = [r for r in unmatched if "out-of-category page" not in r["Notes / Flags"]]
    return matched, review, ooc


def write_outputs(rows: list[dict], base: Path) -> dict[str, tuple[Path, int]]:
    """Write the three-file output set. Returns {kind: (path, row_count)}."""
    matched, review, ooc = split_rows(rows)
    out = {}
    for kind, subset, suffix in [("extract", matched, ""),
                                 ("review", review, "_review"),
                                 ("out_of_scope", ooc, "_out_of_scope")]:
        path = base.with_name(base.name + suffix + ".csv")
        if subset or kind == "extract":     # always write the main file
            write_csv(subset, path)
            out[kind] = (path, len(subset))
    return out
