"""
Layout drift detection — notices when a leaflet stops matching its profile.

Layout rules differ by (retailer, country) and CAN change week-over-week: a
retailer redesigns their flyer and yesterday's geometry rules silently produce
wrong links. This module makes that failure LOUD:

  1. After every extraction, compute the run's observable layout signals
     (where prices actually sat relative to codes, garble rate, clean rate,
     capacity fill, ...).
  2. Compare against the profile's expectations -> human-readable verdicts.
  3. Append everything to output/layout_health.jsonl — the per-(file, country,
     retailer, week) history that makes week-over-week drift visible.

A DRIFT verdict means: audit the page (annotated PNG vs rows), fix
data/master/layout_profiles.json, add a `validated` entry. It never blocks the
run — rows are already flagged individually; this is the page-level alarm.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

HEALTH_LOG = Path("output/layout_health.jsonl")


def assess(rows, profile, ctx, year_week: str = "") -> dict:
    """Compute layout signals for one run and verdicts vs the profile."""
    matched = [r for r in rows if r.matched_code]
    clean = [r for r in matched if not r.flags]
    dys = [r.cluster_dy for r in matched if r.cluster_dy is not None]
    below = sum(1 for d in dys if d > 0)

    signals = {
        "matched": len(matched),
        "clean": len(clean),
        "clean_rate": round(len(clean) / len(matched), 2) if matched else None,
        "priced_rate": round(sum(1 for r in matched if r.promo_price is not None)
                             / len(matched), 2) if matched else None,
        "capacity_rate": round(sum(1 for r in matched if r.capacity)
                               / len(matched), 2) if matched else None,
        "below_share": round(below / len(dys), 2) if dys else None,
        "rescue_count": sum(1 for r in rows if "rescued from garbled" in r.flags),
        "no_cluster_count": sum(1 for r in matched if "no price cluster" in r.flags),
    }

    verdicts: list[str] = []
    if dys:
        expected_below = profile.price_position == "below"
        share = signals["below_share"]
        if expected_below and share < 0.5:
            verdicts.append(f"DRIFT? profile says prices BELOW codes but only "
                            f"{share:.0%} of rows linked downward — layout may "
                            f"have changed; audit + update profile")
        if not expected_below and share > 0.5:
            verdicts.append(f"DRIFT? profile says prices ABOVE codes but "
                            f"{share:.0%} of rows linked downward — layout may "
                            f"have changed; audit + update profile")
    if matched and signals["clean_rate"] is not None and signals["clean_rate"] < 0.5:
        verdicts.append("LOW CLEAN RATE (<50%) — audit the annotated pages")
    if matched and signals["priced_rate"] is not None and signals["priced_rate"] < 0.7:
        verdicts.append("MANY MATCHED ROWS WITHOUT PRICES — price geometry may "
                        "have changed for this retailer/country")
    if signals["rescue_count"] >= 3:
        verdicts.append(f"{signals['rescue_count']} garble rescues — consider "
                        f"raising this profile's zoom")

    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "file": ctx.source_file,
        "retailer": ctx.retailer,
        "country": ctx.country,
        "year_week": year_week,
        "profile": profile.name,
        "zoom": profile.zoom,
        "price_position": profile.price_position,
        **signals,
        "verdicts": verdicts,
    }
    HEALTH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(HEALTH_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def history(retailer: str | None = None, country: str | None = None) -> list[dict]:
    """Past health records, optionally filtered — the week-over-week view."""
    if not HEALTH_LOG.exists():
        return []
    out = []
    for line in HEALTH_LOG.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if retailer and rec.get("retailer") != retailer:
            continue
        if country and rec.get("country") != country:
            continue
        out.append(rec)
    return out
