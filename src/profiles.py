"""
Retailer layout profiles — loaded from data/master/layout_profiles.json.

Layout rules differ by retailer AND can differ by country, AND can change
week-over-week (PM requirement). So:

  - Profiles are DATA (the json), not code — edit the file, no deploy.
  - Lookup order: '<Retailer>/<Country>' override -> '<Retailer>' -> DEFAULT.
  - Every profile carries a `validated` audit trail (country/week/file it was
    last verified against).
  - Drift detection lives in layout_health.py: each run's observed layout
    signals are compared to the profile and logged; a mismatch warns loudly
    instead of silently mis-extracting a redesigned leaflet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PROFILE_FILE = Path("data/master/layout_profiles.json")


@dataclass(frozen=True)
class RetailerProfile:
    name: str
    zoom: float = 3.0
    price_position: str = "above"     # price block relative to the model code
    tile_notes: str = ""
    validated: tuple = field(default_factory=tuple)


DEFAULT = RetailerProfile(
    name="DEFAULT",
    tile_notes="Unknown layout — audit the first page manually (annotated PNG "
               "vs extracted rows) before trusting batch output.",
)


def _load() -> dict[str, RetailerProfile]:
    if not PROFILE_FILE.exists():
        return {}
    raw = json.loads(PROFILE_FILE.read_text())["profiles"]
    out = {}
    for key, p in raw.items():
        out[key] = RetailerProfile(
            name=key,
            zoom=float(p.get("zoom", 3.0)),
            price_position=p.get("price_position", "above"),
            tile_notes=p.get("notes", ""),
            validated=tuple(tuple(sorted(v.items())) for v in p.get("validated", [])),
        )
    return out


def get_profile(retailer: str, country: str | None = None) -> RetailerProfile:
    """Most specific wins: 'Retailer/Country' -> 'Retailer' -> DEFAULT."""
    profiles = _load()
    if country:
        hit = profiles.get(f"{retailer}/{country}")
        if hit:
            return hit
    return profiles.get(retailer, DEFAULT)
