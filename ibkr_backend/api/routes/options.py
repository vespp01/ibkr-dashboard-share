"""Options endpoints — per-contract trades, EAE events, premium series, risk."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ...analytics.options import (
    OptionsContractSnapshot,
    options_assignment_rate,
    options_assignment_risk,
    options_concentration,
    options_contract_snapshot,
    options_dte_buckets,
    options_eae_list,
    options_open_positions,
    options_pnl_attribution,
    options_pnl_by_underlying,
    options_premium_timeseries,
    options_summary,
    options_trades_list,
)
from ...config import get_settings
from ...integrations.optionslab import attach_management_signals
from ..deps import PeriodDep, SessionDep

router = APIRouter()


@router.get("/summary", summary="KPI block for the Opciones page header")
def summary(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
):
    return options_summary(session, period=period, account_id=account_id)


@router.get("/trades", summary="OPT trades enriched with OCC fields + win/loss summary")
def trades(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    underlying: str | None = Query(None, description="Filter by underlying ticker"),
    right: str | None = Query(None, pattern="^[CcPp]$", description="C or P"),
    status: str = Query("all", pattern="^(all|open|closed)$"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    return options_trades_list(
        session,
        period=period,
        account_id=account_id,
        underlying=underlying,
        right=right,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/eae", summary="Exercise / Assignment / Expiration events from OptionEAE")
def eae(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    underlying: str | None = Query(None),
):
    return options_eae_list(
        session, period=period, account_id=account_id, underlying=underlying
    )


@router.get(
    "/premium-timeseries",
    summary="Net premium (signed netCash) bucketed by month or year",
)
def premium_timeseries(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    bucket: str = Query("month", pattern="^(month|year)$"),
):
    return options_premium_timeseries(
        session, period=period, account_id=account_id, bucket=bucket
    )


@router.get("/by-underlying", summary="OPT P&L grouped by underlying ticker")
def by_underlying(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
):
    return options_pnl_by_underlying(session, period=period, account_id=account_id)


@router.get(
    "/open-positions",
    summary="Latest open OPT positions, including long calls/puts and shorts",
)
def open_positions(
    session: SessionDep,
    account_id: str | None = Query(None),
):
    payload = options_open_positions(session, account_id=account_id)
    # Per row: OptionsLab management signal, or None (missing/invalid file never fails).
    attach_management_signals(payload["rows"], get_settings().optionslab_signals_path)
    return payload


@router.get(
    "/contract-snapshot",
    response_model=OptionsContractSnapshot,
    summary="Versioned, account-identifier-free open option legs (ibkr-dashboard.options.v1)",
)
def contract_snapshot(session: SessionDep):
    """Expose EOD option legs for OptionsLab without account identifiers, NAV or P&L."""

    return options_contract_snapshot(session)


@router.get(
    "/assignment-risk",
    summary="Open short options vs latest underlying close (ITM/OTM + intrinsic)",
)
def assignment_risk(
    session: SessionDep,
    account_id: str | None = Query(None),
):
    return options_assignment_risk(session, account_id=account_id)


@router.get(
    "/concentration",
    summary="HHI concentration of the open option book by underlying",
)
def concentration(
    session: SessionDep,
    account_id: str | None = Query(None),
):
    return options_concentration(session, account_id=account_id)


@router.get(
    "/dte-buckets",
    summary="Open short OPT positions bucketed by DTE (tastytrade 21-DTE doctrine)",
)
def dte_buckets(
    session: SessionDep,
    account_id: str | None = Query(None),
):
    return options_dte_buckets(session, account_id=account_id)


@router.get(
    "/pnl-attribution",
    summary="P&L decomposition: short-premium edge vs wheel stock vs dividends vs interest",
)
def pnl_attribution(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
):
    return options_pnl_attribution(session, period=period, account_id=account_id)


@router.get(
    "/assignment-rate",
    summary="Per-underlying OPT disposal breakdown (bought-to-close · expired · exercised · assigned)",
)
def assignment_rate(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
):
    return options_assignment_rate(session, period=period, account_id=account_id)
