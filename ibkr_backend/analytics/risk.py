"""Risk-adjusted return metrics — Sharpe, Sortino, Max DD, Volatility, Calmar, Alpha.

Source of daily returns: `ChangeInNavDaily.twr` (IBKR's per-day TWR, dense:
one row per trading day since the account opened). The same series is the
canonical TWR (`twr.py`), so level and risk figures always agree.

Conventions:

* Calendar-time annualisation. Observations per year = n / (calendar days /
  365), not a fixed 252 (CIN has 261-262 rows per year). Annualised return,
  Calmar and any ratio built on them are null for windows < 365 days (GIPS).
  Short windows still get Sharpe/Sortino, computed from the arithmetic mean
  daily excess return (`sharpe_method = "arithmetic"`), never extrapolated.
* Risk-free: a documented per-year CHF table, default 0 % for every year
  (no external fetch). Override per year with the env var
  `RISK_FREE_CHF_PCT_BY_YEAR="2023:1.7,2024:1.2"` or per call with the
  `risk_free_rate_pct` query parameter. The payload says which one was used.
* Alpha / beta / tracking error / information ratio use ONE currency: the
  portfolio's CHF daily returns are converted into the benchmark currency
  with the same `fx_rates` the benchmark uses (`twr.convert_returns`).
* Portfolio returns are compounded between benchmark dates, so US holidays do
  not drop a portfolio day (B48) and Swiss holidays do not drop a benchmark
  move.

All percentages are numeric percentages (9.5 means 9.5 %).
"""

from __future__ import annotations

import math
import os
import statistics
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import ChangeInNavDaily, NavDaily
from .filters import PeriodFilter
from .twr import cin_daily_returns, portfolio_returns

# Minimum daily-return sample size for the metrics to be meaningful.
_MIN_DAYS = 10
# GIPS: no annualisation below one year.
_ANNUALISE_MIN_DAYS = 365
# Tolerance (calendar days) between the requested start and the first daily
# return before the window is flagged as truncated (weekend / holiday starts).
_START_TOLERANCE_DAYS = 5
# Calendar look-back for the benchmark anchor (as-of close before the start).
_BENCH_LOOKBACK_DAYS = 7

RISK_FREE_ENV = "RISK_FREE_CHF_PCT_BY_YEAR"


def _f(v) -> float:
    if v is None:
        return 0.0
    return float(v)


def risk_free_table() -> dict[int, float]:
    """Annual CHF risk-free rate in % per calendar year. Empty = 0 % always.

    Set `RISK_FREE_CHF_PCT_BY_YEAR="2023:1.7,2024:1.2"` to use a SARON-like
    series; years not listed use 0.
    """
    out: dict[int, float] = {}
    for part in os.environ.get(RISK_FREE_ENV, "").split(","):
        part = part.strip()
        if not part:
            continue
        year, _, rate = part.partition(":")
        try:
            out[int(year)] = float(rate)
        except ValueError:
            continue
    return out


def window_risk_free_pct(start: date, end: date) -> float:
    """Calendar-day-weighted average annual risk-free rate over [start, end]."""
    table = risk_free_table()
    if not table or end <= start:
        return 0.0
    total_days = (end - start).days
    acc = 0.0
    d = start
    while d < end:
        year_end = min(date(d.year + 1, 1, 1), end)
        acc += table.get(d.year, 0.0) * (year_end - d).days
        d = year_end
    return acc / total_days


def _daily_returns(
    session: Session,
    period: PeriodFilter | None,
    account_id: str | None,
) -> list[tuple[date, float]]:
    """Daily returns as decimals (0.01 = 1 %) from ChangeInNavDaily.twr."""
    return cin_daily_returns(session, period, account_id)[0]


def _changeinnav_span(session: Session, account_id: str | None) -> tuple[date | None, date | None]:
    """Earliest / latest snapshot_date in ChangeInNavDaily (the source for these metrics)."""
    q = select(func.min(ChangeInNavDaily.snapshot_date), func.max(ChangeInNavDaily.snapshot_date))
    if account_id:
        q = q.where(ChangeInNavDaily.account_id == account_id)
    row = session.execute(q).first()
    return (row[0], row[1]) if row else (None, None)


