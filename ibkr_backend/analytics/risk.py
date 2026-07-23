"""Risk-adjusted return metrics — Sharpe, Sortino, Max DD, Volatility, Calmar, Alpha.

Source of daily returns: `ChangeInNavDaily.twr` (the per-day TWR percentage
that IBKR computes via Modified Dietz with isolated cashflows). This is the
clean, dense series — 64 rows for Q1 2026, growing daily as the YTD pull
runs.

For periods where `ChangeInNavDaily` is sparse (pre-2026), the function
returns metrics with a `is_reliable=false` flag.

All percentages are returned as numeric percentages (e.g. 9.5 means 9.5 %),
matching the convention used elsewhere in this module.
"""

from __future__ import annotations

import math
import statistics
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import CashTransaction, ChangeInNavDaily, NavDaily
from .filters import PeriodFilter

# Same definition as metrics.DEPOSIT_TYPES — duplicated here to avoid circular import.
_DEPOSIT_TYPES = ("Deposits/Withdrawals", "Internal Transfers")

# 252 = US/Swiss trading days per year (rough average).
_TRADING_DAYS = 252

# Minimum daily-return sample size for the metrics to be meaningful.
_MIN_DAYS = 10


def _f(v) -> float:
    if v is None:
        return 0.0
    return float(v)


def _daily_returns(
    session: Session,
    period: PeriodFilter | None,
    account_id: str | None,
) -> list[tuple[date, float]]:
    """Daily returns as decimals (0.01 = 1 %) from ChangeInNavDaily.twr."""
    q = select(
        ChangeInNavDaily.snapshot_date,
        ChangeInNavDaily.twr,
    ).order_by(ChangeInNavDaily.snapshot_date.asc())
    if account_id:
        q = q.where(ChangeInNavDaily.account_id == account_id)
    if period and period.start:
        q = q.where(ChangeInNavDaily.snapshot_date >= period.start)
    if period and period.end:
        q = q.where(ChangeInNavDaily.snapshot_date <= period.end)
    out: list[tuple[date, float]] = []
    for d, twr in session.execute(q):
        if d is None or twr is None:
            continue
        # IBKR's twr is already a percentage — convert to decimal.
        out.append((d, _f(twr) / 100.0))
    return out


def _changeinnav_span(session: Session, account_id: str | None) -> tuple[date | None, date | None]:
    """Earliest / latest snapshot_date in ChangeInNavDaily (the source for these metrics)."""
    from sqlalchemy import func as _func
    q = select(_func.min(ChangeInNavDaily.snapshot_date), _func.max(ChangeInNavDaily.snapshot_date))
    if account_id:
        q = q.where(ChangeInNavDaily.account_id == account_id)
    row = session.execute(q).first()
    return (row[0], row[1]) if row else (None, None)


def _portfolio_inception(session: Session, account_id: str | None) -> date | None:
    """Earliest dated event in the account — first NAV snapshot or first
    deposit. Used as the 'true start' when the user selects period=all
    (PeriodFilter then carries start=None, which would otherwise mask the
    truncation against ChangeInNAV's much shorter span).
    """
    from sqlalchemy import func as _func
    q1 = select(_func.min(NavDaily.snapshot_date))
    q2 = select(_func.min(CashTransaction.settle_date)).where(
        CashTransaction.type.in_(_DEPOSIT_TYPES)
    )
    if account_id:
        q1 = q1.where(NavDaily.account_id == account_id)
        q2 = q2.where(CashTransaction.account_id == account_id)
    candidates = [d for d in (session.execute(q1).scalar(), session.execute(q2).scalar()) if d]
    return min(candidates) if candidates else None


