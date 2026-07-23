"""Portfolio overview — KPI grid, cash balances, ChangeInNAV attribution.

Extracted from `metrics.py` (god-module split — same pattern as
`dividends_calc` / `commissions` / `interest`). Code moved verbatim;
`metrics.py` re-exports every name for backward compat.

`portfolio_overview` is the aggregator: it composes the per-domain
summaries (dividends, commissions, interest, fees, unrealized) into the
single KPI payload the dashboard renders at the top.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import (
    CashReportRow,
    CashTransaction,
    ChangeInNavDaily,
    NavDaily,
    Trade,
)
from ._common import (
    _apply_period,
    _date_bucket,
    _days_stale,
    _f,
    _latest_snapshot_date,
    _to_base,
)
from .commissions import commissions_summary, transaction_fees_summary
from .dividends_calc import dividends_summary, income_projection
from .filters import PeriodFilter
from .interest import interest_summary
from .positions import _position_consistency, unrealized_pnl

DEPOSIT_TYPES = ("Deposits/Withdrawals", "Internal Transfers")


# ====================================================================
# CASH BALANCES & PORTFOLIO OVERVIEW
# ====================================================================
def cash_balances(
    session: Session,
    *,
    snapshot_date: date | None = None,
    account_id: str | None = None,
) -> list[dict]:
    q = select(CashReportRow)
    if snapshot_date:
        q = q.where(CashReportRow.snapshot_date == snapshot_date)
    else:
        sub = select(func.max(CashReportRow.snapshot_date))
        if account_id:
            sub = sub.where(CashReportRow.account_id == account_id)
        latest = session.execute(sub).scalar()
        if latest is None:
            return []
        q = q.where(CashReportRow.snapshot_date == latest)
    if account_id:
        q = q.where(CashReportRow.account_id == account_id)
    rows = session.execute(q).scalars().all()
    return [
        {
            "snapshot_date": r.snapshot_date.isoformat(),
            "account_id": r.account_id,
            "currency": r.currency,
            "ending_cash": _f(r.ending_cash),
            "ending_settled_cash": _f(r.ending_settled_cash),
            "dividends": _f(r.dividends),
            "withholding_tax": _f(r.withholding_tax),
            "broker_interest": _f(r.broker_interest),
            "commissions": _f(r.commissions),
            "deposits_withdrawals": _f(r.deposits_withdrawals),
        }
        for r in rows
    ]


def portfolio_overview(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> dict:
    """The single 'KPI grid' the dashboard renders at the top."""
    snapshot = _latest_snapshot_date(session, account_id)
    nav_q = (
        select(NavDaily.nav_total, NavDaily.snapshot_date, NavDaily.base_currency)
        .where(NavDaily.nav_total.is_not(None))  # skip cashflow-only rows
        .order_by(NavDaily.snapshot_date.desc())
        .limit(1)
    )
    if account_id:
        nav_q = nav_q.where(NavDaily.account_id == account_id)
    nav_row = session.execute(nav_q).first()

    div = dividends_summary(session, period=period, account_id=account_id)
    com = commissions_summary(session, period=period, account_id=account_id)
    intr = interest_summary(session, period=period, account_id=account_id)
    fees = transaction_fees_summary(session, period=period, account_id=account_id)
    unr = unrealized_pnl(session, snapshot_date=snapshot, account_id=account_id)
    consistency = _position_consistency(session, snapshot, account_id)
    income = income_projection(session, snapshot_date=snapshot, account_id=account_id)

    realized_q = select(
        func.coalesce(
            func.sum(_to_base(Trade.fifo_pnl_realized, Trade.fx_rate_to_base)), 0
        )
    )
    realized_q = _apply_period(realized_q, Trade.trade_date, period)
    if account_id:
        realized_q = realized_q.where(Trade.account_id == account_id)
    realized = _f(session.execute(realized_q).scalar())

    deposits_q = select(
        func.coalesce(
            func.sum(_to_base(CashTransaction.amount, CashTransaction.fx_rate_to_base)),
            0,
        )
    ).where(CashTransaction.type.in_(DEPOSIT_TYPES))
    deposits_q = _apply_period(deposits_q, CashTransaction.settle_date, period)
    if account_id:
        deposits_q = deposits_q.where(CashTransaction.account_id == account_id)
    deposits = _f(session.execute(deposits_q).scalar())

    return {
        "as_of": snapshot.isoformat() if snapshot else None,
        "nav_base": _f(nav_row.nav_total) if nav_row else None,
        "nav_date": nav_row.snapshot_date.isoformat() if nav_row else None,
        "base_currency": nav_row.base_currency if nav_row else None,
        "freshness": {
            "positions_as_of": snapshot.isoformat() if snapshot else None,
            "positions_days_stale": _days_stale(snapshot),
            "nav_as_of": nav_row.snapshot_date.isoformat() if nav_row else None,
            "nav_days_stale": _days_stale(nav_row.snapshot_date) if nav_row else None,
        },
        "positions_consistency": consistency,
        "deposits_withdrawals_base": deposits,
        "dividends": div,
        "commissions": com,
        "interest": intr,
        "transaction_fees": fees,
        "income_projection": income,
        "realized_pnl_base": realized,
        "unrealized": unr,
        "total_pnl_base": (realized or 0) + (unr["unrealized_pnl_base"] or 0)
        + (div["net_base"] or 0) + (com["total_base"] or 0)
        + (intr["net_base"] or 0) + (fees["total_base"] or 0),
        "period": period.to_dict() if period else None,
    }


# ====================================================================
# PERFORMANCE ATTRIBUTION (ChangeInNAV)
# ====================================================================
def change_in_nav_summary(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> dict:
    """Sum every ChangeInNAV waterfall component over the filter window.

    Values are already in the account base currency — no FX needed.
    The `twr_compound` is the time-chained product of daily `twr` percentages.
    """
    q = select(
        func.min(ChangeInNavDaily.snapshot_date).label("first"),
        func.max(ChangeInNavDaily.snapshot_date).label("last"),
        func.count().label("days"),
        func.coalesce(func.sum(ChangeInNavDaily.mtm), 0).label("mtm"),
        func.coalesce(func.sum(ChangeInNavDaily.realized), 0).label("realized"),
        func.coalesce(func.sum(ChangeInNavDaily.change_in_unrealized), 0).label("change_in_unrealized"),
        func.coalesce(func.sum(ChangeInNavDaily.dividends), 0).label("dividends"),
        func.coalesce(func.sum(ChangeInNavDaily.withholding_tax), 0).label("withholding_tax"),
        func.coalesce(func.sum(ChangeInNavDaily.interest), 0).label("interest"),
        func.coalesce(func.sum(ChangeInNavDaily.commissions), 0).label("commissions"),
        func.coalesce(func.sum(ChangeInNavDaily.broker_fees), 0).label("broker_fees"),
        func.coalesce(func.sum(ChangeInNavDaily.other_fees), 0).label("other_fees"),
        func.coalesce(func.sum(ChangeInNavDaily.transaction_tax), 0).label("transaction_tax"),
        func.coalesce(func.sum(ChangeInNavDaily.deposits_withdrawals), 0).label("deposits_withdrawals"),
        func.coalesce(func.sum(ChangeInNavDaily.fx_translation), 0).label("fx_translation"),
    )
    q = _apply_period(q, ChangeInNavDaily.snapshot_date, period)
    if account_id:
        q = q.where(ChangeInNavDaily.account_id == account_id)
    row = session.execute(q).one()

    start_q = select(ChangeInNavDaily.starting_value).order_by(
        ChangeInNavDaily.snapshot_date.asc()
    ).limit(1)
    end_q = select(ChangeInNavDaily.ending_value).order_by(
        ChangeInNavDaily.snapshot_date.desc()
    ).limit(1)
    start_q = _apply_period(start_q, ChangeInNavDaily.snapshot_date, period)
    end_q = _apply_period(end_q, ChangeInNavDaily.snapshot_date, period)
    if account_id:
        start_q = start_q.where(ChangeInNavDaily.account_id == account_id)
        end_q = end_q.where(ChangeInNavDaily.account_id == account_id)
    starting_value = _f(session.execute(start_q).scalar())
    ending_value = _f(session.execute(end_q).scalar())

    # Chained TWR: ∏(1 + twr_daily/100) - 1, expressed as a percentage.
    twr_q = select(ChangeInNavDaily.twr).order_by(ChangeInNavDaily.snapshot_date.asc())
    twr_q = _apply_period(twr_q, ChangeInNavDaily.snapshot_date, period)
    if account_id:
        twr_q = twr_q.where(ChangeInNavDaily.account_id == account_id)
    factor = Decimal("1")
    daily_twrs = [t for (t,) in session.execute(twr_q) if t is not None]
    for t in daily_twrs:
        factor *= (Decimal("1") + (Decimal(t) / Decimal("100")))
    twr_compound = float((factor - Decimal("1")) * Decimal("100")) if daily_twrs else None

    return {
        "period": {
            "first_date": row.first.isoformat() if row.first else None,
            "last_date": row.last.isoformat() if row.last else None,
            "days": int(row.days or 0),
        },
        "starting_value": starting_value,
        "ending_value": ending_value,
        "attribution_base": {
            "mtm": _f(row.mtm),
            "realized": _f(row.realized),
            "change_in_unrealized": _f(row.change_in_unrealized),
            "dividends": _f(row.dividends),
            "withholding_tax": _f(row.withholding_tax),
            "interest": _f(row.interest),
            "commissions": _f(row.commissions),
            "broker_fees": _f(row.broker_fees),
            "other_fees": _f(row.other_fees),
            "transaction_tax": _f(row.transaction_tax),
            "deposits_withdrawals": _f(row.deposits_withdrawals),
            "fx_translation": _f(row.fx_translation),
        },
        "twr_compound_pct": twr_compound,
    }


def change_in_nav_timeseries(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    bucket: str = "day",
) -> list[dict]:
    """Daily / monthly / yearly buckets of the attribution waterfall.

    For day bucket, returns one row per snapshot_date.
    For month / year, sums each component within the bucket (TWR is chained).
    """
    bucket = (bucket or "day").lower()
    if bucket == "day":
        q = (
            select(
                ChangeInNavDaily.snapshot_date,
                ChangeInNavDaily.mtm,
                ChangeInNavDaily.realized,
                ChangeInNavDaily.dividends,
                ChangeInNavDaily.withholding_tax,
                ChangeInNavDaily.interest,
                ChangeInNavDaily.commissions,
                ChangeInNavDaily.transaction_tax,
                ChangeInNavDaily.fx_translation,
                ChangeInNavDaily.starting_value,
                ChangeInNavDaily.ending_value,
                ChangeInNavDaily.twr,
            )
            .order_by(ChangeInNavDaily.snapshot_date.asc())
        )
        q = _apply_period(q, ChangeInNavDaily.snapshot_date, period)
        if account_id:
            q = q.where(ChangeInNavDaily.account_id == account_id)
        return [
            {
                "bucket": r.snapshot_date.isoformat(),
                "mtm": _f(r.mtm),
                "realized": _f(r.realized),
                "dividends": _f(r.dividends),
                "withholding_tax": _f(r.withholding_tax),
                "interest": _f(r.interest),
                "commissions": _f(r.commissions),
                "transaction_tax": _f(r.transaction_tax),
                "fx_translation": _f(r.fx_translation),
                "starting_value": _f(r.starting_value),
                "ending_value": _f(r.ending_value),
                "twr": _f(r.twr),
            }
            for r in session.execute(q)
        ]

    label = _date_bucket(ChangeInNavDaily.snapshot_date, bucket).label("bucket")
    q = (
        select(
            label,
            func.coalesce(func.sum(ChangeInNavDaily.mtm), 0).label("mtm"),
            func.coalesce(func.sum(ChangeInNavDaily.realized), 0).label("realized"),
            func.coalesce(func.sum(ChangeInNavDaily.dividends), 0).label("dividends"),
            func.coalesce(func.sum(ChangeInNavDaily.withholding_tax), 0).label("withholding_tax"),
            func.coalesce(func.sum(ChangeInNavDaily.interest), 0).label("interest"),
            func.coalesce(func.sum(ChangeInNavDaily.commissions), 0).label("commissions"),
            func.coalesce(func.sum(ChangeInNavDaily.transaction_tax), 0).label("transaction_tax"),
            func.coalesce(func.sum(ChangeInNavDaily.fx_translation), 0).label("fx_translation"),
        )
        .group_by(label)
        .order_by(label.asc())
    )
    q = _apply_period(q, ChangeInNavDaily.snapshot_date, period)
    if account_id:
        q = q.where(ChangeInNavDaily.account_id == account_id)
    return [
        {
            "bucket": r.bucket,
            "mtm": _f(r.mtm),
            "realized": _f(r.realized),
            "dividends": _f(r.dividends),
            "withholding_tax": _f(r.withholding_tax),
            "interest": _f(r.interest),
            "commissions": _f(r.commissions),
            "transaction_tax": _f(r.transaction_tax),
            "fx_translation": _f(r.fx_translation),
        }
        for r in session.execute(q)
    ]


# ====================================================================
# All-time return on cash — the "honest" total-return number that doesn't
# need a dense daily TWR series. Useful when ChangeInNAV / NAV-daily
# coverage is too sparse for Modified-Dietz TWR in older account history.
# ====================================================================
def total_return_on_cash(
    session: Session,
    *,
    account_id: str | None = None,
) -> dict:
    """Absolute return computed from cashflows + current NAV.

    Inputs:
      * cumulative_net_deposits = sum(CashTransaction.amount × fx_to_base) over
        type in {Deposits/Withdrawals, Internal Transfers}
      * current_nav = latest NavDaily.nav_total

    Outputs:
      * absolute_profit_base = current_nav − net_deposits
      * absolute_return_pct = profit / net_deposits × 100
      * inception_date = earliest transaction (or first NavDaily snapshot)
      * years_elapsed
      * cagr_pct = (nav / net_deposits) ** (1/years) − 1, when years ≥ 0.5
                   and net_deposits > 0

    This is NOT a time-weighted return — deposits made yesterday weigh the
    same as deposits made years ago. Use TWR for like-for-like manager
    comparison; use this for "what did I make on the money I put in".
    """
    dep_q = select(
        func.coalesce(
            func.sum(CashTransaction.amount * func.coalesce(CashTransaction.fx_rate_to_base, 1)),
            0,
        ),
        func.min(CashTransaction.settle_date),
    ).where(CashTransaction.type.in_(DEPOSIT_TYPES))
    if account_id:
        dep_q = dep_q.where(CashTransaction.account_id == account_id)
    net_deposits_base, first_dep_date = session.execute(dep_q).first() or (0, None)
    net_deposits_base = float(net_deposits_base or 0)

    # Skip cashflow-only rows (nav_total NULL) — a deposit settling after the
    # last NAV snapshot must not zero out current_nav.
    nav_q = (
        select(NavDaily)
        .where(NavDaily.nav_total.is_not(None))
        .order_by(NavDaily.snapshot_date.desc())
    )
    if account_id:
        nav_q = nav_q.where(NavDaily.account_id == account_id)
    nav_row = session.execute(nav_q.limit(1)).scalars().first()
    nav_first_q = (
        select(NavDaily)
        .where(NavDaily.nav_total.is_not(None))
        .order_by(NavDaily.snapshot_date.asc())
    )
    if account_id:
        nav_first_q = nav_first_q.where(NavDaily.account_id == account_id)
    nav_first_row = session.execute(nav_first_q.limit(1)).scalars().first()

    if nav_row is None:
        return {
            "is_reliable": False,
            "reason": "no NAV snapshots",
        }
    current_nav = float(nav_row.nav_total or 0)

    # Inception = whichever is earlier of (first deposit, first NAV snapshot).
    inception: date | None = None
    candidates = [d for d in (first_dep_date, nav_first_row.snapshot_date if nav_first_row else None) if d]
    if candidates:
        inception = min(candidates)

    profit = current_nav - net_deposits_base
    abs_return_pct = (profit / net_deposits_base * 100) if net_deposits_base else None

    years = None
    cagr_pct = None
    if inception:
        years = (nav_row.snapshot_date - inception).days / 365.25
        if years >= 0.5 and net_deposits_base > 0 and current_nav > 0:
            cagr_pct = ((current_nav / net_deposits_base) ** (1 / years) - 1) * 100

    return {
        "is_reliable": True,
        "inception_date": inception.isoformat() if inception else None,
        "as_of": nav_row.snapshot_date.isoformat(),
        "current_nav_base": current_nav,
        "cumulative_net_deposits_base": net_deposits_base,
        "absolute_profit_base": profit,
        "absolute_return_pct": abs_return_pct,
        "years_elapsed": years,
        "cagr_pct": cagr_pct,
        "note": "Absolute return on money invested. Not TWR — deposits are not isolated. Use for the 'what did I make?' question; use /performance/twr for time-weighted manager comparison.",
    }
