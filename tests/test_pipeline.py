"""End-to-end loader test: parse XML → load → analytics."""

from __future__ import annotations

from datetime import date

import pytest

from ibkr_backend.analytics import (
    commissions_summary,
    dividends_by_country,
    dividends_summary,
    income_projection,
    interest_summary,
    portfolio_overview,
    positions_snapshot,
    twr_series,
    unrealized_pnl,
    withholding_tax_by_country,
)
from ibkr_backend.analytics.filters import resolve_period
from ibkr_backend.db import session_scope
from ibkr_backend.flex.parser import parse_flex_xml
from ibkr_backend.loaders.pipeline import dashboard_has_data, load_statement


def _seed(xml_bytes: bytes):
    statements = parse_flex_xml(xml_bytes)
    with session_scope() as session:
        for stmt in statements:
            load_statement(session, stmt)


def test_loaders_are_idempotent(sample_flex_xml):
    _seed(sample_flex_xml)
    _seed(sample_flex_xml)  # rerun must not duplicate
    with session_scope() as s:
        from ibkr_backend.db.models import (
            CashTransaction,
            PositionEod,
            Trade,
        )
        from sqlalchemy import func, select

        assert s.execute(select(func.count()).select_from(Trade)).scalar() == 2
        assert (
            s.execute(select(func.count()).select_from(CashTransaction)).scalar() == 4
        )
        assert s.execute(select(func.count()).select_from(PositionEod)).scalar() == 2


def test_flex_http_errors_do_not_leak_request_params():
    import httpx

    from ibkr_backend.flex.client import FlexError, _raise_for_status

    request = httpx.Request(
        "GET",
        "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
        "?t=secret-token&q=private-query&v=3",
    )
    response = httpx.Response(604, request=request)

    try:
        _raise_for_status(response, "SendRequest")
    except FlexError as exc:
        msg = str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected FlexError")

    assert "secret-token" not in msg
    assert "private-query" not in msg
    assert "HTTP 604 from Flex SendRequest" in msg


def test_commissions_in_base_currency(sample_flex_xml):
    _seed(sample_flex_xml)
    with session_scope() as s:
        period = resolve_period(year=2025, month=1)
        out = commissions_summary(s, period=period)
        # 2 trades * -1 USD commission * fx ≈ -1.84
        assert out["trade_count"] == 2
        assert out["total_base"] is not None
        assert -2.0 < out["total_base"] < -1.5


def test_dividends_summary_net(sample_flex_xml):
    _seed(sample_flex_xml)
    with session_scope() as s:
        period = resolve_period(year=2025)
        out = dividends_summary(s, period=period)
        assert round(out["gross_base"], 2) == round(6.0 * 0.92, 2)
        assert round(out["withholding_base"], 2) == round(-0.9 * 0.92, 2)
        assert round(out["net_base"], 2) == round((6.0 - 0.9) * 0.92, 2)


def test_withholding_by_country(sample_flex_xml):
    _seed(sample_flex_xml)
    with session_scope() as s:
        rows = withholding_tax_by_country(s, period=resolve_period(year=2025))
        countries = {r["country"]: r for r in rows}
        assert "US" in countries
        assert countries["US"]["withholding"] < 0


def test_dividends_by_country_join_falls_back_to_issuer(sample_flex_xml):
    _seed(sample_flex_xml)
    with session_scope() as s:
        rows = dividends_by_country(s, period=resolve_period(year=2025))
        # only US dividends in the fixture
        countries = {r["country"]: r for r in rows}
        assert "US" in countries


def test_unrealized_pnl(sample_flex_xml):
    _seed(sample_flex_xml)
    with session_scope() as s:
        out = unrealized_pnl(s)
        assert out["positions"] == 2
        # 100 USD * 0.92 + 400 EUR ≈ 492 base
        assert 480 < out["unrealized_pnl_base"] < 510


def test_interest_summary_includes_paid(sample_flex_xml):
    _seed(sample_flex_xml)
    with session_scope() as s:
        out = interest_summary(s, period=resolve_period(year=2025))
        assert out["paid_base"] == -12.5


def test_positions_snapshot_enriched(sample_flex_xml):
    _seed(sample_flex_xml)
    with session_scope() as s:
        rows = positions_snapshot(s)
        symbols = {r["symbol"]: r for r in rows}
        assert "AAPL" in symbols
        assert symbols["AAPL"]["country"] == "US"
        assert symbols["AAPL"]["sector"] == "Technology"


def test_open_positions_prefer_summary_over_lots():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="dashboard" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U1" fromDate="20250101" toDate="20250131" whenGenerated="20250201;060000">
      <OpenPositions>
        <OpenPosition reportDate="20250131" accountId="U1" conid="100" symbol="ABC" currency="USD" fxRateToBase="1" assetCategory="STK" position="10" positionValue="1000" costBasisMoney="700" fifoPnlUnrealized="300" markPrice="100" levelOfDetail="SUMMARY" />
        <OpenPosition reportDate="20250131" accountId="U1" conid="100" symbol="ABC" currency="USD" fxRateToBase="1" assetCategory="STK" position="3" positionValue="300" costBasisMoney="210" fifoPnlUnrealized="90" markPrice="100" levelOfDetail="LOT" openDateTime="20250110;100000" />
      </OpenPositions>
      <EquitySummaryByReportDateInBase>
        <EquitySummaryByReportDateInBase accountId="U1" reportDate="20250131" currency="USD" total="1000" cash="0" stock="1000" />
      </EquitySummaryByReportDateInBase>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""
    _seed(xml)
    with session_scope() as s:
        rows = positions_snapshot(s, account_id="U1")
        assert len(rows) == 1
        assert rows[0]["quantity"] == 10.0
        assert rows[0]["position_value_base"] == 1000.0


