"""PADI (income_projection): Po rows only, one event per pay date, FX by the
accrual currency, 365-day window, specials out (B08)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ibkr_backend.analytics.dividends_calc import income_projection
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import (
    Account,
    DividendAccrual,
    FxRate,
    Instrument,
    NavDaily,
    PositionEod,
)

SNAP = date(2026, 10, 5)


def _acc(s, conid, ex, pay, rate, ccy, code):
    s.add(DividendAccrual(
        account_id="U1", conid=conid, symbol=f"S{conid}", ex_date=ex, pay_date=pay,
        quantity=Decimal(10), gross_rate=Decimal(str(rate)),
        gross_amount=Decimal(str(rate * 10 * (1 if code == "Po" else -1))),
        currency=ccy, code=code,
    ))


def _seed(s):
    s.add(Account(account_id="U1", base_currency="CHF"))
    for conid in (1, 2):
        s.add(Instrument(conid=conid, symbol=f"S{conid}", asset_class="STK"))
    s.flush()
    for ccy, r in (("GBP", "1.00"), ("ZAR", "0.05"), ("EUR", "0.90")):
        s.add(FxRate(rate_date=SNAP, from_ccy=ccy, to_ccy="CHF", rate=Decimal(r)))
    s.add(NavDaily(snapshot_date=SNAP, account_id="U1", base_currency="CHF", nav_total=Decimal(10000)))
    for conid, ccy in ((1, "GBP"), (2, "EUR")):
        s.add(PositionEod(
            snapshot_date=SNAP, account_id="U1", conid=conid, symbol=f"S{conid}",
            quantity=Decimal(10), position_value=Decimal(1000), currency=ccy,
            fx_rate_to_base=Decimal("1.0") if ccy == "GBP" else Decimal("0.9"), asset_class="STK",
        ))

    # conid 1: quarterly 0.60 GBP. Each Po is reversed by a Re whose ex-date and
    # rate differ (the old code summed those). The 2026-03 payment was booked
    # first in ZAR then re-elected to GBP: the latest Po (GBP) is the event.
    quarters = [
        (date(2025, 6, 26), date(2025, 8, 1)),
        (date(2025, 10, 1), date(2025, 11, 7)),     # ex-date just outside the window
        (date(2025, 12, 29), date(2026, 2, 4)),
        (date(2026, 3, 26), date(2026, 5, 7)),
        (date(2026, 7, 9), date(2026, 8, 14)),
    ]
    for ex, pay in quarters:
        if pay == date(2026, 5, 7):
            _acc(s, 1, ex, pay, 12.0, "ZAR", "Po")
            _acc(s, 1, ex, pay, 0.60, "GBP", "Po")
            _acc(s, 1, ex, pay, 12.0, "ZAR", "Re")
        else:
            _acc(s, 1, ex, pay, 0.60, "GBP", "Po")
            _acc(s, 1, date(ex.year, ex.month, ex.day), pay, 0.61, "GBP", "Re")
    # Open accrual: ZAR election for the next payment (13 ZAR x 0.05 = 0.65 CHF).
    _acc(s, 1, date(2026, 10, 1), date(2026, 11, 6), 13.0, "ZAR", "Po")

    # conid 2: 1.00 EUR quarterly plus a special 8.00 EUR (and a re-booking
    # 3 days after a regular event with the same rate).
    for ex, pay, rate in (
        (date(2025, 11, 3), date(2025, 11, 13), 1.0),
        (date(2026, 2, 3), date(2026, 2, 13), 1.0),
        (date(2026, 5, 4), date(2026, 5, 14), 1.0),
        (date(2026, 6, 1), date(2026, 6, 10), 8.0),        # special
        (date(2026, 8, 3), date(2026, 8, 13), 1.0),
    ):
        _acc(s, 2, ex, pay, rate, "EUR", "Po")
        _acc(s, 2, ex, pay, rate, "EUR", "Re")


def test_padi_po_only_one_event_per_payment_fx_by_accrual_currency():
    with session_scope() as s:
        _seed(s)
    with session_scope() as s:
        out = income_projection(s)
    rows = {r["symbol"]: r for r in out["by_symbol"]}
    # conid 1: ex-dates inside 365 d are 2025-12-29, 2026-03-26, 2026-07-09 (GBP 0.60
    # each = 1.80 per share) plus the 2026-10-01 ZAR election (13 x 0.05 = 0.65).
    assert rows["S1"]["annual_income_base"] == pytest.approx(10 * (1.80 + 0.65))
    # conid 2: 4 regular EUR 1.00 events x 0.9; the 8.00 special is excluded.
    assert rows["S2"]["annual_income_base"] == pytest.approx(10 * 4 * 0.9)
    assert out["padi_base"] == pytest.approx(10 * (1.80 + 0.65) + 36.0)
    assert out["positions_covered"] == 2
    assert len(out["by_symbol"]) == 2


def test_padi_by_symbol_is_not_truncated():
    with session_scope() as s:
        _seed(s)
        for conid in range(100, 120):
            s.add(Instrument(conid=conid, symbol=f"X{conid}", asset_class="STK"))
            s.flush()
            s.add(PositionEod(
                snapshot_date=SNAP, account_id="U1", conid=conid, symbol=f"X{conid}",
                quantity=Decimal(1), position_value=Decimal(10), currency="GBP",
                fx_rate_to_base=Decimal(1), asset_class="STK",
            ))
    with session_scope() as s:
        assert len(income_projection(s)["by_symbol"]) == 22
