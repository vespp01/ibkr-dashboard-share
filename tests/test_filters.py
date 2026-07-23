"""PeriodFilter helper."""

from __future__ import annotations

from datetime import date

from ibkr_backend.analytics.filters import resolve_period


def test_year_only():
    p = resolve_period(year=2025)
    assert p.start == date(2025, 1, 1)
    assert p.end == date(2025, 12, 31)


def test_year_month():
    p = resolve_period(year=2025, month=2)
    assert p.start == date(2025, 2, 1)
    assert p.end == date(2025, 2, 28)


def test_period_ytd():
    p = resolve_period(period="ytd", today=date(2025, 4, 15))
    assert p.start == date(2025, 1, 1)
    assert p.end == date(2025, 4, 15)


def test_period_1m():
    p = resolve_period(period="1m", today=date(2025, 4, 15))
    assert p.end == date(2025, 4, 15)
    assert (p.end - p.start).days == 30


def test_explicit_range():
    p = resolve_period(from_date="2024-06-01", to_date="2024-09-30")
    assert p.start == date(2024, 6, 1)
    assert p.end == date(2024, 9, 30)


def test_all_period():
    p = resolve_period(period="all")
    assert p.start is None and p.end is None