def _portfolio_inception(session: Session, account_id: str | None) -> date | None:
    """First dated NAV snapshot WITH a NAV value — the true start when the user
    selects period=all (PeriodFilter then carries start=None). NAV-less rows and
    deposit settle dates do not count: they predate the first valued day."""
    q = select(func.min(NavDaily.snapshot_date)).where(NavDaily.nav_total.is_not(None))
    if account_id:
        q = q.where(NavDaily.account_id == account_id)
    return session.execute(q).scalar()


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

    When `period.start` is None (user selected "all"), the request means "from
    inception" — the first valued NAV day. A start within
    `_START_TOLERANCE_DAYS` calendar days before the first daily return is not
    truncation (weekend / holiday / first-deposit lag).
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
    if req_from and series_first and (series_first - req_from).days > _START_TOLERANCE_DAYS:
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
    risk_free_rate_pct: float | None = None,
) -> dict[str, Any]:
    """Sharpe, Sortino, Volatility, Max Drawdown, Calmar — all from daily TWRs.

    Source: `ChangeInNavDaily.twr`, base currency (CHF; the payload says so).
    `risk_free_rate_pct=None` uses the per-year table (default 0 %);
    an explicit value overrides it. See the module docstring for conventions.
    """
    series, base_date = cin_daily_returns(session, period, account_id)
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

    if risk_free_rate_pct is None and series:
        rf_pct = window_risk_free_pct(base_date or series[0][0], series[-1][0])
        rf_source = "table (CHF per-year, default 0 %)"
    else:
        rf_pct = risk_free_rate_pct or 0.0
        rf_source = "param" if risk_free_rate_pct is not None else "table (CHF per-year, default 0 %)"

    if n < _MIN_DAYS:
        return {
            "n_days": n,
            "is_reliable": False,
            "reason": f"Sólo {n} días de retorno (mínimo {_MIN_DAYS} para métricas fiables)",
            "from_date": series[0][0].isoformat() if series else None,
            "to_date": series[-1][0].isoformat() if series else None,
            "currency": "CHF",
            **trunc,
            "risk_free_rate_pct": rf_pct,
            "risk_free_source": rf_source,
            "annualised_return_pct": None,
            "annualised_volatility_pct": None,
            "annualised_downside_volatility_pct": None,
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "max_drawdown_pct": None,
            "calmar_ratio": None,
        }

    rets = [r for _, r in series]
    first_d, last_d = series[0][0], series[-1][0]
    window_days = (last_d - (base_date or first_d)).days or 1
    ppy = n / (window_days / 365.0)  # observations per calendar year
    annualise = window_days >= _ANNUALISE_MIN_DAYS

    std_daily = statistics.stdev(rets)
    annual_vol = std_daily * math.sqrt(ppy) * 100  # %

    # Annualise return GEOMETRICALLY, only for windows of at least a year.
    cumulative = math.prod(1 + r for r in rets)
    annual_return: float | None = None
    if annualise:
        annual_return = (
            (cumulative ** (365.0 / window_days) - 1) * 100 if cumulative > 0 else -100.0
        )

    # Sortino — semi-deviation of the FULL series (positive returns count as
    # zero): sqrt( sum(r^2 for r < 0) / (n - 1) ).
    neg = [r for r in rets if r < 0]
    if neg:
        downside_var = sum(r * r for r in neg) / (n - 1)
        annual_downside_vol = math.sqrt(downside_var) * math.sqrt(ppy) * 100
    else:
        annual_downside_vol = 0.0

    if annual_return is not None:
        excess_annual = annual_return - rf_pct
        sharpe_method = "geometric"
    else:
        # Window < 1 year: arithmetic mean daily excess return x periods/year.
        rf_period = (1 + rf_pct / 100) ** (1 / ppy) - 1
        excess_annual = (sum(rets) / n - rf_period) * ppy * 100
        sharpe_method = "arithmetic"
    sharpe = excess_annual / annual_vol if annual_vol > 0 else None
    sortino = excess_annual / annual_downside_vol if annual_downside_vol > 0 else None

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

    calmar = (
        annual_return / abs(max_dd_pct)
        if annual_return is not None and max_dd_pct != 0 else None
    )

    return {
        "n_days": n,
        "is_reliable": True,
        "from_date": first_d.isoformat(),
        "to_date": last_d.isoformat(),
        "window_days": window_days,
        "currency": "CHF",
        **trunc,
        "risk_free_rate_pct": rf_pct,
        "risk_free_source": rf_source,
        "annualised": annualise,
        "annualised_return_pct": annual_return,
        "annualised_volatility_pct": annual_vol,
        "annualised_downside_volatility_pct": annual_downside_vol,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "sharpe_method": sharpe_method,
        "max_drawdown_pct": max_dd_pct,
        "max_drawdown_peak_date": dd_peak_date.isoformat() if dd_peak_date else None,
        "max_drawdown_trough_date": dd_trough_date.isoformat() if dd_trough_date else None,
        "calmar_ratio": calmar,
        "negative_days": len(neg),
        "positive_days": len([r for r in rets if r > 0]),
    }


