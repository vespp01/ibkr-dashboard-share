"""Dividends — gross / net / withholding / by country / time series."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ...analytics import (
    dividends_by_country,
    dividends_summary,
    dividends_timeseries,
    upcoming_dividends,
    withholding_tax_by_country,
)
from ..deps import PeriodDep, SessionDep

router = APIRouter()


@router.get("/summary")
def summary(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
):
    return dividends_summary(session, period=period, account_id=account_id)


@router.get("/timeseries")
def timeseries(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    bucket: str = Query("month", pattern="^(year|month|day)$"),
):
    return dividends_timeseries(
        session, period=period, account_id=account_id, bucket=bucket
    )


@router.get("/by-country")
def by_country(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
):
    return dividends_by_country(session, period=period, account_id=account_id)


@router.get("/withholding-by-country")
def withholding_by_country(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
):
    return withholding_tax_by_country(
        session, period=period, account_id=account_id
    )


@router.get("/upcoming")
def upcoming(
    session: SessionDep,
    account_id: str | None = Query(None),
):
    """Open Flex accruals paying on/after the data date (forward-looking)."""
    return upcoming_dividends(session, account_id=account_id)
