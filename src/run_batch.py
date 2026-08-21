"""
Batch runner — process every leaflet in data/leaflets/**, crash-proof.

The backfill workflow: PM drops files named COUNTRY_RETAILER_WXX.pdf into week
folders (data/leaflets/2026-wNN/), runs this once, walks away. Everything
resolves from the filename; nothing needs manual input.

FOOLPROOF = resumable at every level. If the run dies at any second (ctrl-C,
crash, sleep, power), NOTHING done so far is lost and a re-run continues where
it stopped:

  - per LEAFLET:  output/batch_state.json ledger — files marked done are
                  skipped on re-run (--force redoes them).
  - per PAGE:     OCR tokens persist to output/ocr/*_tokens.csv the moment a
                  page finishes; on resume those pages replay from disk with
                  zero re-OCR (the expensive part).
  - per SKIM:     the skim report persists to *_skim.json when the skim ends;
                  resume reuses it.
  - per DATASET:  each leaflet's rows merge into master_raw.csv IMMEDIATELY
                  after that leaflet completes (idempotent replace-by-file),
                  so the master is always consistent with the ledger.
  - one bad PDF never kills the batch: it's marked failed in the ledger with
    the error, and the run moves on.

Usage:
    python src/run_batch.py                    # everything pending
    python src/run_batch.py --week 2026-w28    # one week folder
    python src/run_batch.py --force            # redo even 'done' files
    python src/run_batch.py --dry-run          # show the plan only
"""

from __future__ import annotations

import argparse
import atexit
import csv as _csv
import os
import json
import re
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

import fitz

from country_config import resolve
from decrypt import decrypt_if_needed
from dates import parse_promo_dates
from extract_page import extract
from layout_health import assess
from master_store import merge_csvs
from matcher import Matcher
from ocr_page import render_page, ocr_image, save_audit, Token, OUT_DIR, prewarm_pages
from output_writer import to_output_row, write_outputs
from prefilter import skim, SkimReport
from profiles import get_profile
from validate import validate_rows

LEAFLETS = Path("data/leaflets")
STATE = Path("output/batch_state.json")
PROGRESS = Path("output/batch_progress.json")
PIDFILE = Path("output/batch.pid")
STOPFILE = Path("output/batch.stop")


def _cleanup_pidfile() -> None:
    """Remove the pidfile on exit if it still points at THIS process — so a
    finished/crashed batch never leaves a stale 'running' signal behind."""
    try:
        if PIDFILE.exists() and PIDFILE.read_text().strip() == str(os.getpid()):
            PIDFILE.unlink()
    except OSError:
        pass


def write_progress(todo: int, completed: int, failed: int, current: list[str]) -> None:
    """Tiny status file the app polls for its progress bar."""
    PROGRESS.write_text(json.dumps({
        "todo": todo, "completed": completed, "failed": failed,
        "current": current,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }))


# ---------------- ledger (atomic) ----------------
def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=STATE.parent, delete=False) as f:
        json.dump(state, f, indent=1)
        tmp = f.name
    os.replace(tmp, STATE)       # atomic on POSIX AND Windows


# ---------------- reuse helpers ----------------
def load_tokens(path: Path) -> list[Token]:
    out = []
    with open(path) as f:
        for r in _csv.DictReader(f):
            if r["text"].strip():
                out.append(Token(r["text"], float(r["conf"]), float(r["x0"]),
                                 float(r["y0"]), float(r["x1"]), float(r["y1"])))
    return out


def page_tokens(pdf: Path, pno: int, zoom: float, stem: str) -> list[Token]:
    """OCR a page — or replay it from a previous run's saved tokens."""
    cached = OUT_DIR / f"{stem}_p{pno}_tokens.csv"
    if cached.exists():
        return load_tokens(cached)
    img = render_page(pdf, pno, zoom)
    tokens = ocr_image(img)
    save_audit(img, tokens, f"{stem}_p{pno}")
    return tokens


