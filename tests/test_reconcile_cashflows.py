"""Test reconcile_external_cashflows — verifies that spurious residue entries are
zeroed out and real cashflows are correctly summed across the full history."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from ibkr_backend.db import session_scope
from ibkr_backend.db.models import Account, CashTransaction, NavDaily
from ibkr_backend.loaders.pipeline import reconcile_external_cashflows


def _seed_account(session, account_id: str) -> None:
    session.merge(Account(account_id=account_id, base_currency="USD"))


def _seed_nav(session, snapshot_date: date, account_id: str, external_cashflow) -> None:
    session.merge(
        NavDaily(
            snapshot_date=snapshot_date,
            account_id=account_id,
            external_cashflow=external_cashflow,
        )
    )


def _seed_cash_tx(
    session,
    transaction_id: str,
    account_id: str,
    tx_type: str,
    amount: Decimal,
    settle_date: date,
    fx_rate: Decimal | None = Decimal("1"),  # real IBKR rows always carry a rate (1.0 for base ccy)
) -> None:
    session.add(
        CashTransaction(
            transaction_id=transaction_id,
            account_id=account_id,
            type=tx_type,
            amount=amount,
            currency="USD",
            fx_rate_to_base=fx_rate,
            event_datetime=datetime(settle_date.year, settle_date.month, settle_date.day, 12, 0),
            settle_date=settle_date,
        )
    )


def test_reconcile_zeroes_spurious_residue():
    """A nav_daily row with a nonzero external_cashflow but NO matching
    cash_transactions should be set to 0.0 after reconciliation."""
    spurious_date = date(2024, 12, 31)
    real_date = date(2024, 11, 15)
    account = "U9999"

    with session_scope() as s:
        _seed_account(s, account)
        # nav_daily row with spurious aggregated cashflow (the bug)
        _seed_nav(s, spurious_date, account, Decimal("54464.00"))
        # nav_daily row for the real cashflow date
        _seed_nav(s, real_date, account, Decimal("0.00"))
        # Only a real deposit on real_date — nothing on spurious_date
        _seed_cash_tx(
            s, "TX001", account, "Deposits/Withdrawals",
            Decimal("10000.00"), real_date,
        )

    with session_scope() as s:
        changed = reconcile_external_cashflows(s)

    assert changed == 2  # spurious_date goes 54464 → 0; real_date goes 0 → 10000

    with session_scope() as s:
        nav_spurious = s.get(NavDaily, (spurious_date, account))
        nav_real = s.get(NavDaily, (real_date, account))

    assert nav_spurious is not None
    assert nav_spurious.external_cashflow == Decimal("0")

    assert nav_real is not None
    assert nav_real.external_cashflow == Decimal("10000.00")


def test_reconcile_sums_multiple_transactions_on_same_date():
    """Two Deposits/Withdrawals on the same date must be summed together."""
    d = date(2024, 6, 1)
    account = "U8888"

    with session_scope() as s:
        _seed_account(s, account)
        _seed_nav(s, d, account, Decimal("0.00"))
        _seed_cash_tx(s, "TX010", account, "Deposits/Withdrawals", Decimal("5000.00"), d)
        _seed_cash_tx(s, "TX011", account, "Deposits/Withdrawals", Decimal("3000.00"), d)

    with session_scope() as s:
        changed = reconcile_external_cashflows(s)

    assert changed == 1

    with session_scope() as s:
        nav = s.get(NavDaily, (d, account))

    assert nav is not None
    assert nav.external_cashflow == Decimal("8000.00")


def test_reconcile_applies_fx_rate():
    """Cashflows in foreign currency must be converted via fx_rate_to_base."""
    d = date(2024, 9, 10)
    account = "U7777"

    with session_scope() as s:
        _seed_account(s, account)
        _seed_nav(s, d, account, Decimal("0.00"))
        # 1000 EUR at 1.10 USD/EUR → 1100 base
        _seed_cash_tx(
            s, "TX020", account, "Deposits/Withdrawals",
            Decimal("1000.00"), d, fx_rate=Decimal("1.10"),
        )

    with session_scope() as s:
        reconcile_external_cashflows(s)

    with session_scope() as s:
        nav = s.get(NavDaily, (d, account))

    assert nav is not None
    assert nav.external_cashflow == Decimal("1100.00")


def test_reconcile_ignores_non_cashflow_types():
    """Only Deposits/Withdrawals count; dividends etc. must be ignored."""
    d = date(2024, 3, 5)
    account = "U6666"

    with session_scope() as s:
        _seed_account(s, account)
        _seed_nav(s, d, account, Decimal("999.00"))
        # dividend — should NOT count
        _seed_cash_tx(s, "TX030", account, "Dividends", Decimal("200.00"), d)

    with session_scope() as s:
        changed = reconcile_external_cashflows(s)

    # nav had 999, no matching tx → should become 0
    assert changed == 1

    with session_scope() as s:
        nav = s.get(NavDaily, (d, account))

    assert nav.external_cashflow == Decimal("0")


def test_reconcile_ignores_dead_internal_transfers_type():
    """`Internal Transfers` was a dead cash type (0 rows ever; in-kind transfers
    live in ChangeInNAV `assetTransfers`) and no longer counts."""
    d = date(2024, 4, 20)
    account = "U5555"

    with session_scope() as s:
        _seed_account(s, account)
        _seed_nav(s, d, account, Decimal("0.00"))
        _seed_cash_tx(s, "TX040", account, "Internal Transfers", Decimal("2500.00"), d)

    with session_scope() as s:
        reconcile_external_cashflows(s)

    with session_scope() as s:
        nav = s.get(NavDaily, (d, account))

    assert nav.external_cashflow == Decimal("0")


def test_reconcile_returns_zero_when_nothing_changes():
    """If all nav_daily rows already have correct values, changed count is 0."""
    d = date(2024, 7, 4)
    account = "U4444"

    with session_scope() as s:
        _seed_account(s, account)
        _seed_nav(s, d, account, Decimal("3000.00"))
        _seed_cash_tx(s, "TX050", account, "Deposits/Withdrawals", Decimal("3000.00"), d)

    with session_scope() as s:
        changed = reconcile_external_cashflows(s)

    assert changed == 0
