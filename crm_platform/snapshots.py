"""Reading the committed source snapshots (see ``scripts/extract_sources.py``)."""

from __future__ import annotations

import calendar
import csv
import math
import unicodedata
from decimal import ROUND_HALF_UP, Context, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "data" / "snapshots"
_EXACT = Context(prec=400)  # room for any double's exact decimal expansion


def rows(tenant: str, name: str, base: Path = SNAPSHOTS) -> list[dict[str, str]]:
    with (base / tenant / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def ascii_slug(text: str, sep: str = "-") -> str:
    """``"Côté Foods & Co"`` → ``"cote-foods-co"``, for synthetic domains and email addresses."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    parts = "".join(c.lower() if c.isalnum() else " " for c in plain).split()
    return sep.join(parts)


def fixed(value: float | str, places: int) -> str:
    """``value`` to ``places`` decimals, a tie going away from zero on the double's exact value.

    That is what JavaScript's ``toFixed`` and Apex's ``setScale(places, RoundingMode.HALF_UP)`` do, and the same
    number is written by all three: the loader here, ``price_guardrail.js`` in HubSpot and the Apex trigger in
    Salesforce. An f-string rounds a tie to even, so a blended margin of exactly 0.53125 was "0.5312" from the
    loader and "0.5313" from the other two, and the loader and the trigger each saw the other's value as a change.
    """
    number = float(value)
    if not math.isfinite(number):
        return f"{number:.{places}f}"
    return format(Decimal(number).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP, context=_EXACT), "f")


def money(value: float | str) -> str:
    return fixed(value, 2)


def number(value: float | str, places: int = 4) -> str:
    text = fixed(value, places)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def month_end(month_start: str) -> str:
    """``"2026-04-01"`` → ``"2026-04-30"``."""
    year, month = int(month_start[:4]), int(month_start[5:7])
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
