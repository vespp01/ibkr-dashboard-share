"""Parser unit tests."""

from __future__ import annotations

from decimal import Decimal

from ibkr_backend.flex.parser import parse_flex_xml


def test_parses_top_level_metadata(sample_flex_xml):
    statements = parse_flex_xml(sample_flex_xml)
    assert len(statements) == 1
    s = statements[0]
    assert s.account.account_id == "U1234567"
    assert s.account.base_currency == "EUR"
    assert s.account.alias == "main"
    assert s.from_date.isoformat() == "2025-01-01"
    assert s.to_date.isoformat() == "2025-01-31"


def test_parses_instruments(sample_flex_xml):
    s = parse_flex_xml(sample_flex_xml)[0]
    by_conid = {i.conid: i for i in s.instruments}
    assert 265598 in by_conid
    aapl = by_conid[265598]
    assert aapl.symbol == "AAPL"
    assert aapl.issuer_country == "US"
    assert aapl.sector == "Technology"


def test_parses_trades_with_fifo_pnl(sample_flex_xml):
    s = parse_flex_xml(sample_flex_xml)[0]
    by_id = {t.trade_id: t for t in s.trades}
    assert {"T1001", "T1002"} <= set(by_id)
    assert by_id["T1002"].fifo_pnl_realized == Decimal("74.00")
    assert by_id["T1001"].ib_commission == Decimal("-1.00")
    assert by_id["T1001"].fx_rate_to_base == Decimal("0.92")


def test_parses_cash_transactions_country_from_description(sample_flex_xml):
    s = parse_flex_xml(sample_flex_xml)[0]
    by_id = {ct.transaction_id: ct for ct in s.cash_transactions}
    assert by_id["C2001"].type == "Dividends"
    assert by_id["C2002"].type == "Withholding Tax"
    # parsed from " - US TAX" suffix
    assert by_id["C2002"].country == "US"


def test_parses_open_positions(sample_flex_xml):
    s = parse_flex_xml(sample_flex_xml)[0]
    by_conid = {p.conid: p for p in s.open_positions}
    assert by_conid[265598].fifo_pnl_unrealized == Decimal("100.00")
    assert by_conid[100000].position_value == Decimal("2400.00")


def test_parses_nav_series(sample_flex_xml):
    s = parse_flex_xml(sample_flex_xml)[0]
    assert len(s.nav_rows) == 6
    assert s.nav_rows[-1].nav_total == Decimal("16200")


FIFO_MTM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="t" type="AF">
 <FlexStatements count="1">
  <FlexStatement accountId="U99" fromDate="20260101" toDate="20260103" whenGenerated="20260104">
   <AccountInformation accountId="U99" currency="CHF" />
   <FIFOPerformanceSummaryInBase>
     <FIFOPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="AAPL"
        conid="265598" reportDate="20260101"
        realizedSTProfit="0" realizedSTLoss="0" realizedLTProfit="0" realizedLTLoss="0"
        totalRealizedPnl="0" unrealizedProfit="50" unrealizedLoss="0"
        unrealizedSTProfit="50" unrealizedSTLoss="0" unrealizedLTProfit="0" unrealizedLTLoss="0"
        totalUnrealizedPnl="50" totalFifoPnl="50" transferredPnl="0" />
     <FIFOPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="AAPL"
        conid="265598" reportDate="20260102"
        realizedSTProfit="0" realizedSTLoss="0" realizedLTProfit="0" realizedLTLoss="0"
        totalRealizedPnl="0" unrealizedProfit="80" unrealizedLoss="0"
        unrealizedSTProfit="80" unrealizedSTLoss="0" unrealizedLTProfit="0" unrealizedLTLoss="0"
        totalUnrealizedPnl="80" totalFifoPnl="80" transferredPnl="0" />
     <FIFOPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="AAPL"
        conid="265598" reportDate="20260103"
        realizedSTProfit="120" realizedSTLoss="0" realizedLTProfit="0" realizedLTLoss="0"
        totalRealizedPnl="120" unrealizedProfit="0" unrealizedLoss="0"
        unrealizedSTProfit="0" unrealizedSTLoss="0" unrealizedLTProfit="0" unrealizedLTLoss="0"
        totalUnrealizedPnl="0" totalFifoPnl="120" transferredPnl="0" />
     <FIFOPerformanceSummaryUnderlying accountId="U99" assetCategory="" symbol=""
        conid="" reportDate="20260103"
        totalRealizedPnl="120" totalUnrealizedPnl="0" totalFifoPnl="120" />
   </FIFOPerformanceSummaryInBase>
   <MTMPerformanceSummaryInBase>
     <MTMPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="AAPL"
        conid="265598" reportDate="20260101"
        prevCloseQuantity="0" prevClosePrice="0" closeQuantity="10" closePrice="195"
        transactionMtm="50" priorOpenMtm="0" commissions="-1" other="0"
        otherWithAccruals="0" total="49" totalWithAccruals="49" />
     <MTMPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="AAPL"
        conid="265598" reportDate="20260102"
        prevCloseQuantity="10" prevClosePrice="195" closeQuantity="10" closePrice="198"
        transactionMtm="0" priorOpenMtm="30" commissions="0" other="0"
        otherWithAccruals="0" total="30" totalWithAccruals="30" />
     <MTMPerformanceSummaryUnderlying accountId="U99" assetCategory="STK" symbol="AAPL"
        conid="265598" reportDate="20260103"
        prevCloseQuantity="10" prevClosePrice="198" closeQuantity="0" closePrice="210"
        transactionMtm="120" priorOpenMtm="-80" commissions="-1" other="0"
        otherWithAccruals="0" total="39" totalWithAccruals="39" />
   </MTMPerformanceSummaryInBase>
  </FlexStatement>
 </FlexStatements>
