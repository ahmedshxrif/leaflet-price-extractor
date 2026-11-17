"""
Master raw-data store — the accumulating week-by-week dataset.

One file: output/master_raw.csv (same 21 columns as every extract). Extract
CSVs from each leaflet run get merged in; the store grows week over week and is
the input to whatever analysis comes next.

Merge semantics — IDEMPOTENT by leaflet file:
  All existing rows whose 'Leaflet / File Name' matches an incoming file are
  REPLACED by the incoming rows. Re-running a week fixes it in place; nothing
  ever duplicates. History from other files is never touched.

Only matched-SKU rows belong here (feed it *_extract.csv, not *_review.csv) —
enforced with a warning, not silently.
"""

from __future__ import annotations

import csv
from pathlib import Path

from output_writer import HEADERS

MASTER = Path("output/master_raw.csv")


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def merge_into_master(incoming_rows: list[dict],
                      master_path: Path = MASTER) -> dict:
    """Merge rows into the master store. Returns a summary dict."""
    skipped = [r for r in incoming_rows if not r.get("Brand")]
    rows = [r for r in incoming_rows if r.get("Brand")]

    existing = _read(master_path)
    incoming_files = {r["Leaflet / File Name"] for r in rows}
    kept = [r for r in existing if r["Leaflet / File Name"] not in incoming_files]
    replaced = len(existing) - len(kept)

    merged = kept + rows
    merged.sort(key=lambda r: (r.get("Year-Week") or "", r.get("Country / Region") or "",
                               r.get("Retailer") or "", r.get("Leaflet / File Name") or ""))

    master_path.parent.mkdir(parents=True, exist_ok=True)
    with open(master_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(merged)

    weeks_touched = write_week_files(merged, master_path.parent)

    return {"added": len(rows), "replaced": replaced, "total": len(merged),
            "skipped_unmatched": len(skipped), "files": sorted(incoming_files),
            "week_files": weeks_touched}


def write_week_files(merged: list[dict], out_dir: Path) -> list[str]:
    """One deliverable per week: Leaflet_Extraction_<Year-Week>.csv, holding
    every leaflet's rows for that week. Regenerated in full on every merge so
    they always mirror the master. Rows with no resolvable week land in
    Leaflet_Extraction_unknown-week.csv (loud, not lost)."""
    by_week: dict[str, list[dict]] = {}
    for r in merged:
        wk = r.get("Year-Week") or "unknown-week"
        by_week.setdefault(wk, []).append(r)
    written = []
    for wk, rows in sorted(by_week.items()):
        path = out_dir / f"Leaflet_Extraction_{wk}.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=HEADERS)
            w.writeheader()
            w.writerows(rows)
        written.append(str(path))
    return written


def merge_csvs(paths: list[Path], master_path: Path = MASTER) -> dict:
    rows: list[dict] = []
    for p in paths:
        rows.extend(_read(p))
    return merge_into_master(rows, master_path)


def master_stats(master_path: Path = MASTER) -> dict:
    rows = _read(master_path)
    weeks = sorted({r.get("Year-Week", "") for r in rows if r.get("Year-Week")})
    files = sorted({r.get("Leaflet / File Name", "") for r in rows})
    return {"rows": len(rows), "weeks": weeks, "files": files}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Merge extract CSVs into the master store.")
    ap.add_argument("csvs", nargs="+", type=Path)
    args = ap.parse_args()
    s = merge_csvs(args.csvs)
    print(f"added {s['added']} rows ({s['replaced']} replaced) from {len(s['files'])} file(s)"
          f" | skipped unmatched: {s['skipped_unmatched']} | master total: {s['total']}")
