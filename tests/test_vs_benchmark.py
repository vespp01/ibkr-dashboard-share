"""Tests for vs_benchmark() in ibkr_backend/analytics/risk.py.

Design decisions:
- Uses the project-standard _isolated_db fixture (real SQLite via tmp_path, no mocking).
- Patches fetch_sp500_in_currency at its origin module so both callers inside
  risk.py (vs_benchmark and alpha_vs_benchmark / _tracking_error) see the fake.
- Synthetic NavDaily covers: 2 sparse year-end snapshots + a 35-day daily run
  so both sparse and dense segments are exercised.
- ChangeInNavDaily populated for the 35-day dense run to satisfy risk_comparison.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from ibkr_backend.analytics.filters import PeriodFilter
from ibkr_backend.analytics.risk import vs_benchmark
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import ChangeInNavDaily, NavDaily

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ACCOUNT = "U1234567"


def _nav(snapshot_date: date, nav: float, cashflow: float = 0.0) -> NavDaily:
    return NavDaily(
        snapshot_date=snapshot_date,
        account_id=ACCOUNT,
        nav_total=Decimal(str(nav)),
        external_cashflow=Decimal(str(cashflow)),
    )


def _change(snapshot_date: date, twr_pct: float) -> ChangeInNavDaily:
    """twr is stored as a percentage (e.g. 0.05 means 0.05 %) in IBKR's field."""
    return ChangeInNavDaily(
        snapshot_date=snapshot_date,
        account_id=ACCOUNT,
        currency="CHF",
        twr=Decimal(str(twr_pct)),
    )


def _synthetic_sp500(currency: str, from_date: date, to_date: date) -> list[dict]:
    """Deterministic flat-ish SP500 series: starts at 100, grows +0.05 % per day.

    Returns rows for every calendar day in [from_date, to_date], mimicking the
    dict shape that fetch_sp500_in_currency() returns.
    """
    baseline = 5000.0
    rows: list[dict] = []
    d = from_date
    idx = 0
    while d <= to_date:
        close_native = baseline * (1.0005 ** idx)
        fx = 0.90  # fixed CHF/USD
        close_base = close_native * fx
        rows.append({
            "date": d.isoformat(),
            "close_native": close_native,
            "fx": fx,
            "close_base": close_base,
            "index": 100.0 * (1.0005 ** idx),
            "return_pct": ((1.0005 ** idx) - 1.0) * 100.0,
        })
        d += timedelta(days=1)
        idx += 1
    return rows


def _seed_data_full():
    """Seed NavDaily + ChangeInNavDaily with sparse + dense segments."""
    rows_nav: list[NavDaily] = []
    rows_change: list[ChangeInNavDaily] = []

    # Sparse segment: 2 year-end NAV snapshots (2023, 2024) — big gap, no daily TWR.
    rows_nav.append(_nav(date(2023, 12, 31), nav=50_000.0))
    rows_nav.append(_nav(date(2024, 12, 31), nav=60_000.0))

    # Dense segment: 35 consecutive daily rows in Jan–Feb 2026.
    dense_start = date(2026, 1, 5)
    nav = 60_000.0
    daily_return_pct = 0.12   # +0.12 % per day → meaningful cumulative return

    for i in range(35):
        d = dense_start + timedelta(days=i)
        nav *= (1 + daily_return_pct / 100.0)
        rows_nav.append(_nav(d, nav=nav))
        rows_change.append(_change(d, twr_pct=daily_return_pct))

    with session_scope() as s:
        s.add_all(rows_nav)
        s.add_all(rows_change)


def _seed_data_sparse_only():
    """Seed only 1 NAV row — degenerate case."""
    with session_scope() as s:
        s.add(_nav(date(2026, 1, 10), nav=100_000.0))