def test_twr_series_is_chained(sample_flex_xml):
    """Chained daily TWR: prod(1 + twr_t/100) - 1 over the fixture row plus two
    hand-added days.

    Fixture ChangeInNAV row (2025-01-31): twr = 7.6453 %.
    Added rows: 2025-02-03 twr = +1.0 %, 2025-02-04 twr = -2.0 %.
      day 1: 1.076453                 -> 7.6453 %
      day 2: 1.076453 * 1.01 = 1.08721753   -> 8.721753 %
      day 3: 1.08721753 * 0.98 = 1.0654731794 -> 6.54731794 %
    Quality: 3 rows over 5 calendar days (coverage 0.6, max gap 3) -> reliable.
    n_days = 2025-02-04 - base 2025-01-30 = 5.
    """
    from decimal import Decimal

    from ibkr_backend.db.models import ChangeInNavDaily

    _seed(sample_flex_xml)
    with session_scope() as s:
        for d, twr in ((date(2025, 2, 3), "1.0"), (date(2025, 2, 4), "-2.0")):
            s.add(ChangeInNavDaily(
                snapshot_date=d, account_id="U1234567", currency="EUR",
                twr=Decimal(twr),
            ))
    with session_scope() as s:
        out = twr_series(s)
    assert out["quality"]["is_reliable"] is True
    assert out["n_days"] == 5
    assert out["base_date"] == "2025-01-30"
    assert [p["date"] for p in out["series"]] == ["2025-01-31", "2025-02-03", "2025-02-04"]
    assert [p["daily_return_pct"] for p in out["series"]] == pytest.approx(
        [7.6453, 1.0, -2.0]
    )
    assert [p["cumulative_pct"] for p in out["series"]] == pytest.approx(
        [7.6453, 8.721753, 6.54731794]
    )
    assert out["raw_twr_pct"] == pytest.approx(6.54731794)
    assert out["twr_pct"] == pytest.approx(6.54731794)
    assert out["annualised_pct"] is None  # 5 days < 365: not annualised


def test_portfolio_overview_has_all_kpis(sample_flex_xml):
    _seed(sample_flex_xml)
    with session_scope() as s:
        out = portfolio_overview(s, period=resolve_period(year=2025))
        for key in (
            "nav_base",
            "dividends",
            "commissions",
            "unrealized",
            "income_projection",
            "freshness",
        ):
            assert key in out
        # Pruned in P1.3: nothing renders these (and total_pnl_base was wrong).
        for key in (
            "nav_date",
            "deposits_withdrawals_base",
            "interest",
            "transaction_fees",
            "realized_pnl_base",
            "total_pnl_base",
        ):
            assert key not in out
        assert "cost_base" not in out["unrealized"]


def test_sparse_twr_is_flagged_unreliable():
    from decimal import Decimal

    from ibkr_backend.db.models import ChangeInNavDaily

    def cin(d, twr):
        return ChangeInNavDaily(snapshot_date=d, account_id="U1", currency="CHF", twr=Decimal(twr))

    with session_scope() as s:
        s.add_all([cin(date(2024, 1, 1), "1.0"), cin(date(2024, 12, 31), "2.0"), cin(date(2025, 12, 31), "3.0")])
        s.commit()
        out = twr_series(s, account_id="U1")
        assert out["quality"]["is_reliable"] is False
        assert out["twr_pct"] is None
        assert out["raw_twr_pct"] is not None


def test_income_projection_present(sample_flex_xml):
    _seed(sample_flex_xml)
    with session_scope() as s:
        out = income_projection(s)
        assert "padi_base" in out
        assert "yield_pct" in out


def test_dashboard_has_data_detects_empty_and_seeded_db(sample_flex_xml):
    with session_scope() as s:
        assert dashboard_has_data(s) is False
    _seed(sample_flex_xml)
    with session_scope() as s:
        assert dashboard_has_data(s) is True


