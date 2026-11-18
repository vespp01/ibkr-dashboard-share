"""Ingest hygiene: B34-B38, B59 and the NULL-fx guard (B04 / B58).

Real SQLite per test (see conftest); Flex XML is synthetic but uses the
attribute names IBKR sends.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from ibkr_backend.analytics._common import _to_base, null_fx_counts
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import (
    Account,
    CashReportRow,
    CashTransaction,
    CorporateAction,
    FlexRun,
    Instrument,
    InterestAccrual,
    NavDaily,
    PositionEod,
    TransactionFee,
)
from ibkr_backend.flex.parser import parse_flex_xml
from ibkr_backend.loaders import pipeline
from ibkr_backend.loaders.pipeline import (
    fee_id_for,
    is_truncated_statement,
    load_statement,
    run_full_pipeline,
)


def _stmt_xml(body: str, *, to_date: str = "20250131") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="q" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U1234567" fromDate="20250101" toDate="{to_date}"
                   period="LastMonth" whenGenerated="20250201;060000">
      {body}
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>""".encode()


def _load(xml: bytes) -> list:
    out = []
    with session_scope() as s:
        for st in parse_flex_xml(xml):
            out.extend(load_statement(s, st))
    return out


# --------------------------------------------------------- B59 corporate actions
def test_corporate_actions_keep_detail_rows_only():
    xml = _stmt_xml("""
      <CorporateActions>
        <CorporateAction accountId="U1234567" conid="1" symbol="AAA" type="RS"
          dateTime="20250115;202500" levelOfDetail="SUMMARY" quantity="1" actionDescription="x" />
        <CorporateAction accountId="U1234567" conid="1" symbol="AAA" type="RS"
          dateTime="20250115;202500" levelOfDetail="DETAIL" transactionID="777"
          quantity="1" actionDescription="x" />
        <CorporateAction accountId="U1234567" conid="2" symbol="BBB" type="DI"
          dateTime="20250116;202500" transactionID="778" quantity="2" actionDescription="y" />
      </CorporateActions>""")
    _load(xml)
    _load(xml)  # a re-pull must not add SUMMARY rows
    with session_scope() as s:
        ids = sorted(s.execute(select(CorporateAction.ca_id)).scalars())
    assert ids == ["777", "778"]


# ----------------------------------------------------------- B34 interest accruals
def test_interest_accrued_is_parsed_keyed_by_day_and_filled_on_repull():
    row = (
        '<InterestAccrualsCurrency accountId="U1234567" currency="USD" '
        'fromDate="20250131" toDate="20250131" startingAccrualBalance="-10" '
        'interestAccrued="-1.5" accrualReversal="0" fxTranslation="0" '
        'endingAccrualBalance="-11.5" />'
    )
    old = (  # a row stored by the old parser: only the balance
        '<InterestAccrualsCurrency accountId="U1234567" currency="USD" '
        'toDate="20250131" endingAccrualBalance="-11.5" />'
    )
    _load(_stmt_xml(f"<InterestAccruals>{old}</InterestAccruals>"))
    with session_scope() as s:
        r = s.execute(select(InterestAccrual)).scalar_one()
        assert r.interest is None and r.ending_balance == Decimal("-11.5")

    _load(_stmt_xml(f"<InterestAccruals>{row}</InterestAccruals>"))
    _load(_stmt_xml(f"<InterestAccruals>{row}</InterestAccruals>"))
    with session_scope() as s:
        rows = s.execute(select(InterestAccrual)).scalars().all()
        assert len(rows) == 1
        assert rows[0].interest == Decimal("-1.5")
        assert rows[0].interest_type is None
        assert "accrualReversal" in rows[0].raw_xml


# ------------------------------------------------------------- B35 transaction fees
_FEE = (
    '<TransactionTax accountId="U1234567" currency="GBP" symbol="{sym}" conid="{conid}" '
    'tradeId="{tid}" dateTime="20250115;103000" reportDate="20250115" quantity="{qty}" '
    'taxDescription="UK Stamp Tax" taxAmount="-1.5" />'
)


def _fees(*rows: str) -> bytes:
    return _stmt_xml(f"<TransactionTaxes>{''.join(rows)}</TransactionTaxes>")


def test_fee_trade_id_and_datetime_are_captured():
    _load(_fees(_FEE.format(sym="AAA", conid=1, tid="T1", qty=10)))
    with session_scope() as s:
        f = s.execute(select(TransactionFee)).scalar_one()
        assert f.trade_id == "T1"
        assert f.event_datetime == datetime(2025, 1, 15, 10, 30)
        assert f.raw_xml and "tradeId" in f.raw_xml


