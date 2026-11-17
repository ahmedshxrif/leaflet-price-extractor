"""
Appliance-page pre-filter — find the 1-4 pages worth full OCR in a 50-page flyer.

Why: a leaflet is mostly groceries/gadgets; full-zoom OCR of every page costs
~40-60 min. The appliance pages announce themselves (section headers, capacity
units, master model codes), so we:

  1. SKIM every page at low zoom (default 1.5x) with the MOBILE OCR models
     (~3-5x faster again — headers don't need the accuracy-grade model). Every
     page — no striding: Carrefour's appliance section is ONE page (p25) with
     zero signal on its neighbours, so skipping pages guarantees eventual misses.
  2. Score each page:
       - keyword COUNT >= 3 (APPLIANCE/REFRIGERAT/FRIDGE/WASHER/...). A page of
         fridges says 'fridge' 5-16 times (product names alone); a soap bottle
         or one 'Hair Dryer' says it once or twice. Measured across all 4
         retailers: real pages 5-16 kws, false positives 1-2 — count separates
         perfectly where token SIZE does not (real headers are often no bigger
         than body text; a 'DishWash' soap banner was the largest keyword seen).
       - safety net: run the matcher over skim tokens — a master model code on
         a page with no keyword quorum still makes it a candidate.
  3. Candidates = hit pages ± 1 neighbour. The caller full-OCRs only those.

Never silent: the skim report lists every page's score so a wrong skip is
inspectable, and the report is printed/stored with the run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
import re

from extract_page import _looks_like_packsize
from matcher import Matcher, normalize_token
from ocr_page import render_page, ocr_image

SKIM_ZOOM = 1.5
KEYWORDS = re.compile(
    r"APPLIANCE|REFRIGERAT|FRIDGE|FREEZER|WASHER|WASHING\s*MACHINE|DRYER|DISHWASH",
    re.I)
KW_MIN = 3   # min keyword tokens for a page to qualify (real pages: 5-16; FPs: 1-2)


@dataclass
class SkimReport:
    n_pages: int
    keyword_pages: list[int] = field(default_factory=list)
    code_pages: list[int] = field(default_factory=list)      # master-code safety net
    candidates: list[int] = field(default_factory=list)      # hits +- 1, deduped

    def summary(self) -> str:
        return (f"skim: {self.n_pages} pages -> keywords on {self.keyword_pages}, "
                f"master codes on {self.code_pages} -> full-OCR candidates "
                f"{self.candidates}")


def _code_signals(tokens, matcher: Matcher) -> tuple[bool, bool]:
    """(has_master_code, has_code_shaped_token).

    master: ONE readable master code anywhere makes the page a candidate —
    covers the single-fridge-page case with no keyword quorum needed.
    shaped: letters+digits >=7, not a pack-size — covers a single NEW model
    not yet in the master (the RT50 lesson at page level).
    """
    master = shaped = False
    for t in tokens:
        for word in t.text.split():
            w = normalize_token(word)
            if len(w) < 6 or w.isdigit() or not any(ch.isdigit() for ch in w):
                continue
            if matcher.match(w).matched_code:
                master = True
            elif len(w) >= 7 and sum(c.isalpha() for c in w) >= 2 \
                    and sum(c.isdigit() for c in w) >= 2 \
                    and not _looks_like_packsize(w):
                shaped = True
    return master, shaped


# ---- parallel skim workers (each process owns a mobile-OCR + matcher copy) ----
_W: dict = {}


def _init_worker(pdf_str: str, zoom: float) -> None:
    from ocr_page import get_ocr
    _W["pdf"], _W["zoom"] = Path(pdf_str), zoom
    _W["matcher"] = Matcher()
    get_ocr(fast=True)          # warm the model once per worker


def _skim_page(pno: int) -> tuple[int, int, int, bool, bool]:
    tokens = ocr_image(render_page(_W["pdf"], pno, _W["zoom"]), fast=True)
    n_kw = sum(1 for t in tokens if KEYWORDS.search(t.text))
    master, shaped = _code_signals(tokens, _W["matcher"])
    return pno, len(tokens), n_kw, master, shaped


def skim(pdf: Path, n_pages: int, matcher: Matcher,
         zoom: float = SKIM_ZOOM, log=print, workers: int = 3) -> SkimReport:
    """Low-zoom sweep of every page -> candidate appliance pages."""
    rep = SkimReport(n_pages=n_pages)
    hits: set[int] = set()

    if workers > 1 and n_pages > 4:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                                 initargs=(str(pdf), zoom)) as ex:
            results = sorted(ex.map(_skim_page, range(1, n_pages + 1)))
    else:
        results = []
        for pno in range(1, n_pages + 1):
            tokens = ocr_image(render_page(pdf, pno, zoom), fast=True)
            n_kw = sum(1 for t in tokens if KEYWORDS.search(t.text))
            master, shaped = _code_signals(tokens, matcher)
            results.append((pno, len(tokens), n_kw, master, shaped))

    for pno, n_tok, n_kw, master, shaped in results:
        kw = n_kw >= KW_MIN
        # tiers: keyword quorum | ONE master code | any keyword + code-shaped
        # token (single new-model appliance on an otherwise quiet page)
        weak = n_kw >= 1 and shaped
        if kw:
            rep.keyword_pages.append(pno)
        if master:
            rep.code_pages.append(pno)
        if kw or master or weak:
            hits.add(pno)
        log(f"  skim p{pno}: {n_tok} tokens, {n_kw} kw"
            + (" [KEYWORD-QUORUM]" if kw else "")
            + (" [MASTER CODE]" if master else "")
            + (" [KW+CODE-SHAPED]" if (weak and not kw and not master) else ""))
    for p in sorted(hits):
        rep.candidates.extend(x for x in (p - 1, p, p + 1) if 1 <= x <= n_pages)
    rep.candidates = sorted(set(rep.candidates))
    # persist immediately — buffered stdout hides the report until process
    # exit, and the candidate list should be inspectable mid-run
    report_path = Path("output/ocr") / f"{pdf.stem}_skim.json".replace(" ", "_")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(asdict(rep), indent=1))
    log(rep.summary() + f"  [report -> {report_path}]")
    return rep
