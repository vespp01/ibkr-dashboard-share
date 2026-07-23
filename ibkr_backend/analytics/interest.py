"""Interest analytics — broker margin interest paid + cash interest received.

Extracted from `metrics.py` for cohesion. Source: `CashTransaction.type`
in `INTEREST_PAID_TYPES + INTEREST_RECV_TYPES`, plus the per-day
`InterestAccrual` table (Cash Transactions only post once per month).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import CashTransaction, InterestAccrual
from ._common import _apply_period, _f, _to_base
from .filters import PeriodFilter


INTEREST_PAID_TYPES = ("Broker Interest Paid", "Bond Interest Paid")
INTEREST_RECV_TYPES = ("Broker Interest Received", "Bond Interest Received")


def interest_summary(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> dict:
    base = _to_base(CashTransaction.amount, CashTransaction.fx_rate_to_base)
    q = select(
        CashTransaction.type,
        func.coalesce(func.sum(base), 0).label("total"),
    ).group_by(CashTransaction.type)
    q = _apply_period(q, CashTransaction.settle_date, period)
    if account_id:
        q = q.where(CashTransaction.account_id == account_id)
    q = q.where(
        CashTransaction.type.in_(INTEREST_PAID_TYPES + INTEREST_RECV_TYPES)
    )
    by_type = {r.type: _f(r.total) for r in session.execute(q)}
    paid = sum(by_type.get(t, 0) or 0 for t in INTEREST_PAID_TYPES)
    received = sum(by_type.get(t, 0) or 0 for t in INTEREST_RECV_TYPES)

    # Also pull the daily accruals so the user gets sub-monthly granularity.
    accr_q = select(
        InterestAccrual.interest_type,
        func.coalesce(func.sum(InterestAccrual.interest), 0).label("total"),
    ).group_by(InterestAccrual.interest_type)
    accr_q = _apply_period(accr_q, InterestAccrual.accrual_date, period)
    if account_id:
        accr_q = accr_q.where(InterestAccrual.account_id == account_id)
    accruals = {r.interest_type or "OTHER": _f(r.total) for r in session.execute(accr_q)}

    return {
        "paid_base": paid,
        "received_base": received,
        "net_base": paid + received,
        "by_type": by_type,
        "accruals_by_type": accruals,
    }


__all__ = [
    "INTEREST_PAID_TYPES",
    "INTEREST_RECV_TYPES",
    "interest_summary",
]
