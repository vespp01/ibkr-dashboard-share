"""Tests for the `benchmark_daily` DB cache in ibkr_backend/benchmarks/sp500.py.

Design decisions:
- Real SQLite via the autouse _isolated_db fixture (no DB mocking — project rule).
- Only the network layer (`_yahoo_fetch`) is monkeypatched; the cache logic,
  upsert and USD→CHF conversion run for real against the DB.
- Failure semantics under test: network down + populated cache → series served
  from DB (possibly partial); network down + empty cache → error propagates
  (pre-cache behaviour: 502 at the route).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from ibkr_backend.benchmarks import sp500
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import BenchmarkDaily

START = date(2026, 1, 5)
# Use a fixed past date for tests so `date.today()` logic doesn't interfere.
PAST_END = date(2026, 3, 31)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_benchmark(days: int, start: date = START) -> None:
    """Pre-populate ^GSPC closes (USD) + USDCHF=X rates for `days` calendar days."""
    with session_scope() as s:
        for i in range(days):
            d = start + timedelta(days=i)
            s.add(BenchmarkDaily(
                price_date=d, symbol="^GSPC",
                close=Decimal(5000 + i), currency="USD",
            ))
            s.add(BenchmarkDaily(
                price_date=d, symbol="USDCHF=X",
                close=Decimal("0.9"), currency="CHF",
            ))


def _network_down(symbol: str, from_date: date, to_date: date):
    raise RuntimeError("network down")


def _count(symbol: str) -> int:
    with session_scope() as s:
        return s.execute(
            select(func.count())
            .select_from(BenchmarkDaily)
            .where(BenchmarkDaily.symbol == symbol)
        ).scalar_one()


# ---------------------------------------------------------------------------
# Network down → serve from DB
# ---------------------------------------------------------------------------


def test_served_from_db_when_window_fully_cached(monkeypatch):
    """Fully cached window: no network call at all, full series from DB."""
    monkeypatch.setattr(sp500, "_yahoo_fetch", _network_down)
    _seed_benchmark(days=10)

    rows = sp500.fetch_sp500_in_currency("CHF", START, START + timedelta(days=9))

    assert len(rows) == 10
    assert rows[0]["close_native"] == pytest.approx(5000.0)
    assert rows[0]["fx"] == pytest.approx(0.9)
    assert rows[0]["close_base"] == pytest.approx(5000.0 * 0.9)
    assert rows[0]["index"] == pytest.approx(100.0)
    assert rows[-1]["close_native"] == pytest.approx(5009.0)
    # Flat FX → index growth equals native growth.
    assert rows[-1]["index"] == pytest.approx(100.0 * 5009.0 / 5000.0)


def test_network_down_serves_persisted_partial_series(monkeypatch):
    """Window extends beyond the cache + network down → partial series, no raise."""
    monkeypatch.setattr(sp500, "_yahoo_fetch", _network_down)
    _seed_benchmark(days=10)

    rows = sp500.fetch_sp500_in_currency("CHF", START, START + timedelta(days=20))

    # Only the 10 persisted days are served — partial, same shape as a short
    # Yahoo window today; reliability flags downstream handle the gap.
    assert len(rows) == 10
    assert rows[-1]["date"] == (START + timedelta(days=9)).isoformat()


def test_network_down_empty_cache_raises(monkeypatch):
    """Nothing persisted + network down → error propagates (route turns it into 502)."""
    monkeypatch.setattr(sp500, "_yahoo_fetch", _network_down)

    with pytest.raises(RuntimeError, match="network down"):
        sp500.fetch_sp500_in_currency("CHF", START, START + timedelta(days=5))


# ---------------------------------------------------------------------------
# Missing-range fetch + idempotent upsert
# ---------------------------------------------------------------------------


def _fake_yahoo_factory(calls: list, close: str = "6000"):
    def _fake_yahoo(symbol: str, a: date, b: date) -> list[tuple[date, Decimal]]:
        calls.append((symbol, a, b))
        out = []
        d = a
        while d <= b:
            out.append((d, Decimal(close)))
            d += timedelta(days=1)
        return out
    return _fake_yahoo


def test_fetches_only_trailing_missing_range(monkeypatch):
    """With days 0..4 cached and a 0..7 window, only 5..7 hits the network."""
    _seed_benchmark(days=5)
    calls: list = []
    monkeypatch.setattr(sp500, "_yahoo_fetch", _fake_yahoo_factory(calls))

    end = START + timedelta(days=7)
    rows = sp500.fetch_sp500_in_currency("CHF", START, end)

    gspc_calls = [c for c in calls if c[0] == "^GSPC"]
    assert gspc_calls == [("^GSPC", START + timedelta(days=5), end)]
    # Union of persisted + fetched: 8 days.
    assert len(rows) == 8
    assert _count("^GSPC") == 8


def test_fetches_only_leading_missing_range(monkeypatch):
    """With days 5..9 cached and a 0..9 window, only 0..4 hits the network."""
    _seed_benchmark(days=5, start=START + timedelta(days=5))
    calls: list = []
    monkeypatch.setattr(sp500, "_yahoo_fetch", _fake_yahoo_factory(calls))

    end = START + timedelta(days=9)
    rows = sp500.fetch_sp500_in_currency("CHF", START, end)

    gspc_calls = [c for c in calls if c[0] == "^GSPC"]
    assert gspc_calls == [("^GSPC", START, START + timedelta(days=4))]
    assert len(rows) == 10
    assert _count("^GSPC") == 10


def test_upsert_pk_constraint_prevents_duplicates(monkeypatch):
    """A fetch that overlaps cached days upserts them: no duplicate PKs.

    Cache days 0,1,2 and 8,9 at close 5000+i (interior gap of 6 days > the
    4-day tolerance), so a 0..9 window forces a full re-fetch of [0, 9] at
    close=7000. That fetch overlaps the 5 cached days. Expected: 10 unique
    (date, symbol) rows (not 5 + 10 = 15), and every one holds 7000 because the
    ON CONFLICT DO UPDATE replaced the old closes (seeded 5000, 5001, 5002,
    5008, 5009).
    """
    _seed_benchmark(days=3)
    _seed_benchmark(days=2, start=START + timedelta(days=8))
    assert _count("^GSPC") == 5

    calls: list = []
    monkeypatch.setattr(sp500, "_yahoo_fetch", _fake_yahoo_factory(calls, close="7000"))

    end = START + timedelta(days=9)
    rows = sp500.fetch_sp500_in_currency("CHF", START, end)

    # The fetch really overlapped the cache: one request over the whole window.
    assert [c for c in calls if c[0] == "^GSPC"] == [("^GSPC", START, end)]
    # 10 unique date+symbol PKs: no duplicates despite the 5-day overlap.
    assert _count("^GSPC") == 10, "upsert must not insert duplicate PKs"
    assert len(rows) == 10
    with session_scope() as s:
        closes = {
            d: c for d, c in s.execute(
                select(BenchmarkDaily.price_date, BenchmarkDaily.close)
                .where(BenchmarkDaily.symbol == "^GSPC")
            ).all()
        }
    assert sorted(closes) == [START + timedelta(days=i) for i in range(10)]
    assert all(c == Decimal("7000") for c in closes.values()), "overlap rows must be overwritten"


# ---------------------------------------------------------------------------
# BLOCKER 1: today's intraday close must not be frozen in the DB
# ---------------------------------------------------------------------------


def test_today_bar_not_persisted_and_refreshes_each_call(monkeypatch):
    """Intraday bar for today is served in-memory but never written to the DB.

    If today's close changes between two calls (price moves during the session),
    the second call must reflect the updated value — not return the value from
    the first call frozen in the DB.
    """
    today = date.today()
    calls: list = []

    # First call: yahoo returns today's bar at close=6000.
    def _fake_first(symbol: str, a: date, b: date) -> list[tuple[date, Decimal]]:
        calls.append((symbol, a, b))
        out = []
        d = a
        while d <= b:
            out.append((d, Decimal("6000")))
            d += timedelta(days=1)
        return out

    monkeypatch.setattr(sp500, "_yahoo_fetch", _fake_first)
    rows1 = sp500.fetch_sp500_in_currency("USD", today, today)

    # today's row is NOT persisted.
    assert _count("^GSPC") == 0, "today's intraday bar must not be persisted"

    # Second call: today's close moved to 6500.
    def _fake_second(symbol: str, a: date, b: date) -> list[tuple[date, Decimal]]:
        calls.append((symbol, a, b))
        out = []
        d = a
        while d <= b:
            out.append((d, Decimal("6500")))
            d += timedelta(days=1)
        return out

    monkeypatch.setattr(sp500, "_yahoo_fetch", _fake_second)
    rows2 = sp500.fetch_sp500_in_currency("USD", today, today)

    # The second call must return the updated value (not the frozen 6000).
    assert rows2, "should return today's row"
    assert rows2[0]["close_native"] == pytest.approx(6500.0), (
        "today's bar must be re-fetched every call, not served from a frozen DB row"
    )


# ---------------------------------------------------------------------------
# BLOCKER 2: interior gaps wider than _MAX_EXPECTED_GAP_DAYS must be re-fetched
# ---------------------------------------------------------------------------


def test_interior_gap_triggers_refetch(monkeypatch):
    """Pre-populate January and March; a full-range request must fill February.

    The DB has rows for 2026-01-05..2026-01-15 and 2026-03-01..2026-03-10 —
    a ~45-day interior gap that exceeds _MAX_EXPECTED_GAP_DAYS. When the full
    range Jan..Mar is requested and the network is available, _cached_fetch
    must detect the interior gap and re-fetch so February is filled.
    """
    jan_start = date(2026, 1, 5)
    jan_end = date(2026, 1, 15)
    mar_start = date(2026, 3, 1)
    mar_end = date(2026, 3, 10)

    # Pre-populate January and March — no February.
    with session_scope() as s:
        for seg_start, seg_end in [(jan_start, jan_end), (mar_start, mar_end)]:
            d = seg_start
            while d <= seg_end:
                s.add(BenchmarkDaily(
                    price_date=d, symbol="^GSPC",
                    close=Decimal("5000"), currency="USD",
                ))
                s.add(BenchmarkDaily(
                    price_date=d, symbol="USDCHF=X",
                    close=Decimal("0.9"), currency="CHF",
                ))
                d += timedelta(days=1)

    calls: list = []
    monkeypatch.setattr(sp500, "_yahoo_fetch", _fake_yahoo_factory(calls))

    # Request the full Jan..Mar range.
    rows = sp500.fetch_sp500_in_currency("CHF", jan_start, mar_end)

    # ^GSPC must have been re-fetched (interior gap detected).
    gspc_calls = [c for c in calls if c[0] == "^GSPC"]
    assert gspc_calls, "interior gap must trigger a re-fetch of ^GSPC"

    # After the re-fetch the DB should have rows covering February too.
    feb_start = date(2026, 2, 1)
    feb_end = date(2026, 2, 28)
    with session_scope() as s:
        feb_count = s.execute(
            select(func.count())
            .select_from(BenchmarkDaily)
            .where(
                BenchmarkDaily.symbol == "^GSPC",
                BenchmarkDaily.price_date >= feb_start,
                BenchmarkDaily.price_date <= feb_end,
            )
        ).scalar_one()
    assert feb_count > 0, "interior gap (February) must be filled after re-fetch"

    # The returned series must span the full range.
    assert len(rows) > 0
    dates_returned = {r["date"] for r in rows}
    assert jan_start.isoformat() in dates_returned
    assert mar_end.isoformat() in dates_returned
