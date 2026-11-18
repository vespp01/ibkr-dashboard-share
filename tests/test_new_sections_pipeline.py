"""End-to-end parser→loader coverage for the newer Flex sections.

ChangeInNAV / OptionEAE / Transfers previously had
no roundtrip coverage: ChangeInNavDaily — the source of /performance/risk
(Sharpe/Sortino/MaxDD) and the attribution waterfall — was only ever seeded
via ORM in test_vs_benchmark.py, so a parser regression (e.g. IBKR renaming
`twr`) would null those metrics silently with a green suite. These tests load
the shared fixture twice and assert the persisted rows (values + idempotency).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from ibkr_backend.db import session_scope
from ibkr_backend.db.models import (
    ChangeInNavDaily,
    Instrument,
    OptionExerciseAssignment,
    Transfer,
)
from ibkr_backend.flex.parser import parse_flex_xml
from ibkr_backend.loaders.pipeline import load_statement


def _seed(xml_bytes: bytes):
    statements = parse_flex_xml(xml_bytes)
    with session_scope() as session:
        for stmt in statements:
            load_statement(session, stmt)


def test_change_in_nav_roundtrip_idempotent(sample_flex_xml):
    _seed(sample_flex_xml)
    _seed(sample_flex_xml)  # upsert_replace on (snapshot_date, account_id)

    with session_scope() as s:
        rows = s.execute(select(ChangeInNavDaily)).scalars().all()
        assert len(rows) == 1, "wrapper element must not be ingested as a row"
        r = rows[0]
        # The row's own toDate, not the statement's.
        assert r.snapshot_date == date(2025, 1, 31)
        assert r.account_id == "U1234567"
        assert r.currency == "EUR"
        assert r.starting_value == Decimal("10000")
        assert r.ending_value == Decimal("16200")
        # `twr` feeds Sharpe/Sortino/MaxDD — a silent attr rename nulls them.
        assert r.twr == Decimal("7.6453")
        assert r.deposits_withdrawals == Decimal("5000")
        assert r.mtm == Decimal("1135.65")
        assert r.realized == Decimal("74")
        assert r.dividends == Decimal("5.52")
        assert r.withholding_tax == Decimal("-0.83")
        assert r.change_in_dividend_accruals == Decimal("1.15")
        assert r.interest == Decimal("-12.50")
        assert r.commissions == Decimal("-1.84")
        assert r.fx_translation == Decimal("-1.15")
        assert r.raw_xml  # raw row retained for re-derivation


def test_option_eae_roundtrip_idempotent(sample_flex_xml):
    _seed(sample_flex_xml)
    _seed(sample_flex_xml)  # upsert_ignore on deterministic synthetic eae_id

    with session_scope() as s:
        rows = s.execute(select(OptionExerciseAssignment)).scalars().all()
        # OptionEAE is the container==row-tag case of _iter: the wrapper is
        # also yielded and must be filtered, rows must come through once each.
        assert len(rows) == 2, "container==row tag must neither drop nor duplicate rows"
        by_type = {r.transaction_type: r for r in rows}
        assert set(by_type) == {"Assignment", "Expiration"}

        a = by_type["Assignment"]
        # No IBKR transactionID in this section → synthetic id, stable across reruns.
        assert a.eae_id.startswith("syn_")
        assert a.account_id == "U1234567"
        assert a.conid == 700001
        assert a.symbol == "AAPL  250117P00180000"
        assert a.underlying_conid == 265598
        assert a.underlying_symbol == "AAPL"
        assert a.event_date == date(2025, 1, 17)
        assert a.quantity == Decimal("-1")
        assert a.multiplier == Decimal("100")
        # Parsed from IBKR's misspelled `commisionsAndTax` attribute.
        assert a.commission == Decimal("-0.25")
        assert a.realized_pnl == Decimal("-55")
        assert a.currency == "USD"
        assert a.raw_xml

        e = by_type["Expiration"]
        assert e.conid == 700002
        assert e.quantity == Decimal("1")
        assert e.realized_pnl == Decimal("-120")

        instruments = {
            row.conid: row
            for row in s.execute(
                select(Instrument).where(Instrument.conid.in_([700001, 700002]))
            ).scalars()
        }
        assert set(instruments) == {700001, 700002}

        assigned_put = instruments[700001]
        assert assigned_put.asset_class == "OPT"
        assert assigned_put.option_underlying == "AAPL"
        assert assigned_put.option_expiry == date(2025, 1, 17)
        assert assigned_put.option_right == "P"
        assert assigned_put.option_strike == Decimal("180")

        expired_call = instruments[700002]
        assert expired_call.option_underlying == "AAPL"
        assert expired_call.option_expiry == date(2025, 1, 17)
        assert expired_call.option_right == "C"
        assert expired_call.option_strike == Decimal("210")


def test_transfers_roundtrip_idempotent(sample_flex_xml):
    _seed(sample_flex_xml)
    _seed(sample_flex_xml)  # upsert_ignore on transfer_id

    with session_scope() as s:
        rows = s.execute(select(Transfer)).scalars().all()
        assert len(rows) == 2

        acats = next(r for r in rows if r.transfer_id == "TR3001")
        # IBKR-issued transactionID wins over the synthetic fallback.
        assert acats.account_id == "U1234567"
        assert acats.conid == 76792991
        assert acats.symbol == "TSLA"
        assert acats.direction == "IN"
        assert acats.transfer_type == "ACATS"
        assert acats.event_date == date(2025, 1, 12)
        assert acats.quantity == Decimal("5")
        assert acats.position_value == Decimal("1200")
        assert acats.cash_value == Decimal("0")
        assert acats.cost_basis == Decimal("950")
        assert acats.delivering_broker == "OTHR"
        assert acats.currency == "USD"
        assert acats.raw_xml

        # Row without transactionID → deterministic synthetic id (idempotent).
        cash = next(r for r in rows if r.transfer_id != "TR3001")
        assert cash.transfer_id.startswith("syn_")
        assert cash.transfer_type == "INTERNAL"
        assert cash.direction == "IN"
        assert cash.event_date == date(2025, 1, 20)
        assert cash.cash_value == Decimal("2500")
        assert cash.currency == "EUR"
        assert cash.conid is None


def test_new_sections_do_not_touch_external_cashflows(sample_flex_xml):
    """Transfers/ChangeInNAV must NOT feed nav_daily.external_cashflow — that
    column derives exclusively from CashTransactions (Deposits/Withdrawals).
    The fixture's only such flow is the 5000 EUR deposit.
    """
    from ibkr_backend.db.models import NavDaily

    _seed(sample_flex_xml)

    with session_scope() as s:
        navs = s.execute(select(NavDaily)).scalars().all()
        flows = {
            n.snapshot_date: n.external_cashflow
            for n in navs
            if n.external_cashflow is not None and n.external_cashflow != 0
        }
        assert flows == {date(2025, 1, 10): Decimal("5000.00")}