# --------------------------------------------------------------------------
# Portfolio vs benchmark — paired returns on common dates
# --------------------------------------------------------------------------
def _pair_returns(
    port: list[tuple[date, float]],
    bench_close: list[tuple[date, float]],
    base_date: date | None,
) -> dict[str, Any]:
    """Pair portfolio and benchmark returns on the dates both have a value.

    Walks the merged timeline. Portfolio daily returns and benchmark daily
    returns accumulate (compound) until a date present in BOTH series, where
    one pair is emitted. Result: a US holiday (no benchmark bar) compounds the
    portfolio's day into the next common date, a Swiss holiday (no CIN row)
    compounds the benchmark's move — no portfolio day is dropped except those
    after the last common date (`unpaired_days`).

    The benchmark is anchored at the last close on/before `base_date`, so day 1
    of the window is included (B07).
    """
    bench_close = sorted(bench_close)
    anchor_idx = None
    for i, (d, _) in enumerate(bench_close):
        if base_date is not None and d <= base_date:
            anchor_idx = i
        else:
            break
    bench_ret: dict[date, float] = {}
    start_i = anchor_idx if anchor_idx is not None else 0
    for i in range(start_i + 1, len(bench_close)):
        prev = bench_close[i - 1][1]
        if prev > 0:
            bench_ret[bench_close[i][0]] = bench_close[i][1] / prev - 1
    port_ret = dict(port)

    # Without an anchor the first benchmark bar has no return: start after it.
    skip_until = bench_close[0][0] if (anchor_idx is None and bench_close) else None

    pairs: list[tuple[date, float, float]] = []
    rp = rb = 1.0
    pending_port_days = 0
    for d in sorted(set(port_ret) | set(bench_ret)):
        if skip_until is not None and d <= skip_until:
            continue
        in_p, in_b = d in port_ret, d in bench_ret
        if in_p:
            rp *= 1 + port_ret[d]
            pending_port_days += 1
        if in_b:
            rb *= 1 + bench_ret[d]
        if in_p and in_b:
            pairs.append((d, rp - 1, rb - 1))
            rp = rb = 1.0
            pending_port_days = 0
    return {
        "pairs": pairs,
        "unpaired_days": pending_port_days,
        "anchor_date": bench_close[anchor_idx][0] if anchor_idx is not None else None,
    }