def _truncation_info(
    session: Session,
    period: PeriodFilter | None,
    account_id: str | None,
    series_first: date | None,
    series_last: date | None,
    coverage_first: date | None,
    coverage_last: date | None,
) -> dict[str, Any]:
    """Detect whether the user-requested period is wider than what daily-TWR
    data actually covers, and return a payload the frontend can render clearly.

    When `period.start` is None (user selected "all"), we treat the request as
    "from inception" — earliest of (first NAV snapshot, first deposit). Same
    for `period.end` → today. This catches the silent-truncation bug where
    selecting "all" was reporting only the 97 days that ChangeInNAV covers.
    """
    from datetime import date as _date
    if period and period.start:
        req_from = period.start
    else:
        req_from = _portfolio_inception(session, account_id) or coverage_first

    if period and period.end:
        req_to = period.end
    else:
        req_to = _date.today()

    is_truncated = False
    truncation_reason = None
    if req_from and series_first and req_from < series_first:
        is_truncated = True
        truncation_reason = (
            f"ChangeInNAV daily series starts {series_first.isoformat()}; "
            f"requested window starts {req_from.isoformat()}."
        )
    elif req_to and series_last and req_to > series_last and (req_to - series_last).days > 7:
        is_truncated = True
        truncation_reason = (
            f"ChangeInNAV daily series ends {series_last.isoformat()}; "
            f"requested window ends {req_to.isoformat()}."
        )
    return {
        "requested_from": req_from.isoformat() if req_from else None,
        "requested_to": req_to.isoformat() if req_to else None,
        "is_truncated": is_truncated,
        "truncation_reason": truncation_reason,
    }


def risk_metrics(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    risk_free_rate_pct: float = 0.0,
) -> dict[str, Any]:
    """Sharpe, Sortino, Volatility, Max Drawdown, Calmar — all from daily TWRs.

    Source: `ChangeInNavDaily.twr` (IBKR's pre-computed daily TWR). Only
    available only from the first Flex-covered daily TWR date, so for periods
    that extend earlier the response carries `is_truncated=True` plus the actual
    window used. The frontend should surface this prominently — users
    selecting "all-time" expect coverage from 2023, not just Q1 2026.

    `risk_free_rate_pct` is the annual risk-free rate as a percentage. For
    CHF, SARON 1-month was ~0.95 % in early 2026. Default 0 % so Sharpe
    reduces to return-over-volatility.
    """
    series = _daily_returns(session, period, account_id)
    n = len(series)
    coverage_first, coverage_last = _changeinnav_span(session, account_id)
    trunc = _truncation_info(
        session,
        period,
        account_id,
        series[0][0] if series else None,
        series[-1][0] if series else None,
        coverage_first,
        coverage_last,
    )

    if n < _MIN_DAYS:
        return {
            "n_days": n,
            "is_reliable": False,
            "reason": f"Sólo {n} días de retorno (mínimo {_MIN_DAYS} para métricas fiables)",
            "from_date": series[0][0].isoformat() if series else None,
            "to_date": series[-1][0].isoformat() if series else None,
            **trunc,
            "risk_free_rate_pct": risk_free_rate_pct,
            "annualised_return_pct": None,
            "annualised_volatility_pct": None,
            "annualised_downside_volatility_pct": None,
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "max_drawdown_pct": None,
            "calmar_ratio": None,
        }

    rets = [r for _, r in series]
    std_daily = statistics.stdev(rets)

    # Annualise volatility (vol scales with √time).
    annual_vol = std_daily * math.sqrt(_TRADING_DAYS) * 100  # %

    # Annualise return GEOMETRICALLY — compound the realised series and scale
    # to a year. Arithmetic mean × 252 overstates the compound return by an
    # amount proportional to variance ("variance drag"), which would then bias
    # the Sharpe / Sortino ratios upward.
    cumulative = math.prod(1 + r for r in rets)
    if cumulative > 0:
        annual_return = (cumulative ** (_TRADING_DAYS / n) - 1) * 100
    else:
        annual_return = -100.0  # account wiped — degenerate case

    # Sortino — semi-deviation of the FULL series (positive returns count as
    # zero), root-mean-square style. statistics.stdev(neg) was wrong: it
    # measures spread within the negative subset, not deviation below zero.
    # Formula: sqrt( sum(r^2 for r < 0) / (n - 1) ) — standard semi-deviation
    # (Sortino's original definition uses target=0 = risk-free already netted).
    neg = [r for r in rets if r < 0]
    if neg:
        downside_var = sum(r * r for r in neg) / (n - 1)
        annual_downside_vol = math.sqrt(downside_var) * math.sqrt(_TRADING_DAYS) * 100
    else:
        annual_downside_vol = 0.0

    sharpe = (annual_return - risk_free_rate_pct) / annual_vol if annual_vol > 0 else None
    sortino = (
        (annual_return - risk_free_rate_pct) / annual_downside_vol
        if annual_downside_vol > 0 else None
    )

    # Max drawdown — chain returns into a synthetic equity curve starting at 1.
    peak = 1.0
    curve = 1.0
    max_dd = 0.0
    dd_peak_date: date | None = None
    dd_trough_date: date | None = None
    cur_peak_date: date | None = series[0][0]
    for d, r in series:
        curve *= (1 + r)
        if curve > peak:
            peak = curve
            cur_peak_date = d
        dd = (curve / peak - 1) if peak > 0 else 0
        if dd < max_dd:
            max_dd = dd
            dd_peak_date = cur_peak_date
            dd_trough_date = d
    max_dd_pct = max_dd * 100

    calmar = annual_return / abs(max_dd_pct) if max_dd_pct != 0 else None

    return {
        "n_days": n,
        "is_reliable": True,
        "from_date": series[0][0].isoformat(),
        "to_date": series[-1][0].isoformat(),
        **trunc,
        "risk_free_rate_pct": risk_free_rate_pct,
        "annualised_return_pct": annual_return,
        "annualised_volatility_pct": annual_vol,
        "annualised_downside_volatility_pct": annual_downside_vol,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown_pct": max_dd_pct,
        "max_drawdown_peak_date": dd_peak_date.isoformat() if dd_peak_date else None,
        "max_drawdown_trough_date": dd_trough_date.isoformat() if dd_trough_date else None,
        "calmar_ratio": calmar,
        "negative_days": len(neg),
        "positive_days": len([r for r in rets if r > 0]),
    }


