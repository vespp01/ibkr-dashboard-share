"""FastAPI dependencies — DB session + period filter from query params."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from ..analytics.filters import PeriodFilter, resolve_period
from ..db import get_session


def db_session() -> Session:
    """Wrapper so route signatures depend on a stable name."""
    yield from get_session()


def period_filter(
    period: Annotated[str | None, Query(description="ytd | mtd | qtd | 1m | 3m | 6m | 1y | 3y | 5y | all")] = None,
    year: Annotated[int | None, Query(description="Calendar year")] = None,
    month: Annotated[int | None, Query(ge=1, le=12)] = None,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
) -> PeriodFilter:
    return resolve_period(
        period=period, year=year, month=month,
        from_date=from_date, to_date=to_date,
    )


SessionDep = Annotated[Session, Depends(db_session)]
PeriodDep = Annotated[PeriodFilter, Depends(period_filter)]