def test_equal_amount_fees_on_two_stocks_do_not_collide():
    _load(_fees(
        _FEE.format(sym="AAA", conid=1, tid="T1", qty=10),
        _FEE.format(sym="BBB", conid=2, tid="T2", qty=10),
    ))
    with session_scope() as s:
        assert s.execute(select(func.count()).select_from(TransactionFee)).scalar() == 2


def test_identical_fills_keep_distinct_ids_and_reload_is_idempotent():
    xml = _fees(
        _FEE.format(sym="AAA", conid=1, tid="T1", qty=10),
        _FEE.format(sym="AAA", conid=1, tid="T2", qty=10),
    )
    _load(xml)
    _load(xml)
    with session_scope() as s:
        assert s.execute(select(func.count()).select_from(TransactionFee)).scalar() == 2


def test_fee_id_does_not_depend_on_trade_id():
    """A pull that starts filling tradeId must not duplicate stored rows."""
    _load(_fees(_FEE.format(sym="AAA", conid=1, tid="", qty=10)))
    _load(_fees(_FEE.format(sym="AAA", conid=1, tid="T9", qty=10)))
    with session_scope() as s:
        f = s.execute(select(TransactionFee)).scalar_one()
        assert f.trade_id == "T9"  # completed in place


def test_rekey_fee_ids_moves_old_rows_once():
    from ibkr_backend.loaders.dedup import rekey_fee_ids

    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.add(TransactionFee(
            fee_id="fee_oldformula", account_id="U1", symbol="AAA", conid=1,
            report_date=date(2025, 1, 15), quantity=Decimal("10"),
            tax_amount=Decimal("-1.5"), tax_description="UK Stamp Tax", currency="GBP",
        ))
        s.add(TransactionFee(
            fee_id="csv_keepme", account_id="U1", symbol="AAA",
            report_date=date(2025, 12, 31), tax_amount=Decimal("-1.5"), currency="GBP",
        ))
    with session_scope() as s:
        dry = rekey_fee_ids(s, apply=False)
    assert dry["rekeyed"] == 1
    with session_scope() as s:
        assert s.get(TransactionFee, "fee_oldformula") is not None  # dry-run wrote nothing
        rekey_fee_ids(s, apply=True)
    with session_scope() as s:
        ids = set(s.execute(select(TransactionFee.fee_id)).scalars())
        row = s.execute(select(TransactionFee).where(TransactionFee.symbol == "AAA",
                                                     TransactionFee.fee_id != "csv_keepme")).scalar_one()
        assert ids == {row.fee_id, "csv_keepme"}
        assert row.fee_id == fee_id_for(row)
        again = rekey_fee_ids(s, apply=True)
    assert again["rekeyed"] == 0


def test_rekey_fee_ids_is_idempotent_for_equal_fills():
    """Two identical fills on one day: ids 0 and 1, and re-runs change nothing."""
    from ibkr_backend.loaders.dedup import rekey_fee_ids

    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        for old in ("fee_old_a", "fee_old_b"):
            s.add(TransactionFee(
                fee_id=old, account_id="U1", symbol="BBB", conid=2,
                report_date=date(2025, 3, 3), quantity=Decimal("5"),
                tax_amount=Decimal("-0.4131"), tax_description="FTT", currency="EUR",
            ))
    with session_scope() as s:
        first = rekey_fee_ids(s, apply=True)
    assert first["rekeyed"] == 2
    with session_scope() as s:
        rows = list(s.execute(select(TransactionFee).where(TransactionFee.symbol == "BBB")).scalars())
        ids = {r.fee_id for r in rows}
        assert ids == {fee_id_for(rows[0]), fee_id_for(rows[0], 1)}
    for _ in range(2):
        with session_scope() as s:
            again = rekey_fee_ids(s, apply=True)
        assert again["rekeyed"] == 0 and again["clashes_skipped"] == 0
    with session_scope() as s:
        assert {r.fee_id for r in s.execute(select(TransactionFee).where(TransactionFee.symbol == "BBB")).scalars()} == ids


def test_interest_accrual_index_created_after_removing_true_duplicates():
    """An old volume with duplicate (account, day, currency) rows still gets the
    unique index (keeping the newest row), so accrual upserts keep working."""
    from sqlalchemy import inspect, text

    from ibkr_backend.db import session as sess

    with sess.engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_interest_accrual_day"))
        for balance in ("1.0", "2.0"):
            conn.execute(text(
                "INSERT INTO interest_accruals (account_id, accrual_date, currency, ending_balance) "
                f"VALUES ('U1', '2025-02-03', 'EUR', {balance})"
            ))
    sess.init_db()
    assert "uq_interest_accrual_day" in {
        i["name"] for i in inspect(sess.engine).get_indexes("interest_accruals")}
    with sess.engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT ending_balance FROM interest_accruals WHERE account_id='U1'")).all()
    assert [float(r[0]) for r in rows] == [2.0]