def alpha_vs_benchmark(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    benchmark_currency: str = "CHF",
) -> dict[str, Any]:
    """Alpha (simple diff) + Beta vs S&P 500 over the same window.

    Beta is the slope of (portfolio_daily_return) vs (benchmark_daily_return);
    alpha is the intercept-equivalent at the period level (portfolio_return -
    beta × benchmark_return). For < 30 aligned days the regression is
    unreliable and we report only the simple return diff.
    """
    from ..benchmarks.sp500 import fetch_sp500_in_currency

    series = _daily_returns(session, period, account_id)
    if not series:
        return {"is_reliable": False, "reason": "no portfolio returns in period"}

    start = series[0][0]
    end = series[-1][0]
    try:
        bench_rows = fetch_sp500_in_currency(benchmark_currency, start, end)
    except Exception as exc:  # noqa: BLE001
        return {"is_reliable": False, "reason": f"benchmark fetch failed: {exc}"}
    if not bench_rows:
        return {"is_reliable": False, "reason": "benchmark returned no rows"}

    # Build daily-return map for the benchmark.
    bench_by_date: dict[str, float] = {}
    prev: float | None = None
    for row in bench_rows:
        cur = row["close_base"]
        if prev is not None and prev > 0:
            bench_by_date[row["date"]] = (cur / prev) - 1
        prev = cur

    # Align: only days where both series have a return.
    pairs: list[tuple[float, float]] = []
    for d, r in series:
        b = bench_by_date.get(d.isoformat())
        if b is not None:
            pairs.append((r, b))

    if len(pairs) < 5:
        return {
            "is_reliable": False,
            "reason": f"only {len(pairs)} aligned days (need ≥ 30 for beta)",
            "aligned_days": len(pairs),
        }

    port_rets = [p for p, _ in pairs]
    bench_rets = [b for _, b in pairs]

    port_total_pct = (math.prod(1 + r for r in port_rets) - 1) * 100
    bench_total_pct = (math.prod(1 + r for r in bench_rets) - 1) * 100
    simple_alpha_pct = port_total_pct - bench_total_pct

    # Beta — slope of portfolio vs benchmark.
    if len(pairs) >= 30:
        n = len(pairs)
        mean_p = sum(port_rets) / n
        mean_b = sum(bench_rets) / n
        cov = sum((p - mean_p) * (b - mean_b) for p, b in pairs) / (n - 1)
        var_b = sum((b - mean_b) ** 2 for b in bench_rets) / (n - 1)
        beta = cov / var_b if var_b > 0 else None
        # Jensen's alpha (no risk-free) at period level.
        jensen_alpha_pct = port_total_pct - (beta * bench_total_pct) if beta is not None else None
    else:
        beta = None
        jensen_alpha_pct = None

    coverage_first, coverage_last = _changeinnav_span(session, account_id)
    trunc = _truncation_info(session, period, account_id, start, end, coverage_first, coverage_last)
    return {
        "is_reliable": True,
        "aligned_days": len(pairs),
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        **trunc,
        "benchmark": "SP500",
        "benchmark_currency": benchmark_currency,
        "portfolio_total_return_pct": port_total_pct,
        "benchmark_total_return_pct": bench_total_pct,
        "simple_alpha_pct": simple_alpha_pct,
        "beta": beta,
        "jensen_alpha_pct": jensen_alpha_pct,
    }


