"""Per-fill execution analytics (Trade Confirmation Flex section).

Extracted from `metrics.py` (god-module split — same pattern as
`dividends_calc` / `commissions` / `interest`). Code moved verbatim;
`metrics.py` re-exports every name for backward compat.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import FxRate, Instrument, NavDaily, TradeExecution
from ._common import _apply_period, _f
from .filters import PeriodFilter


def _base_currency(session: Session) -> str:
    """Latest NAV daily row's `base_currency`, defaulting to CHF."""
    ccy = session.execute(
        select(NavDaily.base_currency)
        .where(NavDaily.base_currency.is_not(None))
        .order_by(NavDaily.snapshot_date.desc())
        .limit(1)
    ).scalar()
    return (ccy or "CHF").upper()


def _fx_rates_lookup(
    session: Session,
    pairs: set[tuple[date, str]],
    to_ccy: str,
) -> dict[tuple[date, str], float]:
    """Bulk-fetch `from_ccy → to_ccy` FX rates for the given `(date, ccy)` pairs.

    Falls back to 1.0 when a date/currency is missing in `fx_rates` — same
    degrade-gracefully convention `analytics.fx` uses.
    """
    if not pairs:
        return {}
    pairs = {(d, c.upper()) for d, c in pairs if d and c}
    same_ccy = {(d, c): 1.0 for d, c in pairs if c == to_ccy.upper()}
    needed = pairs - set(same_ccy.keys())
    if not needed:
        return same_ccy

    dates = {d for d, _ in needed}
    ccys = {c for _, c in needed}
    rows = session.execute(
        select(FxRate.rate_date, FxRate.from_ccy, FxRate.rate)
        .where(FxRate.rate_date.in_(dates))
        .where(FxRate.from_ccy.in_(ccys))
        .where(FxRate.to_ccy == to_ccy)
    ).all()
    by_pair: dict[tuple[date, str], float] = {
        (rate_date, from_ccy): float(rate)
        for rate_date, from_ccy, rate in rows
    }
    out = dict(same_ccy)
    for d, c in needed:
        out[(d, c)] = by_pair.get((d, c), 1.0)
    return out


