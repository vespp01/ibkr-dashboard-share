"""Contract tests for the read-only Investmentlab portfolio snapshot."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ibkr_backend.api.main import create_app
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import Account, Instrument, NavDaily, PositionEod
from ibkr_backend.integrations.investmentlab import (
    SCHEMA_VERSION,
    build_investmentlab_portfolio_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 2, 8, 30, tzinfo=UTC)


def _add_account(session, account_id: str, *, currency: str = "CHF") -> None:
    session.add(Account(account_id=account_id, base_currency=currency))


def _add_nav(
    session,
    account_id: str,
    snapshot_date: date,
    nav: str,
    *,
    currency: str = "CHF",
) -> None:
    session.add(
        NavDaily(
            snapshot_date=snapshot_date,
            account_id=account_id,
            base_currency=currency,
            nav_total=Decimal(nav),
        )
    )


def _add_position(
    session,
    account_id: str,
    snapshot_date: date,
    *,
    conid: int,
    symbol: str,
    quantity: str,
    market_value: str,
    currency: str = "USD",
    asset_class: str = "STK",
    isin: str | None = None,
) -> None:
    if session.get(Instrument, conid) is None:
        session.add(
            Instrument(
                conid=conid,
                symbol=symbol,
                asset_class=asset_class,
                currency=currency,
                isin=isin,
            )
        )
        session.flush()
    session.add(
        PositionEod(
            snapshot_date=snapshot_date,
            account_id=account_id,
            conid=conid,
            symbol=symbol,
            quantity=Decimal(quantity),
            position_value=Decimal(market_value),
            fx_rate_to_base=Decimal("1"),
            currency=currency,
            asset_class=asset_class,
        )
    )


def _seed_ready_snapshot(snapshot_date: date) -> None:
    with session_scope() as session:
        _add_account(session, "PRIVATE_ACCOUNT_A")
        _add_account(session, "PRIVATE_ACCOUNT_B")
        _add_nav(session, "PRIVATE_ACCOUNT_A", snapshot_date, "6000")
        _add_nav(session, "PRIVATE_ACCOUNT_B", snapshot_date, "4000")
        _add_position(
            session,
            "PRIVATE_ACCOUNT_A",
            snapshot_date,
            conid=101,
            symbol="ACME",
            quantity="10",
            market_value="4000",
            isin="US0000000001",
        )
        _add_position(
            session,
            "PRIVATE_ACCOUNT_B",
            snapshot_date,
            conid=101,
            symbol="ACME",
            quantity="2",
            market_value="1000",
            isin="US0000000001",
        )
        _add_position(
            session,
            "PRIVATE_ACCOUNT_A",
            snapshot_date,
            conid=202,
            symbol="SHORT",
            quantity="-5",
            market_value="-500",
        )


def test_no_data_returns_valid_blocked_contract():
    with session_scope() as session:
        contract = build_investmentlab_portfolio_snapshot(session, now=NOW)

    assert contract.schema_version == SCHEMA_VERSION
    assert contract.status == "blocked"
    assert contract.as_of is None
    assert contract.summary.nav_base is None
    assert contract.positions == []
    assert contract.warnings == ["NO_POSITION_SNAPSHOT"]


def test_ready_snapshot_aggregates_accounts_without_exposing_identifiers():
    _seed_ready_snapshot(NOW.date())

    with session_scope() as session:
        contract = build_investmentlab_portfolio_snapshot(session, now=NOW)

    payload = contract.model_dump(mode="json")
    encoded = json.dumps(payload)
    assert contract.status == "ready"
    assert contract.base_currency == "CHF"
    assert contract.summary.accounts_aggregated == 2
    assert contract.summary.position_count == 2
    assert contract.summary.nav_base == "10000"
    assert contract.summary.gross_market_value_base == "5500"
    assert contract.summary.net_market_value_base == "4500"
    assert contract.warnings == ["MULTIPLE_ACCOUNTS_AGGREGATED"]

    acme = next(position for position in contract.positions if position.symbol == "ACME")
    short = next(position for position in contract.positions if position.symbol == "SHORT")
    assert acme.quantity == "12"
    assert acme.market_value_base == "5000"
    assert acme.weight_pct == "50"
    assert short.weight_pct == "-5"
    assert "PRIVATE_ACCOUNT" not in encoded
    for forbidden in ("account_id", "alias", "cost_basis", "raw_xml", "credential"):
        assert forbidden not in encoded


def test_stale_snapshot_is_not_ready():
    _seed_ready_snapshot(NOW.date() - timedelta(days=8))

    with session_scope() as session:
        contract = build_investmentlab_portfolio_snapshot(session, now=NOW)

    assert contract.status == "stale"
    assert contract.days_stale == 8
    assert "STALE_POSITION_SNAPSHOT" in contract.warnings


def test_missing_nav_discards_partial_position_data():
    with session_scope() as session:
        _add_account(session, "PRIVATE_ACCOUNT_A")
        _add_position(
            session,
            "PRIVATE_ACCOUNT_A",
            NOW.date(),
            conid=101,
            symbol="ACME",
            quantity="10",
            market_value="4000",
        )

    with session_scope() as session:
        contract = build_investmentlab_portfolio_snapshot(session, now=NOW)

    assert contract.status == "blocked"
    assert contract.positions == []
    assert contract.summary.nav_base is None
    assert "NAV_MISSING_FOR_POSITION_DATE" in contract.warnings


def test_ambiguous_base_currency_blocks_contract():
    snapshot_date = NOW.date()
    with session_scope() as session:
        _add_account(session, "PRIVATE_ACCOUNT_A", currency="CHF")
        _add_account(session, "PRIVATE_ACCOUNT_B", currency="USD")
        _add_nav(session, "PRIVATE_ACCOUNT_A", snapshot_date, "6000", currency="CHF")
        _add_nav(session, "PRIVATE_ACCOUNT_B", snapshot_date, "4000", currency="USD")
        _add_position(
            session,
            "PRIVATE_ACCOUNT_A",
            snapshot_date,
            conid=101,
            symbol="ACME",
            quantity="10",
            market_value="4000",
        )

    with session_scope() as session:
        contract = build_investmentlab_portfolio_snapshot(session, now=NOW)

    assert contract.status == "blocked"
    assert "BASE_CURRENCY_AMBIGUOUS" in contract.warnings


def test_api_endpoint_declares_and_returns_versioned_response_model():
    _seed_ready_snapshot(date.today())
    client = TestClient(create_app())

    response = client.get("/portfolio/investmentlab-snapshot")

    assert response.status_code == 200
    assert response.json()["schema_version"] == SCHEMA_VERSION
    operation = client.app.openapi()["paths"]["/portfolio/investmentlab-snapshot"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/InvestmentlabPortfolioSnapshot")


def test_tracked_schema_forbids_account_identifiers():
    schema = json.loads(
        (ROOT / "contracts" / "ibkr-dashboard.portfolio.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    encoded = json.dumps(schema)

    assert schema["properties"]["schema_version"]["const"] == SCHEMA_VERSION
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["position"]["additionalProperties"] is False
    assert "account_id" not in encoded
    assert "raw_xml" not in encoded


def test_consumer_schema_copy_matches_producer():
    # Investmentlab keeps its own copy of the v1 schema; both sides validate
    # strictly, so silent drift between the copies is the one failure mode no
    # runtime check covers. Local-only: the sibling repo is absent in CI.
    consumer_path = (
        ROOT.parent / "Investmentlab" / "contracts" / "ibkr-dashboard.portfolio.v1.schema.json"
    )
    if not consumer_path.exists():
        pytest.skip("Investmentlab sibling repo not present (CI)")

    producer = json.loads(
        (ROOT / "contracts" / "ibkr-dashboard.portfolio.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    consumer = json.loads(consumer_path.read_text(encoding="utf-8"))

    assert producer == consumer


def test_metodo_tendencia_schema_copy_is_byte_identical():
    # metodo-tendencia (second portfolio.v1 consumer) vendors this schema and
    # must keep it byte-identical. Local-only: the sibling repo is absent in CI.
    consumer_path = (
        ROOT.parent / "metodo-tendencia" / "contracts" / "ibkr-dashboard.portfolio.v1.schema.json"
    )
    if not consumer_path.exists():
        pytest.skip("metodo-tendencia sibling repo not present (CI)")

    producer = ROOT / "contracts" / "ibkr-dashboard.portfolio.v1.schema.json"
    assert consumer_path.read_bytes() == producer.read_bytes()
