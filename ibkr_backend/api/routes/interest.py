"""Margin interest paid / received + accruals."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ...analytics import interest_summary
from ..deps import PeriodDep, SessionDep

router = APIRouter()


@router.get("/summary")
def summary(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
):
    return interest_summary(session, period=period, account_id=account_id)
