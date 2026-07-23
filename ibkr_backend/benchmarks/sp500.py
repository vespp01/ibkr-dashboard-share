"""S&P 500 fetcher in arbitrary base currency.

Source: Yahoo Finance v8 chart API (free JSON, no auth). The fetcher converts
USD prices to the requested currency on a date-by-date basis using Yahoo's
USD/<ccy>=X series for the same window. Output is a normalised index where
the first day equals 100, so it's directly comparable to a TWR percentage
series.

Persistence: daily closes are cached in the `benchmark_daily` table with an
idempotent upsert — each call fetches from Yahoo only the date range missing
from the DB. If Yahoo is unreachable, whatever is persisted is served (a
possibly partial series, surfacing through the same reliability signalling
as a short Yahoo window today); only an empty cache propagates the network
error. An in-memory 1-hour TTL cache still fronts the network calls so
repeated requests within the same window don't hammer Yahoo.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..db.models import BenchmarkDaily
from ..loaders._upsert import upsert_replace

log = logging.getLogger(__name__)

_YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_CACHE_TTL_SECONDS = 3600
_cache: dict[tuple[str, str, str], tuple[float, list[tuple[date, Decimal]]]] = {}
# Yahoo blocks the default httpx UA; use a browser-shaped one.
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ibkr-dashboard/0.1"


def _yahoo_fetch(symbol: str, from_date: date, to_date: date) -> list[tuple[date, Decimal]]:
    """Return list of (date, close_price) from Yahoo Finance daily series.

    Cached by (symbol, from_date, to_date). Yahoo returns JSON shaped as:
        chart.result[0].timestamp: [unix_ts...]
        chart.result[0].indicators.quote[0].close: [price...]
    """
    key = (symbol, from_date.isoformat(), to_date.isoformat())
    now = time.time()
    cached = _cache.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    period1 = int(datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc).timestamp())
    # Add a day so today's close is included if Yahoo has it.
    period2 = int(datetime(to_date.year, to_date.month, to_date.day, tzinfo=timezone.utc).timestamp()) + 86400
    params = {"period1": period1, "period2": period2, "interval": "1d"}
    url = _YAHOO_URL.format(symbol=symbol)
    r = httpx.get(url, params=params, headers={"User-Agent": _UA}, timeout=20.0)
    r.raise_for_status()
    payload = r.json()

    out: list[tuple[date, Decimal]] = []
    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        return out
    res0 = results[0]
    timestamps = res0.get("timestamp") or []
    quote = (res0.get("indicators") or {}).get("quote") or [{}]
    closes = quote[0].get("close") if quote else None
    if not timestamps or not closes:
        return out
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        try:
            d = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
            c = Decimal(str(close))
        except (ValueError, ArithmeticError):
            continue
        out.append((d, c))
    out.sort(key=lambda x: x[0])
    _cache[key] = (now, out)
    return out


# Symbol mapping: Yahoo uses ^GSPC for SP500.
# FX semantics: Yahoo `<XXX>=X` returns (XXX per 1 USD), e.g. USDCHF=X is
# "CHF per USD" (~0.79), EUR=X is "EUR per USD" (~0.92), GBP=X is "GBP per
# USD" (~0.79), JPY=X is "JPY per USD" (~155). Our formula
# `close_base = close_native_USD * (target_per_USD)` therefore correctly
# converts USD-denominated SPX into the target currency.
_YAHOO_SYMBOL = {
    "^spx": "^GSPC",
    "usdchf": "USDCHF=X",   # CHF per USD
    "usdeur": "EUR=X",      # EUR per USD
    "usdgbp": "GBP=X",      # GBP per USD
    "usdjpy": "JPY=X",      # JPY per USD
}


def _db_load(
    session: Session, symbol: str, from_date: date, to_date: date
) -> list[tuple[date, Decimal]]:
    """Persisted (date, close) rows for `symbol` within the window, sorted."""
    rows = session.execute(
        select(BenchmarkDaily.price_date, BenchmarkDaily.close)
        .where(
            BenchmarkDaily.symbol == symbol,
            BenchmarkDaily.price_date >= from_date,
            BenchmarkDaily.price_date <= to_date,
        )
        .order_by(BenchmarkDaily.price_date)
    ).all()
    return [(d, c) for d, c in rows]


# Maximum calendar-day gap between consecutive persisted rows that is
# considered "expected market closure" (e.g. long weekends, public holidays).
# Any interior gap wider than this triggers a re-fetch of the full range.
_MAX_EXPECTED_GAP_DAYS = 4


def _has_interior_gap(persisted: list[tuple[date, Decimal]]) -> bool:
    """Return True if any two consecutive persisted rows are >_MAX_EXPECTED_GAP_DAYS apart."""
    for i in range(1, len(persisted)):
        if (persisted[i][0] - persisted[i - 1][0]).days > _MAX_EXPECTED_GAP_DAYS:
            return True
    return False


def _cached_fetch(
    session: Session, symbol: str, currency: str, from_date: date, to_date: date
) -> list[tuple[date, Decimal]]:
    """DB-backed daily closes: serve persisted rows, fetch only missing ranges.

    Strategy:
    - Rows with price_date >= today are NEVER persisted (intraday close would
      freeze as the official close). They are fetched fresh on every call and
      merged into the result in memory only.
    - Leading/trailing gaps vs. the requested window are fetched from Yahoo and
      upserted idempotently (PK date+symbol).
    - Interior gaps wider than _MAX_EXPECTED_GAP_DAYS (missing calendar chunks
      not explained by weekends/holidays) trigger a full re-fetch of [from, to]
      so the gap is filled. The upsert is idempotent, so re-fetching overlap
      is safe.
    - On network failure the persisted rows are served as-is (partial series);
      the error is only re-raised when the cache has nothing for the window,
      preserving the pre-cache failure semantics (502 at the route).
    """
    today = date.today()
    # Persist only up through yesterday; today's bar is intraday.
    persist_to = min(to_date, today - timedelta(days=1))
    persisted = _db_load(session, symbol, from_date, persist_to)

    missing: list[tuple[date, date]] = []
    if not persisted:
        if from_date <= persist_to:
            missing.append((from_date, persist_to))
    else:
        # Interior gap: re-fetch the entire [from, persist_to] range so any
        # missing chunk in the middle gets filled.
        if _has_interior_gap(persisted):
            missing = [(from_date, persist_to)]
        else:
            if persisted[0][0] > from_date:
                missing.append((from_date, persisted[0][0] - timedelta(days=1)))
            if persisted[-1][0] < persist_to:
                missing.append((persisted[-1][0] + timedelta(days=1), persist_to))

    fetch_error: Exception | None = None
    fetched_any = False
    for a, b in missing:
        try:
            fetched = _yahoo_fetch(symbol, a, b)
        except Exception as exc:  # noqa: BLE001 — network/Yahoo; cache may cover
            log.warning(
                "Benchmark fetch failed for %s %s..%s — serving persisted rows: %s",
                symbol, a, b, exc,
            )
            fetch_error = exc
            continue
        # Only persist rows that are strictly before today (no frozen intraday).
        rows_to_persist = [
            {"price_date": d, "symbol": symbol, "close": c, "currency": currency}
            for d, c in fetched
            if d < today
        ]
        if rows_to_persist:
            upsert_replace(session, BenchmarkDaily, rows_to_persist)
            fetched_any = True

    # Merge in today's bar from a fresh fetch if the requested window includes today.
    today_rows: list[tuple[date, Decimal]] = []
    if to_date >= today:
        try:
            fresh = _yahoo_fetch(symbol, today, today)
            today_rows = [(d, c) for d, c in fresh if d == today]
        except Exception as exc:  # noqa: BLE001
            log.debug("Could not fetch today's bar for %s: %s", symbol, exc)

    if fetch_error is not None and not persisted and not fetched_any and not today_rows:
        raise fetch_error

    if fetched_any:
        base = _db_load(session, symbol, from_date, persist_to)
    else:
        base = persisted

    # Merge today's live bar at the end (not persisted, served in-memory only).
    if today_rows:
        # Avoid duplicating if today is already in base (edge case: persist_to == today-1,
        # but base could have it if called twice within the same day after a prior persist).
        today_dates = {d for d, _ in base if d >= today}
        extra = [(d, c) for d, c in today_rows if d not in today_dates]
        return base + extra
    return base


def _build_fx_lookup(
    fx_series: list[tuple[date, Decimal]],
) -> dict[date, Decimal]:
    """Date → rate map. Missing dates filled forward in fetch_sp500_in_currency."""
    return {d: r for d, r in fx_series}


def fetch_sp500_in_currency(
    currency: str,
    from_date: date,
    to_date: date,
    *,
    baseline_index: float = 100.0,
) -> list[dict]:
    """Return SP500 daily series re-denominated in `currency`.

    `currency` is the target base currency (e.g. "CHF"). The series is
    normalised so that `from_date`'s closing value equals `baseline_index`
    (default 100). Each row: {date, close_native, fx, close_base, index,
    return_pct} where:
        close_native: SPX USD close
        fx: USD→<currency> on that date (forward-filled if missing)
        close_base: close_native * fx
        index: normalised level (baseline_index at row 0)
        return_pct: cumulative return % since the first row

    Returns [] if neither Yahoo nor the `benchmark_daily` cache has data for
    the window.
    """
    currency = (currency or "").upper()
    if currency not in {"CHF", "EUR", "USD", "GBP", "JPY"}:
        raise ValueError(f"Unsupported currency: {currency}")

    fx_series: list[tuple[date, Decimal]] = []
    # Use separate session_scopes so an FX fetch failure doesn't roll back the
    # SPX rows that were successfully persisted in the same transaction.
    with session_scope() as session:
        spx = _cached_fetch(session, _YAHOO_SYMBOL["^spx"], "USD", from_date, to_date)
    if currency != "USD" and spx:
        fx_yahoo = _YAHOO_SYMBOL[f"usd{currency.lower()}"]
        with session_scope() as session:
            fx_series = _cached_fetch(session, fx_yahoo, currency, from_date, to_date)
    if not spx:
        return []

    fx_lookup: dict[date, Decimal] = {}
    if currency == "USD":
        # Identity; no FX needed.
        for d, _ in spx:
            fx_lookup[d] = Decimal("1")
    else:
        fx_lookup = _build_fx_lookup(fx_series)
        if not fx_lookup:
            log.warning(
                "No FX data for usd%s in window %s..%s", currency.lower(), from_date, to_date
            )
            return []

    # Forward-fill missing FX dates (SP500 trading days where FX market closed).
    last_fx: Decimal | None = None
    rows: list[dict] = []
    baseline_base: Decimal | None = None
    for d, close_native in spx:
        fx_today = fx_lookup.get(d)
        if fx_today is not None:
            last_fx = fx_today
        if last_fx is None:
            continue
        close_base = close_native * last_fx
        if baseline_base is None:
            baseline_base = close_base
        index = (close_base / baseline_base) * Decimal(str(baseline_index))
        return_pct = (close_base / baseline_base - Decimal("1")) * Decimal("100")
        rows.append({
            "date": d.isoformat(),
            "close_native": float(close_native),
            "fx": float(last_fx),
            "close_base": float(close_base),
            "index": float(index),
            "return_pct": float(return_pct),
        })
    return rows