SYMBOL_ATTR_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="t" type="AF">
 <FlexStatements count="1">
  <FlexStatement accountId="U99" fromDate="20260101" toDate="20260103" whenGenerated="20260104">
   <AccountInformation accountId="U99" currency="CHF" />
   <SecuritiesInfo>
     <SecurityInfo conid="265598" symbol="AAPL" assetCategory="STK" subCategory="COMMON"
        isin="US0378331005" listingExchange="NASDAQ" issuerCountryCode="US"
        currency="USD" multiplier="1" sector="Technology" industry="Computer Hardware"
        description="APPLE INC" />
     <SecurityInfo conid="76792991" symbol="TSLA" assetCategory="STK" subCategory="COMMON"
        isin="US88160R1014" listingExchange="NASDAQ" issuerCountryCode="US"
        currency="USD" multiplier="1" description="TESLA INC" />
   </SecuritiesInfo>
   <FIFOPerformanceSummaryInBase>
     <FIFOPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="AAPL"
        conid="265598" reportDate="20260101"
        realizedSTProfit="0" realizedSTLoss="0" realizedLTProfit="0" realizedLTLoss="0"
        totalRealizedPnl="0" unrealizedProfit="50" unrealizedLoss="0"
        unrealizedSTProfit="50" unrealizedSTLoss="0" unrealizedLTProfit="0" unrealizedLTLoss="0"
        totalUnrealizedPnl="50" totalFifoPnl="50" transferredPnl="0" />
     <FIFOPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="AAPL"
        conid="265598" reportDate="20260103"
        realizedSTProfit="120" realizedSTLoss="0" realizedLTProfit="0" realizedLTLoss="0"
        totalRealizedPnl="120" unrealizedProfit="0" unrealizedLoss="0"
        unrealizedSTProfit="0" unrealizedSTLoss="0" unrealizedLTProfit="0" unrealizedLTLoss="0"
        totalUnrealizedPnl="0" totalFifoPnl="120" transferredPnl="0" />
     <FIFOPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="TSLA"
        conid="76792991" reportDate="20260103"
        realizedSTProfit="0" realizedSTLoss="-40" realizedLTProfit="0" realizedLTLoss="0"
        totalRealizedPnl="-40" unrealizedProfit="0" unrealizedLoss="-15"
        unrealizedSTProfit="0" unrealizedSTLoss="-15" unrealizedLTProfit="0" unrealizedLTLoss="0"
        totalUnrealizedPnl="-15" totalFifoPnl="-55" transferredPnl="0" />
   </FIFOPerformanceSummaryInBase>
   <MTMPerformanceSummaryInBase>
     <MTMPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="AAPL"
        conid="265598" reportDate="20260101"
        transactionMtm="50" commissions="-1" total="49" totalWithAccruals="49" />
     <MTMPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="AAPL"
        conid="265598" reportDate="20260103"
        transactionMtm="120" commissions="-1" total="39" totalWithAccruals="39" />
     <MTMPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="TSLA"
        conid="76792991" reportDate="20260103"
        transactionMtm="-40" commissions="-2" total="-42" totalWithAccruals="-42" />
   </MTMPerformanceSummaryInBase>
  </FlexStatement>
 </FlexStatements>
</FlexQueryResponse>
"""


def test_symbol_attribution_loads_and_aggregates():
    from ibkr_backend.analytics import symbol_attribution
    from ibkr_backend.analytics.filters import resolve_period
    from ibkr_backend.db.models import FifoPerformanceUnderlying, MtmPerformanceUnderlying
    from sqlalchemy import func, select

    _seed(SYMBOL_ATTR_XML)
    # Idempotent rerun: upsert_replace ⇒ same row count, no duplicates.
    _seed(SYMBOL_ATTR_XML)

    with session_scope() as s:
        assert s.execute(select(func.count()).select_from(FifoPerformanceUnderlying)).scalar() == 3
        assert s.execute(select(func.count()).select_from(MtmPerformanceUnderlying)).scalar() == 3

        period = resolve_period(from_date="2026-01-01", to_date="2026-01-03")
        out = symbol_attribution(s, period=period)
        rows = {r["symbol"]: r for r in out["rows"]}
        assert set(rows) == {"AAPL", "TSLA"}

        aapl = rows["AAPL"]
        # Realized = sum daily: 0 + 120 = 120. MTM = 49 + 39 = 88.
        # Unrealized = latest snapshot (20260103) = 0.
        assert aapl["realized_pnl_base"] == 120.0
        assert aapl["mtm_total_base"] == 88.0
        assert aapl["unrealized_pnl_base"] == 0.0
        assert aapl["commissions_base"] == -2.0
        assert aapl["description"] == "APPLE INC"
        assert aapl["sector"] == "Technology"

        tsla = rows["TSLA"]
        assert tsla["realized_pnl_base"] == -40.0
        assert tsla["unrealized_pnl_base"] == -15.0
        assert tsla["mtm_total_base"] == -42.0
        # total = MTM period total (FIFO realized/unrealized are breakdown only)
        assert tsla["total_pnl_base"] == -42.0

        # Ordering by |total|: AAPL mtm 88 vs TSLA mtm -42 → AAPL first.
        assert out["rows"][0]["symbol"] == "AAPL"

        # Period filter: shrink to only 2026-01-03 → AAPL realized must be 120,
        # MTM only the 20260103 row (39), unrealized still 0.
        narrow = resolve_period(from_date="2026-01-03", to_date="2026-01-03")
        out2 = symbol_attribution(s, period=narrow)
        aapl2 = {r["symbol"]: r for r in out2["rows"]}["AAPL"]
        assert aapl2["realized_pnl_base"] == 120.0
        assert aapl2["mtm_total_base"] == 39.0


def test_symbol_attribution_empty_returns_clean_skeleton():
    from ibkr_backend.analytics import symbol_attribution

    with session_scope() as s:
        out = symbol_attribution(s)
        assert out["rows"] == []
        assert out["totals"]["total_pnl_base"] == 0.0
        assert out["as_of"] is None


# A held symbol (row at the latest snapshot) plus a closed one whose last FIFO
# row predates the latest snapshot but still carries unrealized — the exact
# shape of an option that was ITM the day before it expired.
CLOSED_POS_ATTR_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="dashboard_activity" type="AF">
 <FlexStatements count="1">
  <FlexStatement accountId="U99" fromDate="20260101" toDate="20260103" whenGenerated="20260104">
   <AccountInformation accountId="U99" currency="CHF" />
   <SecuritiesInfo>
     <SecurityInfo conid="111" symbol="HELD" assetCategory="STK" subCategory="COMMON"
        currency="USD" multiplier="1" issuerCountryCode="US" description="HELD INC" />
     <SecurityInfo conid="222" symbol="GONE" assetCategory="STK" subCategory="COMMON"
        currency="USD" multiplier="1" issuerCountryCode="US" description="GONE INC" />
   </SecuritiesInfo>
   <FIFOPerformanceSummaryInBase>
     <FIFOPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="HELD"
        conid="111" reportDate="20260103"
        realizedSTProfit="0" realizedSTLoss="0" realizedLTProfit="0" realizedLTLoss="0"
        totalRealizedPnl="0" unrealizedProfit="30" unrealizedLoss="0"
        unrealizedSTProfit="30" unrealizedSTLoss="0" unrealizedLTProfit="0" unrealizedLTLoss="0"
        totalUnrealizedPnl="30" totalFifoPnl="30" transferredPnl="0" />
     <FIFOPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="GONE"
        conid="222" reportDate="20260101"
        realizedSTProfit="10" realizedSTLoss="0" realizedLTProfit="0" realizedLTLoss="0"
        totalRealizedPnl="10" unrealizedProfit="50" unrealizedLoss="0"
        unrealizedSTProfit="50" unrealizedSTLoss="0" unrealizedLTProfit="0" unrealizedLTLoss="0"
        totalUnrealizedPnl="50" totalFifoPnl="60" transferredPnl="0" />
   </FIFOPerformanceSummaryInBase>
   <MTMPerformanceSummaryInBase>
     <MTMPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="HELD"
        conid="111" reportDate="20260103"
        transactionMtm="0" commissions="0" total="0" totalWithAccruals="0" />
     <MTMPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="GONE"
        conid="222" reportDate="20260101"
        transactionMtm="0" commissions="0" total="0" totalWithAccruals="0" />
   </MTMPerformanceSummaryInBase>
  </FlexStatement>
 </FlexStatements>
</FlexQueryResponse>
"""