</FlexQueryResponse>
"""


def test_parses_fifo_performance_underlying():
    s = parse_flex_xml(FIFO_MTM_XML)[0]
    rows = sorted(s.fifo_performance, key=lambda r: r.snapshot_date)
    # 3 valid AAPL rows; the conid-less account-total row is dropped.
    assert len(rows) == 3
    assert all(r.conid == 265598 for r in rows)
    assert rows[0].total_unrealized_pnl == Decimal("50")
    assert rows[-1].total_realized_pnl == Decimal("120")
    assert rows[-1].snapshot_date.isoformat() == "2026-01-03"


def test_parses_mtm_performance_underlying():
    s = parse_flex_xml(FIFO_MTM_XML)[0]
    rows = sorted(s.mtm_performance, key=lambda r: r.snapshot_date)
    assert len(rows) == 3
    assert rows[0].close_price == Decimal("195")
    assert rows[-1].transaction_mtm == Decimal("120")
    # MTM total is daily — sum across days is the period total.
    assert sum(r.total for r in rows) == Decimal("118")


# Synthetic Trade Confirmation Flex payload exercising the three row types TC
# emits (SymbolSummary / Order / TradeConfirm). Only TradeConfirm with
# levelOfDetail=EXECUTION is expected through the parser. The
# placed-Apr-15 / filled-May-13 split exercises the order_time ≠ execution_time
# axis that only TC surfaces; values here are illustrative, not from a real fill.
TC_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="dashboard_tc_daily" type="TCF">
 <FlexStatements count="1">
  <FlexStatement accountId="U99" fromDate="20260513" toDate="20260513" period="" whenGenerated="20260517">
   <TradeConfirms>
     <SymbolSummary accountId="U99" assetCategory="STK" symbol="ZZZ" conid="111111"
        levelOfDetail="SYMBOL_SUMMARY" tradeID="" execID="" orderID=""
        tradeDate="20260513" buySell="BUY" quantity="1" price="100" />
     <Order accountId="U99" assetCategory="STK" symbol="ZZZ" conid="111111"
        levelOfDetail="ORDER" tradeID="" execID="" orderID="ORD-A"
        orderTime="20260415;082454" dateTime="20260513;062422"
        tradeDate="20260513" buySell="BUY" quantity="1" price="100" />
     <TradeConfirm accountId="U99" assetCategory="STK" subCategory="COMMON"
        symbol="ZZZ" description="EXAMPLE STOCK" conid="111111"
        securityID="" securityIDType="ISIN"
        listingExchange="XEX"
        levelOfDetail="EXECUTION" tradeID="TRADE-1"
        execID="EXEC-1" orderID="ORD-A"
        brokerageOrderID="BR-1"
        extExecID="EXT-1" blockID="0"
        orderTime="20260415;082454" dateTime="20260513;062422"
        reportDate="20260513" tradeDate="20260513" settleDate="20260515"
        buySell="BUY" quantity="1" price="100" amount="100"
        proceeds="-100" netCash="-101.5" netCashWithBillable="-101.5"
        commission="-1.5" brokerExecutionCommission="-1.0"
        brokerClearingCommission="0" thirdPartyExecutionCommission="-0.4"
        thirdPartyClearingCommission="-0.1" thirdPartyRegulatoryCommission="0"
        otherCommission="0" commissionCurrency="EUR"
        tax="0" salesTax="0" otherTax="0" tradeCharge="0"
        currency="EUR" multiplier="1" exchange="XEX"
        transactionType="ExchTrade" orderType="LMT" code="O"
        isAPIOrder="N" traderID="" orderReference="" />
   </TradeConfirms>
  </FlexStatement>
 </FlexStatements>
</FlexQueryResponse>
"""