def cached_skim(pdf: Path, n_pages: int, matcher: Matcher,
                workers: int = 3) -> SkimReport:
    report_path = OUT_DIR / f"{pdf.stem}_skim.json".replace(" ", "_")
    if report_path.exists():
        d = json.loads(report_path.read_text())
        return SkimReport(**d)
    return skim(pdf, n_pages, matcher, log=lambda s: None, workers=workers)


# ---------------- one leaflet ----------------
def process_leaflet(pdf: Path, matcher: Matcher, year_hint: int | None,
                    inner_workers: int = 3) -> dict:
    decrypt_if_needed(pdf)              # NASCA de-DRM on Windows; no-op on Mac
    ctx = resolve(pdf.name)
    if not ctx.ok:
        raise ValueError(f"filename does not resolve a country: {pdf.name!r} "
                         f"(expected COUNTRY_RETAILER_WXX)")
    profile = get_profile(ctx.retailer, ctx.country)
    stem = pdf.stem.replace(" ", "_")
    n_pages = len(fitz.open(pdf))

    rep = cached_skim(pdf, n_pages, matcher, workers=inner_workers)
    date_pages = [p for p in (1, 2, 3) if p <= n_pages]
    pages = sorted(set(rep.candidates) | set(date_pages))
    prewarm_pages(pdf, pages, profile.zoom, stem, workers=inner_workers)

    date_texts, extracted = [], []
    for pno in pages:
        tokens = page_tokens(pdf, pno, profile.zoom, stem)
        if pno in date_pages:
            date_texts.extend(t.text for t in tokens)
        if pno in rep.candidates:
            extracted.extend(extract(tokens, page_no=pno,
                                     price_decimals=ctx.decimals, matcher=matcher,
                                     price_position=profile.price_position))

    extracted = validate_rows(extracted, ctx.currency)
    dates = parse_promo_dates(date_texts, year_hint)
    rows = [to_output_row(r, ctx, dates, year_hint=year_hint) for r in extracted]
    written = write_outputs(rows, Path("output") / f"Leaflet_Extraction_{stem}")
    health = assess(extracted, profile, ctx, dates.year_week)

    # NOTE: no master merge here — with --jobs > 1, concurrent workers merging
    # would be a read-modify-write race on master_raw.csv. The PARENT merges
    # serially as results arrive (single writer -> race-free by construction).
    return {
        "status": "done",
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "retailer": ctx.retailer, "country": ctx.country,
        "year_week": dates.year_week,
        "pages_ocrd": len(pages), "candidates": rep.candidates,
        "extract_rows": written["extract"][1],
        "review_rows": written.get("review", (None, 0))[1],
        "extract_csv": str(written["extract"][0]),
        "health_verdicts": health["verdicts"],
    }


# --- worker entry for --jobs > 1 (each process owns its models + matcher) ---
_WORKER_MATCHER = None


def _job_init() -> None:
    global _WORKER_MATCHER
    _WORKER_MATCHER = Matcher()