def test_symbol_attribution_zeroes_stale_unrealized_for_closed_positions():
    from ibkr_backend.analytics import symbol_attribution
    from ibkr_backend.analytics.filters import resolve_period

    _seed(CLOSED_POS_ATTR_XML)

    with session_scope() as s:
        period = resolve_period(from_date="2026-01-01", to_date="2026-01-03")
        out = symbol_attribution(s, period=period)
        rows = {r["symbol"]: r for r in out["rows"]}
        assert set(rows) == {"HELD", "GONE"}

        # HELD sits on the latest snapshot -> live unrealized is kept.
        assert rows["HELD"]["is_held"] is True
        assert rows["HELD"]["unrealized_pnl_base"] == 30.0

        # GONE's last snapshot (20260101) predates the latest (20260103): it is
        # closed, so its stale unrealized (50) must be dropped to 0 while its
        # realized P&L (10) still counts.
        assert rows["GONE"]["is_held"] is False
        assert rows["GONE"]["unrealized_pnl_base"] == 0.0
        assert rows["GONE"]["realized_pnl_base"] == 10.0
        # The headline total is the MTM period total (0 in this fixture); the
        # FIFO realized figure is a breakdown column and is not added on top.
        assert rows["GONE"]["total_pnl_base"] == rows["GONE"]["mtm_total_base"] == 0.0

        # held_only drops the closed position entirely.
        out_held = symbol_attribution(s, period=period, held_only=True)
        assert {r["symbol"] for r in out_held["rows"]} == {"HELD"}


TC_PIPELINE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="dashboard_tc_daily" type="TCF">
 <FlexStatements count="1">
  <FlexStatement accountId="U99" fromDate="20260513" toDate="20260513" period="" whenGenerated="20260517">
   <TradeConfirms>
     <SymbolSummary accountId="U99" assetCategory="STK" symbol="ZZZ" conid="111111"
        levelOfDetail="SYMBOL_SUMMARY" execID="" tradeDate="20260513"
        buySell="BUY" quantity="1" price="100" />
     <Order accountId="U99" assetCategory="STK" symbol="ZZZ" conid="111111"
        levelOfDetail="ORDER" execID="" orderID="ORD-A"
        orderTime="20260415;082454" dateTime="20260513;062422"
        tradeDate="20260513" buySell="BUY" quantity="1" price="100" />
     <TradeConfirm accountId="U99" assetCategory="STK" symbol="ZZZ" conid="111111"
        levelOfDetail="EXECUTION" tradeID="TRADE-1"
        execID="EXEC-1" orderID="ORD-A"
        orderTime="20260415;082454" dateTime="20260513;062422"
        reportDate="20260513" tradeDate="20260513" settleDate="20260515"
        buySell="BUY" quantity="1" price="100" amount="100"
        proceeds="-100" netCash="-101.5"
        commission="-1.5" brokerExecutionCommission="-1.0"
        thirdPartyExecutionCommission="-0.4" thirdPartyClearingCommission="-0.1"
        commissionCurrency="EUR" currency="EUR" multiplier="1" exchange="XEX"
        transactionType="ExchTrade" orderType="LMT" code="O" isAPIOrder="N" />
     <TradeConfirm accountId="U99" assetCategory="STK" symbol="ZZZ" conid="111111"
        levelOfDetail="EXECUTION" tradeID="TRADE-2"
        execID="EXEC-2" orderID="ORD-A"
        orderTime="20260415;082454" dateTime="20260513;062423"
        reportDate="20260513" tradeDate="20260513" settleDate="20260515"
        buySell="BUY" quantity="2" price="100.5" amount="201"
        proceeds="-201" netCash="-203" commission="-2"
        commissionCurrency="EUR" currency="EUR" multiplier="1" exchange="XEX"
        transactionType="ExchTrade" orderType="LMT" code="O" />
   </TradeConfirms>
  </FlexStatement>
 </FlexStatements>
