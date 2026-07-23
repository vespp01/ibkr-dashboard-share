"""Time-Weighted Return — daily Modified Dietz chained.

For each day t with NAV_open, NAV_close and an external cashflow CF (deposits
positive, withdrawals negative), the daily return is

    r_t = (NAV_close - NAV_open - CF) / (NAV_open + 0.5 * CF)

TWR over the period is then `prod(1 + r_t) - 1`. This matches IBKR's own
PortfolioAnalyst Time-Weighted Return.

External cashflows are sourced from `nav_daily.external_cashflow`, which the
loader populates from CashTransactions of type Deposits/Withdrawals and
Internal Transfers.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import NavDaily
from .filters import PeriodFilter
from .fx import get_fx_rates

_ACCOUNT_BASE_CURRENCY = "CHF"


def _series_quality(dates: list[date]) -> dict[str, Any]:
    if not dates:
        return {
            "is_reliable": False,
            "reason": "Sin datos NAV.",
            "rows": 0,
            "expected_days": 0,
            "coverage_ratio": 0.0,
            "max_gap_days": None,
        }
    if len(dates) == 1:
        return {
            "is_reliable": False,
            "reason": "Solo hay un punto NAV; no se puede medir retorno fiable.",
            "rows": 1,
            "expected_days": 1,
            "coverage_ratio": 1.0,
            "max_gap_days": 0,
        }

    expected_days = (dates[-1] - dates[0]).days + 1
    gaps = [(dates[idx] - dates[idx - 1]).days for idx in range(1, len(dates))]
    max_gap = max(gaps) if gaps else 0
    coverage_ratio = len(dates) / expected_days if expected_days > 0 else 1.0
    is_reliable = coverage_ratio >= 0.5 and max_gap <= 10
    if is_reliable:
        reason = "Cobertura NAV suficiente para el periodo."
    elif coverage_ratio < 0.5:
        reason = "Cobertura NAV demasiado dispersa para el periodo."
    else:
        reason = "Hay huecos demasiado largos entre snapshots NAV."
    return {
        "is_reliable": is_reliable,
        "reason": reason,
        "rows": len(dates),
        "expected_days": expected_days,
        "coverage_ratio": coverage_ratio,
        "max_gap_days": max_gap,
    }


def twr_series(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    display_currency: str | None = None,
) -> dict[str, Any]:
    """Return cumulative TWR series + headline numbers.

    When `display_currency` differs from the account base currency (CHF),
    each NAV snapshot and cash flow is converted at the spot rate on that date
    before running the Modified Dietz formula. This strips the CHF appreciation
    or depreciation effect from the return.

    Warm-up rows are skipped: if the very first NAV snapshots are tiny (e.g.,
    an account-opening promo balance of < 0.5 % of the period's max NAV),
    Modified Dietz produces meaningless "thousand-percent" daily returns from
    the first deposit. We start the chain at the first row whose NAV exceeds
    `max(100, 0.005 × max_nav)` in display currency, so the return curve
    reflects performance on capital that was actually at risk.
    """
    q = select(NavDaily).order_by(NavDaily.snapshot_date.asc())
    if account_id:
        q = q.where(NavDaily.account_id == account_id)
    if period and period.start:
        q = q.where(NavDaily.snapshot_date >= period.start)
    if period and period.end:
        q = q.where(NavDaily.snapshot_date <= period.end)
    rows = session.execute(q).scalars().all()

    target_ccy = (display_currency or _ACCOUNT_BASE_CURRENCY).upper()
    convert = target_ccy != _ACCOUNT_BASE_CURRENCY

    if not rows:
        return {
            "series": [],
            "twr_pct": None,
            "annualised_pct": None,
            "n_days": 0,
            "display_currency": target_ccy,
            "warmup_skipped": 0,
        }
    fx_rates = (
        get_fx_rates([row.snapshot_date for row in rows], _ACCOUNT_BASE_CURRENCY, target_ccy)
        if convert
        else {}
    )

    nav_values = [
        (float(row.nav_total) if row.nav_total is not None else 0.0)
        * (fx_rates.get(row.snapshot_date, 1.0) if convert else 1.0)
        for row in rows
    ]
    max_nav = max(nav_values) if nav_values else 0.0
    nav_floor = max(100.0, 0.005 * max_nav)

    # Find the first row whose NAV exceeds the floor; everything before is a
    # warm-up period (account opening, small balance) that would explode the
    # Modified Dietz formula on the first real deposit.
    start_idx = 0
    for i, nav in enumerate(nav_values):
        if nav >= nav_floor:
            start_idx = i
            break
    rows = rows[start_idx:]
    warmup_skipped = start_idx

    series: list[dict] = []
    cumulative = 1.0
    prev_nav: float | None = None
    first_date: date | None = None
    last_date: date | None = None
    pending_cf = 0.0  # cashflows on NAV-less days, carried to the next NAV day
    for row in rows:
        nav_close_raw = float(row.nav_total) if row.nav_total is not None else None
        cf_raw = float(row.external_cashflow or 0)
        if nav_close_raw is None:
            # Cashflow-only row (e.g. deposit settling after the last NAV
            # snapshot): carry the flow to the next day with a NAV so the
            # NAV jump is not booked as performance.
            pending_cf += cf_raw
            continue

        date_str = row.snapshot_date.isoformat()
        fx = fx_rates.get(row.snapshot_date, 1.0) if convert else 1.0
        nav_close = nav_close_raw * fx
        cf = (cf_raw + pending_cf) * fx
        pending_cf = 0.0

        if first_date is None:
            first_date = row.snapshot_date
        last_date = row.snapshot_date
        nav_open = prev_nav
        prev_nav = nav_close

        if nav_open is None or nav_open == 0:
            r_t = 0.0
        else:
            denom = nav_open + 0.5 * cf
            r_t = ((nav_close - nav_open - cf) / denom) if denom else 0.0
        cumulative *= 1 + r_t
        series.append({
            "date": date_str,
            "nav": nav_close,
            "cashflow": cf,
            "daily_return_pct": r_t * 100,
            "cumulative_pct": (cumulative - 1) * 100,
        })

    raw_twr_pct = (cumulative - 1) * 100 if series else None
    n_days = (last_date - first_date).days if first_date and last_date else 0
    raw_annualised: float | None = None
    if n_days > 0 and cumulative > 0:
        raw_annualised = (cumulative ** (365 / n_days) - 1) * 100
    quality = _series_quality([row.snapshot_date for row in rows if row.nav_total is not None])
    twr_pct = raw_twr_pct if quality["is_reliable"] else None
    annualised = raw_annualised if quality["is_reliable"] else None

    return {
        "series": series,
        "twr_pct": twr_pct,
        "annualised_pct": annualised,
        "raw_twr_pct": raw_twr_pct,
        "raw_annualised_pct": raw_annualised,
        "n_days": n_days,
        "from_date": first_date.isoformat() if first_date else None,
        "to_date": last_date.isoformat() if last_date else None,
        "display_currency": target_ccy,
        "quality": quality,
        "warmup_skipped": warmup_skipped,
    }
