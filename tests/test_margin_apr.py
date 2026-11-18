"""Margin APR: per-currency debt converted with fx_rates, BASE row excluded (B09)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from ibkr_backend.analytics.margin import margin_interest_summary
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import Account, CashReportRow, CashTransaction, FxRate


def test_apr_converts_each_currency_and_ignores_base_row():
    start = date(2026, 1, 1)
    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.flush()
        for i in range(10):
            d = start + timedelta(days=i)
            s.add(FxRate(rate_date=d, from_ccy="USD", to_ccy="CHF", rate=Decimal("0.8")))
            # CHF debt 1000, USD debt 1000 (= 800 CHF) every day, and the BASE summary row.
            for ccy, amount in (("CHF", -1000), ("USD", -1000), ("BASE", -1800)):
                s.add(CashReportRow(snapshot_date=d, account_id="U1", currency=ccy,
                                    ending_cash=Decimal(amount)))
        # 10 days of interest = 1800 CHF debt * 5% * 10/365 = 2.4658 CHF.
        s.add(CashTransaction(
            transaction_id="i1", account_id="U1", type="Broker Interest Paid",
            amount=Decimal("-2.4658"), currency="CHF", fx_rate_to_base=Decimal(1),
            event_datetime=datetime(2026, 1, 10, 12), settle_date=date(2026, 1, 10),
        ))
    with session_scope() as s:
        out = margin_interest_summary(s)
    assert out["avg_debt_base"] == pytest.approx(1800.0)
    assert out["effective_apr_pct"] == pytest.approx(5.0, abs=0.01)
    chf = next(r for r in out["paid_by_currency"] if r["currency"] == "CHF")
    assert chf["avg_debt_base"] == pytest.approx(1000.0)


def test_cash_interest_yield_per_currency_excludes_syep():
    """Cash side mirrors the debt side: credit interest / avg positive balance,
    per currency; idle balances show 0 %; SYEP lending income is kept apart."""
    start = date(2026, 3, 1)
    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.flush()
        for i in range(10):
            d = start + timedelta(days=i)
            s.add(FxRate(rate_date=d, from_ccy="USD", to_ccy="CHF", rate=Decimal("0.8")))
            s.add(FxRate(rate_date=d, from_ccy="HKD", to_ccy="CHF", rate=Decimal("0.1")))
            # USD 20,000 (= 16,000 CHF) and HKD 50,000 (= 5,000 CHF) every day.
            for ccy, amount in (("USD", 20000), ("HKD", 50000), ("BASE", 21000)):
                s.add(CashReportRow(snapshot_date=d, account_id="U1", currency=ccy,
                                    ending_cash=Decimal(amount)))
        # 2 % on 16,000 CHF for 10 days = 8.7671 CHF = 10.9589 USD at 0.8.
        s.add(CashTransaction(
            transaction_id="c1", account_id="U1", type="Broker Interest Received",
            amount=Decimal("10.9589"), currency="USD", fx_rate_to_base=Decimal("0.8"),
            description="USD CREDIT INT FOR MAR-2026",
            event_datetime=datetime(2026, 3, 10, 12), settle_date=date(2026, 3, 10),
        ))
        s.add(CashTransaction(
            transaction_id="c2", account_id="U1", type="Broker Interest Received",
            amount=Decimal("5"), currency="USD", fx_rate_to_base=Decimal("0.8"),
            description="USD IBKR MANAGED SECURITIES (SYEP) INTEREST FOR MAR-2026",
            event_datetime=datetime(2026, 3, 10, 12), settle_date=date(2026, 3, 10),
        ))
    with session_scope() as s:
        out = margin_interest_summary(s)
    assert out["avg_cash_base"] == pytest.approx(21000.0)
    assert out["cash_interest_base"] == pytest.approx(8.7671, abs=1e-3)
    assert out["syep_base"] == pytest.approx(4.0)
    assert out["received_total_base"] == pytest.approx(12.7671, abs=1e-3)
    # 8.7671 / 21,000 × 365 / 10 = 1.5238 %
    assert out["effective_cash_yield_pct"] == pytest.approx(1.5238, abs=1e-3)
    by = {r["currency"]: r for r in out["received_by_currency"]}
    assert by["USD"]["yield_pct"] == pytest.approx(2.0, abs=1e-3)
    assert by["USD"]["syep_native"] == pytest.approx(5.0)
    assert by["HKD"]["avg_cash_base"] == pytest.approx(5000.0)
    assert by["HKD"]["cash_interest_base"] == 0.0
    assert by["HKD"]["yield_pct"] == 0.0
    assert "BASE" not in by
    assert out["avg_debt_base"] == 0.0 and out["effective_apr_pct"] is None
