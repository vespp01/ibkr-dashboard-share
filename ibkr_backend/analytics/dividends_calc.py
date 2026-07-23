"""Dividend + withholding-tax analytics.

Extracted from `metrics.py` for cohesion. The dividend functions all
operate on `CashTransaction` rows of `type` in `DIVIDEND_TYPES +
WITHHOLDING_TYPES`. `income_projection` is also here because it
forecasts dividend income (PADI) from current EOD stock positions and
the `DividendAccrual` history.

Note: IBKR Flex emits dividend cash-tx rows with `symbol`, `conid`,
AND `country` all NULL — the ticker and ISIN live only in
`description`. The 3-tier country fallback (explicit → issuer →
ISIN-prefix) is essential for any per-country roll-up.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..db.models import (
    CashTransaction,
    DividendAccrual,
    Instrument,
    NavDaily,
    PositionEod,
)
from ._common import (
    _apply_period,
    _date_bucket,
    _days_stale,
    _f,
    _latest_snapshot_date,
    _to_base,
)
from .filters import PeriodFilter


DIVIDEND_TYPES = ("Dividends", "Payment In Lieu Of Dividends")
WITHHOLDING_TYPES = ("Withholding Tax", "871(m) Withholding")


# IBKR Flex leaves `CashTransaction.country` AND `conid` NULL on dividend
# rows; the ISIN sits inside `description` as `TICKER(<ISIN>) Cash
# Dividend ...`. First 2 chars of the ISIN are the country code. Without
# this fallback, every gross dividend rolls up under "UNKNOWN" on affected
# real exports.
_ISIN_COUNTRY_RE = re.compile(r"\(([A-Z]{2})[A-Z0-9]{9}\d\)")


def _country_from_description(desc: str | None) -> str | None:
    if not desc:
        return None
    m = _ISIN_COUNTRY_RE.search(desc)
    return m.group(1) if m else None


def dividends_summary(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> dict:
    """Gross + net dividends in base currency.

    Net = Dividends + PaymentInLieu + WithholdingTax (withholding is negative).
    """
    base = _to_base(CashTransaction.amount, CashTransaction.fx_rate_to_base)
    q = select(
        CashTransaction.type,
        func.coalesce(func.sum(base), 0).label("total"),
        func.count(CashTransaction.transaction_id).label("rows"),
    ).group_by(CashTransaction.type)
    q = _apply_period(q, CashTransaction.settle_date, period)
    if account_id:
        q = q.where(CashTransaction.account_id == account_id)
    q = q.where(
        CashTransaction.type.in_(DIVIDEND_TYPES + WITHHOLDING_TYPES)
    )
    by_type: dict[str, dict] = {}
    for r in session.execute(q):
        by_type[r.type] = {"total_base": _f(r.total), "rows": r.rows}
    gross = sum((by_type.get(t, {"total_base": 0})["total_base"] or 0) for t in DIVIDEND_TYPES)
    wht = sum((by_type.get(t, {"total_base": 0})["total_base"] or 0) for t in WITHHOLDING_TYPES)
    return {
        "gross_base": gross,
        "withholding_base": wht,
        "net_base": gross + wht,
        "by_type": by_type,
    }


def dividends_timeseries(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    bucket: str = "month",
) -> list[dict]:
    """Bucketed dividend stream — gross / withholding / net."""
    base = _to_base(CashTransaction.amount, CashTransaction.fx_rate_to_base)
    bucket_expr = _date_bucket(CashTransaction.settle_date, bucket)
    gross_expr = func.sum(case((CashTransaction.type.in_(DIVIDEND_TYPES), base), else_=0))
    wht_expr = func.sum(case((CashTransaction.type.in_(WITHHOLDING_TYPES), base), else_=0))
    q = select(
        bucket_expr.label("bucket"),
        gross_expr.label("gross"),
        wht_expr.label("withholding"),
    ).group_by("bucket").order_by("bucket")
    q = _apply_period(q, CashTransaction.settle_date, period)
    if account_id:
        q = q.where(CashTransaction.account_id == account_id)
    q = q.where(CashTransaction.type.in_(DIVIDEND_TYPES + WITHHOLDING_TYPES))
    return [
        {
            "bucket": r.bucket,
            "gross": _f(r.gross),
            "withholding": _f(r.withholding),
            "net": (_f(r.gross) or 0) + (_f(r.withholding) or 0),
        }
        for r in session.execute(q)
    ]


def dividends_by_country(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> list[dict]:
    """Dividend gross/withholding/net grouped by country.

    Country resolves through a 3-tier fallback: explicit
    `CashTransaction.country` → `Instrument.issuer_country` → ISIN prefix
    parsed from `CashTransaction.description`. The third tier is required
    on real Flex data, where dividend rows ship with `country`, `conid`,
    AND `symbol` all NULL.
    """
    q = (
        select(
            CashTransaction.country,
            Instrument.issuer_country,
            CashTransaction.description,
            CashTransaction.type,
            CashTransaction.amount,
            CashTransaction.fx_rate_to_base,
        )
        .join(Instrument, Instrument.conid == CashTransaction.conid, isouter=True)
    )
    q = _apply_period(q, CashTransaction.settle_date, period)
    if account_id:
        q = q.where(CashTransaction.account_id == account_id)
    q = q.where(CashTransaction.type.in_(DIVIDEND_TYPES + WITHHOLDING_TYPES))

    by_country: dict[str, dict] = {}
    for explicit, issuer, desc, ttype, amt, fx in session.execute(q):
        country = explicit or issuer or _country_from_description(desc) or "UNKNOWN"
        amt_base = float(amt or 0) * float(fx or 1)
        b = by_country.setdefault(country, {"country": country, "gross": 0.0, "withholding": 0.0})
        if ttype in DIVIDEND_TYPES:
            b["gross"] += amt_base
        elif ttype in WITHHOLDING_TYPES:
            b["withholding"] += amt_base

    rows = list(by_country.values())
    for r in rows:
        r["net"] = r["gross"] + r["withholding"]
    rows.sort(key=lambda r: r["gross"], reverse=True)
    return rows


def withholding_tax_by_country(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> list[dict]:
    """Just the withholding-tax slice grouped by country.

    Same 3-tier country fallback as `dividends_by_country`.
    """
    q = (
        select(
            CashTransaction.country,
            Instrument.issuer_country,
            CashTransaction.description,
            CashTransaction.amount,
            CashTransaction.fx_rate_to_base,
        )
        .join(Instrument, Instrument.conid == CashTransaction.conid, isouter=True)
    )
    q = _apply_period(q, CashTransaction.settle_date, period)
    if account_id:
        q = q.where(CashTransaction.account_id == account_id)
    q = q.where(CashTransaction.type.in_(WITHHOLDING_TYPES))

    by_country: dict[str, dict] = {}
    for explicit, issuer, desc, amt, fx in session.execute(q):
        country = explicit or issuer or _country_from_description(desc) or "UNKNOWN"
        amt_base = float(amt or 0) * float(fx or 1)
        b = by_country.setdefault(country, {"country": country, "withholding": 0.0, "rows": 0})
        b["withholding"] += amt_base
        b["rows"] += 1

    rows = list(by_country.values())
    rows.sort(key=lambda r: r["withholding"])
    return rows


def income_projection(
    session: Session,
    *,
    snapshot_date: date | None = None,
    account_id: str | None = None,
) -> dict:
    """Projected Annual Dividend Income (PADI) from current EOD stock
    positions × the most recent annualised gross_rate per conid in the
    DividendAccrual table (last 370 days, deduped by event_key).
    """
    snapshot = snapshot_date or _latest_snapshot_date(session, account_id)
    if snapshot is None:
        return {
            "as_of": None, "days_stale": None,
            "padi_base": 0.0, "monthly_base": 0.0,
            "yield_pct": None, "yield_on_nav_pct": None,
            "positions_total": 0, "positions_covered": 0,
            "coverage_ratio": 0.0, "equity_market_value_base": 0.0,
            "nav_base": None, "by_symbol": [],
        }

    pos_q = select(PositionEod).where(
        PositionEod.snapshot_date == snapshot,
        PositionEod.asset_class == "STK",
        PositionEod.quantity > 0,
    )
    if account_id:
        pos_q = pos_q.where(PositionEod.account_id == account_id)
    positions = session.execute(pos_q).scalars().all()
    if not positions:
        return {
            "as_of": snapshot.isoformat(), "days_stale": _days_stale(snapshot),
            "padi_base": 0.0, "monthly_base": 0.0,
            "yield_pct": None, "yield_on_nav_pct": None,
            "positions_total": 0, "positions_covered": 0,
            "coverage_ratio": 0.0, "equity_market_value_base": 0.0,
            "nav_base": None, "by_symbol": [],
        }

    conids = [p.conid for p in positions]
    accrual_cutoff = snapshot - timedelta(days=370)
    acc_q = select(
        DividendAccrual.conid,
        DividendAccrual.ex_date,
        DividendAccrual.pay_date,
        DividendAccrual.gross_rate,
    ).where(
        DividendAccrual.conid.in_(conids),
        DividendAccrual.gross_rate.is_not(None),
        DividendAccrual.ex_date.is_not(None),
        DividendAccrual.ex_date >= accrual_cutoff,
        DividendAccrual.ex_date <= snapshot,
    )
    if account_id:
        acc_q = acc_q.where(DividendAccrual.account_id == account_id)
    annual_rate_by_conid: dict[int, Decimal] = {}
    seen_events: set[tuple[int, date, date | None, Decimal]] = set()
    for conid, ex_date, pay_date, gross_rate in session.execute(acc_q):
        if conid is None or ex_date is None or gross_rate is None:
            continue
        event_key = (conid, ex_date, pay_date, gross_rate)
        if event_key in seen_events:
            continue
        seen_events.add(event_key)
        annual_rate_by_conid[conid] = annual_rate_by_conid.get(conid, Decimal(0)) + gross_rate

    nav_q = select(NavDaily.nav_total).where(NavDaily.snapshot_date == snapshot)
    if account_id:
        nav_q = nav_q.where(NavDaily.account_id == account_id)
    nav_base = _f(session.execute(nav_q).scalar())

    equity_market_value_base = 0.0
    covered_market_value_base = 0.0
    padi_base = 0.0
    by_symbol: list[dict] = []
    positions_covered = 0
    for p in positions:
        market_value_base = _f((p.position_value or 0) * (p.fx_rate_to_base or 1)) or 0.0
        equity_market_value_base += market_value_base
        annual_rate_native = annual_rate_by_conid.get(p.conid, Decimal(0))
        annual_income_native = annual_rate_native * (p.quantity or 0)
        annual_income_base = _f(annual_income_native * (p.fx_rate_to_base or 1)) or 0.0
        if annual_income_base > 0:
            positions_covered += 1
            covered_market_value_base += market_value_base
        padi_base += annual_income_base
        by_symbol.append({
            "symbol": p.symbol,
            "conid": p.conid,
            "quantity": _f(p.quantity),
            "currency": p.currency,
            "market_value_base": market_value_base,
            "annual_dividend_per_share": _f(annual_rate_native),
            "annual_income_base": annual_income_base,
            "yield_pct": ((annual_income_base / market_value_base) * 100) if market_value_base else None,
        })

    by_symbol.sort(key=lambda row: row["annual_income_base"], reverse=True)
    coverage_ratio = (covered_market_value_base / equity_market_value_base) if equity_market_value_base else 0.0
    return {
        "as_of": snapshot.isoformat(),
        "days_stale": _days_stale(snapshot),
        "padi_base": padi_base,
        "monthly_base": padi_base / 12 if padi_base else 0.0,
        "yield_pct": ((padi_base / equity_market_value_base) * 100) if equity_market_value_base else None,
        "yield_on_nav_pct": ((padi_base / nav_base) * 100) if nav_base else None,
        "positions_total": len(positions),
        "positions_covered": positions_covered,
        "coverage_ratio": coverage_ratio,
        "equity_market_value_base": equity_market_value_base,
        "nav_base": nav_base,
        "by_symbol": by_symbol[:12],
    }


__all__ = [
    "DIVIDEND_TYPES",
    "WITHHOLDING_TYPES",
    "dividends_by_country",
    "dividends_summary",
    "dividends_timeseries",
    "income_projection",
    "withholding_tax_by_country",
]
