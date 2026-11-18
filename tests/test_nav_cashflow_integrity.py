"""Cash-flow-only `nav_daily` rows (nav_total NULL) must not corrupt analytics.

History: `_refresh_external_cashflows` used to book deposits on their SETTLE
date even when that date had no EquitySummary row (weekend, T+1 past statement
end), creating rows with nav_total NULL. Since 2026-10 (B50, B01):

  - the loader NEVER inserts such rows any more (it only mirrors flows onto
    existing NAV days; `drop-navless-nav-rows` removes old residue);
  - analytics read flows and returns from ChangeInNAV, not from
    `nav_daily.external_cashflow`, so a stray NAV-less row can no longer book a
    NAV jump as performance;
  - XIRR, the all-time return and portfolio_overview still pick the latest NAV
    with `nav_total IS NOT NULL`, never a stray row;
  - the per-statement refresh and the full-history reconcile derive the SAME
    external_cashflow (settle-date only, fx required).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from ibkr_backend.analytics import (
    margin_summary,
    portfolio_overview,
    total_return_on_cash,
    twr_series,
)
from ibkr_backend.analytics.filters import PeriodFilter
from ibkr_backend.analytics.xirr import xirr_summary
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import Account, ChangeInNavDaily, NavDaily
from ibkr_backend.flex.parser import parse_flex_xml
from ibkr_backend.loaders.pipeline import load_statement, reconcile_external_cashflows


def _seed_xml(xml_bytes: bytes) -> None:
    statements = parse_flex_xml(xml_bytes)
    with session_scope() as session:
        for stmt in statements:
            load_statement(session, stmt)


def _flex(body: str, *, account: str = "U42") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="t" type="AF">
 <FlexStatements count="1">
  <FlexStatement accountId="{account}" fromDate="20250601" toDate="20250610" whenGenerated="20250611">
{body}
  </FlexStatement>
 </FlexStatements>
</FlexQueryResponse>""".encode()


# ------------------------------------------- loader never creates NAV-less rows

def test_loader_does_not_create_navless_rows():
    """Statement 1: NAV for day 1 and a deposit settling on day 2 (no NAV row).
    The old loader created a phantom nav_daily row for day 2; now it must not.
    """
    stmt1 = _flex("""
   <EquitySummaryByReportDateInBase>
     <EquitySummaryByReportDateInBase accountId="U42" reportDate="20250602" currency="CHF" total="100000" cash="100000" stock="0" />
   </EquitySummaryByReportDateInBase>
   <CashTransactions>
     <CashTransaction transactionID="CF1" accountId="U42" type="Deposits/Withdrawals"
        amount="10000" currency="CHF" fxRateToBase="1"
        dateTime="20250602" settleDate="20250603" description="Deposit settling T+1" />
   </CashTransactions>
""")
    _seed_xml(stmt1)

    with session_scope() as s:
        assert s.get(NavDaily, (date(2025, 6, 3), "U42")) is None
        assert s.get(NavDaily, (date(2025, 6, 2), "U42")).nav_total == Decimal("100000")


def test_twr_ignores_stray_navless_rows():
    """A NAV-less nav_daily row (old residue) cannot change the TWR: the chain
    is built from ChangeInNAV only."""
    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.flush()
        s.add_all([
            NavDaily(snapshot_date=date(2025, 6, 2), account_id="U1",
                     base_currency="CHF", nav_total=Decimal("100000")),
            NavDaily(snapshot_date=date(2025, 6, 3), account_id="U1",
                     base_currency="CHF", nav_total=None,
                     external_cashflow=Decimal("10000")),
            NavDaily(snapshot_date=date(2025, 6, 4), account_id="U1",
                     base_currency="CHF", nav_total=Decimal("110000")),
            # Deposit of 10 000 booked on the NAV date: IBKR twr is 0 that day.
            ChangeInNavDaily(snapshot_date=date(2025, 6, 4), account_id="U1",
                             currency="CHF", starting_value=Decimal("100000"),
                             ending_value=Decimal("110000"),
                             deposits_withdrawals=Decimal("10000"),
                             twr=Decimal("0")),
        ])
        s.commit()
        out = twr_series(s, account_id="U1")

    assert out["raw_twr_pct"] == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------- B1 — XIRR

