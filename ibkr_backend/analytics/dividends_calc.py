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
from statistics import median

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import (
    CashTransaction,
    DividendAccrual,
    FxRate,
    Instrument,
    NavDaily,
    PositionEod,
)
from .fx_table import FxTable
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


_ISIN_RE = re.compile(r"\(([A-Z]{2}[A-Z0-9]{9}\d)\)")


def _event_keys(conid: int | None, desc: str | None, settle: date | None) -> list[tuple]:
    """Lookup keys that tie a dividend row to its withholding row: same
    instrument (conid, else ISIN from the description) and same settle date."""
    keys: list[tuple] = []
    if conid is not None:
        keys.append(("conid", conid, settle))
    m = _ISIN_RE.search(desc or "")
    if m:
        keys.append(("isin", m.group(1), settle))
    return keys


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

    A dividend row takes the country of the withholding row for the same
    instrument and settle date when there is one (ISIN prefix and issuer
    country can differ from the taxing country). Otherwise the country
    resolves through a 3-tier fallback: explicit
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
            CashTransaction.conid,
            CashTransaction.settle_date,
        )
        .join(Instrument, Instrument.conid == CashTransaction.conid, isouter=True)
    )
    q = _apply_period(q, CashTransaction.settle_date, period)
    if account_id:
        q = q.where(CashTransaction.account_id == account_id)
    q = q.where(CashTransaction.type.in_(DIVIDEND_TYPES + WITHHOLDING_TYPES))
    rows_all = list(session.execute(q))

    # Country of the tax that was withheld for the same instrument and settle
    # date: a dividend inherits it, so a withholding row never sits in a
    # country with zero gross (e.g. an HK-listed stock with a LU ISIN).
    wht_country: dict[tuple, str] = {}
    wht_size: dict[tuple, float] = {}
    for explicit, issuer, desc, ttype, amt, fx, conid, settle in rows_all:
        if ttype not in WITHHOLDING_TYPES:
            continue
        country = explicit or issuer or _country_from_description(desc)
        if not country:
            continue
        size = abs(float(amt or 0) * float(fx or 1))
        for key in _event_keys(conid, desc, settle):
            if size >= wht_size.get(key, -1.0):
                wht_country[key] = country
                wht_size[key] = size

    by_country: dict[str, dict] = {}
    for explicit, issuer, desc, ttype, amt, fx, conid, settle in rows_all:
        matched = None
        if ttype in DIVIDEND_TYPES:
            matched = next(
                (wht_country[k] for k in _event_keys(conid, desc, settle) if k in wht_country), None
            )
        country = matched or explicit or issuer or _country_from_description(desc) or "UNKNOWN"
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


# A dividend event whose payment is closer than this share of the holding's
# usual interval to the previous one is a re-booking or a special, not part of
# the regular annual cycle. A rate this many times the median is a special.
_CADENCE_MIN_GAP_RATIO = 0.5
_SPECIAL_RATE_RATIO = 2.5


def _annual_dividend_by_conid(
    session: Session,
    conids: list[int],
    snapshot: date,
    *,
    account_id: str | None,
    fx: FxTable,
) -> dict[int, float]:
    """Trailing-365-day dividend per share (base currency) per conid.

    IBKR books every dividend as a `Po` accrual row and later reverses it with a
    `Re` row (often with another ex-date, rate or currency). Only `Po` rows
    describe the dividend, so `Re` rows are ignored. `Po` rows are collapsed to
    one event per (conid, pay_date); when a payment currency is re-elected
    (ZAR/GBP) the latest `Po` wins. Each event is converted with the FX of its
    own currency. The trailing window is 365 days of ex-dates. Events that break
    the holding's cadence (much closer to the previous event than the usual
    interval, or a rate far above the median) are treated as specials and
    excluded. Every figure traces to a Flex accrual field times a Flex FX rate.
    """
    q = (
        select(DividendAccrual)
        .where(
            DividendAccrual.conid.in_(conids),
            DividendAccrual.gross_rate.is_not(None),
            DividendAccrual.ex_date.is_not(None),
            DividendAccrual.ex_date <= snapshot,
        )
        .order_by(DividendAccrual.id)
    )
    if account_id:
        q = q.where(DividendAccrual.account_id == account_id)
    # (conid, pay_date or ex_date) -> latest Po row; ordered by id so last wins.
    events: dict[tuple[int, date], DividendAccrual] = {}
    for row in session.execute(q).scalars():
        if not (row.code or "").strip().endswith("Po") or row.conid is None:
            continue
        events[(row.conid, row.pay_date or row.ex_date)] = row

    by_conid: dict[int, list[DividendAccrual]] = {}
    for (conid, _), row in events.items():
        by_conid.setdefault(conid, []).append(row)

    cutoff = snapshot - timedelta(days=365)
    out: dict[int, float] = {}
    for conid, rows in by_conid.items():
        rows.sort(key=lambda r: r.pay_date or r.ex_date)
        paid = [r.pay_date or r.ex_date for r in rows]
        gaps = [(b - a).days for a, b in zip(paid, paid[1:]) if (b - a).days > 0]
        typical_gap = median(gaps) if len(gaps) >= 2 else None
        # Rates in base currency, so a ZAR election is comparable to a GBP one.
        rates = [fx.convert(float(r.gross_rate), (r.currency or fx.base).upper(), snapshot) for r in rows]
        known = [x for x in rates if x is not None]
        median_rate = median(known) if len(known) >= 4 else None
        total = 0.0
        for i, r in enumerate(rows):
            rate = rates[i]
            if rate is None or r.ex_date <= cutoff:
                continue
            if i > 0 and typical_gap and (paid[i] - paid[i - 1]).days < typical_gap * _CADENCE_MIN_GAP_RATIO:
                continue
            if median_rate and rate > median_rate * _SPECIAL_RATE_RATIO:
                continue
            total += rate
        if total:
            out[conid] = total
    return out