def executions_list(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    symbol: str | None = None,
    asset_class: str | None = None,
    order_id: str | None = None,
    multi_fill_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """Per-fill Trade Confirmation rows + lag/commission summary.

    Granularity is one row per `exec_id` (IBKR's per-fill unique ID). Activity
    Flex collapses multi-slice orders into a single trade row; this endpoint
    re-exposes the individual fills so the UI can render an order→fill lag
    distribution and detect multi-fill executions (today: SKYH / FTNT / PNG).

    `period`: applied against `trade_date`.
    `order_id`: exact match — drilldown for one order's fills.
    `multi_fill_only`: restrict to orders that filled in more than one slice.
    """
    # Build the page query (joins Instrument for description) and a separate
    # `agg` query against TradeExecution only, so aggregates don't cross-join.
    def _apply_filters(stmt):
        stmt = _apply_period(stmt, TradeExecution.trade_date, period)
        if account_id:
            stmt = stmt.where(TradeExecution.account_id == account_id)
        if symbol:
            stmt = stmt.where(TradeExecution.symbol == symbol)
        if asset_class:
            stmt = stmt.where(TradeExecution.asset_class == asset_class)
        if order_id:
            stmt = stmt.where(TradeExecution.order_id == order_id)
        if multi_fill_only:
            multi = (
                select(TradeExecution.order_id)
                .where(TradeExecution.order_id.is_not(None))
                .group_by(TradeExecution.order_id)
                .having(func.count() > 1)
            )
            stmt = stmt.where(TradeExecution.order_id.in_(multi))
        return stmt

    page_q = _apply_filters(
        select(TradeExecution, Instrument).join(
            Instrument, Instrument.conid == TradeExecution.conid, isouter=True
        )
    )
    # Load every execution matching the filter once. Volume is small (45-day
    # TC ceiling × per-day fills); aggregating in Python is simpler and
    # avoids subquery-alias gotchas with self-cross-joins.
    filter_execs: list[TradeExecution] = list(
        session.execute(_apply_filters(select(TradeExecution))).scalars()
    )
    total_fills = len(filter_execs)

    # Distinct + multi-fill orders.
    fills_by_order_filter: dict[str, int] = {}
    for ex in filter_execs:
        if ex.order_id:
            fills_by_order_filter[ex.order_id] = fills_by_order_filter.get(ex.order_id, 0) + 1
    distinct_orders = len(fills_by_order_filter)
    multi_fill_order_ids = sorted(o for o, n in fills_by_order_filter.items() if n > 1)
    multi_fill_orders = len(multi_fill_order_ids)

    # Lag stats + bucket distribution in Python from the filter set.
    bucket_defs = [
        ("<1m",     0,         60),
        ("1-5m",    60,        300),
        ("5-30m",   300,       1800),
        ("30m-2h",  1800,      7200),
        ("2-24h",   7200,      86400),
        ("1-7d",    86400,     7 * 86400),
        ("7-30d",   7 * 86400, 30 * 86400),
        (">30d",    30 * 86400, None),
    ]
    lag_buckets = {label: 0 for label, _, _ in bucket_defs}
    lag_values: list[float] = []
    for ex in filter_execs:
        if ex.order_time and ex.execution_time:
            secs = (ex.execution_time - ex.order_time).total_seconds()
            if secs < 0:
                continue
            lag_values.append(secs)
            for label, lo, hi in bucket_defs:
                if secs >= lo and (hi is None or secs < hi):
                    lag_buckets[label] += 1
                    break
    avg_lag = sum(lag_values) / len(lag_values) if lag_values else None
    max_lag = max(lag_values) if lag_values else None
    min_lag = min(lag_values) if lag_values else None
    lag_count = len(lag_values)
    lag_distribution = [{"bucket": label, "count": lag_buckets[label]} for label, _, _ in bucket_defs]

    # Filter-wide totals (in base currency). Bulk-fetch FX pairs once.
    base_ccy = _base_currency(session)
    fx_pairs_filter: set[tuple[date, str]] = set()
    for ex in filter_execs:
        if ex.trade_date and ex.currency:
            fx_pairs_filter.add((ex.trade_date, ex.currency))
        if ex.trade_date and ex.commission_currency:
            fx_pairs_filter.add((ex.trade_date, ex.commission_currency))
    fx_filter_lookup = _fx_rates_lookup(session, fx_pairs_filter, base_ccy)
    proceeds_total_base = 0.0
    commissions_total_base = 0.0
    for ex in filter_execs:
        proceeds_fx = fx_filter_lookup.get((ex.trade_date, (ex.currency or "").upper()), 1.0)
        comm_ccy = (ex.commission_currency or ex.currency or "").upper()
        comm_fx = fx_filter_lookup.get((ex.trade_date, comm_ccy), 1.0)
        if ex.proceeds is not None:
            proceeds_total_base += float(ex.proceeds) * proceeds_fx
        if ex.commission is not None:
            commissions_total_base += float(ex.commission) * comm_fx

    # Page the rows.
    page_q = page_q.order_by(TradeExecution.execution_time.desc()).limit(limit).offset(offset)
    page_rows = list(session.execute(page_q))

    # Fills-per-order across the same filter — for the page rows.
    page_order_ids = {ex.order_id for ex, _instr in page_rows if ex.order_id}
    fills_per_order: dict[str, int] = {}
    if page_order_ids:
        sub = (
            select(TradeExecution.order_id, func.count().label("n"))
            .where(TradeExecution.order_id.in_(page_order_ids))
            .group_by(TradeExecution.order_id)
        )
        fills_per_order = {row.order_id: int(row.n) for row in session.execute(sub)}

    # FX cache for the page rows. Reuse the filter-wide lookup so we don't
    # round-trip the FxRate table twice.
    rows: list[dict] = []
    for ex, instr in page_rows:
        proceeds_fx = fx_filter_lookup.get((ex.trade_date, (ex.currency or "").upper()), 1.0) if ex.currency else 1.0
        comm_ccy = (ex.commission_currency or ex.currency or "").upper()
        comm_fx = fx_filter_lookup.get((ex.trade_date, comm_ccy), 1.0) if comm_ccy else 1.0
        proceeds_base = (_f(ex.proceeds) or 0.0) * proceeds_fx if ex.proceeds is not None else None
        commission_base = (_f(ex.commission) or 0.0) * comm_fx if ex.commission is not None else None

        lag = None
        if ex.order_time and ex.execution_time:
            lag = (ex.execution_time - ex.order_time).total_seconds()

        rows.append({
            "exec_id": ex.exec_id,
            "trade_id": ex.trade_id,
            "order_id": ex.order_id,
            "block_id": ex.block_id,
            "ext_exec_id": ex.ext_exec_id,
            "account_id": ex.account_id,
            "conid": ex.conid,
            "symbol": ex.symbol,
            "description": instr.description if instr else None,
            "asset_class": ex.asset_class,
            "currency": ex.currency,
            "exchange": ex.exchange,
            "order_type": ex.order_type,
            "transaction_type": ex.transaction_type,
            "execution_time": ex.execution_time.isoformat() if ex.execution_time else None,
            "order_time": ex.order_time.isoformat() if ex.order_time else None,
            "trade_date": ex.trade_date.isoformat() if ex.trade_date else None,
            "settle_date": ex.settle_date.isoformat() if ex.settle_date else None,
            "order_to_fill_lag_seconds": lag,
            "buy_sell": ex.buy_sell,
            "open_close": ex.open_close,
            "quantity": _f(ex.quantity),
            "price": _f(ex.price),
            "proceeds": _f(ex.proceeds),
            "proceeds_base": proceeds_base,
            "net_cash": _f(ex.net_cash),
            "commission": _f(ex.commission),
            "commission_currency": ex.commission_currency,
            "commission_base": commission_base,
            "broker_execution_commission": _f(ex.broker_execution_commission),
            "broker_clearing_commission": _f(ex.broker_clearing_commission),
            "third_party_execution_commission": _f(ex.third_party_execution_commission),
            "third_party_clearing_commission": _f(ex.third_party_clearing_commission),
            "third_party_regulatory_commission": _f(ex.third_party_regulatory_commission),
            "other_commission": _f(ex.other_commission),
            "multiplier": _f(ex.multiplier),
            "is_multi_fill": fills_per_order.get(ex.order_id or "", 0) > 1,
            "fills_in_order": fills_per_order.get(ex.order_id or "", 1),
        })

    return {
        "rows": rows,
        "limit": limit,
        "offset": offset,
        "summary": {
            "total_fills": int(total_fills),
            "distinct_orders": int(distinct_orders),
            "multi_fill_orders": int(multi_fill_orders),
            "max_lag_seconds": float(max_lag) if max_lag is not None else None,
            "min_lag_seconds": float(min_lag) if min_lag is not None else None,
            "avg_lag_seconds": float(avg_lag) if avg_lag is not None else None,
            "lag_sample_size": int(lag_count) if lag_count is not None else 0,
            "total_commissions_base": commissions_total_base,
            "total_proceeds_base": proceeds_total_base,
            "base_currency": base_ccy,
            "lag_distribution": lag_distribution,
            "multi_fill_order_ids": multi_fill_order_ids,
        },
    }
