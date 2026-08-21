"""
Stage 3 — link OCR tokens into product rows for one page.

Layout facts learned from real Carrefour tiles (p.25):
  - Each tile: price CLUSTER at the top (promo price, struck RRP, -NN% badge),
    product image in the middle, spec line + "<Brand> <Type> <CODE>" at the bottom.
  - Tiles are TALL: a code is often vertically CLOSER to the next tile's prices
    than to its own. Naive nearest-neighbour links wrong.
  - Correct rule: a code belongs to the price cluster ABOVE it in the same
    column. We pick, among clusters above the code, the one with the smallest
    horizontal center offset; ties broken by vertical closeness.
  - The currency glyph OCRs as a letter prefix on the small struck price
    ('D2999'), while the big promo price reads bare ('2099'). We strip the
    prefix for parsing and keep it as a secondary RRP signal.

Never silently guess: anything that can't be linked cleanly is emitted as a
flagged row, and every decision lands in the audit CSV.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from matcher import Matcher, normalize_token
from ocr_page import Token
from prices import resolve_prices, find_percents

# price-shaped OCR token: optional currency-glyph letters, then 3+ digits
# (appliance prices; 1-2 digit numbers are footnote junk on these pages),
# optional thousands separators and decimals.
PRICE_TOKEN = re.compile(r"^[A-Za-z]{0,3}\s?(\d{1,3}(?:,\d{3})+|\d{3,6})(?:\.(\d{1,3}))?$")
PERCENT_TOKEN = re.compile(r"^-?\s*\d{1,2}\s*%$")
# OCR reads the '|' spec separator as a glued '1' or 'I' ("550L1 Dual...",
# "110LI Sleek...") — tolerate ONE such artifact char after the unit, and use
# (?![A-Za-z]) instead of \b (which refuses to end between 'L' and '1').
# Optional N/ prefix captures combo washer-dryers ("8/5 kg" = wash/dry).
CAPACITY = re.compile(r"((?:\d+/)?\d+(?:\.\d+)?)\s*(KG|L(?:TRS?|ITRES?)?)[I1l]?(?![A-Za-z])", re.I)

# --- LAYOUT TUNABLES — derived from Carrefour UAE p.25 (@3x zoom) -----------
# PM warning: tile geometry WILL differ across retailers (Carrefour/Lulu/
# SharafDG/Emax) and countries. These values and the "price cluster sits ABOVE
# the code" assumption are a Carrefour-UAE profile, not laws of nature. When the
# first leaflet of a new retailer/country arrives: run one page through the
# audit flow (annotated PNG + tokens.csv), eyeball the links, and if geometry
# differs promote these into per-retailer profiles selected via country_config.
# Until then: mislinks fail LOUDLY (shared-cluster flag, GAP rows, %-mismatch),
# and the first page of every new layout family gets a manual audit pass.
CLUSTER_RADIUS = 350      # px @3x zoom: prices/badge of one tile sit within this
MAX_TILE_HEIGHT = 1200    # px @3x: max vertical gap between a code and its cluster
COL_TIE_PX = 60           # dx this close counts as a tie -> break by dy


def _center(t: Token) -> tuple[float, float]:
    return ((t.x0 + t.x1) / 2, (t.y0 + t.y1) / 2)


@dataclass
class PriceCluster:
    tokens: list[Token] = field(default_factory=list)

    @property
    def center(self) -> tuple[float, float]:
        xs, ys = zip(*(_center(t) for t in self.tokens))
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def texts(self) -> list[str]:
        return [t.text for t in self.tokens]


# Installment/financing markers (Lulu prints 'tabby Đ104' / EMI lines whose
# amounts are price-shaped). A price token adjacent to one of these is a monthly
# amount, not a product price — poison for the promo-price minimum.
FINANCING = re.compile(r"tabby|tamara|emi|per\s*month|/\s*mo(?:nth)?\b", re.I)
FIN_DX, FIN_DY = 300, 45   # "adjacent": same line-ish, near the marker


def _near_financing(t: Token, markers: list[Token]) -> bool:
    cx, cy = _center(t)
    for mk in markers:
        mx, my = _center(mk)
        if abs(cy - my) <= FIN_DY and abs(cx - mx) <= FIN_DX:
            return True
    return False


# Loyalty-price chip marker (Sharaf: black 'DG member' chip beside the price)
MEMBER_MARK = re.compile(r"^DG\b", re.I)

GARBLE_CONF = 0.80   # price tokens below this confidence are quarantined


def rescue_now_price(garble_digits: str, floor: float, ceiling: float) -> float | None:
    """Recover a price from a garbled OCR token via digit-substring candidates.

    Sharaf's bold NOW price merges with its vertical label ('D4799'->'47999',
    'D799'->'207999'). The true price is a contiguous digit run inside the
    garble, and the stack constrains it: member <= NOW < WAS. Among candidates
    in range, take the SMALLEST (member sits only 2-6% below NOW, so the
    nearest-to-floor candidate is the printed price; '207999' in (783,1099)
    yields {799, 999} -> 799). Returns None if no candidate fits.
    """
    digits = re.sub(r"\D", "", garble_digits)
    cands = set()
    for length in (3, 4, 5):
        for i in range(len(digits) - length + 1):
            v = float(digits[i:i + length])
            if floor <= v < ceiling:
                cands.add(v)
    return min(cands) if cands else None


def _cluster_prices(cluster: PriceCluster,
                    member_marks: list[Token] = ()
                    ) -> tuple[list[float], bool, list[float], list[str]]:
    """Parse a cluster -> (values, has_glyph_prefix, member_values, notes).

    Three defenses live here:
    - member-chip adjacency: a price beside a 'DG' marker is a LOYALTY price,
      never the promo.
    - low-confidence quarantine: price tokens under GARBLE_CONF are OCR mangles
      ('D4799' -> '47999' @0.74); letting them in poisons the was/now minimum.
    - stack inference + rescue: when the quarantined garble sits vertically
      BETWEEN two clean prices, the stack is WAS / garbled-NOW / member —
      bottom price becomes the member even without a readable 'DG' chip, and
      the NOW is rescued from the garble's digits (see rescue_now_price).
    """
    entries: list[tuple[float, Token, float]] = []
    garbles: list[Token] = []
    glyph, member, notes = False, [], []
    for t in cluster.tokens:
        mm = PRICE_TOKEN.match(t.text.strip())
        if not mm:
            continue
        if t.conf < GARBLE_CONF:
            garbles.append(t)
            continue
        if t.text.strip()[0].isalpha():
            glyph = True
        intpart = mm.group(1).replace(",", "")
        v = float(f"{intpart}.{mm.group(2)}") if mm.group(2) else float(intpart)
        entries.append((v, t, t.conf))
        tx, ty = _center(t)
        if any(abs(ty - _center(mk)[1]) < 35 and abs(tx - _center(mk)[0]) < 250
               for mk in member_marks):
            member.append(v)

    vals = [v for v, _, _ in entries]

    # stack inference: exactly one garble between two clean prices -> the lower
    # clean price is the member (the 'DG' chip text often fails to OCR).
    if garbles and not member and len(entries) >= 2:
        g = garbles[0]
        gy = _center(g)[1]
        upper = [e for e in entries if _center(e[1])[1] < gy]
        lower = [e for e in entries if _center(e[1])[1] > gy]
        if upper and lower:
            was = max(u[0] for u in upper)
            mem = min(l[0] for l in lower)
            if mem < was:
                member = [mem]
                rescued = rescue_now_price(g.text, mem, was)
                if rescued is not None:
                    vals.append(rescued)
                    notes.append(f"NOW price {rescued:g} rescued from garbled "
                                 f"OCR token {g.text!r} (conf {g.conf:.2f})")
                else:
                    notes.append(f"NOW price unreadable (garbled token "
                                 f"{g.text!r}); member/was kept")
    return vals, glyph, member, notes


# Capacity chips (Sharaf): a bare number with a unit word DIRECTLY BELOW it
# ('650' over 'LITRES'). Never a price — and exactly the leaflet capacity.
UNIT_WORD = re.compile(r"^(LITRES?|LTRS?|LITERS?|KGS?|KILOS?)$", re.I)


# Emax single-token chip: 'NET-607L', 'NET-569 L', 'GROSS 500L'
NET_CHIP = re.compile(r"^(NET|GROSS)[-\s]?(\d{2,4})\s*L?$", re.I)


def find_capacity_chips(tokens: list[Token]) -> dict[int, tuple[str, Token]]:
    """{id(number_token): (capacity_string, number_token)} for chip pairs."""
    chips: dict[int, tuple[str, Token]] = {}
    units = [t for t in tokens if UNIT_WORD.match(t.text.strip())]
    for u in units:
        for t in tokens:
            if not re.fullmatch(r"\d{2,4}", t.text.strip()):
                continue
            if _overlap(t.x0, t.x1, u.x0, u.x1) > 10 and 0 < u.y0 - t.y0 < 80:
                unit = "kg" if u.text.strip().upper().startswith(("KG", "KILO")) else "L"
                gross = any("GROSS" in o.text.upper()
                            and _overlap(o.x0, o.x1, u.x0, u.x1) > 0
                            and 0 < o.y0 - u.y0 < 60 for o in tokens)
                chips[id(t)] = (f"{t.text.strip()} {unit}{' gross' if gross else ''}", t)
    # single-token chips (Emax 'NET-607L')
    for t in tokens:
        m = NET_CHIP.match(t.text.strip())
        if m:
            qual = " net" if m.group(1).upper() == "NET" else " gross"
            chips[id(t)] = (f"{m.group(2)} L{qual}", t)
    return chips


# Emax glued price tokens: 'WAS8999' (label+price), 'D5499D5199' (two prices
# merged), 'WORTHD1999' (freebie value — NEVER a price).
GLUED_WAS = re.compile(r"^WAS[D]?(\d{3,6})[!*]?$", re.I)
GLUED_PAIR = re.compile(r"^([A-Z]\d{3,6})([A-Z]\d{3,6})$")
FREEBIE = re.compile(r"^WORTH", re.I)


def _expand_glued(tokens: list[Token]) -> list[Token]:
    """Split glued price tokens into virtual price tokens (same y, split x)."""
    out: list[Token] = []
    for t in tokens:
        s = t.text.strip()
        if FREEBIE.match(s):
            continue                      # freebie value, not a price
        m = GLUED_WAS.match(s)
        if m:
            out.append(Token(m.group(1), t.conf, t.x0, t.y0, t.x1, t.y1))
            continue
        m = GLUED_PAIR.match(s)
        if m:
            mid = (t.x0 + t.x1) / 2
            out.append(Token(m.group(1), t.conf, t.x0, t.y0, mid, t.y1))
            out.append(Token(m.group(2), t.conf, mid, t.y0, t.x1, t.y1))
            continue
        out.append(t)
    return out


def find_price_clusters(tokens: list[Token],
                        capacity_chips: dict[int, tuple[str, Token]] | None = None
                        ) -> list[PriceCluster]:
    """Greedy proximity clustering of price/percent tokens."""
    markers = [t for t in tokens if FINANCING.search(t.text)]
    chips = capacity_chips or {}
    cand = [t for t in _expand_glued(tokens)
            if (PRICE_TOKEN.match(t.text.strip()) or PERCENT_TOKEN.match(t.text.strip()))
            and not _near_financing(t, markers)
            and id(t) not in chips]          # capacity chips are not prices
    clusters: list[PriceCluster] = []
    for t in cand:
        cx, cy = _center(t)
        for cl in clusters:
            kx, ky = cl.center
            if abs(cx - kx) <= CLUSTER_RADIUS and abs(cy - ky) <= CLUSTER_RADIUS / 2:
                cl.tokens.append(t)
                break
        else:
            clusters.append(PriceCluster([t]))
    # a real tile cluster has at least one actual price
    return [c for c in clusters if any(PRICE_TOKEN.match(t.text.strip()) for t in c.tokens)]


# Grocery pack-size strings ('750G+250GFREE', '24X500ML', '20GPROTEIN') are
# code-shaped but never model codes. Patterns real codes don't use:
_PACKSIZE = [
    re.compile(r"[+=]"),                          # bundle offers: 1+1, 750G+250G
    re.compile(r"^\d+X\d"),                       # multipacks: 24X500ML
    re.compile(r"^\d+(G|KG|ML|CL|L)(?![A-Z0-9])", re.I),   # leading weight/volume
    re.compile(r"^\d+(G|KG|ML|CL|L)[A-Z]+$", re.I),        # 20GPROTEIN
    re.compile(r"\d+(G|KG|ML|CL|L)$", re.I),      # trailing unit: MNT150G
    re.compile(r"(.)\1{3}"),                      # OCR stutter: 50DDDDD
    re.compile(r"\d-\d$"),                        # 1+1 offers misread: FRSZEN1-1
    re.compile(r"^\d+\.\d"),                      # starts with decimal qty: 1.89LX2
    re.compile(r"^(TABBY|TAMARA|EMI)", re.I),     # financing glued: TABBY1050, EMID175
    re.compile(r"[:：]"),                          # spec fragments: POWER:600W
    re.compile(r"\d+W$", re.I),                   # wattage: 600W, 2000W
    re.compile(r"^WAS\d", re.I),                  # glued was-price: WAS8999
    re.compile(r"^[A-Z]\d{3,6}[A-Z]\d{3,6}$"),    # glued price pair: D5499D5199
    re.compile(r"^WORTH", re.I),                  # freebie value: WORTHD1999
    re.compile(r"^(NET|GROSS)[-\s]?\d", re.I),    # capacity chip: NET-607L
]


def _looks_like_packsize(w: str) -> bool:
    return any(p.search(w) for p in _PACKSIZE)


def find_model_codes(tokens: list[Token], matcher: Matcher):
    """Run every code-shaped word through the matcher.

    Returns (hits, unmatched): matched (token, MatchResult) pairs, plus strong
    code-shaped candidates that matched nothing (surfaced as review rows).

    Pure-numeric words are NOT tried as codes here — on a retail page they are
    prices. (A few master codes are pure numbers, e.g. '66100'; if one ever
    appears it will surface as an unmatched tile and go to review, not silently
    become a price. Documented trade-off, revisit if it bites.)
    """
    hits, unmatched = [], []
    for t in tokens:
        for word in t.text.split():
            w = normalize_token(word)
            if len(w) < 5 or w.isdigit() or not any(ch.isdigit() for ch in w):
                continue
            r = matcher.match(w)
            if r.matched_code:
                hits.append((t, r))
                break   # one code per token line
            # strong code-shaped candidate that matched nothing: this is how
            # master coverage gaps surface (the RT50CG6404S9 lesson). Letters+
            # digits, length >= 7, mixed — almost certainly a product code.
            if len(w) >= 7 and sum(ch.isdigit() for ch in w) >= 2 \
                    and sum(ch.isalpha() for ch in w) >= 2 \
                    and not _looks_like_packsize(w):
                unmatched.append((t, r))
                break
    return hits, unmatched


def _cluster_xrange(c: PriceCluster) -> tuple[float, float]:
    return min(t.x0 for t in c.tokens), max(t.x1 for t in c.tokens)


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _cluster_strength(c: PriceCluster) -> bool:
    """A REAL price block has >= 2 prices (was+now), or a price + % badge, or a
    currency-glyph-prefixed price. Lone bare numbers ('230' voltage marking,
    '8888' drum display graphic) are junk that happens to sit inside product
    images — they must never outrank a real block. Layout-independent."""
    prices = [t.text.strip() for t in c.tokens if PRICE_TOKEN.match(t.text.strip())]
    has_pct = any(PERCENT_TOKEN.match(t.text.strip()) for t in c.tokens)
    has_glyph = any(p[0].isalpha() for p in prices)   # D2999-style prefix
    return len(prices) >= 2 or (len(prices) >= 1 and (has_pct or has_glyph))


def _columns_1d(xs: list[float], min_gap: float = 300) -> list[list[float]]:
    """Split sorted x-centers into columns wherever the gap exceeds min_gap."""
    if not xs:
        return []
    xs = sorted(xs)
    cols = [[xs[0]]]
    for x in xs[1:]:
        (cols[-1].append(x) if x - cols[-1][-1] <= min_gap else cols.append([x]))
    return cols


def column_pools(code_toks: list[Token],
                 clusters: list[PriceCluster]) -> dict[int, list[PriceCluster]]:
    """{id(code_token): clusters in its paired column} — only when codes and
    clusters both split into the SAME number (>=2) of clean columns.

    Why: on grid layouts (Sharaf) a code can sit dead-center between two price
    stacks — center-distance then decides by sub-pixel noise (the Siemens bug:
    dy 49.5 vs 50.0). Column pairing is the structural truth; when the page
    doesn't form clean matching columns (Carrefour hero tiles) we return {} and
    the caller falls back to distance logic.
    """
    code_cols = _columns_1d([_center(t)[0] for t in code_toks])
    cl_cols = _columns_1d([c.center[0] for c in clusters])
    if len(code_cols) < 2 or len(code_cols) != len(cl_cols):
        return {}
    code_centers = [sum(c) / len(c) for c in code_cols]
    cl_centers = [sum(c) / len(c) for c in cl_cols]
    pools: dict[int, list[PriceCluster]] = {}
    for t in code_toks:
        x = _center(t)[0]
        idx = min(range(len(code_centers)), key=lambda i: abs(code_centers[i] - x))
        lo = cl_centers[idx] - 0.6 * (cl_cols[idx][-1] - cl_cols[idx][0] + 300)
        hi = cl_centers[idx] + 0.6 * (cl_cols[idx][-1] - cl_cols[idx][0] + 300)
        pools[id(t)] = [c for c in clusters if lo <= c.center[0] <= hi]
    return pools


def link_code_to_cluster(code_tok: Token, clusters: list[PriceCluster],
                         anchor: Token | None = None,
                         price_position: str = "above") -> tuple[PriceCluster | None, str]:
    """Link a code to its tile's price cluster. Returns (cluster, flag).

    Order of criteria (derived from Carrefour p25 + Lulu p41 real geometry):
      1. Only clusters ABOVE the code within a tile height.
      2. STRONG clusters preferred (see _cluster_strength) — junk image tokens
         form weak single-number clusters and are dropped when any strong
         cluster exists. Using a weak cluster is flagged.
      3. x-overlap with the tile x-range (code + spec-line anchor) -> among
         overlapping, vertically nearest (Carrefour: wide spec lines anchor the
         column; stacked tiles disambiguate vertically).
      4. Else min center-dx, ties (within COL_TIE_PX) -> vertically nearest
         (Lulu: left-aligned narrow price blocks never overlap the code range).
    """
    cx, cy = _center(code_tok)
    if price_position == "below":     # Emax: price block at tile BOTTOM
        cand = [c for c in clusters
                if c.center[1] > cy and (c.center[1] - cy) <= MAX_TILE_HEIGHT]
    else:                             # Carrefour/Lulu/Sharaf: price block above
        cand = [c for c in clusters
                if c.center[1] < cy and (cy - c.center[1]) <= MAX_TILE_HEIGHT]
    if not cand:
        return None, ""

    strong = [c for c in cand if _cluster_strength(c)]
    pool, weak_flag = (strong, "") if strong else (cand, "weak price cluster (single bare number)")

    tx0, tx1 = code_tok.x0, code_tok.x1
    if anchor is not None:
        tx0, tx1 = min(tx0, anchor.x0), max(tx1, anchor.x1)

    scored = [(c, _overlap(tx0, tx1, *_cluster_xrange(c))) for c in pool]
    best_ov = max(ov for _, ov in scored)
    if best_ov > 30:   # same-column by overlap -> vertically nearest
        candidates = [c for c, ov in scored if ov > 0.6 * best_ov]
        return min(candidates, key=lambda c: abs(cy - c.center[1])), weak_flag

    # dx path: nearest column center; near-ties resolved by vertical proximity
    pool_sorted = sorted(pool, key=lambda c: abs(c.center[0] - cx))
    best = pool_sorted[0]
    for other in pool_sorted[1:]:
        if abs(other.center[0] - cx) - abs(best.center[0] - cx) <= COL_TIE_PX:
            if abs(cy - other.center[1]) < abs(cy - best.center[1]):
                best = other
    return best, weak_flag


def find_capacity_near(code_tok: Token, tokens: list[Token],
                       other_codes: list[Token] = (),
                       chips: dict[int, tuple[str, Token]] | None = None
                       ) -> tuple[str, Token | None]:
    """Spec line above the product name line. Scored by HORIZONTAL offset
    first (adjacent tiles share the same row, so vertical distance ties; the
    Hoover once stole the freezer's 316L that way). The vertical window is wide
    (700px @3x) because Lulu prints spec bullets at the TOP of the tile, a full
    image + financing line above the code; the next tile's bullets are ~1300px
    up, so the window stays unambiguous. Returns (capacity, anchor token)."""
    cx, cy = _center(code_tok)
    best, best_tok, best_dx = "", None, 1e9

    def consider(cap_str: str, t: Token, dy_min: float = -30) -> None:
        nonlocal best, best_tok, best_dx
        tx, ty = _center(t)
        dy = cy - ty
        dx = abs(tx - cx)
        if not (dy_min <= dy <= 700 and dx < 500 and dx < best_dx):
            return
        # ANOTHER product's code between this spec line and our code means the
        # spec line belongs to that tile, not ours (stops the wide window from
        # reaching into the tile above — the Samsung-shows-550L bug). Works in
        # both directions (Emax chips sit BELOW the code).
        lo, hi = sorted((ty, cy))
        if any(lo + 15 < _center(o)[1] < hi - 15 and abs(_center(o)[0] - tx) < 500
               for o in other_codes if o is not code_tok):
            return
        best, best_tok, best_dx = cap_str, t, dx

    for t in tokens:
        m = CAPACITY.search(t.text)
        if not m:
            continue
        unit = "kg" if m.group(2).upper().startswith("KG") else "L"
        qualifier = ""
        if unit == "L":
            low = t.text.lower()
            qualifier = " net" if "net" in low else (" gross" if "gross" in low else "")
        consider(f"{m.group(1)} {unit}{qualifier}", t)
    # capacity CHIPS (number over 'LITRES', 'NET-607L') are first-class capacity
    # sources; unlike free spec text they're unambiguous, so they may also sit
    # BELOW the code (Emax anatomy) — wider window, guard still applies.
    for cap_str, t in (chips or {}).values():
        consider(cap_str, t, dy_min=-320)
    return best, best_tok


@dataclass
class ExtractedRow:
    model_number: str          # as printed on the leaflet
    matched_code: str
    brand: str
    product_category: str
    sub_category: str
    capacity: str
    rrp: float | None
    promo_price: float | None
    discount_pct_badge: int | None
    confidence: float
    flags: str
    page: int
    other_details: str = ""    # non-price offers (e.g. 'DG member price 1699')
    cluster_dy: float | None = None   # cluster_center_y - code_center_y:
                                      # negative = prices above code. Feeds the
                                      # layout-drift health check.


def extract(tokens: list[Token], page_no: int, price_decimals: int,
            matcher: Matcher | None = None,
            price_position: str = "above") -> list[ExtractedRow]:
    m = matcher or Matcher()
    chips = find_capacity_chips(tokens)
    clusters = find_price_clusters(tokens, chips)
    codes, unmatched = find_model_codes(tokens, m)

    rows: list[ExtractedRow] = []
    used_clusters: dict[int, str] = {}

    # Pages with ZERO master matches are out-of-category (groceries, TVs, audio):
    # their unmatched codes are near-certain noise for this project's scope, so
    # they carry a marker flag the writer/app uses to segregate them from the
    # real review pile. Pages WITH master matches keep full review status —
    # that's where genuine coverage gaps (the RT50 lesson) live.
    out_of_category = not codes
    all_code_toks = [t for t, _ in codes] + [t for t, _ in unmatched]
    pools = column_pools(all_code_toks, clusters)
    member_marks = [t for t in tokens if MEMBER_MARK.match(t.text.strip())]

    def link(tok: Token, anchor: Token | None):
        pool = pools.get(id(tok))
        if pool:
            cluster, flag = link_code_to_cluster(tok, pool, anchor, price_position)
            if cluster is not None:
                return cluster, flag
        return link_code_to_cluster(tok, clusters, anchor, price_position)

    # unmatched code-shaped tokens become REVIEW rows, not silence — every one is
    # either an OCR mangle or a master coverage gap, and the PM needs to see both.
    for tok, match_res in unmatched:
        capacity, anchor = find_capacity_near(tok, tokens, all_code_toks, chips)
        cluster, link_flag = link(tok, anchor)
        rrp = promo = pct = None
        member = ""
        price_notes: list[str] = []
        if cluster is not None:
            vals, glyph, mvals, price_notes = _cluster_prices(cluster, member_marks)
            res = resolve_prices(vals, find_percents(" ".join(cluster.texts())),
                                 glyph_pair=glyph, member_vals=mvals)
            rrp, promo, pct = res.rrp, res.promo_price, res.discount_pct
            if res.member_price is not None:
                member = f"Member price {res.member_price:g}"
        flag_bits = [
            "out-of-category page (no master matches on page)" if out_of_category else "",
            f"code not matched in master ({match_res.flag_reason or 'no candidates'})",
            f"nearest: {match_res.best_guess}" if match_res.best_guess else "",
            link_flag,
            *price_notes,
        ]
        rows.append(ExtractedRow(
            model_number=match_res.norm_token, matched_code="",
            brand="", product_category="", sub_category="",
            capacity=capacity,
            rrp=rrp, promo_price=promo, discount_pct_badge=pct,
            confidence=0.0,
            flags="; ".join(f for f in flag_bits if f),
            page=page_no,
            other_details=member,
        ))

    for tok, match_res in codes:
        capacity, anchor = find_capacity_near(tok, tokens, all_code_toks, chips)
        cluster, link_flag = link(tok, anchor)
        flags = [match_res.flag_reason] if match_res.flag_reason else []
        member = ""
        if link_flag:
            flags.append(link_flag)
        rrp = promo = None
        pct = None
        conf = match_res.confidence

        if cluster is None:
            flags.append("no price cluster found above code")
            conf *= 0.5
        else:
            cid = id(cluster)
            if cid in used_clusters:
                flags.append(f"price cluster shared with {used_clusters[cid]} — layout ambiguity")
            used_clusters[cid] = match_res.matched_code
            price_vals, glyph, mvals, price_notes = _cluster_prices(cluster, member_marks)
            flags.extend(price_notes)
            pcts = find_percents(" ".join(cluster.texts()))
            res = resolve_prices(price_vals, pcts, glyph_pair=glyph, member_vals=mvals)
            rrp, promo, pct = res.rrp, res.promo_price, res.discount_pct
            conf = min(conf, res.confidence)
            if res.member_price is not None:
                member = f"Member price {res.member_price:g}"
            if res.flag_reason:
                flags.append(res.flag_reason)

        info = m.info(match_res.matched_code)
        rows.append(ExtractedRow(
            model_number=match_res.norm_token,
            matched_code=match_res.matched_code,
            brand=info.get("brand", ""),
            product_category=info.get("product_category", ""),
            sub_category=info.get("sub_category", ""),
            capacity=capacity,
            rrp=rrp, promo_price=promo, discount_pct_badge=pct,
            confidence=round(conf, 2),
            flags="; ".join(f for f in flags if f),
            page=page_no,
            other_details=member,
            cluster_dy=(cluster.center[1] - _center(tok)[1]) if cluster else None,
        ))
    return rows


if __name__ == "__main__":
    import csv
    from pathlib import Path

    # replay the saved OCR tokens for p.25 (no re-OCR needed)
    toks = []
    with open("output/ocr/Temporary_Printing_Window_p25_tokens.csv") as f:
        for r in csv.DictReader(f):
            if r["text"].strip():
                toks.append(Token(r["text"], float(r["conf"]),
                                  float(r["x0"]), float(r["y0"]),
                                  float(r["x1"]), float(r["y1"])))
    rows = extract(toks, page_no=25, price_decimals=2)
    print(f"\n=== extracted {len(rows)} product rows from p.25 ===\n")
    for r in rows:
        tag = "OK  " if not r.flags else "FLAG"
        print(f"[{tag}] {r.brand:<8} {r.matched_code:<16} {r.sub_category:<8} "
              f"cap={r.capacity or '-':<10} was={r.rrp} now={r.promo_price} "
              f"badge=-{r.discount_pct_badge}% conf={r.confidence}")
        if r.flags:
            print(f"       flags: {r.flags}")