def _tracking_error(
    session: Session,
    period: PeriodFilter | None,
    account_id: str | None,
    benchmark_currency: str,
) -> dict[str, Any]:
    """Annualised tracking error + information ratio on the daily-TWR segment.

    Tracking error = stdev of (portfolio_daily_return − benchmark_daily_return),
    annualised. Information ratio = annualised mean excess return / TE. Both
    only meaningful on the dense daily series (ChangeInNavDaily), so we require
    ≥ 30 aligned days and otherwise return nulls.
    """
    from ..benchmarks.sp500 import fetch_sp500_in_currency

    series = _daily_returns(session, period, account_id)
    if len(series) < 30:
        return {"tracking_error_pct": None, "information_ratio": None, "te_days": len(series)}
    start, end = series[0][0], series[-1][0]
    try:
        bench_rows = fetch_sp500_in_currency(benchmark_currency, start, end)
    except Exception:  # noqa: BLE001
        return {"tracking_error_pct": None, "information_ratio": None, "te_days": 0}

    bench_by_date: dict[str, float] = {}
    prev: float | None = None
    for row in bench_rows:
        cur = row["close_base"]
        if prev is not None and prev > 0:
            bench_by_date[row["date"]] = (cur / prev) - 1
        prev = cur

    diffs = [r - bench_by_date[d.isoformat()] for d, r in series if d.isoformat() in bench_by_date]
    if len(diffs) < 30:
        return {"tracking_error_pct": None, "information_ratio": None, "te_days": len(diffs)}

    te_daily = statistics.stdev(diffs)
    te_annual = te_daily * math.sqrt(_TRADING_DAYS) * 100
    mean_excess_annual = (sum(diffs) / len(diffs)) * _TRADING_DAYS * 100
    ir = mean_excess_annual / te_annual if te_annual > 0 else None
    return {"tracking_error_pct": te_annual, "information_ratio": ir, "te_days": len(diffs)}


