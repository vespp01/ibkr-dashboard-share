"""Time-Weighted Return — chained IBKR daily TWR (`change_in_nav_daily.twr`).

Canonical method (decision D2, 2026-10): the base-currency (CHF) return of day
t is IBKR's own `ChangeInNAV.twr`, and the period TWR is its geometric chain

    TWR = prod(1 + twr_t / 100) - 1.

The row dated t is the return OF day t (start-of-day value = previous row's
ending value), so a bounded window `[start, end]` simply chains the rows in
that range: day 1 is included, and the base is the last row strictly before
`start` (`base_date`).

Display currency X (EUR, USD, ...): the CHF return is converted with the IBKR
`fx_rates` series (same report dates as NAV):

    r_X,t = (1 + r_CHF,t) * (fx_t / fx_{t-1}) - 1,   fx = X per 1 CHF.

Frankfurter is only a flagged fallback (`fx_degraded`); with no rate the
converted figures are null, never silently CHF.

Modified Dietz is kept ONLY as a reconciliation column. It is computed from the
same ChangeInNAV rows, with flows = `depositsWithdrawals + assetTransfers` on
the CIN date (see `flows.py`), and is reported next to the canonical value.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ChangeInNavDaily
from .filters import PeriodFilter
from .fx import fx_lookup

_ACCOUNT_BASE_CURRENCY = "CHF"
# GIPS: returns for periods shorter than one year are not annualised.
_ANNUALISE_MIN_DAYS = 365


def _series_quality(dates: list[date]) -> dict[str, Any]:
    if not dates:
        return {
            "is_reliable": False,
            "reason": "Sin datos NAV.",
            "rows": 0,
            "expected_days": 0,
            "coverage_ratio": 0.0,
            "max_gap_days": None,
        }
    if len(dates) == 1:
        return {
            "is_reliable": False,
            "reason": "Solo hay un punto NAV; no se puede medir retorno fiable.",
            "rows": 1,
            "expected_days": 1,
            "coverage_ratio": 1.0,
            "max_gap_days": 0,
        }

    expected_days = (dates[-1] - dates[0]).days + 1
    gaps = [(dates[idx] - dates[idx - 1]).days for idx in range(1, len(dates))]
    max_gap = max(gaps) if gaps else 0
    coverage_ratio = len(dates) / expected_days if expected_days > 0 else 1.0
    is_reliable = coverage_ratio >= 0.5 and max_gap <= 10
    if is_reliable:
        reason = "Cobertura NAV suficiente para el periodo."
    elif coverage_ratio < 0.5:
        reason = "Cobertura NAV demasiado dispersa para el periodo."
    else:
        reason = "Hay huecos demasiado largos entre snapshots NAV."
    return {
        "is_reliable": is_reliable,
        "reason": reason,
        "rows": len(dates),
        "expected_days": expected_days,
        "coverage_ratio": coverage_ratio,
        "max_gap_days": max_gap,
    }


def _f(v) -> float:
    return float(v) if v is not None else 0.0


def cin_rows(
    session: Session,
    period: PeriodFilter | None,
    account_id: str | None,
) -> tuple[list[ChangeInNavDaily], date | None]:
    """ChangeInNAV rows in the window with a daily TWR, plus the base date.

    The base date is the last CIN row strictly before `period.start` (the row
    whose ending value opens the window). For an unbounded start it is the day
    before the first row, so a benchmark anchor exists for day 1.
    """
    q = (
        select(ChangeInNavDaily)
        .where(ChangeInNavDaily.twr.is_not(None))
        .order_by(ChangeInNavDaily.snapshot_date.asc())
    )
    if account_id:
        q = q.where(ChangeInNavDaily.account_id == account_id)
    if period and period.start:
        q = q.where(ChangeInNavDaily.snapshot_date >= period.start)
    if period and period.end:
        q = q.where(ChangeInNavDaily.snapshot_date <= period.end)
    rows = list(session.execute(q).scalars().all())
    if not rows:
        return [], None

    base: date | None = None
    if period and period.start:
        bq = select(ChangeInNavDaily.snapshot_date).where(
            ChangeInNavDaily.snapshot_date < rows[0].snapshot_date,
            ChangeInNavDaily.twr.is_not(None),
        ).order_by(ChangeInNavDaily.snapshot_date.desc()).limit(1)
        if account_id:
            bq = bq.where(ChangeInNavDaily.account_id == account_id)
        base = session.execute(bq).scalar()
    if base is None:
        base = rows[0].snapshot_date - timedelta(days=1)
    return rows, base


def cin_daily_returns(
    session: Session,
    period: PeriodFilter | None,
    account_id: str | None,
) -> tuple[list[tuple[date, float]], date | None]:
    """Daily base-currency returns as decimals (0.01 = 1 %) + base date."""
    rows, base = cin_rows(session, period, account_id)
    return [(r.snapshot_date, _f(r.twr) / 100.0) for r in rows], base


def convert_returns(
    session: Session,
    returns: list[tuple[date, float]],
    base_date: date | None,
    display_currency: str,
) -> dict[str, Any]:
    """Convert CHF daily returns into `display_currency`.

    `r_X = (1 + r_CHF) * fx_t / fx_{t-1} - 1`, fx = X per CHF from `fx_rates`.
    Returns ``{"returns": [(date, r)], "fx": {date: X-per-CHF}, "fx_source",
    "fx_degraded", "complete"}``; ``complete`` is False when any needed rate is
    missing (the returns list is then empty — figures must be null).
    """
    ccy = (display_currency or _ACCOUNT_BASE_CURRENCY).upper()
    if ccy == _ACCOUNT_BASE_CURRENCY or not returns:
        return {"returns": list(returns), "fx": {}, "fx_source": "identity",
                "fx_degraded": False, "complete": True}
    dates = [d for d, _ in returns]
    if base_date is not None:
        dates = [base_date] + dates
    look = fx_lookup(session, dates, _ACCOUNT_BASE_CURRENCY, ccy)
    rates = look["rates"]
    if look["missing"] or any(rates.get(d) in (None, 0.0) for d in dates):
        return {"returns": [], "fx": rates, "fx_source": look["source"],
                "fx_degraded": True, "complete": False}
    out: list[tuple[date, float]] = []
    prev = rates[base_date] if base_date is not None else rates[dates[0]]
    for d, r in returns:
        cur = rates[d]
        out.append((d, (1 + r) * cur / prev - 1))
        prev = cur
    return {"returns": out, "fx": rates, "fx_source": look["source"],
            "fx_degraded": look["degraded"], "complete": True}


def portfolio_returns(
    session: Session,
    *,
    period: PeriodFilter | None,
    account_id: str | None,
    display_currency: str | None,
) -> dict[str, Any]:
    """Daily portfolio returns in `display_currency` (shared by TWR, alpha, TE,
    vs-benchmark so every figure on a page uses the same series and dates)."""
    ccy = (display_currency or _ACCOUNT_BASE_CURRENCY).upper()
    base_rets, base_date = cin_daily_returns(session, period, account_id)
    conv = convert_returns(session, base_rets, base_date, ccy)
    return {
        "returns": conv["returns"],
        "base_returns": base_rets,
        "base_date": base_date,
        "display_currency": ccy,
        "fx": conv["fx"],
        "fx_source": conv["fx_source"],
        "fx_degraded": conv["fx_degraded"],
        "complete": conv["complete"],
    }


def _dietz_daily(row: ChangeInNavDaily) -> float:
    """Modified Dietz of one CIN day (reconciliation only)."""
    start = _f(row.starting_value)
    end = _f(row.ending_value)
    cf = _f(row.deposits_withdrawals) + _f(row.asset_transfers)
    denom = start + 0.5 * cf
    return (end - start - cf) / denom if denom else 0.0


def twr_series(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    display_currency: str | None = None,
    summary: bool = False,
) -> dict[str, Any]:
    """Cumulative TWR series + headline numbers (see module docstring).

    `summary=True` omits the daily series (a ~2 KB payload instead of ~100 KB);
    every headline figure is identical to the full response.
    """
    target_ccy = (display_currency or _ACCOUNT_BASE_CURRENCY).upper()
    rows, base_date = cin_rows(session, period, account_id)

    if not rows:
        empty: dict[str, Any] = {
            "twr_pct": None,
            "annualised_pct": None,
            "n_days": 0,
            "display_currency": target_ccy,
            "fx_source": "identity",
            "fx_degraded": False,
            "source": "change_in_nav_daily.twr",
        }
        if not summary:
            empty["series"] = []
        return empty

    base_rets = [(r.snapshot_date, _f(r.twr) / 100.0) for r in rows]
    conv = convert_returns(session, base_rets, base_date, target_ccy)

    quality = _series_quality([r.snapshot_date for r in rows])
    first_date, last_date = rows[0].snapshot_date, rows[-1].snapshot_date
    n_days = (last_date - base_date).days if base_date else (last_date - first_date).days

    # Reconciliation: Modified Dietz on the same rows, base currency.
    dietz_cum = 1.0
    chain_base = 1.0
    for r in rows:
        dietz_cum *= 1 + _dietz_daily(r)
        chain_base *= 1 + _f(r.twr) / 100.0
    reconciliation = {
        "dietz_twr_pct": (dietz_cum - 1) * 100,
        "canonical_base_twr_pct": (chain_base - 1) * 100,
        "diff_pp": (dietz_cum - chain_base) * 100,
        "flow_definition": "depositsWithdrawals + assetTransfers on the ChangeInNAV date",
    }

    result: dict[str, Any] = {
        "twr_pct": None,
        "annualised_pct": None,
        "raw_twr_pct": None,
        "n_days": n_days,
        "from_date": first_date.isoformat(),
        "to_date": last_date.isoformat(),
        "base_date": base_date.isoformat() if base_date else None,
        "display_currency": target_ccy,
        "fx_source": conv["fx_source"],
        "fx_degraded": conv["fx_degraded"],
        "source": "change_in_nav_daily.twr",
        "quality": quality,
        "reconciliation": reconciliation,
    }
    if not conv["complete"]:
        result["note"] = f"Sin tipo de cambio CHF→{target_ccy}: cifras convertidas no disponibles."
        if not summary:
            result["series"] = []
        return result

    series: list[dict] = []
    cumulative = 1.0
    dietz = 1.0
    fx = conv["fx"]
    for row, (d, r_t) in zip(rows, conv["returns"]):
        cumulative *= 1 + r_t
        point = {
            "date": d.isoformat(),
            "nav": _f(row.ending_value) * (fx.get(d, 1.0) if fx else 1.0),
            "cashflow": (_f(row.deposits_withdrawals) + _f(row.asset_transfers))
            * (fx.get(d, 1.0) if fx else 1.0),
            "daily_return_pct": r_t * 100,
            "cumulative_pct": (cumulative - 1) * 100,
        }
        if target_ccy == _ACCOUNT_BASE_CURRENCY:
            dietz *= 1 + _dietz_daily(row)
            point["dietz_cumulative_pct"] = (dietz - 1) * 100
        series.append(point)

    raw_twr_pct = (cumulative - 1) * 100
    raw_annualised: float | None = None
    if n_days >= _ANNUALISE_MIN_DAYS and cumulative > 0:
        raw_annualised = (cumulative ** (365 / n_days) - 1) * 100

    result["raw_twr_pct"] = raw_twr_pct
    result["twr_pct"] = raw_twr_pct if quality["is_reliable"] else None
    result["annualised_pct"] = raw_annualised if quality["is_reliable"] else None
    if raw_annualised is None and n_days < _ANNUALISE_MIN_DAYS:
        result["annualised_note"] = "Ventana < 365 días: no se anualiza (GIPS)."
    result["points"] = len(series)
    if not summary:
        result["series"] = series
    return result
