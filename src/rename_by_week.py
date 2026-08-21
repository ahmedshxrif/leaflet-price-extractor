"""
Week filer — names and folders incoming leaflets by business week.

Drop PDFs into data/leaflets/, run this, and every loose PDF is renamed with a
week suffix and moved into that week's folder. Incoming files arrive as
COUNTRY_RETAILER (per PM: 'BAH_LULU.pdf', 'QTR_LULU.pdf'); this completes them:

    data/leaflets/BAH_LULU.pdf
        -> data/leaflets/2026-w28/BAH_LULU_W28.pdf

"Week" = the TRAILING business week (the last fully completed ISO week — a
leaflet collected today belongs to the promo week that just ran), unless you
override it. Files already inside a week folder, or already carrying a _wNN
suffix, are left alone — safe to re-run any time.

Usage:
    python src/rename_by_week.py                 # trailing week from today
    python src/rename_by_week.py --week 27       # explicit week
    python src/rename_by_week.py --week 27 --year 2026
    python src/rename_by_week.py --dry-run       # show, don't touch
"""

from __future__ import annotations

import argparse
import re
from datetime import date, timedelta
from pathlib import Path

LEAFLETS = Path("data/leaflets")
WEEK_SUFFIX = re.compile(r"_w\d{1,2}$", re.I)
WEEK_FOLDER = re.compile(r"^\d{4}-w\d{1,2}$", re.I)


def trailing_business_week(today: date | None = None) -> tuple[int, int]:
    """(iso_year, iso_week) of the last fully completed ISO week."""
    today = today or date.today()
    last_week_day = today - timedelta(days=7)
    y, w, _ = last_week_day.isocalendar()
    return y, w


def file_leaflets(year: int, week: int, dry_run: bool = False) -> list[tuple[Path, Path]]:
    folder = LEAFLETS / f"{year}-w{week:02d}"
    moves: list[tuple[Path, Path]] = []
    for pdf in sorted(LEAFLETS.glob("*.pdf")):          # loose files only, not subfolders
        stem = pdf.stem
        m = WEEK_SUFFIX.search(stem)
        if m:                                            # already week-tagged ->
            wk = int(m.group(0)[2:])                     # file into ITS OWN week
            dest = LEAFLETS / f"{year}-w{wk:02d}" / pdf.name
        else:
            dest = folder / f"{stem}_W{week:02d}{pdf.suffix}"
        moves.append((pdf, dest))
    for src, dest in moves:
        print(f"  {src.name}  ->  {dest.relative_to(LEAFLETS)}")
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dest)
    return moves


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="File loose leaflet PDFs into week folders.")
    ap.add_argument("--week", type=int, default=None, help="ISO week override (1-53)")
    ap.add_argument("--year", type=int, default=None, help="year override (with --week)")
    ap.add_argument("--dry-run", action="store_true", help="show moves without doing them")
    args = ap.parse_args()

    if args.week:
        y, w = args.year or date.today().year, args.week
    else:
        y, w = trailing_business_week()
    print(f"filing loose PDFs under {y}-w{w:02d}"
          + (" (dry run)" if args.dry_run else ""))
    moves = file_leaflets(y, w, args.dry_run)
    if not moves:
        print("  nothing to file — no loose PDFs in data/leaflets/")