def income_projection(
    session: Session,
    *,
    snapshot_date: date | None = None,
    account_id: str | None = None,
) -> dict:
    """Projected Annual Dividend Income (PADI) from current EOD stock
    positions × the most recent annualised gross_rate per conid in the
    DividendAccrual table (trailing 365 days; see `_annual_dividend_by_conid`).
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
    fx = FxTable(session)
    annual_by_conid = _annual_dividend_by_conid(
        session, conids, snapshot, account_id=account_id, fx=fx
    )

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
        # Per-share annual dividend in base currency (each event converted by
        # its own payment currency), and in the position currency for display.
        annual_rate_base = annual_by_conid.get(p.conid, 0.0)
        pos_fx = float(p.fx_rate_to_base or 1)
        annual_rate_native = Decimal(str(annual_rate_base / pos_fx)) if pos_fx else Decimal(0)
        annual_income_base = annual_rate_base * float(p.quantity or 0)
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
        "by_symbol": by_symbol,
    }


def upcoming_dividends(
    session: Session,
    *,
    account_id: str | None = None,
    base_currency: str | None = None,
) -> dict:
    """Open dividend accruals with a pay date on/after the data date.

    IBKR Flex posts `Po` accrual rows and later `Re` reversal rows (on
    payment, or when it re-books). Group by (account, conid, ex_date,
    pay_date); a group is OPEN while its `Po` count exceeds its `Re` count.
    The current accrual of an open group is its newest `Po` row (handles
    currency re-elections). Open groups already past `as_of` are orphans
    IBKR never reversed: counted in `stale_open_events`, not listed.
    """
    as_of = _latest_snapshot_date(session, account_id)
    base = (base_currency or get_settings().base_currency).upper()
    empty = {
        "as_of": as_of.isoformat() if as_of else None,
        "base_currency": base,
        "events": [],
        "totals": {"count": 0, "gross_base": 0.0, "net_base": 0.0},
        "stale_open_events": 0,
    }
    if as_of is None:
        return empty

    q = select(DividendAccrual).where(
        DividendAccrual.conid.is_not(None),
        DividendAccrual.pay_date.is_not(None),
    ).order_by(DividendAccrual.id)
    if account_id:
        q = q.where(DividendAccrual.account_id == account_id)

    groups: dict[tuple, dict] = {}
    for row in session.execute(q).scalars():
        code = (row.code or "").strip()
        key = (row.account_id, row.conid, row.ex_date, row.pay_date)
        g = groups.setdefault(key, {"po": 0, "re": 0, "latest_po": None})
        if code.endswith("Po"):
            g["po"] += 1
            g["latest_po"] = row  # ordered by id → last wins
        elif code.endswith("Re"):
            g["re"] += 1

    open_rows: list[DividendAccrual] = []
    stale = 0
    for key, g in groups.items():
        if g["po"] <= g["re"] or g["latest_po"] is None:
            continue
        if key[3] < as_of:
            stale += 1
        else:
            open_rows.append(g["latest_po"])
    empty["stale_open_events"] = stale
    if not open_rows:
        return empty

    ccys = {(r.currency or base).upper() for r in open_rows} - {base}
    fx: dict[str, float] = {base: 1.0}
    for ccy in ccys:
        rate = session.execute(
            select(FxRate.rate)
            .where(
                FxRate.from_ccy == ccy,
                FxRate.to_ccy == base,
                FxRate.rate_date <= as_of,
            )
            .order_by(FxRate.rate_date.desc())
            .limit(1)
        ).scalar()
        if rate is not None:
            fx[ccy] = float(rate)

    events = []
    for r in open_rows:
        ccy = (r.currency or base).upper()
        rate = fx.get(ccy)
        gross = _f(r.gross_amount)
        net = _f(r.net_amount)
        events.append({
            "symbol": r.symbol,
            "conid": r.conid,
            "ex_date": r.ex_date.isoformat() if r.ex_date else None,
            "pay_date": r.pay_date.isoformat(),
            "quantity": _f(r.quantity),
            "gross_rate": _f(r.gross_rate),
            "currency": ccy,
            "gross_amount": gross,
            "tax": _f(r.tax),
            "net_amount": net,
            "fx_rate_to_base": rate,
            "gross_base": gross * rate if rate is not None and gross is not None else None,
            "net_base": net * rate if rate is not None and net is not None else None,
        })
    events.sort(key=lambda e: (e["pay_date"], e["symbol"] or ""))
    return {
        "as_of": as_of.isoformat(),
        "base_currency": base,
        "events": events,
        "totals": {
            "count": len(events),
            "gross_base": sum(e["gross_base"] or 0.0 for e in events),
            "net_base": sum(e["net_base"] or 0.0 for e in events),
        },
        "stale_open_events": stale,
    }


__all__ = [
    "DIVIDEND_TYPES",
    "WITHHOLDING_TYPES",
    "dividends_by_country",
    "dividends_summary",
    "dividends_timeseries",
    "income_projection",
    "upcoming_dividends",
    "withholding_tax_by_country",
]
