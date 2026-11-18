"""Account hygiene: base-currency preservation/backfill + orphan pruning."""

from __future__ import annotations

from datetime import date

from ibkr_backend.db import session_scope
from ibkr_backend.db.models import Account, NavDaily
from ibkr_backend.flex.parser import AccountInfo, NavRow, ParsedStatement
from ibkr_backend.loaders.hygiene import (
    backfill_base_currency,
    prune_orphan_accounts,
)
from ibkr_backend.loaders.pipeline import load_statement


def _nav(account_id: str, d: date, ccy: str | None) -> NavRow:
    return NavRow(
        snapshot_date=d, account_id=account_id, base_currency=ccy,
        nav_total=None, cash=None, stock=None, options=None, bonds=None,
        funds=None, dividends_receivable=None, interest_receivable=None,
        accrued_dividends=None, realized_pnl=None, unrealized_pnl=None,
    )


def _stmt(account_id: str, base_currency=None, nav_rows=None) -> ParsedStatement:
    return ParsedStatement(
        account=AccountInfo(account_id=account_id, base_currency=base_currency),
        from_date=None, to_date=None, when_generated=None,
        nav_rows=nav_rows or [],
    )


def test_statement_with_currency_populates_account():
    with session_scope() as s:
        load_statement(s, _stmt("U5", base_currency="EUR"))
        assert s.get(Account, "U5").base_currency == "EUR"


def test_currencyless_pull_does_not_clobber_base_currency():
    # First a normal pull establishes CHF.
    with session_scope() as s:
        load_statement(s, _stmt("U1", base_currency="CHF"))
        assert s.get(Account, "U1").base_currency == "CHF"
    # A later "poor" pull (e.g. weekend LastBusinessDay) carries no currency.
    with session_scope() as s:
        load_statement(s, _stmt("U1", base_currency=None))
        assert s.get(Account, "U1").base_currency == "CHF"  # preserved, not NULL


def test_pipeline_backfills_base_currency_from_nav():
    # AccountInformation lacks a currency, but the NAV section carries CHF.
    stmt = _stmt("U2", base_currency=None, nav_rows=[_nav("U2", date(2025, 1, 31), "CHF")])
    with session_scope() as s:
        load_statement(s, stmt)
        assert s.get(Account, "U2").base_currency == "CHF"  # self-healed


def test_backfill_uses_latest_nav_currency():
    with session_scope() as s:
        s.add(Account(account_id="U3", base_currency=None))
        s.add(NavDaily(snapshot_date=date(2025, 1, 10), account_id="U3", base_currency="CHF"))
        s.add(NavDaily(snapshot_date=date(2025, 2, 10), account_id="U3", base_currency="USD"))
        s.flush()
        fixed = backfill_base_currency(s)
        assert fixed == [("U3", "USD")]  # most recent snapshot wins
        assert s.get(Account, "U3").base_currency == "USD"


def test_backfill_noop_without_nav():
    with session_scope() as s:
        s.add(Account(account_id="U4", base_currency=None))
        s.flush()
        assert backfill_base_currency(s) == []
        assert s.get(Account, "U4").base_currency is None


def test_backfill_skips_already_populated_accounts():
    with session_scope() as s:
        s.add(Account(account_id="U6", base_currency="GBP"))
        s.add(NavDaily(snapshot_date=date(2025, 3, 1), account_id="U6", base_currency="USD"))
        s.flush()
        assert backfill_base_currency(s) == []  # not NULL → untouched
        assert s.get(Account, "U6").base_currency == "GBP"


def test_prune_orphan_accounts():
    with session_scope() as s:
        s.add(Account(account_id="U_REAL", base_currency="CHF"))
        s.add(Account(account_id="U_ORPHAN", base_currency="USD"))
        s.add(NavDaily(snapshot_date=date(2025, 1, 31), account_id="U_REAL", base_currency="CHF"))
        s.flush()
        rep = prune_orphan_accounts(s, apply=False)
        assert rep["orphans"] == ["U_ORPHAN"]
        assert rep["kept"] == ["U_REAL"]
        assert rep["deleted"] == []
    with session_scope() as s:
        assert s.get(Account, "U_ORPHAN") is not None  # dry-run deleted nothing
        rep = prune_orphan_accounts(s, apply=True)
        assert rep["deleted"] == ["U_ORPHAN"]
    with session_scope() as s:
        assert s.get(Account, "U_ORPHAN") is None
        assert s.get(Account, "U_REAL") is not None
