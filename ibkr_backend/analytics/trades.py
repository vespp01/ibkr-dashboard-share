"""Trade-level analytics — per-trade list + multi-leg strategy classification.

Extracted from `metrics.py` (god-module split — same pattern as
`dividends_calc` / `commissions` / `interest`). Code moved verbatim;
`metrics.py` re-exports every name for backward compat.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import CashTransaction, Instrument, Trade
from ._common import (
    _apply_period,
    _f,
    is_closed,
    is_closed_filter,
    is_open,
    is_open_filter,
)
from .dividends_calc import DIVIDEND_TYPES
from .filters import PeriodFilter
from .options_parsing import OptionContract, enrich_option


# ====================================================================
# TRADES (per-trade list)
# ====================================================================
# Option-symbol parsing is delegated to `options_parsing` (single OCC/OSI
# parser shared with the Opciones tab — see `enrich_option`). The former
# local `_parse_option_symbol` duplicate was removed; `enrich_option` is
# strictly more capable: it validates the expiry as a real calendar date,
# caps the root at 6 chars per the OSI standard, accepts space-embedded
# roots ("BRK B"), and falls back to IBKR's human form
# ("DRAM 17JUN27 40 P") on symbol or description.


def _signed_qty(t: Trade) -> int:
    """Net signed quantity. BUY → +qty, SELL → -qty. The `quantity` column is
    occasionally already signed in older Flex variants, so we trust the sign
    when it disagrees with `buy_sell` (defensive — should not happen on EOD)."""
    q = float(t.quantity or 0)
    side = (t.buy_sell or "").upper()
    if side == "BUY" and q != 0:
        return int(q) if q > 0 else -int(abs(q))
    if side == "SELL" and q != 0:
        return -int(abs(q)) if q > 0 else int(q)
    return int(q)


def _enrich_option(symbol: str | None, instr: Instrument | None) -> OptionContract:
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


def _classify_multi_leg(group: list[tuple[Trade, Instrument | None]]) -> str | None:
    """Return a recognised multi-leg strategy label for a group of trades that
    share the same ``ib_order_id`` and ``trade_date``, or ``None`` to defer to
    the generic ``Multi-leg (N)`` fallback.

    Patterns recognised:
      * **Covered call** — long stock + short call on same underlying
      * **Protective put** — long stock + long put on same underlying
      * **Vertical spread** — 2 options, same underlying & expiry, same P/C,
        2 strikes, opposite sides (BUY one strike + SELL the other)
      * **Straddle** — 2 options, same expiry & strike, one call + one put,
        same side (both BUY or both SELL)
      * **Strangle** — 2 options, same expiry, different strikes, one call +
        one put, same side
      * **Calendar spread** — 2 options, same underlying, same strike, same
        P/C, different expiries (BUY one, SELL the other)
      * **Diagonal spread** — 2 options, same underlying, same P/C, different
        strikes AND different expiries
      * **Iron condor** — 4 options same expiry: 2 calls forming a call
        vertical above ATM + 2 puts forming a put vertical below ATM
      * **Iron butterfly** — 4 options same expiry: a call vertical and put
        vertical sharing the middle strike (one common short strike)
    """
    if len(group) < 2:
        return None

    legs = []
    for t, instr in group:
        ac = (t.asset_class or "").upper()
        if ac == "STK":
            legs.append(("STK", t.symbol, None, None, None, _signed_qty(t)))
        elif ac == "OPT":
            c = _enrich_option(t.symbol, instr)
            if not (c.underlying and c.expiry and c.right and c.strike is not None):
                return None  # unparseable option → bail out, generic label
            legs.append(("OPT", c.underlying, c.expiry, c.right, c.strike, _signed_qty(t)))
        else:
            return None  # cross-asset beyond stock+option → unknown

    stocks = [leg for leg in legs if leg[0] == "STK"]
    opts = [leg for leg in legs if leg[0] == "OPT"]

    # ---- stock + option combinations ----
    if len(legs) == 2 and len(stocks) == 1 and len(opts) == 1:
        stk = stocks[0]
        opt = opts[0]
        # Underlying must match — IBKR can pack unrelated instruments under the
        # same `ib_order_id` via block/combo encoding; we refuse to label those.
        if stk[1] != opt[1]:
            return None
        if stk[5] > 0 and opt[3] == "C" and opt[5] < 0:
            return "Covered call"
        if stk[5] > 0 and opt[3] == "P" and opt[5] > 0:
            return "Protective put"
        return None  # other stock+opt combos: defer to generic

    # ---- pure option strategies ----
    if not opts or len(opts) != len(legs):
        return None  # any non-option besides the stock cases above → unknown
    unds = {leg[1] for leg in opts}
    if len(unds) > 1:
        return None  # cross-underlying basket — not a standard strategy

    exps = {leg[2] for leg in opts}
    pcs = [leg[3] for leg in opts]

    if len(opts) == 2:
        a, b = opts
        # 2-leg cases — disambiguate by expiry + strike + P/C + sides.
        same_exp = a[2] == b[2]
        same_pc = a[3] == b[3]
        same_strike = a[4] == b[4]
        opposite_sides = (a[5] > 0) != (b[5] > 0)
        if same_exp and same_pc and not same_strike and opposite_sides:
            return "Vertical spread"
        if same_exp and not same_pc and same_strike and not opposite_sides:
            return "Straddle"
        if same_exp and not same_pc and not same_strike and not opposite_sides:
            return "Strangle"
        if not same_exp and same_pc and same_strike and opposite_sides:
            return "Calendar spread"
        if not same_exp and same_pc and not same_strike and opposite_sides:
            return "Diagonal spread"
        return None

    if len(opts) == 4 and len(exps) == 1:
        calls = sorted([leg for leg in opts if leg[3] == "C"], key=lambda x: x[4])
        puts = sorted([leg for leg in opts if leg[3] == "P"], key=lambda x: x[4])
        if len(calls) != 2 or len(puts) != 2:
            return None
        call_strikes = {leg[4] for leg in calls}
        put_strikes = {leg[4] for leg in puts}
        # Each pair must form a vertical (one BUY + one SELL).
        if (calls[0][5] > 0) == (calls[1][5] > 0):
            return None
        if (puts[0][5] > 0) == (puts[1][5] > 0):
            return None
        # Iron butterfly: the short call and short put share one strike.
        short_call_strike = next(leg[4] for leg in calls if leg[5] < 0)
        short_put_strike = next(leg[4] for leg in puts if leg[5] < 0)
        if short_call_strike == short_put_strike and len(call_strikes | put_strikes) == 3:
            return "Iron butterfly"
        # Iron condor: 4 distinct strikes, put strikes below call strikes.
        if len(call_strikes | put_strikes) == 4 and max(put_strikes) < min(call_strikes):
            return "Iron condor"
        return None

    return None


def _strategy_hint(t: Trade, instr: Instrument | None) -> str:
    """Single-leg classification after multi-leg grouping.

    Labels follow the position the row belongs to, taken from `open_close`
    (Flex reports SELL quantities as negative on every sale, so the sign says
    nothing about shorting): a closing BUY ends a short, a closing SELL ends a
    long. A row without `open_close` is read as an opening row.
    """
    ac = (t.asset_class or "").upper()
    side = (t.buy_sell or "").upper()
    closing = is_closed(t.open_close)
    # Position side the row belongs to.
    short_position = (side == "SELL" and not closing) or (side == "BUY" and closing)
    if ac == "STK":
        if not t.open_close:
            return "Stock long" if side == "BUY" else "Stock sell" if side == "SELL" else "Stock"
        if side == "BUY":
            return "Stock cover" if closing else "Stock long"
        if side == "SELL":
            return "Stock close" if closing else "Stock short"
        return "Stock"
    if ac == "OPT":
        # IBKR option symbols don't directly carry put/call in the Trade row,
        # but Instrument.put_call (if populated) does. Fall back to symbol parsing.
        pc = getattr(instr, "put_call", None) if instr else None
        opt_type = (pc or "").upper()
        if not opt_type:
            opt_type = _enrich_option(t.symbol, instr).right or ""
        leg = "put" if opt_type == "P" else "call" if opt_type == "C" else "option"
        if side not in ("BUY", "SELL"):
            return f"Option {leg}"
        return f"{'Short' if short_position else 'Long'} {leg}"
    if ac in ("FUT", "FOP"):
        return f"{ac} {side.lower()}".strip()
    if ac == "CASH":
        return "FX"
    return ac or "Trade"


def multi_leg_labels(
    session: Session,
    order_keys: set[tuple[str, date, str]],
) -> tuple[dict[str, str], dict[str, int]]:
    """Strategy label and leg count per trade_id for multi-leg orders.

    `order_keys` are `(ib_order_id, trade_date, account_id)` triples. Every
    trade sharing a key is fetched (bypassing any asset-class filter so stock +
    option combos classify). A group is multi-leg only with at least two
    distinct conids: several fills of one contract are one leg. Fills of the
    same conid are merged before classification.
    """
    labels: dict[str, str] = {}
    legs_count: dict[str, int] = {}
    if not order_keys:
        return labels, legs_count
    order_ids = {oid for oid, _, _ in order_keys}
    sibling_q = (
        select(Trade, Instrument)
        .join(Instrument, Instrument.conid == Trade.conid, isouter=True)
        .where(Trade.ib_order_id.in_(order_ids))
    )
    groups: dict[tuple[str, date, str], list[tuple[Trade, Instrument | None]]] = {}
    for t, instr in session.execute(sibling_q):
        if not (t.ib_order_id and t.trade_date and t.account_id):
            continue
        groups.setdefault((t.ib_order_id, t.trade_date, t.account_id), []).append((t, instr))
    for key, group in groups.items():
        if key not in order_keys:
            continue
        legs: dict[int, tuple[Trade, Instrument | None]] = {}
        for t, instr in group:
            legs.setdefault(t.conid, (t, instr))  # first fill represents the leg
        if len(legs) < 2:
            continue
        label = _classify_multi_leg(list(legs.values())) or f"Multi-leg ({len(legs)})"
        for t, _ in group:
            labels[t.trade_id] = label
            legs_count[t.trade_id] = len(legs)
    return labels, legs_count


def trades_list(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    symbol: str | None = None,
    asset_class: str | None = None,
    strategy: str | None = None,
    status: str = "all",
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """Return per-trade rows with derived strategy + win/loss classification.

    `status`: 'all' | 'open' | 'closed'
      - 'open'  := open_close == 'O' (or null) and fifo_pnl_realized is null/zero
      - 'closed':= open_close == 'C' and fifo_pnl_realized is not null
    `strategy`: exact strategy label; filtered on the server BEFORE paging.

    The summary win/loss/BE statistics are always taken over closed trades
    (never over opening fills, whose P&L is 0), one result per order: fills of
    the same `ib_order_id` are summed. `open_count` counts opening fills and
    `closed_count` closed orders under the same filters.
    """
    base_pnl = func.coalesce(Trade.fifo_pnl_realized * func.coalesce(Trade.fx_rate_to_base, 1), 0)
    base_proceeds = func.coalesce(Trade.proceeds * func.coalesce(Trade.fx_rate_to_base, 1), 0)
    base_commission = func.coalesce(Trade.ib_commission * func.coalesce(Trade.fx_rate_to_base, 1), 0)

    def scope(q):
        """Period / account / symbol / asset-class filters shared by every query."""
        q = _apply_period(q, Trade.trade_date, period)
        if account_id:
            q = q.where(Trade.account_id == account_id)
        if symbol:
            q = q.where(Trade.symbol == symbol)
        if asset_class:
            q = q.where(Trade.asset_class == asset_class)
        return q

    s = (status or "all").lower()

    # Strategy labels need sibling legs of the same order, so they are computed
    # in Python over the whole filtered set (a few thousand rows at most), which
    # also yields the list of selectable strategies and the server-side filter.
    label_rows = list(session.execute(
        scope(select(Trade, Instrument).join(Instrument, Instrument.conid == Trade.conid, isouter=True))
    ))
    multi_label, multi_legs = multi_leg_labels(session, {
        (t.ib_order_id, t.trade_date, t.account_id)
        for t, _ in label_rows
        if t.ib_order_id and t.trade_date and t.account_id
    })
    label_by_trade = {
        t.trade_id: multi_label.get(t.trade_id) or _strategy_hint(t, instr)
        for t, instr in label_rows
    }
    strategies = sorted(set(label_by_trade.values()))
    strategy_ids = (
        [tid for tid, lab in label_by_trade.items() if lab == strategy] if strategy else None
    )

    def scoped(q):
        q = scope(q)
        if strategy_ids is not None:
            q = q.where(Trade.trade_id.in_(strategy_ids))
        return q

    # Closed statistics: one result per order, independent of `status`.
    order_key = func.coalesce(Trade.ib_order_id, Trade.trade_id)
    orders = (
        scoped(select(func.sum(base_pnl).label("pnl")).where(is_closed_filter()))
        .group_by(Trade.account_id, order_key)
        .subquery()
    )
    win_count, win_sum = session.execute(
        select(func.count(), func.coalesce(func.sum(orders.c.pnl), 0)).where(orders.c.pnl > 0)
    ).one()
    loss_count, loss_sum = session.execute(
        select(func.count(), func.coalesce(func.sum(orders.c.pnl), 0)).where(orders.c.pnl < 0)
    ).one()
    be_count = session.execute(select(func.count()).where(orders.c.pnl == 0)).scalar() or 0
    realized_total = float(win_sum or 0) + float(loss_sum or 0)
    open_count = session.execute(
        scoped(select(func.count(Trade.trade_id)).where(is_open_filter()))
    ).scalar() or 0

    decided = int(win_count) + int(loss_count)
    win_rate = (float(win_count) / decided) if decided > 0 else None
    loss_total = abs(float(loss_sum))
    profit_factor = (float(win_sum) / loss_total) if loss_total > 0 else None
    avg_winner = (float(win_sum) / int(win_count)) if int(win_count) > 0 else None
    avg_loser = (float(loss_sum) / int(loss_count)) if int(loss_count) > 0 else None

    q = scoped(
        select(
            Trade,
            Instrument,
            base_pnl.label("pnl_base"),
            base_proceeds.label("proceeds_base"),
            base_commission.label("commission_base"),
        ).join(Instrument, Instrument.conid == Trade.conid, isouter=True)
    )
    if s == "closed":
        q = q.where(is_closed_filter())
    elif s == "open":
        q = q.where(is_open_filter())
    total_count = session.execute(select(func.count()).select_from(q.subquery())).scalar() or 0

    q = q.order_by(Trade.trade_datetime.desc()).limit(limit).offset(offset)
    page_trades = list(session.execute(q))

    # Dividends belong on closing rows only, over the window since the previous
    # closing date of the symbol (so overlapping windows never repeat a
    # dividend), and on one row per symbol and day.
    closing_symbols = {t.symbol for t, *_ in page_trades if is_closed(t.open_close)}
    conids_on_page = {t.conid for t, *_ in page_trades if t.conid is not None}
    div_by_conid = _dividends_by_conid(session, account_id, conids_on_page)
    first_buy_by_symbol = _first_buy_dates(session, account_id, {t.symbol for t, *_ in page_trades})
    close_days = _close_days_by_symbol(session, account_id, closing_symbols)

    rows: list[dict] = []
    for t, instr, pnl_b, proceeds_b, comm_b in page_trades:
        hold_start = first_buy_by_symbol.get(t.symbol) or t.trade_date
        closing = is_closed(t.open_close)
        divs = 0.0
        if closing:
            days = close_days.get(t.symbol, [])
            primary = next((tid for d, tid in days if d == t.trade_date), None)
            if primary == t.trade_id:
                prev_close = max((d for d, _ in days if d < t.trade_date), default=None)
                window_start = prev_close or (hold_start - timedelta(days=1))
                divs = sum(
                    amt for d, amt in div_by_conid.get(t.conid, [])
                    if d is not None and window_start < d <= t.trade_date
                )
        pnl_b_f = _f(pnl_b)
        total_return_base = (pnl_b_f or 0.0) + divs if closing else None
        # Total return % is relative to absolute proceeds (a coarse approximation;
        # cost_basis isn't on Trade rows directly).
        proceeds_abs = abs(_f(proceeds_b) or 0.0)
        total_return_pct = (
            (total_return_base / proceeds_abs * 100)
            if total_return_base is not None and proceeds_abs > 0
            else None
        )

        rows.append({
            "trade_id": t.trade_id,
            "ib_order_id": t.ib_order_id,
            "account_id": t.account_id,
            "symbol": t.symbol,
            "description": instr.description if instr else None,
            "asset_class": t.asset_class,
            "currency": t.currency,
            "trade_date": t.trade_date.isoformat() if t.trade_date else None,
            "trade_datetime": t.trade_datetime.isoformat() if t.trade_datetime else None,
            "settle_date": t.settle_date.isoformat() if t.settle_date else None,
            "buy_sell": t.buy_sell,
            "open_close": t.open_close,
            "quantity": _f(t.quantity),
            "trade_price": _f(t.trade_price),
            "proceeds": _f(t.proceeds),
            "proceeds_base": _f(proceeds_b),
            "ib_commission": _f(t.ib_commission),
            "commission_base": _f(comm_b),
            "fifo_pnl_realized": _f(t.fifo_pnl_realized),
            "fifo_pnl_realized_base": pnl_b_f,
            "dividends_during_hold_base": divs if divs else None,
            "total_return_base": total_return_base,
            "total_return_pct": total_return_pct,
            "hold_start": hold_start.isoformat() if hold_start else None,
            "multiplier": _f(t.multiplier),
            "strategy": label_by_trade.get(t.trade_id) or _strategy_hint(t, instr),
            "strategy_legs": multi_legs.get(t.trade_id, 1),
            "is_multi_leg": t.trade_id in multi_label,
            "is_open": is_open(t.open_close),
            "is_closed": closing,
            "is_winner": bool(pnl_b and float(pnl_b) > 0),
            "is_loser": bool(pnl_b and float(pnl_b) < 0),
        })

    return {
        "rows": rows,
        "limit": limit,
        "offset": offset,
        "strategies": strategies,
        "summary": {
            "total": int(total_count),
            "open_count": int(open_count),
            "closed_count": int(win_count) + int(loss_count) + int(be_count),
            "win": int(win_count),
            "loss": int(loss_count),
            "be": int(be_count),
            "realized_total_base": realized_total,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "avg_winner_base": avg_winner,
            "avg_loser_base": avg_loser,
            "win_sum_base": _f(win_sum),
            "loss_sum_base": _f(loss_sum),
        },
    }


def _close_days_by_symbol(
    session: Session,
    account_id: str | None,
    symbols: set[str],
) -> dict[str, list[tuple[date, str]]]:
    """{symbol: [(closing trade_date, first closing trade_id of that day)]}."""
    if not symbols:
        return {}
    q = (
        select(Trade.symbol, Trade.trade_date, func.min(Trade.trade_id))
        .where(Trade.symbol.in_(symbols), is_closed_filter())
        .group_by(Trade.symbol, Trade.trade_date)
    )
    if account_id:
        q = q.where(Trade.account_id == account_id)
    out: dict[str, list[tuple[date, str]]] = {}
    for sym, d, tid in session.execute(q):
        out.setdefault(sym, []).append((d, tid))
    return out


# ====================================================================
# Trades helpers — dividend attribution + holding-window detection
# ====================================================================
def _dividends_by_conid(
    session: Session,
    account_id: str | None,
    conids: set[int],
) -> dict[int, list[tuple[date, float]]]:
    """Pre-fetch (settle_date, amount_base) pairs for each conid.

    Used by trades_list to attribute dividends to a holding window without
    issuing one query per row. Returns {} when `conids` is empty.
    """
    if not conids:
        return {}
    q = select(
        CashTransaction.conid,
        CashTransaction.settle_date,
        (
            func.coalesce(CashTransaction.amount, 0)
            * func.coalesce(CashTransaction.fx_rate_to_base, 1)
        ).label("amount_base"),
    ).where(
        CashTransaction.conid.in_(conids),
        CashTransaction.type.in_(DIVIDEND_TYPES),
    )
    if account_id:
        q = q.where(CashTransaction.account_id == account_id)
    out: dict[int, list[tuple[date, float]]] = {}
    for cid, settle, amt_base in session.execute(q):
        if cid is None or settle is None:
            continue
        out.setdefault(cid, []).append((settle, float(amt_base or 0)))
    return out


def _first_buy_dates(
    session: Session,
    account_id: str | None,
    symbols: set[str],
) -> dict[str, date]:
    """Return {symbol: earliest_buy_date_in_history} so trade rows can use that
    as the start of the holding window. Approximation: for symbols traded in
    and out, this stretches the window further than a precise FIFO walk would.
    """
    if not symbols:
        return {}
    q = select(Trade.symbol, func.min(Trade.trade_date)).where(
        Trade.symbol.in_(symbols),
        Trade.buy_sell == "BUY",
    ).group_by(Trade.symbol)
    if account_id:
        q = q.where(Trade.account_id == account_id)
    return {sym: d for sym, d in session.execute(q) if sym and d}
