"""Performance math: canonical TWR, flows, XIRR,
display-currency conversion, benchmark pairing, risk conventions, waterfall and
the one-off data CLI steps. Real SQLite via the `_isolated_db` fixture; only the
Yahoo index fetch is faked.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select, text

from ibkr_backend.analytics import (
    alpha_vs_benchmark,
    change_in_nav_summary,
    change_in_nav_timeseries,
    risk_metrics,
    total_return_on_cash,
    twr_series,
    vs_benchmark,
    xirr_summary,
)
from ibkr_backend.analytics.filters import PeriodFilter, resolve_period
from ibkr_backend.analytics.risk import (
    RISK_FREE_ENV,
    _pair_returns,
    window_risk_free_pct,
)
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import (
    Account,
    CashTransaction,
    ChangeInNavDaily,
    FxRate,
    NavDaily,
)
from ibkr_backend.flex.parser import parse_flex_xml
from ibkr_backend.loaders.cin_backfill import (
    backfill_cin_columns,
    drop_csv_flow_rows,
    drop_navless_rows,
)
from ibkr_backend.loaders.pipeline import load_statement

ACC = "U1"


def _weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _seed_cin(days: list[date], twr_pct: float | list[float], flows: dict[date, float] | None = None,
              *, start_value: float = 100_000.0, asset_transfers: dict[date, float] | None = None):
    """Seed a dense ChangeInNAV series whose twr equals the Modified-Dietz
    return of its own start/end/flow values (so Dietz reconciles exactly)."""
    flows = flows or {}
    asset_transfers = asset_transfers or {}
    rets = twr_pct if isinstance(twr_pct, list) else [twr_pct] * len(days)
    value = start_value
    with session_scope() as s:
        if s.get(Account, ACC) is None:
            s.add(Account(account_id=ACC, base_currency="CHF"))
            s.flush()
        for d, r in zip(days, rets):
            cf = flows.get(d, 0.0) + asset_transfers.get(d, 0.0)
            start = value
            # Dietz: r = (end - start - cf) / (start + 0.5 cf)
            end = start + cf + r / 100.0 * (start + 0.5 * cf)
            s.add(ChangeInNavDaily(
                snapshot_date=d, account_id=ACC, currency="CHF",
                starting_value=Decimal(str(start)), ending_value=Decimal(str(end)),
                mtm=Decimal(str(end - start - cf)),
                deposits_withdrawals=Decimal(str(flows.get(d, 0.0))),
                asset_transfers=Decimal(str(asset_transfers.get(d, 0.0))),
                twr=Decimal(str(r)),
            ))
            s.add(NavDaily(snapshot_date=d, account_id=ACC, base_currency="CHF",
                           nav_total=Decimal(str(end))))
            value = end


def _chain(rets: list[float]) -> float:
    p = 1.0
    for r in rets:
        p *= 1 + r / 100.0
    return (p - 1) * 100.0


# ------------------------------------------------------------ B01 canonical TWR
def test_twr_equals_chained_cin_per_calendar_year():
    """|app - chained CIN| < 0.1 pp for every calendar year on the same dates,
    even when deposits are keyed on a different date than the NAV move."""
    days = _weekdays(date(2024, 1, 2), 520)  # spans 2024, 2025 and part of 2026
    rets = [0.05 if i % 3 else -0.04 for i in range(len(days))]
    flows = {days[10]: 5_000.0, days[300]: 20_000.0, days[400]: -3_000.0}
    _seed_cin(days, rets, flows)

    with session_scope() as s:
        for year in (2024, 2025):
            expected = _chain([r for d, r in zip(days, rets) if d.year == year])
            got = twr_series(s, period=resolve_period(year=year), summary=True)
            assert got["twr_pct"] == pytest.approx(expected, abs=0.1)
        allt = twr_series(s, summary=True)
        assert allt["twr_pct"] == pytest.approx(_chain(rets), abs=0.1)
        # Modified Dietz on the same rows reconciles (flows on the CIN date).
        assert allt["reconciliation"]["diff_pp"] == pytest.approx(0.0, abs=1e-6)


def test_twr_summary_is_small_and_matches_full():
    days = _weekdays(date(2025, 1, 2), 300)
    _seed_cin(days, 0.03)
    with session_scope() as s:
        full = twr_series(s)
        summ = twr_series(s, summary=True)
    assert "series" not in summ
    assert len(json.dumps(summ)) < 2048
    for k in ("twr_pct", "annualised_pct", "n_days", "from_date", "to_date", "base_date"):
        assert summ[k] == full[k]
    assert len(full["series"]) == 300


def test_annualised_is_null_under_one_year():
    days = _weekdays(date(2025, 1, 2), 100)
    _seed_cin(days, 0.1)
    with session_scope() as s:
        out = twr_series(s, summary=True)
    assert out["twr_pct"] is not None
    assert out["annualised_pct"] is None
    assert "365" in out["annualised_note"]


def test_annualised_present_from_one_year():
    """300 weekdays from Tue 2024-01-02 at +0.05 %/day, ending Mon 2025-02-24.

    base_date = 2024-01-01 (first row - 1 day); n_days = 2025-02-24 - 2024-01-01
    = 366 (2024 is leap) + 31 + 23 = 420 >= 365, so it is annualised.
    cumulative = 1.0005^300 = exp(300 * ln 1.0005) = exp(0.14996251) = 1.16179069
      -> twr = 16.17906893 %.
    annualised = 1.16179069^(365/420) - 1 = exp(0.14996251 * 365/420) - 1
      = exp(0.13032363) - 1 = 0.13919807 -> 13.919807 %.
    """
    days = _weekdays(date(2024, 1, 2), 300)
    _seed_cin(days, 0.05)
    with session_scope() as s:
        out = twr_series(s, summary=True)
    assert out["base_date"] == "2024-01-01"
    assert out["to_date"] == "2025-02-24"
    assert out["n_days"] == 420
    assert out["twr_pct"] == pytest.approx(16.17906893, abs=1e-6)
    assert out["annualised_pct"] == pytest.approx(13.919807, abs=1e-5)


# --------------------------------------------------------------- B07 bounded windows
def test_bounded_window_includes_day_one_and_base_is_previous_row():
    """MTD starting on a holiday (1st = Thursday holiday, first row the 2nd):
    day 1 of the window is chained, base_date is the last row before start."""
    days = [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 2), date(2026, 10, 5)]
    _seed_cin(days, [0.0, 0.0, -1.0, 0.5])
    with session_scope() as s:
        out = twr_series(s, period=PeriodFilter(date(2026, 10, 1), date(2026, 10, 7), "mtd"))
    assert out["from_date"] == "2026-10-02"
    assert out["base_date"] == "2026-09-30"
    assert out["twr_pct"] == pytest.approx(_chain([-1.0, 0.5]))
    assert out["series"][0]["daily_return_pct"] == pytest.approx(-1.0)


def _flat_sp500(currency, from_date, to_date, *, step=0.001):
    """Weekday-only synthetic index, +0.1 %/weekday, identity FX."""
    rows, d, i = [], from_date, 0
    while d <= to_date:
        if d.weekday() < 5 and not (d.month == 1 and d.day == 1):
            close = 5000.0 * (1 + step) ** i
            rows.append({"date": d.isoformat(), "close_native": close, "fx": 1.0,
                         "close_base": close, "index": 100.0, "return_pct": 0.0,
                         "fx_source": "ibkr_fx_rates"})
            i += 1
        d += timedelta(days=1)
    return rows


def test_vs_benchmark_ytd_starting_on_holiday_uses_asof_close(monkeypatch):
    """YTD starts 2026-01-01 (no benchmark bar that day): the benchmark base is
    the as-of close BEFORE the start (2025-12-31), so its first move counts."""
    monkeypatch.setattr("ibkr_backend.benchmarks.sp500.fetch_sp500_in_currency", _flat_sp500)
    days = _weekdays(date(2025, 12, 1), 90)
    _seed_cin(days, 0.05)
    with session_scope() as s:
        v = vs_benchmark(s, period=PeriodFilter(date(2026, 1, 1), date(2026, 3, 31), "ytd"))
        t = twr_series(s, period=PeriodFilter(date(2026, 1, 1), date(2026, 3, 31), "ytd"), summary=True)
    assert v["is_reliable"]
    assert v["base_date"] == "2025-12-31"
    first = v["points"][1]
    assert first["date"] == "2026-01-02"
    # Benchmark's first YTD move is Dec-31 close -> Jan-2 close (one weekday step).
    assert first["benchmark"] == pytest.approx(100.1, abs=1e-3)
    # One portfolio return: the vs-benchmark level equals the TWR summary.
    assert v["level_comparison"]["portfolio_return_pct"] == pytest.approx(t["twr_pct"], abs=1e-9)


# ------------------------------------------------------------ B50 truncation tolerance
def test_truncation_tolerates_weekend_start_but_flags_real_gaps():
    days = _weekdays(date(2026, 1, 5), 60)
    _seed_cin(days, 0.05)
    with session_scope() as s:
        # Saturday 2026-01-03 start, first row Monday 2026-01-05: 2 days.
        ok = risk_metrics(s, period=PeriodFilter(date(2026, 1, 3), days[-1], "x"))
        assert ok["is_truncated"] is False
        bad = risk_metrics(s, period=PeriodFilter(date(2025, 12, 1), days[-1], "x"))
        assert bad["is_truncated"] is True
        # All-time is not truncated even with a NAV-less earlier row.
        s.add(NavDaily(snapshot_date=date(2026, 1, 3), account_id=ACC, nav_total=None,
                       external_cashflow=Decimal("1")))
        s.flush()
        allt = risk_metrics(s, period=PeriodFilter(None, days[-1], "all"))
        assert allt["is_truncated"] is False


# ------------------------------------------------------------- B13/B49 risk conventions
def test_risk_annualisation_rules(monkeypatch):
    monkeypatch.delenv(RISK_FREE_ENV, raising=False)
    days = _weekdays(date(2026, 1, 5), 120)
    _seed_cin(days, [0.2 if i % 2 else -0.1 for i in range(len(days))])
    with session_scope() as s:
        short = risk_metrics(s)
        assert short["annualised"] is False
        assert short["annualised_return_pct"] is None
        assert short["calmar_ratio"] is None
        assert short["sharpe_method"] == "arithmetic"
        assert short["sharpe_ratio"] is not None
        assert short["risk_free_rate_pct"] == 0.0
        assert short["risk_free_source"].startswith("table")


def test_risk_long_window_geometric_and_calendar_time():
    """400 weekdays from Tue 2024-01-02 alternating -0.1 % / +0.2 %, ending Mon 2025-07-14.

    window_days = 2025-07-14 - base 2024-01-01 = 366 + 194 = 560 (>= 365, annualised).
    Each (-0.1, +0.2) pair multiplies by 0.999 * 1.002 = 1.000998; 200 pairs:
      cumulative = 1.000998^200 = exp(200 * 0.00099750) = exp(0.19950046) = 1.22079278
      annualised = cumulative^(365/560) - 1 = exp(0.19950046 * 365/560) - 1
                 = exp(0.13003) - 1 = 13.886432 %.
    Volatility is calendar-time: daily values alternate -0.001 / +0.002, mean 0.0005,
    every deviation = +-0.0015, so sum sq dev = 400 * 2.25e-6 = 9e-4,
    sample stdev = sqrt(9e-4 / 399) = 0.00150188;
    obs per calendar year = 400 / (560/365) = 260.714286 (not 252);
    annual vol = 0.00150188 * sqrt(260.714286) * 100 = 2.425031 %.
    """
    days = _weekdays(date(2024, 1, 2), 400)
    rets = [0.2 if i % 2 else -0.1 for i in range(len(days))]
    _seed_cin(days, rets)
    with session_scope() as s:
        out = risk_metrics(s)
    assert out["annualised"] is True and out["sharpe_method"] == "geometric"
    assert out["window_days"] == 560
    assert out["annualised_return_pct"] == pytest.approx(13.886432, abs=1e-5)
    assert out["annualised_volatility_pct"] == pytest.approx(2.425031, abs=1e-5)


def test_risk_free_table_env_and_param(monkeypatch):
    monkeypatch.setenv(RISK_FREE_ENV, "2024:2.0,2025:1.0")
    assert window_risk_free_pct(date(2024, 1, 1), date(2025, 1, 1)) == pytest.approx(2.0)
    mid = window_risk_free_pct(date(2024, 7, 1), date(2025, 7, 1))
    assert 1.4 < mid < 1.6
    assert window_risk_free_pct(date(2030, 1, 1), date(2030, 6, 1)) == 0.0
    days = _weekdays(date(2024, 1, 2), 300)
    _seed_cin(days, 0.05)
    with session_scope() as s:
        assert risk_metrics(s)["risk_free_rate_pct"] > 0
        explicit = risk_metrics(s, risk_free_rate_pct=0.0)
        assert explicit["risk_free_rate_pct"] == 0.0
        assert explicit["risk_free_source"] == "param"


# -------------------------------------------------------------- B48 pairing, no day dropped
def test_pair_returns_compounds_over_missing_benchmark_days():
    d = date
    port = [(d(2026, 7, 2), 0.01), (d(2026, 7, 3), 0.02), (d(2026, 7, 6), -0.01)]
    # Jul 3 is a US holiday (no bench bar); Jul 1 anchors the base.
    bench = [(d(2026, 7, 1), 100.0), (d(2026, 7, 2), 101.0), (d(2026, 7, 6), 102.01)]
    out = _pair_returns(port, bench, d(2026, 7, 1))
    pairs = out["pairs"]
    assert [p[0] for p in pairs] == [d(2026, 7, 2), d(2026, 7, 6)]
    assert out["unpaired_days"] == 0
    # Jul 3's portfolio day is compounded into Jul 6, not dropped.
    assert pairs[1][1] == pytest.approx(1.02 * 0.99 - 1)
    assert pairs[1][2] == pytest.approx(102.01 / 101.0 - 1)
    total = (1 + pairs[0][1]) * (1 + pairs[1][1]) - 1
    assert total == pytest.approx(1.01 * 1.02 * 0.99 - 1)


def test_pair_returns_compounds_benchmark_over_swiss_holiday():
    d = date
    port = [(d(2026, 5, 4), 0.01), (d(2026, 5, 6), 0.01)]  # no row on May 5
    bench = [(d(2026, 5, 1), 100.0), (d(2026, 5, 4), 101.0), (d(2026, 5, 5), 102.0), (d(2026, 5, 6), 103.0)]
    pairs = _pair_returns(port, bench, d(2026, 5, 1))["pairs"]
    assert [p[0] for p in pairs] == [d(2026, 5, 4), d(2026, 5, 6)]
    assert pairs[1][2] == pytest.approx(103.0 / 101.0 - 1)


def test_alpha_uses_every_portfolio_day_and_one_currency(monkeypatch):
    monkeypatch.setattr("ibkr_backend.benchmarks.sp500.fetch_sp500_in_currency", _flat_sp500)
    days = _weekdays(date(2026, 1, 5), 80)
    rets = [0.1 if i % 2 else -0.05 for i in range(80)]
    _seed_cin(days, rets)
    with session_scope() as s:
        a = alpha_vs_benchmark(s, benchmark_currency="CHF")
    assert a["is_reliable"]
    assert a["portfolio_days"] == 80
    assert a["unpaired_days"] == 0
    assert a["portfolio_total_return_pct"] == pytest.approx(_chain(rets), abs=1e-9)
    assert a["benchmark_currency"] == "CHF"


# ---------------------------------------------------------------- B51 display currency
def test_display_currency_uses_ibkr_fx_rates(monkeypatch):
    # Frankfurter must not be needed: fail loudly if it is called.
    def boom(*a, **k):
        raise AssertionError("Frankfurter called although fx_rates has the series")
    monkeypatch.setattr("ibkr_backend.analytics.fx.urllib.request.urlopen", boom)

    days = _weekdays(date(2026, 3, 2), 5)
    _seed_cin(days, [0.5, -0.2, 0.3, 0.0, 1.0])
    usd_chf = [0.80, 0.81, 0.80, 0.82, 0.83]  # USD -> CHF
    base = days[0] - timedelta(days=3)  # Friday before: the base-date rate
    with session_scope() as s:
        for d, r in zip([base] + days, [0.80] + usd_chf):
            s.add(FxRate(rate_date=d, from_ccy="USD", to_ccy="CHF", rate=Decimal(str(r))))
    with session_scope() as s:
        out = twr_series(s, period=PeriodFilter(days[0], days[-1], "w"), display_currency="USD")
    assert out["fx_source"] == "ibkr_fx_rates" and out["fx_degraded"] is False
    # USD per CHF = 1 / (USD->CHF). r_usd = (1+r) * fx_t/fx_{t-1} - 1.
    fx = [1 / 0.80] + [1 / r for r in usd_chf]
    expected = 1.0
    for i, r in enumerate([0.5, -0.2, 0.3, 0.0, 1.0]):
        expected *= (1 + r / 100) * fx[i + 1] / fx[i]
    assert out["twr_pct"] == pytest.approx((expected - 1) * 100, abs=1e-9)


def test_display_currency_without_rates_is_null_and_flagged(monkeypatch):
    def down(*a, **k):
        raise urllib.error.URLError("down")
    monkeypatch.setattr("ibkr_backend.analytics.fx.urllib.request.urlopen", down)
    from ibkr_backend.analytics import fx as fxmod
    fxmod._cache.clear()
    days = _weekdays(date(2026, 3, 2), 5)
    _seed_cin(days, 0.2)
    with session_scope() as s:
        out = twr_series(s, display_currency="EUR", summary=True)
    assert out["twr_pct"] is None
    assert out["fx_degraded"] is True and out["fx_source"] == "frankfurter"
    assert "EUR" in out["note"]


def test_sp500_converted_with_fx_rates(monkeypatch):
    from ibkr_backend.benchmarks import sp500

    def fake_yahoo(symbol, a, b):
        assert symbol == "^GSPC", "no Yahoo FX call expected when fx_rates covers USD"
        out, d = [], a
        while d <= b:
            if d.weekday() < 5:
                out.append((d, Decimal("5000")))
            d += timedelta(days=1)
        return out
    monkeypatch.setattr(sp500, "_yahoo_fetch", fake_yahoo)
    days = _weekdays(date(2024, 3, 4), 5)
    with session_scope() as s:
        for d, r in zip(days, [0.90, 0.91, 0.92, 0.93, 0.94]):
            s.add(FxRate(rate_date=d, from_ccy="USD", to_ccy="CHF", rate=Decimal(str(r))))
    rows = sp500.fetch_sp500_in_currency("CHF", days[0], days[-1])
    assert [r["fx"] for r in rows] == [0.90, 0.91, 0.92, 0.93, 0.94]
    assert {r["fx_source"] for r in rows} == {"ibkr_fx_rates"}
    assert rows[2]["close_base"] == pytest.approx(5000 * 0.92)


# ------------------------------------------------------------- B02 flows, XIRR, all-time
def test_flex_parser_loads_asset_transfers_and_sales_tax():
    xml = b"""<?xml version="1.0"?><FlexQueryResponse queryName="t" type="AF"><FlexStatements count="1">