</FlexQueryResponse>
"""


def test_trade_executions_idempotent_and_join_on_order_id():
    """TC pipeline drops SymbolSummary/Order, keeps only EXECUTION rows,
    and rerunning the same payload doesn't duplicate.
    """
    from ibkr_backend.db.models import TradeExecution
    from sqlalchemy import func, select

    _seed(TC_PIPELINE_XML)
    _seed(TC_PIPELINE_XML)  # idempotency: upsert_ignore on exec_id

    with session_scope() as s:
        n = s.execute(select(func.count()).select_from(TradeExecution)).scalar()
        assert n == 2, "expected 2 executions (one order, two fills)"
        # Both fills share the same order_id — gives the slice view.
        order_ids = s.execute(
            select(TradeExecution.order_id, func.count())
            .group_by(TradeExecution.order_id)
        ).all()
        assert order_ids == [("ORD-A", 2)]
        # Sanity: exec datetimes are 1 second apart.
        execs = sorted(
            s.execute(select(TradeExecution).order_by(TradeExecution.execution_time)).scalars()
        , key=lambda x: x.execution_time)
        assert (execs[1].execution_time - execs[0].execution_time).seconds == 1
        # The order_time is shared (both fills come from the same parent LMT).
        assert execs[0].order_time == execs[1].order_time


def test_executions_list_summary_aggregates_correctly():
    """Total fills, distinct orders, multi-fill detection, lag stats — sourced
    from the same TC pipeline payload that seeds 2 fills under one parent LMT.
    """
    from ibkr_backend.analytics import executions_list

    _seed(TC_PIPELINE_XML)

    with session_scope() as s:
        out = executions_list(s)
        assert out["summary"]["total_fills"] == 2
        assert out["summary"]["distinct_orders"] == 1
        assert out["summary"]["multi_fill_orders"] == 1
        assert out["summary"]["multi_fill_order_ids"] == ["ORD-A"]
        assert out["summary"]["lag_sample_size"] == 2
        # 2026-04-15 08:24:54 → 2026-05-13 06:24:22 ≈ 2_411_968s (27.9 days).
        avg_lag = out["summary"]["avg_lag_seconds"]
        assert avg_lag is not None
        assert 2_411_500 < avg_lag < 2_412_500
        # Both fills land in the 7-30d bucket (27.9 days).
        buckets = {b["bucket"]: b["count"] for b in out["summary"]["lag_distribution"]}
        assert buckets["7-30d"] == 2
        assert sum(buckets.values()) == 2
        # Row-level multi-fill flag.
        assert all(r["is_multi_fill"] for r in out["rows"])
        assert all(r["fills_in_order"] == 2 for r in out["rows"])


def test_executions_list_order_id_drilldown_isolates_one_order():
    from ibkr_backend.analytics import executions_list

    _seed(TC_PIPELINE_XML)

    with session_scope() as s:
        out = executions_list(s, order_id="ORD-A")
        assert len(out["rows"]) == 2
        # Order-id mismatch returns no rows.
        empty = executions_list(s, order_id="ORD-NOPE")
        assert empty["rows"] == []
        assert empty["summary"]["total_fills"] == 0


def _mk_opt(symbol: str, side: str, qty: int = 1, trade_id: str = "auto") -> object:
    """Build a minimal Trade-shaped object usable by _classify_multi_leg."""
    from ibkr_backend.db.models import Trade
    from datetime import datetime, date as date_cls

    return Trade(
        trade_id=trade_id,
        ib_order_id="ORD",
        account_id="U99",
        conid=hash(symbol) & 0xFFFFFFFF,
        symbol=symbol,
        asset_class="OPT",
        currency="USD",
        trade_date=date_cls(2026, 5, 1),
        trade_datetime=datetime(2026, 5, 1, 10, 0, 0),
        quantity=qty,
        trade_price=1,
        proceeds=qty * 100,
        net_cash=qty * 100,
        buy_sell=side,
    )


def _mk_stk(symbol: str, side: str, qty: int = 100) -> object:
    from ibkr_backend.db.models import Trade
    from datetime import datetime, date as date_cls

    return Trade(
        trade_id=f"T-{symbol}-{side}",
        ib_order_id="ORD",
        account_id="U99",
        conid=hash(symbol) & 0xFFFFFFFF,
        symbol=symbol,
        asset_class="STK",
        currency="USD",
        trade_date=date_cls(2026, 5, 1),
        trade_datetime=datetime(2026, 5, 1, 10, 0, 0),
        quantity=qty,
        trade_price=10,
        proceeds=qty * 10,
        net_cash=qty * 10,
        buy_sell=side,
    )


def test_classify_multi_leg_vertical_spread():
    from ibkr_backend.analytics.metrics import _classify_multi_leg

    # Bull call spread: BUY 40C + SELL 45C, same expiry.
    group = [
        (_mk_opt("XYZ   260918C00040000", "BUY", 1, "T1"), None),
        (_mk_opt("XYZ   260918C00045000", "SELL", 1, "T2"), None),
    ]
    assert _classify_multi_leg(group) == "Vertical spread"


def test_classify_multi_leg_straddle():
    from ibkr_backend.analytics.metrics import _classify_multi_leg

    # Long straddle: BUY 40C + BUY 40P, same strike & expiry.
    group = [
        (_mk_opt("XYZ   260918C00040000", "BUY", 1, "T1"), None),
        (_mk_opt("XYZ   260918P00040000", "BUY", 1, "T2"), None),
    ]
    assert _classify_multi_leg(group) == "Straddle"


def test_classify_multi_leg_strangle():
    from ibkr_backend.analytics.metrics import _classify_multi_leg

    # Short strangle: SELL 45C + SELL 35P, same expiry, different strikes.
    group = [
        (_mk_opt("XYZ   260918C00045000", "SELL", 1, "T1"), None),
        (_mk_opt("XYZ   260918P00035000", "SELL", 1, "T2"), None),
    ]
    assert _classify_multi_leg(group) == "Strangle"


def test_classify_multi_leg_calendar_and_diagonal():
    from ibkr_backend.analytics.metrics import _classify_multi_leg

    calendar = [
        (_mk_opt("XYZ   260918C00040000", "SELL", 1, "T1"), None),
        (_mk_opt("XYZ   261218C00040000", "BUY", 1, "T2"), None),
    ]
    assert _classify_multi_leg(calendar) == "Calendar spread"

    diagonal = [
        (_mk_opt("XYZ   260918C00040000", "SELL", 1, "T1"), None),
        (_mk_opt("XYZ   261218C00045000", "BUY", 1, "T2"), None),
    ]
    assert _classify_multi_leg(diagonal) == "Diagonal spread"


def test_classify_multi_leg_iron_condor_and_butterfly():
    from ibkr_backend.analytics.metrics import _classify_multi_leg

    # Iron condor: short the inner strikes, long the wings.
    condor = [
        (_mk_opt("XYZ   260918P00035000", "BUY", 1, "T1"), None),   # long wing put
        (_mk_opt("XYZ   260918P00040000", "SELL", 1, "T2"), None),  # short put
        (_mk_opt("XYZ   260918C00050000", "SELL", 1, "T3"), None),  # short call
        (_mk_opt("XYZ   260918C00055000", "BUY", 1, "T4"), None),   # long wing call
    ]
    assert _classify_multi_leg(condor) == "Iron condor"

    # Iron butterfly: short call + short put share the same middle strike.
    butterfly = [
        (_mk_opt("XYZ   260918P00040000", "BUY", 1, "T1"), None),
        (_mk_opt("XYZ   260918P00045000", "SELL", 1, "T2"), None),
        (_mk_opt("XYZ   260918C00045000", "SELL", 1, "T3"), None),
        (_mk_opt("XYZ   260918C00050000", "BUY", 1, "T4"), None),
    ]
    assert _classify_multi_leg(butterfly) == "Iron butterfly"


def test_classify_multi_leg_covered_call_and_protective_put():
    from ibkr_backend.analytics.metrics import _classify_multi_leg

    cc = [
        (_mk_stk("XYZ", "BUY", 100), None),
        (_mk_opt("XYZ   260918C00050000", "SELL", 1, "T2"), None),
    ]
    assert _classify_multi_leg(cc) == "Covered call"

    pp = [
        (_mk_stk("XYZ", "BUY", 100), None),
        (_mk_opt("XYZ   260918P00040000", "BUY", 1, "T2"), None),
    ]
    assert _classify_multi_leg(pp) == "Protective put"


def test_classify_multi_leg_covered_call_requires_underlying_match():
    """Cross-underlying stock+option in the same ib_order_id (IBKR can encode
    unrelated legs in a single block) must not be mislabelled as a covered call.
    """
    from ibkr_backend.analytics.metrics import _classify_multi_leg

    weird = [
        (_mk_stk("AAA", "BUY", 100), None),
        (_mk_opt("BBB   260918C00050000", "SELL", 1, "T2"), None),  # different underlying
    ]
    assert _classify_multi_leg(weird) is None


def test_classify_multi_leg_unrecognised_returns_none():
    from ibkr_backend.analytics.metrics import _classify_multi_leg

    # Two long calls at different strikes — not a recognised symmetric pattern.
    weird = [
        (_mk_opt("XYZ   260918C00040000", "BUY", 1, "T1"), None),
        (_mk_opt("XYZ   260918C00045000", "BUY", 1, "T2"), None),
    ]
    assert _classify_multi_leg(weird) is None

    # Cross-underlying basket.
    cross = [
        (_mk_opt("XYZ   260918C00040000", "BUY", 1, "T1"), None),
        (_mk_opt("ABC   260918C00040000", "BUY", 1, "T2"), None),
    ]
    assert _classify_multi_leg(cross) is None


def test_executions_list_multi_fill_only_filter():
    """The flag should drop single-fill orders. We seed one extra single-fill
    order so the filter has something to drop.
    """
    from ibkr_backend.analytics import executions_list

    EXTRA = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="dashboard_tc_daily" type="TCF">
 <FlexStatements count="1">
  <FlexStatement accountId="U99" fromDate="20260513" toDate="20260513" period="" whenGenerated="20260517">
   <TradeConfirms>
     <TradeConfirm accountId="U99" assetCategory="STK" symbol="YYY" conid="222222"
        levelOfDetail="EXECUTION" tradeID="TRADE-99"
        execID="EXEC-SOLO" orderID="ORD-SOLO"
        orderTime="20260513;100000" dateTime="20260513;100030"
        reportDate="20260513" tradeDate="20260513" settleDate="20260515"
        buySell="BUY" quantity="1" price="50" amount="50"
        proceeds="-50" netCash="-50.5" commission="-0.5"
        commissionCurrency="USD" currency="USD" multiplier="1" exchange="XEX"
        transactionType="ExchTrade" orderType="LMT" code="O" />
   </TradeConfirms>
  </FlexStatement>
 </FlexStatements>
</FlexQueryResponse>
"""

    _seed(TC_PIPELINE_XML)
    _seed(EXTRA)

    with session_scope() as s:
        unfiltered = executions_list(s)
        assert unfiltered["summary"]["total_fills"] == 3
        assert unfiltered["summary"]["distinct_orders"] == 2

        only_multi = executions_list(s, multi_fill_only=True)
        assert only_multi["summary"]["total_fills"] == 2
        assert {r["order_id"] for r in only_multi["rows"]} == {"ORD-A"}


