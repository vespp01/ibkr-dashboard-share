"""Dividend country follows the matching withholding row (B32)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from ibkr_backend.analytics.dividends_calc import dividends_by_country
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import Account, CashTransaction, Instrument


def _cash(tid, ttype, amount, desc, country=None):
    return CashTransaction(
        transaction_id=tid, account_id="U1", conid=555, symbol="DEMOHK", type=ttype,
        amount=Decimal(amount), currency="HKD", fx_rate_to_base=Decimal("0.1"),
        event_datetime=datetime(2026, 7, 15), settle_date=date(2026, 7, 15),
        description=desc, country=country,
    )


def test_lu_withholding_has_gross_in_same_country():
    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.add(Instrument(conid=555, symbol="DEMOHK", asset_class="STK", issuer_country="HK"))
        s.flush()
        s.add(_cash("d", "Dividends", "1000", "DEMOHK(LU0000000000) CASH DIVIDEND HKD 0.8 PER SHARE (Ordinary Dividend)"))
        s.add(_cash("w", "Withholding Tax", "-150", "DEMOHK(LU0000000000) CASH DIVIDEND HKD 0.8 PER SHARE - LU TAX", country="LU"))
    with session_scope() as s:
        rows = {r["country"]: r for r in dividends_by_country(s)}
    assert set(rows) == {"LU"}
    assert rows["LU"]["gross"] == 100.0 and rows["LU"]["withholding"] == -15.0
    assert rows["LU"]["net"] == 85.0