def _benchmark_pairs(
    session: Session,
    *,
    period: PeriodFilter | None,
    account_id: str | None,
    currency: str,
) -> dict[str, Any]:
    """Portfolio + benchmark returns in `currency`, paired. Never raises: an
    `error` key explains why no comparison is possible."""
    from ..benchmarks.sp500 import fetch_sp500_in_currency

    pr = portfolio_returns(
        session, period=period, account_id=account_id, display_currency=currency
    )
    if not pr["base_returns"]:
        return {"error": "no portfolio returns in period", "port": pr}
    if not pr["complete"]:
        return {
            "error": f"sin tipo de cambio CHF→{currency}: comparación no disponible",
            "port": pr,
        }
    rets = pr["returns"]
    start, end = rets[0][0], rets[-1][0]
    base_date = pr["base_date"] or start
    try:
        bench_rows = fetch_sp500_in_currency(
            currency, base_date - timedelta(days=_BENCH_LOOKBACK_DAYS), end
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Benchmark fetch failed: {exc}", "port": pr}
    if not bench_rows:
        return {"error": "benchmark returned no rows", "port": pr}
    bench_close = [(date.fromisoformat(r["date"]), float(r["close_base"])) for r in bench_rows]
    paired = _pair_returns(rets, bench_close, base_date)
    fx_sources = {r.get("fx_source") for r in bench_rows if r.get("fx_source")}
    return {
        "port": pr,
        "start": start,
        "end": end,
        "base_date": base_date,
        "bench_close": bench_close,
        "bench_fx_source": ",".join(sorted(fx_sources)) or None,
        **paired,
    }


def _risk_from_pairs(pairs: list[tuple[date, float, float]]) -> dict[str, Any]:
    """Totals, beta, Jensen alpha, tracking error and IR from paired returns."""
    port_rets = [p for _, p, _ in pairs]
    bench_rets = [b for _, _, b in pairs]
    port_total_pct = (math.prod(1 + r for r in port_rets) - 1) * 100
    bench_total_pct = (math.prod(1 + r for r in bench_rets) - 1) * 100
    out: dict[str, Any] = {
        "portfolio_total_return_pct": port_total_pct,
        "benchmark_total_return_pct": bench_total_pct,
        "simple_alpha_pct": port_total_pct - bench_total_pct,
        "beta": None,
        "jensen_alpha_pct": None,
        "tracking_error_pct": None,
        "information_ratio": None,
        "te_days": len(pairs),
    }
    if len(pairs) >= 30:
        n = len(pairs)
        mean_p = sum(port_rets) / n
        mean_b = sum(bench_rets) / n
        cov = sum((p - mean_p) * (b - mean_b) for p, b in zip(port_rets, bench_rets)) / (n - 1)
        var_b = sum((b - mean_b) ** 2 for b in bench_rets) / (n - 1)
        beta = cov / var_b if var_b > 0 else None
        out["beta"] = beta
        # Jensen's alpha (no risk-free) at period level.
        out["jensen_alpha_pct"] = (
            port_total_pct - beta * bench_total_pct if beta is not None else None
        )
        # Tracking error / IR: calendar-time annualisation.
        diffs = [p - b for p, b in zip(port_rets, bench_rets)]
        span_days = max((pairs[-1][0] - pairs[0][0]).days, 1)
        ppy = n / (span_days / 365.0)
        te_annual = statistics.stdev(diffs) * math.sqrt(ppy) * 100
        mean_excess_annual = (sum(diffs) / n) * ppy * 100
        out["tracking_error_pct"] = te_annual
        out["information_ratio"] = mean_excess_annual / te_annual if te_annual > 0 else None
    return out


def alpha_vs_benchmark(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    benchmark_currency: str = "CHF",
    _bp: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Alpha (simple + Jensen) + Beta vs the S&P 500 over the same window.

    Beta is the slope of portfolio vs benchmark returns paired on common dates
    (see `_pair_returns`); both series are in `benchmark_currency` (the
    portfolio's CHF returns are converted with the same `fx_rates`). For fewer
    than 30 pairs only the simple return difference is reported.
    """
    bp = _bp or _benchmark_pairs(
        session, period=period, account_id=account_id, currency=benchmark_currency
    )
    if "error" in bp:
        return {"is_reliable": False, "reason": bp["error"]}
    pairs = bp["pairs"]
    if len(pairs) < 5:
        return {
            "is_reliable": False,
            "reason": f"only {len(pairs)} aligned days (need ≥ 30 for beta)",
            "aligned_days": len(pairs),
        }

    risk = _risk_from_pairs(pairs)
    coverage_first, coverage_last = _changeinnav_span(session, account_id)
    trunc = _truncation_info(
        session, period, account_id, bp["start"], bp["end"], coverage_first, coverage_last
    )
    return {
        "is_reliable": True,
        "aligned_days": len(pairs),
        "unpaired_days": bp["unpaired_days"],
        "portfolio_days": len(bp["port"]["returns"]),
        "from_date": bp["start"].isoformat(),
        "to_date": bp["end"].isoformat(),
        **trunc,
        "benchmark": "SP500",
        "benchmark_type": "price index (^GSPC), not total return",
        "benchmark_currency": benchmark_currency.upper(),
        "fx_source": bp["port"]["fx_source"],
        "benchmark_fx_source": bp["bench_fx_source"],
        "portfolio_total_return_pct": risk["portfolio_total_return_pct"],
        "benchmark_total_return_pct": risk["benchmark_total_return_pct"],
        "simple_alpha_pct": risk["simple_alpha_pct"],
        "beta": risk["beta"],
        "jensen_alpha_pct": risk["jensen_alpha_pct"],
    }


def vs_benchmark(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
    benchmark_currency: str = "CHF",
    display_currency: str = "CHF",
) -> dict[str, Any]:
    """Unified portfolio-vs-S&P comparison for the Rentabilidad chart.

    ONE series, ONE set of dates: portfolio and benchmark are rebased to 100 on
    `base_date` (last NAV day before the window; the benchmark uses its as-of
    close there) and sampled on the dates both have a value, with returns
    compounded between those dates. `level_comparison`, `risk_comparison` and
    the points all derive from the same pairs, so a page shows a single
    portfolio return and a single excess figure.
    """
    from .twr import twr_series

    ccy = (display_currency or benchmark_currency).upper()
    tw = twr_series(
        session, period=period, account_id=account_id, display_currency=ccy, summary=True
    )
    quality = tw.get("quality") or {}
    if not quality:
        return {"is_reliable": False, "reason": "Serie TWR insuficiente para comparar.", "points": []}
    if quality.get("is_reliable") is False:
        return {
            "is_reliable": False,
            "reason": "TWR no fiable en este periodo (cobertura NAV insuficiente): "
            + (quality.get("reason") or ""),
            "resolution": {
                "max_gap_days": quality.get("max_gap_days"),
                "coverage_ratio": quality.get("coverage_ratio"),
            },
            "twr_quality": quality,
            "points": [],
        }

    bp = _benchmark_pairs(
        session, period=period, account_id=account_id, currency=ccy
    )
    if "error" in bp:
        return {"is_reliable": False, "reason": bp["error"], "points": []}
    pairs = bp["pairs"]
    if len(pairs) < 2:
        return {"is_reliable": False, "reason": "Sin solapamiento cartera/benchmark.", "points": []}

    base_date: date = bp["base_date"]
    points = [{"date": base_date.isoformat(), "portfolio": 100.0, "benchmark": 100.0}]
    pidx = bidx = 100.0
    for d, rp, rb in pairs:
        pidx *= 1 + rp
        bidx *= 1 + rb
        points.append({"date": d.isoformat(), "portfolio": round(pidx, 4), "benchmark": round(bidx, 4)})

    risk = _risk_from_pairs(pairs)
    port_total = risk["portfolio_total_return_pct"]
    bench_total = risk["benchmark_total_return_pct"]
    gaps = [(pairs[i][0] - pairs[i - 1][0]).days for i in range(1, len(pairs))]

    risk_comparison = alpha_vs_benchmark(
        session, period=period, account_id=account_id,
        benchmark_currency=ccy, _bp=bp,
    )
    risk_comparison.update({
        "tracking_error_pct": risk["tracking_error_pct"],
        "information_ratio": risk["information_ratio"],
        "te_days": risk["te_days"],
    })

    return {
        "is_reliable": True,
        "from": bp["start"].isoformat(),
        "to": bp["end"].isoformat(),
        "base_date": base_date.isoformat(),
        "display_currency": ccy,
        "benchmark": "SP500",
        "benchmark_type": "price index (^GSPC), not total return",
        "benchmark_currency": ccy,
        "fx_source": bp["port"]["fx_source"],
        "fx_degraded": bp["port"]["fx_degraded"],
        "benchmark_fx_source": bp["bench_fx_source"],
        "resolution": {
            "points": len(points),
            "max_gap_days": max(gaps) if gaps else 0,
            "daily_segment_from": pairs[0][0].isoformat(),
            "is_sparse": False,
            "unpaired_days": bp["unpaired_days"],
            "note": (
                "Cartera y S&P se comparan en las mismas fechas, rebasados a 100 "
                f"el {base_date.isoformat()}; los días sin cotización de uno de los dos "
                "se componen en la siguiente fecha común."
            ),
        },
        "level_comparison": {
            "portfolio_return_pct": port_total,
            "benchmark_return_pct": bench_total,
            "excess_return_pct": port_total - bench_total,
        },
        "risk_comparison": risk_comparison,
        "twr_quality": quality,
        "points": points,
    }
