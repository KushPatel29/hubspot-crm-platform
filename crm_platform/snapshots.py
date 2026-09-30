"""Reading the committed source snapshots (see ``scripts/extract_sources.py``)."""

from __future__ import annotations

import calendar
import csv
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "data" / "snapshots"


def rows(tenant: str, name: str, base: Path = SNAPSHOTS) -> list[dict[str, str]]:
    with (base / tenant / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def ascii_slug(text: str, sep: str = "-") -> str:
    """``"Côté Foods & Co"`` → ``"cote-foods-co"``, for synthetic domains and email addresses."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    parts = "".join(c.lower() if c.isalnum() else " " for c in plain).split()
    return sep.join(parts)


def money(value: float | str) -> str:
    return f"{float(value):.2f}"


def number(value: float | str, places: int = 4) -> str:
    text = f"{float(value):.{places}f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def month_end(month_start: str) -> str:
    """``"2026-04-01"`` → ``"2026-04-30"``."""
    year, month = int(month_start[:4]), int(month_start[5:7])
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