def _seed_data_dense():
    """Seed a continuous daily NAV series (reliable: coverage≈1, max_gap=1).

    Includes one mid-series deposit so external_cashflow is exercised. This is
    the shape that passes the honesty guard (quality.is_reliable=True).
    """
    rows_nav: list[NavDaily] = []
    rows_change: list[ChangeInNavDaily] = []
    start = date(2026, 1, 5)
    nav = 60_000.0
    daily_return_pct = 0.12
    for i in range(40):
        d = start + timedelta(days=i)
        cf = 5_000.0 if i == 12 else 0.0  # one deposit mid-series
        nav = nav * (1 + daily_return_pct / 100.0) + cf
        rows_nav.append(_nav(d, nav=nav, cashflow=cf))
        rows_change.append(_change(d, twr_pct=daily_return_pct))
    with session_scope() as s:
        s.add_all(rows_nav)
        s.add_all(rows_change)


# ---------------------------------------------------------------------------
# Core test: happy path
# ---------------------------------------------------------------------------


def test_vs_benchmark_happy_path(monkeypatch):
    """vs_benchmark returns is_reliable=True with correct alignment and rebase."""
    monkeypatch.setattr(
        "ibkr_backend.benchmarks.sp500.fetch_sp500_in_currency",
        _synthetic_sp500,
    )

    _seed_data_dense()

    with session_scope() as session:
        result = vs_benchmark(
            session,
            period=PeriodFilter(start=None, end=None, label="all"),
            account_id=ACCOUNT,
            benchmark_currency="CHF",
            display_currency="CHF",
        )

    assert result["is_reliable"] is True, f"Expected reliable, got: {result.get('reason')}"
    assert len(result["points"]) >= 2

    # 1. Alignment: every point has both portfolio and benchmark.
    for pt in result["points"]:
        assert "portfolio" in pt
        assert "benchmark" in pt
        assert pt["date"] is not None

    # 2. Rebase: first point must be 100.0 for both series.
    first = result["points"][0]
    assert abs(first["portfolio"] - 100.0) < 1e-6, f"Portfolio first point not 100: {first['portfolio']}"
    assert abs(first["benchmark"] - 100.0) < 1e-6, f"Benchmark first point not 100: {first['benchmark']}"


def test_vs_benchmark_excess_return(monkeypatch):
    """level_comparison.excess_return_pct == portfolio_return_pct - benchmark_return_pct."""
    monkeypatch.setattr(
        "ibkr_backend.benchmarks.sp500.fetch_sp500_in_currency",
        _synthetic_sp500,
    )

    _seed_data_dense()

    with session_scope() as session:
        result = vs_benchmark(
            session,
            period=PeriodFilter(start=None, end=None, label="all"),
            account_id=ACCOUNT,
            benchmark_currency="CHF",
            display_currency="CHF",
        )

    lc = result["level_comparison"]
    expected_excess = lc["portfolio_return_pct"] - lc["benchmark_return_pct"]
    assert abs(lc["excess_return_pct"] - expected_excess) < 1e-6, (
        f"excess_return_pct {lc['excess_return_pct']} != "
        f"portfolio {lc['portfolio_return_pct']} - benchmark {lc['benchmark_return_pct']}"
    )


def test_vs_benchmark_resolution_fields(monkeypatch):
    """resolution reflects a dense series: small gap, not sparse."""
    monkeypatch.setattr(
        "ibkr_backend.benchmarks.sp500.fetch_sp500_in_currency",
        _synthetic_sp500,
    )

    _seed_data_dense()

    with session_scope() as session:
        result = vs_benchmark(
            session,
            period=PeriodFilter(start=None, end=None, label="all"),
            account_id=ACCOUNT,
            benchmark_currency="CHF",
            display_currency="CHF",
        )

    res = result["resolution"]
    # Continuous daily series → max gap is 1 day, not sparse.
    assert res["max_gap_days"] <= 3, f"Expected small gap, got {res['max_gap_days']}"
    assert res["is_sparse"] is False
    assert res["points"] > 0


def test_vs_benchmark_guard_blocks_gappy_series(monkeypatch):
    """Honesty guard: a NAV series with year-end gaps (the residual-cashflow
    failure mode) must return is_reliable=False instead of a misleading level
    comparison — mirrors twr_series' own quality flag.
    """
    monkeypatch.setattr(
        "ibkr_backend.benchmarks.sp500.fetch_sp500_in_currency",
        _synthetic_sp500,
    )

    _seed_data_full()  # 2 year-end snapshots + dense run → max_gap ~370 days

    with session_scope() as session:
        result = vs_benchmark(
            session,
            period=PeriodFilter(start=None, end=None, label="all"),
            account_id=ACCOUNT,
            benchmark_currency="CHF",
            display_currency="CHF",
        )

    assert result["is_reliable"] is False
    assert result["points"] == []
    assert "fiable" in result.get("reason", "").lower()


