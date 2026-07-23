"""Performance — TWR + XIRR + external benchmark overlay.

TWR matches IBKR PortfolioAnalyst's Time-Weighted Return; XIRR matches
PortfolioAnalyst's Money-Weighted Return. The /benchmark route pulls SPX
from Yahoo Finance (persisted in the `benchmark_daily` cache table) and
re-denominates it into the requested base currency so the chart overlay is
apples-to-apples with the CHF portfolio TWR.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from ...analytics import (
    alpha_vs_benchmark,
    risk_metrics,
    twr_series,
    vs_benchmark,
    xirr_summary,
)
from ...benchmarks import fetch_sp500_in_currency
from ..deps import PeriodDep, SessionDep

router = APIRouter()


@router.get("/twr")
def twr(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    currency: str | None = Query(
        None,
        pattern="^(CHF|EUR|USD|GBP|JPY)$",
        description="Display currency (e.g. EUR, USD). Converts NAV at historical spot rates to strip the CHF FX effect from the return.",
    ),
):
    return twr_series(session, period=period, account_id=account_id, display_currency=currency)


@router.get("/xirr")
def xirr(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
):
    return xirr_summary(session, period=period, account_id=account_id)


@router.get(
    "/benchmark",
    summary="External benchmark (SP500) re-denominated in base currency",
)
def benchmark(
    session: SessionDep,
    period: PeriodDep,
    symbol: str = Query("sp500", pattern="^(sp500)$"),
    currency: str = Query(
        "CHF",
        pattern="^(CHF|EUR|USD|GBP|JPY)$",
        description="Target base currency",
    ),
):
    """Fetch SP500 daily closes from Yahoo Finance, multiply by USD→<currency>
    spot, normalise to an index starting at 100 on the first day in the window.
    Returns the series suitable for overlaying on the portfolio TWR chart.

    When period is all-time / unbounded, we use the actual NavDaily date range
    so the SP500 line aligns with the portfolio's TWR series instead of falling
    back to a generic 1-year window.
    """
    from datetime import timedelta
    from sqlalchemy import func, select
    from ...db.models import NavDaily

    if period is None or period.start is None or period.end is None:
        # Try the NAV span first; fall back to 1y only if NAV is empty.
        row = session.execute(
            select(func.min(NavDaily.snapshot_date), func.max(NavDaily.snapshot_date))
        ).first()
        nav_first, nav_last = row if row else (None, None)
        if nav_first and nav_last:
            start = period.start or nav_first
            end = period.end or nav_last
        else:
            end = date.today()
            start = end - timedelta(days=365)
    else:
        start, end = period.start, period.end

    try:
        rows = fetch_sp500_in_currency(currency, start, end)
    except Exception as exc:  # noqa: BLE001 — external API + empty cache; surface as 502
        raise HTTPException(status_code=502, detail=f"Benchmark fetch failed: {exc}")

    return {
        "symbol": "SPX",
        "currency": currency.upper(),
        "from": start.isoformat(),
        "to": end.isoformat(),
        "baseline_index": 100.0,
        "rows": rows,
    }


@router.get(
    "/vs-benchmark",
    summary="Unified honest comparison: portfolio TWR vs S&P (same dates, rebased to 100)",
)
def vs_benchmark_route(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    currency: str = Query(
        "CHF",
        pattern="^(CHF|EUR|USD|GBP|JPY)$",
        description="Base currency for both portfolio TWR and the benchmark",
    ),
):
    """Portfolio TWR and S&P 500 overlaid on the SAME dates, both rebased to
    100 on the first common day. Benchmark is sampled at the portfolio's own
    NAV points, so a sparse pre-2026 stretch shows as straight segments in
    BOTH lines instead of a smooth portfolio curve against a daily index.
    Includes level comparison (full window) and risk comparison (beta, alpha,
    tracking error, information ratio) over the dense daily segment.
    """
    return vs_benchmark(
        session, period=period, account_id=account_id,
        benchmark_currency=currency, display_currency=currency,
    )


@router.get(
    "/risk",
    summary="Sharpe · Sortino · Max DD · Volatility · Calmar (from ChangeInNAV daily TWR)",
)
def risk(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    risk_free_rate_pct: float = Query(
        0.0,
        ge=0.0,
        le=20.0,
        description="Annual risk-free rate as percentage (CHF SARON ~0.95 % in early 2026)",
    ),
):
    return risk_metrics(
        session, period=period, account_id=account_id,
        risk_free_rate_pct=risk_free_rate_pct,
    )


@router.get(
    "/alpha",
    summary="Alpha (simple + Jensen) + Beta vs S&P 500 over the period",
)
def alpha(
    session: SessionDep,
    period: PeriodDep,
    account_id: str | None = Query(None),
    currency: str = Query(
        "CHF",
        pattern="^(CHF|EUR|USD|GBP|JPY)$",
        description="Benchmark currency",
    ),
):
    return alpha_vs_benchmark(
        session, period=period, account_id=account_id,
        benchmark_currency=currency,
    )
