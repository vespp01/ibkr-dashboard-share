"""Shared helpers across the analytics package — kept tiny on purpose.

This module centralises low-level utilities that used to drift between
`metrics.py`, `options.py`, `margin.py`, and the per-domain modules
(`dividends_calc.py`, `commissions.py`, `interest.py`):

1. `_f`: Decimal/None → float|None coercion.
2. `_apply_period`: SQLAlchemy period filter.
3. `is_closed` / `is_open`: IBKR Flex marks closes with compound codes
   like `C;IA`, `C;P`, `C;FPA;P`, `C;IM;P`. Strict `open_close == 'C'`
   drops them in real brokerage exports. Use these
   helpers everywhere instead of writing the prefix check inline.
4. `_to_base`: `amount * fx_rate_to_base`, treating null FX as 1.0.
5. `_date_bucket` / `_dialect_is_sqlite`: portable date GROUP-BY across
   SQLite (dev/local) and Postgres (hypothetical future deploy).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db.models import PositionEod, Trade
from .filters import PeriodFilter


def _f(v) -> float | None:
    """Decimal / number → float, preserving None."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _apply_period(query, col, period: PeriodFilter | None):
    """Apply period.start / period.end as `col >= start` / `col <= end`."""
    if period is None:
        return query
    if period.start is not None:
        query = query.where(col >= period.start)
    if period.end is not None:
        query = query.where(col <= period.end)
    return query


def is_closed_filter():
    """SQL expression: ANY `Trade.open_close` starting with 'C' is closed.

    Catches IBKR's compound close codes. Use in `.where(is_closed_filter())`
    instead of `.where(Trade.open_close == "C")`.
    """
    return Trade.open_close.like("C%")


def is_open_filter():
    """SQL expression: NULL or any code NOT starting with 'C'.

    Mirror of `is_closed_filter`; together they partition every Trade row.
    """
    return or_(Trade.open_close.is_(None), ~Trade.open_close.like("C%"))


def is_closed(open_close: str | None) -> bool:
    """Per-row Python predicate — same semantic as `is_closed_filter`."""
    return (open_close or "").startswith("C")


def is_open(open_close: str | None) -> bool:
    """Per-row Python predicate — inverse of `is_closed`."""
    return not is_closed(open_close)


def _to_base(amount, fx):
    """`amount * fx_rate_to_base`, treating null FX as 1.0."""
    return func.coalesce(amount * func.coalesce(fx, 1), 0)


def _dialect_is_sqlite() -> bool:
    """Single source of truth: check the configured engine's dialect."""
    from ..db.session import engine

    return engine.dialect.name == "sqlite"


def _date_bucket(col, bucket: str):
    """Group-by date bucket compatible with both SQLite and Postgres."""
    bucket = (bucket or "month").lower()
    is_sqlite = _dialect_is_sqlite()
    fmt_sqlite = {"year": "%Y", "month": "%Y-%m", "day": "%Y-%m-%d"}
    fmt_pg = {"year": "YYYY", "month": "YYYY-MM", "day": "YYYY-MM-DD"}
    if bucket not in fmt_sqlite:
        raise ValueError(f"Unsupported bucket: {bucket}")
    return (
        func.strftime(fmt_sqlite[bucket], col)
        if is_sqlite
        else func.to_char(col, fmt_pg[bucket])
    )


def _days_stale(snapshot: date | None) -> int | None:
    if snapshot is None:
        return None
    return (date.today() - snapshot).days


def _latest_snapshot_date(session: Session, account_id: str | None) -> date | None:
    q = select(func.max(PositionEod.snapshot_date))
    if account_id:
        q = q.where(PositionEod.account_id == account_id)
    return session.execute(q).scalar()