def _job(args: tuple) -> tuple[str, dict]:
    key, pdf_str, year_hint, inner = args
    try:
        result = process_leaflet(Path(pdf_str), _WORKER_MATCHER, year_hint,
                                 inner_workers=inner)
        return key, result
    except Exception as e:
        return key, {"status": "failed", "error": f"{type(e).__name__}: {e}",
                     "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}


# ---------------- the batch ----------------
def main() -> None:
    atexit.register(_cleanup_pidfile)
    STOPFILE.unlink(missing_ok=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", default=None, help="only this week folder, e.g. 2026-w28")
    ap.add_argument("--year", type=int, default=None, help="promo year hint")
    ap.add_argument("--force", action="store_true", help="redo files already done")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ocr-budget", type=int, default=4,
                    help="max concurrent OCR processes in total (raise on a "
                         "big machine, e.g. 12 on a 16-core server)")
    ap.add_argument("--jobs", type=int, default=1,
                    help="leaflets processed concurrently (2-3 for backfills; "
                         "each job spawns its own OCR workers — watch RAM)")
    args = ap.parse_args()

    root = LEAFLETS / args.week if args.week else LEAFLETS
    pdfs = sorted(root.rglob("*.pdf"))
    state = load_state()

    todo, skip = [], []
    for pdf in pdfs:
        key = str(pdf.relative_to(LEAFLETS))
        if not args.force and state.get(key, {}).get("status") == "done":
            skip.append(key)
        else:
            todo.append((key, pdf))

    print(f"batch: {len(pdfs)} PDFs found | {len(skip)} already done | {len(todo)} to process")
    if args.dry_run:
        for key, _ in todo:
            print(f"  would process: {key}")
        return
    if not todo:
        return

    prog = {"completed": 0, "failed": 0}
    in_flight: list[str] = []
    write_progress(len(todo), 0, 0, [])

    def finish(key: str, result: dict, i: int, n: int) -> None:
        """Parent-only: merge + ledger (single writer -> no race)."""
        if result.get("status") == "done":
            merge = merge_csvs([Path(result.pop("extract_csv"))])
            result["master_added"] = merge["added"]
            print(f"[{i}/{n}] {key}\n  done: {result['extract_rows']} rows -> "
                  f"master (+{merge['added']}), {result['review_rows']} review, "
                  f"week {result['year_week'] or '?'}"
                  + (f"  !! {'; '.join(result['health_verdicts'])}"
                     if result.get("health_verdicts") else ""))
        else:
            print(f"[{i}/{n}] {key}\n  FAILED: {result.get('error')}  (batch continues)")
        state[key] = result
        save_state(state)          # ledger updated after EVERY file, atomically
        prog["completed" if result.get("status") == "done" else "failed"] += 1
        if key in in_flight:
            in_flight.remove(key)
        write_progress(len(todo), prog["completed"], prog["failed"], in_flight)

    # hard budget: jobs x inner OCR workers <= 4 total — nested pools multiplied
    # (2 jobs x 3 skim workers + prewarm = 8+ paddle processes) and froze the
    # PM's laptop. Never again.
    inner = max(1, args.ocr_budget // max(args.jobs, 1))
    try:
        if args.jobs > 1:
            from concurrent.futures import ProcessPoolExecutor, as_completed
            with ProcessPoolExecutor(max_workers=args.jobs,
                                     initializer=_job_init) as ex:
                futs = {}
                for key, pdf in todo:
                    futs[ex.submit(_job, (key, str(pdf), args.year, inner))] = key
                    in_flight.append(key)
                write_progress(len(todo), 0, 0, in_flight)
                for i, fut in enumerate(as_completed(futs), 1):
                    key, result = fut.result()
                    finish(key, result, i, len(todo))
                    if STOPFILE.exists():
                        print("stop requested — no new files will start "
                              "(in-flight ones finish; all saved)")
                        for f in futs:
                            f.cancel()
                        break
        else:
            matcher = Matcher()
            for i, (key, pdf) in enumerate(todo, 1):
                if STOPFILE.exists():
                    print("stop requested — exiting (completed files are saved)")
                    break
                in_flight.append(key)
                write_progress(len(todo), prog["completed"], prog["failed"], in_flight)
                try:
                    result = process_leaflet(pdf, matcher, args.year,
                                             inner_workers=inner)
                except Exception as e:
                    result = {"status": "failed",
                              "error": f"{type(e).__name__}: {e}",
                              "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                    traceback.print_exc()
                finish(key, result, i, len(todo))
    except KeyboardInterrupt:
        print("\ninterrupted — everything completed so far is saved; "
              "re-run to continue from here")
        raise SystemExit(130)

    done = sum(1 for v in state.values() if v.get("status") == "done")
    failed = [(k, v["error"]) for k, v in state.items() if v.get("status") == "failed"]
    print(f"\n=== batch complete: {done} done, {len(failed)} failed ===")
    for k, err in failed:
        print(f"  failed: {k} — {err}")


if __name__ == "__main__":
    main()