def test_parses_trade_confirms_filters_to_execution_only():
    s = parse_flex_xml(TC_XML)[0]
    # SymbolSummary + Order rows must NOT show up — only TradeConfirm.
    assert len(s.trade_confirms) == 1
    tc = s.trade_confirms[0]
    assert tc.exec_id == "EXEC-1"
    assert tc.trade_id == "TRADE-1"
    assert tc.order_id == "ORD-A"
    # The orderTime / executionTime split — order placed Apr 15, filled May 13.
    assert tc.order_time.isoformat() == "2026-04-15T08:24:54"
    assert tc.execution_time.isoformat() == "2026-05-13T06:24:22"
    assert tc.order_type == "LMT"
    # TC's `code` field carries the open/close flag (NOT openCloseIndicator).
    assert tc.open_close == "O"
    # Commission breakdown is reconstructed.
    assert tc.commission == Decimal("-1.5")
    assert tc.broker_execution_commission == Decimal("-1.0")
    assert tc.third_party_execution_commission == Decimal("-0.4")
    assert tc.third_party_clearing_commission == Decimal("-0.1")
    # Sanity: sum of breakdown == total.
    parts = sum(
        x for x in [
            tc.broker_execution_commission, tc.broker_clearing_commission,
            tc.third_party_execution_commission, tc.third_party_clearing_commission,
            tc.third_party_regulatory_commission, tc.other_commission,
        ] if x is not None
    )
    assert parts == tc.commission


NAV_ACCRUALS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="t" type="AF">
 <FlexStatements count="1">
  <FlexStatement accountId="U99" fromDate="20260101" toDate="20260102" whenGenerated="20260103">
   <AccountInformation accountId="U99" currency="CHF" />
   <EquitySummaryByReportDateInBase>
    <EquitySummaryByReportDateInBase accountId="U99" currency="CHF"
       reportDate="20260101" total="10000" cash="1000"
       dividendsReceivable="" accruedDividends="" dividendAccruals="5" />
    <EquitySummaryByReportDateInBase accountId="U99" currency="CHF"
       reportDate="20260102" total="10100" cash="1000"
       dividendsReceivable="" accruedDividends="3" dividendAccruals="5" />
   </EquitySummaryByReportDateInBase>
  </FlexStatement>
 </FlexStatements>
</FlexQueryResponse>
"""


def test_nav_accrued_dividends_fallback_does_not_double_count():
    """Empty accruedDividends="" must NOT inherit dividendAccruals.

    `dividendAccruals` already feeds `dividends_receivable`; if the empty
    legacy attribute also fell back to it, both columns would carry the same
    value and downstream waterfalls would double-count.
    """
    s = parse_flex_xml(NAV_ACCRUALS_XML)[0]
    by_date = {r.snapshot_date.isoformat(): r for r in s.nav_rows}

    # Both modern attrs empty: exactly ONE column gets the dividendAccruals=5.
    r1 = by_date["2026-01-01"]
    assert r1.dividends_receivable == Decimal("5")
    assert r1.accrued_dividends is None

    # accruedDividends present and non-empty: each column keeps its own attr.
    r2 = by_date["2026-01-02"]
    assert r2.dividends_receivable == Decimal("5")
    assert r2.accrued_dividends == Decimal("3")
