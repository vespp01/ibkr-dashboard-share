"""Cashflow-only nav_daily rows (nav_total NULL) must not corrupt analytics.

`_refresh_external_cashflows` books deposits on their SETTLE date even when
that date has no EquitySummary row (settle T+1 past statement end, holiday,
pre-coverage history) — creating rows with nav_total NULL and a nonzero
external_cashflow. Regressions covered here:

  - TWR must carry such a cashflow to the next day WITH a NAV instead of
    dropping it (otherwise the NAV jump is booked as performance).
  - XIRR and portfolio_overview must pick the latest NAV with
    `nav_total IS NOT NULL`, not the phantom row.
  - The per-statement refresh and the full-history reconcile must derive the
    SAME external_cashflow (settle-date only, fx required) so a daily pull
    never re-introduces values that `reconcile-cashflows` corrected.
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
from ibkr_backend.db.models import Account, CashTransaction, NavDaily
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


# ---------------------------------------------------------------- B1 — TWR

def test_twr_carries_cashflow_from_navless_day_to_next_nav_day():
    """End-to-end repro: statement 1 has NAV for day 1 and a deposit settling
    on day 2 (no NAV row → phantom row). Statement 2 brings NAV for day 3 only
    (day 2 stays NAV-less). The 10 000 deposit must NOT be booked as return:
    r_3 = (110000 - 100000 - 10000) / (100000 + 0.5*10000) = 0.
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
    stmt2 = _flex("""
   <EquitySummaryByReportDateInBase>
     <EquitySummaryByReportDateInBase accountId="U42" reportDate="20250604" currency="CHF" total="110000" cash="110000" stock="0" />
   </EquitySummaryByReportDateInBase>
""")
    _seed_xml(stmt1)
    _seed_xml(stmt2)

    with session_scope() as s:
        # Precondition: the phantom row exists (nav_total NULL, cashflow set).
        phantom = s.get(NavDaily, (date(2025, 6, 3), "U42"))
        assert phantom is not None
        assert phantom.nav_total is None
        assert phantom.external_cashflow == Decimal("10000")

        out = twr_series(s, account_id="U42")

    # Phantom day is skipped from the series but its cashflow is carried.
    assert [p["date"] for p in out["series"]] == ["2025-06-02", "2025-06-04"]
    assert out["series"][-1]["cashflow"] == pytest.approx(10000.0)
    assert out["series"][-1]["daily_return_pct"] == pytest.approx(0.0, abs=1e-9)
    assert out["raw_twr_pct"] == pytest.approx(0.0, abs=1e-9)


def test_twr_without_carry_would_show_phantom_gain():
    """Sanity companion: same NAVs with the deposit correctly attached to the
    NAV day yield 0 % — only a *dropped* cashflow fakes the +10 %."""
    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.flush()
        s.add_all([
            NavDaily(snapshot_date=date(2025, 6, 2), account_id="U1",
                     base_currency="CHF", nav_total=Decimal("100000"),
                     external_cashflow=Decimal("0")),
            NavDaily(snapshot_date=date(2025, 6, 3), account_id="U1",
                     base_currency="CHF", nav_total=None,
                     external_cashflow=Decimal("10000")),
            NavDaily(snapshot_date=date(2025, 6, 4), account_id="U1",
                     base_currency="CHF", nav_total=Decimal("110000"),
                     external_cashflow=Decimal("0")),
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
            CashTransaction(
                transaction_id="dep-1", account_id="U1",
                type="Deposits/Withdrawals", amount=Decimal("10000"),
                currency="CHF", fx_rate_to_base=Decimal("1"),
                settle_date=date(2024, 1, 1),
                event_datetime=datetime(2024, 1, 1, 12, 0),
            ),
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
            NavDaily(snapshot_date=date(2025, 12, 30), account_id="U1",
                     base_currency="CHF", nav_total=Decimal("100000")),
            # Phantom row between the real opening NAV and period.start.
            NavDaily(snapshot_date=date(2025, 12, 31), account_id="U1",
                     base_currency="CHF", nav_total=None,
                     external_cashflow=Decimal("5000")),
            NavDaily(snapshot_date=date(2026, 6, 1), account_id="U1",
                     base_currency="CHF", nav_total=Decimal("120000")),
        ])
        s.commit()
        period = PeriodFilter(start=date(2026, 1, 1), end=None, label="ytd")
        result = xirr_summary(s, account_id="U1", period=period)

    assert result["flows"][0] == {"date": "2026-01-01", "amount": -100000.0}
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
    assert out["nav_date"] == "2026-06-01"
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
    (which would report absolute_profit = -net_deposits)."""
    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.flush()
        s.add_all([
            NavDaily(snapshot_date=date(2026, 6, 1), account_id="U1",
                     base_currency="CHF", nav_total=Decimal("60000")),
            NavDaily(snapshot_date=date(2026, 6, 3), account_id="U1",
                     base_currency="CHF", nav_total=None,
                     external_cashflow=Decimal("10000")),
            CashTransaction(transaction_id="CFX1", account_id="U1",
                            type="Deposits/Withdrawals",
                            amount=Decimal("50000"), currency="CHF",
                            fx_rate_to_base=Decimal("1"),
                            event_datetime=datetime(2026, 6, 2, 0, 0),
                            settle_date=date(2026, 6, 3)),
        ])
        s.commit()
        out = total_return_on_cash(s, account_id="U1")

    assert out["current_nav_base"] == 60000.0
    assert out["absolute_profit_base"] == pytest.approx(10000.0)


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