def vs_benchmark(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    benchmark_currency: str = "CHF",
    display_currency: str = "CHF",
) -> dict[str, Any]:
    """Unified, honest portfolio-vs-S&P comparison for the Rentabilidad chart.

    Honesty model: the benchmark is sampled on the SAME dates the portfolio TWR
    has points. Where the portfolio NAV series is sparse (pre-2026 = only
    year-end snapshots), both lines share those few points — never a smooth
    portfolio line drawn against a daily benchmark. Both series are rebased to
    100 on the first common date so they overlay apples-to-apples.

    Two comparison layers, each honest about its own window:
      - level_comparison: total return of portfolio vs benchmark over the FULL
        common window (just the endpoint ratio of the rebased indices).
      - risk_comparison: beta / Jensen alpha / tracking error / information
        ratio, computed only on the DAILY-resolution segment (the dense
        ChangeInNavDaily series), with that segment reported in `resolution`.
    """
    from datetime import date as _date

    from ..benchmarks.sp500 import fetch_sp500_in_currency
    from .twr import twr_series

    tw = twr_series(session, period=period, account_id=account_id, display_currency=display_currency)
    series = tw.get("series") or []
    if len(series) < 2:
        return {"is_reliable": False, "reason": "Serie TWR insuficiente para comparar.", "points": []}

    # Honesty guard: if the underlying NAV series is too gappy, the chained
    # Modified-Dietz TWR produces spurious returns (a 77-day gap with deposits
    # in it can read as a −40 % "return"). Refuse to publish a misleading level
    # comparison — mirror how twr_series itself flags this via quality.
    quality = tw.get("quality") or {}
    if quality.get("is_reliable") is False:
        return {
            "is_reliable": False,
            "reason": "TWR no fiable en este periodo (cobertura NAV insuficiente): "
            + (quality.get("reason") or ""),
            "resolution": {
                "points": len(series),
                "max_gap_days": quality.get("max_gap_days"),
                "coverage_ratio": quality.get("coverage_ratio"),
                "note": (
                    "La serie NAV tiene huecos que invalidan el TWR encadenado en este "
                    "periodo. Usa un periodo con NAV diaria densa (p.ej. 1Y/YTD) o completa "
                    "el histórico de NAV."
                ),
            },
            "twr_quality": quality,
            "points": [],
        }

    port_pts = [(_date.fromisoformat(s["date"]), 1.0 + s["cumulative_pct"] / 100.0) for s in series]
    start, end = port_pts[0][0], port_pts[-1][0]

    try:
        bench_rows = fetch_sp500_in_currency(benchmark_currency, start, end)
    except Exception as exc:  # noqa: BLE001
        return {"is_reliable": False, "reason": f"Benchmark fetch failed: {exc}", "points": []}
    if not bench_rows:
        return {"is_reliable": False, "reason": "Benchmark sin datos en la ventana.", "points": []}

    bench_close = sorted(
        (_date.fromisoformat(r["date"]), float(r["close_base"])) for r in bench_rows
    )

    def _asof(d: _date) -> float | None:
        """Latest benchmark close on/before d (binary search)."""
        lo, hi, res = 0, len(bench_close) - 1, None
        while lo <= hi:
            mid = (lo + hi) // 2
            if bench_close[mid][0] <= d:
                res = bench_close[mid][1]
                lo = mid + 1
            else:
                hi = mid - 1
        return res

    aligned: list[tuple[_date, float, float]] = []
    port0: float | None = None
    bench0: float | None = None
    for d, pidx in port_pts:
        bc = _asof(d)
        if bc is None:
            continue
        if port0 is None:
            if bc == 0.0 or pidx == 0.0:
                continue  # skip until a non-zero anchor — avoids /0 on rebase
            port0, bench0 = pidx, bc
        aligned.append((d, pidx / port0 * 100.0, bc / bench0 * 100.0))
    if len(aligned) < 2:
        return {"is_reliable": False, "reason": "Sin solapamiento cartera/benchmark.", "points": []}

    gaps = [(aligned[i][0] - aligned[i - 1][0]).days for i in range(1, len(aligned))]
    max_gap = max(gaps) if gaps else 0
    # Daily segment = trailing run with all gaps <= 5 days.
    daily_from = aligned[0][0]
    for i in range(len(aligned) - 1, 0, -1):
        if (aligned[i][0] - aligned[i - 1][0]).days > 5:
            daily_from = aligned[i][0]
            break

    port_total = aligned[-1][1] / aligned[0][1] - 1
    bench_total = aligned[-1][2] / aligned[0][2] - 1

    risk = alpha_vs_benchmark(
        session, period=period, account_id=account_id, benchmark_currency=benchmark_currency
    )
    te = _tracking_error(session, period, account_id, benchmark_currency)

    return {
        "is_reliable": True,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "display_currency": display_currency.upper(),
        "benchmark": "SP500",
        "benchmark_currency": benchmark_currency.upper(),
        "resolution": {
            "points": len(aligned),
            "max_gap_days": max_gap,
            "daily_segment_from": daily_from.isoformat(),
            "is_sparse": max_gap > 7,
            "note": (
                "Cartera y S&P comparten exactamente las mismas fechas: ningún tramo "
                "dibuja una curva suave contra otra dentada. Donde el NAV es disperso "
                f"(pre-{daily_from.year}) ambos son rectas entre snapshots; el segmento "
                f"diario fiable empieza el {daily_from.isoformat()}."
            ),
        },
        "level_comparison": {
            "portfolio_return_pct": port_total * 100,
            "benchmark_return_pct": bench_total * 100,
            "excess_return_pct": (port_total - bench_total) * 100,
        },
        "risk_comparison": {**(risk if isinstance(risk, dict) else {}), **te},
        "twr_quality": tw.get("quality"),
        "points": [
            {"date": d.isoformat(), "portfolio": round(p, 4), "benchmark": round(b, 4)}
            for d, p, b in aligned
        ],
    }
