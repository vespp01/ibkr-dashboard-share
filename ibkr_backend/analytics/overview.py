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
    ChangeInNavDaily,
    NavDaily,
)
from ._common import (
    _apply_period,
    _date_bucket,
    _days_stale,
    _f,
    _latest_snapshot_date,
)
from .commissions import commissions_summary
from .dividends_calc import dividends_summary, income_projection
from .filters import PeriodFilter
from .flows import CIN_FLOW
from .positions import _position_consistency, unrealized_pnl

# ChangeInNAV waterfall: payload key -> ORM column. Together with the two
# external-flow lines they close `ending - starting` exactly (verified on all
# 751 live days, residual < 1e-9 of ending value). `realized` and
# `change_in_unrealized` are NOT here: IBKR leaves them 0 on every row (the
# P&L is already inside `mtm`), so summing them would double count if it ever
# filled both. The columns and `raw_xml` keep them.
_PERFORMANCE_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("mtm", "mtm"),
    ("dividends", "dividends"),
    ("withholding_tax", "withholding_tax"),
    ("change_in_dividend_accruals", "change_in_dividend_accruals"),
    ("interest", "interest"),
    ("change_in_interest_accruals", "change_in_interest_accruals"),
    ("commissions", "commissions"),
    ("broker_fees", "broker_fees"),
    ("other_fees", "other_fees"),
    ("transaction_tax", "transaction_tax"),
    ("sales_tax", "sales_tax"),
    ("fx_translation", "fx_translation"),
)
_FLOW_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("deposits_withdrawals", "deposits_withdrawals"),
    ("asset_transfers", "asset_transfers"),
)
_WATERFALL_COMPONENTS = _PERFORMANCE_COMPONENTS + _FLOW_COMPONENTS


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
    """The slim KPI payload the dashboard shell reads (nav, freshness,
    unrealized, dividends, commissions, income projection)."""
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
    unr = unrealized_pnl(session, snapshot_date=snapshot, account_id=account_id)
    consistency = _position_consistency(session, snapshot, account_id)
    income = income_projection(session, snapshot_date=snapshot, account_id=account_id)

    return {
        "as_of": snapshot.isoformat() if snapshot else None,
        "nav_base": _f(nav_row.nav_total) if nav_row else None,
        "base_currency": nav_row.base_currency if nav_row else None,
        "freshness": {
            "positions_as_of": snapshot.isoformat() if snapshot else None,
            "positions_days_stale": _days_stale(snapshot),
            "nav_as_of": nav_row.snapshot_date.isoformat() if nav_row else None,
            "nav_days_stale": _days_stale(nav_row.snapshot_date) if nav_row else None,
        },
        "positions_consistency": consistency,
        "dividends": div,
        "commissions": com,
        "income_projection": {
            k: v for k, v in income.items() if k != "equity_market_value_base"
        },
        # cost_base is read by nothing; the shell shows value + P&L only.
        "unrealized": {k: v for k, v in unr.items() if k != "cost_base"},
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

    Values are already in the account base currency — no FX needed. The
    waterfall is residual-free: `starting + Σ components = ending` (the
    leftover is reported as `residual_base` / `residual_pct_of_ending`).
    `twr_compound_pct` is the time-chained product of daily `twr` percentages.
    """
    sums = [
        func.coalesce(func.sum(getattr(ChangeInNavDaily, col)), 0).label(key)
        for key, col in _WATERFALL_COMPONENTS
    ]
    q = select(
        func.min(ChangeInNavDaily.snapshot_date).label("first"),
        func.max(ChangeInNavDaily.snapshot_date).label("last"),
        func.count().label("days"),
        *sums,
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

    attribution = {key: _f(getattr(row, key)) for key, _ in _WATERFALL_COMPONENTS}
    residual = ending_value - starting_value - sum(attribution.values())
    return {
        "period": {
            "first_date": row.first.isoformat() if row.first else None,
            "last_date": row.last.isoformat() if row.last else None,
            "days": int(row.days or 0),
        },
        "starting_value": starting_value,
        "ending_value": ending_value,
        "attribution_base": attribution,
        "performance_total_base": sum(attribution[k] for k, _ in _PERFORMANCE_COMPONENTS),
        "residual_base": residual,
        "residual_pct_of_ending": (abs(residual) / abs(ending_value) * 100) if ending_value else None,
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

    Every bucket carries all waterfall components, `total_return_base` (the
    non-flow components summed — what the monthly Total Return matrix shows)
    and, per day, `residual` (= ending - starting - Σ components, ~0).
    For month / year, sums each component within the bucket.
    """
    bucket = (bucket or "day").lower()
    comps = [key for key, _ in _WATERFALL_COMPONENTS]

    def _payload(r, label: str) -> dict:
        out = {"bucket": label}
        for key, _ in _WATERFALL_COMPONENTS:
            out[key] = _f(getattr(r, key))
        out["total_return_base"] = sum(out[k] for k, _ in _PERFORMANCE_COMPONENTS)
        return out

    if bucket == "day":
        q = (
            select(
                ChangeInNavDaily.snapshot_date,
                ChangeInNavDaily.starting_value,
                ChangeInNavDaily.ending_value,
                ChangeInNavDaily.twr,
                *[getattr(ChangeInNavDaily, col).label(key) for key, col in _WATERFALL_COMPONENTS],
            )
            .order_by(ChangeInNavDaily.snapshot_date.asc())
        )
        q = _apply_period(q, ChangeInNavDaily.snapshot_date, period)
        if account_id:
            q = q.where(ChangeInNavDaily.account_id == account_id)
        out_rows = []
        for r in session.execute(q):
            item = _payload(r, r.snapshot_date.isoformat())
            item["starting_value"] = _f(r.starting_value)
            item["ending_value"] = _f(r.ending_value)
            item["twr"] = _f(r.twr)
            item["residual"] = (
                item["ending_value"] - item["starting_value"]
                - sum(item[k] for k in comps)
            )
            out_rows.append(item)
        return out_rows

    label = _date_bucket(ChangeInNavDaily.snapshot_date, bucket).label("bucket")
    q = (
        select(
            label,
            *[
                func.coalesce(func.sum(getattr(ChangeInNavDaily, col)), 0).label(key)
                for key, col in _WATERFALL_COMPONENTS
            ],
        )
        .group_by(label)
        .order_by(label.asc())
    )
    q = _apply_period(q, ChangeInNavDaily.snapshot_date, period)
    if account_id:
        q = q.where(ChangeInNavDaily.account_id == account_id)
    return [_payload(r, r.bucket) for r in session.execute(q)]


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
    """Absolute return computed from external flows + current NAV.

    Inputs:
      * cumulative_net_deposits = Σ ChangeInNAV `depositsWithdrawals +
        assetTransfers` (the single flow definition, `flows.py`) — in-kind
        transfers count as contributions, not profit.
      * current_nav = latest NavDaily.nav_total

    Outputs:
      * absolute_profit_base = current_nav − net_deposits
      * absolute_return_pct = profit / net_deposits × 100
      * inception_date, years_elapsed
      * xirr_pct = all-time money-weighted annual return (the same figure as
        /performance/xirr). It replaces the former `cagr_pct`, which assumed
        every deposit was made at inception.

    This is NOT a time-weighted return — deposits made yesterday weigh the
    same as deposits made years ago. Use TWR for like-for-like manager
    comparison; use this for "what did I make on the money I put in".
    """
    from .xirr import xirr_summary

    flow_q = select(
        func.coalesce(func.sum(CIN_FLOW), 0),
        func.min(ChangeInNavDaily.snapshot_date),
    )
    if account_id:
        flow_q = flow_q.where(ChangeInNavDaily.account_id == account_id)
    net_deposits_base, first_flow_date = session.execute(flow_q).first() or (0, None)
    net_deposits_base = float(net_deposits_base or 0)

    # Skip NAV-less rows (nav_total NULL) so they cannot zero out current_nav.
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

    # Inception = whichever is earlier of (first flow, first NAV snapshot).
    inception: date | None = None
    candidates = [d for d in (first_flow_date, nav_first_row.snapshot_date if nav_first_row else None) if d]
    if candidates:
        inception = min(candidates)

    profit = current_nav - net_deposits_base
    abs_return_pct = (profit / net_deposits_base * 100) if net_deposits_base else None

    years = None
    if inception:
        years = (nav_row.snapshot_date - inception).days / 365.25
    xirr = xirr_summary(session, account_id=account_id)

    return {
        "is_reliable": True,
        "inception_date": inception.isoformat() if inception else None,
        "as_of": nav_row.snapshot_date.isoformat(),
        "current_nav_base": current_nav,
        "cumulative_net_deposits_base": net_deposits_base,
        "absolute_profit_base": profit,
        "absolute_return_pct": abs_return_pct,
        "years_elapsed": years,
        "xirr_pct": xirr.get("xirr_pct"),
        "note": "Absolute return on money invested. Not TWR — deposits are not isolated. Use for the 'what did I make?' question; use /performance/twr for time-weighted manager comparison.",
    }
