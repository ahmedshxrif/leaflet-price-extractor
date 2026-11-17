"""
Country / currency resolution — decides how to read prices, per leaflet file.

Currency is NOT a global setting. A Kuwaiti leaflet prices in KWD with 3 decimals
(fils); a UAE leaflet in AED with 2. Get this wrong and every price is silently
corrupted. So we resolve it per file, BEFORE parsing, from the filename.

Filename convention (per PM): the country + retailer are encoded in the name,
e.g. 'uae c4 july.pdf' -> UAE / Carrefour, 'kuwait lulu wk28.pdf' -> Kuwait / Lulu.

Ethos: never silently guess. If the filename carries no country token, we return
country=UNKNOWN and the caller must pass an explicit override — we do not default
to a currency, because a wrong currency is worse than a stopped run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- currency facts (the part that actually drives the price regex) ---
# decimals: how many fractional digits the minor unit uses.
CURRENCIES = {
    "AED": {"country": "UAE",     "decimals": 2},
    "SAR": {"country": "KSA",     "decimals": 2},
    "QAR": {"country": "Qatar",   "decimals": 2},
    "KWD": {"country": "Kuwait",  "decimals": 3},   # fils
    "BHD": {"country": "Bahrain", "decimals": 3},   # fils
    "OMR": {"country": "Oman",    "decimals": 3},   # baisa
}
COUNTRY_TO_CURRENCY = {v["country"]: k for k, v in CURRENCIES.items()}

# --- filename token aliases (lowercased, matched as whole words) ---
# incoming files use COUNTRY_RETAILER prefixes (PM convention: BAH_LULU, QTR_LULU)
COUNTRY_ALIASES = {
    "uae": "UAE", "dubai": "UAE", "abudhabi": "UAE",
    "ksa": "KSA", "saudi": "KSA", "sa": "KSA",
    "qatar": "Qatar", "qat": "Qatar", "qtr": "Qatar",
    "kuwait": "Kuwait", "kwt": "Kuwait", "kw": "Kuwait", "kuw": "Kuwait",
    "bahrain": "Bahrain", "bhr": "Bahrain", "bh": "Bahrain", "bah": "Bahrain",
    "oman": "Oman", "omn": "Oman", "om": "Oman", "oma": "Oman",
}
# The four retailers in scope for now. Spelling variants map to one canonical name.
RETAILER_ALIASES = {
    "c4": "Carrefour", "carrefour": "Carrefour", "cf": "Carrefour",
    "lulu": "Lulu",
    "sharaf": "SharafDG", "sharafdg": "SharafDG", "sdg": "SharafDG",
    "emax": "Emax", "emaxme": "Emax",
}


@dataclass
class LeafletContext:
    source_file: str
    country: str           # 'UAE' ... or 'UNKNOWN'
    currency: str          # 'AED' ... or '' if unknown
    decimals: int | None   # 2 or 3, or None if unknown
    retailer: str          # 'Carrefour' ... or 'UNKNOWN'
    resolved_by: str       # 'filename' | 'override' | 'unresolved'

    @property
    def ok(self) -> bool:
        return self.country != "UNKNOWN" and bool(self.currency)


def _tokens(filename: str) -> list[str]:
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename)          # drop extension
    return [t for t in re.split(r"[^A-Za-z0-9]+", stem.lower()) if t]


def resolve(filename: str, country_override: str | None = None) -> LeafletContext:
    """Resolve currency/country for a leaflet from its filename (or an override)."""
    toks = _tokens(filename)
    retailer = next((RETAILER_ALIASES[t] for t in toks if t in RETAILER_ALIASES), "UNKNOWN")

    # explicit override wins (used for oddly-named files like browser print dumps)
    if country_override:
        country = country_override if country_override in COUNTRY_TO_CURRENCY else \
                  COUNTRY_ALIASES.get(country_override.lower(), "UNKNOWN")
        by = "override"
    else:
        country = next((COUNTRY_ALIASES[t] for t in toks if t in COUNTRY_ALIASES), "UNKNOWN")
        by = "filename" if country != "UNKNOWN" else "unresolved"

    if country == "UNKNOWN":
        return LeafletContext(filename, "UNKNOWN", "", None, retailer, by)

    cur = COUNTRY_TO_CURRENCY[country]
    return LeafletContext(filename, country, cur, CURRENCIES[cur]["decimals"], retailer, by)


if __name__ == "__main__":
    samples = [
        ("uae c4 july2026.pdf", None),
        ("kuwait lulu wk28.pdf", None),
        ("KSA_Carrefour_summer.pdf", None),
        ("oman-emax-eid.pdf", None),
        ("Temporary Printing Window.pdf", None),          # the browser-dump sample
        ("Temporary Printing Window.pdf", "UAE"),          # ... with override
    ]
    for name, ov in samples:
        c = resolve(name, ov)
        tag = "OK  " if c.ok else "STOP"
        ov_s = f" (override={ov})" if ov else ""
        print(f"[{tag}] {name!r}{ov_s}\n        -> {c.country}/{c.currency} "
              f"{c.decimals}dp, {c.retailer}  [{c.resolved_by}]")
