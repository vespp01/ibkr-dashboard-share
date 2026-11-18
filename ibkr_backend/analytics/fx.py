"""FX rate helpers.

Primary source (2026-10): the IBKR `fx_rates` table (`<ccy> → CHF` per report
date, loaded from the same Flex statement as the NAV), via `fx_lookup`. It
shares dates and valuation conventions with NAV, so display-currency returns
need no external call.

Fallback: historical spot rates via Frankfurter.app (ECB data, free, no auth),
used ONLY when `fx_rates` has no series for a currency, and always flagged
(`fx_degraded`). `get_fx_rate` / `get_fx_rates` keep their legacy contract
(1.0 on failure) for old callers; `fx_lookup` never invents a rate — dates
without a rate come back missing so callers can null the converted figure.

Rates are cached in-memory; historical rates are immutable so the cache never
needs invalidation. Failures are never written to the cache.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from bisect import bisect_right
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_BASE_URL = "https://api.frankfurter.app"
_cache: dict[tuple[str, str, str], float] = {}


def get_fx_rate(on: str, from_ccy: str, to_ccy: str) -> float:
    """Return spot rate `from_ccy → to_ccy` on the given ISO date string.

    Frankfurter snaps to the nearest prior ECB trading day, so weekend and
    holiday dates resolve automatically. Returns 1.0 on same-currency or on
    any network/parse error.
    """
    from_ccy = from_ccy.upper()
    to_ccy = to_ccy.upper()
    if from_ccy == to_ccy:
        return 1.0

    key = (on, from_ccy, to_ccy)
    if key in _cache:
        return _cache[key]

    rate = _fetch_rate(on, from_ccy, to_ccy)
    if rate is None:
        return 1.0
    _cache[key] = rate
    return rate


def _fetch_rate(on: str, from_ccy: str, to_ccy: str) -> float | None:
    """Fetch a single spot rate from the API; ``None`` on any network/parse error.

    Never writes to the cache — callers cache only real rates, so a transient
    failure does not poison the series and the next call retries.
    """
    url = f"{_BASE_URL}/{on}?base={from_ccy}&symbols={to_ccy}"
    req = urllib.request.Request(url, headers={"User-Agent": "ibkr-dashboard/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
            data = json.loads(resp.read())
        rate = float(data["rates"][to_ccy])
        log.debug("FX %s→%s on %s = %.6f", from_ccy, to_ccy, on, rate)
        return rate
    except Exception as exc:
        log.warning(
            "FX rate fetch failed (%s→%s on %s): %s — degrading to 1.0 for this "
            "call (not cached; next call retries)",
            from_ccy, to_ccy, on, exc,
        )
        return None


def get_fx_rates(on_dates: list[date], from_ccy: str, to_ccy: str) -> dict[date, float]:
    """Return rates for many dates with one network call where possible."""
    from_ccy = from_ccy.upper()
    to_ccy = to_ccy.upper()
    dates = sorted(set(on_dates))
    if not dates:
        return {}
    if from_ccy == to_ccy:
        return {d: 1.0 for d in dates}

    missing = [
        d for d in dates
        if (d.isoformat(), from_ccy, to_ccy) not in _cache
    ]
    if missing:
        start = missing[0].isoformat()
        end = missing[-1].isoformat()
        url = f"{_BASE_URL}/{start}..{end}?base={from_ccy}&symbols={to_ccy}"
        req = urllib.request.Request(url, headers={"User-Agent": "ibkr-dashboard/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
                data = json.loads(resp.read())
            rates_by_day = {
                date.fromisoformat(day): float(values[to_ccy])
                for day, values in data.get("rates", {}).items()
                if to_ccy in values
            }
            last_rate: float | None = None
            for d in missing:
                if d in rates_by_day:
                    last_rate = rates_by_day[d]
                if last_rate is None:
                    last_rate = _fetch_rate(d.isoformat(), from_ccy, to_ccy)
                if last_rate is not None:
                    _cache[(d.isoformat(), from_ccy, to_ccy)] = last_rate
        except Exception as exc:
            # Deliberately do NOT cache failures: a transient outage must not
            # pin 1.0 for these dates for the process lifetime (mixed
            # converted/unconverted NAV series). The return below degrades to
            # 1.0 for this call only; the next call retries the fetch.
            log.warning(
                "FX range fetch failed (%s to %s %s..%s): %s; degrading to 1.0 "
                "for this call (not cached; next call retries)",
                from_ccy, to_ccy, start, end, exc,
            )

    return {
        d: _cache.get((d.isoformat(), from_ccy, to_ccy), 1.0)
        for d in dates
    }


# --------------------------------------------------------------------------
# IBKR fx_rates (primary source)
# --------------------------------------------------------------------------
_BASE_CCY = "CHF"


def _ccy_to_base_series(session: Session, ccy: str) -> tuple[list[date], list[float]]:
    """Sorted (dates, rates) of `ccy → CHF` from the `fx_rates` table."""
    from ..db.models import FxRate

    rows = session.execute(
        select(FxRate.rate_date, FxRate.rate)
        .where(FxRate.from_ccy == ccy, FxRate.to_ccy == _BASE_CCY)
        .order_by(FxRate.rate_date.asc())
    ).all()
    return [d for d, _ in rows], [float(r) for _, r in rows]


def _asof(series: tuple[list[date], list[float]], d: date) -> float | None:
    """Latest rate on/before `d`; before the first date, the first rate.

    Report-date rates are dense (every NAV day), so back-filling only matters
    for benchmark look-back windows that start before the first NAV day.
    """
    dates, rates = series
    if not dates:
        return None
    i = bisect_right(dates, d)
    return rates[i - 1] if i > 0 else rates[0]


def fx_lookup(
    session: Session,
    on_dates: list[date],
    from_ccy: str,
    to_ccy: str,
    *,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    """Rates `from_ccy → to_ccy` for many dates, IBKR `fx_rates` first.

    Returns ``{"rates": {date: float}, "source": ..., "degraded": bool,
    "missing": [date, ...]}``. ``source`` is ``identity`` (same currency),
    ``ibkr_fx_rates`` or ``frankfurter`` (fallback → ``degraded`` True).
    Dates with no rate are listed in ``missing`` and absent from ``rates``.
    """
    from_ccy = from_ccy.upper()
    to_ccy = to_ccy.upper()
    dates = sorted(set(on_dates))
    if from_ccy == to_ccy:
        return {"rates": {d: 1.0 for d in dates}, "source": "identity",
                "degraded": False, "missing": []}

    def to_base(ccy: str):
        if ccy == _BASE_CCY:
            return None  # identity marker
        return _ccy_to_base_series(session, ccy)

    legs = {c: to_base(c) for c in (from_ccy, to_ccy)}
    have_db = all(s is None or s[0] for s in legs.values())
    rates: dict[date, float] = {}
    missing: list[date] = []
    if have_db:
        for d in dates:
            num = 1.0 if legs[from_ccy] is None else _asof(legs[from_ccy], d)
            den = 1.0 if legs[to_ccy] is None else _asof(legs[to_ccy], d)
            if num is None or not den:
                missing.append(d)
            else:
                rates[d] = num / den
        return {"rates": rates, "source": "ibkr_fx_rates",
                "degraded": False, "missing": missing}

    if not allow_fallback:
        return {"rates": {}, "source": "none", "degraded": True,
                "missing": list(dates)}

    # Fallback: Frankfurter, real rates only (never 1.0 by default).
    get_fx_rates(dates, from_ccy, to_ccy)
    for d in dates:
        r = _cache.get((d.isoformat(), from_ccy, to_ccy))
        if r is None:
            missing.append(d)
        else:
            rates[d] = r
    return {"rates": rates, "source": "frankfurter", "degraded": True,
            "missing": missing}
