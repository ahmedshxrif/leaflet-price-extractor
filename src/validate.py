"""
Row validation layer — every column must contain values appropriate to it.

Runs AFTER extraction, BEFORE the writer. It cannot fix data (never guess), but
it catches cross-contamination the geometry heuristics let through and either
flags it or blanks the impossible value:

  - Category/unit coherence: a REF with a kg capacity, or a WM with litres, is a
    stolen or garbled spec line -> blank the capacity + flag.
  - Capacity plausibility: REF 40-1200 L; WM 2-30 kg (per side for combos).
    'T1kg' OCR garbage reading as '1 kg' dies here.
  - Price sanity: promo must be < regular; both above a per-currency floor for
    appliances (a 230 AED "washer price" is a voltage marking, not a price).
  - Matched rows must carry brand + category (a match without master metadata
    means a join bug — surface it loudly).

Every check appends a human-readable reason to the row's flags. Confidence is
degraded so the row sorts into the review pile.
"""

from __future__ import annotations

import re

from extract_page import ExtractedRow

# minimum plausible APPLIANCE price by currency (promo side). Not a general
# price floor — leaflet gadgets can be cheap — applied only to matched REF/WM.
PRICE_FLOOR = {"AED": 150, "SAR": 150, "QAR": 150, "KWD": 15, "BHD": 15, "OMR": 15}

REF_L_RANGE = (40, 1200)     # litres
WM_KG_RANGE = (2, 30)        # kg, per side for combos

_CAP_NUM = re.compile(r"(\d+(?:\.\d+)?)")


def _cap_unit(capacity: str) -> str:
    c = capacity.lower()
    if "kg" in c:
        return "kg"
    if "l" in c:
        return "L"
    return ""


def validate_row(r: ExtractedRow, currency: str) -> ExtractedRow:
    """Mutates r in place (flags/capacity/confidence); returns it for chaining."""
    problems: list[str] = []

    # --- matched rows must have complete master metadata ---
    if r.matched_code and not (r.brand and r.product_category):
        problems.append("matched code but master metadata missing (join bug?)")

    # --- category <-> capacity-unit coherence + plausibility ---
    if r.capacity:
        unit = _cap_unit(r.capacity)
        nums = [float(n) for n in _CAP_NUM.findall(r.capacity)]
        if r.product_category == "REF":
            if unit == "kg":
                problems.append(f"REF with kg capacity ({r.capacity!r}) — spec line stolen/garbled, blanked")
                r.capacity = ""
            elif unit == "L" and nums and not (REF_L_RANGE[0] <= nums[0] <= REF_L_RANGE[1]):
                problems.append(f"implausible REF capacity {r.capacity!r}, blanked")
                r.capacity = ""
        elif r.product_category == "WM":
            if unit == "L":
                problems.append(f"WM with litre capacity ({r.capacity!r}) — spec line stolen/garbled, blanked")
                r.capacity = ""
            elif unit == "kg" and nums and not all(WM_KG_RANGE[0] <= n <= WM_KG_RANGE[1] for n in nums):
                problems.append(f"implausible WM capacity {r.capacity!r}, blanked")
                r.capacity = ""

    # --- price sanity ---
    if r.rrp is not None and r.promo_price is not None and r.promo_price >= r.rrp:
        problems.append(f"promo ({r.promo_price}) >= regular ({r.rrp})")
    if r.matched_code and r.product_category in ("REF", "WM"):
        floor = PRICE_FLOOR.get(currency, 0)
        for label, v in (("promo", r.promo_price), ("regular", r.rrp)):
            if v is not None and v < floor:
                problems.append(f"{label} price {v} below plausible {currency} "
                                f"appliance floor ({floor}) — stray token?")

    if problems:
        r.flags = "; ".join(x for x in [r.flags, *problems] if x)
        r.confidence = min(r.confidence, 0.5)
    return r


def validate_rows(rows: list[ExtractedRow], currency: str) -> list[ExtractedRow]:
    return [validate_row(r, currency) for r in rows]