def test_canon_asset_class_maps_legacy_labels():
    """Regression: parser-emitted descriptive labels must collapse to short codes
    so `.where(asset_class == "OPT")` matches both legacy and modern rows."""
    from ibkr_backend.loaders._asset_class import canon

    assert canon("Stocks") == "STK"
    assert canon("Equity and Index Options") == "OPT"
    assert canon("Forex") == "CASH"
    assert canon("Funds") == "FUND"
    # Already-canonical codes pass through.
    assert canon("STK") == "STK"
    assert canon("OPT") == "OPT"
    # None/empty handled gracefully.
    assert canon(None) is None
    assert canon("") is None
    # Case-insensitive.
    assert canon("stocks") == "STK"
    assert canon("EQUITY AND INDEX OPTIONS") == "OPT"
    # Unknown codes pass through verbatim (no silent renames).
    assert canon("XYZ") == "XYZ"


def _cin_flow_row(d, dw=0, at=0, twr="0"):
    from decimal import Decimal

    from ibkr_backend.db.models import ChangeInNavDaily

    return ChangeInNavDaily(
        snapshot_date=d, account_id="U1", currency="CHF", twr=Decimal(twr),
        deposits_withdrawals=Decimal(str(dw)), asset_transfers=Decimal(str(at)),
    )