<FlexStatement accountId="U9" fromDate="20260101" toDate="20260102" whenGenerated="20260103">
<ChangeInNAV><ChangeInNAV accountId="U9" fromDate="20260102" toDate="20260102" currency="CHF"
 startingValue="1000" endingValue="1500" mtm="0" depositsWithdrawals="100" assetTransfers="400"
 salesTax="-1.5" twr="0" /></ChangeInNAV></FlexStatement></FlexStatements></FlexQueryResponse>"""
    with session_scope() as s:
        for stmt in parse_flex_xml(xml):
            load_statement(s, stmt)
    with session_scope() as s:
        row = s.execute(select(ChangeInNavDaily)).scalar_one()
    assert row.asset_transfers == Decimal("400")
    assert row.sales_tax == Decimal("-1.5")


def test_schema_compat_adds_cin_columns_to_old_table(tmp_path, monkeypatch):
    from ibkr_backend import config as cfg
    from ibkr_backend.db import session as sess

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    sess.engine = sess._build_engine()  # type: ignore[attr-defined]
    sess.SessionLocal.configure(bind=sess.engine)
    with sess.engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE change_in_nav_daily (snapshot_date DATE, account_id VARCHAR(32), "
            "currency VARCHAR(8), PRIMARY KEY (snapshot_date, account_id))"
        ))
    sess.init_db()
    sess.init_db()  # idempotent
    cols = {c["name"] for c in inspect(sess.engine).get_columns("change_in_nav_daily")}
    assert {"asset_transfers", "sales_tax"} <= cols


def test_backfill_cin_columns_from_raw_xml_is_idempotent():
    raw = ('<ChangeInNAV accountId="U1" fromDate="20260102" toDate="20260102" currency="CHF" '
           'startingValue="1" endingValue="2" assetTransfers="250.5" salesTax="-2" twr="0" />\n')
    with session_scope() as s:
        s.add(Account(account_id=ACC, base_currency="CHF"))
        s.flush()
        s.add(ChangeInNavDaily(snapshot_date=date(2026, 1, 2), account_id=ACC,
                               currency="CHF", twr=Decimal("0"), raw_xml=raw))
    with session_scope() as s:
        dry = backfill_cin_columns(s)
        assert dry["changed"] == 1 and dry["nonzero_asset_transfers"] == 1
        assert s.execute(select(ChangeInNavDaily.asset_transfers)).scalar() is None
        backfill_cin_columns(s, apply=True)
    with session_scope() as s:
        row = s.execute(select(ChangeInNavDaily)).scalar_one()
        assert row.asset_transfers == Decimal("250.5") and row.sales_tax == Decimal("-2")
        assert backfill_cin_columns(s, apply=True)["changed"] == 0


def test_xirr_all_time_flows_and_total_return_share_one_definition():
    days = _weekdays(date(2024, 1, 2), 270)  # ~1.1 y
    flows = {days[0]: 100_000.0, days[100]: 10_000.0}
    at = {days[50]: 20_000.0}
    _seed_cin(days, 0.04, flows, start_value=0.0, asset_transfers=at)
    with session_scope() as s:
        x = xirr_summary(s)
        a = total_return_on_cash(s)
    assert x["annualised"] is True
    assert a["cumulative_net_deposits_base"] == pytest.approx(130_000.0)
    assert a["xirr_pct"] == pytest.approx(x["xirr_pct"])
    assert "cagr_pct" not in a
    # in-kind transfer appears as a flow, not as profit
    assert any(f["amount"] == pytest.approx(-20_000.0) for f in x["flows"])


# ----------------------------------------------------------------------- B21 waterfall
def test_waterfall_is_residual_free_and_drops_always_zero_fields():
    d1, d2 = date(2026, 2, 2), date(2026, 2, 3)
    comps = dict(mtm=-300, dividends=12, withholding_tax=-3, change_in_dividend_accruals=4,
                 interest=-2, change_in_interest_accruals=-1, commissions=-5, broker_fees=-0.5,
                 other_fees=7, transaction_tax=-1, sales_tax=-0.25, fx_translation=2,
                 deposits_withdrawals=2000, asset_transfers=500)
    with session_scope() as s:
        s.add(Account(account_id=ACC, base_currency="CHF"))
        s.flush()
        start = 100_000.0
        for d in (d1, d2):
            end = start + sum(comps.values())
            s.add(ChangeInNavDaily(snapshot_date=d, account_id=ACC, currency="CHF",
                                   starting_value=Decimal(str(start)),
                                   ending_value=Decimal(str(end)), twr=Decimal("0.1"),
                                   **{k: Decimal(str(v)) for k, v in comps.items()}))
            start = end
    with session_scope() as s:
        summ = change_in_nav_summary(s)
        days = change_in_nav_timeseries(s, bucket="day")
        months = change_in_nav_timeseries(s, bucket="month")
    assert "realized" not in summ["attribution_base"]
    assert "change_in_unrealized" not in summ["attribution_base"]
    assert summ["attribution_base"]["other_fees"] == pytest.approx(14)
    assert summ["attribution_base"]["asset_transfers"] == pytest.approx(1000)
    assert abs(summ["residual_base"]) < 1e-6
    for row in days:
        assert abs(row["residual"]) / row["ending_value"] < 0.001
        assert "realized" not in row
    expected_total = sum(v for k, v in comps.items() if k not in ("deposits_withdrawals", "asset_transfers"))
    assert months[0]["total_return_base"] == pytest.approx(2 * expected_total)
    assert "realized" not in months[0]


# ---------------------------------------------------------------- data CLI steps
def _cash_dw(tid, amount, ccy="CHF", fx=1.0):
    return CashTransaction(
        transaction_id=tid, account_id=ACC, type="Deposits/Withdrawals",
        amount=Decimal(str(amount)), currency=ccy,
        fx_rate_to_base=None if fx is None else Decimal(str(fx)),
        settle_date=date(2024, 1, 2), event_datetime=datetime(2024, 1, 2),
    )


def test_drop_csv_flow_rows_guarded_by_cin_ratio():
    with session_scope() as s:
        s.add(Account(account_id=ACC, base_currency="CHF"))
        s.flush()
        s.add(ChangeInNavDaily(snapshot_date=date(2024, 1, 2), account_id=ACC, currency="CHF",
                               twr=Decimal("0"), deposits_withdrawals=Decimal("1000")))
        s.add_all([_cash_dw("F1", 900), _cash_dw("csv_a", 100)])
    with session_scope() as s:
        dry = drop_csv_flow_rows(s)
        assert dry["ratio_ok"] and dry["csv_rows"] == 1 and dry["deleted"] == 0
        done = drop_csv_flow_rows(s, apply=True)
        assert done["deleted"] == 1
    with session_scope() as s:
        ids = set(s.execute(select(CashTransaction.transaction_id)).scalars())
        assert ids == {"F1"}
        # Re-run: nothing left to delete (idempotent), whatever the ratio now is.
        again = drop_csv_flow_rows(s, apply=True)
        assert again["csv_rows"] == 0 and again["deleted"] == 0


def test_drop_csv_flow_rows_refuses_when_ratio_off():
    with session_scope() as s:
        s.add(Account(account_id=ACC, base_currency="CHF"))
        s.flush()
        s.add(ChangeInNavDaily(snapshot_date=date(2024, 1, 2), account_id=ACC, currency="CHF",
                               twr=Decimal("0"), deposits_withdrawals=Decimal("500")))
        s.add_all([_cash_dw("F1", 900), _cash_dw("csv_a", 100)])
    with session_scope() as s:
        out = drop_csv_flow_rows(s, apply=True)
        assert out["ratio_ok"] is False and out["deleted"] == 0
        assert len(list(s.execute(select(CashTransaction)).scalars())) == 2


def test_drop_navless_rows():
    with session_scope() as s:
        s.add(Account(account_id=ACC, base_currency="CHF"))
        s.flush()
        s.add_all([
            NavDaily(snapshot_date=date(2023, 11, 18), account_id=ACC, nav_total=None,
                     external_cashflow=Decimal("96.8")),
            NavDaily(snapshot_date=date(2023, 11, 20), account_id=ACC, nav_total=Decimal("96.8")),
        ])
    with session_scope() as s:
        dry = drop_navless_rows(s)
        assert dry["navless_rows"] == 1 and dry["dates"] == ["2023-11-18"] and dry["deleted"] == 0
        assert drop_navless_rows(s, apply=True)["deleted"] == 1
        assert drop_navless_rows(s, apply=True)["navless_rows"] == 0
        assert s.get(NavDaily, (date(2023, 11, 20), ACC)) is not None
