"""Commissions and regulatory transaction fees."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ...analytics import (
    commissions_breakdown,
    commissions_summary,
    commissions_timeseries,
)
from ..deps import PeriodDep, SessionDep

router = APIRouter()


@router.get("/summary")
def summary(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    asset_class: str | None = Query(None),
):
    return commissions_summary(
        session, period=period, account_id=account_id, asset_class=asset_class
    )


@router.get("/timeseries")
def timeseries(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    bucket: str = Query("month", pattern="^(year|month|day)$"),
):
    return commissions_timeseries(
        session, period=period, account_id=account_id, bucket=bucket
    )


@router.get("/breakdown")
def breakdown(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
):
    """Commission total split by asset class AND by fee type
    (broker commission · transaction tax · regulatory fees).
    """
    return commissions_breakdown(
        session, period=period, account_id=account_id
    )
