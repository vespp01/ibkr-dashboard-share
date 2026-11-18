"""margin_summary cash rows carry a base value and % of NAV from Flex fx_rates."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ibkr_backend.analytics.margin import margin_summary
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import CashReportRow, FxRate, NavDaily

SNAP = date(2026, 11, 13)


def test_cash_rows_converted_to_base_with_nav_share():
    with session_scope() as s:
        s.add(NavDaily(snapshot_date=SNAP, account_id="U1", base_currency="CHF",
                       nav_total=Decimal("100000"), cash=Decimal("15000")))
        s.add(FxRate(rate_date=SNAP, from_ccy="USD", to_ccy="CHF", rate=Decimal("0.8")))
        for ccy, amount in (("CHF", "5000"), ("USD", "12500"), ("HKD", "-1000")):
            s.add(CashReportRow(snapshot_date=SNAP, account_id="U1", currency=ccy,
                                ending_cash=Decimal(amount)))
    with session_scope() as s:
        rows = {r["currency"]: r for r in margin_summary(s)["cash_by_currency"]}
    assert rows["CHF"]["ending_cash_base"] == 5000 and rows["CHF"]["pct_of_nav"] == 5.0
    assert rows["USD"]["ending_cash_base"] == 10000 and rows["USD"]["pct_of_nav"] == 10.0
    # No Flex HKD rate: no base value, never a guessed 1.0.
    assert rows["HKD"]["ending_cash_base"] is None and rows["HKD"]["pct_of_nav"] is None
    assert rows["HKD"]["is_debt"] is True
