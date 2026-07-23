"""Period filter helpers usable from FastAPI query params or scripts.

A filter is one of:
  * `year` (e.g. 2026) — Jan 1 .. Dec 31
  * `year` + `month` (e.g. 2026, 4) — Apr 1 .. Apr 30
  * `from_date` + `to_date` — explicit ISO range
  * `period`: `ytd`, `mtd`, `1m`, `3m`, `6m`, `1y`, `3y`, `5y`, `all`

`resolve_period(...)` returns a `PeriodFilter` you can use anywhere.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

PeriodToken = Literal[
    "ytd", "mtd", "1m", "3m", "6m", "1y", "3y", "5y", "all", "qtd",
]


@dataclass(slots=True)
class PeriodFilter:
    """Inclusive date range. Both ends may be None (= unbounded)."""

    start: date | None
    end: date | None
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "label": self.label,
        }


def resolve_period(
    *,
    period: PeriodToken | str | None = None,
    year: int | None = None,
    month: int | None = None,
    from_date: date | str | None = None,
    to_date: date | str | None = None,
    today: date | None = None,
) -> PeriodFilter:
    today = today or date.today()
    if from_date or to_date:
        return PeriodFilter(
            start=_as_date(from_date), end=_as_date(to_date),
            label=f"{from_date or 'beginning'} → {to_date or 'today'}",
        )
    if year is not None:
        if month is not None:
            last = calendar.monthrange(year, month)[1]
            return PeriodFilter(
                start=date(year, month, 1),
                end=date(year, month, last),
                label=f"{year}-{month:02d}",
            )
        return PeriodFilter(
            start=date(year, 1, 1), end=date(year, 12, 31), label=str(year)
        )
    if not period:
        return PeriodFilter(start=None, end=None, label="all")
    p = period.lower()
    if p == "ytd":
        return PeriodFilter(start=date(today.year, 1, 1), end=today, label="ytd")
    if p == "mtd":
        return PeriodFilter(start=date(today.year, today.month, 1), end=today, label="mtd")
    if p == "qtd":
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        return PeriodFilter(
            start=date(today.year, q_start_month, 1), end=today, label="qtd"
        )
    if p == "all":
        return PeriodFilter(start=None, end=None, label="all")
    days = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "3y": 365 * 3, "5y": 365 * 5}
    if p in days:
        return PeriodFilter(start=today - timedelta(days=days[p]), end=today, label=p)
    raise ValueError(f"Unknown period: {period}")


def _as_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))