def test_xirr_terminal_nav_skips_cashflow_only_row():
    """When the newest nav_daily row is a phantom (nav_total NULL), XIRR must
    fall back to the latest REAL NAV instead of erroring out."""
    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.flush()
        s.add_all([
            ChangeInNavDaily(snapshot_date=date(2024, 1, 1), account_id="U1",
                             currency="CHF", deposits_withdrawals=Decimal("10000"),
                             twr=Decimal("0")),
            NavDaily(snapshot_date=date(2025, 1, 1), account_id="U1",
                     base_currency="CHF", nav_total=Decimal("12000")),
            # Phantom row newer than the last real NAV.
            NavDaily(snapshot_date=date(2025, 1, 3), account_id="U1",
                     base_currency="CHF", nav_total=None,
                     external_cashflow=Decimal("5000")),
        ])
        s.commit()
        result = xirr_summary(s, account_id="U1")

    assert result.get("error") is None, result.get("error")
    assert result["terminal_nav_base"] == 12000.0
    assert result["terminal_date"] == "2025-01-01"
    assert result["xirr_pct"] == pytest.approx(20.0, abs=0.5)


def test_xirr_opening_nav_skips_cashflow_only_row():
    """The synthesised opening investment for sub-period XIRR must also use the
    latest REAL NAV before period.start, not a newer phantom row."""
    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.flush()
        s.add_all([
            NavDaily(snapshot_date=date(2024, 12, 30), account_id="U1",
                     base_currency="CHF", nav_total=Decimal("100000")),
            # Phantom row between the real opening NAV and period.start.
            NavDaily(snapshot_date=date(2024, 12, 31), account_id="U1",
                     base_currency="CHF", nav_total=None,
                     external_cashflow=Decimal("5000")),
            NavDaily(snapshot_date=date(2026, 2, 1), account_id="U1",
                     base_currency="CHF", nav_total=Decimal("120000")),
        ])
        s.commit()
        period = PeriodFilter(start=date(2025, 1, 1), end=None, label="1y+")
        result = xirr_summary(s, account_id="U1", period=period)

    assert result["flows"][0] == {"date": "2025-01-01", "amount": -100000.0}
    assert result.get("note") is None
    assert result["xirr_pct"] is not None
    assert -100 < result["xirr_pct"] < 200


# ------------------------------------------------------- B1 — overview KPI

def test_portfolio_overview_nav_skips_cashflow_only_row():
    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.flush()
        s.add_all([
            NavDaily(snapshot_date=date(2026, 6, 1), account_id="U1",
                     base_currency="CHF", nav_total=Decimal("50000")),
            NavDaily(snapshot_date=date(2026, 6, 3), account_id="U1",
                     base_currency="CHF", nav_total=None,
                     external_cashflow=Decimal("10000")),
        ])
        s.commit()
        out = portfolio_overview(s, account_id="U1")

    assert out["nav_base"] == 50000.0
    assert out["freshness"]["nav_as_of"] == "2026-06-01"


# ------------------------------------ B4 — refresh ≡ reconcile (one rule)

def test_refresh_and_reconcile_agree_on_external_cashflows():
    """Same statement, both derivation paths, same result — so a daily pull can
    no longer re-introduce values that reconcile-cashflows corrected.

    Canonical rule (the reconcile one): book on SETTLE date only, require
    fx_rate_to_base, sum amount*fx. Rows without settle date or fx are dropped.
    """
    xml = _flex("""
   <EquitySummaryByReportDateInBase>
     <EquitySummaryByReportDateInBase accountId="U42" reportDate="20250602" currency="CHF" total="50000" cash="50000" stock="0" />
     <EquitySummaryByReportDateInBase accountId="U42" reportDate="20250603" currency="CHF" total="61000" cash="61000" stock="0" />
   </EquitySummaryByReportDateInBase>
   <CashTransactions>
     <CashTransaction transactionID="CF1" accountId="U42" type="Deposits/Withdrawals"
        amount="10000" currency="EUR" fxRateToBase="0.95"
        dateTime="20250602" settleDate="20250603" description="Deposit, settles T+1" />
     <CashTransaction transactionID="CF2" accountId="U42" type="Deposits/Withdrawals"
        amount="7000" currency="CHF" fxRateToBase="1"
        dateTime="20250602" description="Deposit without settle date" />
     <CashTransaction transactionID="CF3" accountId="U42" type="Deposits/Withdrawals"
        amount="3000" currency="CHF"
        dateTime="20250602" settleDate="20250602" description="Deposit without fx" />
   </CashTransactions>
""")
    _seed_xml(xml)

    def _snapshot_cashflows() -> dict[date, Decimal]:
        with session_scope() as s:
            rows = s.query(NavDaily).filter(NavDaily.account_id == "U42").all()
            return {
                r.snapshot_date: (r.external_cashflow if r.external_cashflow is not None else Decimal("0"))
                for r in rows
            }

    after_refresh = _snapshot_cashflows()
    # Booked on the SETTLE date (2025-06-03) at amount*fx; the event date
    # (2025-06-02) gets nothing — neither from CF2 (no settle) nor CF3 (no fx).
    assert after_refresh[date(2025, 6, 2)] == Decimal("0")
    assert after_refresh[date(2025, 6, 3)] == Decimal("9500")  # 10000 * 0.95

    with session_scope() as s:
        changed = reconcile_external_cashflows(s)

    assert changed == 0, "reconcile must agree with the per-statement refresh"
    assert _snapshot_cashflows() == after_refresh


