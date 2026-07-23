"""Commission + transaction-fee analytics.

Extracted from `metrics.py` for cohesion. Commissions live on the
`Trade.ib_commission` column; transaction fees on a separate
`TransactionFee` table. The breakdown function combines both so the
"total operational cost" KPI is honest.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Trade, TransactionFee
from ._common import _apply_period, _date_bucket, _f, _to_base
from .filters import PeriodFilter


def commissions_summary(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    asset_class: str | None = None,
) -> dict:
    """Total commissions paid + per-trade aggregates, in base currency.

    Field source: Trades.ib_commission, multiplied by fxRateToBase. Already
    netted into Trades.net_cash, but we report the gross commission so users
    see what they actually paid.
    """
    base = _to_base(Trade.ib_commission, Trade.fx_rate_to_base)
    q = select(
        func.coalesce(func.sum(base), 0).label("total"),
        func.count(Trade.trade_id).label("trade_count"),
        func.coalesce(func.avg(base), 0).label("avg_per_trade"),
        func.min(Trade.trade_date).label("min_date"),
        func.max(Trade.trade_date).label("max_date"),
    )
    q = _apply_period(q, Trade.trade_date, period)
    if account_id:
        q = q.where(Trade.account_id == account_id)
    if asset_class:
        q = q.where(Trade.asset_class == asset_class)
    row = session.execute(q).one()
    return {
        "total_base": _f(row.total),
        "trade_count": row.trade_count,
        "avg_per_trade_base": _f(row.avg_per_trade),
        "min_date": row.min_date.isoformat() if row.min_date else None,
        "max_date": row.max_date.isoformat() if row.max_date else None,
    }


def commissions_timeseries(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    bucket: str = "month",
) -> list[dict]:
    """Commission time series bucketed by month or year."""
    base = _to_base(Trade.ib_commission, Trade.fx_rate_to_base)
    bucket_expr = _date_bucket(Trade.trade_date, bucket)
    q = select(
        bucket_expr.label("bucket"),
        func.coalesce(func.sum(base), 0).label("commission"),
        func.count(Trade.trade_id).label("trades"),
    ).group_by("bucket").order_by("bucket")
    q = _apply_period(q, Trade.trade_date, period)
    if account_id:
        q = q.where(Trade.account_id == account_id)
    return [
        {"bucket": r.bucket, "commission": _f(r.commission), "trades": r.trades}
        for r in session.execute(q)
    ]


def transaction_fees_summary(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> dict:
    """Regulatory transaction fees (formerly 'Transaction Tax')."""
    q = select(
        func.coalesce(func.sum(TransactionFee.tax_amount), 0).label("total"),
        func.count(TransactionFee.fee_id).label("rows"),
    )
    q = _apply_period(q, TransactionFee.report_date, period)
    if account_id:
        q = q.where(TransactionFee.account_id == account_id)
    row = session.execute(q).one()
    return {"total_base": _f(row.total), "rows": row.rows}


def commissions_breakdown(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> dict:
    """Commission total split two ways: by asset class and by fee type
    (broker commission · per-trade transaction tax · regulatory fees).
    """
    base_comm = _to_base(Trade.ib_commission, Trade.fx_rate_to_base)
    base_tax = _to_base(Trade.taxes, Trade.fx_rate_to_base)

    q_ac = select(
        Trade.asset_class,
        func.coalesce(func.sum(base_comm), 0).label("amount"),
        func.count(Trade.trade_id).label("trades"),
    ).group_by(Trade.asset_class)
    q_ac = _apply_period(q_ac, Trade.trade_date, period)
    if account_id:
        q_ac = q_ac.where(Trade.account_id == account_id)
    by_asset_class = [
        {
            "asset_class": (r.asset_class or "OTHER"),
            "amount_base": _f(r.amount),
            "trades": r.trades,
        }
        for r in session.execute(q_ac)
    ]

    bq = select(func.coalesce(func.sum(base_comm), 0))
    bq = _apply_period(bq, Trade.trade_date, period)
    if account_id:
        bq = bq.where(Trade.account_id == account_id)
    broker_total = _f(session.execute(bq).scalar()) or 0.0

    tq = select(func.coalesce(func.sum(base_tax), 0))
    tq = _apply_period(tq, Trade.trade_date, period)
    if account_id:
        tq = tq.where(Trade.account_id == account_id)
    trade_tax_total = _f(session.execute(tq).scalar()) or 0.0

    fq = select(func.coalesce(func.sum(TransactionFee.tax_amount), 0))
    fq = _apply_period(fq, TransactionFee.report_date, period)
    if account_id:
        fq = fq.where(TransactionFee.account_id == account_id)
    txn_fee_total = _f(session.execute(fq).scalar()) or 0.0

    by_type = [
        {"type": "Broker Commission", "amount_base": broker_total},
        {"type": "Transaction Tax", "amount_base": trade_tax_total},
        {"type": "Regulatory Fees", "amount_base": txn_fee_total},
    ]

    return {
        "by_asset_class": sorted(by_asset_class, key=lambda r: abs(r["amount_base"] or 0), reverse=True),
        "by_type": by_type,
        "total_base": broker_total + trade_tax_total + txn_fee_total,
    }


__all__ = [
    "commissions_breakdown",
    "commissions_summary",
    "commissions_timeseries",
    "transaction_fees_summary",
]
