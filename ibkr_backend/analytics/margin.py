"""Margin-adjacent metrics derivable from Flex data alone.

No IB Gateway required. The Reg-T estimates here are intentionally conservative
(50 % initial / 25 % maintenance for stocks, full premium for options, 15 %
for futures); a Portfolio-Margin account at IBKR will see materially lower
real requirements. When `ibgw/live.py` is wired, swap in the real numbers.

Metrics surfaced (and why they matter):
  * Cash by currency      — negative ending_cash = active margin debt in that ccy
  * Gross / Net exposure  — gross/NLV is your leverage ratio (1.0 = unlevered)
  * Concentration         — top-N positions as % of NLV (idiosyncratic risk)
  * Estimated Cushion     — (NLV − estimated MaintMargin) / NLV. <10 % = danger
  * Interest paid         — what margin is costing you; effective APR vs avg debt
  * Leverage time series  — daily gross_position_value / NAV
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import (
    CashReportRow,
    CashTransaction,
    Instrument,
    NavDaily,
    PositionEod,
)
from ._common import _f
from .filters import PeriodFilter
from .fx_table import FxTable

# Reg-T maintenance margin requirement per asset class (conservative defaults).
# Real IBKR Portfolio Margin numbers will usually be 30–50 % lower for diversified books.
_MAINT_MARGIN_PCT = {
    "STK": 0.25,
    "ETF": 0.25,
    "BOND": 0.25,
    "FUT": 0.15,
    "OPT": 1.00,    # Long options: full premium tied up. Short options would be far worse.
    "FOP": 1.00,
    "CASH": 0.05,
    None: 0.30,
}
_INIT_MARGIN_PCT = {  # Reg-T 50 % initial for stocks; same other classes
    "STK": 0.50,
    "ETF": 0.50,
    "BOND": 0.50,
    "FUT": 0.15,
    "OPT": 1.00,
    "FOP": 1.00,
    "CASH": 0.10,
    None: 0.50,
}

# Interest-related cash-transaction type strings IBKR uses.
_INTEREST_PAID = ("Broker Interest Paid", "Bond Interest Paid")
_INTEREST_RECV = ("Broker Interest Received", "Bond Interest Received")


def _is_syep(description: str | None) -> bool:
    """Stock Yield Enhancement Program income is posted as "Broker Interest
    Received" too ("… IBKR MANAGED SECURITIES (SYEP) INTEREST …"), but it is
    stock-lending income, not interest on cash."""
    text = (description or "").upper()
    return "SYEP" in text or "MANAGED SECURITIES" in text

# Warning thresholds.
_LEVERAGE_WARN = 1.30        # gross/NAV
_CONCENTRATION_WARN = 0.15   # any single position > 15 % NLV
_CUSHION_WARN = 0.30         # estimated cushion < 30 %


def _latest_snapshot(session: Session, account_id: str | None) -> date | None:
    # Cashflow-only rows (nav_total NULL) are not real snapshots.
    q = select(func.max(NavDaily.snapshot_date)).where(NavDaily.nav_total.is_not(None))
    if account_id:
        q = q.where(NavDaily.account_id == account_id)
    return session.execute(q).scalar()


def margin_summary(
    session: Session,
    *,
    snapshot_date: date | None = None,
    account_id: str | None = None,
    top_n: int = 10,
) -> dict[str, Any]:
    """Snapshot of margin posture at a given date (default: latest NAV).

    All amounts in account base currency. Returns enough detail to render a
    Margen page in one round-trip.
    """
    snap = snapshot_date or _latest_snapshot(session, account_id)
    if snap is None:
        return {"snapshot_date": None, "warning": "no NAV data"}

    nav_q = select(NavDaily).where(NavDaily.snapshot_date == snap)
    if account_id:
        nav_q = nav_q.where(NavDaily.account_id == account_id)
    nav = session.execute(nav_q).scalars().first()
    nav_total = float(nav.nav_total) if (nav and nav.nav_total is not None) else 0.0
    base_currency = nav.base_currency if nav else None

    # Cash per currency — flag debts.
    cash_q = select(CashReportRow).where(CashReportRow.snapshot_date == snap)
    if account_id:
        cash_q = cash_q.where(CashReportRow.account_id == account_id)
    cash_rows = session.execute(cash_q).scalars().all()
    cash_by_currency = []
    cash_debt_native_sum = 0.0
    fx_table = FxTable(session, base_currency)
    for r in cash_rows:
        ending = _f(r.ending_cash) or 0.0
        is_debt = ending < 0
        if is_debt:
            cash_debt_native_sum += abs(ending)
        ending_base = fx_table.convert(ending, r.currency, snap)  # None if no Flex rate
        cash_by_currency.append({
            "currency": r.currency,
            "ending_cash": ending,
            "ending_cash_base": ending_base,
            "pct_of_nav": (ending_base / nav_total * 100) if (nav_total and ending_base is not None) else None,
            "is_debt": is_debt,
        })

    # Position exposure.
    pos_q = (
        select(PositionEod, Instrument.asset_class.label("instr_asset_class"))
        .where(PositionEod.snapshot_date == snap)
        .join(Instrument, Instrument.conid == PositionEod.conid, isouter=True)
    )
    if account_id:
        pos_q = pos_q.where(PositionEod.account_id == account_id)
    longs_base = 0.0
    shorts_base = 0.0
    maint_estimate = 0.0
    init_estimate = 0.0
    options_value_base = 0.0
    positions: list[dict] = []
    for p, instr_ac in session.execute(pos_q):
        pv_native = _f(p.position_value) or 0.0
        fx = _f(p.fx_rate_to_base) or 1.0
        pv_base = pv_native * fx
        if pv_base > 0:
            longs_base += pv_base
        else:
            shorts_base += pv_base
        ac = (p.asset_class or instr_ac or "").upper() or None
        if ac in ("OPT", "FOP"):
            options_value_base += abs(pv_base)
        maint_pct = _MAINT_MARGIN_PCT.get(ac, _MAINT_MARGIN_PCT[None])
        init_pct = _INIT_MARGIN_PCT.get(ac, _INIT_MARGIN_PCT[None])
        maint_estimate += abs(pv_base) * maint_pct
        init_estimate += abs(pv_base) * init_pct
        positions.append({
            "symbol": p.symbol,
            "asset_class": ac,
            "quantity": _f(p.quantity),
            "position_value_base": pv_base,
            "pct_of_nav": (pv_base / nav_total * 100) if nav_total else None,
        })

    gross_base = longs_base + abs(shorts_base)
    net_base = longs_base + shorts_base
    leverage_x = (gross_base / nav_total) if nav_total else None
    excess_liq_estimate = nav_total - maint_estimate
    cushion_pct = (excess_liq_estimate / nav_total * 100) if nav_total else None
    buying_power_estimate = nav_total - init_estimate  # very rough — actual BP depends on PM/Reg-T regime
    # Liquidity = NAV cash (net of any per-currency debt) / NLV.
    nav_cash = _f(nav.cash) if nav else None
    cash_pct_nlv = (nav_cash / nav_total * 100) if (nav_total and nav_cash is not None) else None

    # Top N concentration (by |value|).
    positions.sort(key=lambda r: abs(r["position_value_base"]), reverse=True)
    top_positions = positions[:top_n]

    warnings: list[str] = []
    if leverage_x is not None and leverage_x > _LEVERAGE_WARN:
        warnings.append(
            f"Leverage {leverage_x:.2f}× > {_LEVERAGE_WARN}× — consider reducing gross exposure."
        )
    if cushion_pct is not None and cushion_pct < _CUSHION_WARN * 100:
        warnings.append(
            f"Estimated cushion {cushion_pct:.1f} % < {_CUSHION_WARN*100:.0f} % "
            "(Reg-T estimate; real PM cushion may be higher)."
        )
    for p in top_positions[:3]:
        pct = p.get("pct_of_nav") or 0
        if abs(pct) >= _CONCENTRATION_WARN * 100:
            warnings.append(
                f"Concentration: {p['symbol']} is {pct:.1f} % of NLV (> {_CONCENTRATION_WARN*100:.0f} %)."
            )

    return {
        "snapshot_date": snap.isoformat(),
        "base_currency": base_currency,
        "nav_total": nav_total,
        "nav_breakdown": {
            "cash": nav_cash,
            "stock": _f(nav.stock) if nav else None,
            "options": _f(nav.options) if nav else None,
            "bonds": _f(nav.bonds) if nav else None,
        },
        "cash_by_currency": cash_by_currency,
        "cash_debt_native_sum": cash_debt_native_sum,
        "cash_pct_nlv": cash_pct_nlv,
        "exposure": {
            "longs_base": longs_base,
            "shorts_base": shorts_base,
            "gross_base": gross_base,
            "net_base": net_base,
            "gross_pct_nlv": (gross_base / nav_total * 100) if nav_total else None,
            "net_pct_nlv": (net_base / nav_total * 100) if nav_total else None,
            "options_value_base": options_value_base,
            "options_pct_nlv": (options_value_base / nav_total * 100) if nav_total else None,
        },
        "leverage_x": leverage_x,
        "reg_t_estimate": {
            "source": "Reg-T conservative (50 % initial, 25 % maintenance for stocks; full premium for options; 15 % for futures). Real IBKR PM numbers will usually be lower.",
            "init_margin_estimate": init_estimate,
            "maint_margin_estimate": maint_estimate,
            "excess_liq_estimate": excess_liq_estimate,
            "buying_power_estimate": buying_power_estimate,
            "cushion_pct_estimate": cushion_pct,
        },
        "top_positions": top_positions,
        "warnings": warnings,
    }


def margin_interest_summary(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Interest paid (margin) and received (cash) over the period.

    Effective rate = paid / avg debt (base) × 365 / calendar span.
    Each currency's negative cash balance is converted to base with `fx_rates`
    of that day (`BASE` summary row excluded) and averaged over all snapshot
    days of the period. Per-currency APR is reported in `paid_by_currency`.

    The cash side mirrors it: credit interest on cash / avg positive cash
    balance (base) × 365 / span = effective yield, per currency in
    `received_by_currency`. SYEP stock-lending income is reported apart
    (`syep_*`) and kept out of the cash yield. Interest is posted monthly in
    arrears (settle date ≈ 3rd business day of the next month), on both sides.
    """
    paid_native: dict[str, float] = {}
    paid_base_by_ccy: dict[str, float] = {}
    recv_native: dict[str, float] = {}
    recv_base_by_ccy: dict[str, float] = {}
    cash_int_native: dict[str, float] = {}     # credit interest on cash
    cash_int_base_by_ccy: dict[str, float] = {}
    syep_native: dict[str, float] = {}         # stock-lending income
    syep_base_by_ccy: dict[str, float] = {}
    paid_total_base = 0.0
    recv_total_base = 0.0

    tx_q = select(CashTransaction).where(
        CashTransaction.type.in_(_INTEREST_PAID + _INTEREST_RECV)
    )
    if account_id:
        tx_q = tx_q.where(CashTransaction.account_id == account_id)
    if period and period.start:
        tx_q = tx_q.where(CashTransaction.settle_date >= period.start)
    if period and period.end:
        tx_q = tx_q.where(CashTransaction.settle_date <= period.end)

    for tx in session.execute(tx_q).scalars():
        amt = float(tx.amount or 0)
        fx = float(tx.fx_rate_to_base or 1)
        amt_base = amt * fx
        ccy = tx.currency or "?"
        if tx.type in _INTEREST_PAID:
            paid_native[ccy] = paid_native.get(ccy, 0.0) + amt
            paid_base_by_ccy[ccy] = paid_base_by_ccy.get(ccy, 0.0) + amt_base
            paid_total_base += amt_base
        else:
            recv_native[ccy] = recv_native.get(ccy, 0.0) + amt
            recv_base_by_ccy[ccy] = recv_base_by_ccy.get(ccy, 0.0) + amt_base
            recv_total_base += amt_base
            if _is_syep(tx.description):
                syep_native[ccy] = syep_native.get(ccy, 0.0) + amt
                syep_base_by_ccy[ccy] = syep_base_by_ccy.get(ccy, 0.0) + amt_base
            else:
                cash_int_native[ccy] = cash_int_native.get(ccy, 0.0) + amt
                cash_int_base_by_ccy[ccy] = cash_int_base_by_ccy.get(ccy, 0.0) + amt_base

    # Average debt during the period, per currency, converted to base with the
    # Flex fx_rates of each snapshot day. The `BASE` row of the cash report is a
    # summary of the other currencies and is excluded. The average runs over all
    # snapshot days of the period (days without debt count as zero).
    debt_q = select(
        CashReportRow.snapshot_date,
        CashReportRow.currency,
        CashReportRow.ending_cash,
    )
    if account_id:
        debt_q = debt_q.where(CashReportRow.account_id == account_id)
    if period and period.start:
        debt_q = debt_q.where(CashReportRow.snapshot_date >= period.start)
    if period and period.end:
        debt_q = debt_q.where(CashReportRow.snapshot_date <= period.end)

    fx_table = FxTable(session)
    snapshot_days: set[date] = set()
    debt_days: dict[date, float] = {}          # day -> total debt in base
    debt_sum_by_ccy: dict[str, float] = {}     # ccy -> sum over days of debt in base
    cash_days: dict[date, float] = {}          # day -> total positive cash in base
    cash_sum_by_ccy: dict[str, float] = {}     # ccy -> sum over days of positive cash in base
    for d, ccy, ec in session.execute(debt_q):
        if ccy == "BASE":
            continue
        snapshot_days.add(d)
        ec_f = float(ec or 0)
        if ec_f == 0:
            continue
        rate = fx_table.rate(ccy, d)
        if rate is None:
            continue
        if ec_f > 0:
            cash_base = ec_f * rate
            cash_days[d] = cash_days.get(d, 0.0) + cash_base
            cash_sum_by_ccy[ccy] = cash_sum_by_ccy.get(ccy, 0.0) + cash_base
            continue
        debt_base = abs(ec_f) * rate
        debt_days[d] = debt_days.get(d, 0.0) + debt_base
        debt_sum_by_ccy[ccy] = debt_sum_by_ccy.get(ccy, 0.0) + debt_base

    if snapshot_days:
        n_days = len(snapshot_days)
        avg_debt_base = sum(debt_days.values()) / n_days
        avg_cash_base = sum(cash_days.values()) / n_days
        days_covered = len(debt_days)
        # Calendar span of the snapshots: each snapshot day stands for
        # span/n calendar days (weekends carry the previous balance).
        span_days = (max(snapshot_days) - min(snapshot_days)).days + 1
    else:
        n_days = 0
        avg_debt_base = 0.0
        avg_cash_base = 0.0
        days_covered = 0
        span_days = 0

    paid_abs_base = abs(paid_total_base)
    effective_apr = None
    if avg_debt_base > 0 and span_days > 0:
        effective_apr = paid_abs_base / avg_debt_base * 365 / span_days * 100

    def _annualised(amount_base: float, avg_base: float) -> float | None:
        if avg_base <= 0 or span_days <= 0:
            return None
        return amount_base / avg_base * 365 / span_days * 100

    cash_int_total_base = sum(cash_int_base_by_ccy.values())
    syep_total_base = sum(syep_base_by_ccy.values())
    effective_cash_yield = _annualised(cash_int_total_base, avg_cash_base)

    apr_by_currency: dict[str, dict] = {}
    for ccy, total in debt_sum_by_ccy.items():
        avg_ccy = total / n_days
        paid_ccy = abs(paid_base_by_ccy.get(ccy, 0.0))
        apr_by_currency[ccy] = {
            "avg_debt_base": avg_ccy,
            "apr_pct": (paid_ccy / avg_ccy * 365 / span_days * 100) if avg_ccy > 0 and span_days > 0 else None,
        }

    return {
        "period": {
            "from": period.start.isoformat() if period and period.start else None,
            "to": period.end.isoformat() if period and period.end else None,
        },
        "paid_total_base": paid_total_base,           # negative number (cost)
        "received_total_base": recv_total_base,       # positive
        "net_base": paid_total_base + recv_total_base,
        "paid_by_currency": [
            {
                "currency": k,
                "amount_native": v,
                "amount_base": paid_base_by_ccy.get(k, 0.0),
                "avg_debt_base": apr_by_currency.get(k, {}).get("avg_debt_base"),
                "apr_pct": apr_by_currency.get(k, {}).get("apr_pct"),
            }
            for k, v in sorted(paid_native.items())
        ],
        # Every currency that earned interest or held positive cash, so idle
        # balances that earn nothing (below IBKR's per-currency threshold) show.
        "received_by_currency": [
            {
                "currency": k,
                "amount_native": recv_native.get(k, 0.0),
                "amount_base": recv_base_by_ccy.get(k, 0.0),
                "cash_interest_native": cash_int_native.get(k, 0.0),
                "cash_interest_base": cash_int_base_by_ccy.get(k, 0.0),
                "syep_native": syep_native.get(k, 0.0),
                "syep_base": syep_base_by_ccy.get(k, 0.0),
                "avg_cash_base": (cash_sum_by_ccy.get(k, 0.0) / n_days) if n_days else 0.0,
                "yield_pct": _annualised(
                    cash_int_base_by_ccy.get(k, 0.0),
                    (cash_sum_by_ccy.get(k, 0.0) / n_days) if n_days else 0.0,
                ),
            }
            for k in sorted(set(recv_native) | set(cash_sum_by_ccy))
        ],
        "avg_debt_base": avg_debt_base,
        "days_with_debt": days_covered,
        "span_days": span_days,
        "effective_apr_pct": effective_apr,
        "cash_interest_base": cash_int_total_base,
        "syep_base": syep_total_base,
        "avg_cash_base": avg_cash_base,
        "days_with_cash": len(cash_days),
        "effective_cash_yield_pct": effective_cash_yield,
    }


