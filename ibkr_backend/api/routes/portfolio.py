"""Portfolio overview, positions, P&L by asset class."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from ...analytics import (
    change_in_nav_summary,
    change_in_nav_timeseries,
    leverage_series,
    margin_interest_summary,
    margin_summary,
    open_shorts,
    portfolio_overview,
    positions_snapshot,
    realized_pnl_timeseries,
    symbol_attribution,
    total_return_on_cash,
)
from ...integrations.investmentlab import (
    InvestmentlabPortfolioSnapshot,
    build_investmentlab_portfolio_snapshot,
)
from ..deps import PeriodDep, SessionDep
from ..payload import ACCOUNT_KEYS, NULL_ONLY_KEYS, strip_keys

router = APIRouter()


@router.get(
    "/investmentlab-snapshot",
    response_model=InvestmentlabPortfolioSnapshot,
    summary="Versioned, account-identifier-free portfolio context",
)
def investmentlab_snapshot(session: SessionDep):
    """Expose EOD holdings context without credentials or account identifiers."""

    return build_investmentlab_portfolio_snapshot(session)


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
    return strip_keys(
        positions_snapshot(
            session,
            snapshot_date=snapshot_date,
            account_id=account_id,
            asset_class=asset_class,
        ),
        ACCOUNT_KEYS | NULL_ONLY_KEYS,
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


@router.get("/open-shorts", summary="Open short positions")
def open_shorts_route(
    session: SessionDep,
    snapshot_date: date | None = Query(None),
    account_id: str | None = Query(None),
):
    return strip_keys(
        open_shorts(session, snapshot_date=snapshot_date, account_id=account_id),
        NULL_ONLY_KEYS,
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
    "/margin/live",
    summary="Account-identifier-free live IBKR margin posture",
)
def margin_live():
    """Return live margin values and policy band without exposing account ids."""
    from ...config import get_settings
    from ...ibgw.live import LiveClient, LiveUnavailable
    from ...notify.margin_alerts import build_live_snapshot, evaluate_margin

    settings = get_settings()
    try:
        with LiveClient(purpose="portfolio") as live:
            snapshot = build_live_snapshot(live.account_summary())
    except LiveUnavailable as exc:
        return {
            "status": "unavailable",
            "source": "ibkr_live",
            "level": "unavailable",
            "message": str(exc),
        }
    return evaluate_margin(snapshot, settings=settings)


@router.get(
    "/margin/financing",
    summary="IBKR interest and historical CHF replay for margin funding currencies",
)
def margin_financing(session: SessionDep):
    """Return a fail-closed, informational currency comparison for cash debt."""
    from ...notify.margin_financing import build_margin_financing_analysis

    return build_margin_financing_analysis(session)


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
    return strip_keys(
        symbol_attribution(
            session,
            period=period,
            account_id=account_id,
            asset_class=asset_class,
            top_n=top_n,
            held_only=held_only,
        ),
        ACCOUNT_KEYS | NULL_ONLY_KEYS,
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
