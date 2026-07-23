"""FX rate helper — historical spot rates via Frankfurter.app (ECB data, free, no auth).

Rates are cached in-memory; historical rates are immutable so the cache never
needs invalidation. If the API is unreachable, returns 1.0 so callers degrade
gracefully (TWR stays in the original currency) — but failures are never
written to the cache, so the next call retries the fetch.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import date

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
