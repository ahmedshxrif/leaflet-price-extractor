"""
Stage 1 (scan branch) — OCR a leaflet page to tokens + boxes, with an audit trail.

Our leaflets are flat ~150 DPI images, so we UPSCALE before OCR (small model codes
need the pixels). Output per page:
  - a list of tokens: {text, conf, x0, y0, x1, y1}
  - tokens.csv           (every token, box, confidence — the audit dump)
  - <page>_annotated.png (boxes drawn + recognized text — eyeball OCR quality)

PaddleOCR 3.x API: PaddleOCR(...).predict(img) -> results carrying rec_texts,
rec_scores, rec_polys.

Usage:
    python src/ocr_page.py "data/leaflets/<file>.pdf" --page 25 --zoom 3
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, asdict
from pathlib import Path

import fitz
import numpy as np
from PIL import Image, ImageDraw

OUT_DIR = Path("output/ocr")


@dataclass
class Token:
    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float


_OCR = None       # lazy singletons — model load is expensive
_OCR_FAST = None  # mobile models: ~3-5x faster, used by the pre-filter skim
                  # (it only needs to spot 40pt section headers, not read codes)


def get_ocr(fast: bool = False):
    global _OCR, _OCR_FAST
    from paddleocr import PaddleOCR
    # our pages are upright printed artwork — skip the doc-orientation/unwarp/
    # textline-orientation stages for speed and determinism.
    common = dict(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        lang="en",
        # Paddle's oneDNN path crashes on Intel CI runners
        # ("ConvertPirAttribute2RuntimeAttribute not support" in
        # onednn_instruction.cc) — every OCR call dies instantly. Plain CPU
        # inference works everywhere, so force oneDNN off.
        enable_mkldnn=False,
    )

    def _make(**kw):
        try:
            return PaddleOCR(**kw)
        except (TypeError, ValueError):
            # installed PaddleOCR predates the enable_mkldnn kwarg
            kw.pop("enable_mkldnn", None)
            return PaddleOCR(**kw)

    if fast:
        if _OCR_FAST is None:
            _OCR_FAST = _make(
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_mobile_rec",
                **common,
            )
        return _OCR_FAST
    if _OCR is None:
        _OCR = _make(**common)
    return _OCR


# Poster-sized pages (some Lulu leaflets are print-shop artwork, metres wide in
# points) render to multi-GB images at profile zoom and the OCR matmul then
# eats RAM+swap until the machine is killed. Cap the longest rendered side —
# normal leaflet pages come nowhere near it and are unaffected.
MAX_RENDER_SIDE_PX = 4500


def render_page(pdf_path: Path, page_no: int, zoom: float) -> np.ndarray:
    """Render a 1-based page to an RGB numpy array at the given zoom."""
    doc = fitz.open(pdf_path)
    page = doc[page_no - 1]
    r = page.rect
    long_side = max(r.width, r.height)
    if long_side * zoom > MAX_RENDER_SIDE_PX:
        zoom = MAX_RENDER_SIDE_PX / long_side
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return img[:, :, :3].copy()


def ocr_image(img: np.ndarray, fast: bool = False) -> list[Token]:
    result = get_ocr(fast).predict(img)
    tokens: list[Token] = []
    for res in result:
        d = res if isinstance(res, dict) else getattr(res, "json", res)
        texts = d.get("rec_texts", [])
        scores = d.get("rec_scores", [])
        polys = d.get("rec_polys", d.get("dt_polys", []))
        for text, score, poly in zip(texts, scores, polys):
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            tokens.append(Token(str(text), float(score),
                                min(xs), min(ys), max(xs), max(ys)))
    return tokens


def save_audit(img: np.ndarray, tokens: list[Token], stem: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # 1) tokens.csv
    csv_path = OUT_DIR / f"{stem}_tokens.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(tokens[0]).keys()) if tokens
                           else ["text", "conf", "x0", "y0", "x1", "y1"])
        w.writeheader()
        for t in tokens:
            w.writerow(asdict(t))
    # 2) annotated image — box colour by confidence (green ok, red weak)
    pil = Image.fromarray(img)
    draw = ImageDraw.Draw(pil)
    for t in tokens:
        colour = (0, 170, 0) if t.conf >= 0.9 else (230, 140, 0) if t.conf >= 0.75 else (220, 0, 0)
        draw.rectangle([t.x0, t.y0, t.x1, t.y1], outline=colour, width=2)
    ann_path = OUT_DIR / f"{stem}_annotated.png"
    pil.save(ann_path)
    print(f"audit -> {csv_path}\n         {ann_path}")


def _ocr_page_task(args: tuple) -> int:
    """Worker task: OCR one page and persist its tokens+audit to the cache.
    Returns the page number. Runs in a spawned process — must only be reached
    from real-script entry points (run_batch/run_leaflet have __main__ guards)."""
    pdf_str, pno, zoom, stem = args
    img = render_page(Path(pdf_str), pno, zoom)
    tokens = ocr_image(img)
    save_audit(img, tokens, f"{stem}_p{pno}")
    return pno


def prewarm_pages(pdf: Path, pages: list[int], zoom: float, stem: str,
                  workers: int = 3) -> int:
    """Parallel-OCR any UNCACHED pages so later loads hit the token cache.
    Workers write straight to output/ocr (each page persists the moment it
    finishes — interrupt-safe like everything else). Returns pages OCR'd."""
    uncached = [p for p in pages
                if not (OUT_DIR / f"{stem}_p{p}_tokens.csv").exists()]
    if not uncached:
        return 0
    if workers <= 1 or len(uncached) == 1:
        for p in uncached:
            _ocr_page_task((str(pdf), p, zoom, stem))
        return len(uncached)
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=min(workers, len(uncached))) as ex:
        list(ex.map(_ocr_page_task, [(str(pdf), p, zoom, stem) for p in uncached]))
    return len(uncached)


def run(pdf_path: Path, page_no: int, zoom: float) -> list[Token]:
    img = render_page(pdf_path, page_no, zoom)
    print(f"rendered p{page_no} at {zoom}x -> {img.shape[1]}x{img.shape[0]}px; running OCR...")
    tokens = ocr_image(img)
    stem = f"{pdf_path.stem}_p{page_no}".replace(" ", "_")
    save_audit(img, tokens, stem)
    weak = sum(1 for t in tokens if t.conf < 0.75)
    print(f"tokens: {len(tokens)}  (low-confidence <0.75: {weak})")
    return tokens


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--zoom", type=float, default=3.0)
    args = ap.parse_args()
    toks = run(args.pdf, args.page, args.zoom)
    print("\n--- sample tokens (first 30) ---")
    for t in toks[:30]:
        print(f"  {t.conf:.2f}  {t.text!r}")