# ------------------------------------------------------------- B37 broker interest 0
def test_zero_broker_interest_stays_zero():
    xml = _stmt_xml("""
      <CashReport>
        <CashReportCurrency accountId="U1234567" currency="USD" endingCash="1" brokerInterest="0"
          depositsWithdrawals="0" deposits="5" />
        <CashReportCurrency accountId="U1234567" currency="EUR" endingCash="1" brokerInterest=""
          brokerInterestPaid="-2" />
        <CashReportCurrency accountId="U1234567" currency="CHF" endingCash="1" />
      </CashReport>""")
    _load(xml)
    with session_scope() as s:
        by = {r.currency: r for r in s.execute(select(CashReportRow)).scalars()}
    assert by["USD"].broker_interest == Decimal("0")
    assert by["USD"].deposits_withdrawals == Decimal("0")  # not the alias value 5
    assert by["EUR"].broker_interest == Decimal("-2")
    assert by["CHF"].broker_interest is None


# ------------------------------------------------------------- B38 stub instruments
def test_discovery_stub_never_overwrites_enrichment():
    with session_scope() as s:
        s.add(Instrument(
            conid=42, symbol="AAA", description="AAA INC", isin="US0000000001",
            issuer_country="US", sector="Tech", asset_class="STK", currency="USD",
        ))
    xml = _stmt_xml("""
      <OpenPositions>
        <OpenPosition accountId="U1234567" conid="42" symbol="AAA" assetCategory="STK"
          currency="USD" position="1" markPrice="1" positionValue="1" fxRateToBase="1"
          reportDate="20250131" />
      </OpenPositions>""")
    _load(xml)
    with session_scope() as s:
        i = s.get(Instrument, 42)
        assert (i.description, i.isin, i.issuer_country, i.sector) == (
            "AAA INC", "US0000000001", "US", "Tech")


# ----------------------------------------------------- B36 rows_replaced + stale run
def test_previous_business_day_and_truncation_rule():
    mon = date(2026, 10, 5)
    assert is_truncated_statement(date(2026, 10, 2), today=mon) is False   # Fri
    assert is_truncated_statement(date(2026, 10, 1), today=mon) is False   # Thu = lbd - 1
    assert is_truncated_statement(date(2026, 9, 30), today=mon) is True
    assert is_truncated_statement(None, today=mon) is False
    wed = date(2026, 10, 7)
    assert is_truncated_statement(date(2026, 10, 5), today=wed) is False
    assert is_truncated_statement(date(2026, 10, 2), today=wed) is True


class _FakeResp:
    def __init__(self, xml: bytes):
        self.xml = xml
        self.reference_code = "R1"


def _patch_client(monkeypatch, xml: bytes):
    class _Client:
        def __init__(self, **kw): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def fetch(self, qid, **kw): return _FakeResp(xml)

    monkeypatch.setattr(pipeline, "FlexClient", _Client)
    monkeypatch.setenv("IB_FLEX_TOKEN", "t" * 16)
    monkeypatch.setenv("IB_FLEX_QUERY_ID", "123")
    from ibkr_backend import config as cfg
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]


def _runs():
    with session_scope() as s:
        return s.execute(select(FlexRun).order_by(FlexRun.run_id)).scalars().all()


def test_truncated_statement_is_logged_stale_and_replaced_rows_counted(monkeypatch):
    xml = _stmt_xml(
        "<InterestAccruals><InterestAccrualsCurrency accountId=\"U1234567\" currency=\"USD\" "
        "toDate=\"20250131\" interestAccrued=\"-1\" endingAccrualBalance=\"-2\" /></InterestAccruals>",
        to_date="20250131",  # long before today's last business day
    )
    _patch_client(monkeypatch, xml)
    run_full_pipeline(note="scheduled")
    run_full_pipeline(note="scheduled")
    first, second = _runs()
    assert first.status == "stale" and second.status == "stale"
    assert second.rows_inserted == 0 and second.rows_replaced >= 1


def test_fresh_statement_is_success_and_explicit_window_is_never_stale(monkeypatch):
    last = pipeline._previous_business_day(date.today())
    xml = _stmt_xml("<InterestAccruals />", to_date=last.strftime("%Y%m%d"))
    _patch_client(monkeypatch, xml)
    run_full_pipeline(note="scheduled")
    old = _stmt_xml("<InterestAccruals />", to_date="20240131")
    _patch_client(monkeypatch, old)
    run_full_pipeline(from_date="20240101", to_date="20240131", note="bootstrap")
    assert [r.status for r in _runs()] == ["success", "success"]


