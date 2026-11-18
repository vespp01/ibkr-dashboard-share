"""Commissions in base currency: FX on fees, stamp duty once, commission ccy (B06, B30)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from ibkr_backend.analytics.commissions import (
    commissions_breakdown,
    commissions_summary,
    commissions_timeseries,
    transaction_fees_summary,
)
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import Account, FxRate, Instrument, Trade, TransactionFee

D = date(2026, 3, 10)


def _seed(s):
    s.add(Account(account_id="U1", base_currency="CHF"))
    s.add(Instrument(conid=1, symbol="XYZ", asset_class="STK"))
    s.add(Instrument(conid=2, symbol="ENGI", asset_class="STK"))
    s.add(Instrument(conid=3, symbol="HKD.CHF", asset_class="CASH"))
    s.flush()
    for ccy, rate in (("GBP", "1.10"), ("EUR", "0.95"), ("HKD", "0.10")):
        s.add(FxRate(rate_date=date(2026, 3, 1), from_ccy=ccy, to_ccy="CHF", rate=Decimal(rate)))

    def trade(tid, conid, ccy, fx, comm, comm_ccy, taxes=None, ac="STK"):
        s.add(Trade(
            trade_id=tid, account_id="U1", conid=conid, symbol="X", trade_date=D,
            trade_datetime=datetime(2026, 3, 10, 10), quantity=Decimal(1),
            trade_price=Decimal(1), proceeds=Decimal(0), net_cash=Decimal(0),
            ib_commission=Decimal(comm), ib_commission_ccy=comm_ccy,
            taxes=None if taxes is None else Decimal(taxes), currency=ccy,
            fx_rate_to_base=Decimal(fx), asset_class=ac,
        ))

    # GBP stock with stamp duty on the trade and the same duty as a fee row.
    trade("t1", 1, "GBP", "1.10", "-10", "GBP", taxes="-5")
    # CASH trade in HKD whose commission is charged in CHF: must NOT use the HKD rate.
    trade("t2", 3, "HKD", "0.10", "-2", "CHF", ac="CASH")
    s.add(TransactionFee(fee_id="fee_a", account_id="U1", conid=1, report_date=D,
                         tax_amount=Decimal("-5"), currency="GBP"))
    # EUR financial-transaction tax: only exists as a fee row.
    s.add(TransactionFee(fee_id="fee_b", account_id="U1", conid=2, report_date=D,
                         tax_amount=Decimal("-10"), currency="EUR"))
    # Legacy subtotal without currency: cannot be converted, ignored.
    s.add(TransactionFee(fee_id="csv_c", account_id="U1", conid=None, report_date=D,
                         tax_amount=Decimal("-999"), currency=None))


def test_commissions_converted_with_their_own_currency():
    with session_scope() as s:
        _seed(s)
    with session_scope() as s:
        out = commissions_summary(s)
        assert out["total_base"] == -10 * 1.10 + -2.0  # GBP via trade fx, CHF as is
        assert out["trade_count"] == 2
        ts = commissions_timeseries(s, bucket="month")
        assert ts == [{"bucket": "2026-03", "commission": out["total_base"], "trades": 2}]


def test_stamp_duty_counted_once_and_fees_in_base():
    with session_scope() as s:
        _seed(s)
    with session_scope() as s:
        b = commissions_breakdown(s)
        types = {r["type"]: r["amount_base"] for r in b["by_type"]}
        assert types["Transaction Tax"] == -5 * 1.10          # trades.taxes (GBP)
        assert types["Regulatory Fees"] == -10 * 0.95         # EUR FTT only; GBP twin and NULL-ccy row dropped
        assert b["total_base"] == sum(types.values())
        assert transaction_fees_summary(s)["total_base"] == -10 * 0.95
