"""Commission + transaction-fee analytics.

Extracted from `metrics.py` for cohesion. Commissions live on the
`Trade.ib_commission` column; transaction fees on a separate
`TransactionFee` table. The breakdown function combines both so the
"total operational cost" KPI is honest.

Every amount is converted to base currency per row:
- `ib_commission` is in `ib_commission_ccy` (CASH trades pay it in CHF while
  the trade currency is HKD/USD...), so it is converted with that currency;
  when it equals the trade currency the Flex `fx_rate_to_base` is used.
- `transaction_fees.tax_amount` is in the row currency, converted with
  `fx_rates` on `report_date`. Rows with no currency (yearly subtotals from
  the legacy CSV import) cannot be converted and are skipped.
- GBP/HKD stamp duty arrives twice: as `trades.taxes` and as a fee row for the
  same trade. It is counted once, as "Transaction Tax"; the duplicate fee row
  is dropped from "Regulatory Fees".
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Trade, TransactionFee
from ._common import _apply_period, _f
from .filters import PeriodFilter
from .fx_table import FxTable

log = logging.getLogger(__name__)


def _bucket_key(d: date, bucket: str) -> str:
    bucket = (bucket or "month").lower()
    fmt = {"year": "%Y", "month": "%Y-%m", "day": "%Y-%m-%d"}
    if bucket not in fmt:
        raise ValueError(f"Unsupported bucket: {bucket}")
    return d.strftime(fmt[bucket])


def _trade_rows(
    session: Session,
    period: PeriodFilter | None,
    account_id: str | None,
    asset_class: str | None = None,
):
    q = select(
        Trade.trade_date,
        Trade.asset_class,
        Trade.currency,
        Trade.fx_rate_to_base,
        Trade.ib_commission,
        Trade.ib_commission_ccy,
        Trade.taxes,
    )
    q = _apply_period(q, Trade.trade_date, period)
    if account_id:
        q = q.where(Trade.account_id == account_id)
    if asset_class:
        q = q.where(Trade.asset_class == asset_class)
    return session.execute(q).all()


def _commission_base(r, fx: FxTable) -> float:
    """Broker commission of one trade row in base currency."""
    amount = _f(r.ib_commission)
    if not amount:
        return 0.0
    ccy = r.ib_commission_ccy or r.currency
    if ccy == r.currency and r.fx_rate_to_base:
        return amount * float(r.fx_rate_to_base)
    out = fx.convert(amount, ccy, r.trade_date)
    if out is None:
        log.warning("No fx_rates for commission currency %s on %s; row skipped", ccy, r.trade_date)
        return 0.0
    return out


def _tax_base(r, fx: FxTable) -> float:
    """Per-trade transaction tax (`Trade.taxes`, trade currency) in base currency."""
    amount = _f(r.taxes)
    if not amount:
        return 0.0
    if r.fx_rate_to_base:
        return amount * float(r.fx_rate_to_base)
    return fx.convert(amount, r.currency, r.trade_date) or 0.0


def _regulatory_fee_rows(
    session: Session,
    fx: FxTable,
    period: PeriodFilter | None,
    account_id: str | None,
) -> list[tuple[date | None, float]]:
    """(report_date, base amount) of fee rows that are not a stamp-duty twin."""
    # Trades that already carry `taxes`: their fee row is the same charge.
    taxed = {
        (a, c, d)
        for a, c, d in session.execute(
            select(Trade.account_id, Trade.conid, Trade.trade_date).where(Trade.taxes != 0)
        )
    }
    q = select(
        TransactionFee.account_id,
        TransactionFee.conid,
        TransactionFee.report_date,
        TransactionFee.tax_amount,
        TransactionFee.currency,
    )
    q = _apply_period(q, TransactionFee.report_date, period)
    if account_id:
        q = q.where(TransactionFee.account_id == account_id)
    out: list[tuple[date | None, float]] = []
    for acct, conid, d, amount, ccy in session.execute(q):
        if not ccy:
            continue  # legacy CSV yearly subtotal: no currency, not a fee event
        if (acct, conid, d) in taxed:
            continue
        base = fx.convert(_f(amount), ccy, d)
        if base is None:
            log.warning("No fx_rates for fee currency %s on %s; row skipped", ccy, d)
            continue
        out.append((d, base))
    return out




def commissions_summary(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    asset_class: str | None = None,
) -> dict:
    """Total broker commissions + per-trade aggregates, in base currency.

    Source: Trades.ib_commission converted from `ib_commission_ccy` (see module
    docstring). Already netted into Trades.net_cash, but we report the gross
    commission so users see what they actually paid.
    """
    fx = FxTable(session)
    rows = _trade_rows(session, period, account_id, asset_class)
    amounts = [_commission_base(r, fx) for r in rows]
    total = sum(amounts)
    dates = [r.trade_date for r in rows]
    return {
        "total_base": total,
        "trade_count": len(rows),
        "avg_per_trade_base": (total / len(rows)) if rows else 0.0,
        "min_date": min(dates).isoformat() if dates else None,
        "max_date": max(dates).isoformat() if dates else None,
    }


def commissions_timeseries(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    bucket: str = "month",
) -> list[dict]:
    """Commission time series bucketed by month or year (base currency)."""
    fx = FxTable(session)
    sums: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for r in _trade_rows(session, period, account_id):
        key = _bucket_key(r.trade_date, bucket)
        sums[key] += _commission_base(r, fx)
        counts[key] += 1
    return [
        {"bucket": k, "commission": sums[k], "trades": counts[k]}
        for k in sorted(sums)
    ]


def transaction_fees_summary(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> dict:
    """Regulatory transaction fees (formerly 'Transaction Tax'), base currency,
    excluding fee rows that duplicate a trade's own `taxes`."""
    rows = _regulatory_fee_rows(session, FxTable(session), period, account_id)
    return {"total_base": sum(a for _, a in rows), "rows": len(rows)}


def commissions_breakdown(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> dict:
    """Commission total split two ways: by asset class and by fee type
    (broker commission · per-trade transaction tax · regulatory fees).
    All amounts in base currency.
    """
    fx = FxTable(session)
    by_ac_amount: dict[str, float] = defaultdict(float)
    by_ac_trades: dict[str, int] = defaultdict(int)
    broker_total = 0.0
    trade_tax_total = 0.0
    for r in _trade_rows(session, period, account_id):
        c = _commission_base(r, fx)
        key = r.asset_class or "OTHER"
        by_ac_amount[key] += c
        by_ac_trades[key] += 1
        broker_total += c
        trade_tax_total += _tax_base(r, fx)
    by_asset_class = [
        {"asset_class": k, "amount_base": by_ac_amount[k], "trades": by_ac_trades[k]}
        for k in by_ac_amount
    ]
    txn_fee_total = sum(a for _, a in _regulatory_fee_rows(session, fx, period, account_id))

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