def test_reconcile_matches_refresh_after_repeated_pull():
    """Daily-pull replay: re-loading the same statement after a reconcile must
    not flip external_cashflow back to a divergent value (the original bug:
    refresh wrote event-date/unconverted values, reconcile zeroed them, the
    next pull re-wrote them)."""
    xml = _flex("""
   <EquitySummaryByReportDateInBase>
     <EquitySummaryByReportDateInBase accountId="U42" reportDate="20250602" currency="CHF" total="50000" cash="50000" stock="0" />
   </EquitySummaryByReportDateInBase>
   <CashTransactions>
     <CashTransaction transactionID="CF2" accountId="U42" type="Deposits/Withdrawals"
        amount="7000" currency="CHF" fxRateToBase="1"
        dateTime="20250602" description="Deposit without settle date" />
   </CashTransactions>
""")
    _seed_xml(xml)
    with session_scope() as s:
        reconcile_external_cashflows(s)
    _seed_xml(xml)  # replayed daily pull

    with session_scope() as s:
        nav = s.get(NavDaily, (date(2025, 6, 2), "U42"))
        changed = reconcile_external_cashflows(s)

    assert nav.external_cashflow == Decimal("0")
    assert changed == 0


# ------------------------------ B1-ext — 4th/5th consumers (review follow-up)

def test_total_return_on_cash_skips_cashflow_only_row():
    """A deposit settling after the last real NAV must not zero current_nav
    (which would report absolute_profit = -net_deposits). Net deposits come
    from ChangeInNAV flows (cash + in-kind)."""
    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.flush()
        s.add_all([
            NavDaily(snapshot_date=date(2026, 6, 1), account_id="U1",
                     base_currency="CHF", nav_total=Decimal("60000")),
            NavDaily(snapshot_date=date(2026, 6, 3), account_id="U1",
                     base_currency="CHF", nav_total=None,
                     external_cashflow=Decimal("10000")),
            ChangeInNavDaily(snapshot_date=date(2026, 5, 1), account_id="U1",
                             currency="CHF", deposits_withdrawals=Decimal("40000"),
                             asset_transfers=Decimal("10000"), twr=Decimal("0")),
        ])
        s.commit()
        out = total_return_on_cash(s, account_id="U1")

    assert out["current_nav_base"] == 60000.0
    assert out["cumulative_net_deposits_base"] == pytest.approx(50000.0)
    assert out["absolute_profit_base"] == pytest.approx(10000.0)
    assert "cagr_pct" not in out


def test_margin_summary_skips_cashflow_only_row():
    """margin_summary's default snapshot must be the latest REAL NAV, not a
    phantom cashflow-only date (nav_total would degrade to 0.0)."""
    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.flush()
        s.add_all([
            NavDaily(snapshot_date=date(2026, 6, 1), account_id="U1",
                     base_currency="CHF", nav_total=Decimal("60000")),
            NavDaily(snapshot_date=date(2026, 6, 3), account_id="U1",
                     base_currency="CHF", nav_total=None,
                     external_cashflow=Decimal("10000")),
        ])
        s.commit()
        out = margin_summary(s, account_id="U1")

    assert out["snapshot_date"] == "2026-06-01"
    assert out["nav_total"] == 60000.0
