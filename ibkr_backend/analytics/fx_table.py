"""Flex `fx_rates` lookup (currency -> base) for per-row conversions in Python.

`fx_rates` holds one `from_ccy -> base` rate per day (Flex ConversionRates).
The helper returns the latest rate on or before a date, so weekends and
holidays resolve like IBKR does. Same-currency is 1.0. A currency with no
rate at all returns ``None`` so the caller decides (skip / flag) instead of
silently treating a native amount as base.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import FxRate, NavDaily


def base_currency(session: Session) -> str:
    """Account base currency from the newest NAV row (fallback: the fx_rates target)."""
    ccy = session.execute(
        select(NavDaily.base_currency)
        .where(NavDaily.base_currency.is_not(None))
        .order_by(NavDaily.snapshot_date.desc())
        .limit(1)
    ).scalar()
    if ccy:
        return ccy
    return session.execute(select(FxRate.to_ccy).limit(1)).scalar() or "CHF"


class FxTable:
    """In-memory `ccy -> base` series, loaded once per request."""

    def __init__(self, session: Session, base: str | None = None):
        self.base = base or base_currency(session)
        self._dates: dict[str, list[date]] = {}
        self._rates: dict[str, list[float]] = {}
        q = (
            select(FxRate.from_ccy, FxRate.rate_date, FxRate.rate)
            .where(FxRate.to_ccy == self.base)
            .order_by(FxRate.from_ccy, FxRate.rate_date)
        )
        for ccy, d, r in session.execute(q):
            self._dates.setdefault(ccy, []).append(d)
            self._rates.setdefault(ccy, []).append(float(r))

    def rate(self, ccy: str | None, on: date | None) -> float | None:
        """Latest `ccy -> base` rate on or before `on` (earliest rate if before the series)."""
        if not ccy or ccy == self.base:
            return 1.0
        dates = self._dates.get(ccy)
        if not dates:
            return None
        if on is None:
            return self._rates[ccy][-1]
        i = bisect_right(dates, on)
        return self._rates[ccy][max(i - 1, 0)]

    def convert(self, amount: float | None, ccy: str | None, on: date | None) -> float | None:
        """`amount` in `ccy` as base on `on`; ``None`` when the rate is unknown."""
        if amount is None:
            return None
        r = self.rate(ccy, on)
        return None if r is None else amount * r
