"""
Stage 2 — Model-code matcher.

Given a token read off a leaflet (e.g. 'RS62R5OO1B4' with OCR noise), decide
which master model code it is — or refuse to guess and flag it for review.

Depends ONLY on the cleaned master list (data/master/master_clean.xlsx). No
leaflets needed to build or test this: we validate it with synthetic OCR noise.

Matching policy (deliberately conservative — never silently guess):
  1. EXACT   — token equals a known code (region tag ignored). Confidence 0.99.
  2. SHORT   — codes <= 5 chars (is_short) are matched EXACTLY ONLY. A near-miss
               on '148' or '66100' is a flag, never a fuzzy match. Too little
               redundancy to trust.
  3. FUZZY   — long codes: rapidfuzz against known codes, threshold 85. The best
               candidate must beat the rest by a MARGIN or it's ambiguous.
  4. CONFUSION — when the top candidates are near-ties (e.g. colour siblings
               'RS62R5001B4' vs 'RS62R5001S4' that differ only in the finish
               chars), we do NOT let fuzzy pick. We expand the read token through
               an OCR confusion map (0<->O, 1<->I<->L, 5<->S, 8<->B, 2<->Z) and
               keep only candidates it could LEGALLY be. Exactly one survivor ->
               take it. Two or more -> flag AMBIGUOUS.

A flagged result has matched_code=None and a human-readable flag_reason, but still
exposes best_guess + candidates so a reviewer has somewhere to start.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process

MASTER = Path("data/master/master_clean.xlsx")

FUZZY_THRESHOLD = 85   # min rapidfuzz score to consider a long-code match at all
NEAR_TIE_WINDOW = 8    # candidates within this many points of the best are "close"
MIN_TOKEN_LEN = 3      # shorter than this can't be a real code

# OCR confusion classes — chars a scanner routinely swaps. Bidirectional.
# Kept tight and defensible (the spec set + 6<->G): a wide map over-collapses
# distinct codes. Every pair here is a genuinely common scanner error.
_CONFUSION_CLASSES = [
    {"0", "O"},
    {"1", "I", "L"},
    {"5", "S"},
    {"8", "B"},
    {"2", "Z"},
    {"6", "G"},
]
_EQUIV: dict[str, set[str]] = {}
for _cls in _CONFUSION_CLASSES:
    for _c in _cls:
        _EQUIV.setdefault(_c, set()).update(_cls)


def _confusable_equal(a: str, b: str) -> bool:
    """True if a could be read as b under OCR confusion (same length required)."""
    if len(a) != len(b):
        return False
    for ca, cb in zip(a, b):
        if ca == cb:
            continue
        if cb not in _EQUIV.get(ca, {ca}):
            return False
    return True


def normalize_token(token: str) -> str:
    """Uppercase, drop spaces, and strip a trailing region tag (/AE, /SG…)."""
    t = str(token).upper().strip().replace(" ", "")
    if "/" in t:
        t = t.split("/", 1)[0]
    # strip surrounding punctuation an extractor might include
    return t.strip(".,:;()[]{}-–—•")


@dataclass
class MatchResult:
    input_token: str
    norm_token: str
    matched_code: str | None          # None => flagged, do not trust
    method: str                       # exact | fuzzy | confusion | none
    confidence: float                 # 0..1
    flag_reason: str = ""             # blank when clean
    best_guess: str | None = None     # for reviewers when flagged
    candidates: list[tuple[str, float]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.matched_code is not None and not self.flag_reason


class Matcher:
    def __init__(self, master_path: Path = MASTER):
        df = pd.read_excel(master_path)
        # empty region_suffix comes back from xlsx as NaN — the known gotcha.
        df["region_suffix"] = df["region_suffix"].fillna("")
        self.df = df

        # We match on stem_code (region tag removed); colour/finish stays in-code.
        stems = df["stem_code"].astype(str)
        self._is_short = dict(zip(stems, df["is_short"]))

        # Exact lookup: stem -> the master row(s) that share it.
        self._by_stem: dict[str, pd.DataFrame] = {
            s: g for s, g in df.groupby(stems)
        }
        self.all_stems = list(self._by_stem.keys())
        # Fuzzy pool excludes short codes (those are exact-only).
        self.fuzzy_pool = [s for s in self.all_stems if not self._is_short.get(s, False)]
        # Length index over the fuzzy pool — confusion recovery only compares
        # equal-length codes, so we scan one bucket instead of all 3k stems.
        self._by_len: dict[int, list[str]] = {}
        for s in self.fuzzy_pool:
            self._by_len.setdefault(len(s), []).append(s)

    # -- public API ---------------------------------------------------------
    def match(self, token: str) -> MatchResult:
        norm = normalize_token(token)
        if len(norm) < MIN_TOKEN_LEN:
            return MatchResult(token, norm, None, "none", 0.0,
                               flag_reason="token too short to be a code")

        # 1) exact
        if norm in self._by_stem:
            return MatchResult(token, norm, norm, "exact", 0.99)

        # 2) short codes: exact only, never fuzzy
        looks_short = len(norm) <= 5
        if looks_short:
            return MatchResult(token, norm, None, "none", norm and 0.0,
                               flag_reason="short code, no exact match")

        # 3) confusion recovery — same-length codes the token could legally be
        #    under OCR char-swaps. This precisely rescues '0<->O' style noise that
        #    pure fuzzy scores below threshold. Exactly one survivor -> take it.
        legal = [s for s in self._by_len.get(len(norm), []) if _confusable_equal(norm, s)]
        if len(legal) == 1:
            return MatchResult(token, norm, legal[0], "confusion", 0.92,
                               candidates=[(legal[0], 100.0)])
        if len(legal) > 1:
            return MatchResult(token, norm, None, "none", 0.0,
                               flag_reason=f"ambiguous (confusion): {', '.join(legal)}",
                               best_guess=legal[0], candidates=[(c, 100.0) for c in legal])

        # 4) fuzzy on long codes (handles inserted/dropped/other-noise chars)
        results = process.extract(norm, self.fuzzy_pool, scorer=fuzz.ratio, limit=5)
        candidates = [(c, s) for c, s, _ in results]
        if not candidates or candidates[0][1] < FUZZY_THRESHOLD:
            best = candidates[0] if candidates else (None, 0.0)
            return MatchResult(token, norm, None, "none", best[1] / 100,
                               flag_reason=f"below fuzzy threshold ({best[1]:.0f}<{FUZZY_THRESHOLD})",
                               best_guess=best[0], candidates=candidates)

        best_code, best_score = candidates[0]
        near = [(c, s) for c, s in candidates if best_score - s <= NEAR_TIE_WINDOW]

        # 4) clear winner
        if len(near) == 1:
            return MatchResult(token, norm, best_code, "fuzzy", best_score / 100,
                               candidates=candidates)

        # 5) near-tie -> disambiguate with the confusion map, never fuzzy-guess
        legal = [c for c, _ in near if _confusable_equal(norm, c)]
        if len(legal) == 1:
            return MatchResult(token, norm, legal[0], "confusion", 0.90,
                               candidates=candidates)
        return MatchResult(token, norm, None, "none", best_score / 100,
                           flag_reason=f"ambiguous: {', '.join(c for c, _ in near)}",
                           best_guess=best_code, candidates=candidates)

    def info(self, stem: str) -> dict:
        """Master metadata (brand/category/subcat) for a matched stem."""
        g = self._by_stem.get(stem)
        if g is None:
            return {}
        row = g.iloc[0]
        return {
            "brand": row["brand"],
            "product_category": row["product_category"],
            "sub_category": row["sub_category"],
            "model_code": row["model_code"],
            "bom_code": row.get("bom_code", row["norm_code"]),
            "is_alias": bool(row.get("is_alias", False)),
        }


if __name__ == "__main__":
    m = Matcher()
    print(f"loaded {len(m.df)} codes  |  fuzzy pool {len(m.fuzzy_pool)}  |  short {len(m.all_stems)-len(m.fuzzy_pool)}")
    for tok in ["RS62R5001B4", "RS62R5OO1B4", "WW90CGC04DABGU", "148", "1 4 8", "WW9OCGCO4DAB", "ZZZZZZZZ"]:
        r = m.match(tok)
        tag = "OK " if r.clean else "FLAG"
        print(f"  [{tag}] {tok!r:>18} -> {r.matched_code}  ({r.method}, conf={r.confidence:.2f}) {r.flag_reason}")
