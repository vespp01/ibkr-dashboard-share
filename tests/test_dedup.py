"""CSV-import dedup — removes csv_* rows that have a Flex twin, keeps csv
orphans and genuine repeated Flex fills. Real SQLite per test (no DB mocking)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select

from ibkr_backend.db import session_scope
from ibkr_backend.db.models import (
    Account,
    CashTransaction,
    Instrument,
    Trade,
)
from ibkr_backend.loaders.dedup import dedup_csv_imports

DAY = date(2026, 5, 5)


def _ensure(session, model, pk, obj):
    if session.get(model, pk) is None:
        session.add(obj)
        session.flush()


def _trade(session, *, tid, symbol="AAPL", qty="10", price="100", buy_sell="BUY"):
    conid = abs(hash(symbol)) % 100000
    _ensure(session, Account, "U1", Account(account_id="U1", base_currency="CHF"))
    _ensure(session, Instrument, conid, Instrument(conid=conid, symbol=symbol))
    session.add(
        Trade(
            trade_id=tid, account_id="U1", conid=conid, symbol=symbol,
            trade_date=DAY, trade_datetime=datetime(2026, 5, 5, 15, 0),
            quantity=Decimal(qty), trade_price=Decimal(price),
            proceeds=Decimal("0"), net_cash=Decimal("0"),
            buy_sell=buy_sell, currency="USD",
        )
    )


def _cash(session, *, tid, ttype="Dividends", amount="5.00"):
    _ensure(session, Account, "U1", Account(account_id="U1", base_currency="CHF"))
    session.add(
        CashTransaction(
            transaction_id=tid, account_id="U1", type=ttype,
            amount=Decimal(amount), currency="USD",
            event_datetime=datetime(2026, 5, 5, 10, 0), settle_date=DAY,
        )
    )


def test_dryrun_reports_without_deleting():
    with session_scope() as s:
        _trade(s, tid="csv_dup1")           # csv with twin
        _trade(s, tid="7001")               # its Flex twin (same key)
        _trade(s, tid="csv_orphan", symbol="MSFT")  # csv, no twin
        _cash(s, tid="csv_div1")            # csv with twin
        _cash(s, tid="9001")                # its Flex twin

    with session_scope() as s:
        report = dedup_csv_imports(s, apply=False)
    assert report["trades"]["removable_csv"] == 1
    assert report["trades"]["orphan_csv_kept"] == 1
    assert report["cash_transactions"]["removable_csv"] == 1
    # nothing deleted on a dry-run
    with session_scope() as s:
        assert s.execute(select(func.count()).select_from(Trade)).scalar() == 3
        assert s.execute(select(func.count()).select_from(CashTransaction)).scalar() == 2


def test_apply_removes_only_csv_with_flex_twin():
    with session_scope() as s:
        _trade(s, tid="csv_dup1")
        _trade(s, tid="7001")
        _trade(s, tid="csv_orphan", symbol="MSFT")
        # genuine repeated Flex fills (two non-csv, same key) — must survive
        _trade(s, tid="7002", symbol="TSLA")
        _trade(s, tid="7003", symbol="TSLA")

    with session_scope() as s:
        dedup_csv_imports(s, apply=True)

    with session_scope() as s:
        ids = set(s.execute(select(Trade.trade_id)).scalars().all())
    assert "csv_dup1" not in ids          # removed
    assert "7001" in ids                  # Flex twin kept
    assert "csv_orphan" in ids            # orphan kept
    assert {"7002", "7003"} <= ids        # genuine duplicates kept


def test_idempotent_second_apply_is_noop():
    with session_scope() as s:
        _trade(s, tid="csv_dup1")
        _trade(s, tid="7001")
    with session_scope() as s:
        dedup_csv_imports(s, apply=True)
    with session_scope() as s:
        report = dedup_csv_imports(s, apply=True)
    assert report["trades"]["removable_csv"] == 0