def test_vs_benchmark_degenerate_too_few_rows(monkeypatch):
    """With < 2 NavDaily rows, vs_benchmark returns is_reliable=False."""
    monkeypatch.setattr(
        "ibkr_backend.benchmarks.sp500.fetch_sp500_in_currency",
        _synthetic_sp500,
    )

    _seed_data_sparse_only()

    with session_scope() as session:
        result = vs_benchmark(
            session,
            period=PeriodFilter(start=None, end=None, label="all"),
            account_id=ACCOUNT,
            benchmark_currency="CHF",
            display_currency="CHF",
        )

    assert result["is_reliable"] is False
    assert result["points"] == []


def test_vs_benchmark_degenerate_no_rows(monkeypatch):
    """With empty NavDaily, vs_benchmark returns is_reliable=False (no seed)."""
    monkeypatch.setattr(
        "ibkr_backend.benchmarks.sp500.fetch_sp500_in_currency",
        _synthetic_sp500,
    )

    # DB is empty — _isolated_db fixture starts fresh each test.
    with session_scope() as session:
        result = vs_benchmark(
            session,
            period=PeriodFilter(start=None, end=None, label="all"),
            account_id=ACCOUNT,
            benchmark_currency="CHF",
            display_currency="CHF",
        )

    assert result["is_reliable"] is False


def test_vs_benchmark_dense_only_segment(monkeypatch):
    """With only the dense 35-day segment, both series still rebase to 100."""
    monkeypatch.setattr(
        "ibkr_backend.benchmarks.sp500.fetch_sp500_in_currency",
        _synthetic_sp500,
    )

    dense_start = date(2026, 1, 5)
    with session_scope() as s:
        nav = 60_000.0
        daily_return_pct = 0.08
        for i in range(35):
            d = dense_start + timedelta(days=i)
            nav *= (1 + daily_return_pct / 100.0)
            s.add(_nav(d, nav=nav))
            s.add(_change(d, twr_pct=daily_return_pct))

    with session_scope() as session:
        result = vs_benchmark(
            session,
            period=PeriodFilter(
                start=dense_start,
                end=dense_start + timedelta(days=34),
                label="dense",
            ),
            account_id=ACCOUNT,
            benchmark_currency="CHF",
            display_currency="CHF",
        )

    assert result["is_reliable"] is True
    pts = result["points"]
    assert len(pts) == 35

    first = pts[0]
    assert abs(first["portfolio"] - 100.0) < 1e-6
    assert abs(first["benchmark"] - 100.0) < 1e-6

    # Portfolio should have grown (positive daily returns).
    last = pts[-1]
    assert last["portfolio"] > 100.0

    # max_gap_days should be 1 (consecutive days).
    assert result["resolution"]["max_gap_days"] == 1
    assert result["resolution"]["is_sparse"] is False


def test_vs_benchmark_benchmark_fetch_failure(monkeypatch):
    """When the benchmark fetch raises, vs_benchmark returns is_reliable=False."""

    def _failing_fetch(currency, from_date, to_date):
        raise RuntimeError("Yahoo down")

    monkeypatch.setattr(
        "ibkr_backend.benchmarks.sp500.fetch_sp500_in_currency",
        _failing_fetch,
    )

    # Seed a couple of NavDaily rows so twr_series returns ≥ 2 points.
    with session_scope() as s:
        s.add(_nav(date(2026, 1, 5), nav=50_000.0))
        s.add(_nav(date(2026, 1, 6), nav=50_200.0))

    with session_scope() as session:
        result = vs_benchmark(
            session,
            period=PeriodFilter(start=None, end=None, label="all"),
            account_id=ACCOUNT,
            benchmark_currency="CHF",
            display_currency="CHF",
        )

    assert result["is_reliable"] is False
    assert "Benchmark fetch failed" in result.get("reason", "")