# ------------------------------------------------------------- B04 / B58 NULL fx
def test_to_base_is_null_for_null_fx_and_warns(caplog):
    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.add(CashTransaction(
            transaction_id="x1", account_id="U1", type="Dividends", amount=Decimal("100"),
            currency="USD", event_datetime=datetime(2025, 1, 1), settle_date=date(2025, 1, 1),
            fx_rate_to_base=None,
        ))
        s.add(CashTransaction(
            transaction_id="x2", account_id="U1", type="Dividends", amount=Decimal("100"),
            currency="USD", event_datetime=datetime(2025, 1, 1), settle_date=date(2025, 1, 1),
            fx_rate_to_base=Decimal("0.8"),
        ))
    with session_scope() as s:
        total = s.execute(select(func.sum(_to_base(
            CashTransaction.amount, CashTransaction.fx_rate_to_base)))).scalar()
        assert float(total) == pytest.approx(80.0)   # NOT 180: the NULL row is dropped
        with caplog.at_level("WARNING"):
            assert null_fx_counts(s) == {"cash_transactions": 1}
    assert "NULL fx_rate_to_base" in caplog.text


def _snapshot_with_position(*, conid: int, fx):
    from ibkr_backend.integrations.investmentlab import build_investmentlab_portfolio_snapshot

    day = date(2026, 8, 1)
    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="CHF"))
        s.add(Instrument(conid=conid, symbol="AAA", asset_class="STK", currency="USD"))
        s.flush()
        s.add(NavDaily(snapshot_date=day, account_id="U1", base_currency="CHF",
                       nav_total=Decimal("1000")))
        s.add(PositionEod(snapshot_date=day, account_id="U1", conid=conid, symbol="AAA",
                          quantity=Decimal("1"), position_value=Decimal("100"),
                          fx_rate_to_base=fx, currency="USD", asset_class="STK"))
    with session_scope() as s:
        return build_investmentlab_portfolio_snapshot(
            s, now=datetime(2026, 8, 2, 8, 0).astimezone())


def test_contract_blocks_null_fx_and_negative_conid_snapshots():
    ok = _snapshot_with_position(conid=10, fx=Decimal("0.8"))
    assert ok.status in {"ready", "stale"}
    assert "POSITION_FX_MISSING" not in ok.warnings


def test_contract_blocks_null_fx():
    snap = _snapshot_with_position(conid=11, fx=None)
    assert snap.status == "blocked" and "POSITION_FX_MISSING" in snap.warnings
    assert snap.positions == []


def test_contract_blocks_negative_conid():
    snap = _snapshot_with_position(conid=-5, fx=Decimal("1"))
    assert snap.status == "blocked" and "INVALID_POSITION_IDENTITY" in snap.warnings


# ------------------------------------------------------------- P1.1 mtd_ytd_performance
def test_mtd_ytd_section_is_ignored_and_legacy_table_is_dropped(sample_flex_xml):
    """The fixture still carries MTDYTDPerformanceSummary; loading it must work
    and must not recreate the table, and `init_db` drops an existing one."""
    from sqlalchemy import inspect, text

    from ibkr_backend.db import session as sess

    _load(sample_flex_xml)
    assert "mtd_ytd_performance" not in inspect(sess.engine).get_table_names()

    with sess.engine.begin() as conn:
        conn.execute(text("CREATE TABLE mtd_ytd_performance (id INTEGER)"))
    sess.init_db()
    assert "mtd_ytd_performance" not in inspect(sess.engine).get_table_names()


def test_fee_id_is_the_same_for_parsed_and_stored_decimals():
    """Rows read back from SQLite carry Decimal('-0.41310000'); a re-pull parses
    Decimal('-0.4131'). Both must map to one fee id or every pull duplicates."""
    xml = _fees(_FEE.format(sym="AAA", conid=1, tid="T1", qty=10))
    _load(xml)
    with session_scope() as s:
        stored = s.execute(select(TransactionFee)).scalar_one()
        stored_id = fee_id_for(stored)
        assert stored_id == stored.fee_id
    with session_scope() as s:
        from ibkr_backend.loaders.dedup import rekey_fee_ids
        assert rekey_fee_ids(s, apply=False)["rekeyed"] == 0
    _load(xml)
    with session_scope() as s:
        assert s.execute(select(func.count()).select_from(TransactionFee)).scalar() == 1
