"""Per-trade list endpoint with filtering + win/loss summary."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ...analytics import executions_list, trades_list
from ..deps import PeriodDep, SessionDep

router = APIRouter()


@router.get("", summary="Per-trade list with optional filters + summary stats")
def list_trades(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    symbol: str | None = Query(None),
    asset_class: str | None = Query(None),
    status: str = Query("all", pattern="^(all|open|closed)$"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    return trades_list(
        session,
        period=period,
        account_id=account_id,
        symbol=symbol,
        asset_class=asset_class,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/executions",
    summary="Per-fill Trade Confirmation rows with lag + commission breakdown",
)
def list_executions(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    symbol: str | None = Query(None),
    asset_class: str | None = Query(None),
    order_id: str | None = Query(None, description="Exact order ID for drilldown"),
    multi_fill_only: bool = Query(
        False, description="Only return executions whose order filled in >1 slice"
    ),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    return executions_list(
        session,
        period=period,
        account_id=account_id,
        symbol=symbol,
        asset_class=asset_class,
        order_id=order_id,
        multi_fill_only=multi_fill_only,
        limit=limit,
        offset=offset,
    )
