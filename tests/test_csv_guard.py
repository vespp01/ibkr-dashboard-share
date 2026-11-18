"""Twin guard and CSV-residue purge (P0.2).

The CSV (Plan B) loader is retired; these tests keep its leftovers from ever
coming back and cover the one-time purge:

* `test_no_csv_row_shares_a_key_with_a_flex_row` is the twin guard: after the
  purge no `csv_` cash row may share (settle_date, currency, amount, ticker)
  with a Flex row of ANY type.
* the purge matches twins with wider keys than the old exact dedup, deletes the
  CSV-only residue, refuses when Flex-only realized P&L does not reconcile
  with the FIFO summary, and is idempotent.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from ibkr_backend.db import session_scope
from ibkr_backend.db.models import (
    Account,
    CashReportRow,
    CashTransaction,
    ChangeInNavDaily,
    CorporateAction,
    FifoPerformanceUnderlying,
    Instrument,
    NavDaily,
    PositionEod,
    Trade,
    TransactionFee,
)
from ibkr_backend.loaders.dedup import (
    PurgeRefused,
    dedup_csv_imports,
    find_trade_twins,
    purge_csv_residue,
)

ACC = "U1"


def _count(s, model) -> int:
    return s.execute(select(func.count()).select_from(model)).scalar()


def _base(s):
    s.add(Account(account_id=ACC, base_currency="CHF"))
    s.flush()


def _inst(s, conid, symbol, asset_class="STK", currency="USD"):
    if s.get(Instrument, conid) is None:
        s.add(Instrument(conid=conid, symbol=symbol, asset_class=asset_class, currency=currency))
        s.flush()


def _trade(s, tid, symbol, conid, qty, price, dt, *, order=None, td=None, pnl="0", fx="0.8",
           ccy="USD", ac="STK"):
    _inst(s, conid, symbol, ac, ccy)
    q = Decimal(str(qty))
    s.add(Trade(
        trade_id=tid, account_id=ACC, conid=conid, symbol=symbol,
        trade_date=td or dt.date(), trade_datetime=dt,
        buy_sell="BUY" if q > 0 else "SELL", quantity=q, trade_price=Decimal(str(price)),
        proceeds=Decimal("0"), net_cash=Decimal("0"), currency=ccy,
        fifo_pnl_realized=Decimal(pnl),
        fx_rate_to_base=None if tid.startswith("csv_") else Decimal(fx),
        ib_order_id=order, asset_class=ac,
    ))


def _cash(s, tid, ttype, amount, desc, day=date(2025, 3, 3), ccy="USD"):
    s.add(CashTransaction(
        transaction_id=tid, account_id=ACC, type=ttype, amount=Decimal(str(amount)),
        currency=ccy, event_datetime=datetime.combine(day, datetime.min.time()),
        settle_date=day, description=desc,
        fx_rate_to_base=None if tid.startswith("csv_") else Decimal("0.8"),
    ))


def _fee(s, fid, symbol, amount, ccy, event, report, conid=None):
    s.add(TransactionFee(
        fee_id=fid, account_id=ACC, symbol=symbol, conid=conid, event_datetime=event,
        report_date=report, tax_amount=Decimal(str(amount)), currency=ccy,
        tax_description="UK Stamp Tax" if ccy else None,
    ))


def _seed_full():
    """A small world with every kind of residue plus the Flex truth."""
    with session_scope() as s:
        _base(s)
        # trades: one CSV order = three Flex fills; option in human vs OCC form;
        # a CSV date that is the Flex fill date - 1 (late-evening trade).
        _trade(s, "csv_t1", "AAA", 10, 100, 5.0, datetime(2025, 3, 3, 10, 0, 0), ccy="USD")
        for i, q in enumerate((60, 30, 10)):
            _trade(s, f"f1{i}", "AAA", 10, q, 5.0, datetime(2025, 3, 3, 10, 0, i),
                   order="O1", pnl="10" if i == 0 else "0")
        _trade(s, "csv_t2", "ZZZ 21NOV25 22 C", -7, 1, 1.5, datetime(2025, 3, 4, 9, 30, 0),
               ac="OPT")
        _trade(s, "f2", "ZZZ   251121C00022000", 77, 1, 1.5, datetime(2025, 3, 4, 9, 30, 0),
               order="O2", ac="OPT")
        _trade(s, "csv_t3", "BBB", 11, 5, 8.0, datetime(2025, 3, 5, 21, 56, 59))
        _trade(s, "f3", "BBB", 11, 5, 8.0, datetime(2025, 3, 5, 21, 56, 59), order="O3",
               td=date(2025, 3, 8))  # Flex trade_date is later than the execution day
        # cash: PIL typed as Dividends in the CSV, interest received typed as Paid
        _cash(s, "csv_c1", "Dividends", 5.0, "AAA(US1) Payment in Lieu of Dividend")
        _cash(s, "70001", "Payment In Lieu Of Dividends", 5.0, "AAA(US1) Payment in Lieu of Dividend")
        _cash(s, "csv_c2", "Broker Interest Paid", 1.25, "USD Credit Interest for Mar")
        _cash(s, "70002", "Broker Interest Received", 1.25, "USD Credit Interest for Mar")
        _cash(s, "csv_dw", "Deposits/Withdrawals", 100, "Internal Transfer In")  # no twin: kept
        # fees: twin re-dated to 31-Dec + subtotal row
        _fee(s, "csv_f1", "AAA", -1.5, "GBP", datetime(2025, 3, 3, 10, 0), date(2025, 12, 31))
        _fee(s, "fee_f1", "AAA", -1.5, "GBP", None, date(2025, 3, 3), conid=10)
        _fee(s, "csv_f2", None, -9.9, None, None, date(2025, 12, 31))
        # corporate actions: CSV row, Flex SUMMARY (syn_), Flex DETAIL (kept)
        for cid, raw in (
            ("csv_ca", None),
            ("syn_ca", '<CorporateAction levelOfDetail="SUMMARY" />'),
            ("90001", '<CorporateAction levelOfDetail="DETAIL" />'),
        ):
            s.add(CorporateAction(ca_id=cid, account_id=ACC, event_datetime=datetime(2025, 1, 1),
                                  raw_xml=raw))
        # CSV snapshot rows: no raw_xml, negative conid, BASE cash, Sunday NAV
        _inst(s, -99, "FX.JUNK", "CASH")
        s.add(PositionEod(snapshot_date=date(2025, 12, 31), account_id=ACC, conid=-99,
                          symbol="FX.JUNK", quantity=Decimal("1"), raw_xml=None))
        s.add(PositionEod(snapshot_date=date(2025, 12, 30), account_id=ACC, conid=10,
                          symbol="AAA", quantity=Decimal("1"), raw_xml="<OpenPosition/>",
                          fx_rate_to_base=Decimal("0.8")))
        s.add(CashReportRow(snapshot_date=date(2025, 12, 31), account_id=ACC, currency="BASE"))
        s.add(CashReportRow(snapshot_date=date(2025, 12, 31), account_id=ACC, currency="USD"))
        s.add(NavDaily(snapshot_date=date(2023, 12, 31), account_id=ACC, nav_total=Decimal("5")))
        s.add(NavDaily(snapshot_date=date(2025, 12, 30), account_id=ACC, nav_total=Decimal("5")))
        # FIFO summary equal to the Flex-only trades (10 * 0.8 = 8) for conid 10
        s.add(FifoPerformanceUnderlying(
            snapshot_date=date(2025, 3, 3), account_id=ACC, conid=10, symbol="AAA",
            total_realized_pnl=Decimal("8")))


def test_purge_dry_run_reports_and_writes_nothing():
    _seed_full()
    with session_scope() as s:
        report = purge_csv_residue(s, apply=False)
    assert report["applied"] is False
    assert report["counts"]["trades"] == 3
    assert report["counts"]["cash_transactions"] == 2      # D/W row has no twin
    assert report["counts"]["transaction_fees"] == 2       # twin + subtotal
    assert report["counts"]["corporate_actions"] == 2      # csv_ + SUMMARY
    assert report["counts"]["positions_eod"] == 1
    assert report["counts"]["cash_report"] == 1
    assert report["counts"]["nav_daily"] == 1
    assert report["counts"]["instruments"] == 2          # -7 (CSV option) and -99
    assert report["kept"]["cash_csv_without_twin"] == 1
    with session_scope() as s:
        assert _count(s, Trade) == 8


def test_purge_apply_keeps_flex_truth_and_is_idempotent():
    _seed_full()
    with session_scope() as s:
        purge_csv_residue(s, apply=True)
    with session_scope() as s:
        assert {t for t in s.execute(select(Trade.trade_id)).scalars()} == {
            "f10", "f11", "f12", "f2", "f3"}
        assert {c for c in s.execute(select(CashTransaction.transaction_id)).scalars()} == {
            "70001", "70002", "csv_dw"}
        assert {f for f in s.execute(select(TransactionFee.fee_id)).scalars()} == {"fee_f1"}
        assert {c for c in s.execute(select(CorporateAction.ca_id)).scalars()} == {"90001"}
        assert _count(s, PositionEod) == 1
        assert [c.currency for c in s.execute(select(CashReportRow)).scalars()] == ["USD"]
        assert [n.snapshot_date for n in s.execute(select(NavDaily)).scalars()] == [date(2025, 12, 30)]
        assert s.get(Instrument, -99) is None and s.get(Instrument, -7) is None
        assert s.get(Instrument, 10) is not None
    with session_scope() as s:
        again = purge_csv_residue(s, apply=True)
    assert sum(again["counts"].values()) == 0


def test_no_csv_row_shares_a_key_with_a_flex_row():
    """The twin guard: no `csv_` cash row twins a Flex row."""
    _seed_full()
    with session_scope() as s:
        purge_csv_residue(s, apply=True)
    tick = re.compile(r"^\s*([A-Za-z0-9.]+)\s*\(")

    def key(r):
        m = tick.match(r.description or "")
        return (r.settle_date, r.currency, round(float(r.amount), 4), m.group(1).upper() if m else None)

    with session_scope() as s:
        rows = s.execute(select(CashTransaction)).scalars().all()
        flex = {key(r) for r in rows if not r.transaction_id.startswith("csv_")}
        twins = [r.transaction_id for r in rows
                 if r.transaction_id.startswith("csv_") and key(r) in flex]
    assert twins == []


def test_trade_without_twin_is_kept_and_listed_for_review():
    with session_scope() as s:
        _base(s)
        _trade(s, "csv_orphan", "CCC", 12, 3, 9.0, datetime(2025, 3, 3, 10, 0, 0))
        _trade(s, "f9", "DDD", 13, 3, 9.0, datetime(2025, 3, 3, 10, 0, 0), order="O9")
        s.add(FifoPerformanceUnderlying(
            snapshot_date=date(2025, 3, 3), account_id=ACC, conid=13, total_realized_pnl=Decimal("0")))
    with session_scope() as s:
        twins, unmatched = find_trade_twins(s)
        report = purge_csv_residue(s, apply=True)
    assert twins == {} and unmatched == ["csv_orphan"]
    assert report["kept"]["trades_csv_without_twin"] == 1
    assert any("CCC" in line for line in report["review"])
    with session_scope() as s:
        assert _count(s, Trade) == 2


def test_purge_refuses_when_flex_realized_does_not_match_fifo_summary():
    _seed_full()
    with session_scope() as s:
        row = s.execute(select(FifoPerformanceUnderlying)).scalar_one()
        row.total_realized_pnl = Decimal("500")   # Flex-only trades say 8
    with session_scope() as s:
        with pytest.raises(PurgeRefused):
            purge_csv_residue(s, apply=True)
    with session_scope() as s:
        assert _count(s, Trade) == 8                 # nothing deleted
        dry = purge_csv_residue(s, apply=False)       # dry-run still reports
        assert dry["checks"]["realized_reconciliation"]["ok"] is False


def test_instrument_still_referenced_is_not_deleted():
    _seed_full()
    with session_scope() as s:
        _inst(s, -50, "KEEP.ME", "CASH")
        s.add(CashTransaction(
            transaction_id="70009", account_id=ACC, type="Other Fees", amount=Decimal("-1"),
            currency="USD", event_datetime=datetime(2025, 1, 1), settle_date=date(2025, 1, 1),
            conid=-50, fx_rate_to_base=Decimal("1")))
    with session_scope() as s:
        purge_csv_residue(s, apply=True)
    with session_scope() as s:
        assert s.get(Instrument, -50) is not None
        assert s.get(Instrument, -99) is None


def test_nav_less_weekend_row_is_left_for_the_performance_cleanup():
    _seed_full()
    with session_scope() as s:
        s.add(NavDaily(snapshot_date=date(2023, 11, 18), account_id=ACC, nav_total=None))
    with session_scope() as s:
        purge_csv_residue(s, apply=True)
    with session_scope() as s:
        dates = {n.snapshot_date for n in s.execute(select(NavDaily)).scalars()}
    assert date(2023, 11, 18) in dates and date(2023, 12, 31) not in dates


def test_weekend_nav_with_flex_change_in_nav_row_is_kept():
    with session_scope() as s:
        _base(s)
        s.add(NavDaily(snapshot_date=date(2025, 3, 8), account_id=ACC, nav_total=Decimal("9")))
        s.add(ChangeInNavDaily(snapshot_date=date(2025, 3, 8), account_id=ACC, currency="CHF"))
    with session_scope() as s:
        report = purge_csv_residue(s, apply=True)
    assert report["counts"]["nav_daily"] == 0


def test_dedup_csv_imports_uses_the_wider_keys():
    _seed_full()
    with session_scope() as s:
        report = dedup_csv_imports(s, apply=True)
    assert report["trades"]["removable_csv"] == 3
    assert report["cash_transactions"]["removable_csv"] == 2
    assert report["transaction_fees"]["removable_csv"] == 1
    with session_scope() as s:
        # subtotal fee row is residue, not a twin: left for the purge
        assert "csv_f2" in set(s.execute(select(TransactionFee.fee_id)).scalars())
        assert _count(s, CorporateAction) == 3
