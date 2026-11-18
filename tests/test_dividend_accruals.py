"""Idempotency of dividend_accruals when unique-key columns are NULL.

The uq_dividend_accrual constraint spans (account_id, conid, ex_date,
pay_date, gross_amount), but conid/ex_date/pay_date/gross_amount are nullable
and SQL NULLs never compare equal — `ON CONFLICT DO NOTHING` silently
re-inserts any accrual with a NULL key column on every load. The loader must
dedupe those rows itself with a NULL-aware existence check.
"""

from __future__ import annotations

from sqlalchemy import func, select

from ibkr_backend.db import session_scope
from ibkr_backend.db.models import DividendAccrual
from ibkr_backend.flex.parser import parse_flex_xml
from ibkr_backend.loaders.pipeline import load_statement


def _xml(*accrual_attrs: str) -> bytes:
    rows = "\n        ".join(
        f'<OpenDividendAccrual accountId="U1234567" {attrs} />'
        for attrs in accrual_attrs
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="dashboard_full_daily" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U1234567" fromDate="20250101" toDate="20250131"
                   period="LastMonth" whenGenerated="20250201;060000">
      <OpenDividendAccruals>
        {rows}
      </OpenDividendAccruals>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
""".encode()


def _load(xml: bytes) -> None:
    statements = parse_flex_xml(xml)
    with session_scope() as session:
        for stmt in statements:
            load_statement(session, stmt)


def _count() -> int:
    with session_scope() as s:
        return s.execute(
            select(func.count()).select_from(DividendAccrual)
        ).scalar()


def test_null_conid_accrual_not_duplicated_on_reload():
    """Same accrual without conid, ingested twice → exactly one row."""
    xml = _xml(
        'symbol="PRIVX" exDate="20250210" payDate="20250215" quantity="5" '
        'grossRate="0.25" grossAmount="1.25" tax="-0.19" netAmount="1.06" '
        'currency="USD"'
    )
    _load(xml)
    _load(xml)
    assert _count() == 1


def test_null_dates_accrual_not_duplicated_on_reload():
    """Same accrual with conid but NULL ex/pay dates → exactly one row."""
    xml = _xml(
        'conid="265598" symbol="AAPL" quantity="5" '
        'grossRate="0.25" grossAmount="1.25" currency="USD"'
    )
    _load(xml)
    _load(xml)
    assert _count() == 1


def test_distinct_null_key_accruals_both_kept():
    """Two different accruals, both missing conid, are NOT collapsed."""
    xml = _xml(
        'symbol="PRIVX" exDate="20250210" payDate="20250215" '
        'grossAmount="1.25" currency="USD"',
        'symbol="PRIVY" exDate="20250210" payDate="20250215" '
        'grossAmount="9.00" currency="USD"',
    )
    _load(xml)
    _load(xml)
    assert _count() == 2


def test_in_batch_duplicate_null_key_rows_collapse():
    """The same NULL-key accrual twice within one statement → one row."""
    attrs = (
        'symbol="PRIVX" exDate="20250210" payDate="20250215" '
        'grossAmount="1.25" currency="USD"'
    )
    _load(_xml(attrs, attrs))
    assert _count() == 1


def test_fully_keyed_accrual_still_idempotent():
    """Regression guard: the ON CONFLICT path keeps working for full keys."""
    xml = _xml(
        'conid="265598" symbol="AAPL" exDate="20250210" payDate="20250215" '
        'quantity="5" grossRate="0.25" grossAmount="1.25" tax="-0.19" '
        'netAmount="1.06" currency="USD"'
    )
    _load(xml)
    _load(xml)
    assert _count() == 1
