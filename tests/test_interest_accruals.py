"""Idempotency of interest_accruals when interest_type is NULL.

The uq_interest_accrual constraint spans (account_id, accrual_date, currency,
interest_type), but interest_type is nullable and SQL NULLs never compare
equal — `ON CONFLICT DO NOTHING` silently re-inserts any accrual without
interestType on every load. The loader must dedupe those rows itself with a
NULL-aware existence check, same pattern as dividend_accruals (finding B10).
"""

from __future__ import annotations

from sqlalchemy import func, select

from ibkr_backend.db import session_scope
from ibkr_backend.db.models import InterestAccrual
from ibkr_backend.flex.parser import parse_flex_xml
from ibkr_backend.loaders.pipeline import load_statement


def _xml(*accrual_attrs: str) -> bytes:
    rows = "\n        ".join(
        f'<InterestAccrualsCurrency accountId="U1234567" {attrs} />'
        for attrs in accrual_attrs
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="dashboard_full_daily" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U1234567" fromDate="20250101" toDate="20250131"
                   period="LastMonth" whenGenerated="20250201;060000">
      <InterestAccruals>
        {rows}
      </InterestAccruals>
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
            select(func.count()).select_from(InterestAccrual)
        ).scalar()


def test_null_interest_type_not_duplicated_on_reload():
    """Same accrual without interestType, ingested twice → exactly one row."""
    xml = _xml(
        'toDate="20250131" currency="USD" interest="-1.23" '
        'endingAccrualBalance="-12.34"'
    )
    _load(xml)
    _load(xml)
    assert _count() == 1


def test_distinct_null_type_accruals_both_kept():
    """Two different currencies, both missing interestType, NOT collapsed."""
    xml = _xml(
        'toDate="20250131" currency="USD" interest="-1.23"',
        'toDate="20250131" currency="CHF" interest="-4.56"',
    )
    _load(xml)
    _load(xml)
    assert _count() == 2


def test_in_batch_duplicate_null_type_rows_collapse():
    """The same NULL-type accrual twice within one statement → one row."""
    attrs = 'toDate="20250131" currency="USD" interest="-1.23"'
    _load(_xml(attrs, attrs))
    assert _count() == 1


def test_typed_accrual_still_idempotent():
    """Regression guard: the ON CONFLICT path keeps working for full keys."""
    xml = _xml(
        'toDate="20250131" currency="USD" interestType="DEBIT" '
        'interest="-1.23" endingAccrualBalance="-12.34"'
    )
    _load(xml)
    _load(xml)
    assert _count() == 1
