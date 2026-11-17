"""
Promo date extraction — dates come from the PAGE (locked decision).

Covers the formats Gulf leaflets actually print:
  '7 July - 18 July'      '7 - 18 July'        '7 July to 18 July 2026'
  '07/07/2026 - 18/07/2026'          '7/7 - 18/7/2026'

Year is often NOT printed. Policy (per OUTPUT_SPEC): explicit year -> use it;
else use the caller's year_hint (from filename/context) and FLAG that it was
inferred; no year at all -> blank dates + flag. Never silently guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}
MONTHS.update({m[:3].lower(): i for m, i in [(k.capitalize(), v) for k, v in MONTHS.items()]})
MON = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|" \
      r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
SEP = r"\s*(?:-|–|—|to|till|until)\s*"

# '7 July - 18 July [2026]'  and  '7 - 18 July [2026]'
RANGE_NAMED = re.compile(
    rf"(\d{{1,2}})\s*({MON})?\s*{SEP}(\d{{1,2}})\s*({MON})\s*,?\s*(\d{{4}})?", re.I)
# '07/07[/2026] - 18/07/2026'
RANGE_NUMERIC = re.compile(
    r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?" + SEP + r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?")


@dataclass
class PromoDates:
    start: date | None
    end: date | None
    flags: str = ""

    @property
    def found(self) -> bool:
        return self.start is not None and self.end is not None

    @property
    def duration_days(self) -> int | None:
        return (self.end - self.start).days + 1 if self.found else None

    @property
    def week_number(self) -> int | None:
        return self.start.isocalendar()[1] if self.found else None

    @property
    def year_week(self) -> str:
        if not self.found:
            return ""
        y, w, _ = self.start.isocalendar()
        return f"{y}-W{w:02d}"


def _year(raw: str | None, hint: int | None, flags: list[str]) -> int | None:
    if raw:
        y = int(raw)
        return y + 2000 if y < 100 else y
    if hint:
        flags.append("year not printed — inferred from context")
        return hint
    flags.append("year not printed and no hint — dates left blank")
    return None


def parse_promo_dates(texts: list[str], year_hint: int | None = None) -> PromoDates:
    """Scan OCR token texts for a promo date range. First clean hit wins."""
    blob_list = texts + [" ".join(texts)]   # ranges can split across tokens
    for blob in blob_list:
        m = RANGE_NAMED.search(blob)
        if m:
            d1, mon1, d2, mon2, yr = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            flags: list[str] = []
            year = _year(yr, year_hint, flags)
            if year is None:
                return PromoDates(None, None, "; ".join(flags))
            m2 = MONTHS[mon2.lower()[:3]]
            m1 = MONTHS[mon1.lower()[:3]] if mon1 else m2
            try:
                start, end = date(year, m1, int(d1)), date(year, m2, int(d2))
            except ValueError:
                continue
            if end < start:
                if m1 == m2 and not mon1:
                    return PromoDates(None, None,
                                      f"ambiguous range '{m.group(0).strip()}' (end before start)")
                end = date(year + 1, m2, int(d2))   # Dec -> Jan wrap
                flags.append("range wraps year end")
            return PromoDates(start, end, "; ".join(flags))

        m = RANGE_NUMERIC.search(blob)
        if m:
            d1, mo1, y1, d2, mo2, y2 = m.groups()
            flags = []
            year_end = _year(y2 or y1, year_hint, flags)
            if year_end is None:
                return PromoDates(None, None, "; ".join(flags))
            year_start = int(y1) + 2000 if y1 and int(y1) < 100 else (int(y1) if y1 else year_end)
            try:
                start = date(year_start, int(mo1), int(d1))
                end = date(year_end, int(mo2), int(d2))
            except ValueError:
                continue
            if end < start:
                return PromoDates(None, None,
                                  f"ambiguous numeric range '{m.group(0).strip()}'")
            return PromoDates(start, end, "; ".join(flags))
    return PromoDates(None, None, "no promo date range found on scanned pages")


if __name__ == "__main__":
    cases = [
        (["Valid from 7 July - 18 July"], 2026),
        (["7 - 18 July 2026"], None),
        (["07/07/2026 - 18/07/2026"], None),
        (["Offer 7/7 - 18/7/26"], None),
        (["no dates here"], 2026),
        (["18 - 7 July"], 2026),                       # end < start, same month
        (["28 December - 3 January"], 2026),           # year wrap
    ]
    for texts, hint in cases:
        pd_ = parse_promo_dates(texts, hint)
        print(f"{texts[0]!r:<38} -> start={pd_.start} end={pd_.end} "
              f"days={pd_.duration_days} {pd_.year_week}  flags={pd_.flags!r}")