def leverage_series(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> list[dict]:
    """Daily leverage = (stock + options + bonds) / nav_total from NavDaily.

    Excludes days where any component is null (sparse historical NAV).
    """
    q = select(NavDaily).order_by(NavDaily.snapshot_date.asc())
    if account_id:
        q = q.where(NavDaily.account_id == account_id)
    if period and period.start:
        q = q.where(NavDaily.snapshot_date >= period.start)
    if period and period.end:
        q = q.where(NavDaily.snapshot_date <= period.end)
    out: list[dict] = []
    for r in session.execute(q).scalars():
        nav = _f(r.nav_total)
        if not nav:
            continue
        stock = _f(r.stock) or 0.0
        opts = _f(r.options) or 0.0
        bonds = _f(r.bonds) or 0.0
        cash = _f(r.cash) or 0.0
        # Gross exposure ignores direction — short stock contributes the same
        # market-value risk as long stock. Mirror the margin_summary() logic
        # which already does longs_base + abs(shorts_base).
        gross = abs(stock) + abs(opts) + abs(bonds)
        out.append({
            "date": r.snapshot_date.isoformat(),
            "nav": nav,
            "stock": stock,
            "options": opts,
            "bonds": bonds,
            "cash": cash,
            "gross_position_value": gross,
            "leverage_x": gross / nav if nav else None,
            "cash_pct_nlv": (cash / nav * 100) if nav else None,
        })
    return out