def test_xirr_converges_with_correct_sign_convention():
    """Regression: deposits must be NEGATIVE flows so pyxirr can converge.

    Flows come from ChangeInNAV (`depositsWithdrawals + assetTransfers` on the
    CIN date). A portfolio with deposits + terminal NAV > deposits gives a
    positive money-weighted return; the all-time window is > 365 days so the
    rate is annualised.
    """
    from ibkr_backend.analytics.xirr import xirr_summary
    from ibkr_backend.db.models import Account, NavDaily

    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.flush()
        s.add_all([
            _cin_flow_row(date(2024, 1, 1), dw=10_000),
            _cin_flow_row(date(2025, 1, 1), dw=10_000),
            NavDaily(snapshot_date=date(2026, 1, 1), account_id="U1",
                     base_currency="CHF", nav_total=24_000),
        ])
        s.commit()
        result = xirr_summary(s)

    assert result["xirr_pct"] is not None
    assert result["xirr_pct"] > 0
    assert result["xirr_pct"] < 100  # sanity: not a runaway
    assert result["annualised"] is True
    assert result["flow_count"] == 3  # 2 deposits + 1 terminal NAV
    flow_sum = sum(f["amount"] for f in result["flows"])
    assert abs(flow_sum - 4_000) < 0.01  # absolute profit


def test_xirr_counts_asset_transfers_as_contributions():
    """In-kind transfers are flows, not profit (B02): 10 000 in cash + 10 000
    of securities transferred in, ending at 20 000 → ~0 % money-weighted."""
    from ibkr_backend.analytics.xirr import xirr_summary
    from ibkr_backend.db.models import Account, NavDaily

    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.flush()
        s.add_all([
            _cin_flow_row(date(2024, 1, 1), dw=10_000),
            _cin_flow_row(date(2024, 6, 1), at=10_000),
            NavDaily(snapshot_date=date(2025, 6, 1), account_id="U1",
                     base_currency="CHF", nav_total=20_000),
        ])
        s.commit()
        result = xirr_summary(s)

    assert result["xirr_pct"] == pytest.approx(0.0, abs=0.01)


def test_xirr_sub_period_uses_opening_nav():
    """When filtering by period, XIRR must use opening NAV as the initial
    investment — not just the explicit flows within the period. A window
    shorter than 365 days is NOT annualised (xirr_pct null, GIPS), but the
    flow construction is still reported."""
    from ibkr_backend.analytics.filters import PeriodFilter
    from ibkr_backend.analytics.xirr import xirr_summary
    from ibkr_backend.db.models import Account, NavDaily

    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.flush()
        s.add_all([
            NavDaily(snapshot_date=date(2025, 12, 31), account_id="U1",
                     base_currency="CHF", nav_total=100_000),
            _cin_flow_row(date(2026, 2, 1), dw=5_000),
            NavDaily(snapshot_date=date(2026, 6, 1), account_id="U1",
                     base_currency="CHF", nav_total=120_000),
        ])
        s.commit()
        period = PeriodFilter(start=date(2026, 1, 1), end=None, label="ytd")
        result = xirr_summary(s, period=period)

    assert result["annualised"] is False
    assert result["xirr_pct"] is None
    # 3 flows: synthesised opening NAV at period.start + 1 deposit + terminal NAV.
    assert result["flow_count"] == 3
    first = result["flows"][0]
    assert first["date"] == "2026-01-01"
    assert first["amount"] == -100_000


