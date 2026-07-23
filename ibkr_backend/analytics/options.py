"""Options analytics — OCC parsing + per-contract views.

This module owns the dashboard's `/options/*` surface. It deals with `Trade`
rows where `asset_class='OPT'`, the `option_eae` table (Exercise / Assignment /
Expiration), and EOD short-option positions for the assignment-risk surface.

OCC symbology (OSI standard, 21 chars):
    AAAAAA YYMMDD R SSSSSSSS
    └─ root, left-justified, space-padded
            └─ expiry YYMMDD (assume 20YY for all 2-digit years emitted by IB)
                   └─ right: C or P
                     └─ strike × 1000, zero-padded to 8 digits

Falls back to parsing IBKR's human description (`AAAA DDMMMYY strike P|C`) if
the symbol can't be matched — defensive only; in practice all Flex rows we've
seen carry the OSI form on `Trade.symbol`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..db.models import (
    CashTransaction,
    Instrument,
    MtmPerformanceUnderlying,
    OptionExerciseAssignment,
    PositionEod,
    Trade,
)
from ._common import _apply_period, _f, is_closed, is_closed_filter, is_open, is_open_filter
from .filters import PeriodFilter
from .metrics import (
    DIVIDEND_TYPES,
    INTEREST_PAID_TYPES,
    INTEREST_RECV_TYPES,
    WITHHOLDING_TYPES,
    _classify_multi_leg,
)
# OCC parsing lives in `options_parsing` — pure regex, no DB.
# Re-exported here so existing callers (frontend `import { parse_occ_symbol }`
# from the test suite) keep working.
from .options_parsing import (
    OptionContract,
    dividend_symbol_from_description,
    enrich_option,
    parse_occ_symbol,
    parse_option_description,
)


# Helpers (_f, _apply_period, is_closed/is_open) live in ._common — see
# that module's docstring for why they were centralised.


def _enrich_option(symbol: str | None, instr: Instrument | None) -> OptionContract:
    """Read persisted OCC fields when present, falling back to parser logic."""
    parsed = enrich_option(symbol, instr.description if instr else None)
    if instr is None:
        return parsed
    strike = (
        float(instr.option_strike)
        if instr.option_strike is not None
        else parsed.strike
    )
    return OptionContract(
        underlying=instr.option_underlying or parsed.underlying,
        expiry=instr.option_expiry or parsed.expiry,
        right=instr.option_right or parsed.right,
        strike=strike,
    )


def _classify_action(t: Trade, contract: OptionContract) -> str:
    """Premium-flow oriented action label."""
    side = (t.buy_sell or "").upper()
    oc = (t.open_close or "").upper()
    right = contract.right or ""
    if oc == "O" and side == "SELL":
        return f"Sell to open {right}".strip()
    if oc == "O" and side == "BUY":
        return f"Buy to open {right}".strip()
    if oc == "C" and side == "BUY":
        return f"Buy to close {right}".strip()
    if oc == "C" and side == "SELL":
        return f"Sell to close {right}".strip()
    return side or "?"


# ---------------------------------------------------------------- queries

def options_trades_list(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    underlying: str | None = None,
    right: str | None = None,
    status: str = "all",  # all | open | closed
    limit: int = 200,
    offset: int = 0,
) -> dict:
    base_pnl = func.coalesce(
        Trade.fifo_pnl_realized * func.coalesce(Trade.fx_rate_to_base, 1), 0
    )
    base_proceeds = func.coalesce(
        Trade.proceeds * func.coalesce(Trade.fx_rate_to_base, 1), 0
    )
    base_commission = func.coalesce(
        Trade.ib_commission * func.coalesce(Trade.fx_rate_to_base, 1), 0
    )
    # net_cash already nets ibCommission (per CLAUDE.md); use it as the
    # summary "net premium" so the KPI agrees with the premium-timeseries
    # chart (which also bins by net_cash).
    base_cash = func.coalesce(
        Trade.net_cash * func.coalesce(Trade.fx_rate_to_base, 1), 0
    )

    # OCC parsing happens in Python, so `underlying` and `right` filters can't
    # be pushed down to SQL — we pull the OPT slice and filter in-memory.
    # Acceptable while OPT history is < few thousand rows; revisit if that
    # changes (option: persist parsed root/right back onto Instrument).
    q = (
        select(Trade, Instrument, base_pnl, base_proceeds, base_commission, base_cash)
        .join(Instrument, Instrument.conid == Trade.conid, isouter=True)
        .where(Trade.asset_class == "OPT")
    )
    q = _apply_period(q, Trade.trade_date, period)
    if account_id:
        q = q.where(Trade.account_id == account_id)
    s = (status or "all").lower()
    if s == "closed":
        q = q.where(is_closed_filter())
    elif s == "open":
        q = q.where(is_open_filter())

    all_rows = list(session.execute(q.order_by(Trade.trade_datetime.desc())))

    enriched: list[tuple[Trade, Instrument | None, float, float, float, float, OptionContract]] = []
    for t, instr, pnl, proc, comm, cash in all_rows:
        c = _enrich_option(t.symbol, instr)
        if underlying and (c.underlying or "").upper() != underlying.upper():
            continue
        if right and (c.right or "").upper() != right.upper():
            continue
        enriched.append((
            t, instr,
            _f(pnl) or 0.0, _f(proc) or 0.0, _f(comm) or 0.0, _f(cash) or 0.0,
            c,
        ))

    total = len(enriched)
    page = enriched[offset:offset + limit]

    # Multi-leg classification: fetch ALL trades sharing any page row's
    # `(ib_order_id, trade_date, account_id)` and classify each group. We
    # intentionally bypass the OPT filter when fetching siblings so combos
    # that mix stock + option legs (covered call, protective put) classify
    # correctly. The lookup is keyed by `trade_id`.
    multi_strategy_by_trade: dict[str, str] = {}
    multi_legs_count_by_trade: dict[str, int] = {}
    order_keys = {
        (t.ib_order_id, t.trade_date, t.account_id)
        for t, *_ in page
        if t.ib_order_id and t.trade_date and t.account_id
    }
    if order_keys:
        order_ids = {oid for oid, _, _ in order_keys}
        sibling_q = (
            select(Trade, Instrument)
            .join(Instrument, Instrument.conid == Trade.conid, isouter=True)
            .where(Trade.ib_order_id.in_(order_ids))
        )
        siblings = list(session.execute(sibling_q))
        groups: dict[tuple[str, date, str], list[tuple[Trade, Instrument | None]]] = {}
        for t, instr in siblings:
            if not (t.ib_order_id and t.trade_date and t.account_id):
                continue
            groups.setdefault((t.ib_order_id, t.trade_date, t.account_id), []).append((t, instr))
        for key, group in groups.items():
            if key not in order_keys or len(group) < 2:
                continue
            label = _classify_multi_leg(group) or f"Multi-leg ({len(group)})"
            for t, _ in group:
                multi_strategy_by_trade[t.trade_id] = label
                multi_legs_count_by_trade[t.trade_id] = len(group)

    realized_total = sum(p for _, _, p, _, _, _, _ in enriched)
    net_premium = sum(ca for _, _, _, _, _, ca, _ in enriched)
    commissions_total = sum(co for _, _, _, _, co, _, _ in enriched)
    contracts_traded = int(sum(
        abs(_f(t.quantity) or 0.0) for t, *_ in enriched
    ))
    win = sum(1 for t, _i, p, _pr, _co, _ca, _c in enriched if is_closed(t.open_close) and p > 0)
    loss = sum(1 for t, _i, p, _pr, _co, _ca, _c in enriched if is_closed(t.open_close) and p < 0)
    be = sum(1 for t, _i, p, _pr, _co, _ca, _c in enriched if is_closed(t.open_close) and p == 0)
    decided_n = win + loss
    win_rate = (win / decided_n) if decided_n > 0 else None
    win_sum = sum(p for t, _i, p, _pr, _co, _ca, _c in enriched if is_closed(t.open_close) and p > 0)
    loss_sum = sum(p for t, _i, p, _pr, _co, _ca, _c in enriched if is_closed(t.open_close) and p < 0)
    avg_winner = (win_sum / win) if win > 0 else None
    avg_loser = (loss_sum / loss) if loss > 0 else None
    loss_abs = abs(loss_sum)
    profit_factor = (win_sum / loss_abs) if loss_abs > 0 else None
    # Expectancy in base currency per closed trade: E[P&L] from the empirical
    # win/loss distribution. Source: Sinclair "Positional Option Trading" ch.
    # 11; tastytrade research. Undefined when there are no decided trades.
    expectancy = (
        (win_rate * (avg_winner or 0.0)) + ((1 - win_rate) * (avg_loser or 0.0))
        if win_rate is not None
        else None
    )

    rows = []
    for t, instr, pnl_b, proc_b, comm_b, _cash_b, c in page:
        # Capital at risk: notional = strike × multiplier × |qty|, in base ccy.
        # This is the correct denominator for cash-secured puts and covered
        # calls (the universally requested ROC; mirrors premium-tracker's
        # AROC). For long debits it overstates capital — the real cap was the
        # premium paid. Documented trade-off; refine when ≥50 long trades.
        qty = _f(t.quantity) or 0.0
        mult = _f(t.multiplier) or 1.0
        fx = _f(t.fx_rate_to_base) or 1.0
        capital_at_risk_base: float | None = (
            abs(qty) * mult * c.strike * fx
            if (c.strike is not None and qty)
            else None
        )
        roc_pct: float | None = (
            (pnl_b / capital_at_risk_base * 100)
            if (is_closed(t.open_close) and capital_at_risk_base and capital_at_risk_base > 0)
            else None
        )
        # Capture-% proxy: only meaningful for BUY-TO-CLOSE of a short premium
        # position (the canonical "manage at 50%" path). capture = pnl /
        # (pnl + |close_proceeds|) reconstructs the open credit X from
        # PnL = X - Y and close debit Y. SELL-to-close of longs returns None
        # because the formula would need the open debit, which we don't have
        # on this row. Source: tastytrade "Manage Winners" research.
        side = (t.buy_sell or "").upper()
        capture_pct: float | None = None
        if is_closed(t.open_close) and side == "BUY":
            close_abs = abs(proc_b)
            denom = (pnl_b or 0.0) + close_abs
            capture_pct = ((pnl_b or 0.0) / denom) if denom > 0 else None
        rows.append({
            "trade_id": t.trade_id,
            "ib_order_id": t.ib_order_id,
            "symbol": t.symbol,
            "description": instr.description if instr else None,
            "underlying": c.underlying,
            "right": c.right,
            "strike": c.strike,
            "expiry": c.expiry.isoformat() if c.expiry else None,
            "dte_today": (c.expiry - date.today()).days if c.expiry else None,
            "trade_date": t.trade_date.isoformat() if t.trade_date else None,
            "trade_datetime": t.trade_datetime.isoformat() if t.trade_datetime else None,
            "buy_sell": t.buy_sell,
            "open_close": t.open_close,
            "action": _classify_action(t, c),
            "quantity": _f(t.quantity),
            "multiplier": _f(t.multiplier),
            "trade_price": _f(t.trade_price),
            "proceeds": _f(t.proceeds),
            "proceeds_base": proc_b,
            "ib_commission": _f(t.ib_commission),
            "commission_base": comm_b,
            "fifo_pnl_realized": _f(t.fifo_pnl_realized),
            "fifo_pnl_realized_base": pnl_b,
            "currency": t.currency,
            "strategy": multi_strategy_by_trade.get(t.trade_id),
            "strategy_legs": multi_legs_count_by_trade.get(t.trade_id, 1),
            "is_multi_leg": t.trade_id in multi_strategy_by_trade,
            "capital_at_risk_base": capital_at_risk_base,
            "roc_pct": roc_pct,
            "capture_pct": capture_pct,
            "is_open": is_open(t.open_close),
            "is_closed": is_closed(t.open_close),
        })

    return {
        "rows": rows,
        "limit": limit,
        "offset": offset,
        "summary": {
            "total": total,
            "contracts_traded": contracts_traded,
            "net_premium_base": net_premium,
            "commissions_base": commissions_total,
            "realized_pnl_base": realized_total,
            "win": int(win),
            "loss": int(loss),
            "be": int(be),
            "win_rate": win_rate,
            "avg_winner_base": avg_winner,
            "avg_loser_base": avg_loser,
            "profit_factor": profit_factor,
            "expectancy_base": expectancy,
            "win_sum_base": win_sum,
            "loss_sum_base": loss_sum,
        },
        "period": period.to_dict() if period else None,
    }


def options_eae_list(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    underlying: str | None = None,
) -> dict:
    q = select(OptionExerciseAssignment, Instrument).join(
        Instrument,
        Instrument.conid == OptionExerciseAssignment.conid,
        isouter=True,
    )
    q = _apply_period(q, OptionExerciseAssignment.event_date, period)
    if account_id:
        q = q.where(OptionExerciseAssignment.account_id == account_id)
    q = q.order_by(OptionExerciseAssignment.event_date.desc())

    all_rows = list(session.execute(q))

    rows = []
    counts = {"exercise": 0, "assignment": 0, "expiration": 0, "buy": 0, "sell": 0, "other": 0}
    cash_impact = 0.0
    for e, instr in all_rows:
        c = _enrich_option(e.symbol, instr)
        if underlying and (c.underlying or e.underlying_symbol or "").upper() != underlying.upper():
            continue
        ttype = (e.transaction_type or "").lower()
        if "exerc" in ttype:
            counts["exercise"] += 1
        elif "assign" in ttype:
            counts["assignment"] += 1
        elif "expir" in ttype:
            counts["expiration"] += 1
        elif ttype == "buy":
            counts["buy"] += 1
        elif ttype == "sell":
            counts["sell"] += 1
        else:
            counts["other"] += 1
        cash_impact += _f(e.proceeds) or 0.0
        rows.append({
            "eae_id": e.eae_id,
            "event_date": e.event_date.isoformat() if e.event_date else None,
            "transaction_type": e.transaction_type,
            "symbol": e.symbol,
            "underlying": c.underlying or e.underlying_symbol,
            "right": c.right,
            "strike": c.strike,
            "expiry": c.expiry.isoformat() if c.expiry else None,
            "quantity": _f(e.quantity),
            "multiplier": _f(e.multiplier),
            "trade_price": _f(e.trade_price),
            "proceeds": _f(e.proceeds),
            "commission": _f(e.commission),
            "realized_pnl": _f(e.realized_pnl),
            "currency": e.currency,
            "description": instr.description if instr else None,
        })

    return {
        "rows": rows,
        "summary": {
            "total": len(rows),
            "counts": counts,
            "cash_impact_total": cash_impact,
        },
        "period": period.to_dict() if period else None,
    }


def options_premium_timeseries(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    bucket: str = "month",
) -> dict:
    """Net premium (signed netCash on OPT trades) grouped by month or year.

    SELL to open / BUY to close pay premium (positive net_cash inflow);
    BUY to open / SELL to close consume it.
    """
    base_cash = (
        Trade.net_cash * func.coalesce(Trade.fx_rate_to_base, 1)
    )
    q = (
        select(Trade.trade_date, base_cash.label("cash_base"))
        .where(Trade.asset_class == "OPT")
    )
    q = _apply_period(q, Trade.trade_date, period)
    if account_id:
        q = q.where(Trade.account_id == account_id)
    rows = list(session.execute(q))

    buckets: dict[str, dict[str, float]] = {}
    for d, cash in rows:
        if d is None:
            continue
        key = d.strftime("%Y-%m") if bucket == "month" else str(d.year)
        b = buckets.setdefault(key, {"premium_in": 0.0, "premium_out": 0.0, "net": 0.0, "count": 0})
        c = _f(cash) or 0.0
        if c >= 0:
            b["premium_in"] += c
        else:
            b["premium_out"] += c
        b["net"] += c
        b["count"] += 1

    series = []
    cumulative = 0.0
    for key in sorted(buckets.keys()):
        b = buckets[key]
        cumulative += b["net"]
        series.append({
            "bucket": key,
            "premium_in_base": b["premium_in"],
            "premium_out_base": b["premium_out"],
            "net_premium_base": b["net"],
            "cumulative_net_base": cumulative,
            "trades": b["count"],
        })

    return {
        "bucket": bucket,
        "series": series,
        "summary": {
            "total_in": sum(b["premium_in"] for b in buckets.values()),
            "total_out": sum(b["premium_out"] for b in buckets.values()),
            "total_net": sum(b["net"] for b in buckets.values()),
            "trades": sum(b["count"] for b in buckets.values()),
        },
        "period": period.to_dict() if period else None,
    }


def options_pnl_by_underlying(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> dict:
    base_pnl = Trade.fifo_pnl_realized * func.coalesce(Trade.fx_rate_to_base, 1)
    base_cash = Trade.net_cash * func.coalesce(Trade.fx_rate_to_base, 1)
    base_comm = Trade.ib_commission * func.coalesce(Trade.fx_rate_to_base, 1)
    q = (
        select(Trade, Instrument, base_pnl, base_cash, base_comm)
        .join(Instrument, Instrument.conid == Trade.conid, isouter=True)
        .where(Trade.asset_class == "OPT")
    )
    q = _apply_period(q, Trade.trade_date, period)
    if account_id:
        q = q.where(Trade.account_id == account_id)

    by_under: dict[str, dict] = {}
    for t, instr, pnl, cash, comm in session.execute(q):
        c = _enrich_option(t.symbol, instr)
        u = c.underlying or t.symbol or "UNKNOWN"
        b = by_under.setdefault(u, {
            "underlying": u,
            "trades": 0,
            "contracts": 0.0,
            "net_premium_base": 0.0,
            "realized_pnl_base": 0.0,
            "commissions_base": 0.0,
            "win": 0, "loss": 0, "be": 0,
        })
        b["trades"] += 1
        b["contracts"] += abs(_f(t.quantity) or 0.0)
        b["net_premium_base"] += _f(cash) or 0.0
        p = _f(pnl) or 0.0
        b["realized_pnl_base"] += p
        b["commissions_base"] += _f(comm) or 0.0
        if is_closed(t.open_close):
            if p > 0:
                b["win"] += 1
            elif p < 0:
                b["loss"] += 1
            else:
                b["be"] += 1

    rows = sorted(by_under.values(), key=lambda r: abs(r["realized_pnl_base"]), reverse=True)
    for r in rows:
        decided = r["win"] + r["loss"]
        r["win_rate"] = (r["win"] / decided) if decided > 0 else None
        r["contracts"] = int(r["contracts"])
    return {
        "rows": rows,
        "period": period.to_dict() if period else None,
    }


def _latest_stock_closes(session: Session, latest_snap: date) -> dict[str, float]:
    """Latest known EOD close by stock symbol for option moneyness checks."""
    stock_close: dict[str, float] = {}
    pos_q = (
        select(PositionEod.symbol, PositionEod.mark_price)
        .where(
            and_(
                PositionEod.snapshot_date == latest_snap,
                PositionEod.asset_class == "STK",
            )
        )
    )
    for sym, px in session.execute(pos_q):
        if sym and px is not None:
            stock_close[sym] = float(px)

    mtm_latest_per_sym = session.execute(
        select(
            MtmPerformanceUnderlying.symbol,
            func.max(MtmPerformanceUnderlying.snapshot_date),
        )
        .where(MtmPerformanceUnderlying.asset_class == "STK")
        .group_by(MtmPerformanceUnderlying.symbol)
    ).all()
    if mtm_latest_per_sym:
        date_by_sym = {s: d for s, d in mtm_latest_per_sym if s}
        mtm_q = (
            select(
                MtmPerformanceUnderlying.symbol,
                MtmPerformanceUnderlying.close_price,
                MtmPerformanceUnderlying.snapshot_date,
            )
            .where(MtmPerformanceUnderlying.asset_class == "STK")
        )
        for sym, px, d in session.execute(mtm_q):
            if sym and px is not None and date_by_sym.get(sym) == d:
                stock_close.setdefault(sym, float(px))

    return stock_close


def options_open_positions(
    session: Session,
    *,
    account_id: str | None = None,
) -> dict:
    """Latest open OPT positions, including long calls/puts and shorts."""
    latest_q = select(func.max(PositionEod.snapshot_date)).where(
        PositionEod.asset_class == "OPT"
    )
    if account_id:
        latest_q = latest_q.where(PositionEod.account_id == account_id)
    latest_snap = session.execute(latest_q).scalar()
    if latest_snap is None:
        return {
            "snapshot_date": None,
            "rows": [],
            "summary": {
                "positions": 0,
                "long_positions": 0,
                "short_positions": 0,
                "long_contracts": 0.0,
                "short_contracts": 0.0,
                "total_abs_exposure_base": 0.0,
                "net_position_value_base": 0.0,
                "unrealized_pnl_base": 0.0,
                "expiring_within_30d": 0,
                "expired": 0,
            },
        }

    q = (
        select(PositionEod, Instrument)
        .join(Instrument, Instrument.conid == PositionEod.conid, isouter=True)
        .where(
            and_(
                PositionEod.snapshot_date == latest_snap,
                PositionEod.asset_class == "OPT",
                PositionEod.quantity != 0,
            )
        )
    )
    if account_id:
        q = q.where(PositionEod.account_id == account_id)

    today = date.today()
    stock_close = _latest_stock_closes(session, latest_snap)
    rows = []
    for pos, instr in session.execute(q):
        contract = _enrich_option(pos.symbol, instr)
        qty = _f(pos.quantity) or 0.0
        multiplier = _f(pos.multiplier) or _f(instr.multiplier if instr else None) or 100.0
        fx = _f(pos.fx_rate_to_base) or 1.0
        native_value = _f(pos.position_value)
        if native_value is None:
            native_value = (_f(pos.mark_price) or 0.0) * qty * multiplier
        position_value_base = native_value * fx
        cost_basis_base = (
            (_f(pos.cost_basis_money) or 0.0) * fx
            if pos.cost_basis_money is not None
            else None
        )
        unrealized_base = (
            (_f(pos.fifo_pnl_unrealized) or 0.0) * fx
            if pos.fifo_pnl_unrealized is not None
            else None
        )
        cost_abs = abs(cost_basis_base or 0.0)
        unrealized_pct = (
            (unrealized_base / cost_abs)
            if unrealized_base is not None and cost_abs > 0
            else None
        )
        dte = (contract.expiry - today).days if contract.expiry else None
        last_close = stock_close.get(contract.underlying) if contract.underlying else None
        moneyness_pct = None
        if last_close is not None and contract.strike is not None and contract.strike > 0:
            if contract.right == "C":
                moneyness_pct = (last_close / contract.strike) - 1
            elif contract.right == "P":
                moneyness_pct = (contract.strike / last_close) - 1 if last_close > 0 else None

        rows.append({
            "symbol": pos.symbol,
            "description": instr.description if instr else None,
            "underlying": contract.underlying,
            "right": contract.right,
            "strike": contract.strike,
            "expiry": contract.expiry.isoformat() if contract.expiry else None,
            "dte": dte,
            "side": "long" if qty > 0 else "short",
            "quantity": qty,
            "contracts": abs(qty),
            "multiplier": multiplier,
            "mark_price": _f(pos.mark_price),
            "position_value_base": position_value_base,
            "cost_basis_money_base": cost_basis_base,
            "fifo_pnl_unrealized_base": unrealized_base,
            "unrealized_pct": unrealized_pct,
            "pct_of_nav": _f(pos.pct_of_nav),
            "last_underlying_close": last_close,
            "moneyness_pct": moneyness_pct,
            "currency": pos.currency,
        })

    rows.sort(key=lambda r: (
        r["dte"] if r["dte"] is not None else 9999,
        r["underlying"] or r["symbol"] or "",
        r["strike"] or 0,
    ))

    return {
        "snapshot_date": latest_snap.isoformat(),
        "rows": rows,
        "summary": {
            "positions": len(rows),
            "long_positions": sum(1 for r in rows if r["side"] == "long"),
            "short_positions": sum(1 for r in rows if r["side"] == "short"),
            "long_contracts": sum(r["contracts"] for r in rows if r["side"] == "long"),
            "short_contracts": sum(r["contracts"] for r in rows if r["side"] == "short"),
            "total_abs_exposure_base": sum(abs(r["position_value_base"]) for r in rows),
            "net_position_value_base": sum(r["position_value_base"] for r in rows),
            "unrealized_pnl_base": sum(
                r["fifo_pnl_unrealized_base"] or 0.0 for r in rows
            ),
            "expiring_within_30d": sum(
                1 for r in rows if r["dte"] is not None and 0 <= r["dte"] <= 30
            ),
            "expired": sum(1 for r in rows if r["dte"] is not None and r["dte"] < 0),
        },
    }


def options_assignment_risk(
    session: Session,
    *,
    account_id: str | None = None,
) -> dict:
    """Snapshot of open SHORT option positions vs latest known underlying close.

    Uses the most recent PositionEod snapshot. For each short option,
    looks up the latest stock close (PositionEod with matching underlying)
    so the user can see ITM/OTM and intrinsic value without live quotes.
    """
    latest_snap = session.execute(
        select(func.max(PositionEod.snapshot_date)).where(
            PositionEod.asset_class == "OPT"
        )
    ).scalar()
    if latest_snap is None:
        return {"snapshot_date": None, "rows": [], "summary": {"shorts": 0}}

    q = (
        select(PositionEod, Instrument)
        .join(Instrument, Instrument.conid == PositionEod.conid, isouter=True)
        .where(
            and_(
                PositionEod.snapshot_date == latest_snap,
                PositionEod.asset_class == "OPT",
                PositionEod.quantity < 0,
            )
        )
    )
    if account_id:
        q = q.where(PositionEod.account_id == account_id)

    stock_close = _latest_stock_closes(session, latest_snap)

    rows = []
    today = date.today()
    for pos, instr in session.execute(q):
        c = _enrich_option(pos.symbol, instr)
        und = c.underlying
        last_close = stock_close.get(und) if und else None
        dte = (c.expiry - today).days if c.expiry else None
        itm = None
        intrinsic = None
        if last_close is not None and c.strike is not None and c.right:
            if c.right == "P":
                itm = last_close < c.strike
                intrinsic = max(0.0, c.strike - last_close)
            else:  # C
                itm = last_close > c.strike
                intrinsic = max(0.0, last_close - c.strike)
        rows.append({
            "symbol": pos.symbol,
            "underlying": und,
            "right": c.right,
            "strike": c.strike,
            "expiry": c.expiry.isoformat() if c.expiry else None,
            "dte": dte,
            "quantity": _f(pos.quantity),
            "multiplier": _f(pos.multiplier),
            "mark_price": _f(pos.mark_price),
            "position_value_base": (
                (_f(pos.position_value) or 0.0)
                * (_f(pos.fx_rate_to_base) or 1.0)
            ),
            "last_underlying_close": last_close,
            "itm": itm,
            "intrinsic_per_contract": intrinsic,
            "intrinsic_total_base": (
                (intrinsic or 0.0)
                * abs(_f(pos.quantity) or 0.0)
                * (_f(pos.multiplier) or 100.0)
                * (_f(pos.fx_rate_to_base) or 1.0)
            ),
            "currency": pos.currency,
        })

    rows.sort(key=lambda r: (r["dte"] if r["dte"] is not None else 9999, r["expiry"] or ""))
    return {
        "snapshot_date": latest_snap.isoformat(),
        "rows": rows,
        "summary": {
            "shorts": len(rows),
            "itm": sum(1 for r in rows if r["itm"]),
            "expiring_within_7d": sum(1 for r in rows if r["dte"] is not None and r["dte"] <= 7),
            "intrinsic_at_risk_base": sum(
                r["intrinsic_total_base"] for r in rows if r["itm"]
            ),
        },
    }


def options_concentration(
    session: Session,
    *,
    account_id: str | None = None,
) -> dict:
    """HHI concentration of the open option book by underlying.

    Uses the latest EOD OPT snapshot and absolute market value per underlying
    as the exposure weight. Short options carry negative market value in
    IBKR's `position_value`, so absolute value is the right concentration
    denominator; net value is still surfaced for context.
    """
    latest_q = select(func.max(PositionEod.snapshot_date)).where(
        PositionEod.asset_class == "OPT"
    )
    if account_id:
        latest_q = latest_q.where(PositionEod.account_id == account_id)
    latest_snap = session.execute(latest_q).scalar()
    if latest_snap is None:
        return {
            "snapshot_date": None,
            "rows": [],
            "summary": {
                "positions": 0,
                "underlyings": 0,
                "total_abs_exposure_base": 0.0,
                "net_position_value_base": 0.0,
                "hhi": None,
                "effective_names": None,
                "top_underlying": None,
                "top_share": None,
            },
        }

    q = (
        select(PositionEod, Instrument)
        .join(Instrument, Instrument.conid == PositionEod.conid, isouter=True)
        .where(
            and_(
                PositionEod.snapshot_date == latest_snap,
                PositionEod.asset_class == "OPT",
            )
        )
    )
    if account_id:
        q = q.where(PositionEod.account_id == account_id)

    by_underlying: dict[str, dict] = {}
    for pos, instr in session.execute(q):
        fx = _f(pos.fx_rate_to_base) or 1.0
        position_value = _f(pos.position_value)
        if position_value is None:
            position_value = (
                (_f(pos.mark_price) or 0.0)
                * (_f(pos.quantity) or 0.0)
                * (_f(pos.multiplier) or 100.0)
            )
        exposure_base = position_value * fx
        abs_exposure = abs(exposure_base)
        if abs_exposure == 0:
            continue

        contract = _enrich_option(pos.symbol, instr)
        underlying = contract.underlying or pos.symbol or (instr.symbol if instr else None) or "UNKNOWN"
        bucket = by_underlying.setdefault(
            underlying,
            {
                "underlying": underlying,
                "positions": 0,
                "contracts": 0.0,
                "net_position_value_base": 0.0,
                "abs_exposure_base": 0.0,
                "exposure_share": None,
            },
        )
        bucket["positions"] += 1
        bucket["contracts"] += abs(_f(pos.quantity) or 0.0)
        bucket["net_position_value_base"] += exposure_base
        bucket["abs_exposure_base"] += abs_exposure

    rows = sorted(
        by_underlying.values(),
        key=lambda row: row["abs_exposure_base"],
        reverse=True,
    )
    total_abs = sum(row["abs_exposure_base"] for row in rows)
    net_value = sum(row["net_position_value_base"] for row in rows)
    hhi = None
    effective_names = None
    if total_abs > 0:
        hhi = 0.0
        for row in rows:
            share = row["abs_exposure_base"] / total_abs
            row["exposure_share"] = share
            row["contracts"] = (
                int(row["contracts"])
                if float(row["contracts"]).is_integer()
                else row["contracts"]
            )
            hhi += share * share
        effective_names = (1 / hhi) if hhi > 0 else None

    top = rows[0] if rows else None
    return {
        "snapshot_date": latest_snap.isoformat(),
        "rows": rows,
        "summary": {
            "positions": sum(row["positions"] for row in rows),
            "underlyings": len(rows),
            "total_abs_exposure_base": total_abs,
            "net_position_value_base": net_value,
            "hhi": hhi,
            "effective_names": effective_names,
            "top_underlying": top["underlying"] if top else None,
            "top_share": top["exposure_share"] if top else None,
        },
    }


_DTE_BUCKETS: tuple[tuple[str, int | None, int | None], ...] = (
    # (label, lower_inclusive, upper_inclusive). None = unbounded on that side.
    ("safe", 46, None),       # > 45 DTE — entry zone, no management pressure
    ("watch", 22, 45),        # 22–45 DTE — tastytrade's "sweet spot" before
                              #             gamma starts dominating theta
    ("manage", 8, 21),        # ≤ 21 DTE — tastytrade "manage at 21" doctrine
    ("critical", 0, 7),       # ≤ 7 DTE — assignment-risk window
)


def _dte_bucket(dte: int | None) -> str | None:
    if dte is None or dte < 0:
        return None
    for label, lo, hi in _DTE_BUCKETS:
        if (lo is None or dte >= lo) and (hi is None or dte <= hi):
            return label
    return None


def options_dte_buckets(
    session: Session,
    *,
    account_id: str | None = None,
) -> dict:
    """Bucket the user's currently open SHORT option positions by DTE so the
    UI can flag the ones in tastytrade's manage zone (≤21 DTE) — the most-
    cited retail short-premium discipline. Reads the latest PositionEod
    snapshot; counts negative-qty OPT rows only (shorts).
    """
    latest_snap = session.execute(
        select(func.max(PositionEod.snapshot_date)).where(
            PositionEod.asset_class == "OPT"
        )
    ).scalar()

    bucket_counts: dict[str, int] = {label: 0 for label, _, _ in _DTE_BUCKETS}
    bucket_positions: dict[str, list[dict]] = {label: [] for label, _, _ in _DTE_BUCKETS}

    if latest_snap is None:
        return {
            "snapshot_date": None,
            "buckets": [
                {"label": label, "lo": lo, "hi": hi, "count": 0, "positions": []}
                for label, lo, hi in _DTE_BUCKETS
            ],
            "summary": {"shorts_open": 0, "manage_count": 0, "expired_skipped": 0},
        }

    q = (
        select(PositionEod, Instrument)
        .join(Instrument, Instrument.conid == PositionEod.conid, isouter=True)
        .where(
            and_(
                PositionEod.snapshot_date == latest_snap,
                PositionEod.asset_class == "OPT",
                PositionEod.quantity < 0,
            )
        )
    )
    if account_id:
        q = q.where(PositionEod.account_id == account_id)

    today = date.today()
    shorts_total = 0
    expired_skipped = 0
    for pos, instr in session.execute(q):
        shorts_total += 1
        c = _enrich_option(pos.symbol, instr)
        dte = (c.expiry - today).days if c.expiry else None
        if dte is None or dte < 0:
            expired_skipped += 1
            continue
        label = _dte_bucket(dte)
        if label is None:
            continue
        bucket_counts[label] += 1
        bucket_positions[label].append({
            "symbol": pos.symbol,
            "underlying": c.underlying,
            "right": c.right,
            "strike": c.strike,
            "expiry": c.expiry.isoformat() if c.expiry else None,
            "dte": dte,
            "quantity": _f(pos.quantity),
        })

    for ps in bucket_positions.values():
        ps.sort(key=lambda r: r["dte"])

    manage_count = bucket_counts.get("manage", 0) + bucket_counts.get("critical", 0)
    return {
        "snapshot_date": latest_snap.isoformat(),
        "buckets": [
            {"label": label, "lo": lo, "hi": hi,
             "count": bucket_counts[label], "positions": bucket_positions[label]}
            for label, lo, hi in _DTE_BUCKETS
        ],
        "summary": {
            "shorts_open": shorts_total,
            "manage_count": manage_count,
            "expired_skipped": expired_skipped,
        },
    }


def options_assignment_rate(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> dict:
    """Per-underlying disposal breakdown of OPT positions: how each option
    contract ended (bought/sold to close, expired worthless, exercised,
    assigned). The wheel community's centerpiece metric — see
    u/ScottishTrader's "Wheel — 7 years of data" template.

    `stock_transfer_rate = (exercise + assignment) / total_disposals` is
    the "got picked off" rate; `expiration_rate` is the cleanest "max
    profit captured" path; `manual_close_rate` is everything else (you
    closed it yourself).

    Notes:
      * EAE events are normalised by substring (IBKR labels them
        "Exercise", "Assignment", "Expiration", sometimes with suffixes).
      * "Buy/Sell" EAE rows are the resulting stock leg, excluded from
        disposal counts.
      * Bought/sold-to-close come from `Trade.open_close LIKE 'C%'`
        (catches compound codes) on OPT rows.
    """
    eae_q = (
        select(OptionExerciseAssignment, Instrument)
        .join(Instrument, Instrument.conid == OptionExerciseAssignment.conid, isouter=True)
    )
    eae_q = _apply_period(eae_q, OptionExerciseAssignment.event_date, period)
    if account_id:
        eae_q = eae_q.where(OptionExerciseAssignment.account_id == account_id)

    by_und: dict[str, dict] = {}
    def _bucket(und: str) -> dict:
        return by_und.setdefault(und, {
            "underlying": und,
            "exercise": 0,
            "assignment": 0,
            "expiration": 0,
            "bought_to_close": 0,
            "sold_to_close": 0,
        })

    for e, instr in session.execute(eae_q):
        ttype = (e.transaction_type or "").lower()
        c = _enrich_option(e.symbol, instr)
        und = c.underlying or e.underlying_symbol
        if not und:
            continue
        b = _bucket(und)
        if "exerc" in ttype:
            b["exercise"] += 1
        elif "assign" in ttype:
            b["assignment"] += 1
        elif "expir" in ttype:
            b["expiration"] += 1
        # "Buy" / "Sell" EAE rows are the resulting stock leg, skip.

    trade_q = (
        select(Trade, Instrument)
        .join(Instrument, Instrument.conid == Trade.conid, isouter=True)
        .where(and_(Trade.asset_class == "OPT", is_closed_filter()))
    )
    trade_q = _apply_period(trade_q, Trade.trade_date, period)
    if account_id:
        trade_q = trade_q.where(Trade.account_id == account_id)
    for t, instr in session.execute(trade_q):
        c = _enrich_option(t.symbol, instr)
        und = c.underlying
        if not und:
            continue
        b = _bucket(und)
        if (t.buy_sell or "").upper() == "BUY":
            b["bought_to_close"] += 1
        elif (t.buy_sell or "").upper() == "SELL":
            b["sold_to_close"] += 1

    rows = []
    totals = {
        "exercise": 0, "assignment": 0, "expiration": 0,
        "bought_to_close": 0, "sold_to_close": 0,
    }
    for b in by_und.values():
        total = b["exercise"] + b["assignment"] + b["expiration"] + b["bought_to_close"] + b["sold_to_close"]
        b["total_disposals"] = total
        b["stock_transfer_rate"] = ((b["exercise"] + b["assignment"]) / total) if total > 0 else None
        b["expiration_rate"] = (b["expiration"] / total) if total > 0 else None
        b["manual_close_rate"] = ((b["bought_to_close"] + b["sold_to_close"]) / total) if total > 0 else None
        rows.append(b)
        for k in totals:
            totals[k] += b[k]
    rows.sort(key=lambda r: r["total_disposals"], reverse=True)

    grand_total = sum(totals.values())
    totals["total_disposals"] = grand_total
    totals["stock_transfer_rate"] = (
        (totals["exercise"] + totals["assignment"]) / grand_total if grand_total > 0 else None
    )
    totals["expiration_rate"] = (totals["expiration"] / grand_total) if grand_total > 0 else None
    totals["manual_close_rate"] = (
        (totals["bought_to_close"] + totals["sold_to_close"]) / grand_total if grand_total > 0 else None
    )

    return {
        "period": period.to_dict() if period else None,
        "by_underlying": rows,
        "totals": totals,
    }


def options_pnl_attribution(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> dict:
    """P&L decomposition for a wheel/short-premium trader.

    Sinclair *Positional Option Trading* ch. 11 and Hull ch. 19 stress
    decomposing total realized P&L before any "skill" claim. A wheel
    trader's apparent options gains are often a mix of: short-premium
    edge (the actual options skill), capital gains on assigned shares
    (directional, not vol-selling), dividends collected while holding
    assigned shares, and interest on idle cash. Conflating them
    overstates the options "edge."

    Decomposition:
      * **short_premium_base** — sum of closed OPT trades' realized P&L
      * **wheel_stock_base** — closed STK realized P&L on underlyings that
        also have OPT activity (the assigned-stock side of the wheel)
      * **wheel_dividends_base** — net dividends (gross + withholding) on
        the same wheel underlyings
      * **wheel_withholding_base** — withholding-tax component of the above
        (surfaced separately so the user sees the drag)
      * **non_options_stock_base** — closed STK realized P&L on underlyings
        WITHOUT options activity (pure equity plays)
      * **non_options_dividends_base** — net dividends on those names
      * **interest_base** — interest received − interest paid (account-wide)
      * **grand_total_base** — sum of all components

    Per-underlying breakdown (`by_underlying`) only includes wheel-related
    names; non-options stocks roll up into the totals block but not the
    table, since they're outside the strategy scope.
    """
    # OPT closed realized P&L by parsed underlying. Join Instrument so the
    # OCC parser can fall back to `description` when `symbol` lacks the OSI
    # form — otherwise valid rows where IBKR Flex emits the human description
    # but not the OSI on `symbol` get silently dropped.
    # `open_close LIKE 'C%'` catches IBKR's compound
    # close codes (e.g. 'C;IA', 'C;P', 'C;FPA;P') — any prefix-C means closed.
    opt_q = (
        select(Trade, Instrument)
        .join(Instrument, Instrument.conid == Trade.conid, isouter=True)
        .where(and_(Trade.asset_class == "OPT", is_closed_filter()))
    )
    opt_q = _apply_period(opt_q, Trade.trade_date, period)
    if account_id:
        opt_q = opt_q.where(Trade.account_id == account_id)

    opt_pnl_by_und: dict[str, float] = {}
    for t, instr in session.execute(opt_q):
        c = _enrich_option(t.symbol, instr)
        und = c.underlying
        if not und:
            continue
        pnl = float(t.fifo_pnl_realized or 0) * float(t.fx_rate_to_base or 1)
        opt_pnl_by_und[und] = opt_pnl_by_und.get(und, 0.0) + pnl

    wheel_underlyings = set(opt_pnl_by_und.keys())

    # STK closed realized P&L by symbol, in base ccy. `open_close LIKE 'C%'`
    # catches compound close codes observed in real exports, including C,
    # C;IA, C;P, C;FP, C;FPA;P, and C;IM;P.
    stk_q = (
        select(Trade.symbol, func.sum(
            Trade.fifo_pnl_realized * func.coalesce(Trade.fx_rate_to_base, 1)
        ).label("pnl_base"))
        .where(and_(Trade.asset_class == "STK", is_closed_filter()))
        .group_by(Trade.symbol)
    )
    stk_q = _apply_period(stk_q, Trade.trade_date, period)
    if account_id:
        stk_q = stk_q.where(Trade.account_id == account_id)

    stk_pnl_by_sym: dict[str, float] = {}
    for sym, pnl in session.execute(stk_q):
        if not sym:
            continue
        stk_pnl_by_sym[sym] = float(pnl or 0)

    # Dividends + withholding by symbol, in base ccy. Net = gross + wht.
    # IBKR Flex usually leaves `CashTransaction.symbol` and `.conid` NULL on
    # dividend/WHT rows and packs the ticker into `description`; without
    # parsing it back out, every per-symbol roll-up can incorrectly sum to 0.
    div_q = (
        select(
            CashTransaction.symbol,
            CashTransaction.description,
            CashTransaction.type,
            CashTransaction.amount,
            CashTransaction.fx_rate_to_base,
        )
        .where(CashTransaction.type.in_(DIVIDEND_TYPES + WITHHOLDING_TYPES))
    )
    div_q = _apply_period(div_q, CashTransaction.settle_date, period)
    if account_id:
        div_q = div_q.where(CashTransaction.account_id == account_id)

    div_gross_by_sym: dict[str, float] = {}
    wht_by_sym: dict[str, float] = {}
    for sym, desc, ttype, amt, fx in session.execute(div_q):
        resolved_sym = sym or dividend_symbol_from_description(desc)
        if not resolved_sym:
            continue
        amt_base = float(amt or 0) * float(fx or 1)
        if ttype in DIVIDEND_TYPES:
            div_gross_by_sym[resolved_sym] = div_gross_by_sym.get(resolved_sym, 0.0) + amt_base
        elif ttype in WITHHOLDING_TYPES:
            wht_by_sym[resolved_sym] = wht_by_sym.get(resolved_sym, 0.0) + amt_base

    # Interest: account-wide, no symbol attribution.
    int_q = select(
        CashTransaction.type,
        func.sum(
            CashTransaction.amount * func.coalesce(CashTransaction.fx_rate_to_base, 1)
        ).label("amt_base"),
    ).where(
        CashTransaction.type.in_(INTEREST_PAID_TYPES + INTEREST_RECV_TYPES)
    ).group_by(CashTransaction.type)
    int_q = _apply_period(int_q, CashTransaction.settle_date, period)
    if account_id:
        int_q = int_q.where(CashTransaction.account_id == account_id)
    interest_base = 0.0
    for _ttype, amt in session.execute(int_q):
        interest_base += float(amt or 0)

    # Roll up.
    wheel_stock_base = sum(p for s, p in stk_pnl_by_sym.items() if s in wheel_underlyings)
    non_options_stock_base = sum(p for s, p in stk_pnl_by_sym.items() if s not in wheel_underlyings)

    wheel_div_gross = sum(v for s, v in div_gross_by_sym.items() if s in wheel_underlyings)
    wheel_wht = sum(v for s, v in wht_by_sym.items() if s in wheel_underlyings)
    wheel_dividends_base = wheel_div_gross + wheel_wht  # wht already negative

    non_options_div_gross = sum(v for s, v in div_gross_by_sym.items() if s not in wheel_underlyings)
    non_options_wht = sum(v for s, v in wht_by_sym.items() if s not in wheel_underlyings)
    non_options_dividends_base = non_options_div_gross + non_options_wht

    short_premium_base = sum(opt_pnl_by_und.values())

    grand_total = (
        short_premium_base
        + wheel_stock_base
        + wheel_dividends_base
        + non_options_stock_base
        + non_options_dividends_base
        + interest_base
    )

    # Per-underlying breakdown for wheel-related names. Sorted by total desc.
    by_underlying = []
    for und in sorted(wheel_underlyings):
        opt_p = opt_pnl_by_und.get(und, 0.0)
        stk_p = stk_pnl_by_sym.get(und, 0.0)
        div_g = div_gross_by_sym.get(und, 0.0)
        wht_v = wht_by_sym.get(und, 0.0)
        div_net = div_g + wht_v
        total = opt_p + stk_p + div_net
        by_underlying.append({
            "underlying": und,
            "opt_pnl_base": opt_p,
            "stk_pnl_base": stk_p,
            "dividends_gross_base": div_g,
            "withholding_base": wht_v,
            "dividends_net_base": div_net,
            "total_base": total,
        })
    by_underlying.sort(key=lambda r: r["total_base"], reverse=True)

    return {
        "period": period.to_dict() if period else None,
        "by_underlying": by_underlying,
        "totals": {
            "short_premium_base": short_premium_base,
            "wheel_stock_base": wheel_stock_base,
            "wheel_dividends_base": wheel_dividends_base,
            "wheel_withholding_base": wheel_wht,
            "non_options_stock_base": non_options_stock_base,
            "non_options_dividends_base": non_options_dividends_base,
            "interest_base": interest_base,
            "grand_total_base": grand_total,
        },
    }


def options_summary(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> dict:
    """KPI block consumed by the Opciones page header."""
    trades = options_trades_list(
        session, period=period, account_id=account_id, limit=10**6
    )
    risk = options_assignment_risk(session, account_id=account_id)
    dte = options_dte_buckets(session, account_id=account_id)
    return {
        "period": period.to_dict() if period else None,
        "trades": trades["summary"],
        "open_risk": risk["summary"],
        "dte": dte["summary"],
        "snapshot_date": risk["snapshot_date"],
    }


__all__ = [
    "OptionContract",
    "enrich_option",
    "parse_occ_symbol",
    "parse_option_description",
    "options_assignment_rate",
    "options_assignment_risk",
    "options_concentration",
    "options_dte_buckets",
    "options_eae_list",
    "options_open_positions",
    "options_pnl_attribution",
    "options_pnl_by_underlying",
    "options_premium_timeseries",
    "options_summary",
    "options_trades_list",
]
