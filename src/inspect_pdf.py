"""
Stage 1, step 0 — Is this leaflet a digital PDF (has a text layer) or a scan?

This answers the single most important question before any pipeline work:
  - Digital PDF with embedded text  -> pdfplumber gives us words + boxes, no OCR.
  - Scanned image (no text layer)   -> we need PaddleOCR instead.

It does NOT extract prices or match models. It just inspects and reports, so we
can decide the approach with eyes on real output. Run it on one leaflet first.

Usage:
    python src/inspect_pdf.py data/leaflets/some-leaflet.pdf
    python src/inspect_pdf.py data/leaflets/some-leaflet.pdf --page 3
"""

import argparse
import sys
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("pdfplumber not installed. Run: pip install -r requirements.txt")


def inspect(pdf_path: Path, sample_page: int | None) -> None:
    if not pdf_path.exists():
        sys.exit(f"File not found: {pdf_path}")

    print(f"\n=== Inspecting: {pdf_path.name} ===\n")

    with pdfplumber.open(pdf_path) as pdf:
        n_pages = len(pdf.pages)
        print(f"Pages: {n_pages}")

        # Per-page tally: how much extractable text, how many images.
        # A digital PDF has words on (nearly) every page. A pure scan has ~0
        # words but one big image per page.
        rows = []
        total_words = 0
        for i, page in enumerate(pdf.pages):
            words = page.extract_words()
            n_words = len(words)
            n_images = len(page.images)
            total_words += n_words
            rows.append((i + 1, n_words, n_images))

        print("\nPer-page summary (page | words | images):")
        for pg, w, im in rows[:15]:
            print(f"  p{pg:<3} words={w:<6} images={im}")
        if n_pages > 15:
            print(f"  ... ({n_pages - 15} more pages)")

        avg_words = total_words / n_pages if n_pages else 0
        print(f"\nTotal words across doc: {total_words}")
        print(f"Avg words/page:         {avg_words:.1f}")

        # Verdict heuristic — deliberately simple and inspectable.
        if avg_words >= 30:
            verdict = "DIGITAL PDF (has text layer) -> use pdfplumber, no OCR"
        elif avg_words <= 3:
            verdict = "SCAN (no text layer) -> need PaddleOCR fallback"
        else:
            verdict = "MIXED / UNCERTAIN -> inspect sample output below by hand"
        print(f"\nVERDICT: {verdict}\n")

        # Show real word-with-box output from one page so we can eyeball quality.
        page_idx = (sample_page - 1) if sample_page else _busiest_page(rows)
        page_idx = max(0, min(page_idx, n_pages - 1))
        page = pdf.pages[page_idx]
        words = page.extract_words()
        print(f"--- Sample: page {page_idx + 1}, first 25 words with boxes ---")
        print(f"{'text':<28} {'x0':>7} {'top':>7} {'x1':>7} {'bottom':>7}")
        for w in words[:25]:
            print(
                f"{w['text'][:28]:<28} {w['x0']:>7.1f} {w['top']:>7.1f} "
                f"{w['x1']:>7.1f} {w['bottom']:>7.1f}"
            )
        if not words:
            print("  (no words on this page — likely a scanned image)")
        print()


def _busiest_page(rows) -> int:
    """Index of the page with the most words — best sample of text quality."""
    if not rows:
        return 0
    best = max(rows, key=lambda r: r[1])
    return best[0] - 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Check if a leaflet PDF has a text layer.")
    ap.add_argument("pdf", type=Path, help="Path to a leaflet PDF")
    ap.add_argument("--page", type=int, default=None, help="1-based page to sample")
    args = ap.parse_args()
    inspect(args.pdf, args.page)
