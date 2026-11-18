"""Position-level analytics — EOD snapshots, unrealized + realized P&L.

Extracted from `metrics.py` (god-module split — same pattern as
`dividends_calc` / `commissions` / `interest`). Code moved verbatim;
`metrics.py` re-exports every name for backward compat.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Instrument, NavDaily, PositionEod, Trade
from ._common import (
    _apply_period,
    _date_bucket,
    _days_stale,
    _f,
    _latest_snapshot_date,
    _to_base,
)
from .filters import PeriodFilter


def _position_consistency(session: Session, snapshot: date | None, account_id: str | None) -> dict:
    if snapshot is None:
        return {"market_value_base": None, "nav_positions_base": None, "coverage_ratio": None}
    q = select(
        func.coalesce(func.sum(_to_base(PositionEod.position_value, PositionEod.fx_rate_to_base)), 0)
    ).where(PositionEod.snapshot_date == snapshot)
    if account_id:
        q = q.where(PositionEod.account_id == account_id)
    market_value_base = _f(session.execute(q).scalar())

    nav_q = select(
        func.coalesce(NavDaily.stock, 0) + func.coalesce(NavDaily.options, 0)
    ).where(NavDaily.snapshot_date == snapshot)
    if account_id:
        nav_q = nav_q.where(NavDaily.account_id == account_id)
    nav_positions_base = _f(session.execute(nav_q).scalar())
    coverage_ratio = None
    if nav_positions_base not in (None, 0):
        coverage_ratio = (market_value_base or 0) / nav_positions_base
    return {
        "market_value_base": market_value_base,
        "nav_positions_base": nav_positions_base,
        "coverage_ratio": coverage_ratio,
    }


# ====================================================================
# POSITIONS / UNREALIZED
# ====================================================================
def positions_snapshot(
    session: Session,
    *,
    snapshot_date: date | None = None,
    account_id: str | None = None,
    asset_class: str | None = None,
) -> list[dict]:
    """Open positions as-of `snapshot_date` (defaults to latest available)."""
    snapshot = snapshot_date or _latest_snapshot_date(session, account_id)
    if snapshot is None:
        return []
    q = (
        select(
            PositionEod,
            Instrument.description,
            Instrument.issuer_country,
            Instrument.sector,
            Instrument.industry,
        )
        .where(PositionEod.snapshot_date == snapshot)
        .join(Instrument, Instrument.conid == PositionEod.conid, isouter=True)
    )
    if account_id:
        q = q.where(PositionEod.account_id == account_id)
    if asset_class:
        q = q.where(PositionEod.asset_class == asset_class)
    # Weight is over total NAV (same formula as margin.py / investmentlab.py).
    # Flex `percentOfNAV` is per asset class (sums to 100 within each), so it is
    # exposed separately as `pct_of_asset_class`.
    nav_q = select(func.sum(NavDaily.nav_total)).where(
        NavDaily.snapshot_date == snapshot, NavDaily.nav_total.is_not(None)
    )
    if account_id:
        nav_q = nav_q.where(NavDaily.account_id == account_id)
    nav_total = float(session.execute(nav_q).scalar() or 0)
    out: list[dict] = []
    for p, desc, country, sector, industry in session.execute(q):
        position_value_base = (
            (p.position_value or 0) * (p.fx_rate_to_base or 1)
        )
        cost_base = (p.cost_basis_money or 0) * (p.fx_rate_to_base or 1)
        unrealized_base = (p.fifo_pnl_unrealized or 0) * (p.fx_rate_to_base or 1)
        out.append({
            "snapshot_date": p.snapshot_date.isoformat(),
            "account_id": p.account_id,
            "conid": p.conid,
            "symbol": p.symbol,
            "description": desc,
            "asset_class": p.asset_class,
            "country": country,
            "sector": sector,
            "industry": industry,
            "currency": p.currency,
            "quantity": _f(p.quantity),
            "mark_price": _f(p.mark_price),
            "position_value": _f(p.position_value),
            "position_value_base": _f(position_value_base),
            "cost_basis_money": _f(p.cost_basis_money),
            "cost_basis_money_base": _f(cost_base),
            "cost_basis_price": _f(p.cost_basis_price),
            "fifo_pnl_unrealized": _f(p.fifo_pnl_unrealized),
            "fifo_pnl_unrealized_base": _f(unrealized_base),
            "weight": (float(position_value_base) / nav_total * 100) if nav_total else None,
            "pct_of_asset_class": _f(p.pct_of_nav),
            "fx_rate_to_base": _f(p.fx_rate_to_base),
            "open_dt": p.open_dt.isoformat() if p.open_dt else None,
        })
    return out


def unrealized_pnl(
    session: Session,
    *,
    snapshot_date: date | None = None,
    account_id: str | None = None,
) -> dict:
    rows = positions_snapshot(
        session, snapshot_date=snapshot_date, account_id=account_id
    )
    total = sum(r["fifo_pnl_unrealized_base"] or 0 for r in rows)
    market_value = sum(r["position_value_base"] or 0 for r in rows)
    cost = sum(r["cost_basis_money_base"] or 0 for r in rows)
    return {
        "snapshot_date": rows[0]["snapshot_date"] if rows else None,
        "days_stale": _days_stale(snapshot_date or _latest_snapshot_date(session, account_id)),
        "positions": len(rows),
        "market_value_base": market_value,
        "cost_base": cost,
        "unrealized_pnl_base": total,
        "unrealized_pct": (total / cost * 100) if cost else None,
    }


def realized_pnl_timeseries(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    bucket: str = "month",
) -> list[dict]:
    base = _to_base(Trade.fifo_pnl_realized, Trade.fx_rate_to_base)
    bucket_expr = _date_bucket(Trade.trade_date, bucket)
    q = select(
        bucket_expr.label("bucket"),
        func.coalesce(func.sum(base), 0).label("realized"),
        func.count(Trade.trade_id).label("trades"),
    ).group_by("bucket").order_by("bucket")
    q = _apply_period(q, Trade.trade_date, period)
    if account_id:
        q = q.where(Trade.account_id == account_id)
    return [
        {"bucket": r.bucket, "realized": _f(r.realized), "trades": r.trades}
        for r in session.execute(q)
    ]


def pnl_by_asset_class(
    session: Session,
    *,
    snapshot_date: date | None = None,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> list[dict]:
    """Combine realized P&L (period) + unrealized (snapshot) by asset class."""
    snapshot = snapshot_date or _latest_snapshot_date(session, account_id)
    realized_base = _to_base(Trade.fifo_pnl_realized, Trade.fx_rate_to_base)
    qr = select(
        Trade.asset_class,
        func.coalesce(func.sum(realized_base), 0).label("realized"),
    ).group_by(Trade.asset_class)
    qr = _apply_period(qr, Trade.trade_date, period)
    if account_id:
        qr = qr.where(Trade.account_id == account_id)
    realized_map = {r.asset_class: _f(r.realized) for r in session.execute(qr)}

    unr_map: dict[str | None, float] = {}
    cost_map: dict[str | None, float] = {}
    mkt_map: dict[str | None, float] = {}
    if snapshot is not None:
        qu = select(
            PositionEod.asset_class,
            func.coalesce(
                func.sum(_to_base(PositionEod.fifo_pnl_unrealized, PositionEod.fx_rate_to_base)),
                0,
            ).label("unrealized"),
            func.coalesce(
                func.sum(_to_base(PositionEod.cost_basis_money, PositionEod.fx_rate_to_base)),
                0,
            ).label("cost"),
            func.coalesce(
                func.sum(_to_base(PositionEod.position_value, PositionEod.fx_rate_to_base)),
                0,
            ).label("market"),
        ).where(PositionEod.snapshot_date == snapshot).group_by(PositionEod.asset_class)
        if account_id:
            qu = qu.where(PositionEod.account_id == account_id)
        for r in session.execute(qu):
            unr_map[r.asset_class] = _f(r.unrealized)
            cost_map[r.asset_class] = _f(r.cost)
            mkt_map[r.asset_class] = _f(r.market)

    classes = set(realized_map) | set(unr_map)
    out = []
    for cls in sorted(classes, key=lambda c: c or ""):
        realized = realized_map.get(cls, 0) or 0
        unrealized = unr_map.get(cls, 0) or 0
        cost = cost_map.get(cls, 0) or 0
        market = mkt_map.get(cls, 0) or 0
        total = realized + unrealized
        out.append({
            "asset_class": cls or "UNKNOWN",
            "realized_pnl_base": realized,
            "unrealized_pnl_base": unrealized,
            "total_pnl_base": total,
            "cost_basis_base": cost,
            "market_value_base": market,
            "return_pct": (total / cost * 100) if cost else None,
        })
    return out


def open_shorts(
    session: Session,
    *,
    snapshot_date: date | None = None,
    account_id: str | None = None,
) -> list[dict]:
    """Open short positions (quantity < 0). Used for the 'Open Credit' panel."""
    snapshot = snapshot_date or _latest_snapshot_date(session, account_id)
    if snapshot is None:
        return []
    q = (
        select(PositionEod, Instrument.description)
        .where(PositionEod.snapshot_date == snapshot)
        .where(PositionEod.quantity < 0)
        .join(Instrument, Instrument.conid == PositionEod.conid, isouter=True)
    )
    if account_id:
        q = q.where(PositionEod.account_id == account_id)
    out: list[dict] = []
    for p, desc in session.execute(q):
        position_value_base = (p.position_value or 0) * (p.fx_rate_to_base or 1)
        cost_base = (p.cost_basis_money or 0) * (p.fx_rate_to_base or 1)
        out.append({
            "symbol": p.symbol,
            "description": desc,
            "asset_class": p.asset_class,
            "currency": p.currency,
            "quantity": _f(p.quantity),
            "mark_price": _f(p.mark_price),
            "position_value": _f(p.position_value),
            "position_value_base": _f(position_value_base),
            "cost_basis_money_base": _f(cost_base),
            "fifo_pnl_unrealized_base": _f((p.fifo_pnl_unrealized or 0) * (p.fx_rate_to_base or 1)),
            "open_dt": p.open_dt.isoformat() if p.open_dt else None,
            "snapshot_date": p.snapshot_date.isoformat(),
        })
    return out
