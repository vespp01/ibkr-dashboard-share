"""Portfolio overview, positions, P&L by asset class."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from ...analytics import (
    cash_balances,
    change_in_nav_summary,
    change_in_nav_timeseries,
    leverage_series,
    margin_interest_summary,
    margin_summary,
    open_shorts,
    pnl_by_asset_class,
    portfolio_overview,
    positions_snapshot,
    realized_pnl_timeseries,
    symbol_attribution,
    total_return_on_cash,
    unrealized_pnl,
)
from ..deps import PeriodDep, SessionDep

router = APIRouter()


@router.get("/overview", summary="KPI grid for the dashboard")
def overview(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
):
    return portfolio_overview(session, period=period, account_id=account_id)


@router.get("/positions", summary="Open positions snapshot")
def positions(
    session: SessionDep,
    snapshot_date: date | None = Query(None),
    account_id: str | None = Query(None),
    asset_class: str | None = Query(None),
):
    return positions_snapshot(
        session,
        snapshot_date=snapshot_date,
        account_id=account_id,
        asset_class=asset_class,
    )


@router.get("/unrealized", summary="Sum of fifoPnlUnrealized at snapshot")
def unrealized(
    session: SessionDep,
    snapshot_date: date | None = Query(None),
    account_id: str | None = Query(None),
):
    return unrealized_pnl(
        session, snapshot_date=snapshot_date, account_id=account_id
    )


@router.get("/realized-timeseries", summary="Realised P&L bucketed by month/year")
def realized_timeseries(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    bucket: str = Query("month", pattern="^(year|month|day)$"),
):
    return realized_pnl_timeseries(
        session, period=period, account_id=account_id, bucket=bucket
    )


@router.get("/pnl-by-asset-class", summary="Realised + unrealised P&L per asset class")
def pnl_asset_class(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    snapshot_date: date | None = Query(None),
):
    return pnl_by_asset_class(
        session,
        period=period,
        account_id=account_id,
        snapshot_date=snapshot_date,
    )


@router.get("/cash", summary="Cash balances by currency")
def cash(
    session: SessionDep,
    snapshot_date: date | None = Query(None),
    account_id: str | None = Query(None),
):
    return cash_balances(
        session, snapshot_date=snapshot_date, account_id=account_id
    )


@router.get("/open-shorts", summary="Open short positions")
def open_shorts_route(
    session: SessionDep,
    snapshot_date: date | None = Query(None),
    account_id: str | None = Query(None),
):
    return open_shorts(
        session, snapshot_date=snapshot_date, account_id=account_id
    )


@router.get(
    "/attribution",
    summary="Performance Attribution (ChangeInNAV waterfall + chained TWR)",
)
def attribution(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
):
    return change_in_nav_summary(session, period=period, account_id=account_id)


@router.get(
    "/attribution-timeseries",
    summary="Daily / monthly / yearly attribution buckets",
)
def attribution_timeseries(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    bucket: str = Query("day", pattern="^(day|month|year)$"),
):
    return change_in_nav_timeseries(
        session, period=period, account_id=account_id, bucket=bucket
    )


@router.get(
    "/margin",
    summary="Margin posture — leverage, cash debt, concentration, Reg-T estimates",
)
def margin(
    session: SessionDep,
    snapshot_date: date | None = Query(None),
    account_id: str | None = Query(None),
    top_n: int = Query(10, ge=1, le=50),
):
    return margin_summary(
        session, snapshot_date=snapshot_date, account_id=account_id, top_n=top_n
    )


@router.get(
    "/margin/interest",
    summary="Interest paid on margin debt + effective APR estimate",
)
def margin_interest(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
):
    return margin_interest_summary(session, period=period, account_id=account_id)


@router.get(
    "/margin/leverage-series",
    summary="Daily leverage (gross/NLV) from NavDaily",
)
def margin_leverage_series(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
):
    return leverage_series(session, period=period, account_id=account_id)


@router.get(
    "/symbol-attribution",
    summary="Per-symbol P&L attribution (FIFO + MTM Underlying)",
)
def symbol_attribution_route(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    asset_class: str | None = Query(
        None,
        pattern="^(STK|OPT|CASH|BOND|FUND|WAR|FUT|CFD|IND)$",
        description="Filter to one IBKR asset category",
    ),
    top_n: int | None = Query(None, ge=1, le=500),
    held_only: bool = Query(
        False,
        description="Only symbols still held as of the latest snapshot "
        "(drops closed/expired positions kept for realized-P&L attribution)",
    ),
):
    """Per-symbol realized + unrealized + MTM attribution over the period.

    Sourced from the IBKR ``FIFOPerformanceSummaryUnderlying`` and
    ``MTMPerformanceSummaryUnderlying`` sections (base currency). Realized
    and MTM fields are summed across snapshots in the period; unrealized
    is the latest snapshot inside the period, and only counts for positions
    still held as of the newest snapshot (a closed position's stale unrealized
    is dropped). Pass ``held_only=true`` to also hide closed positions entirely.
    """
    return symbol_attribution(
        session,
        period=period,
        account_id=account_id,
        asset_class=asset_class,
        top_n=top_n,
        held_only=held_only,
    )


@router.get(
    "/all-time-return",
    summary="Honest absolute return on money invested (NAV vs cumulative deposits)",
)
def all_time_return(
    session: SessionDep,
    account_id: str | None = Query(None),
):
    """Use this when the user asks 'what did I make on my account since
    inception?'. It's NOT a TWR — deposits aren't isolated — but it's the
    correct headline number that doesn't need a dense daily NAV series.
    """
    return total_return_on_cash(session, account_id=account_id)
