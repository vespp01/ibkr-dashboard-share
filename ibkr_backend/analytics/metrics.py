"""Metric queries used by the API routes.

Historically the analytics god-module. The domains now live in sibling
modules — `positions`, `trades`, `executions`, `overview`, plus the earlier
extractions `dividends_calc` / `commissions` / `interest` — and this file
keeps the FIFO+MTM per-symbol attribution plus backward-compat re-exports
so existing callers (`from ibkr_backend.analytics.metrics import
trades_list`) keep working unchanged.

Every function takes:
  * `session`: SQLAlchemy session
  * `period`: PeriodFilter (optional — defaults to all-time)
  * `account_id`: filter by IBKR account (optional)
  * extra dimension filters where useful (asset_class, currency, country)

Results are returned as plain dicts/lists so they serialise cleanly through
FastAPI without extra Pydantic boilerplate.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import (
    FifoPerformanceUnderlying,
    Instrument,
    MtmPerformanceUnderlying,
)
from ._common import _apply_period, _f
from .filters import PeriodFilter

# ====================================================================
# Backward-compat re-exports — every name that used to be defined here.
# ====================================================================
from .commissions import (  # noqa: F401
    commissions_breakdown,
    commissions_summary,
    commissions_timeseries,
    transaction_fees_summary,
)
from .dividends_calc import (  # noqa: F401
    DIVIDEND_TYPES,
    WITHHOLDING_TYPES,
    dividends_by_country,
    dividends_summary,
    dividends_timeseries,
    income_projection,
    withholding_tax_by_country,
)
from .executions import (  # noqa: F401
    _base_currency,
    _fx_rates_lookup,
    executions_list,
)
from .interest import (  # noqa: F401
    INTEREST_PAID_TYPES,
    INTEREST_RECV_TYPES,
    interest_summary,
)
from .overview import (  # noqa: F401
    cash_balances,
    change_in_nav_summary,
    change_in_nav_timeseries,
    portfolio_overview,
    total_return_on_cash,
)
from .positions import (  # noqa: F401
    _position_consistency,
    open_shorts,
    pnl_by_asset_class,
    positions_snapshot,
    realized_pnl_timeseries,
    unrealized_pnl,
)
from .trades import (  # noqa: F401
    _classify_multi_leg,
    _dividends_by_conid,
    _first_buy_dates,
    _signed_qty,
    _strategy_hint,
    trades_list,
)


# ====================================================================
# SYMBOL ATTRIBUTION (FIFO + MTM per-symbol)
# ====================================================================
def symbol_attribution(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    asset_class: str | None = None,
    top_n: int | None = None,
    held_only: bool = False,
) -> dict:
    """Per-symbol P&L attribution sourced from FIFO + MTM Underlying tables.

    These IBKR sections live in base currency, so no FX conversion is needed.

    Aggregation semantics (see ``FifoPerformanceUnderlying`` docstring):
      * Realized fields (FIFO) are **daily** → SUM over period.
      * MTM ``total``/``transaction_mtm``/``commissions`` are **daily** → SUM.
      * Unrealized fields (FIFO) are **snapshots** → take the latest row of
        each (account, conid) inside the period.

    Returns a dict with ``rows`` (one per symbol, ordered by abs(total_pnl)
    desc), ``totals`` (sums across all symbols), and ``period`` / ``as_of``.
    """
    F = FifoPerformanceUnderlying  # noqa: N806
    M = MtmPerformanceUnderlying  # noqa: N806

    fifo_q = (
        select(
            F.account_id,
            F.conid,
            func.sum(func.coalesce(F.realized_st_profit, 0) + func.coalesce(F.realized_st_loss, 0)).label("realized_st"),
            func.sum(func.coalesce(F.realized_lt_profit, 0) + func.coalesce(F.realized_lt_loss, 0)).label("realized_lt"),
            func.sum(func.coalesce(F.total_realized_pnl, 0)).label("realized_total"),
            func.sum(func.coalesce(F.transferred_pnl, 0)).label("transferred"),
            func.max(F.snapshot_date).label("latest_snapshot"),
        )
        .group_by(F.account_id, F.conid)
    )
    fifo_q = _apply_period(fifo_q, F.snapshot_date, period)
    if account_id:
        fifo_q = fifo_q.where(F.account_id == account_id)
    if asset_class:
        fifo_q = fifo_q.where(F.asset_class == asset_class)
    fifo_rows = {(r.account_id, r.conid): r for r in session.execute(fifo_q)}

    # Latest unrealized snapshot per (account, conid) inside the period.
    snap_lookup: dict[tuple[str, int], date] = {
        k: r.latest_snapshot for k, r in fifo_rows.items() if r.latest_snapshot
    }
    # Newest snapshot present across all symbols in the period. A symbol whose
    # own latest snapshot is older than this is no longer held (closed/expired),
    # so its last-seen unrealized is stale and must not be counted as live P&L.
    global_latest_snapshot: date | None = max(snap_lookup.values(), default=None)
    unrealized_lookup: dict[tuple[str, int], FifoPerformanceUnderlying] = {}
    if snap_lookup:
        # One query per snapshot date keeps the SQL portable across SQLite/PG.
        by_date: dict[date, list[tuple[str, int]]] = {}
        for key, snap in snap_lookup.items():
            by_date.setdefault(snap, []).append(key)
        for snap, keys in by_date.items():
            conids = [k[1] for k in keys]
            uq = (
                select(F)
                .where(F.snapshot_date == snap)
                .where(F.conid.in_(conids))
            )
            if account_id:
                uq = uq.where(F.account_id == account_id)
            for row in session.execute(uq).scalars():
                k = (row.account_id, row.conid)
                if k in snap_lookup and snap_lookup[k] == snap:
                    unrealized_lookup[k] = row

    mtm_q = (
        select(
            M.account_id,
            M.conid,
            func.sum(func.coalesce(M.total, 0)).label("mtm_total"),
            func.sum(func.coalesce(M.total_with_accruals, 0)).label("mtm_total_accr"),
            func.sum(func.coalesce(M.transaction_mtm, 0)).label("mtm_transaction"),
            func.sum(func.coalesce(M.prior_open_mtm, 0)).label("mtm_prior_open"),
            func.sum(func.coalesce(M.commissions, 0)).label("commissions"),
            func.sum(func.coalesce(M.other, 0)).label("mtm_other"),
        )
        .group_by(M.account_id, M.conid)
    )
    mtm_q = _apply_period(mtm_q, M.snapshot_date, period)
    if account_id:
        mtm_q = mtm_q.where(M.account_id == account_id)
    if asset_class:
        mtm_q = mtm_q.where(M.asset_class == asset_class)
    mtm_rows = {(r.account_id, r.conid): r for r in session.execute(mtm_q)}

    keys = set(fifo_rows.keys()) | set(mtm_rows.keys())
    if not keys:
        return {
            "period": period.to_dict() if period else None,
            "rows": [],
            "totals": {
                "realized_pnl_base": 0.0,
                "unrealized_pnl_base": 0.0,
                "mtm_total_base": 0.0,
                "commissions_base": 0.0,
                "total_pnl_base": 0.0,
            },
            "as_of": None,
        }

    instr_lookup: dict[int, Instrument] = {}
    conids = list({k[1] for k in keys})
    if conids:
        for instr in session.execute(
            select(Instrument).where(Instrument.conid.in_(conids))
        ).scalars():
            instr_lookup[instr.conid] = instr

    out: list[dict] = []
    for key in keys:
        acct, conid = key
        f = fifo_rows.get(key)
        m = mtm_rows.get(key)
        u = unrealized_lookup.get(key)
        instr = instr_lookup.get(conid)

        realized = float(f.realized_total) if f and f.realized_total is not None else 0.0
        realized_st = float(f.realized_st) if f and f.realized_st is not None else 0.0
        realized_lt = float(f.realized_lt) if f and f.realized_lt is not None else 0.0
        transferred = float(f.transferred) if f and f.transferred is not None else 0.0

        mtm_total = float(m.mtm_total) if m and m.mtm_total is not None else 0.0
        commissions = float(m.commissions) if m and m.commissions is not None else 0.0
        mtm_transaction = float(m.mtm_transaction) if m and m.mtm_transaction is not None else 0.0
        mtm_other = float(m.mtm_other) if m and m.mtm_other is not None else 0.0
        mtm_total_accr = float(m.mtm_total_accr) if m and m.mtm_total_accr is not None else 0.0

        # Only a position still held as of the newest snapshot carries a live
        # unrealized P&L. A closed/expired position's last FIFO row can still
        # report unrealized (e.g. an option ITM the day before it expires); that
        # P&L was realized at close, so counting it here double-books and shows
        # a phantom holding. Gate unrealized on the position still being held.
        is_held = u is not None and snap_lookup.get(key) == global_latest_snapshot
        unrealized = float(u.total_unrealized_pnl) if is_held and u.total_unrealized_pnl is not None else 0.0
        unrealized_st = (
            (float(u.unrealized_st_profit) if is_held and u.unrealized_st_profit is not None else 0.0)
            + (float(u.unrealized_st_loss) if is_held and u.unrealized_st_loss is not None else 0.0)
        )
        unrealized_lt = (
            (float(u.unrealized_lt_profit) if is_held and u.unrealized_lt_profit is not None else 0.0)
            + (float(u.unrealized_lt_loss) if is_held and u.unrealized_lt_loss is not None else 0.0)
        )

        symbol = (u.symbol if u else None) or (instr.symbol if instr else None) or ""
        ac = (u.asset_class if u else None) or (instr.asset_class if instr else None)

        # FIFO realized/unrealized and MTM measure the same P&L on two bases:
        # adding them double-counts. The headline is the MTM period total
        # (reconciles with CIN mtm); realized/unrealized stay as breakdown.
        total_pnl = mtm_total
        out.append({
            "account_id": acct,
            "conid": conid,
            "symbol": symbol,
            "description": instr.description if instr else None,
            "asset_class": ac,
            "sector": instr.sector if instr else None,
            "industry": instr.industry if instr else None,
            "country": instr.issuer_country if instr else None,
            "realized_pnl_base": realized,
            "realized_st_base": realized_st,
            "realized_lt_base": realized_lt,
            "transferred_pnl_base": transferred,
            "unrealized_pnl_base": unrealized,
            "unrealized_st_base": unrealized_st,
            "unrealized_lt_base": unrealized_lt,
            "mtm_total_base": mtm_total,
            "mtm_total_with_accruals_base": mtm_total_accr,
            "mtm_transaction_base": mtm_transaction,
            "mtm_other_base": mtm_other,
            "commissions_base": commissions,
            "total_pnl_base": total_pnl,
            "snapshot_date": u.snapshot_date.isoformat() if u else None,
            "is_held": is_held,
        })

    if held_only:
        out = [r for r in out if r["is_held"]]

    out.sort(key=lambda r: abs(r["total_pnl_base"]), reverse=True)
    if top_n:
        out = out[:top_n]

    totals = {
        "realized_pnl_base": sum(r["realized_pnl_base"] for r in out),
        "unrealized_pnl_base": sum(r["unrealized_pnl_base"] for r in out),
        "mtm_total_base": sum(r["mtm_total_base"] for r in out),
        "commissions_base": sum(r["commissions_base"] for r in out),
        "total_pnl_base": sum(r["total_pnl_base"] for r in out),
    }
    as_of = max((r["snapshot_date"] for r in out if r["snapshot_date"]), default=None)
    return {
        "period": period.to_dict() if period else None,
        "rows": out,
        "totals": totals,
        "as_of": as_of,
        "symbol_count": len(out),
    }