def test_xirr_sub_period_over_a_year_uses_opening_nav():
    from ibkr_backend.analytics.filters import PeriodFilter
    from ibkr_backend.analytics.xirr import xirr_summary
    from ibkr_backend.db.models import Account, NavDaily

    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.flush()
        s.add_all([
            NavDaily(snapshot_date=date(2024, 12, 31), account_id="U1",
                     base_currency="CHF", nav_total=100_000),
            NavDaily(snapshot_date=date(2026, 2, 1), account_id="U1",
                     base_currency="CHF", nav_total=121_000),
        ])
        s.commit()
        period = PeriodFilter(start=date(2025, 1, 1), end=None, label="1y+")
        result = xirr_summary(s, period=period)

    assert result["annualised"] is True
    assert result["xirr_pct"] is not None
    assert 10 < result["xirr_pct"] < 25


def test_dividends_by_country_falls_back_to_isin_in_description():
    """IBKR Flex emits dividend CashTransactions with `country`, `conid`,
    and `symbol` all NULL — only `description` carries the ticker + ISIN
    ('TICKER(<ISIN>) Cash Dividend ...'). The 2-letter country prefix of
    the ISIN must be used as the last-resort fallback. Without this, real
    accounts roll up 100 % of gross dividends under 'UNKNOWN'."""
    from datetime import datetime
    from decimal import Decimal
    from ibkr_backend.db.models import Account, CashTransaction

    with session_scope() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.flush()
        s.add_all([
            # US dividend, no symbol/conid/country, ISIN in description
            CashTransaction(
                transaction_id="D1", account_id="U99", conid=None, symbol=None,
                type="Dividends", amount=Decimal("10"), currency="USD",
                fx_rate_to_base=Decimal("1"),
                event_datetime=datetime(2024, 3, 1), settle_date=date(2024, 3, 3),
                description="ADP(US0530151036) Cash Dividend USD 1.40 per Share",
            ),
            # ES dividend
            CashTransaction(
                transaction_id="D2", account_id="U99", conid=None, symbol=None,
                type="Dividends", amount=Decimal("5"), currency="EUR",
                fx_rate_to_base=Decimal("1.1"),
                event_datetime=datetime(2024, 4, 1), settle_date=date(2024, 4, 3),
                description="RED(ES0173093024) Cash Dividend EUR 0.27 per Share",
            ),
            # US withholding tax on the ADP dividend
            CashTransaction(
                transaction_id="W1", account_id="U99", conid=None, symbol=None,
                type="Withholding Tax", amount=Decimal("-1.5"), currency="USD",
                fx_rate_to_base=Decimal("1"),
                event_datetime=datetime(2024, 3, 1), settle_date=date(2024, 3, 3),
                description="ADP(US0530151036) Cash Dividend USD 1.40 per Share - US Tax",
            ),
        ])
        s.commit()

        rows = dividends_by_country(s, period=resolve_period(year=2024))

    by = {r["country"]: r for r in rows}
    assert set(by) == {"US", "ES"}, "country must be parsed from ISIN, not bucketed as UNKNOWN"
    assert by["US"]["gross"] == 10.0
    assert by["US"]["withholding"] == -1.5
    assert by["US"]["net"] == 8.5
    assert by["ES"]["gross"] == pytest.approx(5.5)  # 5 EUR × 1.1 fx
    assert by["ES"]["withholding"] == 0
    assert by["ES"]["net"] == pytest.approx(5.5)


def test_trades_list_status_filter_handles_compound_close_codes():
    """IBKR Flex marks closes with compound codes — `C;IA` (internal
    adjustment), `C;P` (partial), `C;FPA;P`, `C;IM;P`. The Operaciones tab
    `status=closed` filter and the per-row `is_closed` badge must treat
    ANY code starting with 'C' as closed; strict `== 'C'` drops ~3 % of
    real STK closes in affected exports.
    """
    from datetime import datetime
    from decimal import Decimal
    from ibkr_backend.analytics import trades_list
    from ibkr_backend.db.models import Account, Instrument, Trade

    with session_scope() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.flush()
        s.add(Instrument(conid=4001, symbol="CMP", description="Compound", asset_class="STK"))
        s.flush()
        for tid, code in (
            ("T1", "C"), ("T2", "C;IA"), ("T3", "C;P"),
            ("T4", "C;FPA;P"), ("T5", "O"), ("T6", "O;P"),
        ):
            s.add(Trade(
                trade_id=tid, ib_order_id=f"ORD-{tid}", account_id="U99",
                conid=4001, symbol="CMP", asset_class="STK",
                currency="USD", trade_date=date(2026, 4, 1),
                trade_datetime=datetime(2026, 4, 1, 14, 0),
                buy_sell="SELL", open_close=code, quantity=Decimal("-1"),
                trade_price=Decimal("10"), proceeds=Decimal("10"),
                ib_commission=Decimal("0"), net_cash=Decimal("10"),
                fifo_pnl_realized=Decimal("10"), fx_rate_to_base=Decimal("1"),
            ))
        s.commit()

        closed = trades_list(s, status="closed")
        open_ = trades_list(s, status="open")
        all_ = trades_list(s, status="all")

    closed_ids = {r["trade_id"] for r in closed["rows"]}
    open_ids = {r["trade_id"] for r in open_["rows"]}
    assert closed_ids == {"T1", "T2", "T3", "T4"}, "compound C codes must be counted as closed"
    assert open_ids == {"T5", "T6"}, "compound O codes must be counted as open"
    assert closed["summary"]["total"] == 4
    assert len(all_["rows"]) == 6
    # is_closed / is_open per-row must agree with the filter.
    for r in closed["rows"]:
        assert r["is_closed"] is True and r["is_open"] is False
    for r in open_["rows"]:
        assert r["is_open"] is True and r["is_closed"] is False
