"""
Stage 3/4 — price detection + RRP/PRP resolution (currency-aware).

Two jobs:
  1. Find price-shaped and percentage-shaped tokens in OCR text. The decimal rule
     comes from the leaflet's currency (AED=2dp, KWD=3dp ...) via country_config.
  2. Given the prices near one product, decide which is RRP (was) and which is PRP
     (promo), and CROSS-CHECK against the discount badge.

Key insight from real page 25: a tile shows PROMO + struck ORIGINAL + '-NN%'.
Strikethrough is a visual cue OCR can't read reliably, so we don't rely on it.
Instead: higher price = RRP, lower = PRP, and we verify PRP ≈ RRP*(1-NN%). When
the arithmetic agrees we're confident; when it doesn't we flag rather than guess.

Buildable/testable now — no leaflet needed; boxes get wired in at Stage 3 proper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# A price is digits with optional thousands separators and an optional fractional
# part. We accept 0..maxdp fractional digits (appliances print whole numbers like
# 2099; groceries print 7.99). maxdp is set per currency.
def price_pattern(max_decimals: int) -> re.Pattern:
    # 1,234 or 1234, optional . then up to max_decimals digits
    return re.compile(
        r"(?<![\w.])"
        r"(\d{1,3}(?:,\d{3})+|\d+)"           # integer part, optional , grouping
        rf"(?:\.(\d{{1,{max_decimals}}}))?"    # optional fractional part
        r"(?![\w])"
        r"(?!\s*%)"                            # NOT a percentage (e.g. -31%)
    )

PERCENT = re.compile(r"-?\s*(\d{1,2})\s*%")


def find_prices(text: str, max_decimals: int) -> list[float]:
    out = []
    for m in price_pattern(max_decimals).finditer(text):
        intpart = m.group(1).replace(",", "")
        frac = m.group(2)
        val = float(f"{intpart}.{frac}") if frac else float(intpart)
        out.append(val)
    return out


def find_percents(text: str) -> list[int]:
    return [int(m.group(1)) for m in PERCENT.finditer(text)]


@dataclass
class PriceResolution:
    rrp: float | None
    promo_price: float | None
    discount_pct: int | None
    confidence: float
    flag_reason: str = ""
    member_price: float | None = None   # loyalty price (Sharaf 'DG member' chip)

    @property
    def clean(self) -> bool:
        return self.promo_price is not None and not self.flag_reason


def resolve_prices(prices: list[float], percents: list[int],
                   pct_tolerance: float = 0.03,
                   glyph_pair: bool = False,
                   member_vals: list[float] = ()) -> PriceResolution:
    """Assign RRP/PRP from the prices near one product, cross-checked by % badge.

    member_vals: loyalty prices (Sharaf 'DG member' chip) — excluded from the
    was/now assignment and carried separately. If removing them leaves only ONE
    price, the printed NOW price was unreadable: flag, don't promote the member
    price to promo.
    """
    if member_vals:
        member = min(member_vals)
        rest = sorted(set(prices) - set(member_vals))
        if len(rest) >= 2:
            res = resolve_prices(rest, percents, pct_tolerance, glyph_pair)
            res.member_price = member
            return res
        if len(rest) == 1:
            return PriceResolution(rest[0], None, None, 0.5,
                                   "NOW price unreadable — member price present",
                                   member_price=member)
        return PriceResolution(None, None, None, 0.3,
                               "only member price readable", member_price=member)

    uniq = sorted(set(prices))

    if not uniq:
        return PriceResolution(None, None, None, 0.0, "no price found")

    pct = percents[0] if percents else None

    # one price: it's the promo (or the only) price; no RRP to compare.
    if len(uniq) == 1:
        # if a discount % exists we can back-compute the implied RRP as a sanity aid,
        # but we don't invent an RRP column value — leave rrp None, note it.
        return PriceResolution(None, uniq[0], pct, 0.75,
                               "single price (no RRP shown)" if pct is None
                               else "single price shown; % badge present")

    # two or more: highest = RRP, lowest = PRP (promo).
    rrp, promo = uniq[-1], uniq[0]

    if pct is None:
        # no badge to verify against — apply a plausibility guard: discounts
        # beyond 75% are almost always a stray token (financing amount, footnote
        # number) rather than a real promo. Flag, never guess.
        if promo < 0.25 * rrp:
            return PriceResolution(rrp, promo, None, 0.40,
                                   f"implausible discount ({promo} vs {rrp}) — possible stray token")
        # EXACTLY two prices with a plausible ratio is an unambiguous was/now
        # pair — clean at reduced confidence (Lulu never prints % badges;
        # without this every Lulu row would sit in review forever). A currency
        # glyph on either price strengthens it slightly. THREE or more prices
        # with no badge genuinely is ambiguous -> review.
        if len(uniq) == 2:
            return PriceResolution(rrp, promo, None, 0.88 if glyph_pair else 0.85)
        # THREE prices = WAS / NOW / member stack (Sharaf: 2299/1799/1699 with a
        # 'DG member' chip). RRP = max, promo = the MIDDLE (the printed NOW),
        # member = min -> carried separately, never sold as the promo price.
        if len(uniq) == 3:
            mid = uniq[1]
            if promo >= 0.25 * rrp and mid < rrp:
                return PriceResolution(rrp, mid, None, 0.82, member_price=promo)
        return PriceResolution(rrp, promo, None, 0.60,
                               f"{len(uniq)} prices, no % badge — was/now pair ambiguous")

    implied = rrp * (1 - pct / 100)
    rel_err = abs(implied - promo) / promo if promo else 1.0
    if rel_err <= pct_tolerance:
        return PriceResolution(rrp, promo, pct, 0.97)      # arithmetic agrees
    return PriceResolution(rrp, promo, pct, 0.55,
                           f"%-check mismatch: {rrp}*(1-{pct}%)={implied:.0f} vs promo {promo:.0f}")


if __name__ == "__main__":
    # real values read off page 25 (AED, 2dp)
    cases = [
        ("LG fridge  2099  2999  -31%", 2),      # promo/rrp/pct all present, should agree
        ("Samsung  1699  2149  -21%", 2),        # 2149*0.79=1698 -> agree
        ("Hitachi  2599  3499  -26%", 2),        # 3499*0.74=2589 ~ 2599 -> agree
        ("SUPER DEAL 79.99", 2),                 # single grocery price
        ("bogus 100 500 -5%", 2),                # 500*0.95=475 != 100 -> mismatch flag
        ("KWD 149.500 199.000 -25%", 3),         # 3-decimal currency
    ]
    for text, dp in cases:
        r = resolve_prices(find_prices(text, dp), find_percents(text))
        tag = "OK  " if r.clean else "FLAG"
        print(f"[{tag}] {text!r:<34} rrp={r.rrp} promo={r.promo_price} "
              f"-{r.discount_pct}% conf={r.confidence:.2f} {r.flag_reason}")
